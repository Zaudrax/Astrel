"""Assistant de calibrage du micro (02/11, demande de l'utilisateur) : mesure partagée avec `--calibrate`,
séance du HUD (évènements, annulation), commandes du cœur (refus à Claude, rien d'écrit sans
« Enregistrer », Stop tout), proposition quand un micro sans profil est choisi."""
import io
import json
import threading
import time
import wave

import numpy as np
import pytest

from astrel import audio, calibrage, cli, config, core, micprofile
from astrel.events import bus

FRAME = config.FRAME_SAMPLES


def _bruit(trames, n=1, amp=60.0, seed=1):
    rng = np.random.default_rng(seed)
    return rng.normal(0, amp, size=(trames, FRAME, n))


def _voix(trames, n=1, amp=6000.0, morts=(), seuls_bruit=()):
    """Salves de « voix » (sinus fort) une trame sur deux ; `morts` : canaux à zéro ; `seuls_bruit` :
    canaux sans voix."""
    t = np.arange(FRAME) / config.SAMPLE_RATE
    x = np.zeros((trames, FRAME, n))
    for i in range(trames):
        if i % 2 == 0:
            x[i, :, :] = (amp * np.sin(2 * np.pi * 220 * t))[:, None]
    x += _bruit(trames, n, seed=2)
    for c in morts:
        x[:, :, c] = 0.0
    for c in seuls_bruit:
        x[:, :, c] = _bruit(trames, 1, amp=600.0, seed=3)[:, :, 0]
    return x


def _probs(x):
    """Faux VAD : parole quand la trame est forte."""
    return np.where(np.sqrt((x ** 2).mean(axis=1)) > 1000, 0.9, 0.05)


class FauxVAD:
    def reset_states(self):
        pass

    def predict(self, pcm):
        a = np.asarray(pcm, dtype=np.float64)
        return 0.9 if np.sqrt((a ** 2).mean()) > 1000 else 0.05


class FauxFlux:
    """Capture brute rejouée depuis un tableau trames × échantillons × canaux."""

    def __init__(self, x, lent=0.0):
        self.stdout = io.BytesIO(np.clip(x, -32768, 32767).astype(np.int16).tobytes())
        self.lent = lent
        self.closed = False
        if lent:
            raw = self.stdout

            class Lent:
                def read(_s, n):
                    time.sleep(lent)
                    return raw.read(n)
            self.stdout = Lent()

    def terminate(self):
        self.closed = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.closed = True


# --------------------------------------------------------------------------- mesure

def test_analyser_garde_les_canaux_de_la_voix_et_borne_le_gain():
    bruit, voix = _bruit(20, 4), _voix(40, 4, morts=(1,), seuls_bruit=(3,))
    m = calibrage.analyser(bruit, voix, _probs)
    assert not m.erreur
    assert m.canaux == [0, 2]  # le canal mort et le canal de bruit sont écartés (micro intégré)
    assert calibrage.GAIN_MIN <= m.gain <= calibrage.GAIN_MAX
    assert m.gain == round(min(calibrage.TARGET_P90 / m.voix, calibrage.PIC_MAX / m.pic), 1)
    assert calibrage.VAD_MIN <= m.vad <= calibrage.VAD_MAX
    assert m.rapport > 2 and not m.faible


def test_analyser_rien_entendu_ou_aucun_canal():
    assert calibrage.analyser(_bruit(10), _bruit(20, seed=5), _probs).erreur == calibrage.PRESQUE_RIEN
    m = calibrage.analyser(_bruit(10, 2), np.zeros((20, FRAME, 2)), _probs)
    assert m.erreur == calibrage.AUCUN_CANAL


def test_compter_nom_suit_la_voie_texte_du_mot_d_eveil(monkeypatch):
    monkeypatch.setattr("astrel.eveil.noms_actuels", lambda: ["Astrel"])
    # trois « Astrel » d'environ 0,5 s séparés par 1 s de blanc
    trames = []
    for _ in range(3):
        trames += [np.full(FRAME, 4000.0)] * 6 + [np.zeros(FRAME)] * 12
    x = np.array(trames)
    vus = []

    def transcribe(pcm, hot):
        vus.append(len(pcm))
        return "Astrel."
    assert calibrage.compter_nom(x, 0.35, _probs, transcribe) == 3
    assert len(vus) == 3


# --------------------------------------------------------------------------- CLI inchangée

def _wav(path, x):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(config.SAMPLE_RATE)
        w.writeframes(np.clip(x, -32768, 32767).astype(np.int16).tobytes())


