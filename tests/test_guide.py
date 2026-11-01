"""Guide de démarrage interactif (18/10, plan P5) : source unique docs/GUIDE-DEMARRAGE.md,
progression dans runtime/guide.json, cochage d'office, « Terminé » = fin du premier
lancement, commandes refusées à Claude, aide « ? » de la page « Réglages »."""
import json
import re

import pytest

from astrel import config, guide as guide_mod, settings
from astrel.guide import parse
from astrel.settings import reglages
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)

SAMPLE = """# Bienvenue

Introduction **courte**.

## Ton compte Claude
<!-- id: claude | etape | reglage: compte -->
Connecte-toi.

## Le micro
<!-- id: micro | etape | test: audio.test/mic | reglage: audio.mic -->
Parle.

```
## pas un titre (dans un bloc de code)
```

## Les canaux
<!-- id: canaux -->
Des onglets.

## Sans identifiant
Ignoré.

## Mauvais test
<!-- id: x1 | etape | test: ../../etc | reglage: rm -rf -->
Rien de dangereux ne passe.
"""


@pytest.fixture
def g(tmp_path, monkeypatch):
    src = tmp_path / "GUIDE.md"
    src.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(config, "GUIDE_FILE", src)
    monkeypatch.setattr(config, "GUIDE_STATE_FILE", tmp_path / "guide.json")
    monkeypatch.setattr(config, "FIRST_RUN_FILE", tmp_path / "neuf" / "first-run-done")
    monkeypatch.setattr(config, "WHATSAPP_AUTH_DIR", tmp_path / "whatsapp-auth")
    gd = guide_mod.Guide()
    monkeypatch.setattr(guide_mod, "guide", gd)
    return gd


def test_lecture_du_markdown():
    d = parse(SAMPLE)
    assert d["title"] == "Bienvenue" and d["intro"] == "Introduction **courte**."
    ids = [t["id"] for t in d["topics"]]
    assert ids == ["claude", "micro", "canaux", "x1"]
    micro = d["topics"][1]
    assert micro["etape"] and micro["test"] == {"key": "audio.test", "action": "mic"} and micro["reglage"] == "audio.mic"
    assert "## pas un titre" in micro["corps"] and "<!--" not in micro["corps"]
    assert d["topics"][2]["etape"] is False and d["topics"][2]["test"] is None
    assert d["topics"][3]["test"] is None and d["topics"][3]["reglage"] == ""  # valeurs refusées


def test_progression_et_cochage_d_office(g, monkeypatch):
    from astrel import compte, demarrage

    d = g.describe()
    assert d["first_run"] is True and d["steps"] == 3
    by = {t["id"]: t for t in d["topics"]}
    assert by["claude"]["done"] and by["claude"]["auto"]  # compte connecté (conftest)
    assert not by["micro"]["done"]
    monkeypatch.setattr(demarrage.demarrage, "_result", {"checks": [{"name": "micro", "status": "OK"}]})
    assert {t["id"]: t for t in g.describe()["topics"]}["micro"]["auto"]
    monkeypatch.setattr(demarrage.demarrage, "_result", {"checks": []})
    d = g.mark("micro", True)
    assert {t["id"]: t for t in d["topics"]}["micro"]["done"] and d["steps_done"] == 2
    assert "micro" in json.loads(config.GUIDE_STATE_FILE.read_text())["done"]
    d = g.mark("micro", False)
    assert not {t["id"]: t for t in d["topics"]}["micro"]["done"]
    assert g.mark("canaux", True)["ok"] is False  # pas une étape
    assert g.mark("inconnu", True)["ok"] is False
    compte.compte._state = {"loggedIn": False}
    assert not {t["id"]: t for t in g.describe()["topics"]}["claude"]["done"]


def test_whatsapp_relie_coche_d_office(g, tmp_path):
    assert not guide_mod._whatsapp_ok()
    (tmp_path / "whatsapp-auth").mkdir()
    (tmp_path / "whatsapp-auth" / "self.json").write_text(json.dumps({"pn": "336@s.whatsapp.net"}))
    assert guide_mod._whatsapp_ok()


def test_termine_pose_le_marqueur(g):
    assert g.describe()["first_run"]
    d = guide_mod.command("guide_mark", {"finish": True})
    assert d["ok"] and d["first_run"] is False and config.FIRST_RUN_FILE.exists()


