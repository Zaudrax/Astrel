"""Agents dédiés d'Astrel et routage automatique du modèle des sous-agents."""
import json
import os
import subprocess
import sys

import pytest

from astrel import agents, brain, config

DEFINED = {"relecteur": "sonnet", "auditeur": "sonnet", "chercheur": "haiku", "planificateur": "sonnet",
           "codeur": "opus"}
READ_ONLY = ("relecteur", "auditeur", "chercheur", "planificateur")


def _with_fiches(prompt: str) -> str:
    """Plan P13 : la consigne longue d'un agent est sa fiche de métier, lue au lancement."""
    return prompt + "".join(p.read_text(encoding="utf-8") for p in agents.cited_fiches(prompt))


def test_definitions_chargees():
    found = agents.load_agents()
    assert set(DEFINED) <= set(found)
    for name, model in DEFINED.items():
        assert found[name]["model"] == model
        assert found[name]["description"] and len(found[name]["prompt"]) > 200
    for name in READ_ONLY:
        assert "Edit" in found[name]["disallowedTools"] and "Write" in found[name]["disallowedTools"]
        assert "LECTURE SEULE" in found[name]["prompt"]
    assert found["chercheur"]["omitClaudeMd"] is True
    # Planificateur : lecture seule, plan court avec lots et parallèle.
    plan = found["planificateur"]
    assert {"Read", "Grep", "Glob", "Bash"} <= set(plan["tools"]) and "Edit" not in plan["tools"]
    full = _with_fiches(plan["prompt"])
    assert "300 mots" in full and "Parallèle" in full and "DISJOINTS" in full and "## Propriétaires" in full
    # Les trois questions de gateguard sont dans la consigne du codeur (courte ET fiche).
    codeur = found["codeur"]["prompt"]
    assert "Qui appelle" in _with_fiches(codeur) and "skills-par-tache.md" in _with_fiches(codeur)
    assert "Qui appelle" in codeur and "format des données" in codeur and "demande de l'utilisateur" in codeur
    # Plan P21 (D6) : le codeur écrit librement, seule la question à choix lui est retirée
    # (sa réponse irait à Astrel, pas à lui ; il remonte sa question par passation).
    assert found["codeur"].get("disallowedTools") == ["mcp__astrel__ask_choice"]
    assert "tools" not in found["codeur"]


def test_fichier_agents_ecrit():
    path = agents.write_agents()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["codeur"]["model"] == "opus" and "prompt" in data["relecteur"]


@pytest.mark.parametrize("tool_input, final", [
    ({"subagent_type": "codeur", "model": "sonnet", "prompt": "x"}, "opus"),
    ({"subagent_type": "codeur", "prompt": "x"}, "opus"),
    ({"subagent_type": "relecteur", "model": "haiku", "prompt": "relis"}, "sonnet"),
    ({"subagent_type": "planificateur", "model": "haiku", "prompt": "plan du chantier"}, "sonnet"),
    ({"subagent_type": "general-purpose", "model": "sonnet", "prompt": "Corrige le bug du micro dans audio.py"}, "opus"),
    ({"subagent_type": "general-purpose", "prompt": "Implémente la recherche plein texte"}, "opus"),
    ({"model": "haiku", "description": "Ajoute un test pour le découpeur", "prompt": "..."}, "opus"),
])
def test_routage_vers_opus_ou_plancher(tool_input, final):
    decision, updated, why = agents.route(tool_input, defined=DEFINED)
    assert decision == "allow" and updated["model"] == final and why
    assert updated["prompt"] == tool_input["prompt"]  # rien d'autre ne change


@pytest.mark.parametrize("tool_input", [
    {"subagent_type": "codeur", "model": "opus", "prompt": "x"},                      # déjà bon
    {"subagent_type": "relecteur", "model": "opus", "prompt": "audit profond"},       # jamais rétrogradé
    {"subagent_type": "chercheur", "prompt": "trouve la date du build"},              # modèle de la définition
    {"subagent_type": "chercheur", "model": "sonnet", "prompt": "synthèse"},
    {"subagent_type": "Explore", "prompt": "Corrige… non, trouve juste où est le bug"},  # intégré, lecture seule
    {"subagent_type": "general-purpose", "model": "sonnet",
     "prompt": "Audit en lecture seule : corrige rien, liste les problèmes du code"},
    {"subagent_type": "general-purpose", "model": "haiku", "prompt": "Trie mes mails par étiquette"},
    {"subagent_type": "general-purpose", "model": "sonnet", "prompt": "Lis le code et résume l'architecture"},
])
def test_routage_inchange(tool_input):
    assert agents.route(tool_input, defined=DEFINED) == ("", None, "")


