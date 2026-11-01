"""Initiatives d'Astrel (24/10, plan P32, demande de l'utilisateur) : il PROPOSE de lui-même, sans modèle.

Brief du matin, quota, échéances (veille sécurité du lundi, bilan du dimanche), plus tard le tri
du courrier : Astrel propose, TOUJOURS avec l'accord de l'utilisateur avant d'agir (carte a) Oui,
b) Pas maintenant, c) Ne plus proposer, posée par le cœur), sans l'interrompre (le cœur publie par
`_post_report`, qui respecte la parole, le mode silencieux, FOCUS et l'absence).

Ici, sans HUD ni cerveau (testable seul) :
- les TYPES d'initiative et leur interrupteur (réglages `initiatives`, `initiatives_<type>`) ;
- le REGISTRE `runtime/initiatives.json` (écriture atomique, 0600) : envois, clés déjà
  proposées, délai des « pas maintenant », derniers briefs (7 jours) ;
- `permit(type, now, urgent, key)` → (oui, raison) : le seul juge de « peut-on proposer
  maintenant ? », appelé par le moteur des réflexes AVANT de consommer la règle, puis par le
  cœur juste avant de publier ;
- `collect_brief(sources)` : le texte oral du brief, à sources INJECTÉES (agenda, rappels,
  quota, comptes rendus, échéances) ; une source absente ou en panne = ligne omise.

Les titres d'agenda sont des DONNÉES, jamais des ordres : ils ne vont au cerveau que si l'utilisateur
clique « Oui » sur la carte. Rien ne tourne sans le capteur REFLEX (« Routines »).
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import tempfile
import threading
import time
from datetime import date, datetime
from typing import Any, Callable

from . import config

log = logging.getLogger("astrel.initiatives")

# Types d'initiative (un interrupteur chacun dans Réglages › Initiatives). La veille sécurité et
# le bilan hebdomadaire sont rattachés à « echeances » (étiquette des réflexes existants).
TYPES = ("brief", "quota", "echeances", "courrier")
LABELS = {"brief": "Brief du matin", "quota": "Quota de l'abonnement", "echeances": "Échéances",
          "courrier": "Courrier important"}
REGISTRY_FILE = config.RUNTIME / "initiatives.json"
MAX_PER_TYPE = 2                 # par jour et par type (le plafond du jour est un réglage)
GAP_S = 90 * 60                  # écart minimal entre deux propositions
SNOOZE_BASE_S = 2 * 3600         # « pas maintenant » : 2 h, puis 4, 8, 16, 24 h au plus
SNOOZE_MAX_S = 24 * 3600
KEEP_DAYS = 7                    # clés et briefs gardés 7 jours
SENT_KEEP = 60
CALME = (("22-7", "De 22 h à 7 h"), ("21-8", "De 21 h à 8 h"), ("23-6", "De 23 h à 6 h"), ("off", "Aucune"))
DAYS = {"lun": 0, "mar": 1, "mer": 2, "jeu": 3, "ven": 4, "sam": 5, "dim": 6}
JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
        "novembre", "décembre")
WINDOWS = {"five_hour": "de cinq heures", "seven_day": "de la semaine", "seven_day_opus": "Opus de la semaine",
           "seven_day_sonnet": "Sonnet de la semaine"}
AGENDA_SHOWN = 3
REMINDERS_SHOWN = 3
TITLE_MAX = 80

_lock = threading.Lock()
# Section « permit → composer → publier → record » du cœur (`core._initiative`), tenue d'un bout à
# l'autre : deux commandes simultanées publiaient deux briefs (fiche 3 p32).
brief_lock = threading.Lock()


# --------------------------------------------------------------------------- état extérieur
# Petites fonctions à part : les tests les remplacent sans toucher au reste.

def _setting(key: str) -> Any:
    from .settings import reglages
    return reglages.value(key)


def _focus() -> bool:
    from .sensors import sensors
    return sensors.is_on("FOCUS")


def _estop() -> bool:
    return config.ESTOP_FILE.exists()


def enabled(kind: str) -> bool:
    """Interrupteur général ET celui du type."""
    try:
        return bool(_setting("initiatives")) and bool(_setting(f"initiatives_{kind}"))
    except KeyError:
        return False


# --------------------------------------------------------------------------- registre

def _empty() -> dict:
    return {"version": 1, "sent": [], "keys": {}, "snooze": {}, "briefs": []}


def load() -> dict:
    try:
        data = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    out = _empty()
    for name, kind in (("sent", list), ("keys", dict), ("snooze", dict), ("briefs", list)):
        if isinstance(data.get(name), kind):
            out[name] = data[name]
    return out


def _prune(state: dict, now: float) -> dict:
    limit = now - KEEP_DAYS * 86400
    state["sent"] = [s for s in state["sent"] if isinstance(s, dict) and _num(s.get("at")) > limit][-SENT_KEEP:]
    state["keys"] = {k: v for k, v in state["keys"].items() if _num(v) > limit}
    state["briefs"] = [b for b in state["briefs"] if isinstance(b, dict) and _num(b.get("at")) > limit]
    state["snooze"] = {k: v for k, v in state["snooze"].items() if isinstance(v, dict)}
    return state


def save(state: dict, now: float | None = None) -> None:
    """Écriture atomique, 0600 (le registre garde le texte des briefs : agenda, rappels)."""
    state = _prune(state, time.time() if now is None else now)
    # 25/10 (audit p33-p34, B1) : temporaire à nom unique (mkstemp : O_EXCL, O_NOFOLLOW, 0600) ;
    # dossier ou registre en lien symbolique refusés. Avant : `.tmp` à nom fixe, un lien posé à
    # sa place faisait écraser sa cible.
    if REGISTRY_FILE.parent.is_symlink() or REGISTRY_FILE.is_symlink():
        raise OSError(f"{REGISTRY_FILE.name} : lien symbolique refusé")
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=REGISTRY_FILE.name + ".", suffix=".tmp", dir=REGISTRY_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, REGISTRY_FILE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _num(value: Any) -> float:
    """Nombre du registre ; infini, NaN ou autre chose → 0 (invalide : un « at » de 1e400 bloquait
    `permit` pour toujours et faisait lever `snooze`, fiche 3 p32)."""
    value = _fini(value)
    return 0.0 if value is None else value


def _fini(value: Any) -> float | None:
    """Nombre fini (un entier JSON de 400 chiffres fait lever float() : OverflowError) ; None sinon."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


