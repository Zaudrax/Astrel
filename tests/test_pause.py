"""Pause de la voix, comme un lecteur vidéo (30/09) : le son se coupe NET, le
travail continue, et la reprise redit ce qui n'avait pas été entendu — depuis
le début du mot coupé — puis la suite, dans l'ordre. Rien ne sort pendant la
pause, carillons compris."""
import threading
import time

import numpy as np
import pytest

from astrel import audio, config, core
from astrel.audio import heard_bytes, resume_point
from astrel.voice import SentenceSplitter
from astrel.voice import speaker as speaker_mod


@pytest.fixture(autouse=True)
def _sortie_libre():
    audio.hold_output(False)
    yield
    audio.hold_output(False)


def wait_for(cond, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.005)
    return False


# ------------------------------------------------------------------ où reprendre

def tone(seconds: float, rate: int = 8000, amp: int = 8000) -> bytes:
    t = np.arange(int(seconds * rate)) / rate
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.int16).tobytes()


def silence(seconds: float, rate: int = 8000) -> bytes:
    return bytes(int(seconds * rate) * 2)


def test_programme_des_blocs():
    """Un bloc écrit pendant que le précédent joue sort à sa suite ; un bloc
    écrit après un blanc sort tout de suite."""
    sched = [(10.0, 0, 2000), (11.0, 2000, 1000), (20.0, 3000, 1000)]  # débit 1000 : 2 octets = 1 ms
    assert heard_bytes(sched, 9.0, 1000) == 0
    assert heard_bytes(sched, 10.5, 1000) == 1000
    assert heard_bytes(sched, 11.2, 1000) == 2400
    assert heard_bytes(sched, 15.0, 1000) == 3000  # blanc entre deux phrases
    assert heard_bytes(sched, 99.0, 1000) == 4000


def test_reprise_au_debut_du_mot():
    rate = 8000
    pcm = tone(0.6) + silence(0.1) + tone(0.8)  # « mot1 », blanc, « mot2 »
    cut_at = len(tone(0.6) + silence(0.1) + tone(0.5))  # coupé au milieu de mot2
    at = resume_point(pcm, cut_at, rate)
    start_mot2 = len(tone(0.6) + silence(0.1))
    assert start_mot2 - int(0.1 * rate) * 2 <= at <= start_mot2  # dans le blanc juste avant mot2
    assert at % 2 == 0


def test_reprise_sans_blanc_et_dans_le_silence():
    rate = 8000
    pcm = tone(3.0)
    at = resume_point(pcm, len(pcm) - 2000, rate)
    assert at == len(pcm) - 2000 - int(0.4 * rate) * 2  # pas de blanc : 0,4 s plus tôt
    quiet = silence(1.0)
    assert resume_point(quiet, 4000, rate) == 4000  # coupé dans l'amorce : rien à reculer
    assert resume_point(pcm, 0, rate) == 0


class FakeProc:
    def __init__(self, *a, **kw):
        self.stdin = self
        self.data = bytearray()
        self.killed = False

    def write(self, b):
        if self.killed:
            raise BrokenPipeError
        self.data += b

    def flush(self):
        pass

    def close(self):
        pass

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0

    def communicate(self, data, timeout=None):
        self.data += data


def test_player_coupe_et_rend_le_reste(monkeypatch):
    procs = []
    monkeypatch.setattr(audio.subprocess, "Popen", lambda *a, **kw: procs.append(FakeProc()) or procs[-1])
    p = audio.Player(8000)
    speech = tone(2.0)
    assert p.write(speech)
    rest = p.cut()  # coupé aussitôt : rien n'a encore été entendu
    assert rest == bytes(p._record) and rest.endswith(speech)
    assert p.stopped and procs[0].killed
    assert not p.write(b"\x01\x00")  # plus rien n'est pris après la coupure

    q = audio.Player(8000)
    q.write(speech)
    q._sched = [(time.monotonic() - 100.0, 0, len(q._record))]  # tout est déjà sorti
    assert q.cut() == b""


def test_carillons_muets_en_pause(monkeypatch):
    calls = []
    monkeypatch.setattr(audio.subprocess, "Popen", lambda *a, **kw: calls.append(a) or FakeProc())
    audio.hold_output(True)
    audio.chime("wake")
    audio.chime("confirm")
    assert calls == []
    audio.hold_output(False)
    audio.play_pcm(b"\x00\x00" * 10, 8000)
    assert len(calls) == 1


