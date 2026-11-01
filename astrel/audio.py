"""Capture et restitution audio via PipeWire en ligne de commande.

⚠️ On évite volontairement PortAudio (`sounddevice`) : il exige le paquet système
`libportaudio2`, absent de la machine de référence. `parecord`/`paplay` sont déjà là et suffisent.

Portage (P2, lot 5) : ce moteur « pulse » reste celui de Linux, inchangé. Sous
macOS et Windows, où il n'y a pas PipeWire, le moteur `sounddevice` (PortAudio
livré dans la roue pip) ; pour les tests et la CI, le moteur `fichier`. Voir
`astrel/platform/son.py` ; `ASTREL_AUDIO_ENGINE=pulse|sounddevice|fichier` force
le choix.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
from typing import Callable

import numpy as np

from . import config, micprofile
from .events import bus

log = logging.getLogger("astrel.audio")

try:
    import fcntl
except ImportError:  # Windows : la purge du tube ne sert qu'au moteur « pulse »
    fcntl = None  # type: ignore[assignment]

ENGINES = ("pulse", "sounddevice", "fichier")


def engine() -> str:
    """Moteur audio : `pulse` sous Linux (d'origine), `sounddevice` ailleurs,
    sauf `ASTREL_AUDIO_ENGINE`."""
    forced = os.environ.get("ASTREL_AUDIO_ENGINE", "").strip().lower()
    if forced in ENGINES:
        return forced
    return "pulse" if sys.platform.startswith("linux") else "sounddevice"


def rms(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(a * a))) if a.size else 0.0


def level_from_rms(value: float) -> float:
    """RMS int16 → niveau 0..1 perceptif (échelle log, parole normale ≈ 0,6)."""
    if value <= 1.0:
        return 0.0
    db = 20.0 * math.log10(value / 32768.0)  # ≈ −90 … 0 dBFS
    return max(0.0, min(1.0, (db + 60.0) / 50.0))


class _Throttle:
    """Laisse passer au plus `hz` événements par seconde."""

    def __init__(self, hz: float) -> None:
        self.period = 1.0 / hz
        self._last = 0.0

    def ready(self) -> bool:
        now = time.monotonic()
        if now - self._last >= self.period:
            self._last = now
            return True
        return False


class MicStream:
    """Flux microphone PCM 16 bits mono, lu par blocs de taille fixe.

    Pendant qu'Astrel parle, le micro est rendu SOURD : la capture est purgée
    en continu dans un thread, puis encore `ECHO_TAIL_S` après la fin. Une purge
    unique ne suffit pas — le son continue d'arriver après la fin de la lecture,
    et Astrel s'est un jour répondu trois fois de suite.

    La surdité se compte (`hold_mute` / `release_mute`) : la voix d'une réponse
    et celle d'une question de confirmation peuvent se chevaucher sans que la
    première rende l'oreille au milieu de la seconde.
    """

    def __init__(
        self,
        rate: int = config.SAMPLE_RATE,
        frame_bytes: int = config.FRAME_BYTES,
        source: str | None = None,
        gain: float | None = None,
    ):
        self.rate = rate
        self.frame_bytes = frame_bytes
        self.source = source if source is not None else config.MIC_SOURCE
        self._fixed_gain = gain
        self._channels: tuple[int, ...] = ()  # canaux gardés ; vide = mélange de PipeWire
        self._n_in = 1  # canaux réellement lus sur le flux
        self.profile = micprofile.MicProfile()
        self.gain = self.profile.gain if gain is None else gain
        self._proc: subprocess.Popen | None = None
        self._read_lock = threading.Lock()
        self._mute_lock = threading.Lock()
        self._mute_depth = 0
        self._release_timer: threading.Timer | None = None
        self._open = threading.Event()
        self._open.set()
        self._drain: threading.Thread | None = None
        self._drain_stop = threading.Event()
        self._level = _Throttle(config.LEVEL_HZ)

    # --- cycle de vie ---

    def identity(self) -> tuple[str, str]:
        """(nom PipeWire, nom lisible) du micro réellement ouvert : celui du
        système quand `source` est vide."""
        name, label = self.source, ""
        try:
            mics = list_mics()
            if not name:
                name = next((m["name"] for m in mics if m["default"]), "")
            label = next((m["label"] for m in mics if m["name"] == name), "")
        except Exception:
            pass
        return name, label

    def load_profile(self) -> micprofile.MicProfile:
        """Choisit les réglages du micro réellement ouvert : gain, seuil VAD,
        seuil du mot de réveil."""
        name, label = self.identity()
        self.profile = micprofile.resolve(name, label)
        self.gain = self.profile.gain if self._fixed_gain is None else self._fixed_gain
        log.info("micro %s : %s%s", label or name or "du système", self.profile.describe(),
                 f" (profil « {self.profile.key} »)" if self.profile.key else " (réglages par défaut)")
        return self.profile

    def start(self) -> None:
        if self._proc is not None:
            return
        self.load_profile()
        self._channels, self._n_in = (), 1
        if self.profile.channels:
            n = source_channels(self.identity()[0])
            if n > max(self.profile.channels):
                self._channels, self._n_in = self.profile.channels, n
            else:
                log.warning("profil micro : canaux %s absents (%d canal(aux)), mélange par défaut",
                            self.profile.channels, n)
        eng = engine()
        if eng != "pulse":
            from .platform import son

            self._proc = son.open_capture(eng, self.rate, self._n_in, self.source)
            return
        cmd = [
            "parecord",
            f"--rate={self.rate}",
            f"--channels={self._n_in}",
            "--format=s16le",
            "--raw",
            "--latency-msec=40",
        ]
        if self.source:
            cmd.append(f"--device={self.source}")
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def switch(self, source: str) -> None:
        """Change de micro à chaud. Sous le verrou de lecture : au pire on attend
        la fin du bloc en cours (80 ms), et le lecteur suivant lit le nouveau flux
        sans rien remarquer — ni la veille, ni une écoute en cours, ni la purge."""
        with self._read_lock:
            old, self._proc = self._proc, None
            self.source = source
            if old is None:
                self.load_profile()
                return
            old.terminate()
            try:
                old.wait(timeout=2)
            except subprocess.TimeoutExpired:
                old.kill()
            self.start()

    def stop(self) -> None:
        self._drain_stop.set()
        if self._proc is None:
            return
        self._proc.terminate()
        try:
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None

    def __enter__(self) -> "MicStream":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    # --- lecture ---

    def read_frame(self) -> bytes:
        """Lit exactement un bloc (80 ms). Bloquant."""
        with self._read_lock:
            assert self._proc is not None and self._proc.stdout is not None
            want = self.frame_bytes * self._n_in
            buf = self._proc.stdout.read(want)
        if not buf or len(buf) < want:
            raise IOError("flux microphone interrompu")
        if self._n_in > 1:
            buf = self._select_channels(buf)
        buf = self._apply_gain(buf)
        if self._level.ready() and not self.muted:
            bus.publish("level", source="mic", value=round(level_from_rms(rms(buf)), 3))
        return buf

    def _select_channels(self, buf: bytes) -> bytes:
        """Moyenne des seuls canaux du profil : un micro en réseau a parfois un canal
        mort et un autre saturé de bruit, que le mélange de PipeWire ramène dans la voix."""
        pcm = np.frombuffer(buf, dtype=np.int16).reshape(-1, self._n_in)[:, list(self._channels)]
        return pcm.astype(np.float32).mean(axis=1).astype(np.int16).tobytes()

    def _apply_gain(self, buf: bytes) -> bytes:
        if abs(self.gain - 1.0) < 1e-3:
            return buf
        pcm = np.frombuffer(buf, dtype=np.int16).astype(np.float32) * self.gain
        return np.clip(pcm, -32768, 32767).astype(np.int16).tobytes()

    def flush(self, settle_s: float = 0.0) -> None:
        """Jette l'audio déjà capté, puis ce qui arrive encore pendant settle_s."""
        if self._proc is None or self._proc.stdout is None:
            return
        if hasattr(self._proc, "discard"):  # moteur sans tube (sounddevice, fichier)
            with self._read_lock:
                self._proc.discard(settle_s)
            return
        with self._read_lock:
            stdout = self._proc.stdout
            fd = stdout.fileno()
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            dropped = 0
            try:
                # Le tampon interne du BufferedReader d'abord, le tube ensuite.
                try:
                    while chunk := stdout.read1(65536):
                        dropped += len(chunk)
                except (BlockingIOError, ValueError, AttributeError, TypeError):
                    pass
                deadline = time.monotonic() + settle_s
                while True:
                    try:
                        chunk = os.read(fd, 65536)
                    except BlockingIOError:
                        chunk = b""
                    dropped += len(chunk)
                    if not chunk:
                        if time.monotonic() >= deadline:
                            break
                        time.sleep(0.02)
            finally:
                fcntl.fcntl(fd, fcntl.F_SETFL, flags)
            # Un échantillon coupé en deux décalerait tout le flux : on finit la trame.
            step = 2 * self._n_in
            if dropped % step:
                stdout.read(step - dropped % step)

    # --- surdité pendant la parole ---

    @property
    def muted(self) -> bool:
        """Vrai tant que le micro est sourd, queue d'écho comprise."""
        return not self._open.is_set()

    def hold_mute(self) -> None:
        with self._mute_lock:
            self._mute_depth += 1
            if self._release_timer is not None:
                # Une nouvelle parole commence pendant la queue d'écho : on garde
                # l'oreille fermée, la purge en cours continue.
                self._release_timer.cancel()
                self._release_timer = None
            if self._drain is None:
                self._open.clear()
                self._drain_stop.clear()
                self._drain = threading.Thread(target=self._drain_loop, name="mic-drain", daemon=True)
                self._drain.start()

    def release_mute(self, settle_s: float = config.ECHO_TAIL_S) -> None:
        """Libère un niveau de surdité. Non bloquant : la réouverture se fait
        après `settle_s` dans une minuterie, annulée si une parole reprend."""
        with self._mute_lock:
            if self._mute_depth == 0:
                return
            self._mute_depth -= 1
            if self._mute_depth > 0:
                return
            self._release_timer = threading.Timer(settle_s, self._finish_release)
            self._release_timer.daemon = True
            self._release_timer.start()

    def _finish_release(self) -> None:
        with self._mute_lock:
            if self._mute_depth > 0:
                return
            self._release_timer = None
            self._drain_stop.set()
            drain = self._drain
            self._drain = None
        if drain is not None:
            drain.join(timeout=2.0)
        self.flush()
        self._open.set()

    def wait_open(self, timeout: float = 10.0) -> bool:
        """Attend que le micro entende de nouveau (fin de parole + queue d'écho)."""
        return self._open.wait(timeout)

    def _drain_loop(self) -> None:
        while not self._drain_stop.is_set():
            self.flush()
            time.sleep(0.04)


# --- restitution ---

_PLAYER: str | None = None


def player_cmd(rate: int) -> list[str]:
    global _PLAYER
    if _PLAYER is None:
        _PLAYER = "paplay" if shutil.which("paplay") else "aplay"
    if _PLAYER == "paplay":
        # Nom du client = nom affiché (visible dans les réglages du son de GNOME) ; aucun code ne le
        # recherche, WirePlumber y range seulement le volume de ce flux.
        from .i18n import assistant_name
        return ["paplay", "--raw", f"--rate={rate}", "--channels=1", "--format=s16le",
                f"--client-name={assistant_name()}", "--stream-name=voix"]
    return ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(rate), "-c", "1", "-"]