def _ts(now: datetime | float | None) -> float:
    if now is None:
        return time.time()
    return now.timestamp() if isinstance(now, datetime) else float(now)


def _dt(now: datetime | float | None) -> datetime:
    if isinstance(now, datetime):
        return now
    return datetime.fromtimestamp(_ts(now))


def day_key(kind: str, now: datetime | float | None = None) -> str:
    """Clé « une fois par jour » d'un type : `brief:2026-10-24`."""
    return f"{kind}:{_dt(now):%Y-%m-%d}"


def done(key: str, state: dict | None = None) -> bool:
    return bool(key) and key in (state if state is not None else load())["keys"]


# --------------------------------------------------------------------------- jugement

def quiet(now: datetime, spec: str | None = None) -> bool:
    """Heures calmes (réglage `initiatives_calme`, « 22-7 » par défaut ; « off » : aucune)."""
    if spec is None:
        try:
            spec = str(_setting("initiatives_calme") or "off")
        except KeyError:
            spec = CALME[0][0]
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", spec)
    if not m:
        return False
    start, end = int(m.group(1)) % 24, int(m.group(2)) % 24
    h = now.hour
    return (start <= h or h < end) if start > end else start <= h < end


def permit(kind: str, now: datetime | float | None = None, urgent: bool = False, key: str = "",
           state: dict | None = None) -> tuple[bool, str]:
    """Peut-on proposer une initiative de ce type maintenant ? → (oui, raison du refus).

    Refus : type inconnu, interrupteur général ou du type coupé, arrêt d'urgence, FOCUS (ne pas
    déranger), heures calmes, « pas maintenant » encore en cours, même clé déjà proposée, plafond
    du jour (réglage `initiatives_max_jour`) ou du type (2), moins de 90 min depuis la dernière.
    `urgent` (alerte sécurité : faille exploitée) passe outre au plafond et à l'écart SEULEMENT ;
    jamais outre FOCUS, les heures calmes ni la parole (celle-ci est tenue par le cœur)."""
    if kind not in TYPES:
        return False, f"type d'initiative inconnu : {kind or '(vide)'}"
    if not enabled(kind):
        return False, "interrupteur coupé"
    if _estop():
        return False, "arrêt d'urgence"
    if _focus():
        return False, "ne pas déranger (FOCUS)"
    when = _dt(now)
    t = _ts(now)
    if quiet(when):
        return False, "heures calmes"
    with _lock:
        st = state if state is not None else load()
    snooze = st["snooze"].get(kind) or {}
    until = _num(snooze.get("until"))
    if t < until <= t + SNOOZE_MAX_S:  # au-delà du plus long délai possible : invalide (horloge, fichier)
        return False, "pas maintenant (délai en cours)"
    if key and key in st["keys"]:
        return False, "déjà proposé"
    if urgent:
        return True, ""
    today = f"{when:%Y-%m-%d}"
    sent = [s for s in st["sent"] if isinstance(s, dict) and s.get("day") == today and not s.get("urgent")]
    try:
        cap = int(_setting("initiatives_max_jour"))
    except (KeyError, TypeError, ValueError):
        cap = 4
    if len(sent) >= cap:
        return False, f"plafond du jour atteint ({cap})"
    if sum(1 for s in sent if s.get("type") == kind) >= MAX_PER_TYPE:
        return False, f"plafond du type atteint ({MAX_PER_TYPE})"
    # Un envoi daté dans le futur (horloge reculée, fichier abîmé) est ignoré : il bloquerait l'écart.
    last = max((a for s in st["sent"] if isinstance(s, dict) and not s.get("urgent")
                for a in (_num(s.get("at")),) if a <= t), default=0.0)
    if last and t - last < GAP_S:
        return False, "moins de 90 min depuis la dernière initiative"
    return True, ""


