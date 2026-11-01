#!/usr/bin/env python3
"""Hook PreToolUse des fournisseurs CLI (22/10, plan P27 : Codex, Grok Build).

Lancé par le CLI du fournisseur (`runtime/cli/<id>/`, écrit par le cœur) :
    python hooks/permission_hook_cli.py <cli> <mode>
Convertit la charge du CLI vers celle de Claude Code (`tool_name`, `tool_input`, `cwd`), puis
le garde-fou d'Astrel décide comme pour Claude (`guard.refused`, `guard.assess`,
`confirm.request`).

Règle d'or : dans le doute, REFUSER. Charge illisible, outil inconnu, mode autre que « outils »
(conversation seule), CLI inconnu, garde-fou introuvable ou qui plante, Astrel injoignable →
refus. Le refus est dit de TOUTES les façons connues, faute de savoir laquelle le CLI lit
(⚠️ NON PROUVÉ au 22/10, lot 0 C6/C7) : JSON au format de Claude Code sur stdout (avec
`decision: block`), raison sur stderr, code de sortie 2.

La conversion vit ICI, dans `hooks/` (protégé, dans l'empreinte du doctor) : un fichier libre
qui la ferait pourrait faire passer n'importe quel outil pour une lecture anodine.
"""
from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from astrel import confirm, guard  # noqa: E402
except BaseException:  # garde-fou cassé ou introuvable : `main` plante, donc refuse (voir plus bas)
    confirm = guard = None  # type: ignore[assignment]

PANNE = "hook Astrel en panne : action bloquée par prudence."
CLIS = ("codex", "grokbuild")
MCP_SERVEURS = ("astrel", "connecteurs")

# Noms de Claude Code acceptés tels quels (le garde-fou les connaît). Jamais Agent, Task, Skill… :
# pas de sous-agents derrière un CLI (plan P27).
CLAUDE = {"Bash", "Read", "Grep", "Glob", "LS", "Write", "Edit", "MultiEdit", "NotebookEdit",
          "WebSearch", "WebFetch"}
# Noms SUPPOSÉS des CLI (documentation, à confirmer au lot 0) → nom de Claude Code.
SHELL = {"bash", "shell", "local_shell", "exec_command", "container.exec", "run_command", "run_terminal_cmd",
         "execute", "execute_command", "terminal", "run_shell_command", "shell_command", "unified_exec"}
ECRIRE = {"write", "write_file", "create_file", "writefile"}
EDITER = {"edit", "edit_file", "str_replace", "str_replace_editor", "replace", "search_replace", "multiedit",
          "multi_edit"}
LIRE = {"read", "read_file", "view", "view_file"}
CHERCHER = {"grep", "search", "search_files", "grep_search", "rg"}
LISTER = {"glob", "list_dir", "list_directory", "ls", "find_files"}
WEB_CHERCHE = {"websearch", "web_search", "search_web"}
WEB_LIT = {"webfetch", "web_fetch", "fetch", "fetch_url"}
PATCH = {"apply_patch", "applypatch"}
_PATCH_OP = re.compile(r"^\*\*\* (Add File|Update File|Delete File|Move to): (.+?)\s*$", re.M)
# Dossier où agit l'outil, DANS son entrée (Codex : `workdir` du shell). Correction n°2 du 22/10
# (fiche 3 du journal p27) : seul le `cwd` de la charge était jugé, « cat auth.json » avec
# `workdir` = runtime/cli/codex passait pour une lecture anodine du dossier de travail.
DOSSIER_TRAVAIL = ("workdir", "working_directory", "cwd")
# Recherche et liste : clés de chemin connues. Toute AUTRE clé hors de NON_CHEMINS peut être un
# chemin que le garde-fou ne verrait pas (« grep_search directory=config ») : refus.
CHEMINS_RECHERCHE = ("path", "directory", "dir", "dir_path", "target_directory", "search_path", "root",
                     "file_path")
NON_CHEMINS = frozenset({
    "pattern", "query", "regex", "search", "text", "include", "exclude", "includes", "excludes", "glob", "type",
    "include_pattern", "exclude_pattern", "file_pattern", "case_sensitive", "ignore_case", "fixed_strings",
    "literal", "multiline", "recursive", "depth", "max_depth", "hidden", "follow", "context", "output_mode",
    "head_limit", "limit", "max_results", "offset", "-i", "-n", "-A", "-B", "-C", "explanation", "description",
    "timeout", "timeout_ms"})
FICHIERS_CLAUDE = {"Read", "Write", "Edit", "MultiEdit", "NotebookEdit"}


def emit(decision: str, reason: str) -> int:
    """Décision au format de Claude Code (+ `decision`/`reason` de l'ancien format). Refus : raison
    aussi sur stderr et code 2 (ce que Claude Code lit comme un blocage)."""
    deny = decision != "allow"
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny" if deny else "allow",
            "permissionDecisionReason": reason,
        },
        "decision": "block" if deny else "approve",
        "reason": reason,
    }, ensure_ascii=False))
    sys.stdout.flush()
    if deny:
        sys.stderr.write(reason + "\n")
        return 2
    return 0


def _dict(value) -> dict | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None
    return None


def _commande(value) -> str | None:
    """Commande shell en texte. Liste (`["bash", "-lc", "ls"]`) : le script de `-c`, sinon les mots
    cités ; autre chose : illisible."""
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value and all(isinstance(x, str) for x in value):
        if len(value) >= 3 and Path(value[0]).name in ("bash", "sh", "zsh", "dash") \
                and value[1] in ("-c", "-lc", "-ic", "-lic"):
            return value[2] if len(value) == 3 else shlex.join(value)
        return shlex.join(value)
    return None


def _chemin(inp: dict) -> str:
    for k in ("file_path", "path", "filename", "target_file", "file", "notebook_path"):
        if isinstance(inp.get(k), str) and inp[k]:
            return inp[k]
    return ""


def _patch(texte) -> list[tuple[str, dict]] | None:
    """`apply_patch` de Codex : une écriture par fichier, une suppression jugée comme `rm`."""
    if not isinstance(texte, str):
        return None
    actions: list[tuple[str, dict]] = []
    for op, path in _PATCH_OP.findall(texte):
        if op == "Delete File":
            actions.append(("Bash", {"command": "rm -- " + shlex.quote(path)}))
        else:
            actions.append(("Write" if op != "Update File" else "Edit", {"file_path": path}))
    return actions or None


def _entree(payload: dict) -> tuple[object, object]:
    """(nom de l'outil, entrée brute) de la charge du CLI."""
    call = payload.get("tool_call") if isinstance(payload.get("tool_call"), dict) else {}
    tool = payload.get("tool_name") or payload.get("tool") or payload.get("name") or call.get("name")
    raw = next((payload[k] for k in ("tool_input", "input", "arguments", "args", "parameters")
                if k in payload), call.get("arguments", call.get("input", {})))
    return tool, raw


def dossier(payload: dict) -> tuple[str | None, bool]:
    """(dossier où agit l'outil, donné par son entrée ?). Le `workdir` de l'entrée prime sur le
    `cwd` de la charge ; relatif, il part de ce `cwd` (absolu exigé). None : illisible (refus)."""
    cwd = payload.get("cwd") or payload.get("working_directory") or payload.get("workdir") or ""
    cwd = cwd if isinstance(cwd, str) else ""
    inp = _dict(_entree(payload)[1]) or {}
    for k in DOSSIER_TRAVAIL:
        wd = inp.get(k)
        if wd is None or wd == "":
            continue
        if not isinstance(wd, str):
            return None, True
        p = Path(wd).expanduser()
        if p.is_absolute():
            return str(p), True
        if cwd and Path(cwd).is_absolute():
            return str(Path(cwd) / p), True
        return None, True  # relatif à un dossier inconnu
    return cwd, False


def _recherche(outil: str, inp: dict) -> list[tuple[str, dict]] | None:
    """Recherche ou liste : chaque chemin connu est jugé ; clé inconnue, chemin vide ou absent
    (hors `workdir`) = None (refus). Sans chemin, Claude Code chercherait dans le dossier courant,
    mais une clé mal lue ici ferait juger le mauvais dossier : on ne devine pas."""
    if any(k not in NON_CHEMINS and k not in CHEMINS_RECHERCHE and k not in DOSSIER_TRAVAIL for k in inp):
        return None
    paths: list[str] = []
    for k in CHEMINS_RECHERCHE:
        if k in inp:
            v = inp[k]
            if not isinstance(v, str) or not v.strip():
                return None
            paths.append(v)
    if not paths:
        if not any(isinstance(inp.get(k), str) and inp[k].strip() for k in DOSSIER_TRAVAIL):
            return None
        paths = ["."]  # le `workdir`, devenu le dossier jugé (`dossier`)
    if outil == "Grep":
        motif = str(inp.get("pattern") or inp.get("query") or inp.get("regex") or "")
        return [("Grep", {"pattern": motif, "path": p}) for p in dict.fromkeys(paths)]
    return [(outil, {"pattern": str(inp.get("pattern") or "*"), "path": p}) for p in dict.fromkeys(paths)]


def actions(payload: dict) -> list[tuple[str, dict]] | None:
    """Ce que l'outil va faire, au format de Claude Code ; None = illisible ou inconnu (refus)."""
    tool, raw = _entree(payload)
    if not isinstance(tool, str) or not tool:
        return None
    low = tool.lower()
    if low in PATCH:
        inp = _dict(raw)
        return _patch(inp.get("input") or inp.get("patch")) if inp is not None else _patch(raw)
    if low in SHELL or tool == "Bash":
        inp = _dict(raw) or {}
        cmd = _commande(inp.get("command", inp.get("cmd")) if inp else raw)
        if cmd is None:
            return None
        if isinstance(inp.get("command"), list) and inp["command"] and inp["command"][0] == "apply_patch":
            return _patch(inp["command"][1] if len(inp["command"]) > 1 else None)
        return [("Bash", {"command": cmd})]
    inp = _dict(raw)
    if inp is None:
        return None
    if tool in ("Grep", "Glob", "LS"):
        return _recherche(tool, inp)
    if tool in FICHIERS_CLAUDE:  # sans chemin lisible, le garde-fou ne jugerait rien : refus
        path = _chemin(inp)
        return [(tool, {**inp, "file_path": path})] if path else None
    if tool in CLAUDE:
        return [(tool, inp)]
    if tool.startswith("mcp__"):
        return [(tool, inp)] if tool.count("__") >= 2 else None
    for sep in (".", "/", "__"):  # « astrel.notify », « astrel/notify », « astrel__notify »
        server, _, name = tool.partition(sep)
        if server in MCP_SERVEURS and name and re.fullmatch(r"[A-Za-z0-9_-]+", name):
            return [(f"mcp__{server}__{name}", inp)]
    path = _chemin(inp)
    if low in ECRIRE:
        return [("Write", {"file_path": path})] if path else None
    if low in EDITER:
        return [("Edit", {"file_path": path})] if path else None
    if low in LIRE:
        return [("Read", {"file_path": path})] if path else None
    if low in CHERCHER:
        return _recherche("Grep", inp)
    if low in LISTER:
        return _recherche("Glob", inp)
    if low in WEB_CHERCHE:
        return [("WebSearch", {"query": str(inp.get("query") or "")})]
    if low in WEB_LIT:
        url = inp.get("url")
        return [("WebFetch", {"url": url})] if isinstance(url, str) and url else None
    return None  # outil inconnu : refus


def decider(payload, cli: str, mode: str) -> tuple[str, str]:
    """(« allow » | « deny », raison)."""
    if cli not in CLIS:
        return "deny", f"hook Astrel : fournisseur CLI inconnu ({cli[:40]}), action bloquée."
    if mode != "outils":
        return "deny", "conversation seule : aucun outil n'est permis à ce fournisseur (Astrel)."
    if not isinstance(payload, dict):
        return "deny", "hook Astrel : entrée illisible, action bloquée par prudence."
    todo = actions(payload)
    if not todo:
        tool = payload.get("tool_name") or payload.get("tool") or payload.get("name") or "?"
        return "deny", f"hook Astrel : outil inconnu ou entrée illisible ({str(tool)[:60]}), action bloquée."
    cwd, explicite = dossier(payload)
    if cwd is None:
        return "deny", "hook Astrel : dossier de travail de l'outil illisible, action bloquée par prudence."
    if explicite:  # la commande est jugée (et montrée) telle qu'elle tourne : dans ce dossier
        todo = [(t, {**i, "command": f"cd {shlex.quote(cwd)} && {i['command']}"}) if t == "Bash" else (t, i)
                for t, i in todo]
    for tool, inp in todo:
        why = guard.refused(tool)
        if why:
            return "deny", why
    for tool, inp in todo:
        verdict = guard.assess(tool, inp, cwd=cwd or None)
        if not verdict.needs_confirm:
            continue
        answer = confirm.request(tool, verdict.desc, guard.short_command(tool, inp), verdict.key,
                                 keys=verdict.keys, guard=verdict.guard, cwd=cwd,
                                 lift=bool(getattr(verdict, "lift", False)),
                                 cats=list(getattr(verdict, "cats", ()) or ()))  # 23/10 (plan P29)
        if answer is None:
            return "deny", f"Astrel injoignable pour confirmer : {verdict.desc}. Action bloquée."
        if not answer:
            return "deny", f"refusé par l'utilisateur : {verdict.desc}. Propose une alternative ou demande des précisions."
    return "allow", "action autorisée par Astrel"


def main(argv: list[str]) -> int:
    cli = argv[1] if len(argv) > 1 else ""
    mode = argv[2] if len(argv) > 2 else ""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return emit("deny", "hook Astrel : entrée illisible, action bloquée par prudence.")
    decision, reason = decider(payload, cli, mode)
    return emit(decision, reason)


def fail_closed() -> int:
    """Le hook plante : REFUSER (code 2 en dernier recours, si même la sortie JSON échoue)."""
    try:
        emit("deny", PANNE)
    except BaseException:
        try:
            sys.stderr.write(PANNE + "\n")
        except BaseException:
            pass
    return 2


if __name__ == "__main__":
    try:
        code = main(sys.argv)
    except BaseException:
        code = fail_closed()
    sys.exit(code)
