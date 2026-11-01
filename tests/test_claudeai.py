"""Connecteurs claude.ai découverts par `claude mcp list` (19/10, plan P10) : lecture de la
sortie réelle, cache jamais bloquant, dernier résultat gardé en cas d'échec, réglages."""
import json
import subprocess
import time

from astrel import claudeai, config, settings
from astrel.settings import reglages

# Sortie de `claude mcp list` (CLI 2.1.285, 19/10), avec des connecteurs d'exemple.
SORTIE = """Checking MCP server health…

claude.ai Claude Docs: https://api.anthropic.com/v1/pages/mcp - ✔ Connected
claude.ai harvest: https://mcp.harvest.ai/mcp - ! Needs authentication
claude.ai Mailroom: https://mcp.mailroom.com/mcp - ! Needs authentication
claude.ai Monday.com: https://mcp.monday.com/mcp - ✔ Connected
claude.ai Dropbox: https://mcp.dropbox.com/claude/mcp - ✔ Connected
claude.ai Vercel: https://mcp.vercel.com - ✔ Connected
claude.ai Figma: https://mcp.figma.com/mcp - ✔ Connected
claude.ai Canva: https://mcp.canva.com/mcp - ✔ Connected
claude.ai Google Drive: https://drivemcp.googleapis.com/mcp/v1 - ✔ Connected
claude.ai Gmail: https://gmailmcp.googleapis.com/mcp/v1 - ✔ Connected
claude.ai Google Calendar: https://calendarmcp.googleapis.com/mcp/v1 - ✔ Connected
"""


def test_sortie_reelle_lue_et_triee():
    names = claudeai.parse(SORTIE)
    assert len(names) == 11 and "claude.ai Google Drive" in names and "claude.ai Monday.com" in names
    assert names == sorted(names, key=str.casefold)
    from conftest import CONNECTEURS_DE_TEST

    assert tuple(names) == CONNECTEURS_DE_TEST  # la liste figée des tests suit la vraie sortie


def test_lignes_inconnues_ignorees():
    text = ("playwright: npx @playwright/mcp - ✔ Connected\nNo MCP servers configured\n"
            "claude.ai Notion: https://mcp.notion.com/mcp - ✗ Failed to connect\nclaude.ai : rien\n")
    assert claudeai.parse(text) == ["claude.ai Notion"]
    assert claudeai.parse("") == []


_DISCOVER = claudeai.discover  # la vraie, gardée à l'import (conftest la remplace pendant chaque test)


def test_discover_lance_le_cli_hors_du_projet(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return subprocess.CompletedProcess(cmd, 0, SORTIE, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert len(_DISCOVER()) == 11
    assert seen["cmd"] == [config.CLAUDE_BIN, "mcp", "list"] and seen["timeout"] == claudeai.TIMEOUT_S
    assert seen["stdin"] == subprocess.DEVNULL and "astrel-mcp-list-" in seen["cwd"]


def test_discover_echec_rend_none(monkeypatch):
    def absent(cmd, **kw):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(subprocess, "run", absent)
    assert _DISCOVER() is None
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd, 20)))
    assert _DISCOVER() is None
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "erreur"))
    assert _DISCOVER() is None  # rien trouvé : jamais une liste vide qui effacerait la dernière


def test_cache_jamais_bloquant_et_fichier_du_dernier_succes(monkeypatch):
    calls = []

    def slow():
        calls.append(time.monotonic())
        return ["claude.ai Notion", "claude.ai Gmail"]

    monkeypatch.setattr(claudeai, "discover", slow)
    changed = []
    c = claudeai.Connecteurs(on_change=lambda: changed.append(1))
    assert c.names() == []  # rien vu encore : rend aussitôt, découverte lancée en fond
    c.refresh(wait=True)
    assert c.names() == ["claude.ai Notion", "claude.ai Gmail"] and changed == [1]
    data = json.loads(claudeai.SEEN_FILE.read_text(encoding="utf-8"))
    assert data["names"] == ["claude.ai Notion", "claude.ai Gmail"]
    n = len(calls)
    c.names()
    assert len(calls) == n  # frais : pas de nouvelle découverte avant TTL_S
    # Relance d'Astrel : le dernier succès est relu du disque, sans attendre le CLI.
    monkeypatch.setattr(claudeai, "discover", lambda: None)
    again = claudeai.Connecteurs()
    assert again.names() == ["claude.ai Notion", "claude.ai Gmail"]


def test_echec_garde_le_dernier_resultat(monkeypatch):
    claudeai.SEEN_FILE.write_text(json.dumps({"names": ["claude.ai Gmail", "claude.ai Vercel", "pas un connecteur"]}),
                                  encoding="utf-8")
    monkeypatch.setattr(claudeai, "discover", lambda: None)
    changed = []
    c = claudeai.Connecteurs(on_change=lambda: changed.append(1))
    c.refresh(wait=True)
    assert c.names() == ["claude.ai Gmail", "claude.ai Vercel"] and changed == []
    assert c._at > 0  # l'échec compte : pas de nouvel essai à chaque publication de la page


def test_reglages_suivent_la_decouverte(monkeypatch):
    c = claudeai.Connecteurs()
    c._names, c._at = ["claude.ai Gmail", "claude.ai Notion"], float("inf")
    monkeypatch.setattr(claudeai, "connecteurs", c)
    monkeypatch.setattr(config, "CLAUDE_DENIED_MCP_SERVERS", ("claude.ai Vercel",))
    conn = reglages.value("connectors")
    # Nouveau venu actif (comme le CLI), bloqué par défaut gardé même s'il n'est plus découvert.
    assert conn == {"claude.ai Gmail": True, "claude.ai Notion": True, "claude.ai Vercel": False}
    assert not hasattr(settings, "KNOWN_CONNECTORS")


def test_premier_demarrage_garde_les_choix_de_utilisateur(monkeypatch):
    """Aucune découverte n'a encore abouti : un connecteur bloqué à la main reste bloqué."""
    config.ASTREL_SETTINGS_FILE.write_text(json.dumps({"version": 1, "values": {
        "connectors": {"claude.ai Dropbox": False}}}), encoding="utf-8")
    c = claudeai.Connecteurs()
    c._names, c._at = [], float("inf")
    monkeypatch.setattr(claudeai, "connecteurs", c)
    reglages.reset()
    assert reglages.value("connectors")["claude.ai Dropbox"] is False


def test_connecteur_decouvert_en_fond_pas_en_attente(monkeypatch):
    """Un connecteur apparu après le démarrage du cerveau n'affiche pas « redémarrage nécessaire »."""
    c = claudeai.Connecteurs()
    c._names, c._at = ["claude.ai Gmail"], float("inf")
    monkeypatch.setattr(claudeai, "connecteurs", c)
    reglages.mark_applied("brain")
    c._names = ["claude.ai Gmail", "claude.ai Notion"]
    assert not reglages.pending("connectors")
    reglages.set("connectors", {"claude.ai Notion": False})
    assert reglages.pending("connectors")  # le bloquer, lui, attend bien le redémarrage
