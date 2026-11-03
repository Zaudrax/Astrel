"""Preuve de fin de mission sous Windows (03/11/2026, portage, lot 3) : chemins « C:\\… » ramenés à
la forme interne « /C:/… », casse ignorée, noms réservés refusés (simulé ici sous Linux), et
ouverture sûre par l'API Windows (jonction, lien dur, fichier en cours d'écriture : machine Windows
de la CI seulement)."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from astrel import preuve

windows = pytest.mark.skipif(sys.platform != "win32", reason="ouverture réelle : Windows seulement")


@pytest.fixture
def comme_windows(monkeypatch):
    monkeypatch.setattr(preuve, "WINDOWS", True)


# ------------------------------------------------------------------ chemins (simulé partout)

def test_forme_interne(comme_windows):
    assert preuve._canon("C:\\Users\\Ami\\Projets") == "/C:/Users/Ami/Projets"
    assert preuve._canon("d:/x/y") == "/D:/x/y"
    assert preuve._systeme("/C:/Users/Ami") == "C:\\Users\\Ami"
    assert preuve._systeme("/C:") == "C:\\"
    assert preuve.affiche("/C:/Users/Ami") == "C:/Users/Ami"
    assert preuve._parties("C:\\Users\\Ami") == ["C:", "Users", "Ami"]


@pytest.mark.parametrize("chemin, motif", [
    ("\\\\serveur\\partage\\a.txt", "réseau"),
    ("//serveur/partage/a.txt", "réseau"),
    ("C:\\p\\CON", "réservé|refusé"),
    ("C:\\p\\nul.txt", "refusé"),
    ("C:\\p\\COM1", "refusé"),
    ("C:\\p\\a.txt:flux", "refusé"),
    ("C:\\p\\fin.", "refusé"),
    ("C:\\p\\a?b", "refusé"),
    ("C:\\p\\..\\q", "« . » et « .. »"),
    ("p\\a.txt", "absolu"),
])
def test_chemins_refuses(comme_windows, chemin, motif):
    with pytest.raises(preuve.CritereInvalide, match=motif):
        preuve._parties(chemin)


def test_perimetre_sans_casse(comme_windows):
    c = preuve.valider_critere({"controles": [{"type": "fichier_existe", "chemin": "c:\\projets\\Essai\\a.md",
                                               "genre": "fichier"}]}, ["C:\\Projets\\essai"])
    assert c["controles"][0]["chemin"] == "/C:/projets/Essai/a.md"
    with pytest.raises(preuve.CritereInvalide, match="hors du périmètre"):
        preuve.valider_critere({"controles": [{"type": "fichier_existe", "chemin": "C:\\Autre\\a.md",
                                               "genre": "fichier"}]}, ["C:\\Projets\\essai"])


def test_lignes_du_hud_et_commande(comme_windows):
    brut = preuve.lire_lignes("existe a.md\nexiste D:\\x\\b.md\ncommande . :: pytest -q", "C:\\Projets\\essai")
    c = preuve.valider_critere(brut, ["C:\\Projets\\essai", "D:\\x"])["controles"]
    assert [x.get("chemin") or x.get("dossier") for x in c] == ["/C:/Projets/essai/a.md", "/D:/x/b.md", "/C:/Projets/essai"]
    assert preuve.commande_attendue(c[2]) == "cd C:/Projets/essai && pytest -q"  # Git Bash comprend « C:/… »
    assert all("/C:/" not in ligne for ligne in preuve.decrire({"controles": c, "figer": []}))


# ------------------------------------------------------------------ ouverture réelle (Windows)

def _verif(dossier, *controles):
    scope = [str(dossier)]
    c = preuve.valider_critere({"controles": list(controles)}, scope)
    inst = {"mission_id": "m", "generation": 1, "verification_id": "m:1", "critere": c,
            "critere_sha": preuve.empreinte_critere(c), "origine": "utilisateur", "scope": scope,
            "ancrage": preuve.ancrer(c, scope)}
    return [(x["etat"], x["code"]) for x in preuve.verifier(inst, lecture_permise=lambda _c: True)["controles"]]


@windows
def test_lectures_ordinaires(tmp_path):
    (tmp_path / "a.md").write_text("P43 ok", encoding="utf-8")
    (tmp_path / "sous").mkdir()
    assert _verif(tmp_path,
                  {"type": "fichier_existe", "chemin": str(tmp_path / "a.md"), "genre": "fichier"},
                  {"type": "fichier_contient", "chemin": str(tmp_path / "a.md"), "texte": "P43 ok"},
                  {"type": "fichier_existe", "chemin": str(tmp_path / "sous"), "genre": "dossier"},
                  {"type": "dossier_contient", "chemin": str(tmp_path), "attendus": ["a.md"]},
                  {"type": "fichier_existe", "chemin": str(tmp_path / "absent.md"), "genre": "fichier"}) == [
        ("ok", "existe"), ("ok", "texte_trouve"), ("ok", "existe"), ("ok", "dossier_complet"), ("echec", "absent")]


@windows
def test_jonction_refusee(tmp_path):
    dehors = tmp_path / "dehors"
    dehors.mkdir()
    (dehors / "a.md").write_text("x", encoding="utf-8")
    projet = tmp_path / "projet"
    projet.mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(projet / "j"), str(dehors)], check=True, capture_output=True)
    assert _verif(projet, {"type": "fichier_existe", "chemin": str(projet / "j" / "a.md"), "genre": "fichier"}) == [
        ("bloque", "lien")]


@windows
def test_lien_dur_refuse(tmp_path):
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    os.link(tmp_path / "a.md", tmp_path / "b.md")
    assert _verif(tmp_path, {"type": "fichier_contient", "chemin": str(tmp_path / "b.md"), "texte": "x"}) == [
        ("bloque", "lien_dur")]


@windows
def test_fichier_en_cours_d_ecriture(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("x", encoding="utf-8")
    with open(f, "a", encoding="utf-8"):  # un autre l'a ouvert en écriture : lecture refusée, jamais à moitié
        assert _verif(tmp_path, {"type": "fichier_contient", "chemin": str(f), "texte": "x"}) == [("bloque", "instable")]
    assert _verif(tmp_path, {"type": "fichier_contient", "chemin": str(f), "texte": "x"}) == [("ok", "texte_trouve")]


@windows
def test_empreinte_et_casse(tmp_path):
    import hashlib
    (tmp_path / "A.bin").write_bytes(b"abc")
    sha = hashlib.sha256(b"abc").hexdigest()
    assert _verif(tmp_path, {"type": "empreinte", "chemin": str(tmp_path / "a.BIN"), "sha256": sha}) == [
        ("ok", "empreinte")]
