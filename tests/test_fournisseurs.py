"""Plan P11 (20/10) : fournisseur du cerveau. Clé API Claude par `apiKeyHelper`, fournisseurs à
clé (OpenAI, Gemini, OpenRouter) derrière la passerelle locale, conversation seule par défaut.

La règle de l'utilisateur éprouvée ici : une clé n'apparaît JAMAIS dans la ligne de commande, les
réglages, l'environnement des enfants, le journal (`logs/`), l'historique du bus, ni le prompt.
Aucune vraie clé : des valeurs factices reconnaissables (`CLE`)."""
import chemins_machine  # chemins de la machine qui lance la suite
import importlib.util
import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from astrel import brain, config, core, fournisseurs, guard, passerelle
from astrel.brain import Brain
from astrel.fournisseurs import Effectif, Secret
from astrel.settings import reglages
from faux_openai import FauxOpenAI
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)
from conftest import mode_prive, prompt_de

ROOT = Path(__file__).resolve().parent.parent
CLE = "sk-ant-api03-CLE-FACTICE-DU-TEST-0123456789abcdef"
CLE_OPENAI = "sk-proj-CLE-FACTICE-OPENAI-0123456789abcdef"
REAL_VENV = ROOT / ".venv-passerelle"


def _secrets(**values) -> None:
    config.SECRETS_FILE.write_text(json.dumps(values))


class FakePopen:
    seen: dict = {}

    def __init__(self, cmd, **kw):
        FakePopen.seen = {"cmd": cmd, "env": kw.get("env") or {}}
        self.stdout, self.stderr, self.stdin, self.pid = iter(()), iter(()), None, 4242

    def poll(self):
        return None


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "claude-settings.json")
    monkeypatch.setattr(config, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(config, "MCP_CONFIG_FILE", tmp_path / "mcp-absent.json")
    monkeypatch.setattr(brain.subprocess, "Popen", FakePopen)
    FakePopen.seen = {}
    return FakePopen


def _everything(seen: dict) -> str:
    """Tout ce que le process du cerveau reçoit : ligne de commande, environnement, réglages."""
    return json.dumps(seen["cmd"]) + json.dumps(seen["env"]) + config.SETTINGS_FILE.read_text()


# ------------------------------------------------------------------ choix et clés

def test_choix_et_lecture():
    values = [v for v, _ in fournisseurs.choix()]
    assert values[:2] == ["abonnement", "cle_claude"] and "openai:gpt-5" in values and "gemini:gemini-2.5-pro" in values
    assert fournisseurs.lire("openai") == ("openai", "gpt-5")
    assert fournisseurs.lire("openrouter:qwen/qwen3-coder") == ("openrouter", "qwen/qwen3-coder")
    assert fournisseurs.lire("inconnu:x") == ("abonnement", "") and fournisseurs.lire("") == ("abonnement", "")
    assert reglages.value("fournisseur") == "abonnement"


CLE_XAI = "xai-CLE-FACTICE-GROK-0123456789abcdefABCDEF"


def test_grok_au_catalogue_par_la_passerelle():
    """P15 lot A : Grok (xAI) par clé API, comme OpenAI (passerelle, conversation seule)."""
    choix = dict(fournisseurs.choix())
    assert {"xai:grok-4.3", "xai:grok-4.20", "xai:grok-code-fast-1"} <= set(choix)
    assert choix["xai:grok-4.3"] == "Grok · Grok 4.3 (clé API, sans Claude)"
    assert fournisseurs.lire("xai:grok-4.3") == ("xai", "grok-4.3")
    assert fournisseurs.lire("xai") == ("xai", "grok-4.3")  # le premier : conseillé
    assert fournisseurs.libelle("xai", "grok-4.3") == "Grok · Grok 4.3"
    f = fournisseurs.CATALOGUE["xai"]
    assert f.cle == "CLE_API_XAI" and f.passerelle and f.prefixe == "xai-"
    err = fournisseurs.verifier_cle("xai", CLE_OPENAI)
    assert "xai-" in err and CLE_OPENAI not in err
    assert fournisseurs.verifier_cle("xai", CLE_XAI) == ""
    assert fournisseurs.enregistrer_cle("xai", CLE_XAI) == {"ok": True}
    assert json.loads(config.SECRETS_FILE.read_text()) == {"CLE_API_XAI": CLE_XAI}
    assert mode_prive(config.SECRETS_FILE, 0o600) and fournisseurs.a_une_cle("xai")


def test_grok_modele_plus_recent_sans_code(monkeypatch):
    monkeypatch.setenv("ASTREL_FOURNISSEUR_MODELES", '{"xai": ["grok-4.7"]}')
    assert "xai:grok-4.7" in dict(fournisseurs.choix())


def test_grok_prefixe_litellm_de_la_passerelle():
    spec = importlib.util.spec_from_file_location("passerelle_serveur_p15", ROOT / "astrel" / "passerelle_serveur.py")
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)
    p = srv.Passerelle("xai", "grok-4.3", CLE_XAI, "jeton-local-0123456789")
    assert p.modele == "xai/grok-4.3"
    assert "use_xai_oauth" not in p.appel({"model": "claude-sonnet-4-5", "messages": []})  # jamais l'abonnement
    assert CLE_XAI not in p.masque(f"401 : clé {CLE_XAI} refusée")
    # Tous les fournisseurs de passerelle du catalogue sont connus du serveur.
    assert {k for k, f in fournisseurs.CATALOGUE.items() if f.passerelle} <= set(srv.PREFIXES)


