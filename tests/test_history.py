"""Recherche plein texte dans l'historique des conversations (index FTS5 incrémental)."""
import json

import pytest

from astrel import history


def line(kind, text, ts="2026-10-10T09:00:00.000Z", session="s1", **extra):
    content = text if kind == "user" else [{"type": "text", "text": text}]
    return json.dumps({"type": kind, "sessionId": session, "timestamp": ts,
                       "message": {"role": kind, "content": content}, **extra}, ensure_ascii=False) + "\n"


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "projets"
    d.mkdir()
    (d / "s1.jsonl").write_text(
        line("user", "On garde le build Expo numéro 9 en file d'attente, décision prise.")
        + line("assistant", "D'accord, je surveille la build Expo et je te préviens.")
        + json.dumps({"type": "user", "sessionId": "s1", "timestamp": "2026-10-10T09:01:00Z",
                      "message": {"role": "user", "content": [{"type": "tool_result", "content": "Expo sortie"}]}}) + "\n"
        + line("assistant", "Rapport d'agent Expo", isSidechain=True)
        + line("user", "[Réflexe programmé, l'utilisateur n'a rien demandé à l'instant.] Vérifie la build Expo"),
        encoding="utf-8")
    return d


def test_recherche_avec_extraits(folder, tmp_path):
    db = tmp_path / "h.db"
    assert history.update(folder, db) == 3  # ni sortie d'outil, ni rapport d'agent
    res = history.search("build expo", folder=folder, db=db)
    assert len(res) == 3
    assert res[-1]["qui"] == "automatique"   # le réflexe passe après
    assert "«" in res[0]["extrait"] and res[0]["session"] == "s1"
    only_user = history.search("expo", role="user", folder=folder, db=db)
    assert {r["qui"] for r in only_user} == {"l'utilisateur", "automatique"}
    assert history.search("décision", folder=folder, db=db)[0]["qui"] == "l'utilisateur"  # accents ignorés
    assert history.search("decision", folder=folder, db=db)


def test_mise_a_jour_incrementale(folder, tmp_path):
    db = tmp_path / "h.db"
    history.update(folder, db)
    path = folder / "s1.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line("user", "Et le bracelet Studio ?", ts="2026-10-11T10:00:00Z"))
        fh.write('{"type": "user", "message": {"content": "ligne pas fin')  # en cours d'écriture
    assert history.update(folder, db) == 1
    assert history.update(folder, db) == 0
    with path.open("a", encoding="utf-8") as fh:
        fh.write('ie bracelet"}, "sessionId": "s1", "timestamp": "2026-10-11T10:01:00Z"}\n')
    assert history.update(folder, db) == 1    # la ligne complétée est lue en entier
    assert len(history.search("bracelet", folder=folder, db=db)) == 2


def test_fichier_remplace_reindexe(folder, tmp_path):
    db = tmp_path / "h.db"
    history.update(folder, db)
    (folder / "s1.jsonl").write_text(line("user", "tout neuf"), encoding="utf-8")
    history.update(folder, db)
    assert history.search("expo", folder=folder, db=db) == []
    assert history.search("neuf", folder=folder, db=db)


def test_filtre_par_date(folder, tmp_path):
    db = tmp_path / "h.db"
    (folder / "s2.jsonl").write_text(line("user", "Expo aujourd'hui", ts="2099-01-01T00:00:00Z", session="s2"))
    res = history.search("expo", days=3, folder=folder, db=db)
    assert [r["session"] for r in res] == ["s2"]


def test_question_vide_ou_bizarre(folder, tmp_path):
    db = tmp_path / "h.db"
    with pytest.raises(ValueError):
        history.search("  ? ", folder=folder, db=db)
    # Les opérateurs FTS5 sont neutralisés : pas d'erreur de syntaxe.
    assert history.search('expo" OR NEAR(', folder=folder, db=db) is not None
