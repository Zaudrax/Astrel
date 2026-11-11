"""Capacité de pentest (09/11/2026, branche cyber-local).

Vérifie : les motifs vocaux « pentest/teste/scan <cible> » ; le loader du périmètre (IP, CIDR,
hôte, URL) ; la commande dans la table ; le refus net d'une cible HORS périmètre (rien n'est
lancé) ; le refus à Claude ; les coupures (« rien ne sort », arrêt d'urgence) ; et le lancement
en tâche de fond qui réutilise le lanceur `cyberlab/pentest.sh`.
"""
from __future__ import annotations

import threading

import pytest

from astrel import config, core, cyber
from astrel.coeur import cyber as coeur_cyber
from astrel.events import bus


# ------------------------------------------------------------------ motifs vocaux

@pytest.mark.parametrize("phrase, cible, outils", [
    ("pentest 1.2.3.4", "1.2.3.4", False),
    ("teste https://x.fr", "https://x.fr", False),
    ("scan 192.168.1.10", "192.168.1.10", False),
    ("Astrel, pentest 10.0.0.5", "10.0.0.5", False),
    ("teste la cible 10.0.0.5", "10.0.0.5", False),
    ("pentest 10.0.0.5 avec les outils", "10.0.0.5", True),
    ("scan https://site.fr/admin with tools", "https://site.fr/admin", True),
    ("test example.com", "example.com", False),
])
def test_motifs_vocaux(phrase, cible, outils):
    assert cyber.commande(phrase) == (cible, outils)


@pytest.mark.parametrize("phrase", [
    "teste cette fonction",          # pas de cible plausible : repart vers Claude
    "scanne la pièce",
    "pentest",                        # sans cible
    "raconte-moi une blague",
    "teste mon idée",
])
def test_phrases_sans_cible_ne_declenchent_rien(phrase):
    assert cyber.commande(phrase) is None


# ------------------------------------------------------------------ loader du périmètre

@pytest.fixture
def cyberlab(tmp_path, monkeypatch):
    """Un faux dossier cyberlab (périmètre + lanceur) ; `ASTREL_CYBERLAB` y pointe."""
    d = tmp_path / "cyberlab"
    (d / "resultats").mkdir(parents=True)
    (d / "perimetre.txt").write_text(
        "127.0.0.1\n10.0.0.0/24   # lab\nmon-site.fr\n# commentaire\n", encoding="utf-8")
    (d / "pentest.sh").write_text("#!/bin/bash\necho ok\n", encoding="utf-8")
    monkeypatch.setenv("ASTREL_CYBERLAB", str(d))
    return d


@pytest.mark.parametrize("cible, autorisee, host", [
    ("127.0.0.1", True, "127.0.0.1"),
    ("http://127.0.0.1/x", True, "127.0.0.1"),
    ("10.0.0.5", True, "10.0.0.5"),               # dans le CIDR
    ("10.0.1.5", False, "10.0.1.5"),              # hors du CIDR
    ("mon-site.fr", True, "mon-site.fr"),
    ("https://MON-SITE.fr/page", True, "mon-site.fr"),  # casse ignorée pour l'hôte
    ("8.8.8.8", False, "8.8.8.8"),
    ("autre.fr", False, "autre.fr"),
])
def test_perimetre(cyberlab, cible, autorisee, host):
    h, ok = cyber.verifier_cible(cible)
    assert (h, ok) == (host, autorisee)


def test_lire_resume():
    sortie = ("✅ Cible autorisée : 10.0.0.5\n"
              '{"modele":"qwen3.5:9b","score":2,"sur":5,"flags":{"FLAG{a}":3},"duree_s":1.0}\n')
    r = cyber.lire_resume(sortie)
    assert r is not None and r["score"] == 2 and r["sur"] == 5 and "FLAG{a}" in r["flags"]
    assert cyber.lire_resume("rien de json ici") is None


# ------------------------------------------------------------------ la commande

def test_commande_dans_la_table():
    table = core.Astrel._COMMANDES
    assert table.get("pentest") == "_cmd_pentest" and table.get("teste_cible") == "_cmd_pentest"
    assert callable(getattr(core.Astrel, "_cmd_pentest", None))


def test_refuse_a_claude():
    j = object.__new__(core.Astrel)
    j._off = False
    r = j._on_command({"cmd": "pentest", "cible": "10.0.0.5", "_from_claude": True, "_via": "socket"})
    assert r == {"ok": False, "error": coeur_cyber.REFUS_CLAUDE}


