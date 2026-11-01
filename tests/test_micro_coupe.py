"""Micro coupé (🎙) = bouton MUTE d'un appel (15/10, demande de l'utilisateur : « des
fois ça ne fonctionne pas »). Dès la coupure, plus rien n'est écouté ni envoyé,
même si l'utilisateur parlait : phrase en enregistrement ou en transcription jetée,
même si le micro est rallumé avant la fin de la transcription. Le micro ne se
rallume que par un geste exprès de l'utilisateur, et la coupure survit à une relance."""
import threading

import pytest

from astrel import audio, config, core, state
from astrel.events import bus


class FakeSpeaker:
    paused = False
    busy = False
    pending = False

    def __init__(self):
        self.said = []

    def say(self, text, raw=False, **_):
        self.said.append(text)

    def interrupt(self):
        pass

    def wait_idle(self, timeout=None):
        return True


class FakeMic:
    muted = False

    def wait_open(self, timeout=None):
        return True


class FakeRecorder:
    """L'enregistrement d'une phrase : `during` simule ce que fait l'utilisateur
    pendant qu'il parle (clic sur 🎙…), puis la phrase se clôt."""

    def __init__(self, during=None, pcm=b"\x01\x00" * 16000):
        self.during, self.pcm, self.calls = during, pcm, 0

    def record(self, mic, initial_timeout=None, abort=None, on_voice=None):
        self.calls += 1
        if self.during is not None:
            self.during()
        return self.pcm


class FakeStt:
    def __init__(self, text="Merci, Mare.", during=None):
        self.text, self.during, self.calls = text, during, 0

    def transcribe(self, pcm):
        self.calls += 1
        if self.during is not None:
            self.during()
        return self.text


