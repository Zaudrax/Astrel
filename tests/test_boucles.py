"""Détecteur de boucles d'outils : alerte seule, une fois, par agent."""
import threading

from astrel import audio, core, boucles
from astrel.brain import Brain, BrainEvent
from astrel.events import bus


def test_meme_appel_repete():
    g = boucles.LoopGuard(repeat=3, errors=3)
    args = {"command": "eas build:view 42"}
    assert g.check_call("Bash", args) is None
    assert g.check_call("Read", {"file_path": "a"}) is None
    assert g.check_call("Bash", args) is None
    alert = g.check_call("Bash", dict(args))
    assert alert and alert.kind == "repeat" and alert.count == 3 and alert.tool == "Bash"
    assert g.check_call("Bash", args) is None  # une seule alerte


def test_arguments_differents_pas_d_alerte():
    g = boucles.LoopGuard(repeat=3, errors=3)
    for i in range(10):
        assert g.check_call("Read", {"file_path": "a", "offset": i * 100}) is None


def test_portees_separees_et_remise_a_zero():
    g = boucles.LoopGuard(repeat=2, errors=3)
    assert g.check_call("Grep", {"pattern": "x"}, scope="agent1") is None
    assert g.check_call("Grep", {"pattern": "x"}, scope="main") is None
    assert g.check_call("Grep", {"pattern": "x"}, scope="agent1").scope == "agent1"
    g.reset("main")  # fin du tour : le compte d'Astrel repart de zéro
    assert g.check_call("Grep", {"pattern": "x"}, scope="main") is None
    assert g.check_call("Grep", {"pattern": "x"}, scope="main") is not None


def test_erreurs_identiques_en_serie():
    g = boucles.LoopGuard(repeat=9, errors=3)
    assert g.check_result("Bash", True, "Error: port 8081 in use (pid 1234)") is None
    assert g.check_result("Bash", True, "Error: port 8081 in use (pid 5678)") is None
    alert = g.check_result("Bash", True, "Error: port 8081 in use (pid 9999)")
    assert alert and alert.kind == "errors" and alert.count == 3
    # Un succès entre deux casse la série.
    g2 = boucles.LoopGuard(repeat=9, errors=2)
    g2.check_result("Bash", True, "boom")
    g2.check_result("Bash", False, "")
    assert g2.check_result("Bash", True, "boom") is None


def test_message_pour_un_sous_agent():
    hud, nudge = boucles.describe(boucles.Alert("toolu_1", "repeat", "Bash", 3), "Build Android")
    assert "Build Android" in hud and "TaskStop" in nudge and nudge.startswith("[Alerte automatique d'Astrel")


class _Speaker:
    busy = False
    paused = False


class _Status:
    current = "sleeping"

    def set(self, *a, **k):
        return True


def make_astrel(monkeypatch):
    monkeypatch.setattr(audio, "chime", lambda *_: None)
    j = object.__new__(core.Astrel)
    j.voice_out = False
    j.speaker, j.status = _Speaker(), _Status()
    j._turn_active, j._turn_source, j._last_event = False, "", 0.0
    j._turn_idle = threading.Event()
    j._send_lock = threading.Lock()
    from astrel.voice import SentenceSplitter
    j._splitter, j._spoken_any, j._reply, j._hushed = SentenceSplitter(), False, [], False
    sent = []
    j.brain = Brain()
    j.brain.send = lambda text, images=None: sent.append(text)
    return j, sent


def test_le_coeur_alerte_sans_interrompre(monkeypatch):
    j, sent = make_astrel(monkeypatch)
    interrupted = []
    j.brain.interrupt = lambda: interrupted.append(1)
    published = []
    unsub = bus.subscribe(published.append)
    started = threading.Event()
    real_thread = threading.Thread

    class Sync(real_thread):  # le message discret part dans un fil : on l'exécute tout de suite
        def start(self):
            self.run()
            started.set()

    monkeypatch.setattr(core.threading, "Thread", Sync)
    try:
        j._begin_turn("voix")
        for _ in range(3):
            j._on_brain_event(BrainEvent("tool_start", tool="Bash", tool_id="t", text="eas",
                                         data={"input": {"command": "eas build:view 1"}}))
    finally:
        unsub()
    notices = [e for e in published if e["type"] == "notice" and e.get("level") == "warning"]
    assert len(notices) == 1 and "Boucle possible" in notices[0]["text"]
    assert len(sent) == 1 and "[Alerte automatique d'Astrel" in sent[0]
    assert not interrupted and j._turn_active  # alerte seule


def test_alerte_d_un_agent_nommee(monkeypatch):
    j, sent = make_astrel(monkeypatch)
    monkeypatch.setattr(core.threading, "Thread",
                        type("T", (threading.Thread,), {"start": lambda self: self.run()}))
    j._on_brain_event(BrainEvent("tool_start", tool="Agent", tool_id="toolu_A", text="Build Android",
                                 data={"input": {"description": "Build Android"}}))
    for _ in range(3):
        j._on_brain_event(BrainEvent("tool_result", tool="Bash", tool_id="x", text="ENOENT: no such file",
                                     data={"is_error": True, "agent": "toolu_A"}))
    assert len(sent) == 1 and "Build Android" in sent[0] and "TaskStop" in sent[0]
    assert j._turn_active and j._turn_source == "réflexe"  # tour discret ouvert, rien d'interrompu
