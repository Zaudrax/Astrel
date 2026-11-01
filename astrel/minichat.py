"""Ce que la BULLE montre sans ouvrir le HUD (14/10, demande de l'utilisateur : son
HUD n'est pas toujours ouvert) : les derniers échanges, pour un mini-chat, et
la confirmation en attente, pour y répondre d'un clic.

Logique pure, sans GTK : la bulle (`bubble.py`) l'alimente avec les événements
du bus reçus par WebSocket et ne fait que l'afficher. Testable sans écran.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass


@dataclass
class Line:
    who: str           # « user » ou « astrel »
    text: str
    mid: str = ""
    live: bool = False  # réponse en cours d'écriture


class MiniChat:
    """Les derniers messages (tous canaux confondus) et la question en attente."""

    def __init__(self, size: int = 8) -> None:
        self.lock = threading.Lock()
        self.lines: deque[Line] = deque(maxlen=size)
        self.confirm: dict | None = None  # événement « confirm » en attente
        self.usage = "conversation"
        self.total = False  # 15/10 : mode « Autonome TOTAL » actif (indicateur rouge sur la bulle)

    def _find(self, mid: str) -> Line | None:
        return next((ln for ln in self.lines if mid and ln.mid == mid), None)

    def apply(self, ev: dict) -> bool:
        """Intègre un événement du bus. Vrai si l'affichage doit changer."""
        kind = ev.get("type")
        with self.lock:
            if kind == "transcript":
                if self._find(str(ev.get("mid") or "")):
                    return False
                self._close_live()
                self.lines.append(Line("user", str(ev.get("text") or ""), str(ev.get("mid") or "")))
                return True
            if kind == "reply_delta":
                last = self.lines[-1] if self.lines else None
                if last is None or not last.live:
                    last = Line("astrel", "", live=True)
                    self.lines.append(last)
                last.text += str(ev.get("text") or "")
                return True
            if kind in ("reply", "report"):
                mid = str(ev.get("mid") or "")
                text = str(ev.get("text") or "")
                if self._find(mid):
                    return False
                last = self.lines[-1] if self.lines else None
                if kind == "reply" and last is not None and last.live and not ev.get("replay"):
                    last.text, last.mid, last.live = text or last.text, mid, False
                elif text:
                    self.lines.append(Line("astrel", text, mid))
                return True
            if kind == "confirm":
                if ev.get("pending") and not ev.get("replay"):
                    self.confirm = dict(ev)
                    return True
                if not ev.get("pending") and self.confirm and self.confirm.get("id") == ev.get("id"):
                    self.confirm = None
                    return True
                return False
            if kind == "usage":
                self.usage = str(ev.get("mode") or "conversation")
                return True
            if kind == "total":
                self.total = bool(ev.get("on"))
                return True
        return False

    def _close_live(self) -> None:
        for ln in self.lines:
            ln.live = False

    def snapshot(self) -> list[Line]:
        with self.lock:
            return [Line(ln.who, ln.text, ln.mid, ln.live) for ln in self.lines]

    def answer_command(self, allow: bool, always: bool = False) -> dict | None:
        """Commande à envoyer au cœur pour répondre à la question affichée."""
        with self.lock:
            if not self.confirm:
                return None
            cid = self.confirm.get("id")
            self.confirm = None
        return {"cmd": "confirm", "confirm_id": cid, "allow": allow, "always": allow and always}