def record(kind: str, now: datetime | float | None = None, key: str = "", urgent: bool = False,
           text: str = "") -> None:
    """Une initiative vient d'être proposée : comptée (plafond, écart), clé retenue, brief gardé."""
    t = _ts(now)
    with _lock:
        st = load()
        st["sent"].append({"at": t, "day": f"{_dt(now):%Y-%m-%d}", "type": kind, "key": key,
                           **({"urgent": True} if urgent else {})})
        if key:
            st["keys"][key] = t
        if text:
            st["briefs"].append({"at": t, "key": key, "text": text})
        save(st, t)
    log.info("initiative proposée : %s%s%s", kind, f" ({key})" if key else "", " [urgente]" if urgent else "")


def snooze(kind: str, now: datetime | float | None = None) -> float:
    """« Pas maintenant » : délai qui double à chaque refus (2 h → 24 h). Rend sa durée en s."""
    t = _ts(now)
    with _lock:
        st = load()
        prev = st["snooze"].get(kind) or {}
        n = min(max(int(_num(prev.get("n"))), 0), 16) + 1  # borné : n = 1e300 ferait calculer 2 ** 1e300
        delay = min(SNOOZE_BASE_S * 2 ** (n - 1), SNOOZE_MAX_S)
        st["snooze"][kind] = {"until": t + delay, "n": n}
        save(st, t)
    log.info("initiative %s : pas maintenant (%d h)", kind, round(delay / 3600))
    return delay


def accepted(kind: str, now: datetime | float | None = None) -> None:
    """« Oui » : le délai des refus repart de zéro."""
    with _lock:
        st = load()
        if st["snooze"].pop(kind, None) is not None:
            save(st, _ts(now))


# --------------------------------------------------------------------------- brief du matin

