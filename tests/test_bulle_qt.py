"""Bulle Qt de Windows et macOS (03/11/2026, portage, `astrel/bulle_qt.py`) et sa partie sans interface
(`astrel/bulle_commun.py`). La bulle Qt est essayée hors écran (QT_QPA_PLATFORM=offscreen) là où
PySide6 est installé : machines Windows et macOS de la CI (requirements.txt)."""
from __future__ import annotations

import json
import os
import time

import pytest

from astrel import bulle_commun, state


# ------------------------------------------------------------------ partie commune (partout)

def test_place_visible():
    ecrans = [(0, 0, 1920, 1080), (1920, 0, 1280, 1024)]
    assert bulle_commun.visible_position(1800, 1000, 148, ecrans, (10, 10)) == (1772, 932)
    assert bulle_commun.visible_position(5000, 5000, 148, ecrans, (10, 10)) == (10, 10)
    assert bulle_commun.visible_position(5, 5, 148, [], (10, 10)) is None


def test_reponse_du_coeur_a_la_carte():
    assert bulle_commun.answer_refusal(json.dumps({"ok": True})) == ""
    assert "ne répond pas" in bulle_commun.answer_refusal(None)
    assert "lancée par Astrel" in bulle_commun.answer_refusal(json.dumps({"ok": False, "error": "refusée à Claude"}))


def test_une_seule_bulle(tmp_path, monkeypatch):
    monkeypatch.setattr(bulle_commun, "LOCK_FILE", tmp_path / "bubble.lock")
    monkeypatch.setattr(bulle_commun.config, "RUNTIME", tmp_path)
    fd = bulle_commun.seule_bulle()
    assert fd is not None
    try:
        import subprocess
        import sys
        code = ("import sys; sys.path.insert(0, %r); from astrel import bulle_commun; from pathlib import Path; "
                "bulle_commun.LOCK_FILE = Path(%r); print(bulle_commun.seule_bulle())") % (
            str(bulle_commun.config.ROOT), str(tmp_path / "bubble.lock"))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30).stdout
        assert out.strip() == "None"  # une autre bulle le tient : celle-ci ne s'ouvre pas
    finally:
        os.close(fd)


# ------------------------------------------------------------------ bulle Qt (là où PySide6 est installé)

@pytest.fixture
def qt(monkeypatch):
    pytest.importorskip("PySide6")
    pytest.importorskip("cairo")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(bulle_commun.config, "HUD_PORT", 1)  # aucun Astrel : le flux lit state.json
    from PySide6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _attendre(app, s: float) -> None:
    fin = time.monotonic() + s
    while time.monotonic() < fin:
        app.processEvents()
        time.sleep(0.01)


def test_dessin_identique_a_la_bulle_linux(qt):
    import cairo
    from PySide6 import QtGui

    from astrel import bubble_premium, bulle_qt
    lk = bubble_premium.look("dark", "bleu")
    ref = cairo.ImageSurface(cairo.FORMAT_ARGB32, 148, 148)
    bubble_premium.draw(cairo.Context(ref), 148, 148, layers=[(state.SPEAKING, 1.0)], t=1.0, level=0.5, lk=lk)
    ref.flush()
    a = QtGui.QImage(bytes(ref.get_data()), 148, 148, ref.get_stride(), QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    b = bulle_qt.peindre(148, 148, 1.0, [(state.SPEAKING, 1.0)], 1.0, 0.5, lk)
    assert a.size() == b.size()
    assert all(a.pixel(x, y) == b.pixel(x, y) for y in range(0, 148, 3) for x in range(0, 148, 3))


def test_bulle_carte_et_mini_chat(qt, monkeypatch, tmp_path):
    from PySide6 import QtCore

    from astrel import bulle_qt
    monkeypatch.setattr(bulle_commun, "POS_FILE", tmp_path / "bubble.json")
    monkeypatch.setattr(bulle_qt, "lire_position", lambda: (50, 60))
    envois = []
    monkeypatch.setattr(bulle_qt, "_envoyer", lambda payload, fini=None: envois.append(payload))
    b = bulle_qt.Bulle()
    b.show()
    b.flux.state = state.SPEAKING
    _attendre(qt, 0.5)
    img = b.grab().toImage()
    assert img.pixelColor(img.width() // 2, img.height() // 2).alpha() > 0 and img.pixelColor(1, 1).alpha() == 0
    # question : carte À CÔTÉ, sans jamais prendre le clavier
    b.flux.chat.apply({"type": "confirm", "pending": True, "id": "c1", "question": "Supprimer ?", "cmd": "rm x",
                       "grantable": True})
    b._rafraichir()
    assert b._carte.isVisible() and b._carte.toujours.isVisible()
    assert b._carte.windowFlags() & QtCore.Qt.WindowType.WindowDoesNotAcceptFocus
    # mini-chat : s'ouvre et se ferme au clic
    b._chat.basculer(b._ancre())
    assert b._chat.isVisible()
    b._chat.saisie.setText("bonjour")
    b._chat._envoyer()
    assert envois and envois[-1]["cmd"] == "say" and envois[-1]["text"] == "bonjour"
    b._chat.basculer(b._ancre())
    assert not b._chat.isVisible()
    # réduire / agrandir
    b._compacte()
    assert b.width() == bulle_commun.COMPACT
    b.close()


def test_feuille_de_style_suit_le_theme(qt):
    from astrel import bubble_premium, bulle_qt
    sombre, clair = (bulle_qt.feuille_de_style(bubble_premium.look(m, "bleu")) for m in ("dark", "light"))
    assert sombre != clair and "QWidget#carte" in sombre