def test_grok_variables_xai_retirees_des_enfants(monkeypatch):
    base = {"PATH": "/usr/bin", "XAI_API_KEY": CLE_XAI}
    assert fournisseurs.env_cerveau(base, "abonnement")["XAI_API_KEY"] == CLE_XAI  # abonnement : inchangé
    env = fournisseurs.env_cerveau(base, "passerelle", url="http://127.0.0.1:5", modele="grok-4.3")
    assert "XAI_API_KEY" not in env and CLE_XAI not in json.dumps(env)
    # La passerelle et pip : ni la clé, ni une adresse xAI du shell (elle y enverrait la clé).
    for k, v in {"XAI_API_KEY": CLE_XAI, "XAI_API_BASE": "https://ailleurs", "XAI_OAUTH_API_BASE": "https://x"}.items():
        monkeypatch.setenv(k, v)
    assert not [k for k in passerelle._clean_env() if k.startswith("XAI_")]


def test_cle_enregistree_0600_atomique_et_autres_secrets_gardes():
    config.SECRETS_FILE.write_text(json.dumps({"DEEPL_API_KEY": "deepl"}))
    os.chmod(config.SECRETS_FILE, 0o644)
    assert fournisseurs.enregistrer_cle("cle_claude", CLE) == {"ok": True}
    data = json.loads(config.SECRETS_FILE.read_text())
    assert data == {"DEEPL_API_KEY": "deepl", "CLE_API_ANTHROPIC": CLE}
    assert mode_prive(config.SECRETS_FILE, 0o600)
    assert config.secret("CLE_API_ANTHROPIC") == CLE and fournisseurs.a_une_cle("cle_claude")
    assert not list(config.SECRETS_FILE.parent.glob(".*.tmp"))
    assert fournisseurs.effacer_cle("cle_claude")["ok"]
    assert json.loads(config.SECRETS_FILE.read_text()) == {"DEEPL_API_KEY": "deepl"}


@pytest.mark.parametrize("bad", ["", "court", "sk-ant-api03 avec espace 0123456789", "pas-le-bon-debut-0123456789abcdef",
                                 "sk-ant-é-accent-0123456789abcdef"])
def test_saisie_refusee_sans_citer_la_cle(bad):
    res = fournisseurs.enregistrer_cle("cle_claude", bad)
    assert res["ok"] is False and (not bad or bad not in res["error"])
    assert not config.SECRETS_FILE.exists()


def test_secrets_illisible_jamais_ecrase():
    config.SECRETS_FILE.write_text("{pas du json")
    assert fournisseurs.enregistrer_cle("cle_claude", CLE)["ok"] is False
    assert config.SECRETS_FILE.read_text() == "{pas du json"


def test_secret_masque_partout():
    cmd = {"cmd": "settings_action", "key": "fournisseur.cles", "action": "cle_claude", "value": CLE}
    fournisseurs.masquer_commande(cmd)
    assert isinstance(cmd["value"], Secret) and cmd["value"].reveler() == CLE
    for text in (repr(cmd), str(cmd), f"{cmd}", "%r" % (cmd,), json.dumps(cmd, default=str)):
        assert CLE not in text
    other = {"cmd": "settings_action", "key": "whatsapp", "action": "link_code", "value": "0612345678"}
    assert fournisseurs.masquer_commande(other)["value"] == "0612345678"  # rien d'autre n'est touché


