"""Moteurs de synthèse vocale. Tous rendent du PCM 16 bits mono, bloc par bloc.

| Moteur  | Où         | Voix                                  | Latence 1ʳᵉ phrase |
|---------|------------|---------------------------------------|--------------------|
| gemini  | en ligne   | Charon & co, celles de Gemini Live    | ~0,5-1,5 s         |
| edge    | en ligne   | Henri, Rémy… (Microsoft, sans clé)    | ~0,5 s             |
| piper   | local, CPU | UPMC, Joe (secours seulement)         | ~0,1 s             |

L'utilisateur ne garde que les quatre voix Edge (27/09/2026) : Kokoro, Piper Siwis
et Tom ont été supprimés. Piper UPMC reste, hors de la liste, comme voix de
secours quand le moteur en ligne tombe. Depuis le 02/11, Piper (GPL-3.0) est un
PROGRAMME SÉPARÉ lancé en sous-processus, jamais importé (`PiperEngine`).

La voix la plus naturelle n'est pas un fichier : c'est la voix native
de Gemini Live (Charon par défaut). On la retrouve par l'API TTS de Gemini,
avec une clé `GEMINI_API_KEY`. Seul le TEXTE des réponses part chez Google,
jamais le micro.
"""
from __future__ import annotations

import base64
import logging
import os
import queue
import re
import threading
from dataclasses import dataclass
from typing import Iterator

from .. import config, hors_ligne

log = logging.getLogger("astrel.voice")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…:])\s+")


class Engine:
    name = "?"
    sample_rate = 22050
    online = False

    def load(self) -> None:
        """Charge le modèle ; lève une exception si le moteur est inutilisable."""

    def synthesize(self, text: str) -> Iterator[bytes]:
        """Produit des blocs PCM 16 bits mono au fil de la synthèse."""
        raise NotImplementedError

    def close(self) -> None:
        """Libère ce que le moteur garde ouvert (le process piper). Rien pour les autres."""


PIPER_TIMEOUT_S = 30.0       # une phrase (ou le chargement du modèle) au-delà : process tenu pour bloqué
PIPER_PURGE_S = 10.0         # attente d'un résultat périmé (phrase interrompue) avant de tuer le process
_PIPER_WROTE = re.compile(r"Wrote (.+\.wav)\s*$")       # journal du CLI piper1-gpl (`--output-dir`)
_PIPER_PHRASES = re.compile(r"(?<=[.!?…])\s+")


def piper_command() -> list[str]:
    """Le PROGRAMME piper à lancer (02/11, licence : Piper est GPL-3.0, Astrel ne l'importe jamais).
    `ASTREL_PIPER_CMD` d'abord, puis le `piper` installé à côté du Python courant (le paquet pip
    `piper-tts` de la venv fournit cette commande), puis celui du PATH. Liste vide : introuvable."""
    import shlex
    import shutil
    import sys
    from pathlib import Path

    if config.PIPER_CMD:
        return shlex.split(config.PIPER_CMD)
    bindir = Path(sys.executable).parent
    for name in ("piper", "piper.exe"):
        if (bindir / name).is_file():
            return [str(bindir / name)]
    found = shutil.which("piper")
    return [found] if found else []


def _piper_lines(text: str) -> list[str]:
    """Une ligne par phrase pour le CLI (une ligne = une synthèse = un WAV) : le 1er son part dès la
    1re phrase. Sans lettre ni chiffre, une ligne ferait planter le CLI (WAV vide) : elle est sautée."""
    flat = " ".join(text.split())  # aucun saut de ligne, même Unicode, dans une ligne
    return [p for p in _PIPER_PHRASES.split(flat) if any(c.isalnum() for c in p)]


