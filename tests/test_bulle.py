"""Bulle (18/10) : ramenée dans la zone visible (écran débranché, géométrie changée),
et réponse de la carte de confirmation refusée ou non reçue : jamais jetée en silence.

`astrel.bubble` importe GTK : il est chargé dans un processus à part, sans écran
(DISPLAY retiré), pour ne pas ouvrir de connexion X dans la suite de tests."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CODE = r"""
import json, sys
from astrel.bubble import visible_position as v, answer_refusal as refus
FHD = (0, 0, 1920, 1080)
EXT = (1920, 0, 2560, 1440)  # second écran à droite
HOME = (1732, 852)
print(json.dumps({
    "sur_ecran": v(100, 200, 148, [FHD], HOME),
    "ecran_debranche": v(3000, 400, 148, [FHD], HOME),
    "debord_a_droite": v(1800, 300, 148, [FHD], HOME),
    "debord_en_bas": v(500, 1000, 148, [FHD], HOME),
    "debord_haut_gauche": v(-60, -50, 148, [FHD], HOME),
    "second_ecran": v(3000, 400, 148, [FHD, EXT], HOME),
    "compacte": v(1890, 1050, 52, [FHD], HOME),
    "aucun_ecran": v(100, 100, 148, [], HOME),
    "refus_ok": refus('{"ok": true}'),
    "refus_injoignable": refus(None),
    "refus_vide": refus(""),
    "refus_illisible": refus("pas du json"),
    "refus_close": refus('{"ok": false, "error": "aucune confirmation en attente avec cet identifiant"}'),
    "refus_claude": refus('{"ok": false, "error": "commande réservée à l\'utilisateur (HUD ou terminal), refusée à Claude"}'),
}))
"""


def _run() -> dict:
    env = {k: val for k, val in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    env["XDG_CURRENT_DESKTOP"] = ""  # pas d'attente de GNOME
    env["PYTHONPATH"] = str(ROOT)
    out = subprocess.run([sys.executable, "-c", CODE], cwd=str(ROOT), env=env, capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_bulle_ramenee_dans_la_zone_visible():
    r = _run()
    assert r["sur_ecran"] == [100, 200]                # déjà visible : ne bouge pas
    assert r["ecran_debranche"] == [1732, 852]         # écran disparu : place par défaut
    assert r["debord_a_droite"] == [1920 - 148, 300]   # centre visible : remise entière
    assert r["debord_en_bas"] == [500, 1080 - 148]
    assert r["debord_haut_gauche"] == [0, 0]
    assert r["second_ecran"] == [3000, 400]            # l'écran est là : rien à faire
    assert r["compacte"] == [1920 - 52, 1080 - 52]     # taille réduite prise en compte
    assert r["aucun_ecran"] is None                    # géométrie inconnue : on ne bouge pas


def test_reponse_de_la_carte_refusee_ou_perdue_a_une_raison():
    r = _run()
    assert r["refus_ok"] == ""                                   # prise : rien à montrer
    assert r["refus_injoignable"].startswith("Astrel ne répond pas")
    assert r["refus_vide"].startswith("Astrel ne répond pas")
    assert r["refus_illisible"].startswith("réponse illisible")
    # Question close (délai, réponse ailleurs) : la bulle s'appuie sur ce début de phrase.
    assert r["refus_close"].startswith("aucune confirmation en attente")
    assert "relance-la depuis ta session" in r["refus_claude"]   # bulle lancée par Astrel


# 19/10 (carte noire) : chaque affichage de la carte, après un masquage, doit se faire dans
# une fenêtre X NEUVE ; la même tant qu'elle reste à l'écran. Joué dans un serveur X virtuel
# (Xvfb) : rien ne s'affiche sur le bureau de l'utilisateur.
CARTE = r"""
import json
from astrel import bubble as b
from astrel.minichat import MiniChat
card = b.ConfirmCard(MiniChat())
card.chat.answer_command = lambda *a, **k: None  # rien vers un vrai Astrel
ev = {"id": "a", "question": "Je continue ?", "cmd": "ls", "grantable": True}
xid = lambda: card.get_window().get_xid()
def pump():
    while b.Gtk.events_pending():
        b.Gtk.main_iteration_do(False)
card.show_for(ev, (300, 300, 148)); pump(); premier = xid()
card.show_for(dict(ev, id="b", cmd="ls -la"), (300, 300, 148)); pump(); encore_visible = xid()
card.hide(); pump()
card.show_for(dict(ev, id="c"), (300, 300, 148)); pump(); apres_masquage = xid()
texte = card.question.get_text()
print(json.dumps({"meme_si_visible": premier == encore_visible, "neuve_apres_masquage": apres_masquage != premier,
                  "visible": card.get_visible(), "texte": texte, "classe": card.get_style_context().has_class("astrel-card")}))
