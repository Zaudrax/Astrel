"""Propositions de mémoire à valider (04/11, carte T49) : dépôt, écran du HUD (commandes du cœur), refus à Claude."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from astrel import audit
from astrel.tools import memory, vault_backup, vault_review
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)

COMMANDES = ("propositions_get", "proposition_appliquer", "proposition_refuser")
NOTE = "---\nname: idee\ndescription: une idée\n---\n\nUne idée durable."


@pytest.fixture
def coffre(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(memory, "VAULT", vault)
    monkeypatch.setattr(memory, "INDEX_DB", tmp_path / "memoire.db")
    monkeypatch.setattr(vault_backup, "BACKUP_DIR", tmp_path / "sauvegardes")
    monkeypatch.setattr(memory, "_seen", {})
    return vault


def _pid(r: dict) -> str:
    return Path(r["proposition"]).stem  # 04/11 (CI Windows) : séparateur du système, pas « / »


def _une(coffre, kind="nouvelle", notes=("projets/idee.md",), text=NOTE, now=None):
    return _pid(vault_review.propose(kind, list(notes), text, "raison de test", now=now))


# ------------------------------------------------------------------ module

def test_liste_avec_texte_exact_et_empreinte_stable(coffre):
    pid = _une(coffre)
    (vault_review.PROPOSALS_DIR / "2026-11-01-abimee-nouvelle.json").write_text("{pas du json", encoding="utf-8")
    items = vault_review.lister()
    assert [it["proposition_id"] for it in items] == [pid] and vault_review.en_attente() == 1
    it = items[0]
    assert it["texte"].startswith("---\nname: idee\n") and "\n# Idee\n\nUne idée durable." in it["texte"]
    assert it["empreinte"] == vault_review.lister()[0]["empreinte"] and len(it["empreinte"]) == 16


def test_appliquer_une_nouvelle_puis_jamais_deux_fois(coffre):
    pid = _une(coffre)
    emp = vault_review.lister()[0]["empreinte"]
    assert vault_review.appliquer(pid, emp)["ok"]
    texte = (coffre / "projets" / "idee.md").read_text(encoding="utf-8")
    assert texte == vault_review.lister(toutes=True)[0]["texte"] + "\n"
    data = json.loads((vault_review.PROPOSALS_DIR / f"{pid}.json").read_text(encoding="utf-8"))
    assert data["statut"] == "appliquée" and data["traitee"]["par"] == "utilisateur"
    with pytest.raises(ValueError, match="déjà traitée"):
        vault_review.appliquer(pid, emp)
    assert vault_review.en_attente() == 0


def test_empreinte_changee_refusee(coffre):
    pid = _une(coffre)
    emp = vault_review.lister()[0]["empreinte"]
    path = vault_review.PROPOSALS_DIR / f"{pid}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["texte_propose"] = "Autre chose, réécrite entre l'affichage et le clic."
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="a changé"):
        vault_review.appliquer(pid, emp)
    assert not (coffre / "projets" / "idee.md").exists()


def test_nouvelle_devenue_existante_refusee(coffre):
    pid = _une(coffre)
    emp = vault_review.lister()[0]["empreinte"]
    (coffre / "projets").mkdir()
    (coffre / "projets" / "idee.md").write_text("# Déjà là\n", encoding="utf-8")
    with pytest.raises(ValueError, match="existe déjà"):
        vault_review.appliquer(pid, emp)
    assert (coffre / "projets" / "idee.md").read_text(encoding="utf-8") == "# Déjà là\n"


def test_consolidation_refusee_si_la_note_a_change_sinon_version_gardee(coffre):
    (coffre / "a.md").write_text("# A\n\nAncien texte.\n", encoding="utf-8")
    pid = _une(coffre, "consolidation", ["a.md"], "# A\n\nTexte consolidé.")
    emp = vault_review.lister()[0]["empreinte"]
    (coffre / "a.md").write_text("# A\n\nModifiée dans Obsidian.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="a changé depuis la proposition"):
        vault_review.appliquer(pid, emp)
    (coffre / "a.md").write_text("# A\n\nAncien texte.\n", encoding="utf-8")
    assert vault_review.appliquer(pid, emp)["ok"]
    assert (coffre / "a.md").read_text(encoding="utf-8") == "# A\n\nTexte consolidé.\n"
    assert vault_backup.list_versions(coffre, coffre / "a.md")


def test_fusion_avec_renvois(coffre):
    (coffre / "a.md").write_text("# A\n\nUn.\n", encoding="utf-8")
    (coffre / "b.md").write_text("# B\n\nDeux.\n", encoding="utf-8")
    pid = _une(coffre, "fusion", ["a.md", "b.md"], "# A\n\nUn et deux.")
    assert vault_review.appliquer(pid, vault_review.lister()[0]["empreinte"])["ok"]
    assert "Un et deux." in (coffre / "a.md").read_text(encoding="utf-8")
    assert "Fusionnée dans [[a]]" in (coffre / "b.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("pid", ["../x", "2026-11-03-../../etc", "2026-11-03-inconnue-nouvelle", ""])
def test_identifiant_hors_du_dossier_refuse(coffre, pid):
    _une(coffre)
    with pytest.raises(ValueError, match="proposition inconnue"):
        vault_review.appliquer(pid, "x")


def test_note_hors_du_coffre_refusee(coffre):
    pid = _une(coffre)
    path = vault_review.PROPOSALS_DIR / f"{pid}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["note"] = "../../dehors.md"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="hors du coffre"):
        vault_review.appliquer(pid, vault_review.lister()[0]["empreinte"])


def test_refuser_ecrit_statut_et_raison_coffre_intact(coffre):
    pid = _une(coffre)
    assert vault_review.refuser(pid, "pas utile")["ok"]
    data = json.loads((vault_review.PROPOSALS_DIR / f"{pid}.json").read_text(encoding="utf-8"))
    assert data["statut"] == "refusée" and data["traitee"]["raison"] == "pas utile"
    assert not (coffre / "projets").exists() and vault_review.lister() == []


# ------------------------------------------------------------------ commandes du cœur

@pytest.mark.parametrize("name", COMMANDES)
def test_commandes_refusees_a_claude(astrel, coffre, name, published):
    j, _, _ = astrel
    pid = _une(coffre)
    r = j.on_command({"cmd": name, "proposition_id": pid, "empreinte": vault_review.lister()[0]["empreinte"],
                      "_from_claude": True, "_via": "socket"})
    assert r["ok"] is False and "refusées à Claude" in r["error"]
    assert vault_review.en_attente() == 1 and not (coffre / "projets").exists()
    assert any("refusé" in ev.get("text", "") for ev in of(published, "notice"))


def test_appliquer_par_le_hud_publie_et_note_au_journal(astrel, coffre, published):
    j, _, _ = astrel
    pid = _une(coffre)
    got = j.on_command({"cmd": "propositions_get", "_via": "hud"})
    assert got["ok"] and got["propositions"][0]["texte"]
    r = j.on_command({"cmd": "proposition_appliquer", "proposition_id": pid,
                      "empreinte": got["propositions"][0]["empreinte"], "_via": "hud"})
    assert r["ok"] and (coffre / "projets" / "idee.md").exists()
    assert of(published, "propositions")[-1]["en_attente"] == 0
    entries = audit.read(kinds=["memoire"])
    assert entries and "appliquée" in entries[0]["what"]


def test_par_la_socket_liste_reduite_sans_textes(astrel, coffre):
    j, _, _ = astrel
    for i in range(25):  # 10 par jour au plus : 3 jours
        _une(coffre, notes=(f"projets/idee-{i:02d}.md",), text=NOTE + " " + "x" * 2000, now=1_793_000_000 + i // 10 * 86_400)
    r = j.on_command({"cmd": "propositions_get", "_via": "socket"})
    assert r["ok"] and len(r["propositions"]) <= 20 and all("texte" not in it for it in r["propositions"])
    assert len(json.dumps(r, ensure_ascii=False).encode()) <= 4096


def test_erreur_rendue_sans_exception(astrel, coffre):
    j, _, _ = astrel
    r = j.on_command({"cmd": "proposition_appliquer", "proposition_id": "../x", "_via": "hud"})
    assert r == {"ok": False, "error": "proposition inconnue"}
