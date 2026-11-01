"""Reprise après coupure (15/10, idée 6 validée par l'utilisateur).

Trois règles, toutes tournées contre la DOUBLE EXÉCUTION :

1. Limite atteinte (5 h, hebdomadaire) : l'heure de réinitialisation est gardée
   dans `runtime/reprise.json` et UN réflexe `reprise-limite` (déclencheur `at`,
   à `reset_at` + `REPRISE_DELAY_S`, action `ask`) est posé. Le fichier fait foi,
   pas le réflexe : il n'y a qu'une reprise par limite, même après une relance
   d'Astrel, et elle est effacée AVANT de partir. L'utilisateur qui reprend la
   conversation entre-temps l'annule.
2. Fenêtre fermée (de la limite à `reset_at`) : les réflexes `ask` sont mis en
   attente dans le même fichier (pas perdus) et relancés un par un une fois la
   reprise partie ; les réflexes sans modèle continuent.
3. Panne passagère (529, 5xx, réseau) : le cœur rejoue le tour 15, 30 puis 60 s
   plus tard SEULEMENT si rien n'a été dit, écrit ni exécuté (`classify` ne
   range jamais une limite ni une erreur d'authentification en « passagère »).

Ce module ne parle ni au cerveau ni au HUD : le cœur (`core.py`) décide.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta

from . import config

log = logging.getLogger("astrel.reprise")

REFLEX_ID = "reprise-limite"
# 19/10 (plan P8) : chez un ami, la note n'existe pas forcément (elle n'est écrite qu'à l'alerte
# de limite proche) : repli sur l'historique récent.
PROMPT = ("La limite est levée : reprends depuis la note CHANTIERS-EN-COURS, en vérifiant d'abord l'état "
          "réel (git status, agents, fichiers) avant de relancer quoi que ce soit ; ne rejoue aucune "
          "commande déjà faite. Si la note est absente, relis l'historique récent (history_search).")
HELD_MAX = 20  # réflexes gardés pendant la fenêtre fermée (les plus anciens tombent)

# --------------------------------------------------------------------------- erreurs

# Texte réel du CLI vu dans logs/astrel.log (09/10) : « You've hit your session
# limit · resets 4:10pm (Europe/Paris) ». Ancien format : « Claude AI usage limit
# reached|1760000000 ». Un 429 est rangé en LIMITE : jamais rejoué.
_LIMIT_RE = re.compile(r"hit your (?:[\w-]+ )*limit|usage limit|limit reached|rate[ _-]?limit|\b429\b"
                       r"|out of (?:extra )?usage", re.I)
_AUTH_RE = re.compile(r"authenticat|unauthori[sz]ed|\b401\b|\b403\b|invalid (?:api|x-api) key|/login"
                      r"|oauth|token (?:has )?expired|not logged in|log ?in again|permission_error|forbidden",
                      re.I)
_TRANSIENT_RE = re.compile(r"\b5\d\d\b|overloaded|api_error|internal server error|bad gateway"
                           r"|service unavailable|gateway time-?out|connection (?:error|reset|refused|closed)"
                           r"|econnreset|econnrefused|etimedout|enotfound|eai_again|socket hang up"
                           r"|fetch failed|network|request timed out|timed out", re.I)


def classify(text: str) -> str:
    """« limit », « auth », « transient » ou vide (erreur inconnue : rien de rejoué).
    L'ordre compte : une limite ou un refus d'accès n'est jamais « passager »."""
    t = str(text or "")
    if _LIMIT_RE.search(t):
        return "limit"
    if _AUTH_RE.search(t):
        return "auth"
    if _TRANSIENT_RE.search(t):
        return "transient"
    return ""


def limit_kind(text: str) -> str:
    """Fenêtre nommée par le texte d'erreur (mêmes noms que `brain.LIMIT_KINDS`)."""
    t = str(text or "").lower()
    if "weekly" in t or "week" in t:
        return "hebdomadaire_opus" if "opus" in t else "hebdomadaire"
    if "session" in t or "5-hour" in t or "five" in t:
        return "cinq_heures"
    return ""


_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                       "nov", "dec"), start=1)}
_RESET_RE = re.compile(
    r"resets?\s+(?:at\s+|on\s+)?(?:(?P<mon>[A-Za-z]{3})[a-z]*\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+"
    r"(?:at\s+)?)?(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)?\s*(?:\((?P<tz>[A-Za-z_]+/[A-Za-z_/+-]+|UTC)\))?",
    re.I)
_EPOCH_RE = re.compile(r"limit reached\|(\d{9,13})", re.I)


def parse_reset(text: str, now: float | None = None) -> float:
    """Heure de réinitialisation lue dans le texte d'erreur du CLI, en epoch s
    (0.0 si illisible) : « resets 4:10pm (Europe/Paris) », « resets 7pm »,
    « resets Oct 20, 9am », « limit reached|1760000000 ». Une heure sans date
    est la PROCHAINE occurrence après `now`."""
    now = time.time() if now is None else now
    t = str(text or "")
    m = _EPOCH_RE.search(t)
    if m:
        v = float(m.group(1))
        return v / 1000 if v > 1e12 else v
    m = _RESET_RE.search(t)
    if not m:
        return 0.0
    hour, minute = int(m.group("h")), int(m.group("m") or 0)
    ap = (m.group("ap") or "").lower()
    if ap == "pm" and hour < 12:
        hour += 12
    elif ap == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return 0.0
    tz = None
    if m.group("tz"):
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(m.group("tz"))
        except Exception:
            tz = None
    base = datetime.fromtimestamp(now, tz) if tz else datetime.fromtimestamp(now)
    mon = _MONTHS.get((m.group("mon") or "").lower()[:3])
    try:
        if mon and m.group("day"):
            when = base.replace(month=mon, day=int(m.group("day")), hour=hour, minute=minute, second=0, microsecond=0)
            if when.timestamp() < now - 86400:
                when = when.replace(year=when.year + 1)  # « Jan 2 » lu fin décembre
        else:
            when = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if when.timestamp() <= now:
                when += timedelta(days=1)
    except ValueError:
        return 0.0
    return when.timestamp()


_WEEKDAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


def heure(ts: float, now: float | None = None) -> str:
    """« 16 h 10 », précédé du jour s'il n'est pas aujourd'hui (« lundi 9 h 00 »)."""
    now = time.time() if now is None else now
    t, today = time.localtime(ts), time.localtime(now)
    day = "" if (t.tm_year, t.tm_yday) == (today.tm_year, today.tm_yday) else _WEEKDAYS[t.tm_wday] + " "
    return f"{day}{t.tm_hour} h {t.tm_min:02d}"


def reflex_rule(reset_at: float) -> dict:
    """Le réflexe de reprise : UN seul (id fixe, `create_rule` remplace le précédent)."""
    return {"id": REFLEX_ID, "name": f"Reprise après la limite ({heure(reset_at)})",
            "when": {"type": "at", "ts": round(reset_at + config.REPRISE_DELAY_S)},
            "do": {"type": "ask", "prompt": PROMPT}}


# --------------------------------------------------------------------------- état

class Reprise:
    """État de `runtime/reprise.json`, relu à chaque appel (le fichier fait foi,
    une relance d'Astrel le retrouve tel quel). Champs : `pending` (une reprise
    reste à lancer), `limit_at`, `reset_at` (epoch s), `limit_kind`, `held`
    (réflexes gelés : `{text, channel, reflex, at}`)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()

    @staticmethod
    def _read() -> dict:
        try:
            data = json.loads(config.REPRISE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _write(data: dict) -> None:
        path = config.REPRISE_FILE
        if not data.get("pending") and not data.get("held"):
            path.unlink(missing_ok=True)  # plus rien à reprendre : nettoyé
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)  # jamais de fichier à moitié écrit après une coupure

    def info(self) -> dict:
        with self._lock:
            return self._read()

    def closed(self, now: float | None = None) -> bool:
        """Fenêtre fermée : une limite est enregistrée et sa réinitialisation n'est pas passée."""
        now = time.time() if now is None else now
        d = self.info()
        return bool(d.get("pending")) and now < float(d.get("reset_at") or 0)

    def due(self, now: float | None = None) -> bool:
        """La reprise peut partir (limite levée, pas encore lancée ni annulée)."""
        now = time.time() if now is None else now
        d = self.info()
        return bool(d.get("pending")) and now >= float(d.get("reset_at") or 0)

    def on_limit(self, reset_at: float, kind: str = "", now: float | None = None) -> bool:
        """Enregistre une limite. Faux si rien ne change (même limite revue : les
        tours de fond qui échouent l'un après l'autre ne font qu'UNE reprise)."""
        now = time.time() if now is None else now
        if not reset_at or reset_at <= now:
            return False
        with self._lock:
            d = self._read()
            if d.get("pending"):
                if reset_at <= float(d.get("reset_at") or 0) + 1:
                    return False  # déjà connue (ou plus tôt : la fenêtre la plus longue l'emporte)
            else:
                d["limit_at"] = now
            d.update(pending=True, reset_at=float(reset_at), limit_kind=str(kind or d.get("limit_kind") or ""))
            self._write(d)
            return True

    def launch(self, now: float | None = None) -> bool:
        """Prend la reprise : vrai UNE seule fois, et l'état est effacé AVANT que le
        cœur n'envoie quoi que ce soit (une coupure juste après ne la relance pas)."""
        now = time.time() if now is None else now
        with self._lock:
            d = self._read()
            if not d.get("pending") or now < float(d.get("reset_at") or 0):
                return False
            d["pending"] = False
            self._write(d)
            return True

    def cancel(self) -> bool:
        """l'utilisateur a repris la main : plus de reprise automatique (les réflexes gelés restent)."""
        with self._lock:
            d = self._read()
            if not d.get("pending"):
                return False
            d["pending"] = False
            self._write(d)
            return True

    def hold(self, text: str, channel: str = "", reflex: str = "", now: float | None = None) -> bool:
        """Gèle un réflexe `ask` pendant la fenêtre fermée. Faux si elle est ouverte.
        Un même réflexe (id) n'est gardé qu'une fois : sa dernière demande."""
        now = time.time() if now is None else now
        with self._lock:
            if not self.closed(now):
                return False
            d = self._read()
            held = [h for h in d.get("held") or [] if isinstance(h, dict)]
            held = [h for h in held if not ((reflex and h.get("reflex") == reflex) or h.get("text") == text)]
            held.append({"text": text, "channel": channel, "reflex": reflex, "at": now})
            d["held"] = held[-HELD_MAX:]
            self._write(d)
            return True

    def held_count(self) -> int:
        return len(self.info().get("held") or [])

    def take_held(self) -> dict | None:
        """Le plus ancien réflexe gelé, retiré du fichier (relancé une seule fois)."""
        with self._lock:
            d = self._read()
            held = list(d.get("held") or [])
            if not held:
                return None
            item = held.pop(0)
            d["held"] = held
            self._write(d)
            return item if isinstance(item, dict) else None

    def forecast(self, now: float | None = None) -> str:
        """Texte pour le champ `forecast` de l'évènement quota pendant la fenêtre fermée."""
        now = time.time() if now is None else now
        if not self.closed(now):
            return ""
        return f"limite atteinte, reprise prévue à {heure(float(self.info()['reset_at']), now)}"


state = Reprise()