def test_cli_calibrate_garde_ses_messages_et_ecrit_le_profil(tmp_path, monkeypatch, capsys):
    prof = tmp_path / "mic_profiles.json"
    monkeypatch.setattr(micprofile, "FILE", prof)
    son = np.concatenate([_bruit(8).reshape(-1), _voix(16).reshape(-1)])
    _wav(tmp_path / "in.wav", son)
    monkeypatch.setenv("ASTREL_AUDIO_ENGINE", "fichier")
    monkeypatch.setenv("ASTREL_AUDIO_IN", str(tmp_path / "in.wav"))
    monkeypatch.setattr(cli.state, "send_command", lambda *_a, **_k: None)
    monkeypatch.setattr(config, "MIC_SOURCE", "")
    assert cli.calibrate_mode(noise_s=0.5, speech_s=1.2) == 0
    out = capsys.readouterr().out
    for line in ("1/2 Reste silencieux", "2/2 Parle normalement", "Bruit de fond : RMS", "Rapport voix / bruit",
                 "Profil retenu pour ce micro", "Astrel ne tourne pas"):
        assert line in out
    data = json.loads(prof.read_text())
    (saved,) = data["profiles"].values()
    assert set(saved) == {"gain", "vad"}  # jamais le seuil de l'ancien mot d'éveil


# --------------------------------------------------------------------------- séance

class FauxProfil:
    def __init__(self, gain=2.0, vad=0.35, wake=0.5, channels=(), key=""):
        self.gain, self.vad, self.wake, self.channels, self.key = gain, vad, wake, tuple(channels), key

    def describe(self):
        return f"gain ×{self.gain:g}"


def _seance(x, n=1, transcribe=None, lent=0.0, events=None, **kw):
    calls = []
    events = [] if events is None else events
    s = calibrage.Seance("usb.mic", "Generic", n, FauxProfil(), lambda **d: events.append(d),
                         vad=FauxVAD, transcribe=transcribe, debut=lambda: calls.append("debut"),
                         fin=lambda: calls.append("fin"), bruit_s=0.4, parole_s=1.6,
                         flux=lambda _src, _n: FauxFlux(x, lent), **kw)
    return s, events, calls


def test_seance_mesure_puis_propose_sans_rien_ecrire(tmp_path, monkeypatch):
    monkeypatch.setattr(micprofile, "FILE", tmp_path / "mic_profiles.json")
    monkeypatch.setattr("astrel.eveil.noms_actuels", lambda: ["Astrel"])
    x = np.concatenate([_bruit(5), _voix(20)])
    s, events, calls = _seance(x, transcribe=lambda pcm, hot: "Astrel")
    s.start()
    calibrage.attendre(s, 20)
    etats = [e["etat"] for e in events]
    assert etats[0] == "bruit" and "parole" in etats and etats[-1] == "resultat"
    assert all(0.0 <= e["niveau"] <= 1.0 for e in events if "niveau" in e)
    assert calls == ["debut", "fin"]
    res = events[-1]
    assert res["ancien"]["gain"] == 2.0 and res["propose"]["wake"] == 0.5  # ancien mot d'éveil inchangé
    assert res["nom"]["sur"] == 3 and res["nom"]["propose"] >= 1
    assert s.proposition == res["propose"]
    assert not (tmp_path / "mic_profiles.json").exists()  # rien d'écrit sans « Enregistrer »


def test_seance_annulee_pendant_la_mesure_retablit_l_ecoute():
    x = np.concatenate([_bruit(5), _voix(20)])
    s, events, calls = _seance(x, lent=0.02)
    s.start()
    time.sleep(0.15)
    assert s.annuler("Stop tout") is True
    calibrage.attendre(s, 5)
    assert events[-1]["etat"] == "annule" and s.proposition is None
    assert calls == ["debut", "fin"]


def test_seance_erreur_de_mesure_dite_au_hud():
    x = np.concatenate([_bruit(5), _bruit(20, seed=9)])
    s, events, calls = _seance(x)
    s.start()
    calibrage.attendre(s, 10)
    assert events[-1]["etat"] == "erreur" and events[-1]["erreur"] == calibrage.PRESQUE_RIEN
    assert calls == ["debut", "fin"] and s.proposition is None


# --------------------------------------------------------------------------- cœur

class Speaker:
    def __init__(self):
        self.paused, self.busy, self.log = False, False, []

    def pause(self):
        self.log.append("pause")
        was, self.paused = self.paused, True
        return not was

    def resume(self):
        self.log.append("resume")
        was, self.paused = self.paused, False
        return was