def test_commandes_du_coeur_refusees_a_claude(astrel, g, published):
    j, _, _ = astrel
    assert j.on_command({"cmd": "guide_get", "_from_claude": True})["ok"] is False
    assert j.on_command({"cmd": "guide_mark", "finish": True, "_from_claude": True})["ok"] is False
    assert not config.FIRST_RUN_FILE.exists()
    d = j.on_command({"cmd": "guide_get"})
    assert d["ok"] and [t["id"] for t in d["topics"]][:2] == ["claude", "micro"]
    assert j.on_command({"cmd": "guide_mark", "topic": "micro", "done": True})["steps_done"] >= 1
    assert j.on_command({"cmd": "guide_mark", "finish": True})["first_run"] is False
    assert of(published, "settings")[-1]["first_run"] is False  # la page « Réglages » suit


# ------------------------------------------------------------------ le vrai guide (docs/GUIDE-DEMARRAGE.md)

REAL = parse(config.GUIDE_FILE.read_text(encoding="utf-8")) if config.GUIDE_FILE.exists() else None
needs_guide = pytest.mark.skipif(REAL is None, reason="docs/GUIDE-DEMARRAGE.md absent")


@needs_guide
def test_vrai_guide_etapes_et_sujets():
    ids = {t["id"]: t for t in REAL["topics"]}
    steps = [t["id"] for t in REAL["topics"] if t["etape"]]
    for step in ("claude", "micro", "voix", "langue", "projets", "whatsapp", "demarrage-auto"):
        assert step in steps, step
    for topic in ("parler", "hud", "canaux", "memoire", "agents", "garde-fou", "reflexes", "mode-plan",
                  "absent", "bulle", "rappels", "connecteurs", "depannage", "limites"):
        assert topic in ids and not ids[topic]["etape"], topic
    for t in REAL["topics"]:
        assert len(t["corps"]) > 80, t["id"]


@needs_guide
def test_vrai_guide_boutons_branches_sur_des_reglages_existants():
    for t in REAL["topics"]:
        if t["reglage"]:
            assert t["reglage"] in settings.ITEMS, t["id"]
        if t["test"]:
            assert settings.ITEMS[t["test"]["key"]].kind in settings.PANEL_KINDS, t["id"]
    assert {"key": "audio.test", "action": "mic"} in [t["test"] for t in REAL["topics"]]


@needs_guide
def test_aide_des_reglages_pointe_vers_des_sujets_existants():
    ids = {t["id"] for t in REAL["topics"]}
    assert set(settings.GUIDE_TOPICS.values()) <= ids
    assert set(settings.GUIDE_TOPICS) <= set(settings.ITEMS)
    items = {i["key"]: i for g in reglages.describe()["groups"] for i in g["items"]}
    assert items["whatsapp"]["guide"] == "whatsapp" and items["audio.mic"]["guide"] == "micro"
    assert "guide" not in items["skills"]


@needs_guide
def test_vrai_guide_sans_donnee_personnelle():
    text = config.GUIDE_FILE.read_text(encoding="utf-8")
    for word in ("/data/", "/home/", "@gmail", "Debian 13"):
        assert word not in text, word
    assert not re.search(r"\+?33\s?[67](?:[\s.]?\d{2}){4}", text)  # pas de vrai numéro


def test_lignes_recollees_hors_blocs():
    from astrel.guide import reflow

    src = "Une phrase coupée\nau milieu.\n\n- une puce\n  qui continue\n- une autre\n\n```\nligne 1\nligne 2\n```\nfin"
    assert reflow(src) == ("Une phrase coupée au milieu.\n\n- une puce qui continue\n- une autre\n\n"
                           "```\nligne 1\nligne 2\n```\nfin")


def test_sujets_des_reglages_existent_dans_le_guide():
    """19/10 (plan P10, audit du code en dur) : `GUIDE_TOPICS` reste une table écrite à la main,
    mais chaque sujet qu'elle cite doit exister dans docs/GUIDE-DEMARRAGE.md."""
    from astrel import config as _config
    from astrel.guide import parse
    from astrel.settings import GUIDE_TOPICS, ITEMS

    ids = {t["id"] for t in parse(_config.GUIDE_FILE.read_text(encoding="utf-8"))["topics"]}
    assert set(GUIDE_TOPICS.values()) <= ids, set(GUIDE_TOPICS.values()) - ids
    assert set(GUIDE_TOPICS) <= set(ITEMS), set(GUIDE_TOPICS) - set(ITEMS)