class PiperEngine(Engine):
    """Voix locale de secours. Piper (paquet `piper-tts`, GPL-3.0) tourne en PROGRAMME SÉPARÉ
    (02/11, choix de l'utilisateur) : Astrel n'importe aucun de ses modules, il lance la commande `piper`
    et lui parle par un tube, comme à n'importe quel programme. Le process reste ouvert (charger
    le modèle coûte ~1,3 s, une phrase ~0,1 s) : une phrase par ligne sur l'entrée, un WAV par
    ligne dans un dossier privé, son chemin lu dans le journal du CLI (« Wrote … »).
    Un seul lecteur à la fois (le fil du Speaker). Process mort ou bloqué : relancé à la phrase
    suivante (la voix ne se tait jamais pour une panne)."""

    def __init__(self, voice: str, speaker: int | None = None, length_scale: float | None = None,
                 noise_scale: float | None = None, noise_w_scale: float | None = None,
                 model_dir=None) -> None:
        """Les réglages après `voice` servent aux outils (banc du mot d'éveil, données d'entraînement) ;
        `model_dir` : dossier des `.onnx` (défaut `models/piper`)."""
        self.voice = voice
        self.name = f"piper:{voice}"
        self.speaker = speaker
        self.length_scale = length_scale
        self.noise_scale = noise_scale
        self.noise_w_scale = noise_w_scale
        self.model_dir = model_dir
        self._proc = None
        self._out: "queue.Queue[str | None] | None" = None
        self._dir: str | None = None
        self._stale = 0  # phrases envoyées dont le WAV n'a pas été lu (parole interrompue)
        self._lock = threading.Lock()

    # --- process ---

    def _model(self):
        from pathlib import Path

        path = Path(self.model_dir or config.MODELS / "piper") / f"{self.voice}.onnx"
        if not path.exists():
            raise FileNotFoundError(f"voix Piper absente : {path}")
        return path

    def load(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            self._stop_locked()
            self._start_locked()
        # Chauffe : le CLI charge le modèle AVANT de lire son entrée et ne dit pas quand il est prêt.
        # Une courte phrase attendue ici fait lever `load()` si la voix est inutilisable.
        for _ in self._render(["Ok."], relance=False):
            pass

    def _start_locked(self) -> None:
        import json
        import subprocess
        import tempfile

        from ..platform import enfants

        model = self._model()
        cmd = piper_command()
        if not cmd:
            raise FileNotFoundError("programme piper introuvable (paquet piper-tts ou ASTREL_PIPER_CMD)")
        try:
            meta = json.loads(model.with_name(model.name + ".json").read_text(encoding="utf-8"))
            self.sample_rate = int(meta["audio"]["sample_rate"])
        except (OSError, ValueError, KeyError, TypeError):
            pass  # lu dans chaque WAV de toute façon
        self._dir = tempfile.mkdtemp(prefix="astrel-piper-")  # 0700
        args = [*cmd, "-m", str(model), "--output-dir", self._dir]
        if self.speaker is not None:
            args += ["-s", str(self.speaker)]
        scale = self.length_scale if self.length_scale is not None else 1.0 / max(config.TTS_SPEED, 0.1)
        args += ["--length-scale", f"{scale:.4f}"]
        if self.noise_scale is not None:
            args += ["--noise-scale", f"{self.noise_scale:.4f}"]
        if self.noise_w_scale is not None:
            args += ["--noise-w-scale", f"{self.noise_w_scale:.4f}"]
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        # Process long, enfant du cœur : il meurt avec lui (Fiabilité, 01/11).
        try:
            self._proc = enfants.popen(
                args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1, env=env, cwd=self._dir)
        except BaseException:
            self._stop_locked()
            raise
        self._out = queue.Queue()
        self._stale = 0
        threading.Thread(target=self._read, args=(self._proc, self._out), name="piper-journal",
                         daemon=True).start()
        log.info("voix %s : programme piper lancé (pid %s)", self.name, self._proc.pid)

    @staticmethod
    def _read(proc, out: "queue.Queue[str | None]") -> None:
        try:
            for line in proc.stdout:
                m = _PIPER_WROTE.search(line)
                if m:
                    out.put(m.group(1))
                elif line.strip():
                    log.info("piper : %s", line.rstrip())
        except (OSError, ValueError):
            pass
        out.put(None)  # fin du process

    def _stop_locked(self) -> None:
        import shutil

        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=2)
            except Exception:  # noqa: BLE001 — bloqué : tué
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except Exception:  # noqa: BLE001
                    pass
        if self._dir:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None
        self._out = None
        self._stale = 0

    def close(self) -> None:
        with self._lock:
            self._stop_locked()

    def _next(self, out: "queue.Queue[str | None]", timeout: float) -> str:
        try:
            path = out.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"piper n'a rien rendu en {timeout:.0f} s") from None
        if path is None:
            out.put(None)  # la fin reste visible pour l'appel suivant
            raise RuntimeError("le programme piper s'est arrêté")
        return path

    def _fail(self) -> None:
        with self._lock:
            self._stop_locked()

    # --- synthèse ---

    def synthesize(self, text: str) -> Iterator[bytes]:
        lines = _piper_lines(text)
        if lines:
            yield from self._render(lines, relance=True)

    def _render(self, lines: list[str], relance: bool) -> Iterator[bytes]:
        """`relance` : process mort ou bloqué AVANT tout son = relancé une fois (jamais pendant la
        chauffe de `load`, qui lèverait en boucle sur une commande qui s'arrête aussitôt)."""
        import wave

        for attempt in ((0, 1) if relance else (1,)):
            with self._lock:
                alive = self._proc is not None and self._proc.poll() is None
            if not alive:
                if not relance:
                    self._fail()
                    raise RuntimeError("le programme piper s'est arrêté aussitôt lancé")
                self.load()
            proc, out = self._proc, self._out
            assert proc is not None and out is not None
            try:
                while self._stale:  # WAV de phrases interrompues : lus et jetés, dans l'ordre
                    self._discard(self._next(out, PIPER_PURGE_S))
                    self._stale -= 1
                proc.stdin.write("".join(f"{line}\n" for line in lines))
                proc.stdin.flush()
                break
            except Exception as exc:
                self._fail()
                if attempt:
                    raise
                log.warning("voix %s : %s, programme piper relancé", self.name, exc)
        pending = len(lines)
        try:
            for _ in lines:
                path = self._next(out, PIPER_TIMEOUT_S)
                pending -= 1
                try:
                    with wave.open(path, "rb") as w:
                        self.sample_rate = w.getframerate()
                        pcm = w.readframes(w.getnframes())
                finally:
                    self._discard(path)
                if pcm:
                    yield pcm
        except (TimeoutError, RuntimeError, OSError, EOFError, wave.Error):
            self._fail()
            raise
        finally:
            if self._proc is proc:
                self._stale += pending  # parole coupée : ces WAV arriveront quand même

    @staticmethod
    def _discard(path: str) -> None:
        try:
            os.unlink(path)
        except OSError:
            pass


