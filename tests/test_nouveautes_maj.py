"""Versions, notes de version et annonce des mises à jour (10/11/2026).

`astrel/nouveautes.py` (pur), `maj.py` (version et notes lues sur la branche suivie, évènement
`maj_dispo`, vérification automatique), `update.py` (version avant/après), `tools/version.py`.
Aucun git réel : `_git`/`_out` remplacés."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from astrel import maj, nouveautes

ROOT = Path(__file__).resolve().parent.parent

NOTES = """# Nouveautés d'Astrel

Intro.

## 3.0.0 — 2026-12-01

- Gros changement.

## 2.1.0 — 2026-11-20

- Petit ajout.

## 2.0.0 — 2026-11-10

- Première version numérotée.
"""


def _charger(nom: str, chemin: Path):
    spec = importlib.util.spec_from_file_location(nom, chemin)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[nom] = mod
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ nouveautes.py


def test_parse_et_comparaisons():
    assert nouveautes.parse("2.1.0") == (2, 1, 0)
    assert nouveautes.parse("2.0.0-dev") == (2, 0, 0)
    assert nouveautes.parse("v3.1") == (3, 1, 0)
    assert nouveautes.parse("") is None and nouveautes.parse("abc") is None
    assert nouveautes.plus_recente("2.10.0", "2.9.0")  # numérique, pas alphabétique
    assert not nouveautes.plus_recente("2.0.0", "2.0.0-dev")
    assert not nouveautes.plus_recente("", "2.0.0")
    assert nouveautes.est_majeure("3.0.0", "2.9.9")
    assert not nouveautes.est_majeure("2.1.0", "2.0.0")
    assert not nouveautes.est_majeure("3.0.0", "")


def test_lire_version():
    assert nouveautes.lire_version("\n 2.1.0 \n") == "2.1.0"
    assert nouveautes.lire_version("n'importe quoi") == ""
    assert nouveautes.lire_version(None) == ""


def test_sections_et_depuis():
    secs = nouveautes.sections(NOTES)
    assert [s["version"] for s in secs] == ["3.0.0", "2.1.0", "2.0.0"]
    assert secs[0]["date"] == "2026-12-01" and secs[0]["texte"] == "- Gros changement."
    # Seulement ce qui est plus récent que l'installée, la plus récente d'abord, borné par `jusqua`.
    assert [s["version"] for s in nouveautes.depuis(NOTES, "2.0.0")] == ["3.0.0", "2.1.0"]
    assert [s["version"] for s in nouveautes.depuis(NOTES, "2.0.0", "2.1.0")] == ["2.1.0"]
    assert nouveautes.depuis(NOTES, "3.0.0") == []
    assert nouveautes.section(NOTES, "2.1") == {"version": "2.1.0", "date": "2026-11-20", "texte": "- Petit ajout."}


def test_depuis_borne_la_taille(monkeypatch):
    monkeypatch.setattr(nouveautes, "NOTES_MAX_OCTETS", 40)
    long = "## 2.1.0\n\n" + "x" * 500 + "\n"
    out = nouveautes.depuis(long, "2.0.0")
    assert len(out) == 1 and out[0]["texte"].endswith("…") and len(out[0]["texte"]) < 60


def test_monter():
    assert nouveautes.monter("2.1.3", "majeure") == "3.0.0"
    assert nouveautes.monter("2.1.3", "mineure") == "2.2.0"
    assert nouveautes.monter("2.1.3", "correctif") == "2.1.4"
    with pytest.raises(ValueError):
        nouveautes.monter("2.1.3", "énorme")


def test_fichiers_du_depot_coherents():
    """VERSION lisible, sa section présente dans NOUVEAUTES.md, et les deux partent dans le dépôt public."""
    v = nouveautes.lire_version((ROOT / "VERSION").read_text(encoding="utf-8"))
    assert v, "VERSION illisible"
    sec = nouveautes.section((ROOT / "NOUVEAUTES.md").read_text(encoding="utf-8"), v)
    assert sec and sec["texte"], f"NOUVEAUTES.md sans section {v}"
    outil = ROOT / "tools" / "export_depot.py"  # absent du dépôt public (outil propre à la machine de l'utilisateur)
    if outil.exists():
        export = outil.read_text(encoding="utf-8")
        assert '"VERSION"' in export and '"NOUVEAUTES.md"' in export


# ------------------------------------------------------------------ maj.py


@pytest.fixture
def faux_depot(monkeypatch):
    """MiseAJour neuve, dépôt propre ; `git show` de la branche suivie servi par `distant`."""
    distant = {"VERSION": "3.0.0\n", "NOUVEAUTES.md": NOTES, "behind": "4", "fetch": 0}

    def fake_git(*args, timeout=10.0):
        if args[:1] == ("fetch",):
            return subprocess.CompletedProcess(args, distant["fetch"], "", "")
        return subprocess.CompletedProcess(args, 1, "", "")

    def fake_out(*args):
        if args[:2] == ("rev-list", "--count"):
            return distant["behind"]
        if args[:1] == ("show",):
            return distant.get(args[1].split(":", 1)[1], "").strip()
        return ""

    monkeypatch.setattr(maj, "_git", fake_git)
    monkeypatch.setattr(maj, "_out", fake_out)
    monkeypatch.setattr(maj, "version_locale", lambda: "2.0.0")
    publies = []
    monkeypatch.setattr(maj.bus, "publish", lambda kind, **kw: publies.append((kind, kw)))
    m = maj.MiseAJour()
    m.etat_local = {"git": True, "commit": "abc1234", "origin": "https://exemple.fr/astrel.git", "changed": 0}
    m.local = lambda: dict(m.etat_local)  # `_fetch` remet le cache à zéro : l'état reste celui du test
    return m, distant, publies


def test_version_majeure_annoncee_avec_ses_notes(faux_depot):
    m, _distant, publies = faux_depot
    m.check(wait=True)
    d = m.dispo()
    assert d["disponible"] and d["behind"] == 4
    assert d["version"] == "3.0.0" and d["version_locale"] == "2.0.0" and d["majeure"]
    assert [n["version"] for n in d["notes"]] == ["3.0.0", "2.1.0"]
    assert d["bloque"] == ""
    assert publies and publies[-1][0] == "maj_dispo" and publies[-1][1]["majeure"]
    lignes = {x["label"]: x["text"] for x in m.view()["value"]["lines"]}
    assert lignes["Disponible"] == "version 3.0.0 (majeure)"
    assert lignes["Version"].startswith("2.0.0")


def test_version_mineure(faux_depot):
    m, distant, _ = faux_depot
    distant["VERSION"] = "2.1.0\n"
    m.check(wait=True)
    d = m.dispo()
    assert d["version"] == "2.1.0" and not d["majeure"]
    assert [n["version"] for n in d["notes"]] == ["2.1.0"]  # rien au-delà de la version reçue


def test_corrections_sans_nouvelle_version(faux_depot):
    m, distant, _ = faux_depot
    distant["VERSION"] = "2.0.0\n"
    m.check(wait=True)
    d = m.dispo()
    assert d["disponible"] and d["version"] == "" and not d["majeure"] and d["notes"] == []


def test_a_jour_et_injoignable(faux_depot):
    m, distant, _ = faux_depot
    distant["behind"] = "0"
    m.check(wait=True)
    assert not m.dispo()["disponible"]
    distant["fetch"] = 1
    m.check(wait=True)
    assert not m.dispo()["disponible"]


def test_mise_a_jour_bloquee_par_des_fichiers_modifies(faux_depot):
    m, _distant, _ = faux_depot
    m.etat_local["changed"] = 2
    m.check(wait=True)
    d = m.dispo()
    assert d["disponible"] and "modifié" in d["bloque"]


def test_tick_auto(faux_depot, monkeypatch):
    m, _distant, _ = faux_depot
    lancees = []
    monkeypatch.setattr(m, "check", lambda wait=False: lancees.append(1) or {"ok": True})
    assert not m.tick_auto(now=1000.0)                          # premier passage : programme
    assert not m.tick_auto(now=1000.0 + maj.AUTO_PREMIER_S - 1)
    assert m.tick_auto(now=1000.0 + maj.AUTO_PREMIER_S)          # une minute après
    assert not m.tick_auto(now=1000.0 + maj.AUTO_PREMIER_S + 3600)
    assert m.tick_auto(now=1000.0 + maj.AUTO_PREMIER_S + maj.AUTO_PERIODE_S)  # puis une fois par jour
    assert len(lancees) == 2


def test_tick_auto_coupe(faux_depot, monkeypatch):
    m, _distant, _ = faux_depot
    monkeypatch.setattr(m, "check", lambda wait=False: pytest.fail("ne doit pas vérifier"))
    monkeypatch.setenv("ASTREL_MAJ_AUTO", "0")
    for t in (0.0, 1e6, 2e6):
        assert not m.tick_auto(now=t)
    monkeypatch.delenv("ASTREL_MAJ_AUTO")
    m.auto = False
    for t in (0.0, 1e6, 2e6):
        assert not m.tick_auto(now=t)


def test_tick_auto_sans_depot_d_origine(faux_depot, monkeypatch):
    m, _distant, _ = faux_depot
    m.etat_local["origin"] = ""
    monkeypatch.setattr(m, "check", lambda wait=False: pytest.fail("pas de dépôt d'origine"))
    m.tick_auto(now=0.0)
    assert not m.tick_auto(now=maj.AUTO_PREMIER_S)


def test_les_tests_ne_verifient_jamais_seuls():
    """conftest fige `maj.maj` et coupe la vérification automatique (jamais de git fetch en test)."""
    assert maj.maj.auto is False


def test_tick_du_coeur_branche():
    from astrel.coeur import mission as coeur_mission
    from astrel.coeur.reglages import ReglagesCoeur

    assert ("mises à jour", "_maj_tick") in coeur_mission.MissionCoeur._TICKS
    assert callable(getattr(ReglagesCoeur, "_maj_tick", None))


def test_maj_dispo_est_collant():
    from astrel import events

    assert "maj_dispo" in events.STICKY


# ------------------------------------------------------------------ update.py et tools/version.py


def test_update_affiche_version_et_notes(tmp_path):
    up = _charger("astrel_update_test", ROOT / "update.py")
    (tmp_path / "astrel").mkdir()
    (tmp_path / "astrel" / "nouveautes.py").write_text((ROOT / "astrel" / "nouveautes.py").read_text())
    (tmp_path / "VERSION").write_text("3.0.0\n")
    (tmp_path / "NOUVEAUTES.md").write_text(NOTES)
    up.ROOT = tmp_path
    lignes = up.nouveautes_recues("2.0.0", root=tmp_path)
    assert lignes[0].strip() == "Version : 2.0.0 → 3.0.0  (MISE À JOUR MAJEURE)"
    texte = "\n".join(lignes)
    assert "Gros changement" in texte and "Petit ajout" in texte and "Première version" not in texte
    assert up.nouveautes_recues("3.0.0", root=tmp_path) == []


def test_tools_version(tmp_path, monkeypatch, capsys):
    tv = _charger("astrel_tools_version_test", ROOT / "tools" / "version.py")
    monkeypatch.setattr(tv, "VERSION", tmp_path / "VERSION")
    monkeypatch.setattr(tv, "NOTES", tmp_path / "NOUVEAUTES.md")
    (tmp_path / "VERSION").write_text("2.0.0\n")
    (tmp_path / "NOUVEAUTES.md").write_text(NOTES.replace("## 3.0.0 — 2026-12-01\n\n- Gros changement.\n\n", "")
                                            .replace("## 2.1.0 — 2026-11-20\n\n- Petit ajout.\n\n", ""))
    assert tv.main(["--verifier"]) == 0
    assert tv.main(["--monter", "mineure"]) == 0
    assert (tmp_path / "VERSION").read_text() == "2.1.0\n"
    notes = (tmp_path / "NOUVEAUTES.md").read_text()
    assert notes.index("## 2.1.0") < notes.index("## 2.0.0") and notes.index("Intro.") < notes.index("## 2.1.0")
    assert tv.main(["--verifier"]) == 1  # section encore « à remplir »
    capsys.readouterr()
    assert tv.main(["--notes", "2.0.0"]) == 0
    assert capsys.readouterr().out.strip() == "- Première version numérotée."
