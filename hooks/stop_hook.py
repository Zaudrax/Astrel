#!/usr/bin/env python3
"""Hook Stop d'Astrel : garde « annonce sans action » (15/10, n° 1 de
`docs/idees/TRI-TOP-IDEES.md`, idées 5 et 6 de `docs/idees/HERMES-agent-coeur.md`)
et garde « vérifier avant de finir » (01/11, idée 7 du même fichier, accord de l'utilisateur).

Quand un tour de l'utilisateur se termine
- par une ANNONCE (« je vais maintenant… », « je m'en occupe », « ensuite je… »)
  sans que rien ne suive, ou
- par un FRAGMENT (phrase coupée, « Voici ce que j'ai trouvé : ») après des outils,
le hook relance le tour avec « continue maintenant » (`decision: block`) ;
- après une MODIFICATION DE CODE (Write, Edit, MultiEdit, NotebookEdit, ou `sed -i`, `>`,
  `tee` vers un fichier de code) qu'aucune vérification n'a suivie (tests, vérification de
  syntaxe, build, lint, script lancé, agent testeur ou relecteur), il le relance avec
  « vérifie avant de finir ». Même garde pour un SOUS-AGENT qui s'arrête (`SubagentStop`).

Bornes, contre la boucle et les faux positifs :
- au plus `MAX_RELANCES` (2) par message de l'utilisateur et par garde ; le compteur est dans
  `runtime/stop-guard.json` (annonce) et `runtime/stop-verif.json` (vérification), clé =
  l'entrée du message dans le transcript (ou l'agent), donc remis à zéro à chaque message ;
- `stop_hook_active` (le CLI continue déjà à cause d'un hook Stop) : relance
  seulement si c'est la NÔTRE, d'après les compteurs ;
- tours automatiques jamais relancés : réflexe, alerte de boucle, relève (et sa
  sauvegarde), mission, tâche de fond (`<task-notification>` du CLI) ; un sous-agent
  lancé par l'un d'eux non plus ;
- arrêt d'urgence posé, transcript illisible, message introuvable : rien ;
- `ASTREL_STOP_GUARD=0` coupe la garde d'annonce, `ASTREL_STOP_VERIF=0` celle de
  vérification, `ASTREL_STOP_VERIF_AGENTS=0` celle des sous-agents seulement.

Ce n'est PAS un garde-fou : une panne ici ne bloque rien, le hook se tait.
Format reçu du CLI (2.1.285, vérifié en réel en stream-json) : `session_id`,
`transcript_path`, `cwd`, `prompt_id`, `hook_event_name`, `stop_hook_active`,
`last_assistant_message` (texte du dernier message), `background_tasks`,
`session_crons` ; `SubagentStop` : en plus `agent_id`, `agent_type`,
`agent_transcript_path`. La raison du blocage revient au modèle comme message
« Stop hook feedback: … » (isMeta), le tour continue sous le même `result`.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astrel import config  # noqa: E402  (léger : json, os, pathlib)

MAX_RELANCES = int(os.environ.get("ASTREL_STOP_GUARD_MAX", "2"))
STATE_FILE = config.RUNTIME / "stop-guard.json"
VERIF_FILE = config.RUNTIME / "stop-verif.json"
LOG_FILE = config.LOGS / "stop-guard.jsonl"

# Annonce : réponse courte (Hermes : 400 caractères), motif cherché dans la fin.
ANNONCE_MAX = 400
TAIL = 160
# Transcript lu par la fin, jusqu'à ce plafond (images en base64 : lignes lourdes).
READ_MAX = 32 * 1024 * 1024

RAISON_ANNONCE = (
    "[Garde d'Astrel] Tu as annoncé une action sans la faire. Continue maintenant : fais-la avec "
    "les outils, sans la ré-annoncer. Si tu attends l'utilisateur ou une tâche de fond, n'ajoute rien."
)
RAISON_FRAGMENT = (
    "[Garde d'Astrel] Ta réponse s'arrête en pleine phrase. Continue maintenant : termine, sans "
    "répéter ce qui est déjà dit. Si elle était complète, n'ajoute rien."
)
# 01/11 : ≈ 70 tokens (+ la vérification elle-même). Chemins : 4 noms au plus.
RAISON_VERIF = (
    "[Garde d'Astrel] Tu as modifié du code ({fichiers}) sans rien vérifier ensuite. Avant de "
    "finir : lance les tests ciblés (ou une vérification de syntaxe, un build). Si c'est impossible "
    "ou déjà fait autrement, dis-le en une phrase, sans tout répéter."
)

# Préfixes posés par le cœur sur les tours que l'utilisateur n'a pas lancés
# (`core.handle`, `boucles.py`, `relief.SAVE_PROMPT`, `mission.py`). Attention :
# l'amorce de relève (« [Nouvelle session : relève automatique… ») précède un VRAI
# message, elle ne doit pas compter.
AUTO_MARKERS = (
    ("réflexe", "[Réflexe programmé, l'utilisateur n'a rien demandé"),
    ("alerte", "[Alerte automatique d'Astrel"),
    ("relève", "[Relève automatique d'Astrel"),
    ("mission", "[Mission autonome lancée par l'utilisateur"),
    ("fond", "<task-notification>"),
    # 01/11 (relecture) : messages automatiques de l'équipe (`equipe.py`, `courrier.py`), reprises après
    # l'arrêt pour quota (`quota_arret.py`) ou après la relève (`relief.py`, consigne d'un agent relancé).
    ("équipe", "[Équipe, message automatique du cœur"),
    ("quota", "[Reprise après l'arrêt pour quota"),
    ("relève", "[Reprise après la relève"),
    ("relève", "[Relance incomplète"),
)

_INF = r"[a-zà-ÿœ]+(?:er|ir|re|oir)\b"
# Infinitifs qui ne sont pas une action à faire tout de suite.
_PAS_ACTION = (r"(?:être|avoir|devoir|pouvoir|falloir|attendre|patienter|rester|arrêter|"
               r"dormir|partir|laisser|garder|retenir|noter|essayer\s+de\s+retenir)\b")

# Annonces FORTES : action immédiate et explicite.
_FORT = re.compile(
    r"\bje\s+vais\s+(?:maintenant|tout\s+de\s+suite|dès\s+maintenant|de\s+ce\s+pas|donc|d'abord|"
    r"ensuite|alors|à\s+présent)\b"
    r"|\b(?:ensuite|puis|maintenant|à\s+présent|après\s+ça|et\s+maintenant),?\s+je\s+"
    r"(?:vais|lance|passe|regarde|vérifie|cherche|m'occupe|corrige|lis|ouvre|écris|ajoute|mets|fais|"
    r"reprends|continue|poursuis|attaque|commence|relance|teste|prépare|rédige)\b"
    r"|\bje\s+(?:commence|passe|enchaîne)\s+(?:par|à|au|aux|avec|sur)\b",
    re.I)
# Annonces FAIBLES : souvent légitimes quand une tâche de fond travaille déjà.
_FAIBLE = re.compile(
    # (pronom suivi d'un blanc, sinon « laisser » se lirait « la » + « isser »)
    r"\bje\s+vais\s+(?:(?:le|la|les|lui|leur|te|y|en)\s+|[lt]')?(?!" + _PAS_ACTION + r")" + _INF
    + r"|\bje\s+m'en\s+(?:occupe|charge)\b"
    r"|\bje\s+m'y\s+(?:mets|attelle|colle)\b"
    r"|\bje\s+(?:lance|regarde|vérifie|cherche|corrige|prépare)\s+(?:ça|cela|tout\s+ça)\b"
    r"|\blaisse[- ]moi\s+(?:vérifier|regarder|chercher|voir|lancer|lire|ouvrir|corriger)\b"
    r"|\bc'est\s+parti\b|\bon\s+y\s+va\b",
    re.I)
# Fins légitimes : question, proposition, attente de l'utilisateur ou d'une tâche, plus tard.
_VETO = re.compile(
    r"\bsi\s+tu\b|\bveux[- ]tu\b|\btu\s+veux\b|\bdis[- ]moi\b|\bquand\s+tu\b|\bdès\s+que\s+tu\b"
    r"|\bton\s+(?:feu\s+vert|accord|signal|go|ok)\b|\bj'attends\b|\bje\s+peux\b|\bpréfères\b"
    # « Donne-moi le lien et je m'y mets » : il attend quelque chose de l'utilisateur.
    r"|\b(?:donne|envoie|montre|précise|indique|colle|passe|confirme|explique|redis|transmets|"
    r"partage|dicte|copie|redonne|renvoie)[- ]moi\b"
    r"|\bje\s+te\s+(?:préviens|tiens\s+au\s+courant|dis\s+(?:quand|dès|ce\s+qu)|fais\s+signe|redis|"
    r"recontacte|rappelle)\b|\bje\s+reviens\s+vers\s+toi\b"
    r"|\bquand\s+(?:il|elle|ils|elles|ça|l'\w+|le\s+\w+|la\s+\w+|les\s+\w+)\s+(?:aura|auront|a|ont|sera|seront)\b"
    r"|\bdès\s+qu'(?:il|elle|ils|elles)\b|\bune\s+fois\s+(?:que|qu'|terminé|fini)"
    r"|\ben\s+(?:fond|arrière-plan|parallèle)\b"
    r"|\bdemain\b|\bplus\s+tard\b|\bce\s+soir\b|\bprochaine\s+session\b|\bà\s+\d{1,2}\s*h",
    re.I)
# Fragment : texte qui finit sur un deux-points, une virgule ou un mot de liaison
# (une fin sans ponctuation seule ne suffit pas : « Plan P3 déposé » est complet).
_LIAISON = re.compile(
    r"(?:[:,;(\-–]|\b(?:et|ou|mais|puis|donc|car|de|des|du|le|la|les|un|une|pour|avec|sur|dans|que|"
    r"qui|à|au|aux|en|par|ton|ta|tes|mon|ma|mes|ce|cette|son|sa|ses))\s*$", re.I)


def _norm(text: str) -> str:
    return " ".join(str(text or "").replace("’", "'").split())


def _derniere_phrase(text: str) -> str:
    """Dernière phrase de la fin du texte (ce qu'on entend en dernier)."""
    tail = text[-TAIL:]
    parts = [p for p in re.split(r"(?<=[.!?…])\s+", tail) if p.strip()]
    return parts[-1] if parts else ""


def evaluate(text: str, *, tools_used: bool, background: bool) -> str:
    """« annonce », « fragment » ou "" (rien à faire). Fonction pure."""
    t = _norm(text)
    if not t:
        return ""  # rien dit après des outils : souvent voulu (post_report, compte rendu silencieux)
    last = _derniere_phrase(t)
    if len(t) <= ANNONCE_MAX and last and not last.rstrip().endswith("?") and not _VETO.search(last):
        if _FORT.search(last) or (not background and _FAIBLE.search(last)):
            return "annonce"
    if tools_used and "```" not in t[-TAIL:] and _LIAISON.search(t):
        # Seule une fin sur une liaison, une virgule ou un deux-points est un fragment :
        # « Plan P3 déposé », « Oui », « Terminé, 3 fichiers modifiés » sont complets.
        return "fragment"
    return ""


# --- 01/11 : « vérifier avant de finir » (idée 7 de HERMES-agent-coeur.md) ---------------------

# Fichiers de CODE (la prose, les réglages et les données n'entrent pas en compte).
CODE_EXTS = frozenset({
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts", ".vue", ".svelte",
    ".sh", ".bash", ".zsh", ".rs", ".go", ".java", ".kt", ".kts", ".swift", ".c", ".h", ".cc", ".cpp",
    ".hpp", ".rb", ".php", ".lua", ".pl", ".dart", ".ipynb"})
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
# Une commande qui VÉRIFIE (qu'elle réussisse ou non : un échec dit, c'est un compte rendu honnête).
_VERIF = re.compile(
    r"\bpytest\b|\bpy\.test\b"
    r"|-m\s+(?:pytest|unittest|py_compile|compileall|pyflakes|mypy|ruff|flake8|pylint|pyright|doctest|tabnanny)\b"
    r"|(?:^|[\s;&|(/])(?:ruff|mypy|flake8|pylint|pyright|pyflakes|shellcheck|tsc|eslint|vitest|jest|mocha)\b"
    r"|\b(?:black|isort|prettier)\s+(?:[^;&|]*\s)?--check\b"
    r"|\bnode\s+(?:[^;&|]*\s)?(?:--test|--check|-c)\b|\bnode\s+\S*(?:test|check|spec)\S*"
    r"|\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:test|build|lint|check|typecheck|verify)\b|\bnpm\s+t\b"
    r"|\b(?:npx|pnpx|bunx)\s+(?:-y\s+)?(?:tsc|eslint|vitest|jest|playwright\s+test|prettier\s+--check)\b"
    r"|\b(?:bash|sh|zsh|dash)\s+-n\b"
    r"|\b(?:cargo|go)\s+(?:test|check|build|vet|clippy)\b|\bmake(?:\s+(?:test|check|build|all|lint))?\s*(?:$|[;&|])"
    r"|\bhud_check\w*|\bi18n_inventaire\b|\brun\.sh\s+(?:doctor|--doctor|--text)\b|\bcheck_install\b"
    r"|\bpython[\d.]*\s+(?:-\w+\s+)*\S*(?:test|check|verif|demo)\w*\.py\b"
    r"|\bpython[\d.]*\s+(?:-\w+\s+)*-c\s+[\"']?[^\"']{0,80}\bimport\b",
    re.I | re.M)
# Lancer un script (le fichier modifié ou un script d'essai) est aussi une vérification.
_RUN = re.compile(r"(?:^|[\s;&|(/])(?:python[\d.]*|node|deno|bun|tsx|ts-node|bash|sh|zsh|ruby|perl|php)"
                  r"\s+(?:-\S+\s+)*[^\s;&|<>]+\.(?:py|js|mjs|cjs|ts|tsx|mts|sh|rb|pl|php)\b|\bgo\s+run\b")
_AGENT_VERIF = re.compile(r"\b(?:test|vérif|verif|relecture|relis|relecteur|testeur|contrôle|contrôler|review)", re.I)
_AGENTS_QUI_VERIFIENT = {"testeur", "relecteur", "auditeur", "code-reviewer"}


def _lance(cmd: str, path: str) -> bool:
    """Le fichier modifié est-il le PROGRAMME d'une des commandes (`./outil.sh`, `/x/y.py args`) ?"""
    nom = re.escape(os.path.basename(path))
    return bool(re.search(r"(?:^|[;&|]\s*|\b(?:then|do|exec|timeout\s+\S+)\s+)(?:\S*/)?" + nom + r"(?:\s|$)", cmd, re.M))


def _code(path: str) -> bool:
    """Fichier de code d'un projet (pas un brouillon de /tmp ni du dossier de travail de Claude)."""
    p = str(path or "")
    if not p or p.startswith(("/tmp/", "/var/tmp/")):
        return False
    return os.path.splitext(p)[1].lower() in CODE_EXTS


def _cibles_bash(cmd: str, cwd: str) -> list[str]:
    try:
        from astrel import instantanes  # lecture évidente des écritures shell (`> f`, `tee`, `sed -i`)
        return instantanes.cibles_bash(cmd, cwd)
    except Exception:  # noqa: BLE001
        return []


def a_verifier(uses: list[dict], cwd: str = "") -> list[str]:
    """Fichiers de code modifiés APRÈS la dernière vérification du tour (vide : rien à demander).
    `uses` : les `tool_use` du tour, dans l'ordre. Fonction pure (hors lecture des cibles shell)."""
    pending: dict[str, None] = {}
    for u in uses:
        name = str(u.get("name") or "")
        inp = u.get("input") if isinstance(u.get("input"), dict) else {}
        if name in WRITE_TOOLS:
            p = str(inp.get("file_path") or inp.get("notebook_path") or "")
            if _code(p):
                pending[p] = None
        elif name == "Bash":
            cmd = str(inp.get("command") or "")
            for p in _cibles_bash(cmd, cwd):
                if _code(p):
                    pending[p] = None
            if _VERIF.search(cmd) or _RUN.search(cmd) or any(_lance(cmd, p) for p in pending):
                pending.clear()
        elif name in ("Agent", "Task"):
            kind = str(inp.get("subagent_type") or "")
            if kind in _AGENTS_QUI_VERIFIENT or _AGENT_VERIF.search(str(inp.get("description") or "")):
                pending.clear()
    return list(pending)


def source_of(prompt: str) -> str:
    """Origine du message qui a ouvert le tour : "" = l'utilisateur."""
    for name, marker in AUTO_MARKERS:
        if marker in prompt[:4000]:
            return name
    return ""


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _prompt_of(rec: dict, agent: bool = False) -> tuple[str, str] | None:
    """(clé, texte) si l'entrée est un message qui ouvre ou relance un tour. `agent` : transcript
    d'un sous-agent (toutes ses entrées sont « sidechain »)."""
    if rec.get("isSidechain") and not agent:
        return None
    att = rec.get("attachment")
    if agent and (rec.get("type") == "attachment" or (rec.get("origin") or {}).get("kind") == "task-notification"
                  or "<task-notification>" in _text_of((rec.get("message") or {}).get("content"))[:200]):
        return None  # sous-agent : une tâche de fond finie ne commence pas un nouveau travail
    if rec.get("type") == "attachment" and isinstance(att, dict) and att.get("type") == "queued_command":
        # Message arrivé en plein tour (l'utilisateur, réflexe, tâche de fond finie).
        return str(rec.get("uuid") or ""), _text_of(att.get("prompt"))
    if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isCompactSummary"):
        return None
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return None
    text = _text_of(content)
    if not text.strip():
        return None
    if (rec.get("origin") or {}).get("kind") == "task-notification" and "<task-notification>" not in text:
        text = "<task-notification>" + text
    return str(rec.get("uuid") or rec.get("promptId") or ""), text


def read_turn(path: str | Path, agent: bool = False) -> dict | None:
    """Lit la fin du transcript : message qui a ouvert le tour, outils appelés depuis.
    None si illisible ou introuvable (le hook ne fait alors rien)."""
    p = Path(path)
    try:
        size = p.stat().st_size
        chunk = 256 * 1024
        while True:
            start = max(0, size - chunk)
            with p.open("rb") as fh:
                fh.seek(start)
                data = fh.read()
            lines = data.split(b"\n")
            if start > 0:
                lines = lines[1:]  # première ligne coupée
            after: list[dict] = []
            for raw in reversed(lines):
                if not raw.strip():
                    continue
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                found = _prompt_of(rec, agent)
                if found:
                    key, text = found
                    after.reverse()
                    mine = [r for r in after if r.get("type") == "assistant" and (agent or not r.get("isSidechain"))]
                    uses = [b for r in mine for b in ((r.get("message") or {}).get("content") or [])
                            if isinstance(b, dict) and b.get("type") == "tool_use"]
                    bg = any(u.get("name") in ("Agent", "Task")
                             or (u.get("input") or {}).get("run_in_background") for u in uses)
                    last_text = _text_of((mine[-1].get("message") or {}).get("content")) if mine else ""
                    return {"key": key, "prompt": text, "tools_used": bool(uses), "uses": uses,
                            "background": bg, "last_text": last_text}
                after.append(rec)
            if start == 0 or chunk >= READ_MAX:
                return None
            chunk *= 4
    except OSError:
        return None


def _load_state(path: Path | None = None) -> dict:
    path = path or STATE_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(key: str, count: int, path: Path | None = None) -> None:
    path = path or STATE_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"key": key, "count": count, "at": time.time()}), encoding="utf-8")
    tmp.replace(path)


