"""Groupe « Mises à jour » de la page « Réglages » (17/10, plan P4, lot 7) : version,
`git fetch`, `update.py` (jamais update.sh) seulement avec une origine et sans fichier suivi
modifié. Faux git : rien n'est lancé pour de vrai."""
import types

import pytest

from astrel import __version__, maj as maj_mod
from astrel.settings import reglages
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)


@pytest.fixture
def git(monkeypatch, tmp_path):
    state = {"origin": "https://jeton123@github.com/ami/astrel.git", "status": "", "behind": "2", "fetch": 0,
             "git": True}
    calls, launched = [], []

    def fake(*args, timeout=10.0):
        calls.append(args)
        out = {("rev-parse", "--is-inside-work-tree"): "true" if state["git"] else "",
               ("rev-parse", "--short", "HEAD"): "abc1234",
               ("remote", "get-url", "origin"): state["origin"],
               ("status", "--porcelain", "--untracked-files=no"): state["status"],
               ("rev-list", "--count", "HEAD..@{upstream}"): state["behind"]}.get(args, "")
        code = state["fetch"] if args[0] == "fetch" else 0
        return types.SimpleNamespace(stdout=out, stderr="", returncode=code)

    monkeypatch.setattr(maj_mod, "_git", fake)
    monkeypatch.setattr(maj_mod.subprocess, "Popen", lambda cmd, **k: launched.append((cmd, k)))
    # 30/10 : `update` ouvre `config.ROOT / "logs" / "update.log"` avant de lancer update.py ; sans ceci,
    # le test créait ou touchait le VRAI logs/update.log (git et Popen sont faux, ROOT ne sert qu'à eux).
    monkeypatch.setattr(maj_mod.config, "ROOT", tmp_path)
    m = maj_mod.MiseAJour()
    monkeypatch.setattr(maj_mod, "maj", m)
    return m, state, calls, launched


def actions(view):
    return {a["id"]: a for a in view["actions"]}


def test_version_et_depot_sans_identifiants(git):
    m, *_ = git
    v = m.view()
    lines = {line["label"]: line["text"] for line in v["value"]["lines"]}
    assert lines["Version"] == f"{__version__} (commit abc1234)"
    assert lines["Dépôt"] == "https://github.com/ami/astrel.git" and lines["État"] == "pas encore vérifié"


def test_sans_depot_d_origine_tout_grise(git):
    m, state, calls, launched = git
    state["origin"] = ""
    a = actions(m.view())
    assert a["check"]["disabled"] and a["update"]["disabled"] and "origine" in a["update"]["reason"]
    assert m.run("update")["ok"] is False and m.run("check")["ok"] is False
    assert launched == [] and not any(c[0] == "fetch" for c in calls)
    assert reglages.action("maj", "update", {})["ok"] is False


def test_fichiers_suivis_modifies(git):
    m, state, _, launched = git
    state["status"] = " M astrel/core.py\n M hud/src/settings.js\n"
    m.check(wait=True)
    a = actions(m.view())
    assert a["update"]["disabled"] and a["update"]["reason"].startswith("2 fichier(s) suivi(s) modifié(s)")
    assert m.run("update")["ok"] is False and launched == []


def test_verifier_puis_mettre_a_jour(git):
    m, state, calls, launched = git
    res = m.run("check")
    assert res["ok"]
    for _ in range(200):
        if not m._checking:
            break
        import time
        time.sleep(0.01)
    assert ("fetch", "--quiet", "origin") in calls
    v = m.view()
    assert {line["label"]: line["text"] for line in v["value"]["lines"]}["État"] == "2 nouveauté(s) disponible(s)"
    a = actions(v)
    assert not a["update"]["disabled"] and a["update"]["confirm"]
    res = m.run("update")
    assert res["ok"] and len(launched) == 1
    cmd, kw = launched[0]
    assert cmd[-1].endswith("update.py") and not any("update.sh" in x for x in cmd)
    assert kw["start_new_session"] is True
    assert actions(m.view())["update"]["reason"] == "mise à jour déjà lancée"
    assert m.run("update")["ok"] is False and len(launched) == 1


def test_deja_a_jour(git):
    m, state, _, launched = git
    state["behind"] = "0"
    m.check(wait=True)
    assert actions(m.view())["update"]["reason"] == "déjà à jour"


def test_depot_injoignable(git):
    m, state, *_ = git
    state["fetch"] = 128
    m.check(wait=True)
    assert {line["label"]: line["text"] for line in m.view()["value"]["lines"]}["État"].startswith("dépôt injoignable")


def test_pas_un_depot_git(git):
    m, state, *_ = git
    state["git"] = False
    assert "pas un dépôt git" in actions(m.view())["update"]["reason"]


def test_adresse_nettoyee():
    assert maj_mod.clean_url("https://user:pass@github.com/a/b.git") == "https://github.com/a/b.git"
    assert maj_mod.clean_url("git@github.com:a/b.git") == "git@github.com:a/b.git"
