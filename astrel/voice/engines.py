"""Moteurs de synthèse vocale. Tous rendent du PCM 16 bits mono, bloc par bloc.

| Moteur  | Où         | Voix                                  | Latence 1ʳᵉ phrase |
|---------|------------|---------------------------------------|--------------------|
| gemini  | en ligne   | Charon & co, celles de Gemini Live    | ~0,5-1,5 s         |
| edge    | en ligne   | Henri, Rémy… (Microsoft, sans clé)    | ~0,5 s             |
| piper   | local, CPU | UPMC (secours seulement)              | quasi instantané   |

L'utilisateur ne garde que les quatre voix Edge (27/09/2026) : Kokoro, Piper Siwis
et Tom ont été supprimés. Piper UPMC reste, hors de la liste, comme voix de
secours quand le moteur en ligne tombe.

Autre voix possible : la voix native de Gemini Live
(Charon par défaut). On la retrouve par l'API TTS de Gemini,
avec une clé `GEMINI_API_KEY`. Seul le TEXTE des réponses part chez Google,
jamais le micro.
"""
from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from typing import Iterator

from .. import config

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


class PiperEngine(Engine):
    def __init__(self, voice: str) -> None:
        self.voice = voice
        self.name = f"piper:{voice}"
        self._voice = None
        self._syn = None

    def load(self) -> None:
        if self._voice is not None:
            return
        from piper import PiperVoice
        from piper.config import SynthesisConfig

        path = config.MODELS / "piper" / f"{self.voice}.onnx"
        if not path.exists():
            raise FileNotFoundError(f"voix Piper absente : {path}")
        self._voice = PiperVoice.load(str(path))
        self._syn = SynthesisConfig(length_scale=1.0 / max(config.TTS_SPEED, 0.1))
        self.sample_rate = self._voice.config.sample_rate

    def synthesize(self, text: str) -> Iterator[bytes]:
        self.load()
        assert self._voice is not None
        for chunk in self._voice.synthesize(text, syn_config=self._syn):
            self.sample_rate = chunk.sample_rate
            yield chunk.audio_int16_bytes


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
