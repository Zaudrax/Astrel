#!/usr/bin/env python3
"""Hook PreToolUse de Claude Code : laisse passer le travail courant, fait
confirmer l'irréversible par l'utilisateur (à l'oral, d'un clic ou d'un geste).
Mode « autonome » par défaut : voir `astrel/guard.py`.

Règle d'or : dans le doute, REFUSER. Astrel injoignable, réponse ambiguë ou
entrée illisible pour une action sensible → l'action n'a pas lieu, et Claude
reçoit la raison pour proposer autre chose.

01/11/2026 (accord explicite de l'utilisateur ; idées 4 et 5 de docs/idees/HERMES-outils.md) :
refus FERMES (ne pas relancer, ne pas reformuler, ne pas obtenir la même chose autrement) et
DISJONCTEUR : au 3e refus d'affilée dans un même message de l'utilisateur (même `prompt_id`, même
agent), le refus dit d'arrêter de contourner et de demander à l'utilisateur ; ensuite, jusqu'au
message suivant, toute action qui demanderait une question est refusée SANS carte. Un « oui »
de l'utilisateur remet la série à zéro. Un sous-agent du même message hérite d'un disjoncteur ouvert dans
le fil principal (déléguer ne le referme pas). Sans `prompt_id` (absent avant le premier message du
process), la série est celle de la session : elle tombe 30 min après le dernier refus. Compteur dans
`runtime/garde-refus/` (protégé par le garde-fou contre Write/Edit et les commandes qui le visent ; un
script qui construirait le chemin reste la limite connue de l'analyse de texte). Le disjoncteur
n'accorde jamais rien : en panne, il se tait et la décision reste celle d'avant.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from astrel import confirm, guard  # noqa: E402
except BaseException:  # garde-fou cassé ou introuvable : `main` plante, donc refuse (voir plus bas)
    confirm = guard = None  # type: ignore[assignment]

PANNE = "hook Astrel en panne : action bloquée par prudence."

SEUIL_DISJONCTEUR = 3
SERIE_MAX_S = 30 * 60  # une série sans nouveau refus depuis 30 min est oubliée (sans `prompt_id` surtout)
FERME = ("Ne la relance pas, ne la reformule pas et n'obtiens pas le même résultat par un autre moyen : "
         "propose une alternative ou demande des précisions.")
DISJONCTEUR = ("[Disjoncteur du garde-fou] {n} refus d'affilée dans ce message. ARRÊTE : plus aucune variante ni "
               "autre voie pour cette action. Dis à l'utilisateur ce qui est bloqué et pourquoi, puis attends sa réponse.")
DISJONCTE = ("disjoncteur du garde-fou ouvert ({n} refus d'affilée dans ce message) : plus aucune demande de "
             "confirmation avant le prochain message de l'utilisateur. Arrête et demande-lui.")


def _refus_file() -> Path | None:
    """`runtime/garde-refus/series.json` (dossier 0700, protégé par le garde-fou) ; None si le dossier
    n'est pas un vrai dossier (lien posé à sa place : le disjoncteur se tait plutôt que d'écrire ailleurs)."""
    try:
        d = Path(guard.REFUS_DIR)
        if not d.exists():
            d.mkdir(mode=0o700)
        if d.is_symlink() or not d.is_dir():
            return None
        return d / "series.json"
    except Exception:  # noqa: BLE001
        return None


def _cle(payload: dict) -> str:
    """Une série par message de l'utilisateur (`prompt_id`) et par agent (`agent_id`, vide = fil principal)."""
    return "|".join(str(payload.get(k) or "") for k in ("session_id", "prompt_id", "agent_id"))[:300]


def _ouvert(payload: dict) -> int:
    """Refus d'affilée qui ouvrent le disjoncteur pour cet appel : sa série, ou celle du fil principal
    du même message pour un sous-agent (01/11, relecture : déléguer ne remet pas le compteur à zéro)."""
    n = _serie(payload)
    if payload.get("agent_id"):
        n = max(n, _serie({k: v for k, v in payload.items() if k != "agent_id"}))
    return n


