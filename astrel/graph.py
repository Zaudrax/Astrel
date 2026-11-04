"""Le graphe neuronal d'Astrel : ce qu'il sait FAIRE et ce qu'il SAIT.

Nœuds :
- le cœur (Astrel) et son cerveau (le modèle Claude) ;
- les outils natifs de Claude Code, les skills, les agents ;
- les serveurs MCP et leurs outils ;
- les capteurs (EYES, WATCH, HOLO, FOCUS, REFLEX) ;
- les notes de mémoire du coffre (`vault/`, liens `[[wiki]]` compris) ;
- les projets de l'utilisateur.

Le graphe se construit d'abord SANS Claude (disque seul) : au démarrage, le
CLI n'annonce ses outils qu'à la première requête, et en envoyer une pour rien
coûterait sur l'abonnement. Il est enrichi ensuite par l'`init` du cerveau.

Chaque appel d'outil incrémente le « poids » du nœud : les connecteurs qui
servent grossissent, comme des synapses renforcées.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

from . import config
from .events import bus

log = logging.getLogger("astrel.graph")

VAULT = config.ROOT / "vault"
CLAUDE_HOME = Path.home() / ".claude"
USAGE_FILE = config.RUNTIME / "graph-usage.json"
# Serveur MCP d'Astrel sous son nom actuel ou l'ancien (renommage du 29/10) : `_load_usage` les fusionne.
_MCP_ASTREL = re.compile(r"^mcp:(?:astrel|jarvis):(.+)$")
MCP_TOOLS_MAX = 30

# Outils natifs connus, présents même avant l'`init` du CLI.
NATIVE_TOOLS = {
    "Bash": "Terminal : exécuter des commandes",
    "Read": "Lire des fichiers",
    "Write": "Créer des fichiers",
    "Edit": "Modifier des fichiers",
    "Grep": "Chercher dans le code",
    "Glob": "Trouver des fichiers",
    "WebSearch": "Recherche sur le web",
    "WebFetch": "Lire une page web",
    "Task": "Déléguer à un sous-agent",
    "Skill": "Utiliser une compétence",
    "TodoWrite": "Liste de tâches",
    "NotebookEdit": "Carnets Jupyter",
}

SENSORS = {
    "EYES": "La webcam sait si tu es là",
    "WATCH": "Voit ton écran",
    "HOLO": "Gestes de la main",
    "FOCUS": "Ne pas déranger",
    "REFLEX": "Routines automatiques",
}

GROUPS = {
    "core": "Cœur", "brain": "Cerveau", "hub": "Pôle", "tool": "Outil",
    "skill": "Skill", "agent": "Agent", "mcp": "Serveur MCP", "mcp_tool": "Outil MCP",
    "sensor": "Capteur", "note": "Note", "project": "Projet", "voice": "Voix",
}

_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
_MDLINK = re.compile(r"\]\(([^)]+\.md)\)")


class Graph:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._init: dict[str, Any] = {}
        self._sensors: dict[str, str] = {name: "off" for name in SENSORS}
        self._voice = ""
        self._usage: Counter[str] = Counter(self._load_usage())
        self._vault_sig = ""
        self._last_publish = 0.0
        self._pending = False

    # --- sources ---

    def set_init(self, info: dict[str, Any]) -> None:
        with self._lock:
            self._init = info or {}
        self.publish()

    def set_sensor(self, name: str, status: str) -> None:
        with self._lock:
            self._sensors[name] = status
        self.publish()

    def set_voice(self, name: str) -> None:
        with self._lock:
            self._voice = name
        self.publish()

    def record_use(self, tool: str) -> str:
        """Compte un appel d'outil ; rend l'id du nœud touché (pour l'animation)."""
        node = self.node_for_tool(tool)
        with self._lock:
            self._usage[node] += 1
            usage = dict(self._usage)
        try:
            USAGE_FILE.write_text(json.dumps(usage), encoding="utf-8")
        except OSError:
            pass
        return node

    @staticmethod
    def node_for_tool(tool: str) -> str:
        if tool.startswith("mcp__"):
            parts = tool.split("__", 2)
            if len(parts) == 3:
                return f"mcp:{parts[1]}:{parts[2]}"
        return f"tool:{tool}"

    def vault_changed(self) -> bool:
        sig = self._vault_signature()
        if sig != self._vault_sig:
            self._vault_sig = sig
            return True
        return False

    # --- construction ---

    def build(self) -> dict[str, Any]:
        with self._lock:
            init = dict(self._init)
            sensors = dict(self._sensors)
            voice = self._voice
            usage = dict(self._usage)

        nodes: dict[str, dict] = {}
        links: list[dict] = []

        def node(nid: str, label: str, group: str, desc: str = "", **extra) -> None:
            if nid not in nodes:
                nodes[nid] = {"id": nid, "label": label, "group": group, "desc": desc,
                              "uses": usage.get(nid, 0), **extra}

        def link(a: str, b: str, kind: str = "") -> None:
            if a in nodes and b in nodes and a != b:
                links.append({"source": a, "target": b, "kind": kind})

        node("core", "ASTREL", "core", "Le cœur : écoute, décide, agit, parle")
        model = str(init.get("model") or config.CLAUDE_MODEL or "Claude")
        node("brain", model, "brain", "Le cerveau : Claude Code sur l'abonnement")
        link("core", "brain", "pense")
        for hub, label in (("hub:tools", "Outils"), ("hub:skills", "Skills"), ("hub:agents", "Agents"),
                           ("hub:mcp", "Connecteurs"), ("hub:sensors", "Capteurs"),
                           ("hub:memory", "Mémoire"), ("hub:projects", "Projets")):
            node(hub, label, "hub")
        link("brain", "hub:tools"); link("brain", "hub:skills"); link("brain", "hub:agents")
        link("brain", "hub:mcp"); link("brain", "hub:memory"); link("core", "hub:sensors")
        link("brain", "hub:projects")
        if voice:
            node("voice", voice, "voice", "La voix d'Astrel")
            link("core", "voice", "parle")

        # Outils natifs
        tools = [t for t in (init.get("tools") or list(NATIVE_TOOLS)) if not str(t).startswith("mcp__")]
        for t in tools:
            node(f"tool:{t}", t, "tool", NATIVE_TOOLS.get(t, ""))
            link("hub:tools", f"tool:{t}")

        # Skills : annoncées par le CLI, sinon trouvées sur le disque.
        skills = init.get("skills") or self._disk_skills()
        for s in skills:
            name = str(s)
            plugin = name.split(":", 1)[0] if ":" in name else ""
            node(f"skill:{name}", name.split(":", 1)[-1], "skill", f"Skill{' du plugin ' + plugin if plugin else ''}")
            if plugin:
                node(f"plugin:{plugin}", plugin, "hub", "Paquet de skills")
                link("hub:skills", f"plugin:{plugin}")
                link(f"plugin:{plugin}", f"skill:{name}")
            else:
                link("hub:skills", f"skill:{name}")

        for a in init.get("agents") or []:
            node(f"agent:{a}", str(a).split(":", 1)[-1], "agent", "Sous-agent")
            link("hub:agents", f"agent:{a}")

        # Serveurs MCP et leurs outils
        servers = {str(s.get("name")): s.get("status", "") for s in (init.get("mcp_servers") or []) if isinstance(s, dict)}
        for name in self._configured_mcp():
            servers.setdefault(name, "configuré")
        for name, status in servers.items():
            node(f"mcp:{name}", name, "mcp", f"Serveur MCP — {status}", status=status)
            link("hub:mcp", f"mcp:{name}")
        per_server: dict[str, list[str]] = {}
        for t in init.get("tools") or []:
            t = str(t)
            if t.startswith("mcp__"):
                _, server, tool = t.split("__", 2)
                per_server.setdefault(server, []).append(tool)
        for server, server_tools in per_server.items():
            label = server.replace("claude_ai_", "").replace("_", " ")
            node(f"mcp:{server}", label, "mcp", f"Connecteur — {len(server_tools)} outils", tools=len(server_tools))
            link("hub:mcp", f"mcp:{server}")
            # Un connecteur de 200 outils (Vercel) noyait tout le graphe sous
            # des centaines de points : au-delà de MCP_TOOLS_MAX, on n'affiche
            # que les outils RÉELLEMENT utilisés, le reste est compté dans le nœud.
            shown = server_tools if len(server_tools) <= MCP_TOOLS_MAX else [
                t for t in server_tools if usage.get(f"mcp:{server}:{t}")
            ]
            for tool in shown:
                node(f"mcp:{server}:{tool}", tool.replace("_", " "), "mcp_tool", f"Outil {tool} de {label}")
                link(f"mcp:{server}", f"mcp:{server}:{tool}")

        # Capteurs
        for name, desc in SENSORS.items():
            node(f"sensor:{name}", name, "sensor", desc, status=sensors.get(name, "off"))
            link("hub:sensors", f"sensor:{name}")

        # Mémoire : le coffre de notes
        notes = self._vault_notes()
        for rel, info in notes.items():
            node(f"note:{rel}", info["title"], "note", info["summary"], path=rel, folder=info["folder"])
            folder = info["folder"]
            if folder:
                node(f"folder:{folder}", folder, "hub", "Dossier de notes")
                link("hub:memory", f"folder:{folder}")
                link(f"folder:{folder}", f"note:{rel}")
            else:
                link("hub:memory", f"note:{rel}")
        by_title = {info["title"].lower(): rel for rel, info in notes.items()}
        by_stem = {Path(rel).stem.lower(): rel for rel in notes}
        for rel, info in notes.items():
            for target in info["links"]:
                key = target.lower().strip()
                dest = by_title.get(key) or by_stem.get(Path(key).stem)
                if dest:
                    link(f"note:{rel}", f"note:{dest}", "lien")

        # Projets
        for project in self._projects():
            node(f"project:{project}", project, "project", f"{config.WORKDIR / project}")
            link("hub:projects", f"project:{project}")

        return {"nodes": list(nodes.values()), "links": links, "groups": GROUPS,
                "stats": {"nodes": len(nodes), "links": len(links), "notes": len(notes)}}

    def publish(self) -> None:
        # Pas plus de 2 reconstructions par seconde : un changement de capteur
        # et un init simultanés ne doivent pas faire clignoter le HUD.
        now = time.monotonic()
        if now - self._last_publish < 0.5:
            with self._lock:
                if self._pending:
                    return
                self._pending = True
            timer = threading.Timer(0.5, self._publish_now)
            timer.daemon = True
            timer.start()
            return
        self._publish_now()

    def _publish_now(self) -> None:
        with self._lock:
            self._pending = False
        self._last_publish = time.monotonic()
        try:
            bus.publish("graph", **self.build())
        except Exception:
            log.exception("construction du graphe impossible")

    # --- lecture du disque ---

    @staticmethod
    def _disk_skills() -> list[str]:
        found: list[str] = []
        for base in (CLAUDE_HOME / "skills", config.WORKDIR / ".claude" / "skills", config.ROOT / ".claude" / "skills"):
            if base.is_dir():
                found += [p.parent.name for p in base.glob("*/SKILL.md")]
        return sorted(set(found))

    @staticmethod
    def _configured_mcp() -> list[str]:
        try:
            data = json.loads(config.MCP_CONFIG_FILE.read_text(encoding="utf-8"))
            return list((data.get("mcpServers") or {}).keys())
        except (OSError, ValueError):
            return []

    @staticmethod
    def _projects() -> list[str]:
        try:
            return sorted(
                p.name for p in config.WORKDIR.iterdir()
                if p.is_dir() and not p.name.startswith((".", "_")) and (p / ".git").exists()
            )
        except OSError:
            return []

    @staticmethod
    def _vault_signature() -> str:
        if not VAULT.is_dir():
            return ""
        parts = [f"{p}:{p.stat().st_mtime_ns}" for p in sorted(VAULT.rglob("*.md"))]
        return str(hash("|".join(parts)))

    @staticmethod
    def _vault_notes() -> dict[str, dict]:
        notes: dict[str, dict] = {}
        if not VAULT.is_dir():
            return notes
        for path in sorted(VAULT.rglob("*.md")):
            if any(part.startswith(".") for part in path.relative_to(VAULT).parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            rel = path.relative_to(VAULT).as_posix()  # 03/11 : « projets/x.md » aussi sous Windows
            title = path.stem
            m = re.search(r"^#\s+(.+)$", text, re.M)
            if m:
                title = m.group(1).strip()
            body = re.sub(r"^---.*?---\s*", "", text, flags=re.S)
            body = re.sub(r"^#.*$", "", body, flags=re.M).strip()
            links = _WIKILINK.findall(text) + [Path(x).stem for x in _MDLINK.findall(text)]
            notes[rel] = {
                "title": title,
                "summary": " ".join(body.split())[:180],
                "folder": path.parent.relative_to(VAULT).as_posix() if path.parent != VAULT else "",
                "links": links,
            }
        return notes

    @staticmethod
    def _load_usage() -> dict[str, int]:
        try:
            usage = {k: int(v) for k, v in json.loads(USAGE_FILE.read_text(encoding="utf-8")).items()}
        except (OSError, ValueError):
            return {}
        # 04/11 (T50) : les appels comptés sous l'ancien nom du serveur MCP rejoignent ceux d'« astrel »
        # (la page des capacités sous-comptait) ; fichier réécrit une fois, idempotent.
        fusion: Counter[str] = Counter()
        for key, n in usage.items():
            m = _MCP_ASTREL.match(key)
            fusion[f"mcp:astrel:{m.group(1)}" if m else key] += n
        if dict(fusion) != usage:
            try:
                tmp = USAGE_FILE.with_name(USAGE_FILE.name + ".tmp")
                tmp.write_text(json.dumps(dict(fusion)), encoding="utf-8")
                tmp.replace(USAGE_FILE)
                log.info("compteurs d'outils : entrées de l'ancien nom du serveur MCP fusionnées dans « astrel »")
            except OSError:
                pass
        return dict(fusion)


graph = Graph()
