"""Réglages propres à chaque micro : gain logiciel, seuil VAD, seuil du mot de réveil.

Un micro USB de proximité (le micro USB) et le micro d'un portable n'ont ni le même
niveau ni le même bruit de fond : les réglages mesurés sur l'un ratent l'autre.
Les profils vivent dans `config/mic_profiles.json` :

    {"default":  {"gain": 2.0, "vad": 0.35, "wake": 0.4},
     "profiles": {"generic": {"gain": 2.0}, "skl_hda": {"gain": 6.0, "channels": [0, 2]}}}

`channels` : pour un micro à plusieurs canaux (le micro intégré en a 4, dont un
mort et un saturé de bruit), seuls ceux-là sont gardés et moyennés.

Chaque clé de `profiles` est un morceau du nom PipeWire OU du nom lisible du
micro (sans casse, le plus long gagne). Un profil partiel complète avec
`default`. Les variables `ASTREL_MIC_GAIN`, `ASTREL_VAD_THRESHOLD` et
`ASTREL_WAKE_THRESHOLD` restent prioritaires sur tout profil.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass

from . import config

log = logging.getLogger("astrel.micprofile")

FILE = config.CONFIG_DIR / "mic_profiles.json"


@dataclass(frozen=True)
class MicProfile:
    gain: float = config.MIC_GAIN
    vad: float = config.VAD_SPEECH_THRESHOLD
    wake: float = config.WAKE_THRESHOLD
    key: str = ""  # profil retenu ; vide = réglages par défaut
    channels: tuple[int, ...] = ()  # canaux gardés (0 = premier) ; vide = mélange de PipeWire

    def describe(self) -> str:
        chans = f", canaux {'+'.join(map(str, self.channels))}" if self.channels else ""
        return f"gain ×{self.gain:g}, VAD {self.vad:.2f}, réveil {self.wake:.2f}{chans}"


def _read() -> dict:
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _num(value, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _env_override(profile: MicProfile) -> MicProfile:
    def env(name: str, current: float) -> float:
        return _num(os.environ[name], current) if name in os.environ else current

    return MicProfile(
        gain=env("ASTREL_MIC_GAIN", profile.gain),
        vad=env("ASTREL_VAD_THRESHOLD", profile.vad),
        wake=env("ASTREL_WAKE_THRESHOLD", profile.wake),
        key=profile.key,
        channels=profile.channels,
    )


def _channels(value) -> tuple[int, ...]:
    if not isinstance(value, list):
        return ()
    good = [c for c in value if isinstance(c, int) and not isinstance(c, bool) and c >= 0]
    return tuple(dict.fromkeys(good))


def resolve(source: str, label: str = "") -> MicProfile:
    """Profil du micro `source` (nom PipeWire ; `label` = nom lisible, facultatif)."""
    data = _read()
    base = data.get("default") if isinstance(data.get("default"), dict) else {}
    profiles = data.get("profiles") if isinstance(data.get("profiles"), dict) else {}
    haystack = f"{source} {label}".lower()
    matches = [k for k in profiles if k and k.lower() in haystack and isinstance(profiles[k], dict)]
    key = max(matches, key=len) if matches else ""
    chosen = {**base, **(profiles[key] if key else {})}
    default = MicProfile()
    return _env_override(MicProfile(
        gain=_num(chosen.get("gain"), default.gain),
        vad=_num(chosen.get("vad"), default.vad),
        wake=_num(chosen.get("wake"), default.wake),
        key=key,
        channels=_channels(chosen.get("channels")),
    ))


def save(source: str, label: str = "", channels: list[int] | None = None, **values: float) -> MicProfile:
    """Retient des réglages pour ce micro (clé = son nom PipeWire complet, sauf si
    un profil existant le couvre déjà, auquel cas on le met à jour)."""
    allowed = {k: round(float(v), 3) for k, v in values.items() if k in ("gain", "vad", "wake") and v is not None}
    data = _read()
    profiles = data.setdefault("profiles", {})
    key = resolve(source, label).key or source
    if channels is not None:
        allowed["channels"] = list(_channels(list(channels)))
    profiles[key] = {**(profiles.get(key) or {}), **allowed}
    data.setdefault("default", {k: v for k, v in asdict(MicProfile()).items() if k != "key"})
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(FILE)
    log.info("profil micro « %s » : %s", key, profiles[key])
    return resolve(source, label)