class GeminiEngine(Engine):
    """Voix Gemini par l'API TTS, en flux (PCM brut 24 kHz)."""

    sample_rate = 24000
    online = True

    def __init__(self, voice: str = "Charon") -> None:
        self.voice = voice or "Charon"
        self.name = f"gemini:{self.voice}"
        self._client = None

    def load(self) -> None:
        if self._client is not None:
            return
        key = config.secret("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY absente (config/secrets.json ou environnement)")
        from google import genai

        self._client = genai.Client(api_key=key)

    def _request(self, text: str, stream: bool):
        assert self._client is not None
        content: dict = {"type": "text", "text": text}
        if config.GEMINI_TTS_STYLE:
            content["annotations"] = [{"type": "speech_metadata", "style": config.GEMINI_TTS_STYLE}]
        return self._client.interactions.create(
            model=config.GEMINI_TTS_MODEL,
            input=[{"type": "user_input", "content": [content]}],
            response_format={"type": "audio", "mime_type": "audio/l16", "sample_rate": self.sample_rate},
            generation_config={"speech_config": [{"voice": self.voice, "language": "fr-FR"}]},
            stream=stream,
        )

    def synthesize(self, text: str) -> Iterator[bytes]:
        self.load()
        produced = False
        for payload in _audio_payloads(self._request(text, stream=True)):
            pcm = _strip_wav_header(base64.b64decode(payload))
            if pcm:
                produced = True
                yield pcm
        if not produced:
            raise RuntimeError("Gemini n'a renvoyé aucun audio")


class EdgeEngine(Engine):
    """Voix neuronales de Microsoft Edge (paquet `edge-tts`) : gratuites, sans
    compte ni clé. Le service ne rend que du MP3 : décodé au fil de l'eau par PyAV."""

    sample_rate = 24000
    online = True

    def __init__(self, voice: str = "fr-FR-HenriNeural") -> None:
        self.voice = voice or "fr-FR-HenriNeural"
        self.name = f"edge:{self.voice}"

    def load(self) -> None:
        import av  # noqa: F401
        import edge_tts  # noqa: F401

    def synthesize(self, text: str) -> Iterator[bytes]:
        # 04/11 : le texte partirait chez Microsoft ; le haut-parleur passe alors sur sa voix de secours (Piper)
        hors_ligne.verifier("voix Edge")
        import asyncio
        import queue
        import threading

        import av
        import edge_tts
        import edge_tts.communicate as edge_communicate
        from edge_tts.constants import TRUSTED_CLIENT_TOKEN

        # Contournement du 403 de Microsoft (oct. 2026) : nouveau serveur, ancien jeton.
        # À retirer quand edge-tts publiera un correctif (issue rany2/edge-tts#490).
        edge_communicate.WSS_URL = (
            "wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"
            f"?Ocp-Apim-Subscription-Key={TRUSTED_CLIENT_TOKEN}")

        rate = f"{round((config.TTS_SPEED - 1.0) * 100):+d}%"
        chunks: queue.Queue = queue.Queue()

        async def pump() -> None:
            async for msg in edge_tts.Communicate(text, self.voice, rate=rate).stream():
                if msg["type"] == "audio":
                    chunks.put(msg["data"])

        def run() -> None:
            try:
                asyncio.run(pump())
                chunks.put(None)
            except Exception as exc:  # remonté au Speaker, qui bascule sur le repli
                chunks.put(exc)

        threading.Thread(target=run, name="edge-tts", daemon=True).start()
        codec = av.CodecContext.create("mp3", "r")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=self.sample_rate)
        produced = False
        while True:
            item = chunks.get(timeout=30)
            if isinstance(item, Exception):
                raise item
            for packet in codec.parse(item if item is not None else b""):
                for frame in codec.decode(packet):
                    for out in resampler.resample(frame):
                        produced = True
                        yield bytes(out.planes[0])[: out.samples * 2]
            if item is None:
                break
        for out in resampler.resample(None):
            yield bytes(out.planes[0])[: out.samples * 2]
        if not produced:
            raise RuntimeError("Edge n'a renvoyé aucun audio")


