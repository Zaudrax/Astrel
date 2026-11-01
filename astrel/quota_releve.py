"""Relevé du quota de l'abonnement HORS des tours d'Astrel (22/10, demande de l'utilisateur : « le
compteur ne se met pas à jour dans Astrel quand je ne l'utilise pas sur Astrel »).

Le quota (`Brain.quota`) ne bougeait qu'avec les `rate_limit_event` du CLI, donc seulement quand
Astrel parlait à Claude ; un usage de Claude Code ailleurs (VS Code) laissait l'affichage figé.

Ce relevé passe par le CLI LUI-MÊME, jamais par `~/.claude/.credentials.json` ni l'API OAuth
(interdits par `compte.py` et le garde-fou). Deux voies, mesurées le 22/10 (CLI 2.1.285) :

1. `claude -p "/usage"` : commande LOCALE du CLI, AUCUN appel au modèle (0 token, `local_command:
   "usage"`, ≈ 0,9 s). Texte anglais lu ici : « Current session: 3% used · resets Oct 22, 8:19pm
   (Europe/Paris) » (fenêtre de 5 h), « Current week (all models): 63% used · … » (7 jours).
2. Repli si ce texte devient illisible (CLI mis à jour) : `claude -p` minimal sur haiku, sans
   outils, sans MCP, sans réflexion, sans réglages ni session gardée ; seul son `rate_limit_event`
   est lu (fenêtres avec `utilization`). Coût mesuré : 345 jetons d'entrée + 84 de sortie.

Les deux rendent un `rate_limit_info` au FORMAT DU CLI (`unifiedWindows`…), que le cerveau
convertit comme un évènement venu du flux (`Brain.apply_rate_limit`) : même chemin ensuite
(`QuotaTrend`, `_publish_quota`, `_guard_quota`). Rien n'est écrit dans l'historique des
conversations (`--no-session-persistence`).
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from . import config

log = logging.getLogger("astrel.quota")

# Au plus un relevé automatique toutes les 10 min, seulement si le dernier relevé (flux compris)
# a plus de 10 min. Bouton du HUD : 30 s entre deux.
try:
    PERIODE_S = max(60.0, float(os.environ.get("ASTREL_QUOTA_REFRESH_S", 600) or 600))
except ValueError:
    PERIODE_S = 600.0
MANUEL_ECART_S = 30.0
DELAI_S = 30.0  # un relevé qui dépasse est abandonné (process tué)

_MOIS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
# Mois français, par début de mot sans accent (« juin »/« juil » : 4 lettres).
_MOIS_FR = (("janv", 1), ("fev", 2), ("mars", 3), ("avr", 4), ("mai", 5), ("juin", 6), ("juil", 7),
            ("aou", 8), ("sep", 9), ("oct", 10), ("nov", 11), ("dec", 12))
# Une ligne « libellé : N % … » : « Current session: 3% used · resets Oct 22, 8:19pm (Europe/Paris) »,
# ou en français « Session en cours (fenêtre de 5 h) : 3 % utilisés · se réinitialise le 22 oct. à
# 20:19 (Europe/Paris) » (22/10, correction n°2 : format français SUPPOSÉ, jamais vu, le CLI reste en
# anglais sous LANG=fr_FR ; sans lui, un /usage traduit ferait une sonde haiku à chaque cycle).
_LIGNE = re.compile(r"^[ \t]*(?P<lib>[^:\n%]{1,80}?)[ \t]*:[ \t]*(?P<pct>\d+(?:[.,]\d+)?)[ \t]*%(?P<reste>[^\n]*)$", re.M)
_RESET = re.compile(r"resets\s+(?:(?P<mois>[A-Za-z]{3})[a-z]*\.?\s+(?P<jour>\d{1,2}),?\s*)?"
                    r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ampm>am|pm)?\s*(?:\((?P<tz>[^)]+)\))?", re.I)
_RESET_FR = re.compile(r"r[ée]initiali(?:se|sation)\w*\s*(?:le\s+)?(?:(?P<jour>\d{1,2})(?:er)?\s+(?P<mois>[^\W\d_]{3,9})\.?,?\s*)?"
                       r"(?:[àa]\s+)?(?P<h>\d{1,2})\s*(?:h|:)\s*(?P<m>\d{2})?\s*(?:\((?P<tz>[^)]+)\))?", re.I)
# Fenêtres prises : les mêmes que le flux (`rate_limit_event`) ; les semaines par modèle
# (« Current week (Fable) ») ne sont pas dans le flux, on ne les ajoute pas.
_TOUS = {"all models", "tous les modeles", "tous modeles"}
RECALAGE_S = 300.0  # « 10:59am » du texte = 11:00 du flux : on garde l'heure déjà connue


def _sans_accent(texte: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", texte) if not unicodedata.combining(c)).lower()


def _fenetre(libelle: str) -> str | None:
    """Libellé d'une ligne de /usage → fenêtre du flux (« five_hour », « seven_day »), None sinon."""
    lib = _sans_accent(libelle)
    qui = re.search(r"\(([^)]*)\)", lib)
    qui = qui[1].strip() if qui else ""
    if re.search(r"\bsession\b", lib) or re.search(r"\b5\s*-?\s*h", lib) or "cinq heures" in lib:
        return "five_hour"  # « session » au singulier : « … sessions: 74% » n'est pas une fenêtre
    if re.search(r"\bweek\b|semaine|hebdo|7\s*jours", lib):
        return "seven_day" if not qui or qui in _TOUS else None
    return None