def _verif_count(key: str) -> int:
    """Relances « vérifie » déjà faites pour ce message ou cet agent (`VERIF_FILE` : les 50 derniers)."""
    entry = (_load_state(VERIF_FILE).get("cles") or {}).get(key)
    return int(entry.get("count") or 0) if isinstance(entry, dict) else 0


def _verif_save(key: str, count: int) -> None:
    cles = _load_state(VERIF_FILE).get("cles")
    cles = cles if isinstance(cles, dict) else {}
    cles[key] = {"count": count, "at": time.time()}
    cles = dict(sorted(cles.items(), key=lambda kv: float((kv[1] or {}).get("at") or 0))[-50:])
    tmp = VERIF_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"cles": cles}), encoding="utf-8")
    tmp.replace(VERIF_FILE)


def _log(kind: str, action: str, text: str, count: int) -> None:
    """Une ligne par déclenchement (relance ou plafond) : de quoi mesurer les faux positifs."""
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind,
                                 "action": action, "count": count, "fin": _norm(text)[-160:]},
                                ensure_ascii=False) + "\n")
    except OSError:
        pass


def _off(var: str) -> bool:
    return os.environ.get(var, "1").strip().lower() in ("0", "off", "non", "false")


def _raison_verif(fichiers: list[str]) -> str:
    noms = list(dict.fromkeys(os.path.basename(f) for f in fichiers))
    liste = ", ".join(noms[:4]) + (f" et {len(noms) - 4} autre(s)" if len(noms) > 4 else "")
    return RAISON_VERIF.format(fichiers=liste)