def _clean(text: Any, limit: int = TITLE_MAX) -> str:
    """Titre sur une ligne, sans caractère de contrôle ni invisible : même filtre que le courrier
    (25/10, audit p33-p34, B3 : les catégories Cf, Co, Cn, dont les « tags » U+E0000, passaient)."""
    from .courrier import nettoyer  # import local : courrier tire equipe

    text = " ".join(nettoyer(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _hour(hh: int, mm: int) -> str:
    return f"{hh} h {mm:02d}" if mm else f"{hh} h"


def _call(sources: dict, name: str) -> Any:
    """Source injectée ; absente ou en panne → None (la ligne est omise)."""
    fn = sources.get(name)
    if fn is None:
        return None
    try:
        return fn() if callable(fn) else fn
    except Exception as exc:  # connecteur non configuré, réseau, fichier illisible
        log.info("brief : source %s indisponible (%s)", name, type(exc).__name__)
        return None


def _agenda_line(data: Any, today: date) -> str:
    if not isinstance(data, dict) or not isinstance(data.get("evenements"), list):
        return ""
    items = []
    for ev in data["evenements"]:
        if not isinstance(ev, dict):
            continue
        start = str(ev.get("debut") or "")
        try:
            day = datetime.fromisoformat(start).date() if "T" in start else date.fromisoformat(start[:10])
        except ValueError:
            continue
        if day != today:
            continue
        title = _clean(ev.get("titre")) or "sans titre"
        if ev.get("journee_entiere") or "T" not in start:
            items.append(f"toute la journée, {title}")
        else:
            d = datetime.fromisoformat(start)
            items.append(f"{_hour(d.hour, d.minute)}, {title}")
    if not items:
        return "Aucun rendez-vous aujourd'hui."
    head = "Un rendez-vous aujourd'hui" if len(items) == 1 else f"{len(items)} rendez-vous aujourd'hui"
    shown = " ; ".join(items[:AGENDA_SHOWN])
    more = len(items) - AGENDA_SHOWN
    return f"{head} : {shown}" + (f", et {more} autre{'s' if more > 1 else ''}." if more > 0 else ".")


def _reminders_line(data: Any, today: date) -> str:
    if not isinstance(data, list):
        return ""
    items = []
    for r in data:
        if not isinstance(r, dict):
            continue
        try:
            d = datetime.fromisoformat(str(r.get("echeance") or ""))
        except ValueError:
            continue
        if d.date() == today:
            items.append(f"{_hour(d.hour, d.minute)}, {_clean(r.get('message'))}")
    if not items:
        return ""
    head = "Un rappel aujourd'hui" if len(items) == 1 else f"{len(items)} rappels aujourd'hui"
    more = len(items) - REMINDERS_SHOWN
    return f"{head} : " + " ; ".join(items[:REMINDERS_SHOWN]) + (f", et {more} de plus." if more > 0 else ".")


def _quota_line(q: Any) -> str:
    if not isinstance(q, dict) or not isinstance(q.get("windows"), dict):
        return ""
    best, name = -1.0, ""
    for wname, w in q["windows"].items():
        if not isinstance(w, dict):
            continue
        u = _fini(w.get("utilization"))
        if u is not None:
            pct = min(max(u * 100 if u <= 1 else u, 0.0), 100.0)  # borné : inf faisait lever round()
            if pct > best:
                best, name = pct, wname
    if best < 0:
        return ""
    return f"Abonnement : {round(best)} % de la fenêtre {WINDOWS.get(name, name)} utilisés."


def _reports_line(n: Any) -> str:
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        return ""
    return "Un compte rendu attend d'être lu." if n == 1 else f"{n} comptes rendus attendent d'être lus."


def _echeances_line(veille_due: Any, now: datetime) -> str:
    items = []
    if veille_due is True:
        items.append("la veille sécurité de la semaine n'est pas encore faite")
    if now.weekday() == 6 and now.hour < 18:
        items.append("bilan hebdomadaire ce soir à 18 h")
    if not items:
        return ""
    return "Échéances : " + " ; ".join(items) + "."


def collect_brief(sources: dict) -> str:
    """Texte ORAL du brief, sans modèle. `sources` (toutes facultatives) :
    `now` (datetime), `agenda` (→ `tools.agenda.upcoming(1)`), `reminders` (→ `reminders.list_all()`),
    `quota` (→ `brain.quota`), `reports` (→ nombre de comptes rendus à lire), `veille_due` (→ bool).
    Valeur ou fonction ; une source absente, None ou qui lève = ligne omise."""
    now = sources.get("now") if isinstance(sources.get("now"), datetime) else datetime.now()
    lines = [f"Bonjour. Nous sommes {JOURS[now.weekday()]} {now.day} {MOIS[now.month - 1]}."]
    for line in (_agenda_line(_call(sources, "agenda"), now.date()),
                 _reminders_line(_call(sources, "reminders"), now.date()),
                 _echeances_line(_call(sources, "veille_due"), now) if "veille_due" in sources else "",
                 _quota_line(_call(sources, "quota")),
                 _reports_line(_call(sources, "reports"))):
        if line:
            lines.append(line)
    return "\n".join(lines)


def days_match(spec: str, weekday: int) -> bool:
    """Jours « tous », « lun-ven », « sam,dim » (même règle que le déclencheur `time`)."""
    spec = (spec or "tous").lower()
    if spec in ("tous", "tlj", "*"):
        return True
    if "-" in spec:
        a, b = spec.split("-", 1)
        return DAYS.get(a[:3], 0) <= weekday <= DAYS.get(b[:3], 6)
    return any(DAYS.get(d.strip()[:3]) == weekday for d in spec.split(","))


def last_brief() -> str:
    """Dernier brief gardé (7 jours)."""
    briefs = load()["briefs"]
    return str(briefs[-1].get("text") or "") if briefs else ""


def sources_from(*, agenda: Callable | None = None, reminders: Callable | None = None,
                 quota: Callable | None = None, reports: Callable | None = None,
                 veille_due: Callable | None = None, now: datetime | None = None) -> dict:
    """Sources du brief selon les interrupteurs : quota et échéances suivent le leur."""
    src: dict[str, Any] = {"now": now or datetime.now(), "agenda": agenda, "reminders": reminders, "reports": reports}
    if enabled("quota"):
        src["quota"] = quota
    if enabled("echeances"):
        src["veille_due"] = veille_due
    return {k: v for k, v in src.items() if v is not None}


def snooze_text(delay_s: float) -> str:
    hours = max(1, round(delay_s / 3600))
    return f"{hours} heure{'s' if hours > 1 else ''}"


