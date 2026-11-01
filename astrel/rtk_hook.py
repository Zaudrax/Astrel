#!/usr/bin/env python3
"""Hook PreToolUse d'Astrel sur l'outil Bash : fait passer QUELQUES commandes simples
par rtk (rtk-ai/rtk, édition Apache-2.0 « rtk-oss »), qui compacte leur SORTIE avant
que le modèle la relise (20/10, plan P12, lot 2 : essai mesuré).

Ce n'est PAS un garde-fou : `hooks/permission_hook.py` (protégé) tourne en parallèle
sur la commande d'ORIGINE et un refus l'emporte toujours. Ce hook n'émet JAMAIS de
décision (ni allow, ni deny) : seulement `updatedInput`, format repris de
`rtk hook claude`. Une panne ici ne bloque rien : le hook se tait, la commande part
telle quelle.

Réécrit seulement une commande SIMPLE (aucun |, &, ;, <, >, $, `, parenthèse ni
retour à la ligne) d'une liste blanche, mesurée le 20/10 sur ce dossier :
- `git status` sans option (5 761 → 4 257 caractères) ;
- `git log`, `git log -N`, `git log -n N` (8 048 → 643 : une ligne par commit, hash
  court, message coupé ; `--format`, `-p`, `--stat`… restent bruts) ;
- `ls -l…` (long : droits en octal, sans propriétaire ni date) ; `ls` court n'est PAS
  réécrit : rtk le rend plus long (713 → 918).
Phase 2 (`git diff`, `grep`) seulement après la mesure (`tools/usage_report.py`).

Couper : `echo off > runtime/rtk.txt` (relu à chaque appel, sans relance). Données de
rtk (`rtk gain`) sous `runtime/rtk/`, télémétrie interdite (elle est coupée par défaut).
"""
from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_UNSAFE = re.compile(r"[|&;<>$`()\n\r\\]")
_LS_LONG = re.compile(r"-[lahAF1]*l[lahAF1]*")


def _rewritable(words: list[str]) -> bool:
    if len(words) >= 2 and words[0] == "git":
        sub, rest = words[1], words[2:]
        if sub == "status":
            return not rest
        if sub == "log":
            return (not rest or (len(rest) == 1 and re.fullmatch(r"-\d{1,4}", rest[0]) is not None)
                    or (len(rest) == 2 and rest[0] == "-n" and rest[1].isdigit()))
        return False
    if words and words[0] == "ls":
        opts = [w for w in words[1:] if w.startswith("-")]
        return len(opts) == 1 and _LS_LONG.fullmatch(opts[0]) is not None
    return False


def rewrite(command: str, rtk: Path, data_home: Path) -> str | None:
    """Commande réécrite pour rtk, ou None si elle doit partir telle quelle."""
    cmd = command.strip()
    if not cmd or _UNSAFE.search(cmd):
        return None
    try:
        words = shlex.split(cmd)
    except ValueError:
        return None
    if not _rewritable(words):
        return None
    env = (f"XDG_DATA_HOME={shlex.quote(str(data_home))} RTK_TELEMETRY_DISABLED=1 "
           "RTK_SUPPRESS_HOOK_WARNING=1")
    # Le reste de la ligne est gardé TEL QUEL (guillemets, jokers) : le shell le lit comme avant.
    return f"env {env} {shlex.quote(str(rtk))} {cmd}"


def enabled() -> bool:
    from astrel import config

    if not config.RTK_BIN.is_file():
        return False
    try:
        return config.RTK_SWITCH_FILE.read_text(encoding="utf-8").strip().lower() not in ("off", "non", "0")
    except OSError:
        return True  # pas de fichier : rtk installé = essai en cours


def main() -> int:
    try:
        from astrel import config

        payload = json.load(sys.stdin)
        if str(payload.get("tool_name", "")) != "Bash" or not enabled():
            return 0
        tool_input = payload.get("tool_input") or {}
        new = rewrite(str(tool_input.get("command") or ""), config.RTK_BIN, config.RUNTIME)
        if new is None:
            return 0
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecisionReason": "Astrel : sortie compactée par rtk",
            "updatedInput": {**tool_input, "command": new},
        }}, ensure_ascii=False))
    except Exception:
        return 0  # confort seulement : en cas de doute, ne rien changer
    return 0


if __name__ == "__main__":
    sys.exit(main())