def _decide_verif(key: str, uses: list[dict], cwd: str, active_ours: bool, active: bool) -> str | None:
    fichiers = a_verifier(uses, cwd)
    if not fichiers:
        return None
    count = _verif_count(key)
    if active and not active_ours and count == 0:
        return None  # une continuation qui n'est pas la nôtre : ne pas s'en mêler
    if count >= MAX_RELANCES:
        _log("verif", "plafond", " ".join(fichiers), count)
        return None
    _verif_save(key, count + 1)
    _log("verif", "relance", " ".join(fichiers), count + 1)
    return _raison_verif(fichiers)


def _premier_prompt(path: str) -> tuple[str, str] | None:
    """(promptId, texte) du message qui a lancé un sous-agent (première entrée « user » de son
    transcript ; le CLI y met le `promptId` du message du fil principal)."""
    try:
        with open(path, "rb") as fh:
            for raw in fh.read(256 * 1024).split(b"\n")[:200]:
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("type") == "user":
                    return str(rec.get("promptId") or ""), _text_of((rec.get("message") or {}).get("content"))
    except OSError:
        return None
    return None


def _prompt_par_id(path: str, prompt_id: str) -> str | None:
    """Texte du message du fil principal qui porte ce `promptId` (lu par la fin) ; None : introuvable."""
    p = Path(path)
    try:
        size = p.stat().st_size
        chunk = 1024 * 1024
        while True:
            start = max(0, size - chunk)
            with p.open("rb") as fh:
                fh.seek(start)
                lines = fh.read().split(b"\n")
            for raw in reversed(lines[1:] if start else lines):
                if prompt_id.encode() not in raw:
                    continue
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("promptId") == prompt_id:
                    found = _prompt_of(rec)
                    if found:
                        return found[1]
            if start == 0 or chunk >= READ_MAX:
                return None
            chunk *= 4
    except OSError:
        return None