class Mic:
    def __init__(self, profile=None):
        self.source = "usb.mic"
        self.profile = profile or FauxProfil()
        self.switched, self.loaded = [], 0

    def identity(self):
        return self.source, "Generic"

    def switch(self, source):
        self.switched.append(source)
        self.load_profile()

    def load_profile(self):
        self.loaded += 1
        self.profile = micprofile.resolve(*self.identity())
        return self.profile


class Wake:
    _text = None

    def __init__(self):
        self.resets = 0

    def reset(self):
        self.resets += 1


def _coeur(monkeypatch):
    a = core.Astrel.__new__(core.Astrel)
    a.speaker, a.mic, a.wake, a.recorder = Speaker(), Mic(), Wake(), object()
    a._off = a._in_conversation = a._recording = a._followup = False
    a._confirm_evt = threading.Event()
    a._calibrage = None
    monkeypatch.setattr(type(a), "_calibrage_vad", staticmethod(lambda: FauxVAD()))
    return a


@pytest.fixture
def lieux(tmp_path, monkeypatch):
    monkeypatch.setattr(micprofile, "FILE", tmp_path / "mic_profiles.json")
    monkeypatch.setattr(config, "ESTOP_FILE", tmp_path / "ESTOP")
    monkeypatch.setattr(calibrage, "BRUIT_S", 0.4)
    monkeypatch.setattr(calibrage, "PAROLE_S", 1.6)
    monkeypatch.setattr(audio, "source_channels", lambda _n: 1)
    seen = []
    monkeypatch.setattr(bus, "publish", lambda kind, **d: seen.append({"type": kind, **d}) or d)
    return tmp_path, seen


def _flux(monkeypatch, x, lent=0.0):
    monkeypatch.setattr(calibrage, "ouvrir_flux", lambda _s, _n: FauxFlux(x, lent))


def test_coeur_refuse_le_calibrage_a_claude(lieux, monkeypatch):
    a = _coeur(monkeypatch)
    for name in ("calibrage_start", "calibrage_save", "calibrage_cancel"):
        r = a._calibrage_command(name, {"_from_claude": True})
        assert r["ok"] is False and "refusé à Claude" in r["error"]
    assert a._calibrage is None
    assert any(e["type"] == "notice" for e in lieux[1])


def test_coeur_calibrage_complet_ecrit_seulement_apres_enregistrer(lieux, monkeypatch):
    tmp, seen = lieux
    a = _coeur(monkeypatch)
    _flux(monkeypatch, np.concatenate([_bruit(5), _voix(20)]), lent=0.01)
    assert a._calibrage_command("calibrage_save", {})["ok"] is False  # rien à enregistrer
    assert a._calibrage_command("calibrage_start", {})["ok"] is True
    deadline = time.time() + 5
    while not a._calibrating and time.time() < deadline:
        time.sleep(0.01)
    assert a._calibrating and a.speaker.paused  # écoute suspendue, rien n'est dit
    calibrage.attendre(a._calibrage, 20)
    assert not a._calibrating and a.speaker.log == ["pause", "resume"] and a.wake.resets == 1
    assert a._calibrage.etat == "resultat"
    assert not (tmp / "mic_profiles.json").exists()
    r = a._calibrage_command("calibrage_save", {})
    assert r["ok"] is True
    saved = json.loads((tmp / "mic_profiles.json").read_text())["profiles"]["usb.mic"]
    assert set(saved) == {"gain", "vad"}
    assert a.mic.loaded == 1 and a._calibrage is None
    assert any(e["type"] == "calibrage" and e["etat"] == "enregistre" for e in seen)


def test_coeur_voix_deja_en_pause_reste_en_pause(lieux, monkeypatch):
    a = _coeur(monkeypatch)
    a.speaker.paused = True
    _flux(monkeypatch, np.concatenate([_bruit(5), _voix(20)]))
    a._calibrage_command("calibrage_start", {})
    calibrage.attendre(a._calibrage, 20)
    assert a.speaker.paused and "resume" not in a.speaker.log


def test_coeur_refus_micro_coupe_ecoute_en_cours_et_estop(lieux, monkeypatch):
    tmp, _seen = lieux
    a = _coeur(monkeypatch)
    a._off = True
    assert "micro coupé" in a._calibrage_command("calibrage_start", {})["error"]
    a._off, a._in_conversation = False, True
    assert "écoute est en cours" in a._calibrage_command("calibrage_start", {})["error"]
    a._in_conversation = False
    (tmp / "ESTOP").write_text("x")
    assert "arrêt d'urgence" in a._calibrage_command("calibrage_start", {})["error"]


