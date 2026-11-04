"""Écriture du coffre de mémoire (04/11, carte T49) : en-tête YAML en tête, « When to use » indexé,
migration des notes écrites avec le titre AVANT l'en-tête."""
from __future__ import annotations

import os
import sqlite3

import pytest

from astrel import rappel
from astrel.tools import memory, vault_backup

YAML = "---\nname: feedback-essai\ndescription: une règle\nmetadata:\n  type: feedback\n---\n"


@pytest.fixture
def coffre(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(memory, "VAULT", vault)
    monkeypatch.setattr(memory, "INDEX_DB", tmp_path / "memoire.db")
    monkeypatch.setattr(vault_backup, "BACKUP_DIR", tmp_path / "sauvegardes")
    monkeypatch.setattr(memory, "_seen", {})
    return vault


def test_yaml_garde_en_tete_puis_titre(coffre):
    memory.write("feedback-essai.md", YAML + "\nLe corps de la règle.", "create")
    text = (coffre / "feedback-essai.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: feedback-essai\n")
    assert text == YAML + "\n# Feedback essai\n\nLe corps de la règle.\n"
    assert memory._title(coffre / "feedback-essai.md") == "Feedback essai"


def test_yaml_avec_titre_inchange(coffre):
    memory.write("a.md", YAML + "\n# Mon titre\n\nCorps.", "create")
    assert (coffre / "a.md").read_text(encoding="utf-8") == YAML + "\n# Mon titre\n\nCorps.\n"


def test_texte_nu_titre_ajoute_comme_avant(coffre):
    memory.write("une-note.md", "Juste du texte.", "create")
    assert (coffre / "une-note.md").read_text(encoding="utf-8") == "# Une note\n\nJuste du texte.\n"
    memory.write("une-note.md", "# Déjà titrée\n\nAutre.", "replace")
    assert (coffre / "une-note.md").read_text(encoding="utf-8") == "# Déjà titrée\n\nAutre.\n"


def test_commentaire_yaml_n_est_pas_un_titre(coffre):
    (coffre / "n.md").write_text("---\n# commentaire yaml\nname: n\n---\n\n# Le vrai titre\n", encoding="utf-8")
    assert memory._title(coffre / "n.md") == "Le vrai titre"


def test_when_to_use_indexe(coffre):
    (coffre / "procedures").mkdir()
    (coffre / "procedures" / "procedure-deploiement.md").write_text(
        "# Procédure déploiement\n\nWhen to use: before shipping a release to the store\n\nSteps.\n", encoding="utf-8")
    assert memory._QUAND_RE.search("When to use: x").group(1) == "x"
    found = memory.title_matches("shipping the release", procedures=True)
    assert found and found[0]["path"] == "procedures/procedure-deploiement.md"
    assert "before shipping" in found[0]["quand"]
    assert rappel.excerpt("procedures/procedure-deploiement.md").startswith("before shipping")


def test_index_d_une_version_precedente_reconstruit(coffre):
    con = sqlite3.connect(memory.INDEX_DB)
    con.executescript("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT); INSERT INTO meta VALUES ('schema', '3');"
                      "CREATE TABLE notes (path TEXT PRIMARY KEY, mtime_ns INTEGER, size INTEGER);"
                      "INSERT INTO notes VALUES ('vieille.md', 1, 1);")
    con.commit()
    con.close()
    (coffre / "x.md").write_text("# Xylophone\n\nCorps.\n", encoding="utf-8")
    assert memory.search("xylophone")[0]["path"] == "x.md"
    con = sqlite3.connect(memory.INDEX_DB)
    assert con.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0] == memory._SCHEMA_VERSION == "4"
    assert con.execute("SELECT count(*) FROM notes WHERE path = 'vieille.md'").fetchone()[0] == 0
    con.close()


# ------------------------------------------------------------------ migration des notes déjà écrites

def _abimee(coffre, name="feedback-essai.md", body="Corps.\n"):
    """Forme exacte écrite par l'ancien `write` : titre automatique, ligne vide, YAML, corps."""
    p = coffre / name
    p.write_text(f"# {memory._titre_auto(p)}\n\n{YAML}\n{body}", encoding="utf-8")
    os.utime(p, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    return p


def test_migration_liste_puis_corrige_en_gardant_la_date(coffre):
    p = _abimee(coffre)
    (coffre / "propre.md").write_text(YAML + "\n# Propre\n", encoding="utf-8")
    (coffre / "autre-titre.md").write_text("# Un titre écrit à la main\n\n" + YAML, encoding="utf-8")
    assert memory.migrer_entete() == [{"note": "feedback-essai.md", "etat": "a_migrer"}]
    assert p.read_text(encoding="utf-8").startswith("# Feedback essai")  # liste seulement : rien touché

    assert memory.migrer_entete(appliquer=True) == [{"note": "feedback-essai.md", "etat": "migree"}]
    assert p.read_text(encoding="utf-8") == YAML + "\n# Feedback essai\n\nCorps.\n"
    assert p.stat().st_mtime_ns == 1_700_000_000_000_000_000
    assert vault_backup.list_snapshots() and vault_backup.list_versions(coffre, p)
    assert rappel._first_sentence(p.read_text(encoding="utf-8")) == "Corps."
    assert memory.migrer_entete(appliquer=True) == []  # idempotente
    assert (coffre / "autre-titre.md").read_text(encoding="utf-8").startswith("# Un titre écrit à la main")


def test_migration_laisse_une_note_changee_pendant_la_passe(coffre, monkeypatch):
    p = _abimee(coffre)
    vrai = vault_backup.save_version

    def change(vault, note):
        out = vrai(vault, note)
        note.write_text(note.read_text(encoding="utf-8") + "ajout d'Obsidian\n", encoding="utf-8")
        return out

    monkeypatch.setattr(vault_backup, "save_version", change)
    assert memory.migrer_entete(appliquer=True) == [{"note": "feedback-essai.md", "etat": "changee"}]
    assert p.read_text(encoding="utf-8").endswith("ajout d'Obsidian\n")
    assert not list(coffre.glob(".*.migration"))