def _decide_agent(payload: dict) -> str | None:
    """`SubagentStop` : seule la garde de vérification (un sous-agent ne parle pas à l'utilisateur).
    01/11 (relecture) : on juge le message qui a LANCÉ l'agent (pas le dernier du fil principal) et la
    consigne de l'agent lui-même ; le codeur est laissé au relais de l'équipe (testeur, relecteur)."""
    if _off("ASTREL_STOP_VERIF") or _off("ASTREL_STOP_VERIF_AGENTS"):
        return None
    agent_id = str(payload.get("agent_id") or "")
    if not agent_id:
        return None
    if payload.get("agent_type") == "codeur" and not _off("ASTREL_EQUIPE_APRES_CODEUR"):
        return None  # `equipe.after_codeur` lance déjà le testeur et le relecteur
    agent_path = str(payload.get("agent_transcript_path") or "")
    first = _premier_prompt(agent_path)
    if not first or not first[0] or source_of(first[1]):
        return None  # consigne automatique (relève, quota, équipe…), ou impossible de savoir
    lanceur = _prompt_par_id(str(payload.get("transcript_path") or ""), first[0])
    if lanceur is None or source_of(lanceur):
        return None  # lancé par un tour automatique, ou message introuvable : on passe
    turn = read_turn(agent_path, agent=True)
    if not turn:
        return None
    return _decide_verif(f"agent:{agent_id}", turn["uses"], str(payload.get("cwd") or ""),
                         _verif_count(f"agent:{agent_id}") > 0, bool(payload.get("stop_hook_active")))


