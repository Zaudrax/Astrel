"""Modèle principal du cerveau : choisi sur le HUD, changé À CHAUD par la requête
de contrôle « set_model » du CLI (vérifiée en réel le 27/09 sur le CLI 2.1.282),
retenu dans `runtime/model.txt`."""
import io
import queue
import threading

import pytest

from astrel import config, core
from astrel.brain import Brain, model_label


class FakeProc:
    def poll(self):
        return None  # process vivant


def brain_answering(response: dict) -> tuple[Brain, list]:
    """Un cerveau dont le « CLI » répond `response` à chaque requête de contrôle."""
    b = Brain()
    b._proc = FakeProc()
    sent: list = []

    def write(msg: dict) -> None:
        sent.append(msg)
        b._on_control({"request_id": msg["request_id"], **response})

    b._write = write
    return b, sent


def test_set_model_a_chaud():
    b, sent = brain_answering({"subtype": "success"})
    b.set_model("opus")
    assert sent[0]["type"] == "control_request"
    assert sent[0]["request"] == {"subtype": "set_model", "model": "opus"}
    assert b.model == "opus"
    assert not b._control_waiters  # rien ne reste en attente


def test_set_model_refuse():
    b, _ = brain_answering({"subtype": "error", "error": "Model 'x' not found"})
    b.model = "sonnet"
    with pytest.raises(RuntimeError, match="not found"):
        b.set_model("x")
    assert b.model == "sonnet"


def test_set_model_cerveau_arrete():
    b = Brain()
    b.set_model("haiku")  # pris au prochain démarrage, sans rien écrire
    assert b.model == "haiku"


def test_reponse_de_controle_hors_file():
    """La réponse va à qui l'attend, pas dans la file lue par le cœur."""
    proc = type("P", (), {"stdout": io.StringIO(
        '{"type":"control_response","response":{"request_id":"r1","subtype":"success"}}\n'
        '{"type":"result","result":"ok"}\n')})()
    events: queue.Queue = queue.Queue()
    got: list = []
    Brain._read_stdout(proc, events, got.append)
    assert got == [{"request_id": "r1", "subtype": "success"}]
    assert events.get()["type"] == "result" and events.get()["type"] == "_eof"


def test_modele_retenu(tmp_path, monkeypatch):
    f = tmp_path / "model.txt"
    monkeypatch.setattr(config, "MODEL_FILE", f)
    assert config._saved_model() == ""
    f.write_text("Opus\n")
    assert config._saved_model() == "opus"
    f.write_text("n'importe quoi")
    assert config._saved_model() == ""  # un fichier abîmé ne bloque pas le démarrage
    assert model_label("haiku") == "Haiku" and model_label("claude-x") == "claude-x"


def make_astrel(brain) -> core.Astrel:
    j = object.__new__(core.Astrel)
    j.brain = brain
    j._turn_active = False
    j._tasks, j._tasks_lock, j._turn_lock = {}, threading.Lock(), threading.Lock()
    return j


def test_commande_model(tmp_path, monkeypatch):
    f = tmp_path / "model.txt"
    monkeypatch.setattr(config, "MODEL_FILE", f)
    b, sent = brain_answering({"subtype": "success"})
    b.model = "sonnet"
    j = make_astrel(b)
    assert j.on_command({"cmd": "model opus"}) == {"ok": True, "model": "opus"}
    assert f.read_text().strip() == "opus" and b.model == "opus"
    assert j.on_command({"cmd": "model"})["model"] == "opus"
    assert not j.on_command({"cmd": "model", "model": "gpt"})["ok"]
    assert [m["model"] for m in j.on_command({"cmd": "models"})["models"]] == ["sonnet", "opus", "haiku"]


def test_repli_jamais_pendant_une_tache(tmp_path, monkeypatch):
    """Si le CLI refuse le changement à chaud, on relance le cerveau (--resume) —
    mais pas pendant qu'une tâche tourne : la relance la tuerait."""
    monkeypatch.setattr(config, "MODEL_FILE", tmp_path / "model.txt")
    b, _ = brain_answering({"subtype": "error", "error": "inconnu"})
    b.model = "sonnet"
    restarts: list = []
    b.restart = lambda fresh=False: restarts.append(fresh)
    j = make_astrel(b)
    j._tasks = {"t1": {}}
    assert not j.on_command({"cmd": "model", "model": "opus"})["ok"]
    assert b.model == "sonnet" and not restarts
    j._tasks = {}
    assert j.on_command({"cmd": "model", "model": "opus"})["ok"]
    assert b.model == "opus" and restarts == [False]  # reprise de la session, pas remise à zéro
