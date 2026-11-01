"""Groupe « Compte » de la page « Réglages » (17/10, plan P4, lot 2) : état par
`claude auth status --json`, connexion `claude auth login --claudeai`, déconnexion,
avec un FAUX `claude` (config.CLAUDE_BIN) : jamais le compte de l'utilisateur."""
import json
import re
import sys
import time

import pytest

from astrel import compte as compte_mod
from astrel import config
from astrel.settings import reglages
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)

FAKE = r'''#!@PY@
import json, os, sys, time
from pathlib import Path
d = Path(@DIR@)
state = d / "state.json"
args = sys.argv[1:]
(d / "calls.txt").open("a").write(" ".join(args) + "\n")
if args[:2] == ["auth", "status"]:
    s = json.loads(state.read_text()) if state.exists() else {"loggedIn": False, "authMethod": "none"}
    print(json.dumps({**s, "orgId": "secret-org", "apiProvider": "firstParty", "token": "NE-PAS-GARDER"}))
elif args[:2] == ["auth", "login"]:
    print("Opening browser to sign in…", flush=True)
    print("If the browser didn't open, visit: https://claude.ai/oauth/authorize?code=true&state=abc", flush=True)
    print("token-like-line sk-ant-xyz", flush=True)
    while not (d / "go").exists():
        time.sleep(0.02)
    state.write_text(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "pro",
                                 "email": "ami@exemple.fr", "orgName": "Ami"}))
elif args[:2] == ["auth", "logout"]:
    state.write_text(json.dumps({"loggedIn": False, "authMethod": "none"}))
'''


@pytest.fixture
def fake(tmp_path, monkeypatch):
    exe = tmp_path / "claude"
    exe.write_text(FAKE.replace("@PY@", sys.executable).replace("@DIR@", repr(str(tmp_path))))
    exe.chmod(0o755)
    monkeypatch.setattr(config, "CLAUDE_BIN", str(exe))
    c = compte_mod.Compte()
    monkeypatch.setattr(compte_mod, "compte", c)
    return c, tmp_path


