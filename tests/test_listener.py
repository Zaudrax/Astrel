"""Fin de phrase (30/09) : l'utilisateur se sentait coupé en pleine explication.

Causes corrigées (détail dans CLAUDE.md) : durée maximale de 45 s comptée dès
l'ouverture du micro, blanc de 1,0 s pour tout, un seul seuil pour commencer ET
finir la parole, et une phrase jetée quand Astrel parlait par-dessus."""
import pytest

from astrel import config
from astrel.listener import VoiceRecorder, end_threshold

FRAME_S = config.FRAME_SAMPLES / config.SAMPLE_RATE  # 80 ms


class FakeMic:
    """Rejoue une suite de probabilités de parole (une par bloc de 80 ms) ;
    `muted` : indices des blocs où Astrel parle (micro sourd)."""

    def __init__(self, probs, muted=(), vad=0.39):
        self.probs = list(probs)
        self.muted_at = set(muted)
        self.i = -1
        self.profile = type("P", (), {"vad": vad})()

    def read_frame(self):
        self.i += 1
        if self.i >= len(self.probs):
            raise AssertionError("l'enregistrement aurait dû se terminer")
        return self.i.to_bytes(2, "little") * (config.FRAME_BYTES // 2)

    @property
    def muted(self):
        return self.i in self.muted_at


def recorder(mic):
    rec = object.__new__(VoiceRecorder)
    rec._vad = type("V", (), {"reset_states": lambda self: None})()
    rec.speech_prob = lambda frame: mic.probs[mic.i]
    return rec


def frames_of(pcm):
    return [int.from_bytes(pcm[k:k + 2], "little") for k in range(0, len(pcm), config.FRAME_BYTES)]


def n(seconds):
    return round(seconds / FRAME_S)


@pytest.fixture(autouse=True)
def reglages(monkeypatch):
    monkeypatch.setattr(config, "VAD_END_MARGIN", 0.15)
    monkeypatch.setattr(config, "VAD_SILENCE_S", 1.2)
    monkeypatch.setattr(config, "VAD_SILENCE_LONG_S", 2.0)
    monkeypatch.setattr(config, "VAD_LONG_AFTER_S", 3.0)
    monkeypatch.setattr(config, "VAD_MAX_UTTERANCE_S", 120.0)


def test_syllabe_faible_ne_compte_pas_comme_blanc():
    """Silero rend 0,30 sur une syllabe faible : sous le seuil 0,39, mais pas un blanc."""
    assert end_threshold(0.39) == pytest.approx(0.24)
    probs = [0.9] * n(1) + [0.30] * n(1.5) + [0.9] * n(0.5) + [0.0] * n(1.2)
    mic = FakeMic(probs)
    pcm = recorder(mic).record(mic)
    assert len(frames_of(pcm)) == len(probs)  # tout est gardé, fermé au vrai blanc final


def test_ordre_court_reste_rapide():
    probs = [0.9] * n(0.8) + [0.0] * n(1.2) + [0.9] * 10
    mic = FakeMic(probs)
    recorder(mic).record(mic)
    assert mic.i == n(0.8) + n(1.2) - 1  # « vas-y » : fermé après 1,2 s de blanc


def test_pause_de_reflexion_toleree_quand_il_explique():
    """Après plus de 3 s de parole, une pause de 1,8 s ne coupe plus."""
    probs = [0.9] * n(4) + [0.0] * n(1.8) + [0.9] * n(2) + [0.0] * n(2.0) + [0.9] * 10
    mic = FakeMic(probs)
    pcm = recorder(mic).record(mic)
    assert len(frames_of(pcm)) == n(4) + n(1.8) + n(2) + n(2.0)


def test_duree_max_comptee_depuis_le_debut_de_la_parole(monkeypatch):
    """Avant : 45 s depuis l'ouverture du micro, attente comprise, et les longues
    explications de l'utilisateur étaient coupées net."""
    monkeypatch.setattr(config, "VAD_MAX_UTTERANCE_S", 10.0)
    probs = [0.0] * n(8) + [0.9] * n(12)
    mic = FakeMic(probs)
    pcm = recorder(mic).record(mic, initial_timeout=9.0)
    assert len(frames_of(pcm)) == 5 + n(10)  # 400 ms d'amorce + 10 s de parole, l'attente ne compte pas


def test_astrel_parle_par_dessus_la_phrase_est_gardee():
    probs = [0.9] * n(2) + [0.0] * n(3) + [0.9] * n(1) + [0.0] * n(1.2)
    muted = range(n(2), n(2) + n(3))  # Astrel parle 3 s pendant que l'utilisateur est enregistré
    mic = FakeMic(probs, muted=muted)
    kept = frames_of(recorder(mic).record(mic))
    assert kept[:n(2)] == list(range(n(2)))  # le début de sa phrase n'est plus jeté
    assert not set(kept) & set(muted)  # la voix d'Astrel n'est pas dedans


def test_voix_de_astrel_avant_la_parole_ignoree():
    probs = [0.9] * n(1) + [0.0] * n(0.4) + [0.9] * n(1) + [0.0] * n(1.2)
    mic = FakeMic(probs, muted=range(n(1)))
    kept = frames_of(recorder(mic).record(mic))
    assert min(kept) >= n(1)  # sa propre voix, captée avant que l'utilisateur parle, n'y est pas


def test_aucune_parole():
    mic = FakeMic([0.1] * n(3))
    assert recorder(mic).record(mic, initial_timeout=2.0) == b""


def test_lexique_stt_suit_le_nom_affiche_a_chaud(monkeypatch):
    """25/10 (relecture P36, fiche 5) : `assistant_name` se change à chaud ; le lexique de Whisper est
    recalculé à chaque phrase, pas figé au démarrage. Une chaîne posée dans `_prompt` le remplace
    (tools/banc_eveil.py : "" = sans lexique)."""
    from astrel import listener

    seen = []

    class FakeModel:
        def transcribe(self, audio, **kw):
            seen.append(kw["initial_prompt"])
            return [], None

    monkeypatch.setenv("ASTREL_ASSISTANT_NAME", "Astrel")
    tr = listener.Transcriber()
    tr._model = FakeModel()
    tr.transcribe(b"\x00\x00" * 160)
    monkeypatch.setenv("ASTREL_ASSISTANT_NAME", "Nova")
    tr.transcribe(b"\x00\x00" * 160)
    assert seen[0].startswith("Vocabulaire : Astrel") and seen[1].startswith("Vocabulaire : Nova")
    tr._prompt = ""
    tr.transcribe(b"\x00\x00" * 160)
    assert seen[2] == ""
