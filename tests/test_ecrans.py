"""Plusieurs écrans (22/10, plan P24, demande du 21/10) : `astrel/ecrans.py`, l'outil MCP
`screen_capture(ecran)` + `list_screens`, et WATCH (`senses/watch.py`).

Disposition MESURÉE le 22/10 (gdbus GetCurrentState de Mutter, lecture seule) : HDMI-1 « LG FULL HD »
1920x1080 en (0,0) ; eDP-1 « Affichage intégré » 2560x1600, échelle 1,502347, en (0,1080), principal.
Aucune vraie capture ici : sous-processus et portail simulés (la capture côté portail est dans
tests/test_platform.py).
"""
from __future__ import annotations

import asyncio
import subprocess
import sys

import pytest

from astrel import ecrans

ECHELLE = 1.502347469329834  # échelle réelle du portable (GetCurrentState)


def _mode(w, h, courant, echelle=1.0):
    return (f"{w}x{h}@60.000", w, h, 60.0, echelle, [1.0, echelle], {"is-current": True} if courant else {})


# Réponse de GetCurrentState déballée, réduite aux champs lus (relevé du 22/10).
ETAT_MESURE = (
    1,
    [
        (("HDMI-1", "GSM", "LG FULL HD", "0x00000000"),
         [_mode(1920, 1080, False), _mode(1920, 1080, True), _mode(1280, 720, False)],
         {"is-builtin": False, "display-name": 'LG Electronics 22"'}),
        (("eDP-1", "CSW", "MNG007DA5-5", "0x00000000"),
         [_mode(2560, 1600, True, ECHELLE), _mode(2560, 1440, False, 1.4953)],
         {"is-builtin": True, "display-name": "Affichage intégré"}),
    ],
    [
        (0, 1080, ECHELLE, 0, True, [("eDP-1", "CSW", "MNG007DA5-5", "0x00000000")], {}),
        (0, 0, 1.0, 0, False, [("HDMI-1", "GSM", "LG FULL HD", "0x00000000")], {}),
    ],
    {"layout-mode": 1, "supports-changing-layout-mode": True},
)


@pytest.fixture
def liste():
    return ecrans.depuis_etat(ETAT_MESURE)


def _ids(choix):
    return [e["id"] for e in choix.cibles]


# ------------------------------------------------------------------ lecture de l'état de GNOME

def test_disposition_mesuree(liste):
    lg, portable = liste  # du haut vers le bas
    assert lg == {"id": "HDMI-1", "nom": "LG FULL HD", "w": 1920, "h": 1080, "x": 0, "y": 0,
                  "principal": False, "integre": False, "echelle": 1.0, "pw": 1920, "ph": 1080}
    # Taille LOGIQUE (2560/1,5023 = 1704) et pixels réels gardés à part : le portail peut rendre l'un ou l'autre.
    assert portable == {"id": "eDP-1", "nom": "Affichage intégré", "w": 1704, "h": 1065, "x": 0, "y": 1080,
                        "principal": True, "integre": True, "echelle": 1.5, "pw": 2560, "ph": 1600}


def test_mode_physique_rotation_et_ecran_eteint():
    etat = (
        2,
        [
            (("DP-1", "DEL", "DELL U2720Q", "0x1"), [_mode(3840, 2160, True, 2.0)], {"display-name": "Dell 27"}),
            (("DP-2", "XXX", "0x0000", "0x2"), [_mode(1920, 1080, True)], {"display-name": "Écran externe"}),
            (("DP-3", "AAA", "Éteint", "0x3"), [_mode(1920, 1080, False)], {}),
        ],
        [
            (0, 0, 2.0, 0, True, [("DP-1", "DEL", "DELL U2720Q", "0x1")], {}),
            (3840, 0, 1.0, 1, False, [("DP-2", "XXX", "0x0000", "0x2")], {}),  # tourné de 90°
        ],
        {"layout-mode": 2},  # physique : pas de division par l'échelle
    )
    dell, tourne = ecrans.depuis_etat(etat)
    assert (dell["w"], dell["h"], dell["nom"]) == (3840, 2160, "DELL U2720Q")
    assert (tourne["w"], tourne["h"], tourne["pw"], tourne["ph"]) == (1080, 1920, 1080, 1920)
    assert tourne["nom"] == "Écran externe"  # produit illisible (« 0x0000 ») : nom donné par GNOME
    assert [e["id"] for e in ecrans.depuis_etat(etat)] == ["DP-1", "DP-2"]  # DP-3 éteint : absent


