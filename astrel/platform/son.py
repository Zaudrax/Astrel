"""Moteurs audio hors PipeWire, pour `astrel/audio.py` (P2, lot 5).

- `sounddevice` (PortAudio, livré DANS la roue pip sous macOS et Windows) :
  le micro et le haut-parleur du système. JAMAIS choisi d'office sous Linux :
  PortAudio y est souvent absent et faisait planter l'import (piège 1 du
  CLAUDE.md) ; Linux garde `parecord`/`paplay` ;
- `fichier` : un micro qui lit un fichier WAV (`ASTREL_AUDIO_IN`, sinon du
  silence) au rythme réel, et un haut-parleur muet (qui écrit dans
  `ASTREL_AUDIO_OUT` si demandé). Pour les tests et la CI, sans carte son.

Les objets rendus imitent juste assez un `subprocess.Popen` pour que `audio.py`
ne change pas : `stdout.read(n)` (micro), `stdin.write/flush/close` (lecteur),
`wait`, `kill`, `terminate`, `communicate`. En plus, le micro a `discard()`,
qui remplace la purge non bloquante par `fcntl` (absente de Windows).

⚠️ `sounddevice` NON VÉRIFIÉ sur un vrai Mac ou un vrai Windows au 17/10/2026.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import wave

log = logging.getLogger("astrel.audio")

ENGINES = ("pulse", "sounddevice", "fichier")


class _Fifo:
    """Tampon d'octets partagé entre le thread qui capte et celui qui lit."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._cond = threading.Condition()
        self.closed = False

    def put(self, data: bytes) -> None:
        with self._cond:
            if not self.closed:
                self._buf += data
                self._cond.notify_all()

    def read(self, n: int) -> bytes:
        """Bloque jusqu'à `n` octets ; rend moins (voire rien) si le flux est fermé."""
        with self._cond:
            while len(self._buf) < n and not self.closed:
                self._cond.wait(0.5)
            out = bytes(self._buf[:n])
            del self._buf[:n]
            return out

    def read1(self, n: int = 65536) -> bytes:
        with self._cond:
            out = bytes(self._buf[:n])
            del self._buf[:n]
            return out

    def clear(self) -> int:
        with self._cond:
            n = len(self._buf)
            self._buf.clear()
            return n

    def close(self) -> None:
        with self._cond:
            self.closed = True
            self._cond.notify_all()


class _Capture:
    """Base des micros : le tampon tient lieu de `stdout`."""

    def __init__(self) -> None:
        self.stdout = _Fifo()

    def discard(self, settle_s: float = 0.0) -> None:
        """Jette ce qui est déjà capté, puis ce qui arrive pendant `settle_s`.
        Les blocs arrivent entiers : pas d'échantillon coupé en deux."""
        self.stdout.clear()
        deadline = time.monotonic() + settle_s
        while time.monotonic() < deadline:
            time.sleep(0.02)
            self.stdout.clear()

    def _halt(self) -> None:
        pass

    def terminate(self) -> None:
        self._halt()
        self.stdout.close()

    kill = terminate

    def wait(self, timeout: float | None = None) -> int:
        return 0


class _Writer:
    """`stdin` d'un lecteur : écrire bloque tant que le son n'est pas parti."""

    def __init__(self, player: "_Player") -> None:
        self._player = player

    def write(self, data: bytes) -> int:
        if self._player.dead:
            raise BrokenPipeError("lecture coupée")
        try:
            self._player._write(bytes(data))
        except Exception as exc:  # PortAudio : flux arrêté, périphérique perdu…
            raise BrokenPipeError(str(exc)) from exc
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self._player._finish()


class _Player:
    def __init__(self) -> None:
        self.stdin: _Writer | None = _Writer(self)
        self.dead = False
        self._ending: threading.Thread | None = None

    def _write(self, data: bytes) -> None:
        raise NotImplementedError

    def _drain_and_close(self) -> None:
        pass

    def _abort(self) -> None:
        pass

    def _finish(self) -> None:
        """Fin d'écriture : le son déjà écrit finit de sortir dans un thread,
        comme un `paplay` dont on ferme l'entrée."""
        if self._ending is None and not self.dead:
            self._ending = threading.Thread(target=self._drain_and_close, name="son-fin", daemon=True)
            self._ending.start()

    def wait(self, timeout: float | None = None) -> int:
        if self._ending is not None:
            self._ending.join(timeout)
        return 0

    def kill(self) -> None:
        if not self.dead:
            self.dead = True
            self._abort()

    def communicate(self, data: bytes = b"", timeout: float | None = None):
        if data and self.stdin is not None:
            self.stdin.write(data)
        if self.stdin is not None:
            self.stdin.close()
        self.wait(timeout)
        return None, None


# --- moteur « fichier » (tests, CI) ---

def _read_wav(path: str, rate: int) -> bytes:
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != rate:
            raise ValueError(f"{path} : il faut un WAV mono 16 bits à {rate} Hz")
        return w.readframes(w.getnframes())


