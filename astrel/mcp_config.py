"""Écrit `runtime/mcp.json` : les serveurs MCP que Claude reçoit au démarrage.

- `astrel`     : les outils du bureau et la mémoire (`astrel.mcp_server`) ;
- `connecteurs`: agenda CalDAV, transports, traduction (`astrel.mcp_connectors`) ;
- `homeassistant` : le serveur MCP OFFICIEL de Home Assistant (intégration
  « Model Context Protocol Server », HTTP, `/api/mcp`), seulement si
  HOMEASSISTANT_URL et HOMEASSISTANT_TOKEN sont renseignés ;
- `playwright` : un navigateur que Claude peut piloter (Chrome, profil dédié) ;
- `playwright2`, `playwright3`… : navigateurs de test jetables, un par agent.
  Chacun ne lance Chrome qu'à son premier usage.

Le jeton Home Assistant et les clés posées dans l'environnement finissent dans
ce fichier : il est donc écrit en 0600 (et `runtime/` est hors Git).

Chemins ABSOLUS partout : le service systemd n'a pas le PATH du shell (nvm
installe Node dans ~/.nvm), et un serveur introuvable échoue sans bruit — la
section apparaît simplement absente dans le graphe.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from . import config
from .settings import reglages
from .tools import connector

log = logging.getLogger("astrel.mcp")

PLAYWRIGHT_CLI = config.ROOT / "mcp-servers" / "node_modules" / "@playwright" / "mcp" / "cli.js"


def _node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    nvm = sorted((Path.home() / ".nvm" / "versions" / "node").glob("*/bin/node"))
    return str(nvm[-1]) if nvm else None


def _session_vars() -> dict[str, str]:
    """Variables de la session graphique, transmises explicitement aux serveurs."""
    keys = ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "WAYLAND_DISPLAY",
            "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE")
    return {k: os.environ[k] for k in keys if os.environ.get(k)}


def _connector_env() -> dict[str, str]:
    """Identifiants des connecteurs posés dans l'environnement d'Astrel : le client
    MCP ne les transmettrait pas. Ceux de `config/secrets.json` sont relus sur place."""
    return {k: os.environ[k].strip() for k in connector.ALL_KEYS if os.environ.get(k, "").strip()}


def _home_assistant() -> dict | None:
    """Le serveur MCP officiel de Home Assistant (Streamable HTTP), avec un jeton
    d'accès longue durée. Rien n'est réécrit côté Astrel : Home Assistant expose
    lui-même ses intentions (allumer, éteindre, régler, état de la maison), limitées
    aux entités « exposées à Assist »."""
    url = config.secret("HOMEASSISTANT_URL").rstrip("/")
    token = config.secret("HOMEASSISTANT_TOKEN")
    if not (url and token):
        if url or token:
            log.warning("Home Assistant à moitié configuré : il faut HOMEASSISTANT_URL ET HOMEASSISTANT_TOKEN")
        return None
    if not url.endswith("/api/mcp"):
        url += "/api/mcp"
    return {"type": "http", "url": url, "headers": {"Authorization": f"Bearer {token}"}}


def write() -> dict:
    servers: dict[str, dict] = {
        "astrel": {
            "type": "stdio",
            "command": config.VENV_PYTHON.as_posix(),  # « / » partout (plan P6)
            "args": ["-m", "astrel.mcp_server"],
            "env": {"PYTHONPATH": str(config.ROOT), **_session_vars()},
        },
        "connecteurs": {
            "type": "stdio",
            "command": config.VENV_PYTHON.as_posix(),
            "args": ["-m", "astrel.mcp_connectors"],
            "env": {"PYTHONPATH": str(config.ROOT), **_connector_env()},
        },
    }
    home = _home_assistant()
    if home:
        servers["homeassistant"] = home
    node = _node()
    if config.BROWSER_ENABLED and node and PLAYWRIGHT_CLI.exists():
        args = [str(PLAYWRIGHT_CLI), "--browser", "chrome",
                "--user-data-dir", str(config.BROWSER_PROFILE),
                "--output-dir", str(config.RUNTIME / "browser-output")]
        # Visible ou non, nombre de navigateurs de test : page « Réglages » du HUD
        # (`astrel/settings.py`), ASTREL_BROWSER_HEADLESS / _EXTRA priment.
        if not reglages.value("browser.main_visible"):
            args.append("--headless")
        servers["playwright"] = {"type": "stdio", "command": node, "args": args, "env": _session_vars()}
        # Profil en mémoire : deux Chrome ne peuvent pas partager un même profil.
        for n in range(2, 2 + int(reglages.value("browser.extra_count"))):
            extra = [str(PLAYWRIGHT_CLI), "--browser", "chrome", "--isolated",
                     "--output-dir", str(config.RUNTIME / f"browser-output-{n}")]
            # Navigateurs de test des sous-agents : invisibles par défaut (l'utilisateur
            # travaille en entreprise, une fenêtre qui surgit le gêne, 14/10).
            if not reglages.value("browser.extra_visible"):
                extra.append("--headless")
            servers[f"playwright{n}"] = {"type": "stdio", "command": node, "args": extra, "env": _session_vars()}
    elif config.BROWSER_ENABLED:
        log.warning("navigateur indisponible : Node ou mcp-servers/ absent (cd mcp-servers && npm install)")
    data = {"mcpServers": servers}
    # Créé d'emblée en 0600 : il peut contenir des jetons.
    fd = os.open(config.MCP_CONFIG_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2))
    os.chmod(config.MCP_CONFIG_FILE, 0o600)  # s'il existait déjà avec d'autres droits
    reglages.mark_applied("mcp")
    return data