def wait(cond, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


def lines(view):
    return {line["label"]: line for line in view["value"]["lines"]}


def ids(view):
    return [a["id"] for a in view["actions"]]


def write_state(d, **s):
    (d / "state.json").write_text(json.dumps(s))


def test_etat_lu_hors_du_fil_et_cinq_champs_seulement(fake):
    c, d = fake
    write_state(d, loggedIn=True, authMethod="claude.ai", subscriptionType="max", email="ami@exemple.fr", orgName="Ami")
    first = c.view()
    assert lines(first)["État"]["text"] == "vérification…"  # rien d'attendu : le fil lit à part
    assert wait(lambda: c._state is not None)
    assert set(c._state) == set(compte_mod.FIELDS)  # ni orgId, ni jeton
    v = c.view()
    got = lines(v)
    assert got["État"]["level"] == "ok" and got["Abonnement"]["text"] == "Max"
    assert got["E-mail"]["text"] == "ami@exemple.fr" and "Attention" not in got
    assert ids(v) == ["logout", "refresh"] and v["actions"][0]["danger"] is True


def test_cache_de_30_s(fake, monkeypatch):
    c, d = fake
    write_state(d, loggedIn=False)
    c.refresh(wait=True)
    calls = (d / "calls.txt").read_text().count("status")
    c.view()
    c.view()
    time.sleep(0.2)
    assert (d / "calls.txt").read_text().count("status") == calls
    monkeypatch.setattr(compte_mod, "CACHE_S", 0.0)
    c.view()
    assert wait(lambda: (d / "calls.txt").read_text().count("status") > calls)


def test_cle_api_etat_normal(fake):
    """20/10 (plan P11) : être connecté par clé API est un état normal, signalé sans alerte."""
    c, d = fake
    write_state(d, loggedIn=True, authMethod="api_key")
    c.refresh(wait=True)
    info = lines(c.view())["Facturation"]
    assert info["level"] == "info" and "facturée" in info["text"]
    assert "Attention" not in lines(c.view())


def test_claude_code_absent(fake, monkeypatch, tmp_path):
    c, _ = fake
    monkeypatch.setattr(config, "CLAUDE_BIN", str(tmp_path / "nulle-part" / "claude"))
    c.refresh(wait=True)
    v = c.view()
    assert lines(v)["Claude Code"]["level"] == "error"
    assert v["value"]["link"]["url"].startswith("https://") and ids(v) == ["refresh"]
    assert c.run("login")["ok"] is False


def test_connexion_lien_de_secours_puis_connecte(fake):
    c, d = fake
    published_views = []
    c.on_change = lambda: published_views.append(c.view())
    write_state(d, loggedIn=False)
    c.refresh(wait=True)
    assert ids(c.view()) == ["login", "refresh"]
    res = c.run("login")
    assert res["ok"] and c.logging_in()
    assert c.run("login")["ok"] is False  # une seule à la fois
    assert wait(lambda: "link" in c.view()["value"])
    v = c.view()
    assert v["value"]["link"]["url"] == "https://claude.ai/oauth/authorize?code=true&state=abc"
    assert ids(v) == ["cancel", "refresh"] and lines(v)["Connexion"]["text"].startswith("en cours")
    assert "auth login --claudeai" in (d / "calls.txt").read_text()
    (d / "go").touch()
    assert wait(lambda: not c.logging_in() and (c._state or {}).get("loggedIn"))
    v = c.view()
    assert lines(v)["Dernière connexion"]["text"] == "connexion terminée" and "link" not in v["value"]
    assert ids(v) == ["logout", "refresh"]
    # Rien de la sortie du CLI (ligne « sk-ant… ») n'est gardé hors de l'adresse.
    assert not any("sk-ant" in json.dumps(x) for x in published_views)


def test_connexion_annulee(fake):
    c, d = fake
    write_state(d, loggedIn=False)
    c.refresh(wait=True)
    c.run("login")
    proc = c._login
    assert c.run("cancel")["ok"]
    assert wait(lambda: not c.logging_in())
    assert proc.poll() is not None and lines(c.view())["Dernière connexion"]["text"] == "annulée"


def test_connexion_abandonnee_au_bout_du_delai(fake, monkeypatch):
    c, d = fake
    monkeypatch.setattr(compte_mod, "LOGIN_TIMEOUT_S", 0.3)
    write_state(d, loggedIn=False)
    c.refresh(wait=True)
    c.run("login")
    assert wait(lambda: not c.logging_in())
    assert lines(c.view())["Dernière connexion"]["text"].startswith("abandonnée")


def test_deconnexion(fake):
    c, d = fake
    write_state(d, loggedIn=True, authMethod="claude.ai")
    c.refresh(wait=True)
    res = c.run("logout")
    assert res["ok"] and "Déconnecté" in res["text"]
    assert wait(lambda: (c._state or {}).get("loggedIn") is False)


def test_aucun_fichier_lu():
    """Le module ne lit aucun fichier (ni .credentials.json ni autre) : il passe par le CLI."""
    src = (config.ROOT / "astrel" / "compte.py").read_text(encoding="utf-8")
    code = src.split('"""', 2)[2]  # sans la docstring
    assert "credentials" not in code and not re.search(r"(?<![A-Za-z])open\(", code) and "read_text" not in code


# ------------------------------------------------------------------ page « Réglages » et cœur

def test_groupe_compte_en_tete_et_ouvert(fake):
    c, d = fake
    write_state(d, loggedIn=True, authMethod="claude.ai")
    c.refresh(wait=True)
    g = reglages.describe()["groups"][0]
    assert g["id"] == "compte" and g["open"] is True
    it = g["items"][0]
    assert it["kind"] == "info" and it["actions"][0]["id"] == "logout" and it["value"]["lines"]


def test_settings_action_par_le_coeur(astrel, published, fake):
    j, _, _ = astrel
    c, d = fake
    write_state(d, loggedIn=True, authMethod="claude.ai")
    c.refresh(wait=True)
    res = j.on_command({"cmd": "settings_action", "key": "compte", "action": "refresh", "_from_claude": False})
    assert res["ok"] and res["text"] == "Vérification lancée." and res["groups"]
    # bouton absent de la vue (on est connecté : pas de « login ») → refusé
    res = j.on_command({"cmd": "settings_action", "key": "compte", "action": "login", "_from_claude": False})
    assert res["ok"] is False and "indisponible" in res["error"]
    # Terminal : --send "settings_action compte refresh"
    assert j.on_command({"cmd": "settings_action compte refresh"})["ok"]
    res = j.on_command({"cmd": "settings_action", "key": "skills", "action": "x", "_from_claude": False})
    assert res["ok"] is False


def test_settings_action_refusee_a_claude(astrel, published, fake):
    j, _, _ = astrel
    c, d = fake
    write_state(d, loggedIn=True, authMethod="claude.ai")
    c.refresh(wait=True)
    res = j.on_command({"cmd": "settings_action", "key": "compte", "action": "logout", "_from_claude": True})
    assert res["ok"] is False and "Claude" in of(published, "notice")[-1]["text"]
    assert "logout" not in (d / "calls.txt").read_text()


def test_mot_exige_et_bouton_grise(monkeypatch):
    seen = []
    view = {"value": {"lines": []}, "actions": [
        {"id": "wipe", "label": "Effacer", "danger": True, "word": "EFFACER"},
        {"id": "off", "label": "Grisé", "disabled": True, "reason": "pas ici"}]}
    reglages.bind("compte", view=lambda: view, run=lambda a, cmd: seen.append(a) or {"text": "fait"})
    try:
        assert reglages.action("compte", "wipe", {})["ok"] is False
        assert reglages.action("compte", "wipe", {"confirm": "oui"})["ok"] is False and seen == []
        assert reglages.action("compte", "off", {})["error"] == "pas ici" and seen == []
        assert reglages.action("compte", "wipe", {"confirm": " effacer "}) == {"ok": True, "text": "fait"}
        assert seen == ["wipe"]
    finally:
        reglages._bound.clear()


def test_ligne_info_jamais_ecrite():
    from astrel.settings import SettingError

    with pytest.raises(SettingError, match="lecture seule"):
        reglages.set("compte", {"lines": []})