def _audio_payloads(obj) -> Iterator[str]:
    """Extrait les données audio base64 d'un flux d'événements, quelle qu'en soit
    la forme exacte : le SDK des « interactions » est récent et sa structure a
    déjà changé d'une version à l'autre. On cherche tout objet `type == audio`
    porteur de `data`, en profondeur."""
    try:
        iterator = iter(obj)
        is_stream = not isinstance(obj, (dict, str, bytes))
    except TypeError:
        is_stream = False
    items = iterator if is_stream else [obj]
    for item in items:
        data = item.model_dump() if hasattr(item, "model_dump") else item
        yield from _find_audio(data)


def _find_audio(node) -> Iterator[str]:
    if isinstance(node, dict):
        if node.get("type") == "audio" and isinstance(node.get("data"), str):
            yield node["data"]
            return
        for value in node.values():
            yield from _find_audio(value)
    elif isinstance(node, list):
        for value in node:
            yield from _find_audio(value)


def _strip_wav_header(raw: bytes) -> bytes:
    if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
        idx = raw.find(b"data")
        if idx != -1:
            return raw[idx + 8:]
    return raw


def build_engine(spec: str) -> Engine:
    """`spec` : « gemini:Charon », « edge:fr-FR-HenriNeural », « piper:fr_FR-upmc-medium »."""
    kind, _, arg = spec.partition(":")
    kind = kind.strip().lower()
    if kind == "gemini":
        return GeminiEngine(arg or "Charon")
    if kind == "edge":
        return EdgeEngine(arg or "fr-FR-HenriNeural")
    return PiperEngine(arg or "fr_FR-upmc-medium")


@dataclass
class VoiceInfo:
    spec: str
    label: str
    online: bool = False


GEMINI_VOICES = {
    "Charon": "informative, grave",
    "Kore": "ferme",
    "Puck": "enjouée",
    "Fenrir": "vive",
    "Aoede": "légère",
    "Zephyr": "claire",
}


EDGE_VOICES = {
    "fr-FR-HenriNeural": "Henri — masculine, posée",
    "fr-FR-RemyMultilingualNeural": "Rémy — masculine, très naturelle",
    "fr-FR-VivienneMultilingualNeural": "Vivienne — féminine, très naturelle",
    "fr-FR-DeniseNeural": "Denise — féminine",
    # 02/11 (conversation anglaise) : voix anglaises (noms vérifiés avec edge-tts 7.3.1 --list-voices) ;
    # la liste des Réglages ne montre que celles de la langue de la conversation.
    "en-US-AndrewNeural": "Andrew — masculine, américaine, posée",
    "en-US-AndrewMultilingualNeural": "Andrew multilingue — masculine, américaine",
    "en-US-BrianNeural": "Brian — masculine, américaine, détendue",
    "en-US-AvaMultilingualNeural": "Ava multilingue — féminine, américaine",
    "en-US-EmmaNeural": "Emma — féminine, américaine, enjouée",
    "en-GB-RyanNeural": "Ryan — masculine, britannique",
    "en-GB-SoniaNeural": "Sonia — féminine, britannique",
}


def available_voices() -> list[VoiceInfo]:
    """Voix proposées au choix. La voix de secours Piper n'y figure pas exprès."""
    out: list[VoiceInfo] = []
    if config.secret("GEMINI_API_KEY"):
        for name, label in GEMINI_VOICES.items():
            out.append(VoiceInfo(f"gemini:{name}", f"Gemini {name} — {label}", online=True))
    for name, label in EDGE_VOICES.items():
        out.append(VoiceInfo(f"edge:{name}", f"Edge {label}", online=True))
    return out