# ------------------------------------------------------------------ l'orateur

class FakeEngine:
    name = "fake"
    sample_rate = 1000
    online = False

    def __init__(self, gate: threading.Event):
        self.gate = gate
        self.started = threading.Event()

    def load(self):
        pass

    def synthesize(self, text):
        yield f"[{text}|1]".encode()
        self.started.set()
        if text.startswith("Bonjour"):
            self.gate.wait(5)  # la phrase est encore en synthèse quand la pause tombe
        yield f"[{text}|2]".encode()
        yield f"[{text}|3]".encode()


class FakePlayer:
    all: list = []
    heard = 3  # octets déjà entendus au moment d'une coupure

    def __init__(self, rate, on_level=None):
        self.rate = rate
        self.accepted = bytearray()
        self.stopped = False
        FakePlayer.all.append(self)

    def accept(self, pcm):
        if self.stopped:
            return False
        self.accepted += pcm
        return True

    def push(self, pcm):
        return not self.stopped

    def cut(self):
        self.stopped = True
        return bytes(self.accepted[FakePlayer.heard:])

    def kill(self):
        self.stopped = True

    def close(self, wait=True):
        self.stopped = True


@pytest.fixture
def speaker(monkeypatch):
    FakePlayer.all = []
    gate = threading.Event()
    engine = FakeEngine(gate)
    monkeypatch.setattr(speaker_mod, "build_engine", lambda spec: engine)
    monkeypatch.setattr(speaker_mod, "Player", FakePlayer)
    activity = []
    sp = speaker_mod.Speaker("fake", fallback="fake", on_activity=activity.append)
    sp.load()
    yield sp, engine, gate, activity
    gate.set()
    sp.close()


def played() -> bytes:
    return b"".join(bytes(p.accepted) for p in FakePlayer.all)


def test_pause_coupe_net_et_reprend_ou_elle_en_etait(speaker):
    sp, engine, gate, activity = speaker
    sp.say("Bonjour, voici la suite.", raw=True)
    assert engine.started.wait(2) and wait_for(lambda: FakePlayer.all and FakePlayer.all[0].accepted)

    assert sp.pause()
    first = FakePlayer.all[0]
    assert first.stopped  # coupé net, sans attendre la fin de la phrase
    assert audio.output_held()
    gate.set()  # la synthèse de la phrase se termine PENDANT la pause
    sp.say("Deuxième phrase.", raw=True)
    assert wait_for(lambda: activity == [True, False])  # le micro est rendu pendant la pause
    time.sleep(0.1)
    assert len(FakePlayer.all) == 1 and bytes(first.accepted) == b"[Bonjour, voici la suite.|1]"
    assert not sp.busy and sp.wait_idle(0.01) and sp.pending

    assert sp.resume() and not audio.output_held()
    assert wait_for(lambda: sp._idle.is_set(), 3)
    after = b"".join(bytes(p.accepted) for p in FakePlayer.all[1:])
    phrase = "Bonjour, voici la suite."
    # Ce qui n'avait pas été entendu, puis la fin de la phrase, puis la suite : dans l'ordre, sans trou ni doublon.
    assert after == (f"[{phrase}|1]"[FakePlayer.heard:] + f"[{phrase}|2][{phrase}|3]"
                     "[Deuxième phrase.|1][Deuxième phrase.|2][Deuxième phrase.|3]").encode()
    assert activity == [True, False, True, False]


def test_pause_pendant_la_queue_de_son(speaker):
    """Phrase entièrement écrite, mais encore dans le tube quand la pause tombe."""
    sp, _engine, _gate, activity = speaker
    sp.say("Courte.", raw=True)
    assert wait_for(lambda: FakePlayer.all and FakePlayer.all[0].accepted.endswith(b"|3]"))
    assert sp.pause()
    assert wait_for(lambda: activity == [True, False])
    sp.resume()
    assert wait_for(lambda: sp._idle.is_set() and len(FakePlayer.all) == 2)
    assert bytes(FakePlayer.all[1].accepted) == b"[Courte.|1][Courte.|2][Courte.|3]"[FakePlayer.heard:]