"""


def test_carte_dans_une_fenetre_x_neuve_a_chaque_affichage():
    import shutil

    import pytest

    if not shutil.which("xvfb-run"):
        pytest.skip("xvfb-run absent")
    env = {k: val for k, val in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "GDK_SCALE")}
    env["XDG_CURRENT_DESKTOP"] = ""
    env["PYTHONPATH"] = str(ROOT)
    out = subprocess.run(["xvfb-run", "-a", sys.executable, "-c", CARTE], cwd=str(ROOT), env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert r["meme_si_visible"]          # nouvelle question, carte déjà affichée : même fenêtre
    assert r["neuve_apres_masquage"]     # réaffichée : fenêtre X neuve (gel Mutter laissé derrière)
    assert r["visible"] and r["texte"] == "Je continue ?" and r["classe"]


# 23/10 (plan P31, demande de l'utilisateur) : nom affiché « Astrel » par défaut, configurable par le réglage
# `assistant_name` (même source que le HUD), relu À CHAUD par la bulle ; posé par `tr()` dans les textes
# connus du catalogue (menu, invites, erreurs de la carte), jamais dans l'ancien mot d'éveil ni un identifiant.
NOM = r"""
import json, sys, tempfile, pathlib, os, time
from astrel import config
reg = pathlib.Path(tempfile.mkdtemp()) / "settings.json"
config.ASTREL_SETTINGS_FILE = reg
from astrel import bubble as b
from astrel.i18n import nom
def poser(valeur):
    reg.write_text(json.dumps({"version": 1, "values": {"assistant_name": valeur}}), encoding="utf-8")
    os.utime(reg, ns=(time.time_ns(), time.time_ns()))
tr = b.tr
out = {"defaut": b.assistant_name(), "menu": tr("Éteindre Astrel (libère la mémoire)"),
       "carte_injoignable": tr(b.answer_refusal(None)), "carte_refus": tr(b.answer_refusal('{"ok": false}')),
       "invite": tr("Écris à Astrel… (Entrée ; Ctrl+V colle une image)"),
       "inconnu": tr("Astrel : texte de Claude inconnu du catalogue"),
       "eveil": nom("tu peux enchaîner, sans « Hey Astrel »"), "ok": nom("dis « OK Astrel »"),
       "ids": nom("systemctl --user restart Astrel.service ; /data/Studio/Astrel/run.sh ; ASTREL_THEME"),
       "fin": nom("Parle à Astrel.")}
poser("Nova")
out["chaud"] = b.assistant_name()
out["chaud_menu"] = tr("Éteindre Astrel (libère la mémoire)")
poser("<b>Vega</b>")
out["chevrons"] = b.assistant_name()
poser("   ")
out["vide"] = b.assistant_name()
print(json.dumps(out, ensure_ascii=False))
"""


def test_nom_affiche_astrel_configurable():
    env = {k: val for k, val in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "ASTREL_ASSISTANT_NAME")}
    env["XDG_CURRENT_DESKTOP"] = ""
    env["PYTHONPATH"] = str(ROOT)
    env["ASTREL_UI_LANGUAGE"] = "fr"
    out = subprocess.run([sys.executable, "-c", NOM], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert r["defaut"] == "Astrel"
    assert r["menu"] == "Éteindre Astrel (libère la mémoire)"
    assert r["carte_injoignable"].startswith("Astrel ne répond pas")      # erreur de la carte (bubble.py:395)
    assert r["carte_refus"] == "réponse refusée par Astrel"
    assert r["invite"].startswith("Écris à Astrel…")
    assert r["inconnu"].startswith("Astrel :")                            # texte inconnu : jamais touché
    assert r["eveil"].endswith("« Hey Astrel »") and r["ok"] == "dis « OK Astrel »"  # P36 : le mot d'éveil suit le nom
    assert r["ids"] == "systemctl --user restart Astrel.service ; /data/Studio/Astrel/run.sh ; ASTREL_THEME"
    assert r["fin"] == "Parle à Astrel."
    assert r["chaud"] == "Nova" and r["chaud_menu"] == "Éteindre Nova (libère la mémoire)"   # à chaud
    assert r["chevrons"] == "Vega" and r["vide"] == "Astrel"


SEULE = r"""
import json, subprocess, sys
from pathlib import Path
from astrel import bubble, config
config.RUNTIME = Path(sys.argv[1])
bubble.LOCK_FILE = config.RUNTIME / "bubble.lock"
autre = subprocess.Popen([sys.executable, "-c",
    "import fcntl, os, sys, time; fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600); "
    "fcntl.flock(fd, fcntl.LOCK_EX); print('pris', flush=True); time.sleep(60)", str(bubble.LOCK_FILE)],
    stdout=subprocess.PIPE, text=True)
autre.stdout.readline()
pendant = bubble.seule_bulle()
code = bubble.main()  # une autre bulle tient le verrou : sort tout de suite, code 0, aucune fenêtre
autre.kill(); autre.wait()
apres = bubble.seule_bulle()
print(json.dumps({"pendant": pendant, "code": code, "apres": apres is not None}))
"""


def test_une_seule_bulle(tmp_path):
    """29/10 : le service systemd ET l'autostart GNOME avaient ouvert deux bulles : la seconde sort
    (code 0, pas relancée par systemd) ; le verrou est rendu dès que la première meurt."""
    env = {k: val for k, val in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    env["XDG_CURRENT_DESKTOP"] = ""
    env["PYTHONPATH"] = str(ROOT)
    out = subprocess.run([sys.executable, "-c", SEULE, str(tmp_path)], cwd=str(ROOT), env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    assert json.loads(out.stdout.strip().splitlines()[-1]) == {"pendant": None, "code": 0, "apres": True}
    assert "une bulle tourne déjà" in out.stderr
