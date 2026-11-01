"""Vue « Tâches » du HUD (26/10, plan P38 lot 2) : côté cœur seulement. La carte venue du HUD
est nommée `todo_id` (son `id` est le numéro de la requête, conn.js) ; les agents vivants
portent `refs` (plans et missions cités par leur consigne) pour le badge « agent en cours »."""
import pytest

from astrel import core, todo as todo_mod
from astrel.todo import todo
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def isole(tmp_path, monkeypatch):
    monkeypatch.setattr(todo_mod, "TODO_FILE", tmp_path / "todo.json")
    monkeypatch.setattr(todo_mod, "JOURNAL_FILE", tmp_path / "todo-journal.jsonl")
    monkeypatch.setattr(todo_mod.Todo, "_plan_statuts", staticmethod(lambda: {}))
    todo.reset()
    yield tmp_path
    todo.reset()


@pytest.mark.parametrize("consigne, refs", [
    ("Mission : réaliser le lot 2 du plan P38, fichier runtime/plans/P38.md", ["p38"]),
    ("passation (mission p38-lot2, champs sur une ligne)", ["p38-lot2"]),
    ("mission=P38-lot2. Comme pour P036 et P7.", ["p38", "p36", "p7", "p38-lot2"]),  # P38 aussi : carte du plan
    ("Mission : corriger le paywall", []),          # mot sans chiffre : pas une mission
    ("Lis CAMP12 et HP38 et P12345", []),            # P collé à un mot, ou 5 chiffres
    ("", []),
])
def test_refs_tirees_de_la_consigne(consigne, refs):
    assert sorted(core._todo_refs(consigne)) == sorted(refs)


def test_refs_bornees_a_six():
    assert len(core._todo_refs(" ".join(f"P{i}" for i in range(1, 20)))) == 6


def test_hud_nomme_la_carte_todo_id_jamais_id(astrel, published):
    j, _, _ = astrel
    for n in range(1, 4):
        assert j.on_command({"cmd": "todo_add", "projet": "Studio", "titre": f"Sujet numéro {n}", "force": True})["ok"]
    # le HUD : `id` = numéro de requête 2, la carte voulue = T3
    r = j.on_command({"cmd": "todo_move", "id": 2, "todo_id": "T3", "etat": "cours", "_via": "hud", "_from_claude": False})
    assert r["ok"] and r["id"] == "T3"
    assert todo.get("T2")["etat"] == "planifier"
    assert j.on_command({"cmd": "todo_get", "id": 1, "todo_id": "T3", "_via": "hud"})["carte"]["etat"] == "cours"
    # sans todo_id, le numéro de requête ne désigne JAMAIS une carte
    r = j.on_command({"cmd": "todo_done", "id": 1, "_via": "hud"})
    assert not r["ok"] and todo.get("T1")["etat"] == "planifier"
    # par le canal de contrôle (outils MCP), `id` reste la carte
    assert j.on_command({"cmd": "todo_done", "id": "T1", "_via": "socket", "_from_claude": True})["ok"]


def test_agent_vivant_porte_les_refs_de_sa_consigne(astrel, published):
    j, _, _ = astrel
    j._agent_log["tu1"] = {"type": "codeur", "consigne": "Réalise le lot 2 du plan P38 (mission p38-lot2).",
                           "files": [], "pending": {}}
    j._on_task({"id": "t1", "phase": "start", "kind": "agent", "desc": "Codeur P38", "tool_use_id": "tu1"})
    items = of(published, "tasks")[-1]["items"]
    assert items[0]["refs"] == ["p38", "p38-lot2"]


def test_refs_calculees_quand_la_consigne_arrive_apres_le_debut(astrel, published):
    j, _, _ = astrel
    j._on_task({"id": "t1", "phase": "start", "kind": "agent", "desc": "Codeur", "tool_use_id": "tu9"})
    assert "refs" not in of(published, "tasks")[-1]["items"][0]
    j._agent_log["tu9"] = {"type": "codeur", "consigne": "plan P40", "files": [], "pending": {}}
    j._on_task({"id": "t1", "phase": "progress", "step": "Lire", "tool_use_id": "tu9"})
    assert of(published, "tasks")[-1]["items"][0]["refs"] == ["p40"]