def test_pause_sans_rien_a_dire(speaker):
    sp, _engine, _gate, activity = speaker
    assert sp.pause() and not sp.pause()  # déjà en pause
    assert sp.wait_idle(0.01) and not sp.pending
    assert sp.resume() and not sp.resume()
    time.sleep(0.05)
    assert FakePlayer.all == [] and activity == []


def test_chut_pendant_la_pause_jette_tout(speaker):
    sp, engine, gate, _activity = speaker
    sp.say("Bonjour, une longue réponse.", raw=True)
    assert engine.started.wait(2)
    sp.pause()
    gate.set()
    sp.say("Encore une.", raw=True)
    sp.interrupt()  # 🔇 ou ■ : l'utilisateur ne veut plus l'entendre
    assert not sp.pending
    sp.resume()
    time.sleep(0.2)
    assert len(FakePlayer.all) == 1 and sp.wait_idle(0.5)


# ------------------------------------------------------------------ le cœur

class FakeSpeaker:
    def __init__(self):
        self.paused = False
        self.pending = False
        self.said = []
        self.busy = False

    def pause(self):
        changed, self.paused = not self.paused, True
        return changed

    def resume(self):
        changed, self.paused = self.paused, False
        return changed

    def say(self, text, raw=False, **_):
        self.said.append(text)


class FakeStatus:
    current = "sleeping"

    def __init__(self):
        self.paused = None

    def set(self, st=None, paused=None, unless=(), only=(), **_):
        if paused is not None:
            self.paused = paused
        return True


