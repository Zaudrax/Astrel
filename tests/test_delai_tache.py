"""Délai « la tâche ne répond plus » : il ne court pas pendant qu'une carte de
confirmation attend l'utilisateur (18/10 : une carte restée 16 min tuait la tâche)."""
import time

from astrel import config, core
from astrel.brain import BrainEvent


class _Brain:
    def __init__(self):
        self.interrupts = 0

    def interrupt(self):
        self.interrupts += 1


def _astrel(pending):
    j = object.__new__(core.Astrel)
    j.brain = _Brain()
    j._turn_active = True
    j._last_event = time.time() - config.BRAIN_TURN_TIMEOUT_S - 60   # silence très long
    j._pending_confirm = pending
    j.errors, j.ended = [], []
    j._on_turn_error = lambda ev: j.errors.append(ev.text)
    j._end_turn = lambda followup=True: j.ended.append(followup)
    return j


def test_carte_en_attente_ne_tue_pas_la_tache():
    j = _astrel({"id": "c1"})
    j._on_stream(BrainEvent("idle"))
    assert j.errors == [] and j.brain.interrupts == 0
    # Le délai repart de maintenant : pas d'arrêt immédiat à la réponse.
    assert time.time() - j._last_event < 5


def test_sans_carte_le_silence_long_arrete_la_tache():
    j = _astrel(None)
    j._on_stream(BrainEvent("idle"))
    assert j.errors == ["La tâche ne répond plus, je l'arrête."]
    assert j.brain.interrupts == 1 and j.ended == [False]
