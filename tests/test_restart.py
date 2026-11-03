"""Relance du cerveau pendant que le cœur lit le flux en continu.

Le 27/09, chaque remise à zéro de session par inactivité (toutes les 30 min)
faisait dire « Le moteur s'est arrêté. » : la fin de flux de l'ANCIEN process,
arrêté exprès par `restart`, arrivait dans la file encore courante (la nouvelle
n'était créée qu'après le `sleep`) et passait pour une panne. Le nouveau
process, lui, était sain, et la relance « de secours » le tuait.

Un faux CLI suffit : comme le vrai, il n'écrit rien avant la première requête
et s'arrête quand son entrée standard se ferme."""
import sys
import threading
import time

import pytest

from astrel import config
from astrel.brain import Brain
from conftest import programme_python

QUIET_CLI = "import sys\nfor _ in sys.stdin:\n    pass\n"
DYING_CLI = "import sys\nsys.exit(1)\n"


def fake_cli(tmp_path, monkeypatch, body: str):
    monkeypatch.setattr(config, "CLAUDE_BIN", programme_python(tmp_path / "claude", body))
    monkeypatch.setattr(config, "WORKDIR", tmp_path)
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(config, "MCP_CONFIG_FILE", tmp_path / "absent.json")


def pump(brain: Brain, errors: list, stop: threading.Event) -> None:
    """Comme `Astrel._pump` : un fil qui lit `events` sans fin."""
    for ev in brain.events(idle_s=0.05):
        if stop.is_set():
            return
        if ev.kind == "error":
            errors.append(ev.text)


def test_relance_voulue_sans_fausse_panne(tmp_path, monkeypatch):
    fake_cli(tmp_path, monkeypatch, QUIET_CLI)
    b = Brain()
    b.start()
    errors: list = []
    stop = threading.Event()
    threading.Thread(target=pump, args=(b, errors, stop), daemon=True).start()
    try:
        for _ in range(5):
            time.sleep(0.2)
            b.restart(fresh=True)  # comme `reset_session`, depuis un autre fil
            assert b.alive
        time.sleep(0.5)
        assert errors == []
        assert b.alive  # le premier process relancé est bien vivant
    finally:
        stop.set()
        b.stop()


def test_vraie_panne_toujours_signalee(tmp_path, monkeypatch):
    """Un process qui meurt de lui-même reste une panne, dite à l'utilisateur."""
    fake_cli(tmp_path, monkeypatch, DYING_CLI)
    b = Brain()
    errors: list = []
    stop = threading.Event()
    b.start()
    threading.Thread(target=pump, args=(b, errors, stop), daemon=True).start()
    try:
        deadline = time.time() + 5
        while not errors and time.time() < deadline:
            time.sleep(0.05)
        assert errors == ["Le moteur s'est arrêté."]
    finally:
        stop.set()
        b.stop()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