def _mois(nom: str) -> int | None:
    nom = _sans_accent(nom)
    return _MOIS.get(nom[:3]) or next((i for debut, i in _MOIS_FR if nom.startswith(debut)), None)


def _zone(nom: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(nom.strip())
    except Exception:  # noqa: BLE001 — zone inconnue : heure locale
        return None


def _heure(m: dict, now: float) -> float | None:
    """Heure de réinitialisation du texte (groupes d'un `_RESET` ou `_RESET_FR`), en epoch ;
    année la plus proche de maintenant."""
    h, mn = int(m["h"]), int(m.get("m") or 0)
    ampm = (m.get("ampm") or "").lower()
    if ampm == "pm" and h < 12:
        h += 12
    elif ampm == "am" and h == 12:
        h = 0
    if h > 23 or mn > 59:
        return None
    tz = _zone(m["tz"]) if m.get("tz") else None
    base = datetime.fromtimestamp(now, tz) if tz else datetime.fromtimestamp(now).astimezone()
    if m.get("mois") and m.get("jour"):
        mois = _mois(m["mois"])
        if not mois:
            return None
        best = None
        for an in (base.year - 1, base.year, base.year + 1):
            try:
                d = base.replace(year=an, month=mois, day=int(m["jour"]), hour=h, minute=mn, second=0, microsecond=0)
            except ValueError:
                continue
            if best is None or abs(d.timestamp() - now) < abs(best.timestamp() - now):
                best = d
        return best.timestamp() if best else None
    d = base.replace(hour=h, minute=mn, second=0, microsecond=0)  # heure seule : aujourd'hui, sinon demain
    if d.timestamp() < now - 60:
        d += timedelta(days=1)
    return d.timestamp()


def lire_usage(texte: str, now: float | None = None, connu: dict | None = None) -> dict | None:
    """Texte de `/usage` → `rate_limit_info` au format du CLI, None s'il est illisible (aucune
    des deux fenêtres). `connu` : fenêtres déjà connues (`Brain.quota["windows"]`) ; une heure
    de réinitialisation à moins de 5 min de la leur est recalée dessus, sinon `QuotaTrend`
    croirait à une nouvelle fenêtre à chaque changement de source.
    Une fenêtre pleine (≥ 100 %) rend « rejected » et `rateLimitType` = cette fenêtre (la plus
    longue à se réinitialiser si les deux le sont), comme le flux : sinon `_guard_quota` lançait
    au repos un tour « point de reprise » voué à l'échec (relecture du 22/10)."""
    now = time.time() if now is None else now
    fenetres: dict[str, dict[str, Any]] = {}
    for m in _LIGNE.finditer(texte or ""):
        nom = _fenetre(m["lib"])
        if not nom or nom in fenetres:
            continue
        w: dict[str, Any] = {"utilization": round(float(m["pct"].replace(",", ".")) / 100, 4)}
        r = _RESET.search(m["reste"] or "") or _RESET_FR.search(m["reste"] or "")
        reset = _heure(r.groupdict(), now) if r else None
        ancien = ((connu or {}).get(nom) or {}).get("resetsAt") if isinstance((connu or {}).get(nom), dict) else None
        if reset is not None and isinstance(ancien, (int, float)) and not isinstance(ancien, bool) \
                and abs(float(ancien) - reset) <= RECALAGE_S:
            reset = ancien
        if reset is not None:
            w["resetsAt"] = int(reset) if float(reset).is_integer() else reset
        fenetres[nom] = w
    if not fenetres:
        return None
    pleines = [n for n, w in fenetres.items() if w["utilization"] >= 1.0]
    if not pleines:
        return {"status": "allowed", "unifiedWindows": fenetres}
    limite = max(pleines, key=lambda n: (float(fenetres[n].get("resetsAt") or 0), n == "seven_day"))
    return {"status": "rejected", "rateLimitType": limite, "unifiedWindows": fenetres}


def lire_flux(lignes: str) -> tuple[dict | None, int]:
    """Sortie stream-json d'un `claude -p` : (`rate_limit_info` du dernier `rate_limit_event`,
    jetons consommés d'après le `result`)."""
    info, jetons = None, 0
    for ligne in (lignes or "").splitlines():
        try:
            ev = json.loads(ligne)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "rate_limit_event" and isinstance(ev.get("rate_limit_info"), dict):
            info = ev["rate_limit_info"]
        elif ev.get("type") == "result" and isinstance(ev.get("usage"), dict):
            u = ev["usage"]
            jetons = sum(int(u.get(k) or 0) for k in ("input_tokens", "output_tokens",
                                                       "cache_creation_input_tokens", "cache_read_input_tokens"))
    if info is not None and not isinstance(info.get("unifiedWindows"), dict):
        info = None  # un statut seul, sans pourcentage : inutile ici
    return info, jetons


_COMMUN = ("--no-session-persistence", "--tools", "", "--strict-mcp-config", "--mcp-config",
           '{"mcpServers":{}}', "--setting-sources", "")


def commande_usage() -> list[str]:
    return [config.CLAUDE_BIN, "-p", "/usage", "--output-format", "json", *_COMMUN]


def commande_sonde() -> list[str]:
    return [config.CLAUDE_BIN, "-p", ".", "--model", "haiku", "--output-format", "stream-json", "--verbose",
            "--disable-slash-commands", "--system-prompt", "Reponds OK.", *_COMMUN]


def environnement() -> dict:
    """Celui du cerveau sur l'abonnement : aucune variable Anthropic ni clé (elle primerait sur
    l'abonnement), pas de réflexion pour la sonde."""
    from . import fournisseurs
    env = fournisseurs.env_cerveau(dict(os.environ), "abonnement")
    env["MAX_THINKING_TOKENS"] = "0"
    return env


Lanceur = Callable[..., "subprocess.CompletedProcess[str]"]


def _lancer(cmd: list[str], lanceur: Lanceur) -> str:
    # Dossier neutre : aucun CLAUDE.md de projet joint à la sonde (jetons pour rien).
    r = lanceur(cmd, cwd=tempfile.gettempdir(), env=environnement(), stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=DELAI_S)
    if r.returncode != 0:
        raise RuntimeError(f"le CLI a répondu {r.returncode}")
    return r.stdout or ""


def relever(connu: dict | None = None, lanceur: Lanceur = subprocess.run, now: float | None = None,
            sonde: bool = True) -> dict:
    """Un relevé : `/usage` d'abord (0 jeton), la sonde haiku seulement s'il est illisible.
    `sonde` faux (24/10, plan P34 : relevé rapide pendant que des agents tournent) : `/usage` SEUL,
    jamais de sonde, qui coûterait des jetons à chaque relevé.
    Rend {ok, info, source ("usage"|"sonde"), jetons} ou {ok: False, error}."""
    try:
        sortie = _lancer(commande_usage(), lanceur)
        try:
            texte = str((json.loads(sortie) or {}).get("result") or "")
        except (ValueError, AttributeError):
            texte = sortie
        info = lire_usage(texte, now, connu)
        if info is not None:
            return {"ok": True, "info": info, "source": "usage", "jetons": 0}
        if not sonde:
            return {"ok": False, "error": "relevé du quota : texte de /usage illisible (sonde exclue)"}
        log.warning("relevé du quota : texte de /usage illisible, repli sur une sonde haiku (%r)", texte[:200])
        info, jetons = lire_flux(_lancer(commande_sonde(), lanceur))
        if info is None:
            return {"ok": False, "error": "relevé du quota : aucune fenêtre d'utilisation dans la réponse du CLI"}
        return {"ok": True, "info": info, "source": "sonde", "jetons": jetons}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "relevé du quota : le CLI ne répond pas"}
    except (OSError, RuntimeError) as exc:
        return {"ok": False, "error": f"relevé du quota impossible : {exc}"}


def main() -> int:  # pragma: no cover — essai à la main : .venv/bin/python -m astrel.quota_releve
    r = relever()
    print(json.dumps(r, ensure_ascii=False, indent=1))
    for nom, w in ((r.get("info") or {}).get("unifiedWindows") or {}).items():
        reset = w.get("resetsAt")
        quand = datetime.fromtimestamp(reset, timezone.utc).astimezone().strftime("%a %H:%M") if reset else "?"
        print(f"{nom}: {w.get('utilization', 0) * 100:.0f} % (réinitialisation {quand})")
    return 0 if r.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
