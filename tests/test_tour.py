"""T58 pas 2a (04/11) : l'état du tour dans `Astrel.tour`, anciens noms en descripteurs."""
from __future__ import annotations

from astrel import core
from astrel.tour import NOMS, DuTour, Tour


def test_lecture_ecriture_sans_init():
    j = object.__new__(core.Astrel)
    assert j._turn_active is False and j._turn_idle.is_set()  # défauts, sans `__init__`
    j._turn_active = True
    j._hushed = True
    assert j.tour.active is True and j.tour.hushed is True and j.__dict__["tour"] is j.tour


def test_chaque_champ_a_son_descripteur():
    champs = set(Tour.__dataclass_fields__)
    assert set(NOMS.values()) == champs and len(champs) == 17
    for ancien, champ in NOMS.items():
        d = core.Astrel.__dict__.get(ancien)
        assert isinstance(d, DuTour) and d.champ == champ, ancien


def test_nouveau_remet_le_tour_a_zero():
    j = object.__new__(core.Astrel)
    j._spoken_any, j._reply, j._hushed, j._turn_acted, j._turn_retry = True, ["x"], True, True, 2
    j._silent_hold, j._silenced, j._auto_joined, j._report_chunk = "a", True, True, True
    j._held_group = object()
    j._begin_turn("clavier")
    assert (j._spoken_any, j._reply, j._hushed, j._turn_acted, j._turn_retry) == (False, [], False, False, 0)
    assert (j._silent_hold, j._silenced, j._auto_joined, j._report_chunk, j._held_group) == ("", False, False, False, None)
    assert j._turn_source == "clavier" and j._turn_active and not j._turn_idle.is_set() and j._turn_sent == []


def test_monkeypatch_sur_un_ancien_nom(monkeypatch):
    j = object.__new__(core.Astrel)
    monkeypatch.setattr(j, "_turn_active", True)
    assert j.tour.active is True
    monkeypatch.undo()
    assert j.tour.active is False