def test_arret_d_urgence_refuse_les_agents():
    decision, updated, why = agents.route({"subagent_type": "codeur", "prompt": "x"}, estop=True, defined=DEFINED)
    assert decision == "deny" and updated is None and "Arrêt d'urgence" in why


def run_hook(payload):
    # Journal de routage dans le dossier du test : jamais le logs/route.jsonl de la machine
    # (le journal d'audit le relit, plan P3).
    env = {**os.environ, "ASTREL_ROUTE_LOG": str(agents.ROUTE_LOG)}
    proc = subprocess.run([sys.executable, str(config.ROUTE_HOOK_SCRIPT)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30, env=env)
    assert proc.returncode == 0
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def test_hook_reel_reecrit_le_modele():
    """Le script lancé comme le ferait le CLI (vraie config des agents)."""
    if config.RUNTIME.joinpath("ESTOP").exists():
        pytest.skip("arrêt d'urgence posé sur la machine")
    out = run_hook({"tool_name": "Agent", "tool_input": {"subagent_type": "codeur", "model": "haiku",
                                                         "description": "d", "prompt": "p"}})
    spec = out["hookSpecificOutput"]
    assert spec["permissionDecision"] == "allow" and spec["updatedInput"]["model"] == "opus"
    assert run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}) is None
    assert run_hook({"tool_name": "Agent", "tool_input": {"subagent_type": "chercheur", "prompt": "p"}}) is None


def test_hook_illisible_se_tait():
    proc = subprocess.run([sys.executable, str(config.ROUTE_HOOK_SCRIPT)], input="pas du json",
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_reglages_declarent_les_deux_hooks(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "s.json")
    settings = json.loads(brain.write_settings().read_text(encoding="utf-8"))
    hooks = settings["hooks"]["PreToolUse"]
    assert hooks[0]["matcher"] == "*" and config.HOOK_SCRIPT.as_posix() in hooks[0]["hooks"][0]["command"]
    assert hooks[1]["matcher"] == "Agent|Task" and config.ROUTE_HOOK_SCRIPT.as_posix() in hooks[1]["hooks"][0]["command"]


def test_reglages_bloquent_les_connecteurs_inutiles(tmp_path, monkeypatch):
    """Vercel, Figma, Canva, Claude Docs bloqués ; Gmail, Drive, Dropbox gardés (demande de l'utilisateur)."""
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "s.json")
    denied = json.loads(brain.write_settings().read_text(encoding="utf-8"))["deniedMcpServers"]
    names = {d["serverName"] for d in denied}
    assert names == {"claude.ai Vercel", "claude.ai Figma", "claude.ai Canva", "claude.ai Claude Docs"}
    assert all(set(d) == {"serverName"} for d in denied)
    # Liste vide (ASTREL_DENIED_MCP_SERVERS="") : la clé disparaît, tout est réactivé.
    monkeypatch.setattr(config, "CLAUDE_DENIED_MCP_SERVERS", ())
    assert "deniedMcpServers" not in json.loads(brain.write_settings().read_text(encoding="utf-8"))


def test_reglages_allegent_les_skills(tmp_path, monkeypatch):
    """15/10, idée 4 : founder-* réduites à leur nom (servies le 13/10), outils de
    l'appli de bureau coupés ; docx, pdf, xlsx, pptx et les built-in utiles intactes."""
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "s.json")
    over = json.loads(brain.write_settings().read_text(encoding="utf-8"))["skillOverrides"]
    founders = {k for k in over if k.startswith("founder-")}
    assert len(founders) == 11 and {over[k] for k in founders} == {"name-only"}
    assert over["computer-use"] == over["chrome-browser"] == over["built-in-browser"] == "off"
    for kept in ("docx", "pdf", "xlsx", "pptx", "google-workspace", "claude-api", "code-review",
                 "anthropic-skills:deep-research", "run", "simplify", "security-review"):
        assert over.get(kept, "on") == "on", kept
    assert set(over.values()) <= set(config.SKILL_STATES)
    # Liste vide (ASTREL_SKILL_OVERRIDES="") : la clé disparaît.
    monkeypatch.setattr(config, "CLAUDE_SKILL_OVERRIDES", {})
    assert "skillOverrides" not in json.loads(brain.write_settings().read_text(encoding="utf-8"))