def test_coeur_stop_tout_annule_le_calibrage(lieux, monkeypatch):
    tmp, seen = lieux
    a = _coeur(monkeypatch)
    _flux(monkeypatch, np.concatenate([_bruit(5), _voix(20)]), lent=0.03)
    a._calibrage_command("calibrage_start", {})
    seance = a._calibrage
    time.sleep(0.1)
    a._calibrage_estop()
    calibrage.attendre(seance, 5)
    assert seance.etat == "annule" and not a._calibrating and a.speaker.log[-1] == "resume"
    assert a._calibrage_command("calibrage_save", {})["ok"] is False
    assert not (tmp / "mic_profiles.json").exists()


def test_coeur_canaux_changes_rouvrent_le_flux(lieux, monkeypatch):
    tmp, _seen = lieux
    a = _coeur(monkeypatch)
    monkeypatch.setattr(audio, "source_channels", lambda _n: 4)
    _flux(monkeypatch, np.concatenate([_bruit(5, 4), _voix(20, 4, morts=(1,), seuls_bruit=(3,))]))
    a._calibrage_command("calibrage_start", {})
    calibrage.attendre(a._calibrage, 20)
    assert a._calibrage.proposition["canaux"] == [0, 2]
    assert a._calibrage_command("calibrage_save", {})["ok"] is True
    assert a.mic.switched == ["usb.mic"]
    assert json.loads((tmp / "mic_profiles.json").read_text())["profiles"]["usb.mic"]["channels"] == [0, 2]


def test_coeur_micro_change_entre_mesure_et_enregistrement(lieux, monkeypatch):
    a = _coeur(monkeypatch)
    _flux(monkeypatch, np.concatenate([_bruit(5), _voix(20)]))
    a._calibrage_command("calibrage_start", {})
    calibrage.attendre(a._calibrage, 20)
    a.mic.source = "autre.mic"
    assert "micro a changé" in a._calibrage_command("calibrage_save", {})["error"]


def test_micro_sans_profil_propose_l_assistant(lieux, monkeypatch):
    _tmp, seen = lieux
    a = _coeur(monkeypatch)
    a._calibrage_propose_si_besoin()
    # (seulement nos évènements : un fil d'un autre test peut publier pendant celui-ci)
    assert [e for e in seen if e["type"] == "calibrage_propose"][0]["micro"] == "Generic"
    seen.clear()
    a.mic.profile = FauxProfil(key="generic")
    a._calibrage_propose_si_besoin()
    assert not [e for e in seen if e["type"] == "calibrage_propose"]


def test_vue_des_reglages_ouvre_l_assistant_du_hud(lieux, monkeypatch):
    a = _coeur(monkeypatch)
    v = a._calibrage_view()
    assert v["actions"] == [{"id": "ouvrir", "label": "Calibrer ce micro", "hud": "calibrage"}]
    assert v["value"]["lines"][0]["level"] == "warn"
    a.mic.profile = FauxProfil(key="generic")
    assert a._calibrage_view()["value"]["lines"][0]["level"] == "ok"


def test_followup_ferme_pendant_une_mesure(monkeypatch):
    a = _coeur(monkeypatch)
    a._followup = True
    a.speaker.busy = False

    class M:
        muted = False
    a.mic = M()
    a._calib_suspensions = 1
    assert a._followup_due() is False


# --------------------------------------------------------------------------- relecture du 02/11

class FluxBloquant:
    """Micro qui cale (parecord sans données) : `read` bloque jusqu'à `terminate`."""

    def __init__(self):
        self._closed = threading.Event()
        outer = self

        class Out:
            def read(_s, n):
                outer._closed.wait(30)
                return b""
        self.stdout = Out()

    def terminate(self):
        self._closed.set()

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self._closed.set()


def test_annuler_debloque_une_capture_qui_cale():
    calls = []
    events = []
    s = calibrage.Seance("usb.mic", "Generic", 1, FauxProfil(), lambda **d: events.append(d), vad=FauxVAD,
                         debut=lambda: calls.append("debut"), fin=lambda: calls.append("fin"),
                         bruit_s=0.4, parole_s=1.6, flux=lambda _s, _n: FluxBloquant())
    s.start()
    deadline = time.time() + 3
    while "debut" not in calls and time.time() < deadline:
        time.sleep(0.01)
    time.sleep(0.1)
    s.annuler("Stop tout")
    s.join(3)
    assert not s.vivante and calls == ["debut", "fin"]  # l'écoute est rétablie
    assert events[-1]["etat"] == "annule" and not any(e["etat"] == "erreur" for e in events)