class FileCapture(_Capture):
    """Rend le fichier au rythme réel (blocs de 20 ms), puis du silence."""

    def __init__(self, rate: int, channels: int = 1, path: str | None = None) -> None:
        super().__init__()
        path = os.environ.get("ASTREL_AUDIO_IN", "") if path is None else path
        self._data = _read_wav(path, rate) if path else b""
        self._rate, self._channels = rate, max(1, channels)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="micro-fichier", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        step = int(self._rate * 0.02) * 2  # 20 ms en mono
        pos = 0
        while not self._stop.is_set():
            chunk = self._data[pos:pos + step]
            pos += step
            chunk += bytes(step - len(chunk))
            if self._channels > 1:  # même voix sur chaque canal
                import numpy as np

                mono = np.frombuffer(chunk, dtype=np.int16)
                chunk = np.repeat(mono, self._channels).tobytes()
            self.stdout.put(chunk)
            time.sleep(0.02)

    def _halt(self) -> None:
        self._stop.set()


class FilePlayer(_Player):
    """Haut-parleur muet ; écrit le son dans `ASTREL_AUDIO_OUT` si la variable est posée."""

    def __init__(self, rate: int) -> None:
        super().__init__()
        self.rate = rate
        self._out = os.environ.get("ASTREL_AUDIO_OUT", "")

    def _write(self, data: bytes) -> None:
        if self._out:
            with open(self._out, "ab") as f:
                f.write(data)


# --- moteur « sounddevice » (macOS, Windows) ---

def _sd():
    try:
        import sounddevice as sd
    except Exception as exc:  # PortAudio absent (Linux) ou paquet non installé
        raise RuntimeError(f"moteur audio sounddevice indisponible ({exc}) ; "
                           "pip install sounddevice, ou ASTREL_AUDIO_ENGINE=pulse sous Linux") from exc
    return sd


def _input_devices(sd) -> list[tuple[int, dict]]:
    """Entrées de l'API audio par défaut seulement : Windows liste chaque micro
    une fois par API (MME, DirectSound, WASAPI…)."""
    try:
        hostapi = sd.query_devices(kind="input")["hostapi"]
    except Exception:
        hostapi = None
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) > 0 and (hostapi is None or d.get("hostapi") == hostapi):
            out.append((i, d))
    return out


def _resolve(sd, name: str) -> int | None:
    """Nom retenu (runtime/mic.txt) → index PortAudio ; None = micro par défaut."""
    if not name:
        return None
    devices = _input_devices(sd)
    for i, d in devices:
        if d.get("name") == name:
            return i
    for i, d in devices:
        if name.lower() in str(d.get("name", "")).lower():
            return i
    log.warning("micro « %s » introuvable : micro par défaut", name)
    return None


class SoundDeviceCapture(_Capture):
    def __init__(self, rate: int, channels: int = 1, device: str = "") -> None:
        super().__init__()
        sd = _sd()

        def on_audio(indata, _frames, _time, status) -> None:
            if status:
                log.debug("micro : %s", status)
            self.stdout.put(bytes(indata))

        self._stream = sd.RawInputStream(samplerate=rate, channels=channels, dtype="int16",
                                         device=_resolve(sd, device), callback=on_audio, latency="low")
        self._stream.start()

    def _halt(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


class SoundDevicePlayer(_Player):
    def __init__(self, rate: int) -> None:
        super().__init__()
        sd = _sd()
        self.rate = rate
        self._stream = sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16")
        self._stream.start()

    def _write(self, data: bytes) -> None:
        self._stream.write(data)

    def _drain_and_close(self) -> None:
        try:
            self._stream.stop()  # attend que le son déjà écrit soit sorti
            self._stream.close()
        except Exception:
            pass

    def _abort(self) -> None:
        try:
            self._stream.abort()
            self._stream.close()
        except Exception:
            pass


# --- points d'entrée pour audio.py ---

def open_capture(engine: str, rate: int, channels: int, device: str) -> _Capture:
    if engine == "fichier":
        return FileCapture(rate, channels)
    return SoundDeviceCapture(rate, channels, device)


def open_player(engine: str, rate: int) -> _Player:
    if engine == "fichier":
        return FilePlayer(rate)
    return SoundDevicePlayer(rate)


def list_mics(engine: str) -> list[dict]:
    if engine == "fichier":
        return [{"name": "fichier", "label": "Fichier audio (tests)", "default": True}]
    sd = _sd()
    try:
        default = sd.query_devices(kind="input").get("name", "")
    except Exception:
        default = ""
    return [{"name": d["name"], "label": " ".join(str(d["name"]).split()), "default": d["name"] == default}
            for _, d in _input_devices(sd)]


def source_channels(engine: str, name: str) -> int:
    if engine == "fichier":
        return 1
    try:
        sd = _sd()
        idx = _resolve(sd, name)
        info = sd.query_devices(idx if idx is not None else sd.default.device[0])
        return int(info.get("max_input_channels") or 1)
    except Exception:
        return 1