def _serie(payload: dict, change: int | None = None) -> int:
    """Refus d'affilée de cette série ; `change` : +1 (un refus de plus), 0 (remise à zéro). Ne lève
    jamais : en panne, 0 (le disjoncteur se tait, la décision reste celle du garde-fou)."""
    try:
        path = _refus_file()
        if path is None:
            return 0
        key, now = _cle(payload), time.time()
        lock = None
        try:
            import fcntl
            lock = os.open(path.with_name(".verrou"), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        try:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            series = data.get("series") if isinstance(data, dict) and isinstance(data.get("series"), dict) else {}
            entry = series.get(key) if isinstance(series.get(key), dict) else {}
            count = int(entry.get("n") or 0) if now - float(entry.get("at") or 0) < SERIE_MAX_S else 0
            if change is None:
                return count
            if change == 0 and not count:
                return 0
            count = count + 1 if change > 0 else 0
            series[key] = {"n": count, "at": now}
            series = dict(sorted(series.items(), key=lambda kv: float((kv[1] or {}).get("at") or 0))[-50:])
            tmp = path.with_name(f".series.{os.getpid()}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"series": series}, fh)
            os.replace(tmp, path)
            return count
        finally:
            if lock is not None:
                os.close(lock)
    except Exception:  # noqa: BLE001
        return 0


def _refuse(payload: dict, reason: str) -> str:
    """Note le refus ; dès le 3e d'affilée, la raison dit d'arrêter et de demander à l'utilisateur."""
    n = _serie(payload, +1)
    return f"{reason} {DISJONCTEUR.format(n=n)}" if n >= SEUIL_DISJONCTEUR else reason


def emit(decision: str, reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        emit("deny", "hook Astrel : entrée illisible, action bloquée par prudence.")
        return 0
    if not isinstance(payload, dict):
        emit("deny", "hook Astrel : entrée illisible, action bloquée par prudence.")
        return 0

    tool = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input") or {}
    # 15/10 : dossier courant de Claude (champ `cwd` de l'entrée du hook), pour
    # juger les chemins relatifs là où la commande s'exécute vraiment.
    cwd = str(payload.get("cwd") or "")

    # 20/10 (plan P12, lot 6) : outil refusé d'office par hooks/tool-policy.json, sans carte.
    # 05/11 (lot 5 du moteur local) : l'entrée aussi (outils du serveur `astrel_local`, jugés sur leur entrée).
    why = guard.refused(tool, tool_input)
    if why:
        emit("deny", _refuse(payload, why))
        return 0

    verdict = guard.assess(tool, tool_input, cwd=cwd or None)
    if not verdict.needs_confirm:
        emit("allow", "action autorisée par Astrel")
        return 0

    # 01/11 : disjoncteur ouvert (3 refus d'affilée dans ce message) : refusé sans carte.
    n = _ouvert(payload)
    if n >= SEUIL_DISJONCTEUR:
        emit("deny", f"{verdict.desc} : {DISJONCTE.format(n=n)}")
        return 0

    # 18/10 (plan P7) : `lift` = levable pendant une levée du garde-fou (le cœur décide).
    # 23/10 (plan P29) : `cats` = catégories de l'action (cases des limites de Total et de la levée).
    # 03/11 (lot B, T43.5-A) : le cœur recalcule le verdict depuis l'ENTRÉE de l'outil (bornée sans
    # changer le verdict) ; impossible à borner : refusé, sans carte. 04/11 : les champs d'avant partent
    # aussi (`confirm.request`), pour le cœur encore en marche tant qu'il n'est pas relancé.
    inp = confirm.entree_bornee(tool, tool_input, cwd)
    if inp is None:
        emit("deny", _refuse(payload, f"{verdict.desc} : entrée de l'outil illisible ou trop grande pour être "
                                      "confirmée. Action bloquée. " + FERME))
        return 0
    answer = confirm.request(tool, verdict.desc, guard.short_command(tool, tool_input), verdict.key,
                             keys=verdict.keys, guard=verdict.guard, cwd=cwd,
                             lift=bool(getattr(verdict, "lift", False)),
                             cats=list(getattr(verdict, "cats", ()) or ()), tool_input=inp)
    if answer is None:
        emit("deny", _refuse(payload, f"Astrel injoignable pour confirmer : {verdict.desc}. Action bloquée. "
                                      "Sans réponse, ce n'est pas un accord : " + FERME))
    elif answer:
        _serie(payload, 0)  # un « oui » de l'utilisateur : la série repart de zéro
        emit("allow", f"confirmé par l'utilisateur : {verdict.desc}")
    else:
        emit("deny", _refuse(payload, f"refusé par l'utilisateur : {verdict.desc}. L'utilisateur n'a PAS donné son accord. "
                                      + FERME))
    return 0


def fail_closed() -> int:
    """Le hook plante : REFUSER. Pour Claude Code, un code de sortie autre que 0
    ou 2 est une erreur « non bloquante » : l'action passait (vu par
    `./run.sh --doctor`, 15/10). Le code 2 bloque si même la sortie JSON échoue."""
    try:
        emit("deny", PANNE)
        return 0
    except BaseException:
        sys.stderr.write(PANNE + "\n")
        return 2


if __name__ == "__main__":
    try:
        code = main()
    except BaseException:
        code = fail_closed()
    sys.exit(code)