def make_astrel(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PAUSE_FILE", tmp_path / "voice-paused")
    j = object.__new__(core.Astrel)
    j.voice_out = True
    j.speaker, j.status = FakeSpeaker(), FakeStatus()
    j._turn_active, j._turn_source = False, ""
    return j


def test_commande_pause(monkeypatch, tmp_path):
    j = make_astrel(monkeypatch, tmp_path)
    assert j.on_command({"cmd": "pause"}) == {"ok": True, "paused": True, "pending": False}
    assert config.PAUSE_FILE.exists() and j.status.paused is True  # retenue d'une relance à l'autre
    assert j.on_command({"cmd": "pause status"})["paused"] is True
    assert j.on_command({"cmd": "pause toggle"})["paused"] is False
    assert not config.PAUSE_FILE.exists() and j.status.paused is False
    assert j.on_command({"cmd": "pause", "on": True})["paused"] is True  # HUD
    assert j.on_command({"cmd": "resume"})["paused"] is False
    assert not j.on_command({"cmd": "pause n'importe"})["ok"]


def test_claude_ne_reprend_pas_la_voix_seul(monkeypatch, tmp_path):
    j = make_astrel(monkeypatch, tmp_path)
    assert j.on_command({"cmd": "pause", "on": True, "source": "claude"})["ok"]  # se taire : toujours permis
    j._turn_active, j._turn_source = True, "fond"  # fin d'une tâche de fond, l'utilisateur absent
    r = j.on_command({"cmd": "pause", "on": False, "source": "claude"})
    assert not r["ok"] and j.speaker.paused
    j._turn_active, j._turn_source = False, ""  # un réflexe hors tour non plus
    assert not j.on_command({"cmd": "pause", "on": False, "source": "claude"})["ok"]
    j._turn_active, j._turn_source = True, "voix"  # « Astrel, reprends »
    assert j.on_command({"cmd": "pause", "on": False, "source": "claude"})["ok"] and not j.speaker.paused


def test_phrases_de_l_instant_jetees_en_pause(monkeypatch, tmp_path):
    j = make_astrel(monkeypatch, tmp_path)
    j.on_command({"cmd": "pause"})
    j.say("Attention, un commit git. Je continue ?", raw=True, live=True)
    j.say("Voici la fin de ma réponse.")
    assert j.speaker.said == ["Voici la fin de ma réponse."]  # la réponse attend la reprise, pas la question


def test_pas_d_ecoute_relancee_apres_un_tour_dit_en_pause(monkeypatch, tmp_path):
    from astrel.reports import reports

    monkeypatch.setattr(reports, "_mode", "direct")  # compte rendu dit aussitôt (sinon rien n'est dit)
    monkeypatch.setattr(audio, "chime", lambda *_: None)
    j = make_astrel(monkeypatch, tmp_path)
    j._off = j._in_conversation = j._followup = False
    j._turn_idle = threading.Event()
    j._splitter, j._spoken_any, j._reply, j._hushed = SentenceSplitter(), False, [], False
    j.on_command({"cmd": "pause"})
    j._begin_turn("fond")
    j._on_stream(core.BrainEvent("text", text="La tâche de fond est finie. "))
    j._on_stream(core.BrainEvent("turn_end"))
    assert j.speaker.said == ["La tâche de fond est finie."]  # gardée pour la reprise
    assert not j._followup  # pas d'écoute ouverte pour une réponse que personne n'a entendue


# ------------------------------------------------------------------ l'écoute en pause

class FakeMic:
    def wait_open(self, timeout=None):
        return True


def make_conversation(monkeypatch, tmp_path, heard):
    """Un Astrel dont l'oreille rend les phrases de `heard` (fonctions appelées
    avec l'arrêt d'écoute) et qui note ce qui part chez Claude."""
    j = make_astrel(monkeypatch, tmp_path)
    j.speaker.wait_idle = lambda timeout=None: True
    j.mic = FakeMic()
    j._running, j._off, j._in_conversation, j._followup = True, False, False, False
    j._pending_confirm, j._confirm_evt = None, threading.Event()
    sent, listened = [], []

    def listen_once(timeout=None, abort=None):
        listened.append(abort)
        return heard.pop(0)(abort) if heard else ""

    j.listen_once = listen_once
    j.handle = lambda text, *a, **kw: sent.append(text)
    return j, sent, listened


def test_pause_ferme_la_conversation(monkeypatch, tmp_path):
    """Le bug du 30/09 : pause en pleine conversation, et la boucle rouvrait
    l'écoute après chaque réponse (non dite) — « Merci, Mare » partait chez Claude."""
    j = None

    def euh(_abort):
        j.on_command({"cmd": "pause"})  # l'utilisateur met en pause pendant qu'Astrel répond
        return "Euh..."

    j, sent, listened = make_conversation(monkeypatch, tmp_path, [euh, lambda _a: "Merci, Mare."])
    j.conversation(followup=True)
    assert sent == [] and len(listened) == 1  # la phrase finie après la pause est jetée, plus d'écoute
    assert not j._in_conversation


def test_pause_pendant_l_ecoute_coupe_l_enregistrement(monkeypatch, tmp_path):
    j = None

    def pause_puis_parle(abort):
        assert not abort.is_set()
        j.on_command({"cmd": "pause"})
        assert abort.is_set()  # l'enregistrement s'arrête sur la pause, pas au bout du silence
        return "Merci, Mare."

    j, sent, listened = make_conversation(monkeypatch, tmp_path, [pause_puis_parle])
    j.conversation(followup=False)
    assert sent == [] and len(listened) == 1
    assert j.speaker.said == []  # pas de « je n'ai pas compris » non plus


def test_pas_de_conversation_en_pause(monkeypatch, tmp_path):
    j, sent, listened = make_conversation(monkeypatch, tmp_path, [lambda _a: "Bonjour."])
    j.on_command({"cmd": "pause"})
    j.conversation(followup=True)
    assert sent == [] and listened == []


def test_la_conversation_continue_hors_pause(monkeypatch, tmp_path):
    j, sent, listened = make_conversation(monkeypatch, tmp_path, [lambda _a: "Bonjour.", lambda _a: "Et demain ?"])
    j.conversation(followup=True)
    assert sent == ["Bonjour.", "Et demain ?"] and len(listened) == 3  # la 3e écoute n'entend rien : fin


def test_reveil_expres_reprend_la_voix(monkeypatch, tmp_path):
    """En pause, « Astrel » et le geste HOLO sont ignorés ; Ctrl+Alt+J ou un
    double clic sur la bulle sont voulus : la voix reprend, puis l'écoute."""
    j = make_astrel(monkeypatch, tmp_path)
    j._off = j._force_wake = False
    j.on_command({"cmd": "pause"})
    j._followup = True
    j.on_command({"cmd": "pause", "on": True})
    assert not j._followup  # pas de fenêtre d'écoute ouverte pendant la pause
    j._pending_confirm = None
    j.on_gesture("wake")
    assert not j._force_wake and j.speaker.paused
    assert j.on_command({"cmd": "wake"})["ok"]
    assert j._force_wake and not j.speaker.paused and not config.PAUSE_FILE.exists()
