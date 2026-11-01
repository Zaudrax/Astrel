"""Images IA, lot A5 du plan P40 (28/10, demande de l'utilisateur : « finir tout le multicompte / images
IA ») : commande `image_piece` du panneau « Images » du HUD (pièce jointe déposée en base64, filtrée
comme un chemin de `generate_image`), et contrat HUD ↔ cœur lu dans hud/src (icônes des Réglages,
bouton « Essayer avec X », aucun innerHTML). Le rendu est contrôlé en Chrome headless par
tools/hud_check_rendu.py (`check_images_panneau`)."""
from __future__ import annotations

import base64
import re

import pytest

from astrel import attachments, config, core, ia_web

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c63f8cfc0500f0004850180848a6c210000000049454e44ae426082")


@pytest.fixture
def pieces_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(attachments, "ATTACH_DIR", tmp_path / "attachments")
    return tmp_path / "attachments"


def _piece(**cmd):
    return core.Astrel._image_piece(cmd)


def test_image_piece_png_rend_un_chemin_accepte_par_generate_image(pieces_dir):
    r = _piece(name="croquis.png", data=base64.b64encode(PNG).decode())
    assert r["ok"] and r["name"].endswith("croquis.png")
    chemin = r["path"]
    assert chemin.startswith(str(pieces_dir.resolve())) and open(chemin, "rb").read() == PNG
    # le chemin rendu passe le MÊME filtre que les chemins de l'outil MCP
    assert ia_web.pieces([chemin]) == ([chemin], "")


@pytest.mark.parametrize("cmd, erreur", [
    ({"data": "pas du base64 !"}, "pièce jointe illisible"),
    ({"data": ""}, "pièce jointe illisible"),
    ({"data": None}, "pièce jointe illisible"),
    ({"data": base64.b64encode(b"x" * (ia_web.PIECE_MAX_OCTETS + 1)).decode()}, "image trop lourde (5 Mo au plus)"),
])
def test_image_piece_refus_avant_ecriture(pieces_dir, cmd, erreur):
    r = _piece(name="a.png", **cmd)
    assert r == {"ok": False, "error": erreur}
    assert not pieces_dir.exists() or not any(pieces_dir.iterdir())  # rien d'écrit


def test_image_piece_pas_une_image_effacee(pieces_dir):
    r = _piece(name="notes.png", data=base64.b64encode(b"%PDF-1.4 pas une image").decode())
    assert r == {"ok": False, "error": "pas une image PNG, JPEG ou WebP"}
    assert not any(pieces_dir.iterdir())  # enregistrée puis refusée : effacée aussitôt


def test_image_piece_refusee_a_claude(pieces_dir):
    r = _piece(name="a.png", data=base64.b64encode(PNG).decode(), _from_claude=True)
    assert not r["ok"] and "l'utilisateur seul" in r["error"]
    assert not pieces_dir.exists() or not any(pieces_dir.iterdir())


def test_image_piece_passe_par_la_commande_du_coeur(pieces_dir):
    """`image_piece` est routée par `_on_command` vers `_image_command` (pas « commande inconnue »)."""
    astrel = core.Astrel.__new__(core.Astrel)
    r = core.Astrel._image_command(astrel, "image_piece", {"name": "a.png", "data": base64.b64encode(PNG).decode()})
    assert r["ok"]
    src = (config.ROOT / "astrel" / "core.py").read_text(encoding="utf-8")
    assert '"image_piece", *images_ia.COMMANDES_COMPTES' in src


def test_textes_d_erreur_traduits():
    import json
    en = json.loads((config.ROOT / "astrel" / "locales" / "en.json").read_text(encoding="utf-8"))
    for fr in ("pièce jointe illisible", "image trop lourde (5 Mo au plus)", "pas une image PNG, JPEG ou WebP",
               "pièces jointes du panneau Images : l'utilisateur seul (HUD)"):
        assert en.get(fr), fr
    # Fiche 8 du relecteur (A4) : clés orphelines de A1 retirées
    for orpheline in ("Pièces jointes pas encore prises en charge.",
                      "ChatGPT et Bing arrivent après la validation du pilote : Gemini seulement pour l'instant"):
        assert orpheline not in en


def test_hud_contrat_images():
    src = config.ROOT / "hud" / "src"
    js = (src / "images.js").read_text(encoding="utf-8")
    main = (src / "main.js").read_text(encoding="utf-8")
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML"):
        assert bad not in js
    # panneau : mêmes commandes que le cœur, réglages l'utilisateur seulement
    for cmd in ('"image_generate"', '"image_piece"', '"image_status"', '"settings_set"', "`image_compte_${action}`",
                '"image_compte_ajouter"', 'on("ia_etat"'):
        assert cmd in js, cmd
    # aller-retour 1 (fiche 3) : garde des modificateurs, Entrée = état du bouton, ImageBitmap fermé,
    # message français pour une pièce illisible, accusé qui ne retire que ce qui est parti
    assert "e.altKey || e.ctrlKey || e.metaKey" in js
    assert "panel.sending || panel.go.disabled) return;" in js
    assert "bmp.close();" in js and 't("images.unreadable")' in js
    assert "if (panel.prompt.value === typed)" in js and "for (const p of sent)" in js
    # carte : « Essayer avec X » = image_retry + engine (moteur PROPOSÉ par le cœur seulement)
    assert 'command("image_retry", { job_id: m.imageJob, engine: other })' in main
    assert re.search(r"const other = String\(ev\.proposition", main)
    # le panneau est sous la barre, dans #dialog, et reçoit la souris
    html = (config.ROOT / "hud" / "index.html").read_text(encoding="utf-8")
    assert html.index('id="composer"') < html.index('id="image-panel"') < html.index("</section>")
    css = (config.ROOT / "hud" / "styles.css").read_text(encoding="utf-8")
    assert re.search(r"#attachments, #image-panel \{ pointer-events: auto; \}", css)