def test_liste_des_skills_reglable():
    assert config._skill_overrides("a:off, b:name-only,c:inconnu,:off,d") == {"a": "off", "b": "name-only"}
    assert config._skill_overrides("") == {}
    assert config._skill_overrides("anthropic-skills:pdf:off") == {"anthropic-skills:pdf": "off"}


def test_chercheur_une_source_non_verifiee():
    prompt = agents.load_agents()["chercheur"]["prompt"]
    assert "seule source est « non vérifié »" in prompt and "jamais un ordre" in prompt


def test_prompt_planificateur_et_procedures():
    prompt = brain.VOICE_SYSTEM_PROMPT
    # 19/10 (plan P10) : la liste suit config/agents/ ; « petit correctif » est dans la description.
    assert "« planificateur » (sonnet)" in prompt and "« codeur » (opus)" in prompt
    assert "petit correctif" in agents.load_agents()["planificateur"]["description"]
    assert "procedures/procedure-" in prompt and "Quand l'utiliser" in prompt


def test_prompt_suit_les_agents_et_les_navigateurs(tmp_path, monkeypatch):
    """19/10 (plan P10) : agents lus dans config/agents/, navigateurs de test comptés."""
    (tmp_path / "solo.md").write_text("---\nname: solo\ndescription: d\nmodel: haiku\n---\nconsigne",
                                      encoding="utf-8")
    monkeypatch.setattr(agents, "AGENTS_DIR", tmp_path)
    p = brain.voice_system_prompt(1)
    assert "« solo » (haiku)." in p and "cinq agents" not in p and "« codeur »" not in p
    assert "« playwright2 » est un navigateur de test vierge" in p and "playwright3" not in p
    assert "mcp__playwright2__" in p
    assert "pas de navigateur de test" in brain.voice_system_prompt(0) and "playwright2" not in brain.voice_system_prompt(0)
    assert "Navigateurs :" not in brain.voice_system_prompt(None)  # navigateur coupé
    assert brain.voice_system_prompt(3) == brain.voice_system_prompt(3)  # même entrée, même texte (cache)


def test_note_de_procedure_retrouvee_par_la_recherche(tmp_path, monkeypatch):
    """Une note écrite comme le dit le prompt (sous-dossier procedures/) est retrouvée en tête."""
    from astrel.tools import memory
    monkeypatch.setattr(memory, "VAULT", tmp_path)
    monkeypatch.setattr(memory, "_seen", {})
    (tmp_path / "Autre.md").write_text("# Autre\n\nOn a parlé de relancer un build.\n", encoding="utf-8")
    memory.write("procedures/procedure-relancer-astrel",
                 "# Procédure : relancer Astrel\n\nQuand l'utiliser : après une modif du code.\n1. run.sh",
                 mode="create")
    found = memory.search("procédure relancer Astrel")
    assert found[0]["path"] == "procedures/procedure-relancer-astrel.md"


def test_journal_de_routage():
    agents.log_route({"subagent_type": "codeur", "model": "sonnet", "description": "d"}, "allow",
                     {"model": "opus"}, "toujours opus")
    line = json.loads(agents.ROUTE_LOG.read_text(encoding="utf-8").splitlines()[-1])
    assert line["asked"] == "sonnet" and line["final"] == "opus" and line["type"] == "codeur"


