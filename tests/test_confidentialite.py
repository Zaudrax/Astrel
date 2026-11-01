"""Groupe « Confidentialité et données » de la page « Réglages » (17/10, plan P4, lot 6) :
oubli de l'historique après N jours, effacements avec mot tapé et instantané préalable,
export de la mémoire. Coffre, sauvegardes, index et dossier d'export : dossiers du test."""
import json
import sqlite3
import tarfile
import time
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from astrel import confidentialite, config, history
from astrel.settings import reglages
from astrel.tools import memory, vault_backup
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)


@pytest.fixture
def lieux(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "projets").mkdir(parents=True)
    (vault / "Accueil.md").write_text("# Accueil\n")
    (vault / "projets" / "Compta.md").write_text("# Compta\nsecret\n")
    monkeypatch.setattr(memory, "VAULT", vault)
    monkeypatch.setattr(confidentialite, "_vault", lambda: vault)  # conftest le détourne par sécurité
    monkeypatch.setattr(vault_backup, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(config, "EXPORT_DIR", tmp_path / "Astrel-export")
    opened = []
    from astrel import platform

    monkeypatch.setattr(platform, "desktop_backend", lambda: type("B", (), {"open_path": staticmethod(opened.append)}))
    return vault, tmp_path, opened


def transcript(folder, days_old):
    ts = lambda d: (datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    lines = [json.dumps({"type": "user", "sessionId": "s1", "timestamp": ts(d),
                         "message": {"content": f"message vieux de {d} jours"}}) for d in days_old]
    folder.mkdir(exist_ok=True)
    (folder / "s1.jsonl").write_text("\n".join(lines) + "\n")


def count(db):
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT count(*) FROM messages").fetchone()[0]
    finally:
        con.close()


def test_texte_de_ce_qui_part():
    it = next(i for g in reglages.describe()["groups"] if g["id"] == "confidentialite" for i in g["items"])
    labels = [line["label"] for line in it["value"]["lines"]]
    assert "Vers Claude (Anthropic)" in labels and "Gardé par Claude Code" in labels


def test_oubli_apres_n_jours(tmp_path):
    folder, db = tmp_path / "projects", tmp_path / "history.db"
    transcript(folder, [40, 10, 1])
    history.update(folder, db)
    assert count(db) == 3
    reglages.set("history_keep_days", 30)
    assert history.keep_days() == 30  # lu dans le fichier : le serveur MCP le voit aussi
    assert history.update(folder, db) == 0 and count(db) == 2  # purgé à la mise à jour suivante
    assert history.purge(5, db) == 1 and count(db) == 1
    history.update(folder, db)
    assert count(db) == 1  # les vieux messages ne reviennent pas (position gardée)


def test_reglage_oubli_borne():
    from astrel.settings import SettingError

    with pytest.raises(SettingError):
        reglages.set("history_keep_days", -1)
    assert history.keep_days() == 0


def test_effacer_l_historique(tmp_path, monkeypatch):
    folder, db = tmp_path / "projects", tmp_path / "history.db"
    monkeypatch.setattr(config, "HISTORY_DB", db)
    transcript(folder, [3, 2, 1])
    history.update(folder, db)
    assert reglages.action("donnees", "historique", {})["ok"] is False  # sans le mot : rien
    assert count(db) == 3
    res = reglages.action("donnees", "historique", {"confirm": "effacer"})
    assert res["ok"] and "3 message(s)" in res["text"] and count(db) == 0
    assert b"message vieux" not in db.read_bytes()  # compacté : le texte ne traîne pas dans le fichier
    history.update(folder, db)
    assert count(db) == 0 and (folder / "s1.jsonl").exists()  # transcriptions de Claude Code intactes


def test_effacer_la_memoire_apres_instantane(lieux):
    vault, tmp, _ = lieux
    assert reglages.action("donnees", "memoire", {"confirm": "oui"})["ok"] is False
    assert (vault / "Accueil.md").exists()
    res = reglages.action("donnees", "memoire", {"confirm": "EFFACER"})
    assert res["ok"] and "Instantané" in res["text"]
    assert vault.is_dir() and not any(vault.iterdir())
    snap = next((tmp / "backups" / "snapshots").glob("*.tar.gz"))
    with tarfile.open(snap) as tar:
        assert sorted(tar.getnames()) == ["Accueil.md", "projets/Compta.md"]


def test_rien_efface_sans_instantane(lieux, monkeypatch):
    vault, _, _ = lieux

    def boom(*a, **k):
        raise OSError("disque plein")

    monkeypatch.setattr(vault_backup, "snapshot", boom)
    res = reglages.action("donnees", "memoire", {"confirm": "EFFACER"})
    assert res["ok"] is False and "rien n'est effacé" in res["error"]
    assert (vault / "projets" / "Compta.md").exists()


def test_effacements_refuses_a_claude(astrel, published, lieux):
    j, _, _ = astrel
    vault, _, _ = lieux
    res = j.on_command({"cmd": "settings_action", "key": "donnees", "action": "memoire", "confirm": "EFFACER",
                        "_from_claude": True})
    assert res["ok"] is False and (vault / "Accueil.md").exists()


def test_export(lieux):
    vault, tmp, opened = lieux
    res = reglages.action("donnees", "export", {})
    assert res["ok"] and "2 fichier(s)" in res["text"]
    z = next((tmp / "Astrel-export").glob("memoire-astrel-*.zip"))
    with zipfile.ZipFile(z) as f:
        assert sorted(f.namelist()) == ["Accueil.md", "projets/Compta.md"]
    assert opened == [str(tmp / "Astrel-export")]
    assert (vault / "Accueil.md").exists()  # exporter ne retire rien


def test_purge_au_reglage_par_le_coeur(astrel, tmp_path, monkeypatch):
    j, _, _ = astrel
    folder, db = tmp_path / "projects", tmp_path / "history.db"
    monkeypatch.setattr(config, "HISTORY_DB", db)
    transcript(folder, [50, 1])
    history.update(folder, db)
    assert j.on_command({"cmd": "settings_set", "key": "history_keep_days", "value": 7})["ok"]
    deadline = time.time() + 3
    while count(db) != 1 and time.time() < deadline:
        time.sleep(0.02)
    assert count(db) == 1