def test_refaire_juste_apres_annuler_ne_laisse_pas_la_voix_en_pause(lieux, monkeypatch):
    from astrel.floor import floor

    a = _coeur(monkeypatch)
    _flux(monkeypatch, np.concatenate([_bruit(5), _voix(20)]), lent=0.05)
    before = floor._listening
    a._calibrage_command("calibrage_start", {})
    time.sleep(0.2)
    a._calibrage_command("calibrage_cancel", {})
    assert a._calibrage_command("calibrage_start", {})["ok"] is True  # « Refaire » tout de suite
    calibrage.attendre(a._calibrage, 20)
    assert not a._calibrating and not a.speaker.paused and floor._listening == before
    assert a.speaker.log == ["pause", "resume", "pause", "resume"]


def test_couper_le_micro_annule_le_calibrage(lieux, monkeypatch, tmp_path):
    a = _coeur(monkeypatch)
    a._mute_gen = 0
    a.status = type("St", (), {"set": lambda self, *x, **k: None})()
    monkeypatch.setattr(core, "MUTE_FILE", tmp_path / "mic-muted")
    _flux(monkeypatch, np.concatenate([_bruit(5), _voix(20)]), lent=0.03)
    a._calibrage_command("calibrage_start", {})
    seance = a._calibrage
    time.sleep(0.15)
    a._mute()
    seance.join(5)
    assert seance.etat == "annule" and seance.raison == "micro coupé" and not a._calibrating
    assert a._calibrage is None and a.speaker.log[-1] == "resume"


def test_enregistrer_refuse_micro_coupe_ou_stop_tout(lieux, monkeypatch):
    tmp, _seen = lieux
    a = _coeur(monkeypatch)
    _flux(monkeypatch, np.concatenate([_bruit(5), _voix(20)]))
    a._calibrage_command("calibrage_start", {})
    calibrage.attendre(a._calibrage, 20)
    a._off = True
    assert "micro coupé" in a._calibrage_command("calibrage_save", {})["error"]
    a._off = False
    (tmp / "ESTOP").write_text("x")
    assert "arrêt d'urgence" in a._calibrage_command("calibrage_save", {})["error"]
    (tmp / "ESTOP").unlink()
    a._in_conversation = True
    assert "écoute est en cours" in a._calibrage_command("calibrage_save", {})["error"]
    assert not (tmp / "mic_profiles.json").exists()


def test_enregistrer_garde_les_autres_profils_et_le_seuil_de_reveil(lieux, monkeypatch):
    tmp, _seen = lieux
    existant = {"default": {"gain": 2.0, "vad": 0.35, "wake": 0.5},
                "profiles": {"generic": {"gain": 2.0, "vad": 0.35, "wake": 0.5},
                             "skl_hda": {"gain": 0.5, "vad": 0.39, "channels": [0, 2]},
                             "usb.mic": {"gain": 3.0, "vad": 0.3, "wake": 0.45}}}
    (tmp / "mic_profiles.json").write_text(json.dumps(existant))
    a = _coeur(monkeypatch)
    a.mic.profile = micprofile.resolve("usb.mic", "Generic")
    _flux(monkeypatch, np.concatenate([_bruit(5), _voix(20)]))
    a._calibrage_command("calibrage_start", {})
    calibrage.attendre(a._calibrage, 20)
    prop = a._calibrage.proposition
    assert a._calibrage_command("calibrage_save", {})["ok"] is True
    data = json.loads((tmp / "mic_profiles.json").read_text())
    assert data["default"] == existant["default"]
    assert data["profiles"]["skl_hda"] == existant["profiles"]["skl_hda"]
    # « Generic » est le nom lisible : le profil « generic » (le plus long qui couvre ce micro) est mis à jour
    key = micprofile.resolve("usb.mic", "Generic").key
    autres = {k: v for k, v in data["profiles"].items() if k != key}
    assert autres == {k: v for k, v in existant["profiles"].items() if k != key}
    assert data["profiles"][key]["wake"] == existant["profiles"][key]["wake"]  # seuil de réveil intact
    assert data["profiles"][key]["gain"] == prop["gain"] and data["profiles"][key]["vad"] == prop["vad"]