def decide(payload: dict) -> str | None:
    """Raison du blocage, ou None pour laisser le tour finir."""
    if config.ESTOP_FILE.exists():
        return None  # arrêt d'urgence : rien ne repart
    if payload.get("hook_event_name") == "SubagentStop":
        return _decide_agent(payload)
    annonce_on, verif_on = not _off("ASTREL_STOP_GUARD"), not _off("ASTREL_STOP_VERIF")
    if not (annonce_on or verif_on):
        return None
    turn = read_turn(payload.get("transcript_path") or "")
    if not turn or not turn["key"] or source_of(turn["prompt"]):
        return None  # tour automatique, ou impossible de savoir : on passe
    active = bool(payload.get("stop_hook_active"))
    state = _load_state()
    count = int(state.get("count") or 0) if state.get("key") == turn["key"] else 0
    vcount = _verif_count(turn["key"])
    ours = count > 0 or vcount > 0
    if annonce_on:
        text = payload.get("last_assistant_message")
        if not isinstance(text, str):
            text = turn["last_text"]
        background = turn["background"] or bool(payload.get("background_tasks"))
        kind = evaluate(text, tools_used=turn["tools_used"], background=background)
        if kind and not (active and not ours):  # une continuation qui n'est pas la nôtre : rien
            if count >= MAX_RELANCES:
                _log(kind, "plafond", text, count)
            else:
                _save_state(turn["key"], count + 1)
                _log(kind, "relance", text, count + 1)
                return RAISON_ANNONCE if kind == "annonce" else RAISON_FRAGMENT
    if verif_on:
        return _decide_verif(turn["key"], turn["uses"], str(payload.get("cwd") or ""), ours, active)
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or payload.get("hook_event_name", "Stop") not in ("Stop", "SubagentStop"):
            return 0
        reason = decide(payload)
        if reason:
            print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    except Exception:
        return 0  # garde de confort : dans le doute, laisser le tour finir
    return 0


if __name__ == "__main__":
    sys.exit(main())