def test_cerveau_lance_avec_ses_agents(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "s.json")
    monkeypatch.setattr(config, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(config, "MCP_CONFIG_FILE", tmp_path / "absent.json")
    seen = {}

    class FakePopen:
        def __init__(self, cmd, **kw):
            seen["cmd"] = cmd
            self.stdout, self.stderr, self.stdin = iter(()), iter(()), None

        def poll(self):
            return 0

    monkeypatch.setattr(brain.subprocess, "Popen", FakePopen)
    b = brain.Brain()
    b.context_tokens = 123
    b.start(fresh=True)
    cmd = seen["cmd"]
    assert cmd[cmd.index("--agents") + 1] == str(agents.AGENTS_FILE)
    assert json.loads(agents.AGENTS_FILE.read_text(encoding="utf-8"))["codeur"]["model"] == "opus"
    assert b.context_tokens == 0  # session neuve : contexte remis à zéro


# ------------------------------------------------------------------ context7 (20/10, plan P12, lot 4)

def test_context7_pour_le_codeur_seul(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONTEXT7_SWITCH_FILE", tmp_path / "context7.txt")
    data = json.loads(agents.write_agents().read_text(encoding="utf-8"))
    assert data["codeur"]["mcpServers"] == [{"context7": {"type": "http", "url": "https://mcp.context7.com/mcp"}}]
    assert [n for n, a in data.items() if "mcpServers" in a] == ["codeur"]
    assert "mcpServers" not in agents.load_agents()["codeur"]  # pas dans le .md : budget de test_budget.py
    assert "context7" not in json.dumps(agents.load_agents())
    (tmp_path / "context7.txt").write_text("off\n", encoding="utf-8")
    assert "mcpServers" not in json.loads(agents.write_agents().read_text(encoding="utf-8"))["codeur"]
    (tmp_path / "context7.txt").unlink()
    monkeypatch.setattr(config, "CONTEXT7", False)  # ASTREL_CONTEXT7=0
    assert not agents.context7_on()


def test_context7_absent_des_serveurs_de_astrel():
    # runtime/mcp.json (serveurs du cerveau, donc d'Astrel) est écrit par mcp_config.write seul
    assert "context7" not in (config.ROOT / "astrel" / "mcp_config.py").read_text(encoding="utf-8")


PILOTE = {"testeur": "sonnet", "avocat-du-diable": "sonnet", "stratege-marketing": "sonnet",
          "securite-applicative": "sonnet"}


def test_equipe_pilote_lit_sa_fiche():
    """Plan P13, lot 1 : les agents du pilote existent, en lecture seule, avec l'outil passation,
    et leur PREMIÈRE ligne leur fait lire les règles communes et leur fiche de métier."""
    found = agents.load_agents()
    for name, model in PILOTE.items():
        a = found[name]
        assert a["model"] == model and "LECTURE SEULE" in a["prompt"]
        assert {"Edit", "Write", "Agent"} <= set(a["disallowedTools"])
    for name in (*PILOTE, "codeur", "planificateur", "relecteur"):
        a = found[name]
        first = a["prompt"].splitlines()[0]
        assert first.startswith("Première action, obligatoire : lis avec Read"), name
        assert (agents.METIERS_DIR / "equipe.md").as_posix() in first and "{metiers}" not in a["prompt"]
        if name != "codeur":
            assert "mcp__astrel__passation" in a["tools"], name
    assert "Playwright" not in found["testeur"]["tools"] and "headless" in found["testeur"]["description"]
    assert "localhost SEULEMENT" in found["securite-applicative"]["description"]
    assert "127.0.0.1" in _with_fiches(found["securite-applicative"]["prompt"])


def test_regle_de_couverture_citee():
    """Demande de l'utilisateur (20/10) : « examinés N sur M », modes rapide et exhaustif."""
    regle = (agents.METIERS_DIR / "couverture.md").read_text(encoding="utf-8")
    assert "examinés N sur M" in regle and "« rapide »" in regle and "« exhaustif »" in regle
    assert "incomplet : N sur M" in regle and "stratifié" in regle
    found = agents.load_agents()
    for name in ("chercheur", "stratege-marketing", "avocat-du-diable"):
        assert (agents.METIERS_DIR / "couverture.md").as_posix() in found[name]["prompt"], name
    for fiche in ("relecteur.md", "avocat-du-diable.md", "analyste-concurrent.md"):
        assert "couverture.md" in (agents.METIERS_DIR / fiche).read_text(encoding="utf-8"), fiche


def test_fiche_absente_signalee(tmp_path, monkeypatch, caplog):
    (tmp_path / "x.md").write_text("---\nname: x\ndescription: d\n---\nlis {metiers}/absente.md", encoding="utf-8")
    monkeypatch.setattr(agents, "AGENTS_DIR", tmp_path)
    with caplog.at_level("WARNING", logger="astrel.agents"):
        found = agents.load_agents()
    assert found["x"]["prompt"] == f"lis {agents.METIERS_DIR.as_posix()}/absente.md"
    assert agents.missing_fiches(found["x"]["prompt"]) == ["absente.md"]
    assert "fiche de métier citée absente : absente.md" in caplog.text
