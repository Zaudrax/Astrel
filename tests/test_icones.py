"""Icônes locales des services (19/10, plan P10) : résolution du nom, repli stable à lettre,
couleur lisible sur le fond sombre, icônes présentes dans le HUD, champ `icons` des Réglages."""
import json

import pytest

from astrel import icones
from astrel.settings import reglages


def test_slug_comme_simple_icons():
    assert icones.slugify("Google Drive") == "googledrive"
    assert icones.slugify("Monday.com") == "mondaydotcom"
    assert icones.slugify("Hugging Face") == "huggingface" and icones.slugify("Ça & là+") == "caandlaplus"


@pytest.mark.parametrize("name, slug", [
    ("claude.ai Gmail", "gmail"), ("claude.ai Google Drive", "googledrive"),
    ("claude.ai Google Calendar", "googlecalendar"), ("claude.ai Vercel", "vercel"),
    ("claude.ai Claude Docs", "claude"),  # sans le dernier mot
])
def test_icone_du_vrai_dossier(name, slug):
    got = icones.for_name(name)
    assert got["src"] == f"assets/icons/{slug}.svg" and (icones.ICON_DIR / f"{slug}.svg").is_file()
    assert got["color"].startswith("#") and len(got["color"]) == 7


def test_repli_lettre_stable():
    a, b = icones.for_name("claude.ai Canva"), icones.for_name("claude.ai Canva")
    assert a == b and a["src"] is None and a["letter"] == "C"
    assert icones.for_name("claude.ai harvest")["letter"] == "H"
    assert icones.for_name("claude.ai Mailroom")["color"] != icones.for_name("claude.ai harvest")["color"]
    assert icones.for_name("")["letter"] == "?"


def test_couleur_lisible_sur_fond_sombre():
    assert icones.readable("#000000") != "#000000"
    assert icones._luminance(icones.readable("#000000")) >= icones.MIN_LUMINANCE
    assert icones.readable("#EA4335") == "#EA4335"  # déjà lisible : inchangée
    assert icones.for_name("claude.ai Vercel")["color"] != "#000000"


def test_index_absent_ou_abime(tmp_path, monkeypatch):
    monkeypatch.setattr(icones, "ICON_DIR", tmp_path)
    assert icones.for_name("claude.ai Gmail")["src"] is None  # pas d'index : repli
    (tmp_path / "index.json").write_text("{pas du json", encoding="utf-8")
    assert icones.for_name("claude.ai Gmail")["src"] is None
    (tmp_path / "index.json").write_text(json.dumps({"icons": {"gmail": {"title": "Gmail", "hex": "EA4335"},
                                                              "../x": {"title": "X", "hex": "000000"}}}),
                                         encoding="utf-8")
    assert icones.for_name("claude.ai Gmail")["src"] is None  # indexée mais SVG absent : repli
    (tmp_path / "gmail.svg").write_text("<svg/>", encoding="utf-8")
    assert icones.for_name("claude.ai Gmail")["src"] == "assets/icons/gmail.svg"
    assert icones.for_name("../x")["src"] is None  # jamais un chemin hors du dossier


def test_icones_du_hud_legeres_et_index_coherent():
    data = json.loads((icones.ICON_DIR / "index.json").read_text(encoding="utf-8"))
    svgs = {p.stem for p in icones.ICON_DIR.glob("*.svg")}
    assert set(data["icons"]) == svgs and "simple-icons" in data["source"]
    assert sum(p.stat().st_size for p in icones.ICON_DIR.iterdir()) < 300_000


def test_reglages_portent_les_icones():
    d = reglages.describe()
    found = {i["key"]: i for g in d["groups"] for i in g["items"]}
    for key in ("connectors", "plugins", "skills"):
        assert set(found[key]["icons"]) == set(found[key]["value"]), key
    conn = found["connectors"]["icons"]
    assert conn["claude.ai Gmail"]["src"] == "assets/icons/gmail.svg"
    assert conn["claude.ai Canva"]["src"] is None and conn["claude.ai Canva"]["letter"] == "C"