def _open_player(rate: int):
    """Un lecteur qui reçoit le PCM sur son entrée : `paplay`/`aplay` (pulse),
    ou l'équivalent de `platform/son.py`."""
    eng = engine()
    if eng == "pulse":
        return subprocess.Popen(player_cmd(rate), stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    from .platform import son

    return son.open_player(eng, rate)


class Player:
    """Lecture PCM en continu : on écrit des blocs, le son sort au fil de l'eau.

    Publie le niveau de SORTIE sur le bus, calé sur le moment où l'échantillon
    est effectivement joué et non sur celui où il est écrit dans le tube
    (le tube a plusieurs secondes d'avance) : c'est ce qui fait bouger l'orbe
    en rythme avec la voix.

    Garde aussi tout ce qu'il a reçu et l'heure à laquelle chaque bloc sort
    (un bloc écrit pendant que le précédent joue sortira à sa suite, un bloc
    écrit après un blanc sort tout de suite) : `cut()` sait donc ce qui n'a pas
    encore été entendu, pour la pause de la voix.
    """

    CHUNK_S = 1.0 / config.LEVEL_HZ

    def __init__(self, rate: int, on_level: Callable[[float], None] | None = None) -> None:
        self.rate = rate
        self._proc = _open_player(rate)
        self._on_level = on_level
        self._written_s = 0.0
        self._record = bytearray()                     # tout ce qui a été accepté
        self._sched: list[tuple[float, int, int]] = []  # (heure de sortie, décalage, taille) par bloc
        self._play_end = 0.0                           # heure où le dernier bloc accepté aura fini de sortir
        self._levels: list[tuple[float, float]] = []   # (heure de sortie, niveau)
        self._levels_lock = threading.Lock()
        self._stop = threading.Event()
        self._meter = threading.Thread(target=self._meter_loop, name="out-level", daemon=True)
        self._meter.start()
        self.write(_lead_silence(rate))

    def accept(self, pcm: bytes) -> bool:
        """Inscrit un bloc au programme SANS l'écrire (non bloquant). False si
        la lecture est déjà coupée : le bloc n'a alors pas été pris."""
        with self._levels_lock:
            if self._stop.is_set() or self._proc.stdin is None:
                return False
            start = max(time.monotonic(), self._play_end)
            self._sched.append((start, len(self._record), len(pcm)))
            self._record += pcm
            step = int(self.rate * self.CHUNK_S) * 2
            for i in range(0, len(pcm), step):
                part = pcm[i:i + step]
                self._levels.append((start + i / 2 / self.rate, level_from_rms(rms(part))))
            self._play_end = start + len(pcm) / 2 / self.rate
            self._written_s += len(pcm) / 2 / self.rate
            return True

    def push(self, pcm: bytes) -> bool:
        """Écrit dans le lecteur un bloc déjà accepté (bloque si le tube est plein)."""
        try:
            assert self._proc.stdin is not None
            self._proc.stdin.write(pcm)
            self._proc.stdin.flush()
            return True
        except (AssertionError, BrokenPipeError, ValueError, OSError):
            return False

    def write(self, pcm: bytes) -> bool:
        """Écrit un bloc. False si la lecture a été interrompue."""
        return self.accept(pcm) and self.push(pcm)

    @property
    def duration_s(self) -> float:
        return self._written_s

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def cut(self) -> bytes:
        """Coupe le son NET et rend ce qui n'a pas encore été entendu, à partir
        du début du mot en cours (`resume_point`) : la reprise le redit en entier."""
        with self._levels_lock:
            self._stop.set()
            if heard_bytes(self._sched, time.monotonic(), self.rate) >= len(self._record):
                rest = b""  # tout est déjà sorti : rien à redire
            else:
                heard = heard_bytes(self._sched, time.monotonic() - config.OUTPUT_LATENCY_S, self.rate)
                rest = bytes(self._record[resume_point(self._record, heard, self.rate):])
        self.kill()
        return rest

    def close(self, wait: bool = True) -> None:
        """Fin de l'écriture ; attend la fin du son si `wait`."""
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
        except (BrokenPipeError, ValueError):
            pass
        if wait:
            try:
                self._proc.wait(timeout=max(5.0, self._written_s + 5.0))
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._stop.set()
        if self._on_level:
            self._on_level(0.0)

    def kill(self) -> None:
        """Coupe le son immédiatement."""
        self._stop.set()
        try:
            self._proc.kill()
        except ProcessLookupError:
            pass
        if self._on_level:
            self._on_level(0.0)

    def _meter_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(self.CHUNK_S)
            if self._on_level is None:
                continue
            now = time.monotonic()
            value = None
            with self._levels_lock:
                while self._levels and self._levels[0][0] <= now:
                    value = self._levels.pop(0)[1]
            if value is not None and not self._stop.is_set():
                self._on_level(value)


def heard_bytes(sched: list[tuple[float, int, int]], now: float, rate: int) -> int:
    """Octets déjà sortis du haut-parleur à l'heure `now`, d'après le programme
    des blocs (heure de sortie, décalage, taille). Toujours pair (16 bits)."""
    heard = 0
    for start, offset, size in sched:
        if now <= start:
            break
        heard = offset + min(size, int((now - start) * rate + 1e-6) * 2)
    return heard


def resume_point(pcm: bytes | bytearray, pos: int, rate: int, max_back_s: float = 1.2) -> int:
    """Où reprendre un son coupé à l'octet `pos` : juste avant le mot en cours,
    c'est-à-dire au dernier blanc (fenêtre de 20 ms nettement plus faible que la
    voix alentour) des `max_back_s` précédentes. Sans blanc trouvé : 0,4 s plus tôt."""
    pos = max(0, min(pos, len(pcm))) & ~1
    lo = max(0, pos - int(max_back_s * rate) * 2)
    win = max(2, int(0.02 * rate) * 2)
    if pos - lo < 2 * win:
        return lo
    a = np.frombuffer(bytes(pcm[lo:pos]), dtype=np.int16).astype(np.float32)
    n = a.size // (win // 2)
    if n < 2:
        return lo
    frames = a[: n * (win // 2)].reshape(n, -1)
    levels = np.sqrt((frames * frames).mean(axis=1))
    loud = float(np.percentile(levels, 90))
    if loud < 300.0:  # tout est silence ici : reprendre là où on s'est arrêté
        return pos
    quiet = np.nonzero(levels < max(150.0, 0.12 * loud))[0]
    if quiet.size:
        return lo + int(quiet[-1]) * win
    return max(0, pos - int(0.4 * rate) * 2)


# Pause de la voix : tant qu'elle est active, AUCUN son ne sort, carillons compris
# (le cœur publie l'état, l'orateur garde ce qu'il avait à dire pour la reprise).
_output_held = threading.Event()


def hold_output(on: bool) -> None:
    (_output_held.set if on else _output_held.clear)()


def output_held() -> bool:
    return _output_held.is_set()


def _lead_silence(rate: int) -> bytes:
    """Silence d'amorce : laisse le haut-parleur se réveiller avant le son."""
    return bytes(int(rate * config.OUTPUT_LEAD_S) * 2)


def play_pcm(pcm: bytes, rate: int) -> None:
    """Joue du PCM 16 bits mono et attend la fin (rien pendant la pause de la voix)."""
    if not pcm or output_held():
        return
    pcm = _lead_silence(rate) + pcm
    proc = _open_player(rate)
    try:
        proc.communicate(pcm, timeout=120)
    except subprocess.TimeoutExpired:
        proc.kill()


# --- carillons ---

def _tone(freq: float, ms: int, rate: int, amp: float) -> list[float]:
    n = int(rate * ms / 1000)
    return [amp * math.sin(2 * math.pi * freq * i / rate) for i in range(n)]


def _render(samples: list[float], rate: int, fade_ms: int = 12) -> bytes:
    n = len(samples)
    fade = max(int(rate * fade_ms / 1000), 1)
    out = []
    for i, v in enumerate(samples):
        env = 1.0
        if i < fade:
            env = i / fade
        elif i > n - fade:
            env = max((n - i) / fade, 0.0)
        out.append(int(max(-1.0, min(1.0, v * env)) * 32767))
    return struct.pack(f"<{n}h", *out)


def chime(kind: str = "wake", rate: int = config.SAMPLE_RATE) -> None:
    """Signaux sonores courts, façon interface de vaisseau.

    wake    : deux notes qui montent (une quinte) → « je t'écoute »
    done    : deux notes qui descendent → retour en veille
    confirm : trois notes brèves → une décision est attendue
    error   : note basse répétée
    """
    if kind == "wake":
        a1 = _tone(784.0, 110, rate, 0.34)
        a1 = [v + 0.12 * math.sin(2 * math.pi * 1568.0 * i / rate) for i, v in enumerate(a1)]
        a2 = _tone(1174.7, 190, rate, 0.34)
        a2 = [v + 0.14 * math.sin(2 * math.pi * 2349.0 * i / rate) for i, v in enumerate(a2)]
        a2 = [v * (1.0 - 0.75 * i / len(a2)) for i, v in enumerate(a2)]
        samples = a1 + a2
    elif kind == "done":
        a1 = _tone(1174.7, 100, rate, 0.26)
        a2 = _tone(784.0, 180, rate, 0.26)
        a2 = [v * (1.0 - 0.8 * i / len(a2)) for i, v in enumerate(a2)]
        samples = a1 + a2
    elif kind == "confirm":
        gap = [0.0] * int(rate * 0.04)
        samples = _tone(988.0, 70, rate, 0.28) + gap + _tone(988.0, 70, rate, 0.28) + gap + _tone(1318.5, 120, rate, 0.28)
    else:
        a1 = _tone(320.0, 130, rate, 0.30)
        samples = a1 + [0.0] * int(rate * 0.05) + a1
    play_pcm(_render(samples, rate), rate)


# --- périphériques ---

def list_sources() -> list[str]:
    """Noms des sources PipeWire disponibles (hors moniteurs)."""
    if engine() != "pulse":
        try:
            return [m["name"] for m in list_mics()]
        except Exception:
            return []
    try:
        out = subprocess.run(
            ["pactl", "list", "short", "sources"], capture_output=True, text=True, timeout=5
        ).stdout
    except Exception:
        return []
    names = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and not parts[1].endswith(".monitor"):
            names.append(parts[1])
    return names


def source_channels(name: str) -> int:
    """Nombre de canaux natifs d'une source PipeWire (1 si on ne sait pas)."""
    if engine() != "pulse":
        from .platform import son

        return son.source_channels(engine(), name)
    try:
        out = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True,
                             timeout=5).stdout
    except Exception:
        return 1
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 4 and parts[1] == name:
            m = re.search(r"(\d+)ch", parts[3])
            return int(m.group(1)) if m else 1
    return 1


def list_mics() -> list[dict]:
    """Micros disponibles avec leur nom lisible : [{name, label, default}]."""
    if engine() != "pulse":
        from .platform import son

        return son.list_mics(engine())
    try:
        # LC_ALL=C : en français, `pactl -f json` casse sur les accents.
        out = subprocess.run(
            ["pactl", "-f", "json", "list", "sources"], capture_output=True, text=True, timeout=5,
            env={**os.environ, "LC_ALL": "C"},
        ).stdout
        sources = json.loads(out or "[]")
        default = subprocess.run(
            ["pactl", "get-default-source"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except Exception:
        return [{"name": n, "label": n, "default": False} for n in list_sources()]
    mics = []
    for s in sources:
        name = s.get("name") or ""
        if not name or name.endswith(".monitor") or s.get("monitor_source"):
            continue
        label = " ".join(str(s.get("description") or name).split())
        mics.append({"name": name, "label": label, "default": name == default})
    return mics


def list_sinks() -> list[dict]:
    """Sorties de son (17/10, plan P4, page « Réglages ») : [{name, label}], pactl seulement."""
    if engine() != "pulse":
        return []
    try:
        out = subprocess.run(["pactl", "-f", "json", "list", "sinks"], capture_output=True, text=True, timeout=5,
                             env={**os.environ, "LC_ALL": "C"}).stdout  # LC_ALL=C : voir list_mics
        sinks = json.loads(out or "[]")
    except Exception:
        return []
    out = []
    for s in sinks:
        if isinstance(s, dict) and s.get("name"):
            desc = str(s.get("description") or "")
            out.append({"name": s["name"], "label": " ".join((desc if desc and desc != "(null)" else s["name"]).split())})
    return out


def default_sink() -> str:
    if engine() != "pulse":
        return ""
    try:
        return subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def set_default_sink(name: str) -> dict:
    """Sortie du son de TOUT le système (comme les réglages de son du bureau)."""
    if engine() != "pulse":
        return {"ok": False, "error": "choix de la sortie : Linux (PipeWire/PulseAudio) seulement pour l'instant"}
    if name not in {s["name"] for s in list_sinks()}:
        return {"ok": False, "error": f"sortie introuvable : {name or '(vide)'}"}
    try:
        proc = subprocess.run(["pactl", "set-default-sink", name], capture_output=True, text=True, timeout=5)
    except Exception as exc:
        return {"ok": False, "error": f"pactl : {exc}"}
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or "pactl a refusé").strip()}
    log.info("sortie du son : %s", name)
    return {"ok": True}


def wait_device(timeout: float = 15.0) -> bool:
    """Attend que PipeWire expose une source, utile au démarrage du PC."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if list_sources():
            return True
        time.sleep(1.0)
    return False
