"""Lecture du cerveau en continu, sur un enregistrement RÉEL du CLI
(`tests/fixtures/claude-stream-fond.jsonl`, 26/09/2026) : une commande lancée en
arrière-plan, le tour qui se termine, puis le tour que le CLI relance SEUL
quand la commande a fini. Avant la lecture en continu, ce second tour était
jeté : Astrel ne prévenait jamais de la fin d'une tâche de fond."""
import json
import queue
import time
from pathlib import Path

from astrel import audio, core, state
from astrel.brain import Brain, task_info
from astrel.events import bus
from astrel.voice import SentenceSplitter

FIXTURE = Path(__file__).parent / "fixtures" / "claude-stream-fond.jsonl"


def brain_events() -> list:
    b = Brain()
    b._events = queue.Queue()
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        b._events.put(json.loads(line))
    out = []
    for ev in b.events(idle_s=0.01):
        if ev.kind == "idle":
            return out
        out.append(ev)


def test_deux_tours_dont_un_spontane():
    kinds = [e.kind for e in brain_events()]
    assert kinds.count("turn_end") == 2
    second = kinds[kinds.index("turn_end") + 1:]
    assert "message_start" in second and "text" in second


def test_taches_traduites():
    tasks = [e.data for e in brain_events() if e.kind == "task"]
    assert tasks[0]["phase"] == "start" and tasks[0]["background"]
    assert "6 seconds" in tasks[0]["desc"]
    assert tasks[-1]["phase"] == "end" and tasks[-1]["id"] == tasks[0]["id"]


def test_etape_sans_prefixe_anglais():
    info = task_info("task_progress", {"task_id": "a1", "description": "Running Lister les fichiers",
                                       "subagent_type": "general-purpose", "usage": {"tool_uses": 2}})
    assert info["step"] == "Lister les fichiers" and info["kind"] == "agent" and info["tool_uses"] == 2


class FakeSpeaker:
    busy = False
    paused = False


class FakeStatus:
    current = state.SLEEPING

    def set(self, st=None, unless=(), only=(), **_):
        if self.current in unless or (only and self.current not in only):
            return False
        if st is not None:
            self.current = st
        return True


def make_astrel(monkeypatch):
    monkeypatch.setattr(audio, "chime", lambda *_: None)
    j = object.__new__(core.Astrel)
    j.voice_out = False
    j.speaker, j.status = FakeSpeaker(), FakeStatus()
    j.brain = Brain()  # sans process : l'init ne fait que publier le modèle
    j._off = j._in_conversation = j._followup = j._recording = False
    j._turn_active, j._turn_source, j._last_event, j._last_turn = False, "", 0.0, 0.0
    import threading
    j._turn_idle = threading.Event()
    j._turn_idle.set()
    j._splitter, j._spoken_any, j._reply, j._hushed = SentenceSplitter(), False, [], False
    j._tasks, j._tasks_lock = {}, threading.Lock()
    said = []
    j.say = lambda text, raw=False, **_: said.append(text)
    return j, said


def test_fin_de_tache_annoncee(monkeypatch):
    """Réglage « direct » : le compte rendu est dit aussitôt (le défaut, « à la
    demande », est dans test_reports.py)."""
    from astrel.reports import reports

    monkeypatch.setattr(reports, "_mode", "direct")
    j, said = make_astrel(monkeypatch)
    published = []
    unsub = bus.subscribe(lambda ev: published.append(ev))
    try:
        j._begin_turn("voix")
        for ev in brain_events():
            j._on_stream(ev)
    finally:
        unsub()
    text = " ".join(said)
    assert "Lancé" in text and "finie" in text  # le second tour est DIT
    assert not j._turn_active and j._followup
    tasks = [ev for ev in published if ev["type"] == "tasks"]
    assert tasks[0]["items"] and tasks[-1]["items"] == []  # la tâche apparaît puis disparaît


def test_tour_bloque_interrompu(monkeypatch):
    j, said = make_astrel(monkeypatch)
    interrupted = []
    j.brain = type("B", (), {"interrupt": lambda self: interrupted.append(1), "alive": True})()
    j._on_turn_error = lambda ev: said.append(ev.text)
    j._begin_turn("clavier")
    j._last_event = time.time() - 10_000
    j._on_stream(core.BrainEvent("idle"))
    assert interrupted and not j._turn_active


def test_chut_tait_le_reste_du_tour(monkeypatch):
    """Le bouton 🔇 : la suite du tour n'est plus dite, mais le tour continue."""
    j, said = make_astrel(monkeypatch)
    j.speaker.interrupt = lambda: None
    j._begin_turn("voix")
    j._on_stream(core.BrainEvent("text", text="Première phrase dite. "))
    j.on_command({"cmd": "hush"})
    j._on_stream(core.BrainEvent("text", text="Celle-ci est tue. "))
    assert said == ["Première phrase dite."] and j._turn_active
    j._begin_turn("voix")  # l'utilisateur reparle : on lui répond à voix haute
    j._on_stream(core.BrainEvent("text", text="Réponse à la relance. "))
    j._on_stream(core.BrainEvent("turn_end"))
    assert said[-1] == "Réponse à la relance."


def test_ecoute_prioritaire_sur_travail_de_fond(monkeypatch):
    """27/09 : l'utilisateur interpelle Astrel en plein travail de fond ; un outil qui
    démarre ne doit pas repasser la bulle en violet pendant qu'il est écouté."""
    j, _ = make_astrel(monkeypatch)
    j.mic = type("M", (), {"hold_mute": lambda s: None, "release_mute": lambda s: None})()
    j._begin_turn("voix")
    j.status.current = state.LISTENING
    j._recording = True
    j._on_stream(core.BrainEvent("tool_pending", tool="Bash", tool_id="t1"))
    j._on_stream(core.BrainEvent("tool_start", tool="Bash", tool_id="t1", text="npm test"))
    assert j.status.current == state.LISTENING
    j._on_stream(core.BrainEvent("tool_result", tool="Bash", tool_id="t1"))
    assert j.status.current == state.LISTENING
    # Astrel parle pendant l'enregistrement, puis se tait : retour à l'écoute.
    j._on_speech_activity(True)
    assert j.status.current == state.SPEAKING
    j._on_speech_activity(False)
    assert j.status.current == state.LISTENING
    # Hors écoute, l'activité de fond s'affiche toujours.
    j._recording = False
    j.status.current = state.THINKING
    j._on_stream(core.BrainEvent("tool_start", tool="Bash", tool_id="t2", text="ls"))
    assert j.status.current == state.WORKING
    j._on_stream(core.BrainEvent("tool_result", tool="Bash", tool_id="t2"))
    assert j.status.current == state.THINKING


def test_etat_conditionnel_atomique(monkeypatch, tmp_path):
    from astrel import config

    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "state.json")
    s = state.StatePublisher()
    s.set(state.LISTENING)
    assert not s.set(state.WORKING, unless=(state.ASKING, state.LISTENING))
    assert s.current == state.LISTENING
    assert not s.set(state.THINKING, only=(state.WORKING,))
    assert s.set(state.THINKING, only=(state.LISTENING,)) and s.current == state.THINKING