def test_assistant_de_cle_imprime_la_cle_seule():
    _secrets(CLE_API_ANTHROPIC=CLE)
    helper = fournisseurs.helper("cle_claude")
    assert CLE not in helper and str(config.SECRETS_FILE) in helper and "-I" in helper.split()
    out = subprocess.run(helper, shell=True, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and out.stdout == CLE
    _secrets(AUTRE="x")
    # 04/11 (CI Windows) : lu en UTF-8 strict, comme le CLI ; un message accentué en cp1252 rendait stderr illisible
    # (None : le fil de lecture de subprocess mourait sur l'erreur de décodage).
    out = subprocess.run(helper, shell=True, capture_output=True, encoding="utf-8", errors="strict", timeout=30)
    assert out.returncode == 1 and out.stdout == "" and CLE not in out.stderr
    assert "aucune clé enregistrée" in out.stderr
    config.PASSERELLE_JETON_FILE.write_text("jeton-local-de-test")
    out = subprocess.run(fournisseurs.helper("passerelle"), shell=True, capture_output=True, text=True, timeout=30)
    assert out.stdout == "jeton-local-de-test"
    assert fournisseurs.helper("abonnement") == ""


def test_assistant_de_cle_sous_windows_entre_guillemets_doubles(monkeypatch):
    """03/11 (CI Windows) : cmd.exe ne lit pas les « '…' » de shlex. Simulé sous Linux : chemins Windows."""
    from pathlib import PureWindowsPath
    from types import SimpleNamespace

    monkeypatch.setattr(fournisseurs, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(config, "VENV_PYTHON", PureWindowsPath(r"D:\a\Astrel\.venv\Scripts\python.exe"))
    monkeypatch.setattr(config, "CLE_API_SCRIPT", PureWindowsPath(r"D:\a\Astrel\astrel\cle_api.py"))
    monkeypatch.setattr(config, "SECRETS_FILE", PureWindowsPath(r"C:\Users\Jean Dupont\secrets.json"))
    # 04/11 (CI Windows) : `-X utf8`, sinon le message d'erreur de cle_api.py part en cp1252 (-I ignore PYTHONUTF8).
    assert fournisseurs.helper("cle_claude") == (r'"D:\a\Astrel\.venv\Scripts\python.exe" -I -X utf8 '
                                                 r'"D:\a\Astrel\astrel\cle_api.py" anthropic '
                                                 r'"C:\Users\Jean Dupont\secrets.json"')


def test_environnement_du_cerveau_sans_cle():
    base = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-shell", "ANTHROPIC_AUTH_TOKEN": "t",
            "ANTHROPIC_BASE_URL": "https://ailleurs", "CLE_API_OPENAI": CLE_OPENAI, "OPENAI_API_KEY": CLE_OPENAI,
            "CLAUDE_CODE_USE_BEDROCK": "1", "ASTREL_BRAIN": "x"}
    env = fournisseurs.env_cerveau(base, "abonnement", windows=False)  # windows=False : test du filtrage, pas du PYTHONUTF8 Windows
    assert env == {"PATH": "/usr/bin", "ASTREL_BRAIN": "x", "OPENAI_API_KEY": CLE_OPENAI}  # clé d'un autre service : gardée
    env = fournisseurs.env_cerveau(base, "passerelle", url="http://127.0.0.1:5", modele="gpt-5")
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:5" and env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "gpt-5"
    assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "gpt-5"
    assert CLE_OPENAI not in json.dumps(env) and "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_AUTH_TOKEN" not in env


# ------------------------------------------------------------------ démarrage du cerveau

def test_abonnement_inchange_mais_variables_du_shell_retirees(fake_cli, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-du-shell")
    Brain().start(fresh=True)
    seen = fake_cli.seen
    assert "ANTHROPIC_API_KEY" not in seen["env"] and "sk-ant-du-shell" not in _everything(seen)
    settings = json.loads(config.SETTINGS_FILE.read_text())
    assert "apiKeyHelper" not in settings
    assert any(h["matcher"] == "Agent|Task" for h in settings["hooks"]["PreToolUse"])
    assert "--dangerously-skip-permissions" in seen["cmd"] and "--append-system-prompt-file" in seen["cmd"]


def test_cle_claude_par_assistant_jamais_en_clair(fake_cli):
    _secrets(CLE_API_ANTHROPIC=CLE)
    reglages.set("fournisseur", "cle_claude")
    b = Brain()
    b.start(fresh=True)
    seen = fake_cli.seen
    assert CLE not in _everything(seen)  # ligne de commande, environnement, réglages, prompt
    settings = json.loads(config.SETTINGS_FILE.read_text())
    assert settings["apiKeyHelper"] == fournisseurs.helper("cle_claude")
    assert any(h["matcher"] == "*" for h in settings["hooks"]["PreToolUse"])  # garde-fou en place
    assert b.fournisseur.genre == "cle_claude" and fournisseurs.etat.actif.genre == "cle_claude"
    prompt = prompt_de(seen["cmd"])
    assert CLE not in prompt and chemins_machine.longueur_type(prompt) <= 10_800


def test_cle_absente_repli_sur_l_abonnement(fake_cli):
    reglages.set("fournisseur", "cle_claude")  # choisie, puis clé effacée
    b = Brain()
    b.start(fresh=True)
    assert b.fournisseur.genre == "abonnement" and "aucune clé" in b.fournisseur.note
    assert "apiKeyHelper" not in json.loads(config.SETTINGS_FILE.read_text())


def _choisir_openai(monkeypatch, url="http://127.0.0.1:9"):
    _secrets(CLE_API_OPENAI=CLE_OPENAI)
    monkeypatch.setattr(passerelle, "installed", lambda: True)
    started = []
    monkeypatch.setattr(passerelle.passerelle, "start",
                        lambda f, m, cle, base="": started.append((f, m, cle)) or url)
    reglages.set("fournisseur", "openai:gpt-5")
    return started


def test_passerelle_conversation_seule_par_construction(fake_cli, monkeypatch):
    from test_mode_leger import assert_sure

    started = _choisir_openai(monkeypatch)
    b = Brain()
    b.start(fresh=True)
    seen = fake_cli.seen
    assert started == [("openai", "gpt-5", "CLE_API_OPENAI")]  # le NOM de la clé, jamais sa valeur
    cmd = seen["cmd"]
    # Même contrôle que le mode léger, sauf `--settings` (l'assistant de clé, seul).
    i = cmd.index("--settings")
    assert_sure(cmd[:i] + cmd[i + 2:])
    assert json.loads(config.SETTINGS_FILE.read_text()) == {"apiKeyHelper": fournisseurs.helper("passerelle")}
    assert cmd[cmd.index("--system-prompt") + 1] == brain.PASSERELLE_SYSTEM_PROMPT
    env = seen["env"]
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9" and env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "gpt-5"
    assert CLE_OPENAI not in _everything(seen)
    assert b.fournisseur.genre == "passerelle" and not b.fournisseur.outils


def test_grok_cerveau_conversation_seule_sans_la_cle(fake_cli, monkeypatch):
    from test_mode_leger import assert_sure

    _secrets(CLE_API_XAI=CLE_XAI)
    monkeypatch.setenv("XAI_API_KEY", CLE_XAI)  # une clé du shell : jamais transmise au cerveau
    monkeypatch.setattr(passerelle, "installed", lambda: True)
    started = []
    monkeypatch.setattr(passerelle.passerelle, "start",
                        lambda f, m, cle, base="": started.append((f, m, cle)) or "http://127.0.0.1:9")
    reglages.set("fournisseur", "xai:grok-4.3")
    b = Brain()
    b.start(fresh=True)
    seen = fake_cli.seen
    assert started == [("xai", "grok-4.3", "CLE_API_XAI")]  # le NOM de la clé, jamais sa valeur
    cmd = seen["cmd"]
    i = cmd.index("--settings")
    assert_sure(cmd[:i] + cmd[i + 2:])
    assert seen["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "grok-4.3" and "XAI_API_KEY" not in seen["env"]
    assert CLE_XAI not in _everything(seen)
    assert b.fournisseur.genre == "passerelle" and b.fournisseur.id == "xai" and not b.fournisseur.outils
    assert b.fournisseur.session_key == "passerelle:xai:grok-4.3:sans-outil"


def test_passerelle_avec_outils_seulement_en_prudent(fake_cli, monkeypatch):
    _choisir_openai(monkeypatch)
    monkeypatch.setattr(config, "PASSERELLE_OUTILS", True)
    monkeypatch.setattr(fournisseurs, "mode_prudent", lambda: True)
    b = Brain()
    b.start(fresh=True)
    settings = json.loads(config.SETTINGS_FILE.read_text())
    assert b.fournisseur.outils and "--dangerously-skip-permissions" in fake_cli.seen["cmd"]
    # garde-fou oui, routage non ; P20 : le refus du navigateur de test en blocage mémoire reste
    assert [h["matcher"] for h in settings["hooks"]["PreToolUse"]] == ["*", "mcp__playwright.*__browser_navigate",
                                                                       config.SUIVI_MATCHER]  # 01/11 : instantanés
    assert settings["apiKeyHelper"] == fournisseurs.helper("passerelle")
    monkeypatch.setattr(fournisseurs, "mode_prudent", lambda: False)  # autonomy.json changé à la main
    b.start(fresh=True)
    assert not b.fournisseur.outils and "--bare" in fake_cli.seen["cmd"] and "prudent" in b.fournisseur.note


def test_passerelle_impossible_repli(fake_cli, monkeypatch):
    _choisir_openai(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("la passerelle n'a pas démarré")

    monkeypatch.setattr(passerelle.passerelle, "start", boom)
    b = Brain()
    b.start(fresh=True)
    assert b.fournisseur.genre == "abonnement" and "pas démarré" in b.fournisseur.note
    assert "ANTHROPIC_BASE_URL" not in fake_cli.seen["env"]


def test_session_reprise_seulement_chez_le_meme_fournisseur(fake_cli, monkeypatch):
    sid = "11111111-1111-4111-8111-111111111111"
    config.SESSION_FILE.write_text(json.dumps({"id": sid, "at": time.time()}))  # d'avant le 20/10 : Claude
    _secrets(CLE_API_ANTHROPIC=CLE)
    reglages.set("fournisseur", "cle_claude")
    Brain().start()
    assert fake_cli.seen["cmd"][fake_cli.seen["cmd"].index("--resume") + 1] == sid  # abonnement → clé : même fil
    _choisir_openai(monkeypatch)
    Brain().start()
    assert "--resume" not in fake_cli.seen["cmd"]  # autre fournisseur : conversation neuve
    b = Brain()
    b._proc, b.session_id = FakePopen(["x"]), sid
    b.fournisseur = fournisseurs.effectif()
    b._remember_session()
    assert json.loads(config.SESSION_FILE.read_text())["fournisseur"] == "passerelle:openai:gpt-5:sans-outil"


def test_mode_leger_arrete_la_passerelle(fake_cli, monkeypatch):
    stopped = []
    monkeypatch.setattr(passerelle.passerelle, "stop", lambda: stopped.append(1))
    b = Brain()
    b.local = "qwen"
    b.start()
    assert stopped


# ------------------------------------------------------------------ dépense (alerte seule)

def test_depense_cumulee_par_process_et_alerte_une_fois():
    d = fournisseurs.Depense()
    assert d.noter(1.0, 10, 5.0) is None and d.noter(3.0, 10, 5.0) is None
    assert d.jour() == pytest.approx(3.0)
    assert d.noter(2.5, 11, 5.0) == pytest.approx(5.5)  # nouveau process : il repart de zéro
    assert d.noter(4.0, 11, 5.0) is None  # une seule alerte par jour
    assert d.jour() == pytest.approx(7.0)
    assert d.noter("x", 11, 5.0) is None and d.noter(None, 11, 5.0) is None


# ------------------------------------------------------------------ le cœur

def _hud(**kw):
    return {"cmd": "settings_action", "key": "fournisseur.cles", "_via": "hud", "_from_claude": False, **kw}


def test_cle_collee_dans_le_hud_jamais_journalisee(astrel, published, caplog):
    j, _, _ = astrel
    caplog.set_level(logging.DEBUG)
    cmd = _hud(action="enregistrer_openai", value=CLE_OPENAI)
    res = j.on_command(cmd)
    assert res["ok"] is True and "enregistrée" in res["text"]
    assert json.loads(config.SECRETS_FILE.read_text())["CLE_API_OPENAI"] == CLE_OPENAI
    # Le serveur du HUD journalise `cmd` s'il échoue : le même objet, masqué.
    logging.getLogger("astrel.server").exception("commande %r en échec", cmd)
    for where in (caplog.text, json.dumps(published, default=str), json.dumps(res, default=str),
                  json.dumps(reglages.describe(), default=str), repr(cmd)):
        assert CLE_OPENAI not in where
    assert not config.ASTREL_SETTINGS_FILE.exists() or CLE_OPENAI not in config.ASTREL_SETTINGS_FILE.read_text()
    lines = {line["label"]: line["text"] for line in reglages.view("fournisseur.cles")["value"]["lines"]}
    assert lines["Clé OpenAI"] == "enregistrée sur cette machine"


def test_cle_grok_collee_dans_le_hud(astrel, published, caplog):
    j, _, _ = astrel
    caplog.set_level(logging.DEBUG)
    actions = {a["id"] for a in reglages.view("fournisseur.cles")["actions"]}
    assert "enregistrer_xai" in actions
    cmd = _hud(action="enregistrer_xai", value=CLE_XAI)
    res = j.on_command(cmd)
    assert res["ok"] is True and res["text"].startswith("Clé Grok enregistrée")
    logging.getLogger("astrel.server").exception("commande %r en échec", cmd)
    for where in (caplog.text, json.dumps(published, default=str), json.dumps(res, default=str),
                  json.dumps(reglages.describe(), default=str), repr(cmd)):
        assert CLE_XAI not in where
    view = reglages.view("fournisseur.cles")["value"]
    lines = {line["label"]: line["text"] for line in view["lines"]}
    assert lines["Clé Grok"] == "enregistrée sur cette machine" and CLE_XAI not in json.dumps(view)
    assert _set(j, "xai:grok-4.3")["ok"] is False  # passerelle non installée
    res = j.on_command(_hud(action="effacer_xai"))
    assert res["ok"] is True and not fournisseurs.a_une_cle("xai")


def test_cle_refusee_en_ligne_de_commande_et_a_claude(astrel, caplog):
    j, _, _ = astrel
    caplog.set_level(logging.DEBUG)
    res = j.on_command(_hud(action="enregistrer_openai", value=CLE_OPENAI, _via="socket"))
    assert res["ok"] is False and "HUD" in res["error"]
    res = j.on_command(_hud(action="enregistrer_openai", value=CLE_OPENAI, _from_claude=True))
    assert res["ok"] is False
    assert not config.SECRETS_FILE.exists() and CLE_OPENAI not in caplog.text


def test_echec_pendant_l_enregistrement_masque(astrel, monkeypatch, caplog):
    j, _, _ = astrel
    caplog.set_level(logging.DEBUG)

    def boom(fid, cle):
        raise RuntimeError("disque plein")

    monkeypatch.setattr(fournisseurs, "enregistrer_cle", boom)
    res = j.on_command(_hud(action="enregistrer_cle_claude", value=CLE))
    assert res["ok"] is False and "disque plein" in res["error"]  # le chemin d'exception est bien pris
    assert CLE not in caplog.text and CLE not in json.dumps(res, default=str)


def _set(j, value):
    return j.on_command({"cmd": "settings_set", "key": "fournisseur", "value": value, "_via": "hud",
                         "_from_claude": False})


def test_choix_refuse_tant_qu_il_manque_quelque_chose(astrel, monkeypatch):
    j, _, _ = astrel
    res = _set(j, "openai:gpt-5")
    assert res["ok"] is False and "clé" in res["error"]
    _secrets(CLE_API_OPENAI=CLE_OPENAI)
    assert "passerelle" in _set(j, "openai:gpt-5")["error"]
    monkeypatch.setattr(passerelle, "installed", lambda: True)
    monkeypatch.setattr(j, "_total", True)
    assert "Sans confirmation" in _set(j, "openai:gpt-5")["error"]
    monkeypatch.setattr(j, "_total", False)
    monkeypatch.setattr(j, "_lift", True)
    assert "garde-fou" in _set(j, "openai:gpt-5")["error"]
    monkeypatch.setattr(j, "_lift", False)
    monkeypatch.setattr(config, "PASSERELLE_OUTILS", True)
    monkeypatch.setattr(fournisseurs, "mode_prudent", lambda: False)
    assert "PRUDENT" in _set(j, "openai:gpt-5")["error"]
    monkeypatch.setattr(config, "PASSERELLE_OUTILS", False)  # conversation seule : prudent inutile
    assert _set(j, "openai:gpt-5")["ok"] is True and reglages.value("fournisseur") == "openai:gpt-5"
    assert _set(j, "cle_claude")["ok"] is False  # pas de clé Claude
    assert _set(j, "abonnement")["ok"] is True


def test_total_levee_et_autonome_refuses_hors_claude(astrel, monkeypatch, tmp_path):
    j, _, _ = astrel
    monkeypatch.setattr(guard, "MODE_FILE", tmp_path / "autonomy.json")  # jamais le vrai fichier
    reglages.set("fournisseur", "openai:gpt-5")
    assert j.set_total(True, "HUD")["ok"] is False and not j._total
    assert j.set_lift(True, "HUD")["ok"] is False and not j._lift
    monkeypatch.setattr(config, "PASSERELLE_OUTILS", True)
    res = j.on_command({"cmd": "autonomy", "mode": "autonome", "_via": "hud", "_from_claude": False})
    assert res["ok"] is False and not (tmp_path / "autonomy.json").exists()
    reglages.set("fournisseur", "abonnement")
    j.brain.fournisseur = Effectif("passerelle", "openai", "gpt-5", outils=False)  # encore en marche
    assert j.set_total(True, "HUD")["ok"] is False
    j.brain.fournisseur = Effectif("cle_claude", "cle_claude")
    assert j.set_total(True, "HUD")["ok"] is True  # clé Claude : c'est Claude
    j.set_total(False, "HUD")


def test_badge_et_notice_hors_claude(astrel, published):
    j, _, _ = astrel
    j.brain.fournisseur = Effectif("passerelle", "openai", "gpt-5", outils=False)
    j._publish_model()
    j._publish_model("gpt-5")
    model = of(published, "model")[-1]
    assert model["ext"] is True and model["label"] == "OpenAI · GPT-5 · sans outil"
    notices = [n for n in of(published, "notice") if "OpenAI" in n["text"]]
    assert len(notices) == 1 and notices[0]["level"] == "warning" and "conversation seule" in notices[0]["text"]
    assert j._set_model("opus")["ok"] is False  # le modèle se choisit dans Réglages › Fournisseur
    j.brain.fournisseur = Effectif("cle_claude", "cle_claude")
    j._publish_model()
    assert of(published, "model")[-1]["cle"] is True and "clé API" in of(published, "model")[-1]["label"]


def test_alerte_de_depense_sans_arret(astrel, published, monkeypatch):
    j, _, _ = astrel
    stopped = []
    j.brain.interrupt = lambda: stopped.append(1)
    j.brain.fournisseur = Effectif("cle_claude", "cle_claude")
    reglages.set("depense_alerte_usd", 2)
    j._note_depense({"total_cost_usd": 1.0})
    j._note_depense({"total_cost_usd": 2.5})
    alerts = [n for n in of(published, "notice") if "Dépense" in n["text"]]
    assert len(alerts) == 1 and alerts[0]["level"] == "warning" and not stopped
    j.brain.fournisseur = Effectif("abonnement", "abonnement")
    j._note_depense({"total_cost_usd": 99.0})  # abonnement : rien n'est compté
    assert fournisseurs.depense.jour() == pytest.approx(2.5)


def test_grok_abonnement_forfait_ni_depense_ni_ligne(astrel, published):
    """Plan P19 : Grok par abonnement (genre passerelle, `cle_api` vrai) est un FORFAIT : ni dépense en
    dollars, ni alerte, ni ligne « Dépense du jour » dans Réglages › Fournisseur."""
    j, _, _ = astrel
    eff = Effectif("passerelle", "xaiabo", "grok-4.3", outils=False)
    assert eff.cle_api and eff.facturation == "forfait"
    j.brain.fournisseur = eff
    fournisseurs.etat.actif = eff
    reglages.set("depense_alerte_usd", 1)
    j._note_depense({"total_cost_usd": 5.0})
    assert fournisseurs.depense.jour() == 0.0
    assert not [n for n in of(published, "notice") if "Dépense" in n["text"]]
    assert not [l for l in fournisseurs.vue()["value"]["lines"] if l["label"] == "Dépense du jour"]
    fournisseurs.etat.actif = Effectif("passerelle", "xai", "grok-4.3", outils=False)  # clé API Grok
    assert [l for l in fournisseurs.vue()["value"]["lines"] if l["label"] == "Dépense du jour"]


def test_compte_dit_ce_que_astrel_utilise():
    from astrel import compte as compte_mod

    assert compte_mod._fournisseur_line() is None
    fournisseurs.etat.actif = Effectif("cle_claude", "cle_claude")
    assert "Clé API Claude" in compte_mod._fournisseur_line()["text"]


def test_controle_d_installation_cle_api_normale(monkeypatch):
    spec = importlib.util.spec_from_file_location("check_install_p11", ROOT / "tools" / "check_install.py")
    chk = importlib.util.module_from_spec(spec)
    sys.modules["check_install_p11"] = chk
    spec.loader.exec_module(chk)
    status = {"loggedIn": True, "authMethod": "api_key"}
    monkeypatch.setattr(chk, "_out", lambda cmd, timeout=15.0: (0, "2.1.285") if "--version" in cmd
                        else (0, json.dumps(status)))
    assert [c.status for c in chk.check_claude("linux", ci=False)] == [chk.OK, chk.OK]
    status = {"loggedIn": False}
    monkeypatch.setattr(chk, "_fournisseur_choisi", lambda: "openai:gpt-5")
    assert chk.check_claude("linux", ci=False)[1].status == chk.INFO


# ------------------------------------------------------------------ la vraie passerelle (venv à part)

needs_venv = pytest.mark.skipif(not (REAL_VENV / "bin" / "python").exists(), reason="passerelle non installée")


def _post(url, body, token=None):
    headers = {"content-type": "application/json", **({"x-api-key": token} if token else {})}
    req = urllib.request.Request(url + "/v1/messages", data=json.dumps(body).encode(), headers=headers)
    return urllib.request.urlopen(req, timeout=60)


@needs_venv
def test_passerelle_reelle_jeton_cle_et_traces(monkeypatch, caplog):
    monkeypatch.setattr(config, "PASSERELLE_VENV", REAL_VENV)
    if not passerelle.installed():
        pytest.skip("passerelle installée à une autre version")
    caplog.set_level(logging.DEBUG)
    _secrets(CLE_API_OPENAI=CLE_OPENAI)
    gw = passerelle.passerelle
    with FauxOpenAI() as srv:
        url = gw.start("openai", "faux-modele", "CLE_API_OPENAI", base=srv.url)
        token = config.PASSERELLE_JETON_FILE.read_text()
        assert mode_prive(config.PASSERELLE_JETON_FILE, 0o600) and len(token) >= 32
        pid = gw._proc.pid
        raw = Path(f"/proc/{pid}/cmdline").read_bytes() + Path(f"/proc/{pid}/environ").read_bytes()
        assert CLE_OPENAI.encode() not in raw and token.encode() not in raw  # ni `ps`, ni environnement
        body = {"model": "claude-sonnet-4-5", "max_tokens": 50, "messages": [{"role": "user", "content": "Salut"}]}
        with pytest.raises(urllib.error.HTTPError) as err:
            _post(url, body)
        assert err.value.code == 401
        with pytest.raises(urllib.error.HTTPError):
            _post(url, body, token="faux-jeton")
        out = json.loads(_post(url, body, token=token).read())
        assert out["content"][0]["text"] == "Bonjour depuis le faux OpenAI."
        sent = srv.requests[-1]
        auth = {k.lower(): v for k, v in sent["headers"].items()}["authorization"]
        assert auth == f"Bearer {CLE_OPENAI}" and sent["body"]["model"] == "faux-modele"
        assert url == gw.start("openai", "faux-modele", "CLE_API_OPENAI", base=srv.url)  # même choix : gardée
        proc = gw._proc
        gw.stop()
        assert proc.poll() is not None and not config.PASSERELLE_JETON_FILE.exists()
    assert CLE_OPENAI not in caplog.text and token not in caplog.text


@needs_venv
def test_passerelle_reelle_grok(monkeypatch, caplog):
    """La vraie passerelle (LiteLLM, préfixe `xai/`) vers un faux serveur compatible OpenAI : la
    clé Grok part en `Bearer`, le modèle sans préfixe, rien dans `ps` ni l'environnement."""
    monkeypatch.setattr(config, "PASSERELLE_VENV", REAL_VENV)
    if not passerelle.installed():
        pytest.skip("passerelle installée à une autre version")
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("XAI_API_KEY", "xai-du-shell-ne-doit-pas-servir-0123456789")
    _secrets(CLE_API_XAI=CLE_XAI)
    gw = passerelle.passerelle
    with FauxOpenAI() as srv:
        url = gw.start("xai", "grok-4.3", "CLE_API_XAI", base=srv.url)
        try:
            token = config.PASSERELLE_JETON_FILE.read_text()
            pid = gw._proc.pid
            raw = Path(f"/proc/{pid}/cmdline").read_bytes() + Path(f"/proc/{pid}/environ").read_bytes()
            assert CLE_XAI.encode() not in raw and b"XAI_API_KEY" not in raw
            body = {"model": "claude-sonnet-4-5", "max_tokens": 50, "messages": [{"role": "user", "content": "Salut"}]}
            out = json.loads(_post(url, body, token=token).read())
            assert out["content"][0]["text"] == "Bonjour depuis le faux OpenAI."
            sent = srv.requests[-1]
            auth = {k.lower(): v for k, v in sent["headers"].items()}["authorization"]
            assert auth == f"Bearer {CLE_XAI}" and sent["body"]["model"] == "grok-4.3"
            assert sent["path"].endswith("/chat/completions")
        finally:
            gw.stop()
    assert CLE_XAI not in caplog.text


@needs_venv
@pytest.mark.skipif(not shutil.which("claude"), reason="CLI claude absent")
def test_vrai_cli_par_la_passerelle(monkeypatch, tmp_path):
    """Bout à bout : le VRAI CLI, lancé comme le fait Astrel pour OpenAI (conversation seule),
    parle au faux OpenAI à travers la vraie passerelle ; la clé n'est nulle part dans son process."""
    monkeypatch.setattr(config, "PASSERELLE_VENV", REAL_VENV)
    if not passerelle.installed():
        pytest.skip("passerelle installée à une autre version")
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "claude-settings.json")
    monkeypatch.setattr(config, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(config, "WORKDIR", tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))  # rien dans ~/.claude
    _secrets(CLE_API_OPENAI=CLE_OPENAI)
    monkeypatch.setenv("ASTREL_FOURNISSEUR_MODELES", '{"openai": ["faux-modele"]}')  # modèle ajouté sans code
    reglages.set("fournisseur", "openai:faux-modele")
    with FauxOpenAI() as srv:
        real_start = passerelle.passerelle.start
        monkeypatch.setattr(passerelle.passerelle, "start",
                            lambda f, m, cle, base="": real_start(f, m, cle, base=srv.url))
        b = Brain()
        b.start(fresh=True)
        try:
            raw = Path(f"/proc/{b._proc.pid}/environ").read_bytes() + Path(f"/proc/{b._proc.pid}/cmdline").read_bytes()
            events = []
            for ev in b.turn("Dis bonjour."):
                events.append(ev)
                if len(events) > 200:
                    break
        finally:
            b.stop()
    assert CLE_OPENAI.encode() not in raw
    init = next(e for e in events if e.kind == "init").data
    assert init["tools"] == [] and init["mcp_servers"] == []
    assert "Bonjour depuis le faux OpenAI." in "".join(e.text for e in events if e.kind == "text")
    assert events[-1].kind == "turn_end"
    posted = [r for r in srv.requests if r["path"].endswith("/chat/completions")]
    assert posted and all(r["body"]["model"] == "faux-modele" for r in posted)