@pytest.fixture
def j(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(audio, "chime", lambda *_: None)
    j = object.__new__(core.Astrel)
    j.voice_out = True
    j.keyboard = False
    j.speaker, j.mic, j.status = FakeSpeaker(), FakeMic(), state.StatePublisher()
    j.status.set(state.SLEEPING)
    j._running, j._off, j._in_conversation, j._followup, j._force_wake = True, False, False, False, False
    j._turn_active, j._turn_source, j._recording = False, "", False
    j._pending_confirm, j._confirm_evt, j._confirm_lock = None, threading.Event(), threading.Lock()
    j._ear = threading.Lock()
    j._usage, j._followup_setting, j._dictation_text = "conversation", 8.0, ""
    j.recorder, j.stt = FakeRecorder(), FakeStt()
    j.sent = []
    j.handle = lambda text, *a, **kw: j.sent.append(text)
    return j


def mute(j):
    return lambda: j.on_command({"cmd": "mute", "_via": "hud", "_from_claude": False})


def mute_then_unmute(j):
    def go():
        j.on_command({"cmd": "mute", "_via": "hud", "_from_claude": False})
        j.on_command({"cmd": "unmute", "_via": "hud", "_from_claude": False})
    return go


# ------------------------------------------------------------------ l'écoute et la transcription

def test_coupe_a_la_cloture_de_la_phrase_rien_n_est_transcrit(j):
    """Phrase close à l'instant de la coupure (l'arrêt de l'écoute n'est regardé
    qu'entre deux blocs de 80 ms) : avant, elle était transcrite, et la
    conversation seule la jetait. Maintenant l'audio est jeté sans transcription."""
    j.recorder = FakeRecorder(during=mute(j))
    assert j.listen_once(abort=j._listen_abort()) == ""
    assert j.stt.calls == 0
    assert j.status.current == state.OFF  # pas de « transcription » par-dessus « coupé »


def test_coupe_puis_rallume_pendant_la_transcription_rien_ne_part(j):
    """Le cas raté : 🎙 coupé puis rallumé pendant que Whisper transcrit (1 à 2 s).
    Avant, la conversation ne voyait plus `_off` et envoyait la phrase à Claude."""
    j.stt = FakeStt(during=mute_then_unmute(j))
    j.conversation(followup=True)
    assert j.sent == [] and not j._off
    assert j.speaker.said == []  # ni « je n'ai pas compris »


def test_coupe_puis_rallume_pendant_l_ecoute_premiere_phrase(j):
    """Écoute après « Astrel » : couper puis rallumer ne doit pas faire dire
    « je n'ai pas compris » (la phrase a été jetée exprès)."""
    j.recorder = FakeRecorder(during=mute_then_unmute(j))
    j.conversation(followup=False)
    assert j.sent == [] and j.speaker.said == [] and j.stt.calls == 0


def test_conversation_normale_inchangee(j):
    heard = ["Quelle heure est-il ?"]
    j.stt = FakeStt()
    j.stt.transcribe = lambda pcm: heard.pop(0) if heard else ""
    j.conversation(followup=True)
    assert j.sent == ["Quelle heure est-il ?"]


def test_pas_d_ecoute_micro_coupe(j):
    j.on_command({"cmd": "mute"})
    assert j.listen_once() == "" and j.recorder.calls == 0


# ------------------------------------------------------------------ dictée, confirmation

def test_dictee_coupe_puis_rallume_rien_n_est_copie(j, monkeypatch):
    j._usage = "dictee"
    dictated = []
    monkeypatch.setattr(j, "_dictate", lambda text: dictated.append(text), raising=False)
    j.stt = FakeStt(text="Bonjour à tous.", during=mute_then_unmute(j))
    j._dictation()
    assert dictated == [] and j.sent == []


def test_reponse_orale_a_une_confirmation_jetee_si_micro_coupe(j):
    """« Oui » dit pendant que l'utilisateur coupe (puis rallume) le micro : la commande
    n'est pas autorisée à la voix, la question reste à l'écran."""
    j.voice_out = False  # la question est imprimée, pas dite
    j.stt = FakeStt(text="Oui.", during=mute_then_unmute(j))
    box, answered = {"allow": None, "by": "", "tag": "confirm-x"}, threading.Event()
    j._confirm_by_voice("Je lance le commit ?", box, answered)
    assert box["allow"] is None and not answered.is_set()


# ------------------------------------------------------------------ ce qui ne rallume pas le micro

def test_geste_holo_ne_rallume_pas_le_micro(j):
    j.on_command({"cmd": "mute"})
    j.on_gesture("wake")
    assert j._off and not j._force_wake and j.status.muted


def test_pas_de_fenetre_de_suite_micro_coupe(j):
    j._followup = True
    assert j._followup_due()
    j.on_command({"cmd": "mute"})
    assert not j._followup and not j._followup_due()
    j._followup = True  # posé par un fil qui ne regardait pas le micro
    assert not j._followup_due()


def test_reveil_en_attente_annule_par_la_coupure(j):
    j._force_wake = True
    j.on_command({"cmd": "mute"})
    assert not j._force_wake


@pytest.mark.parametrize("cmd", ["unmute", "on", "toggle", "wake"])
def test_claude_ne_rallume_pas_le_micro(j, cmd):
    j.on_command({"cmd": "mute", "_from_claude": True})  # couper : toujours permis
    assert j._off
    r = j.on_command({"cmd": cmd, "_from_claude": True, "_via": "socket"})
    assert not r["ok"] and j._off and j.status.muted and core.MUTE_FILE.exists()


def test_utilisateur_rallume_le_micro(j):
    j.on_command({"cmd": "mute"})
    assert j.on_command({"cmd": "toggle", "_from_claude": False, "_via": "hud"}) == {"ok": True, "muted": False}
    assert not j._off and not j.status.muted and j.status.current == state.SLEEPING


def test_reveil_expres_rallume_le_micro_et_ecoute(j):
    j.on_command({"cmd": "mute"})
    assert j.on_command({"cmd": "wake", "_from_claude": False, "_via": "socket"})["ok"]
    assert not j._off and j._force_wake and not j.status.muted and not core.MUTE_FILE.exists()


# ------------------------------------------------------------------ état affiché et relance

def test_etat_muted_publie_a_chaque_changement(j):
    seen = []
    unsub = bus.subscribe(lambda ev: seen.append(ev) if ev.get("type") == "state" else None)
    try:
        j.on_command({"cmd": "mute"})
        assert seen[-1]["muted"] is True and seen[-1]["state"] == state.OFF
        j.status.set(state.SPEAKING)  # Astrel parle micro coupé : l'orbe change, `muted` reste
        assert seen[-1]["muted"] is True
        j.status.set(state.OFF, only=(state.SPEAKING,))
        j._turn_active = True
        j.on_command({"cmd": "unmute"})
    finally:
        unsub()
    # Astrel travaille encore : l'orbe repasse en « réfléchit », pas en veille.
    assert seen[-1]["muted"] is False and seen[-1]["state"] == state.THINKING


def test_micro_coupe_retenu_d_une_relance_a_l_autre(j):
    """Astrel se relance seul plusieurs fois par jour (`restart` après une
    modification) : avant, le micro coupé (en mémoire) se rallumait sans prévenir."""
    j.on_command({"cmd": "mute"})
    assert core.MUTE_FILE.exists()
    j.on_command({"cmd": "unmute"})
    assert not core.MUTE_FILE.exists()
