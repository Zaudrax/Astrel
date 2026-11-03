"""Ce que la bulle fait sans interface graphique (03/11/2026, portage Windows et macOS).

La bulle GTK de Linux (`bubble.py`, réglée finement, relancée par l'utilisateur seulement) garde son code
tel quel ; la bulle Qt de Windows et macOS (`bulle_qt.py`) prend ici les mêmes morceaux, recopiés
sans GTK : flux du cœur (état, niveau sonore, mini-chat, questions), réponse du cœur à une carte,
place visible de la bulle, verrou d'une seule bulle, apparence de repli."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from typing import Callable

from . import bubble_premium, config, state
from .minichat import MiniChat

SIZE = 148
COMPACT = 52
FPS = 60
CALME_PAS = 4  # au repos stable : une image sur 4 (15 images/s)
FADE_STEP = 0.06  # par image : fondu d'un état à l'autre en ≈ 0,3 s
POS_FILE = config.RUNTIME / "bubble.json"
LOCK_FILE = config.RUNTIME / "bubble.lock"


def fallback_look() -> bubble_premium.Look:
    """Premium sombre, accent bleu, si config/themes.json est illisible (comme `bubble._fallback_look`)."""
    accent, gray, warn = (10 / 255, 132 / 255, 1.0), (174 / 255, 174 / 255, 178 / 255), (1.0, 214 / 255, 10 / 255)
    states = {s: accent for s in (state.SLEEPING, state.LISTENING, state.THINKING, state.WORKING, state.SPEAKING)}
    states.update({state.BOOTING: gray, state.OFF: gray, state.ASKING: warn})
    return bubble_premium.Look(fill=(44 / 255, 44 / 255, 46 / 255), fill_alpha=0.92, line=(1.0, 1.0, 1.0),
                               line_alpha=0.12, shade_alpha=0.35, states=states, warn=warn,
                               ok=(48 / 255, 209 / 255, 88 / 255), ink_2=gray, ink=(245 / 255, 245 / 255, 247 / 255))


class Feed:
    """État et niveau sonore d'Astrel, mini-chat et questions, reçus du HUD dans un fil à part.
    `on_change` est appelé DANS CE FIL : l'interface le renvoie vers son propre fil."""

    def __init__(self) -> None:
        self.state = state.OFF
        self.muted = False
        self.paused = False
        self.detail = ""
        self.level = 0.0
        self.connected = False
        self.chat = MiniChat()
        self.closed_confirms: dict[str, None] = {}
        self.on_change: Callable[[], None] = lambda: None
        threading.Thread(target=self._run, name="bubble-feed", daemon=True).start()

    def _run(self) -> None:
        while True:
            try:
                asyncio.run(self._listen())
            except Exception:
                pass
            self.connected = False
            self._from_file()
            time.sleep(1.0)

    def _from_file(self) -> None:
        data = state.read_state()
        self.state = data.get("state", state.OFF)
        self.muted = bool(data.get("muted"))
        self.paused = bool(data.get("paused"))
        self.detail = data.get("detail", "")

    async def _listen(self) -> None:
        import aiohttp

        token = config.HUD_TOKEN_FILE.read_text(encoding="utf-8").strip()
        url = f"http://{config.HUD_HOST}:{config.HUD_PORT}/ws?t={token}"
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url, heartbeat=20) as ws:
                self.connected = True
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    ev = json.loads(msg.data)
                    if ev.get("type") == "hello":
                        for sticky in ev.get("sticky", []):
                            self.apply(sticky)
                        for old in ev.get("history", []):
                            self.apply({**old, "replay": True})
                    else:
                        self.apply(ev)

    def apply(self, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "confirm" and not ev.get("pending"):
            self.closed_confirms[str(ev.get("id") or "")] = None
            while len(self.closed_confirms) > 50:
                self.closed_confirms.pop(next(iter(self.closed_confirms)))
        if self.chat.apply(ev):
            self.on_change()
        if kind == "state":
            self.state = ev.get("state", self.state)
            self.muted = bool(ev.get("muted"))
            self.paused = bool(ev.get("paused"))
            self.detail = ev.get("detail", "")
        elif kind == "level":
            if ev.get("source") == "voice" or self.state in (state.LISTENING, state.ASKING):
                self.level = float(ev.get("value", 0.0))


def answer_refusal(reply: str | None) -> str:
    """Réponse du cœur à une réponse de la carte : "" si elle est prise, sinon la raison."""
    if not reply:
        return "Astrel ne répond pas : réponse non transmise, réessaie ou réponds dans le HUD"
    try:
        res = json.loads(reply)
    except ValueError:
        return "réponse illisible d'Astrel : réponds dans le HUD"
    if isinstance(res, dict) and res.get("ok") is True:
        return ""
    err = str(res.get("error") or "") if isinstance(res, dict) else ""
    if "refusée à Claude" in err:
        return "refusé : cette bulle a été lancée par Astrel ; relance-la depuis ta session ou réponds dans le HUD"
    return err or "réponse refusée par Astrel"


def visible_position(x: int, y: int, size: int, monitors: list[tuple[int, int, int, int]],
                     home: tuple[int, int] | None) -> tuple[int, int] | None:
    """Place de la bulle ramenée dans la zone visible (comme `bubble.visible_position`)."""
    if not monitors:
        return None
    cx, cy = x + size // 2, y + size // 2
    for gx, gy, gw, gh in monitors:
        if gx <= cx < gx + gw and gy <= cy < gy + gh:
            return max(gx, min(x, gx + gw - size)), max(gy, min(y, gy + gh - size))
    return home


def lire_position() -> tuple[int, int] | None:
    try:
        pos = json.loads(POS_FILE.read_text(encoding="utf-8"))
        return int(pos["x"]), int(pos["y"])
    except Exception:
        return None


def ecrire_position(x: int, y: int) -> None:
    try:
        POS_FILE.write_text(json.dumps({"x": x, "y": y}), encoding="utf-8")
    except Exception:
        pass


def seule_bulle() -> int | None:
    """Verrou de `LOCK_FILE`, tenu jusqu'à la sortie du processus : son descripteur, ou None si une
    autre bulle le tient déjà (comme `bubble.seule_bulle`)."""
    config.RUNTIME.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            import fcntl
        except ImportError:  # Windows : verrou d'un octet, non bloquant
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd
