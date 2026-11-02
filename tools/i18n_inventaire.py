#!/usr/bin/env python3
"""Inventaire des textes FRANÇAIS que le code Python envoie à l'écran (20/10, demande de
l'utilisateur : « une traduction complète de l'application en anglais »).

Le HUD et la bulle traduisent ces textes par le catalogue `astrel/locales/en.json`
(clé = le texte français, `{nom}` = une partie variable). Ce script dit lesquels n'y
sont pas encore :

    .venv/bin/python tools/i18n_inventaire.py            # manquants (code 1 s'il en reste)
    .venv/bin/python tools/i18n_inventaire.py --tout     # tout l'inventaire, avec le fichier et la ligne
    .venv/bin/python tools/i18n_inventaire.py --json     # manquants en JSON {texte: ""} à traduire

Deux façons de lire un module :
- `FULL` : pages de Réglages (presque tout y est affiché) : chaque littéral qui a l'air
  français, sauf journal (`log.*`), docstrings et commentaires ;
- le reste de `astrel/` : seulement ce qui part à l'écran, c'est-à-dire les arguments
  `text=`, `detail=`, `desc=`… d'un `publish(...)` ou `status.set(...)`, et les valeurs
  des clés `"error"`, `"text"`, `"label"`… d'un dict littéral.
Une f-string devient un modèle : `f"Voix {engine.name} revenue."` → `Voix {name} revenue.`
Les consignes envoyées à Claude et la mémoire ne passent pas par ces contextes. 02/11 : les phrases DITES
par le code (`say`, `say_and_wait`, `_announce`, phrases de `config.py`) en font partie : la conversation
anglaise les dit par le même catalogue.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Modules des pages Réglages : presque tout y est un texte d'écran.
FULL = (
    "astrel/settings.py", "astrel/compte.py", "astrel/fournisseurs.py", "astrel/demarrage.py",
    "astrel/confidentialite.py", "astrel/maj.py", "astrel/modeles_locaux.py", "astrel/claudeai.py",
    "astrel/levee.py", "astrel/graph.py", "astrel/sensors.py", "astrel/audit.py", "astrel/bubble.py",
    "astrel/minichat.py", "tools/check_install.py",
)
# Jamais lus : le garde-fou (fichiers protégés, ses descriptions sont ajoutées à la main au
# catalogue), les serveurs MCP (réponses à Claude), le prompt et les outils de Claude.
SKIP = (
    "astrel/guard.py", "astrel/mcp_server.py", "astrel/mcp_connectors.py", "astrel/brain.py",
    "astrel/passerelle_serveur.py", "astrel/cle_api.py", "astrel/route_hook.py", "astrel/rtk_hook.py",
    "astrel/banc.py", "astrel/banc_mcp.py", "astrel/doctor.py", "astrel/cli.py",
)
SKIP_DIRS = ("astrel/tools/", "astrel/platform/")
VISIBLE_KW = {"text", "detail", "desc", "label", "reason", "error", "title", "message", "hint", "note", "help"}
VISIBLE_KEYS = VISIBLE_KW | {"what", "who", "auth", "confirm", "status", "step"}
PUBLISHERS = {"publish", "set", "notice", "_notice", "toast"}
VISIBLE_VARS = {"text", "hud", "notice", "question", "cost", "go", "stay", "never", "when", "then", "why", "note",
                "label", "msg", "message", "reason", "detail"}
LOG_FUNCS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
# 02/11 (conversation anglaise, `astrel/langue.py`) : phrases DITES par le code (premier argument), traduites
# par le même catalogue quand la conversation est en anglais ; et les constantes de phrases de `config.py`.
SPEAKERS = {"say", "say_and_wait", "_announce"}
SPOKEN_CONSTS = {"SAY_READY", "SAY_NOT_UNDERSTOOD", "SAY_ERROR", "GOODBYES"}

FRENCH = re.compile(
    r"[àâäçéèêëîïôöùûüœÀÂÇÉÈÊÎÔÙÛŒ«»]|\b(?:le|la|les|un|une|des|du|de|et|pour|pas|est|sur|avec|dans|tu|je|il|"
    r"ou|à|au|aux|ce|cette|ton|ta|tes|son|sa|ses|ne|plus|rien|tout|aucun|aucune|sans|par|en|ici|mais|"
    r"qui|que|quand|non|oui|voix|micro|fichier|fichiers|dossier|canal|canaux|joint|jointe|jointes|lancer|"
    r"relancer|couper|ouvrir|fermer|essai|outil|outils|projet|projets|silencieux|absent|connecteur|"
    r"connecteurs|capteur|capteurs|cerveau|chargement|limite|reprise|pont|jamais|toujours|seul|seule)\b", re.I)
PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
# Mots qui ont l'air d'un libellé mais n'en sont pas (noms propres, identifiants).
NOT_TEXT = {"Claude", "Astrel", "ASTREL", "Astrel", "ASTREL",  # Astrel : nom affiché par défaut (23/10, plan P31)
            "Max", "Pro", "Team", "Enterprise", "Bash", "Read", "Write", "Edit",
            "Grep", "Glob", "Task", "Skill", "Skills", "Ollama", "WhatsApp", "OpenAI", "Gemini", "OpenRouter",
            "Grok", "xAI", "Authorization", "Bearer",
            "Opus", "Sonnet", "Haiku", "Linux", "Agent", "Agents", "Plugins", "Chrome", "GNOME", "Node",
            "Gdk", "Gtk", "Pango", "Programs", "Accept", "Claude Code", "Python", "Windows", "macOS", "Git"}


def looks_french(s: str) -> bool:
    """Texte d'écran en français : des lettres, une tournure française, pas un identifiant."""
    t = s.strip()
    if len(t) < 2 or not re.search(r"[A-Za-zÀ-ÿ]{2}", t):
        return False
    if re.fullmatch(r"[\w.\-/:#@*{}]+", t) and not re.search(r"[À-ÿ]", t):
        return False  # identifiant, chemin, clé
    if t.startswith(("http://", "https://")):
        return False
    return bool(FRENCH.search(PLACEHOLDER.sub(" ", t)))


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args \
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            return node.args[0].value
        return _name(node.func)
    if isinstance(node, ast.Subscript):
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            return node.slice.value
        return _name(node.value)
    if isinstance(node, ast.BoolOp) and node.values:
        return _name(node.values[0])
    return "x"


def _clean(name: str) -> str:
    name = re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_") or "x"
    return name if re.match(r"[a-z_]", name) else f"v_{name}"


def templates(node: ast.AST) -> list[str]:
    """Modèles possibles d'une expression de texte : littéral, f-string, `a + b`, `a if c else b`."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        out, used = [], {}
        for part in node.values:
            if isinstance(part, ast.Constant):
                out.append(str(part.value))
            elif isinstance(part, ast.FormattedValue):
                inner = templates(part.value) if isinstance(part.value, (ast.IfExp, ast.Constant, ast.JoinedStr)) else []
                if inner and all(not PLACEHOLDER.search(i) and looks_french(i) for i in inner) and len(inner) == 1:
                    out.append(inner[0])
                    continue
                base = _clean(_name(part.value))
                used[base] = used.get(base, 0) + 1
                out.append("{" + (base if used[base] == 1 else f"{base}{used[base]}") + "}")
        return ["".join(out)]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = templates(node.left), templates(node.right)
        if left and right:
            return [a + b for a in left for b in right][:16]
        return left or right
    if isinstance(node, ast.IfExp):
        return templates(node.body) + templates(node.orelse)
    if isinstance(node, ast.BoolOp):
        out = []
        for v in node.values:
            out += templates(v)
        return out
    return []


def _parents(tree: ast.AST) -> dict[int, ast.AST]:
    parents = {}
    for p in ast.walk(tree):
        for c in ast.iter_child_nodes(p):
            parents[id(c)] = p
    return parents


def _in_log_call(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
    cur = node
    while id(cur) in parents:
        cur = parents[id(cur)]
        if isinstance(cur, ast.Call):
            f = cur.func
            if isinstance(f, ast.Attribute) and f.attr in LOG_FUNCS and isinstance(f.value, ast.Name) \
                    and f.value.id in ("log", "logging", "logger", "_log"):
                return True
            if isinstance(f, ast.Name) and f.id == "print":
                return True
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            return False
    return False


def _docstrings(tree: ast.AST) -> set[int]:
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
            first = n.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def _outermost_text(node: ast.AST, parents: dict[int, ast.AST]) -> ast.AST:
    """Remonte aux expressions de texte englobantes (f-string, +, if/else) : un seul modèle."""
    cur = node
    while id(cur) in parents:
        p = parents[id(cur)]
        if isinstance(p, ast.JoinedStr) or isinstance(p, ast.FormattedValue) \
                or (isinstance(p, ast.BinOp) and isinstance(p.op, ast.Add)) or isinstance(p, ast.IfExp) \
                and cur is not p.test or isinstance(p, ast.BoolOp):
            cur = p
            continue
        break
    return cur


# Repérés par le contexte mais jamais affichés : consignes envoyées à Claude (pas à traduire).
NOT_SCREEN = {
    'Révision du plan {pid} (version {version}) demandée par l\'utilisateur. Ses remarques :\n\nLis {plan_path}. '
    '{replies_hint} Puis republie le plan corrigé EN ENTIER avec plan_submit(plan_id="{pid}", …), mêmes rubriques. '
    'Ensuite réponds seulement [SILENT].',
    "[Rappel automatique de la mémoire par Astrel, pas un message de l'utilisateur ; à lire avec memory_read seulement "
    "si utile] .",
}

# Libellé d'un seul mot ou titre (« Outils », « Paquet de skills ») : lu seulement en mode FULL.
LABEL = re.compile(r"[A-ZÀ-Ý][a-zà-ÿ'’-]+(?: [A-Za-zÀ-ÿ'’-]+)*")


def scan_file(path: Path, full: bool, demo: bool = False) -> list[tuple[int, str]]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    parents = _parents(tree)
    docs = _docstrings(tree)
    seen: set[int] = set()
    found: list[tuple[int, str]] = []

    def add(expr: ast.AST, visible: bool = False) -> None:
        """`visible` : contexte d'écran sûr (argument `text=` d'une notice…) : tout texte à mots compte."""
        if id(expr) in seen:
            return
        seen.add(id(expr))
        for t in templates(expr):
            words = PLACEHOLDER.sub("", t).strip()
            label = LABEL.fullmatch(t.strip()) and t.strip() not in NOT_TEXT
            phrase = " " in words and re.search(r"[a-zà-ÿ]{3}", words)  # des mots, pas un identifiant
            if t in NOT_SCREEN:
                continue
            if looks_french(t) or ((full or visible) and label) or (visible and phrase):
                found.append((getattr(expr, "lineno", 0), t))

    if full:
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
                if _in_log_call(n, parents):
                    continue
                add(_outermost_text(n, parents))
        return found

    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            fname = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            if demo and fname == "publish" and n.args and isinstance(n.args[0], ast.Constant) \
                    and n.args[0].value != "notice":
                continue  # démo : conversation, plans, comptes rendus = CONTENU (en français, voulu)
            if demo and fname == "it":  # réglage de la démo : it(clé, libellé, aide, …)
                for a in n.args[1:3]:
                    add(a, visible=True)
            if fname in PUBLISHERS:
                for kw in n.keywords:
                    if kw.arg in VISIBLE_KW:
                        add(kw.value, visible=True)
            if fname in SPEAKERS and n.args:
                add(n.args[0], visible=True)
        elif isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if isinstance(k, ast.Constant) and k.value in VISIBLE_KEYS and v is not None:
                    add(v, visible=True)
        elif isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) \
                and n.targets[0].id in SPOKEN_CONSTS:
            for elt in (n.value.elts if isinstance(n.value, ast.Tuple) else [n.value]):
                add(elt, visible=True)
        elif isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) \
                and n.targets[0].id in VISIBLE_VARS:
            add(n.value)  # `text = f"…"` puis `publish("notice", text=text)` : texte composé à part
    return found


def modules() -> list[tuple[Path, bool]]:
    out = []
    for p in sorted((ROOT / "astrel").rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        if rel in SKIP or rel.startswith(SKIP_DIRS) or "__pycache__" in rel:
            continue
        out.append((p, rel in FULL))
    out += [(ROOT / rel, True) for rel in FULL if not rel.startswith("astrel/")]
    return out


# Démo du HUD (tools/hud_demo.py) : ses Réglages, notices et boutons imitent ceux du cœur et
# passent par le même catalogue ; le contrôle en anglais (tools/hud_check_langue.py) la parcourt.
DEMO = "tools/hud_demo.py"


def inventaire() -> list[tuple[str, int, str]]:
    """[(fichier, ligne, modèle)] de tous les textes d'écran repérés, sans doublon de modèle."""
    seen, out = set(), []
    for path, full in [*modules(), (ROOT / DEMO, False)]:
        demo = path == ROOT / DEMO
        for line, t in scan_file(path, full, demo):
            if t in seen:
                continue
            seen.add(t)
            out.append((path.relative_to(ROOT).as_posix(), line, t))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tout", action="store_true", help="tout l'inventaire")
    ap.add_argument("--json", action="store_true", help="manquants en JSON à traduire")
    args = ap.parse_args()
    from astrel import i18n

    inv = inventaire()
    missing = [(f, ln, t) for f, ln, t in inv if not i18n.covered(t)]
    if args.json:
        print(json.dumps({t: "" for _f, _l, t in missing}, ensure_ascii=False, indent=1))
        return 1 if missing else 0
    for f, ln, t in (inv if args.tout else missing):
        print(f"{f}:{ln}: {t}")
    print(f"{len(inv)} textes d'écran, {len(missing)} sans traduction anglaise", file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
