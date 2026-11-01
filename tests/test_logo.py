"""Logo anneau d'Astrel (22/10, plan P25) : `tools/logo.py` dessine les 4 variantes en vecteur
depuis UNE description, couleurs lues dans `config/themes.json` ; les fichiers de
`hud/assets/logo/` sont à jour."""
import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageChops

from astrel import theme

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("astrel_logo", ROOT / "tools" / "logo.py")
logo = importlib.util.module_from_spec(spec)
sys.modules["astrel_logo"] = logo
spec.loader.exec_module(logo)


def test_fichiers_a_jour_et_valides():
    for lettre in logo.VARIANTES:
        svg = logo.svg_path(lettre)
        assert svg.read_text(encoding="utf-8") == logo.svg(lettre), f"relancer tools/logo.py ({svg.name})"
        ET.fromstring(svg.read_text(encoding="utf-8"))
        for size in logo.SIZES:
            disk = Image.open(logo.png_path(lettre, size))
            assert disk.size == (size, size) and disk.mode == "RGBA"
            assert ImageChops.difference(disk, logo.image(lettre, size)).getbbox() is None, \
                f"relancer tools/logo.py ({lettre} {size})"


def test_coins_transparents_centre_opaque_et_marge():
    for lettre in logo.VARIANTES:
        for size in (16, 48, 1024):
            im = logo.image(lettre, size)
            assert im.getpixel((0, 0))[3] == 0 and im.getpixel((size - 1, size - 1))[3] == 0
            assert im.getpixel((size // 2, size // 2))[3] == 255
        v = logo.VARIANTES[lettre]
        if "etoile" in v:  # étoile Astrel : pointes à la marge de 15 %
            etoile = [f for f in logo.formes(lettre) if f[0] == "etoile"][0]
            assert abs(etoile[3] - (0.5 - logo.MARGE)) < 1e-9
            continue
        anneau = [f for f in logo.formes(lettre) if f[0] == "anneau"][0]
        assert abs(anneau[3] + v["anneau"] / 2 - (0.5 - logo.MARGE)) < 1e-9  # bord extérieur à 15 %


def test_couleurs_lues_dans_themes_json():
    pal = theme.palette("classique", "dark")
    svg = logo.svg("d")
    assert pal["bg"] in svg and pal["accent"] in svg
    im = logo.image("d", 1024)
    assert im.getpixel((512, 512))[:3] == tuple(round(c * 255) for c in theme.rgb(pal["accent"]))
    light = theme.palette("premium", "light", theme.data()["default"]["accent"])
    assert light["accent"] in logo.svg("c") and light["bg"] in logo.svg("c")


def test_lisible_a_16_px():
    """À 16 px, l'anneau et le point se distinguent du fond : un trou sombre entre les deux."""
    for lettre in logo.VARIANTES:
        im = logo.image(lettre, 16).convert("RGB")
        fond = im.getpixel((8, 1))
        assert im.getpixel((8, 8)) != fond and im.getpixel((8, 3)) != fond


def test_etoile_astrel_meme_geometrie_que_la_bulle():
    """Variante e (P31 lot D) : étoile à 4 branches de `bubble_premium._star` (pincement 0,20), accent
    Premium sur fond Premium sombre ; pointes et centre à l'accent, creux entre deux branches = fond."""
    v = logo.VARIANTES["e"]
    assert v["pincement"] == 0.20
    pal = theme.palette("premium", "dark", theme.data()["default"]["accent"])
    svg = logo.svg("e")
    assert pal["accent"] in svg and "<path d=\"M512 153.6 C512 440.32" in svg and "Astrel" in svg
    im = logo.image("e", 1024).convert("RGB")
    accent = tuple(round(c * 255) for c in theme.rgb(pal["accent"]))
    assert im.getpixel((512, 512)) == accent
    assert im.getpixel((512, 250)) == accent and im.getpixel((780, 512)) == accent  # branches
    assert im.getpixel((300, 300)) != accent  # creux entre deux branches


def test_choix_remplace_l_icone_en_place(tmp_path, monkeypatch):
    monkeypatch.setattr(logo, "CHOIX_FILE", tmp_path / "choix.txt")
    monkeypatch.setattr(logo, "ICON_FILE", tmp_path / "astrel-icon.png")
    assert logo.lire_choix() is None
    logo.appliquer_choix("B")
    assert logo.lire_choix() == "b"
    im = Image.open(tmp_path / "astrel-icon.png")
    assert im.size == (1024, 1024) and im.mode == "RGBA"
    (tmp_path / "choix.txt").write_text("x")
    assert logo.lire_choix() is None