def _bare(monkeypatch, tmp_path):
    """Une instance d'Astrel sans démarrage, avec un canal et une voix factices."""
    j = object.__new__(core.Astrel)
    j._off = False
    j._channel_for_tool = lambda ch, alert=False: ("general", "")
    j._voice_muted = lambda: False
    monkeypatch.setattr(config, "ESTOP_FILE", tmp_path / "ESTOP")  # inexistant
    monkeypatch.setattr(coeur_cyber.hors_ligne, "actif", lambda: False)
    return j


def test_cible_hors_perimetre_ne_lance_rien(cyberlab, monkeypatch, tmp_path):
    j = _bare(monkeypatch, tmp_path)
    lance = []

    def interdit(*a, **k):
        lance.append(a)
        raise AssertionError("un pentest a été lancé hors périmètre")

    monkeypatch.setattr(coeur_cyber.enfants, "popen", interdit)
    r = j._cmd_pentest("pentest", {"cible": "8.8.8.8"})
    assert r["ok"] is False and "hors périmètre" in r["error"] and "8.8.8.8" in r["error"]
    assert lance == []  # AUCUN lancement


def test_hors_ligne_refuse(cyberlab, monkeypatch, tmp_path):
    j = _bare(monkeypatch, tmp_path)
    monkeypatch.setattr(coeur_cyber.hors_ligne, "actif", lambda: True)
    r = j._cmd_pentest("pentest", {"cible": "10.0.0.5"})
    assert r["ok"] is False and "rien ne sort" in r["error"]


def test_arret_d_urgence_refuse(cyberlab, monkeypatch, tmp_path):
    j = _bare(monkeypatch, tmp_path)
    estop = tmp_path / "ESTOP"
    estop.write_text("x", encoding="utf-8")
    monkeypatch.setattr(config, "ESTOP_FILE", estop)
    r = j._cmd_pentest("pentest", {"cible": "10.0.0.5"})
    assert r["ok"] is False and "arrêt d'urgence" in r["error"]


class _FakeProc:
    def __init__(self, out):
        self._out, self.returncode, self._done = out, 0, False

    def communicate(self):
        self._done = True
        return self._out, None

    def poll(self):
        return 0 if self._done else None

    def terminate(self):
        self.returncode = -15


def test_lance_en_tache_de_fond_et_remonte_un_compte_rendu(cyberlab, monkeypatch, tmp_path):
    j = _bare(monkeypatch, tmp_path)
    argvs = []
    somme = '{"modele":"qwen3.5:9b","score":2,"sur":5,"flags":{"FLAG{a}":3},"duree_s":1.0}\n'

    def fake_popen(argv, **kw):
        argvs.append(argv)
        return _FakeProc(somme)

    monkeypatch.setattr(coeur_cyber.enfants, "popen", fake_popen)
    rapport = threading.Event()
    got = []
    stop = bus.subscribe(lambda ev: (got.append(ev), rapport.set()) if ev.get("type") == "report" else None)
    try:
        r = j._cmd_pentest("pentest", {"cible": "10.0.0.5"})
        assert r["ok"] is True and r["cible"] == "10.0.0.5" and r["host"] == "10.0.0.5"
        assert rapport.wait(5.0), "pas de compte rendu publié"
    finally:
        stop()
    # Le lanceur cyberlab/pentest.sh a été appelé avec la cible (sans --outils).
    assert argvs and argvs[0][0].endswith("pentest.sh") and argvs[0][1] == "10.0.0.5" and "--outils" not in argvs[0]
    rep = got[-1]
    assert rep["report"] is True and "10.0.0.5" in rep["text"] and "2/5" in rep["text"]


def test_option_outils_passee_au_lanceur(cyberlab, monkeypatch, tmp_path):
    j = _bare(monkeypatch, tmp_path)
    argvs = []
    monkeypatch.setattr(coeur_cyber.enfants, "popen",
                        lambda argv, **k: argvs.append(argv) or _FakeProc('{"score":0,"sur":5,"flags":{}}\n'))
    fini = threading.Event()
    stop = bus.subscribe(lambda ev: fini.set() if ev.get("type") == "report" else None)
    try:
        j._cmd_pentest("pentest", {"cible": "10.0.0.5", "outils": True})
        assert fini.wait(5.0)
    finally:
        stop()
    assert argvs and "--outils" in argvs[0]


def test_estop_arrete_les_pentests(monkeypatch):
    j = object.__new__(core.Astrel)
    tue = []

    class P:
        def poll(self):
            return None

        def terminate(self):
            tue.append(True)

    j.__dict__["_pentests_map"] = {"a": {"proc": P()}}
    assert j._pentest_estop() == 1 and tue == [True]
    assert j._pentests["a"].get("annule") is True