def test_lister_vide_hors_linux_et_en_panne(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert ecrans.lister() == []
    monkeypatch.setattr(sys, "platform", "linux")
    from gi.repository import Gio

    def panne(*_a, **_k):
        raise RuntimeError("pas de session D-Bus")

    monkeypatch.setattr(Gio, "bus_get_sync", panne)
    assert ecrans.lister() == []


# ------------------------------------------------------------------ choix de l'écran

@pytest.mark.parametrize("demande, voulu", [
    ("", ["eDP-1"]), ("auto", ["eDP-1"]), ("principal", ["eDP-1"]),
    ("HDMI-1", ["HDMI-1"]), ("hdmi1", ["HDMI-1"]), ("eDP-1", ["eDP-1"]),
    ("LG", ["HDMI-1"]), ("l'écran LG", ["HDMI-1"]), ("LG FULL HD", ["HDMI-1"]),
    ("portable", ["eDP-1"]), ("Affichage intégré", ["eDP-1"]), ("externe", ["HDMI-1"]),
    ("en haut", ["HDMI-1"]), ("l'écran du haut", ["HDMI-1"]), ("tout en haut", ["HDMI-1"]), ("en bas", ["eDP-1"]),
])
def test_choisir_par_nom(liste, demande, voulu):
    choix = ecrans.choisir(liste, demande)
    assert _ids(choix) == voulu and not choix.tous and choix.remarques == []


@pytest.mark.parametrize("demande", ["tous", "Tous les écrans", "les deux", "all screens", "both"])
def test_choisir_tous(liste, demande):
    choix = ecrans.choisir(liste, demande)
    assert choix.tous and _ids(choix) == ["HDMI-1", "eDP-1"]


@pytest.mark.parametrize("demande, voulu", [
    ("hdmi", ["HDMI-1"]), ("HDMI", ["HDMI-1"]), ("edp", ["eDP-1"]), ("l'écran hdmi", ["HDMI-1"]),
])
def test_choisir_par_prefixe_de_connecteur(liste, demande, voulu):
    """Fiche 2 du testeur : « hdmi » donnait « introuvable (débranché ?) » et le portable."""
    choix = ecrans.choisir(liste, demande)
    assert _ids(choix) == voulu and choix.remarques == []


def test_prefixe_de_connecteur_ambigu_et_strict(liste):
    lg = liste[0]
    deux_dp = [dict(lg, id="DP-1", nom="Dell"), dict(lg, id="DP-2", nom="Asus", x=1920), liste[1]]
    choix = ecrans.choisir(deux_dp, "dp")
    assert _ids(choix) == ["DP-1"] and "DP-2" in choix.remarques[0]  # « dp » ne vise pas eDP-1
    assert _ids(ecrans.choisir([dict(lg, id="HDMI-A-1")], "hdmi")) == ["HDMI-A-1"]
    seul_10 = [dict(lg, id="HDMI-10", nom="Autre"), liste[1]]
    assert "introuvable" in ecrans.choisir(seul_10, "hdmi1").remarques[0]  # « hdmi1 » n'est pas HDMI-10


@pytest.mark.parametrize("demande", ["portable et LG", "le LG et le portable", "HDMI-1, eDP-1", "edp + hdmi",
                                     "l'écran du haut et celui du bas", "LG and laptop"])
def test_deux_ecrans_cites_montres_ensemble(liste, demande):
    """Fiche 2 du testeur : « portable et LG » ne montrait que le portable, sans rien dire. Les deux
    cités = les deux écrans branchés : traité comme « tous » (assemblés, ordre du bureau)."""
    choix = ecrans.choisir(liste, demande)
    assert choix.tous and _ids(choix) == ["HDMI-1", "eDP-1"] and choix.remarques == []


def test_ecrans_cites_dont_un_absent(liste):
    choix = ecrans.choisir(liste, "LG et Samsung")
    assert _ids(choix) == ["HDMI-1"] and not choix.tous
    assert "« Samsung » introuvable" in choix.remarques[0]
    choix = ecrans.choisir(liste, "Samsung et Dell")  # aucun : repli habituel sur le principal
    assert _ids(choix) == ["eDP-1"] and "Samsung et Dell" in choix.remarques[0] and "principal" in choix.remarques[0]
    assert _ids(ecrans.choisir(liste, "LG et HDMI-1")) == ["HDMI-1"]  # le même écran deux fois
    trois = liste + [dict(liste[0], id="DP-1", nom="Dell", x=1920)]
    choix = ecrans.choisir(trois, "LG et Dell")  # deux sur trois : ceux-là seulement, assemblés
    assert choix.tous and _ids(choix) == ["HDMI-1", "DP-1"]


def test_nom_qui_contient_et_reste_un_seul_ecran(liste):
    nomme = [dict(liste[0], nom="Noir et Blanc"), liste[1]]
    choix = ecrans.choisir(nomme, "Noir et Blanc")
    assert _ids(choix) == ["HDMI-1"] and not choix.tous and choix.remarques == []


def test_assembler_ecran_sans_mode_erreur_propre(liste):
    """Fiche 2 du testeur : un écran sans mode en cours (w = h = 0) faisait planter cv2.resize."""
    import cv2
    import numpy as np

    from astrel.platform import linux_capture

    ok, buf = cv2.imencode(".png", np.zeros((10, 16, 3), np.uint8))
    vide = dict(liste[0], w=0, h=0, pw=0, ph=0)
    with pytest.raises(RuntimeError, match=r"sans mode en cours.*LG FULL HD \(HDMI-1\)"):
        linux_capture.assembler([(buf.tobytes(), vide), (buf.tobytes(), liste[1])])


def test_ecran_debranche_repli_sur_le_principal(liste):
    """Plan P24 : écran absent (branchement à chaud) = repli sur le principal, avec une phrase."""
    choix = ecrans.choisir(liste, "Samsung")
    assert _ids(choix) == ["eDP-1"]
    assert "Samsung" in choix.remarques[0] and "principal" in choix.remarques[0]
    seul = [e for e in liste if e["id"] == "eDP-1"]  # LG débranché, réglage resté sur lui
    choix = ecrans.choisir(seul, "HDMI-1")
    assert _ids(choix) == ["eDP-1"] and "introuvable" in choix.remarques[0]


def test_deux_ecrans_au_meme_nom_le_disent(liste):
    jumeaux = [dict(liste[0]), dict(liste[0], id="HDMI-2", x=1920)]
    choix = ecrans.choisir(jumeaux, "LG")
    assert _ids(choix) == ["HDMI-1"] and "HDMI-2" in choix.remarques[0]


def test_sans_liste_rien_a_choisir():
    assert ecrans.choisir([], "LG") == ecrans.Choix([], False, [])
    assert ecrans.choisir([], "tous").tous


def test_positions(liste):
    lg, portable = liste
    assert ecrans.position(lg, liste) == "en haut" and ecrans.position(portable, liste) == "en bas"
    assert ecrans.position(lg, [lg]) == ""
    ligne = [dict(lg, id=f"D{i}", x=1920 * i) for i in range(3)]
    assert [ecrans.position(e, ligne) for e in ligne] == ["à gauche", "au milieu", "à droite"]


# ------------------------------------------------------------------ flux du portail ↔ écrans

def test_flux_par_position(liste):
    flux = [{"node": 51, "position": (0, 1080), "size": (1704, 1065)}, {"node": 52, "position": (0, 0)}]
    assert ecrans.associer_flux(flux, liste) == {"eDP-1": 0, "HDMI-1": 1}


def test_flux_par_taille_logique_ou_pixels(liste):
    """Échelle 1,5 : le portail peut donner la taille logique (1704x1065) ou les pixels (2560x1600)."""
    assert ecrans.associer_flux([{"node": 1, "size": (1704, 1065)}], liste) == {"eDP-1": 0}
    assert ecrans.associer_flux([{"node": 1, "size": (2560, 1600)}], liste) == {"eDP-1": 0}
    assert ecrans.associer_flux([{"node": 1}, {"node": 2, "pixels": (1920, 1080)}], liste) == {"HDMI-1": 1}
    assert ecrans.associer_flux([{"node": 1, "size": (1280, 800)}], liste) == {"eDP-1": 0}  # même rapport 16:10


def test_flux_ambigu_non_attribue(liste):
    jumeaux = [dict(liste[0]), dict(liste[0], id="HDMI-2", x=1920)]
    assert ecrans.associer_flux([{"node": 1, "size": (1920, 1080)}], jumeaux) == {}
    assert ecrans.associer_flux([{"node": 1}], liste) == {}


# ------------------------------------------------------------------ légende, list_screens

def test_legende_un_ecran(liste):
    lg, portable = liste
    texte = ecrans.legende([lg], liste)
    assert texte.startswith("Écran vu : LG FULL HD (HDMI-1, 1920x1080, en haut).")
    assert "Autre écran : Affichage intégré (eDP-1, principal, portable, en bas)" in texte
    assert "screen_capture" in texte and "\n" not in texte


def test_legende_tous_et_remarques(liste):
    texte = ecrans.legende(liste, liste, ["Non partagé : X."])
    assert texte.startswith("Écrans vus, assemblés comme sur le bureau : LG FULL HD")
    assert "Autre" not in texte and texte.endswith("Non partagé : X.")
    seul = liste[1:]
    assert ecrans.legende(seul, seul) == "Écran vu : Affichage intégré (eDP-1, 2560x1600, portable)."


def test_resume(liste):
    r = ecrans.resume(liste, "LG")
    assert [e["id"] for e in r["ecrans"]] == ["HDMI-1", "eDP-1"]
    assert r["ecrans"][0]["position"] == "en haut" and r["regarde_par_defaut"] == "HDMI-1"
    assert "Plusieurs écrans" in r["conseil"]
    assert ecrans.resume(liste)["regarde_par_defaut"] == "eDP-1"
    assert "illisible" in ecrans.resume([])["conseil"] and ecrans.resume([])["regarde_par_defaut"] == ""


def test_ecran_par_defaut(monkeypatch):
    from astrel import settings

    monkeypatch.delenv(ecrans.ENV, raising=False)
    if ecrans.REGLAGE not in settings.ITEMS:  # lot 2 pas encore là : la variable d'environnement seule
        assert ecrans.ecran_par_defaut() == ""
        monkeypatch.setenv(ecrans.ENV, "HDMI-1")
        assert ecrans.ecran_par_defaut() == "HDMI-1"
        monkeypatch.setenv(ecrans.ENV, "auto")
        assert ecrans.ecran_par_defaut() == ""
    monkeypatch.setitem(settings.ITEMS, ecrans.REGLAGE, object())
    monkeypatch.setattr(settings.reglages, "value", lambda key: {"ecran_defaut": "LG"}[key])
    assert ecrans.ecran_par_defaut() == "LG"
    monkeypatch.setattr(settings.reglages, "value", lambda key: "auto")
    assert ecrans.ecran_par_defaut() == ""


# ------------------------------------------------------------------ outil MCP

class _Proc:
    def __init__(self, out="", err="", code=0):
        self.stdout, self.stderr, self.returncode = out, err, code


@pytest.fixture
def mcp(monkeypatch, tmp_path):
    from astrel import mcp_server

    png = tmp_path / "astrel-ecran-test.png"
    png.write_bytes(b"png")
    appels = []

    def run(cmd, **kw):
        appels.append(cmd)
        return mcp_server_reponse["proc"]

    mcp_server_reponse = {"proc": _Proc(f"{png}\nÉcran vu : LG FULL HD (HDMI-1, 1920x1080, en haut).\n")}
    monkeypatch.setattr(mcp_server.subprocess, "run", run)
    monkeypatch.setattr(mcp_server.camera, "shrink_image", lambda path: b"\xff\xd8jpeg")
    return mcp_server, appels, mcp_server_reponse, png


def test_screen_capture_rend_image_et_legende(mcp):
    mcp_server, appels, _r, png = mcp
    res = asyncio.run(mcp_server.server.call_tool("screen_capture", {"ecran": "LG"}))
    assert [c.type for c in res.content] == ["image", "text"]
    assert res.content[1].text == "Écran vu : LG FULL HD (HDMI-1, 1920x1080, en haut)."
    assert appels[0][-1] == "--ecran=LG" and appels[0][-3:-1] == ["-m", "astrel.tools.screenshot"]
    assert not png.exists()  # le PNG temporaire est effacé
    asyncio.run(mcp_server.server.call_tool("screen_capture", {}))
    assert appels[1][-1] == "astrel.tools.screenshot"  # sans écran : pas d'option, défaut du réglage


def test_screen_capture_nom_bizarre_reste_une_valeur(mcp):
    mcp_server, appels, _r, _png = mcp
    asyncio.run(mcp_server.server.call_tool("screen_capture", {"ecran": "--autoriser\n" + "x" * 200}))
    arg = appels[0][-1]
    assert arg.startswith("--ecran=--autoriser x") and len(arg) == len("--ecran=") + mcp_server.ECRAN_MAX
    assert "--autoriser" not in appels[0][:-1]


def test_screen_capture_erreur_lisible(mcp):
    from mcp.server.mcpserver.exceptions import ToolError

    mcp_server, _a, reponse, _png = mcp
    reponse["proc"] = _Proc(err="ERREUR: l'écran LG FULL HD (HDMI-1) n'est pas couvert", code=1)
    with pytest.raises(ToolError, match="HDMI-1"):
        asyncio.run(mcp_server.server.call_tool("screen_capture", {"ecran": "LG"}))


def test_screen_capture_ancien_format_sans_legende(mcp):
    mcp_server, _a, reponse, png = mcp
    reponse["proc"] = _Proc(f"{png}\n")
    res = asyncio.run(mcp_server.server.call_tool("screen_capture", {}))
    assert res.content[1].text == "Écran vu : non précisé."


def test_list_screens(monkeypatch, liste):
    from astrel import mcp_server

    monkeypatch.setattr(ecrans, "lister", lambda: liste)
    monkeypatch.setattr(ecrans, "ecran_par_defaut", lambda: "")
    res = asyncio.run(mcp_server.server.call_tool("list_screens", {}))
    texte = res.content[0].text
    assert '"HDMI-1"' in texte and '"regarde_par_defaut": "eDP-1"' in texte and "en haut" in texte


def test_outils_documentent_plusieurs_ecrans():
    from astrel import mcp_server

    tools = {t.name: t for t in asyncio.run(mcp_server.server.list_tools())}
    assert "Plusieurs écrans" in tools["screen_capture"].description
    assert "tous" in tools["screen_capture"].description
    assert "ecran" in tools["screen_capture"].input_schema["properties"]
    assert tools["list_screens"].input_schema.get("properties", {}) == {}


# ------------------------------------------------------------------ WATCH

def test_watch_lit_la_premiere_ligne_et_la_legende(monkeypatch, tmp_path):
    from astrel.senses import watch

    png = tmp_path / "astrel-ecran-watch.png"
    png.write_bytes(b"png")
    appels = []
    monkeypatch.setattr(watch.subprocess, "run",
                        lambda cmd, **kw: appels.append(cmd) or _Proc(f"{png}\nÉcran vu : Affichage intégré.\n"))
    monkeypatch.setattr(watch, "shrink_image", lambda path, max_width=1600: b"jpeg")
    image, legende = watch.capture_avec_legende()
    assert image["media_type"] == "image/jpeg" and image["data"] == "anBlZw=="
    assert legende == "Écran vu : Affichage intégré." and not png.exists()
    assert appels[0][-1] == "astrel.tools.screenshot"  # écran du réglage résolu par screenshot lui-même
    png.write_bytes(b"png")
    assert watch.capture_for_prompt()["data"] == "anBlZw=="  # contrat lu par core.py inchangé
    png.write_bytes(b"png")
    watch.capture_avec_legende("HDMI-1")
    assert appels[-1][-1] == "--ecran=HDMI-1"


def test_watch_en_echec_rend_none(monkeypatch):
    from astrel.senses import watch

    monkeypatch.setattr(watch.subprocess, "run", lambda cmd, **kw: _Proc(err="ERREUR: refus", code=1))
    assert watch.capture_avec_legende() == (None, "")
    assert watch.capture_for_prompt() is None

    def lent(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(watch.subprocess, "run", lent)
    assert watch.capture_avec_legende() == (None, "")
