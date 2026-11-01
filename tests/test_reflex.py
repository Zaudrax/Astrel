from datetime import datetime

from astrel.senses import reflex


def make(rules, monkeypatch, presence=(False, False, 0.0)):
    monkeypatch.setattr(reflex, "load_rules", lambda: rules)
    sent = []
    engine = reflex.Reflex(command=sent.append, presence=lambda: presence)
    return engine, sent


def test_heure_fixe_une_fois_par_jour(monkeypatch):
    rules = [{"id": "b", "name": "Briefing", "enabled": True,
              "when": {"type": "time", "at": "08:30", "days": "lun-ven"},
              "do": {"type": "ask", "prompt": "briefing"}}]
    engine, sent = make(rules, monkeypatch)
    lundi = datetime(2026, 9, 28, 8, 31)
    assert engine.tick(lundi) == ["b"]
    assert engine.tick(lundi.replace(minute=40)) == []          # pas deux fois le même jour
    assert engine.tick(datetime(2026, 9, 27, 8, 31)) == []      # dimanche : hors jours
    assert sent == [{"cmd": "say", "text": "briefing", "source": "réflexe", "reflex": "b"}]


def test_condition_rearmee_seulement_apres_disparition(monkeypatch):
    rules = [{"id": "d", "name": "Disque", "enabled": True,
              "when": {"type": "disk_below", "path": "/", "gb": 10**9},  # toujours vrai
              "do": {"type": "announce", "text": "{free} Go"}}]
    engine, sent = make(rules, monkeypatch)
    assert engine.tick() == ["d"]
    assert engine.tick() == []  # toujours vrai : pas de répétition toutes les 20 s
    assert sent[0]["cmd"] == "announce" and "Go" in sent[0]["text"]


def test_presence_requise_attend_le_retour(monkeypatch):
    rules = [{"id": "p", "name": "Hello", "enabled": True, "require_presence": True,
              "when": {"type": "time", "at": "09:00"}, "do": {"type": "announce", "text": "coucou"}}]
    monkeypatch.setattr(reflex, "load_rules", lambda: rules)
    state = {"present": False}
    sent = []
    engine = reflex.Reflex(command=sent.append, presence=lambda: (True, state["present"], 0.0))
    t = datetime(2026, 9, 28, 9, 1)
    assert engine.tick(t) == []           # absent : mis en attente
    state["present"] = True
    assert engine.tick(t.replace(minute=5)) == ["p"]
    assert len(sent) == 1


def test_regle_desactivee_ignoree(monkeypatch):
    rules = [{"id": "x", "name": "X", "enabled": False,
              "when": {"type": "disk_below", "gb": 10**9}, "do": {"type": "announce", "text": "x"}}]
    engine, sent = make(rules, monkeypatch)
    assert engine.tick() == [] and sent == []


def test_creation_validee(tmp_path, monkeypatch):
    monkeypatch.setattr(reflex, "RULES_FILE", tmp_path / "r.json")
    reflex.save_rules([])
    reflex.create_rule({"id": "a", "name": "A", "when": {"type": "time", "at": "07:00"},
                        "do": {"type": "notify", "text": "debout"}})
    assert reflex.load_rules()[0]["enabled"] is True
    # « phone » (téléphone, essai du 20/10) retiré : action inconnue désormais.
    for bad in ({"id": "b"}, {"id": "c", "name": "C", "when": {"type": "lune"}, "do": {"type": "announce"}},
                {"id": "d", "name": "D", "when": {"type": "time", "at": "07:00"}, "do": {"type": "phone"}}):
        try:
            reflex.create_rule(bad)
            raise AssertionError("aurait dû refuser")
        except ValueError:
            pass


def test_m4_heure_invalide_refusee_et_regle_abimee_ne_bloque_pas_les_suivantes(monkeypatch, tmp_path):
    """Audit p33-p34, M4 : « 8h30 » refusé à la création ; une règle abîmée (écrite à la main)
    est sautée, les suivantes (brief-matin) partent quand même, sans lever à chaque passage."""
    import pytest

    monkeypatch.setattr(reflex, "RULES_FILE", tmp_path / "reflexes.json")
    reflex.save_rules([])
    for heure in ("8h30", "24:00", "08:60", "", "8:30:00"):
        with pytest.raises(ValueError, match="HH:MM"):
            reflex.create_rule({"id": "bad", "name": "b", "when": {"type": "time", "at": heure},
                                "do": {"type": "announce", "text": "x"}})
    reflex.create_rule({"id": "ok", "name": "o", "when": {"type": "time", "at": "8:30"},
                        "do": {"type": "announce", "text": "o"}})
    rules = [{"id": "bad", "name": "b", "enabled": True, "when": {"type": "time", "at": "8h30"},
              "do": {"type": "announce", "text": "x"}},
             "pas une règle",
             {"id": "apres", "name": "c", "enabled": True, "when": {"type": "time", "at": "08:30"},
              "do": {"type": "announce", "text": "apres"}}]
    engine, sent = make(rules, monkeypatch)
    warnings = []
    monkeypatch.setattr(reflex.log, "warning", lambda *a: warnings.append(a))
    lundi = datetime(2026, 9, 28, 8, 31)
    assert engine.tick(lundi) == ["apres"] and [c["text"] for c in sent] == ["apres"]
    engine.tick(lundi.replace(minute=32))
    assert len(warnings) == 2  # une fois par règle abîmée, pas à chaque passage
