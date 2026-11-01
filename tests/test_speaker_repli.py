"""Voix de secours (13/10) : un 429 ou une coupure réseau passagère a droit à
un nouvel essai rapide avant de basculer ; après un échec définitif, le
disjoncteur met Edge au repos (plus longtemps à chaque rechute) au lieu de le
retenter à chaque phrase, puis son retour est journalisé."""
import logging
import types

import aiohttp
import pytest

from astrel.voice import speaker as speaker_mod


def handshake(status, retry_after=None):
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    return aiohttp.WSServerHandshakeError(
        request_info=None, history=(), status=status, message="Invalid response status", headers=headers)


class ScriptedEngine:
    """Rejoue un scénario : chaque appel prend l'issue suivante (exception ou succès)."""

    sample_rate = 1000
    online = True

    def __init__(self, name, outcomes=()):
        self.name = name
        self.outcomes = list(outcomes)
        self.calls = []

    def load(self):
        pass

    def synthesize(self, text):
        self.calls.append(text)
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, BaseException):
            raise outcome
        yield f"[{self.name}:{text}]".encode()


class FakePlayer:
    def __init__(self, rate, on_level=None):
        self.rate = rate
        self.stopped = False

    def accept(self, pcm):
        return True

    def push(self, pcm):
        return True

    def close(self, wait=True):
        self.stopped = True

    def kill(self):
        self.stopped = True


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


@pytest.fixture
def rig(monkeypatch):
    edge, piper = ScriptedEngine("edge"), ScriptedEngine("piper")
    monkeypatch.setattr(speaker_mod, "build_engine", lambda spec: edge if spec == "edge" else piper)
    monkeypatch.setattr(speaker_mod, "Player", FakePlayer)
    clock = Clock()
    monkeypatch.setattr(speaker_mod, "time", types.SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep))
    sp = speaker_mod.Speaker("edge", fallback="piper")  # pas de load() : on appelle _speak à la main
    return sp, edge, piper, clock


def say(sp, text):
    sp._speak(text, sp._generation)


def test_erreurs_passageres_ou_non():
    assert speaker_mod.is_transient(handshake(429))
    assert speaker_mod.is_transient(handshake(503))
    assert not speaker_mod.is_transient(handshake(403))
    assert speaker_mod.is_transient(aiohttp.ServerDisconnectedError())
    assert speaker_mod.is_transient(OSError("Temporary failure in name resolution"))
    assert not speaker_mod.is_transient(RuntimeError("Edge n'a renvoyé aucun audio"))
    assert speaker_mod.retry_after(handshake(429, 30)) == 30.0
    assert speaker_mod.retry_after(handshake(429)) is None


def test_429_passager_rattrape_par_un_nouvel_essai(rig):
    sp, edge, piper, clock = rig
    edge.outcomes = [handshake(429)]
    say(sp, "Bonjour.")
    assert len(edge.calls) == 2 and piper.calls == []
    assert clock.slept == [speaker_mod.RETRY_DELAYS_S[0]]
    assert sp._failures == 0 and sp.voice == "edge"


def test_echec_definitif_bascule_puis_disjoncteur(rig, caplog):
    sp, edge, piper, clock = rig
    edge.outcomes = [handshake(429)] * 3
    with caplog.at_level(logging.INFO, logger="astrel.speaker"):
        say(sp, "Un.")
    assert len(edge.calls) == 3 and piper.calls == ["Un."]  # 1 essai + 2 nouveaux, puis secours
    assert sp.voice == "piper" and sp._failures == 1
    assert sp._degraded_until - clock.now == pytest.approx(speaker_mod.COOLDOWN_BASE_S)
    assert "nouvel essai dans 90 s" in caplog.text and "HTTP 429" in caplog.text

    # Pendant le repos : Edge n'est plus du tout sollicité, aucune attente ajoutée.
    slept = list(clock.slept)
    say(sp, "Deux.")
    assert len(edge.calls) == 3 and piper.calls == ["Un.", "Deux."] and clock.slept == slept


def test_rechute_allonge_le_repos_et_sonde_sans_nouvel_essai(rig):
    sp, edge, piper, clock = rig
    edge.outcomes = [handshake(403)]  # pas passager : bascule tout de suite
    say(sp, "Un.")
    assert len(edge.calls) == 1 and clock.slept == []

    clock.now = sp._degraded_until + 1
    edge.outcomes = [handshake(429)]  # la sonde échoue : une seule tentative, repos doublé
    say(sp, "Deux.")
    assert len(edge.calls) == 2 and clock.slept == []
    assert sp._failures == 2
    assert sp._degraded_until - clock.now == pytest.approx(2 * speaker_mod.COOLDOWN_BASE_S)


def test_retry_after_long_respecte(rig):
    sp, edge, piper, clock = rig
    edge.outcomes = [handshake(429, retry_after=300)]
    say(sp, "Un.")
    assert len(edge.calls) == 1 and clock.slept == []  # inutile de réessayer dans 0,3 s
    assert sp._degraded_until - clock.now == pytest.approx(300)


def test_retour_sur_edge_journalise(rig, caplog):
    sp, edge, piper, clock = rig
    edge.outcomes = [OSError("Temporary failure in name resolution")] * 3
    say(sp, "Un.")
    assert sp.voice == "piper"
    clock.now = sp._degraded_until + 1
    with caplog.at_level(logging.INFO, logger="astrel.speaker"):
        say(sp, "Deux.")
    assert edge.calls[-1] == "Deux." and piper.calls == ["Un."]
    assert sp._failures == 0 and sp.voice == "edge"
    assert "voix edge de retour" in caplog.text


def test_pas_de_nouvel_essai_hors_budget(rig, monkeypatch):
    sp, edge, piper, clock = rig

    def slow_fail(text):
        edge.calls.append(text)
        clock.now += 3.0  # la connexion a traîné : réessayer ajouterait trop de latence
        raise handshake(503)
        yield  # pragma: no cover

    monkeypatch.setattr(edge, "synthesize", slow_fail)
    say(sp, "Un.")
    assert len(edge.calls) == 1 and piper.calls == ["Un."]
