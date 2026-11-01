"""Groupe « Voix, audio et langue » de la page « Réglages » (17/10, plan P4, lot 5) :
micro, haut-parleur, voix filtrée par la langue, seuil de l'ancien mot d'éveil, essais, langue
de la reconnaissance. Rien de réel : ni pactl, ni parecord, ni la voix."""
import struct
import types

import pytest

from astrel import audio, config, core, listener, micprofile
from astrel.settings import SettingError, reglages
from astrel.voice.engines import VoiceInfo
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)
from conftest import remplacer_coeur

MICS = [{"name": "alsa_input.usb-Generic", "label": "Generic USB", "default": False},
        {"name": "alsa_input.pci-interne", "label": "Micro interne", "default": True}]
VOICES = [VoiceInfo("edge:fr-FR-HenriNeural", "Edge Henri", True),
          VoiceInfo("edge:fr-FR-RemyMultilingualNeural", "Edge Rémy", True),
          VoiceInfo("edge:en-US-AvaNeural", "Edge Ava", True)]


class FakeMic:
    def __init__(self):
        self.source = ""
        self.profile = micprofile.MicProfile()
        self.switched = []

    def switch(self, source):
        self.switched.append(source)
        self.source = source

    def identity(self):
        return self.source or "defaut", "Micro"

    def load_profile(self):
        self.profile = micprofile.resolve(*self.identity())
        return self.profile


class FakeVoice:
    voice = "edge:fr-FR-RemyMultilingualNeural"

    def set_voice(self, spec):
        if not spec.startswith("edge:"):
            raise ValueError(f"voix inconnue : {spec}")
        self.voice = spec
        return spec


@pytest.fixture
def j(astrel, monkeypatch, tmp_path):
    j, said, _ = astrel
    j.mic, j.speaker = FakeMic(), FakeVoice()
    j.voice_out = True
    j._publish_mic = lambda *a: None
    monkeypatch.setattr(config, "MIC_FILE", tmp_path / "mic.txt")
    monkeypatch.setattr(config, "VOICE_FILE", tmp_path / "voice.txt")
    monkeypatch.setattr(micprofile, "FILE", tmp_path / "mic_profiles.json")
    monkeypatch.setattr(audio, "list_mics", lambda: MICS)
    remplacer_coeur(monkeypatch, "available_voices", lambda: VOICES)
    monkeypatch.setattr(core.graph, "set_voice", lambda v: None)
    j._bind_settings()
    return j, said


def item(key):
    return next(i for g in reglages.describe()["groups"] for i in g["items"] if i["key"] == key)


def test_sans_le_coeur_grise():
    it = item("audio.mic")
    assert it["locked"] and it["lock_reason"] == "Réglable quand Astrel tourne."
    with pytest.raises(SettingError, match="Astrel tourne"):
        reglages.set("audio.voice", "edge:fr-FR-HenriNeural")
    assert "lines" in item("audio.test")["value"] and item("audio.test")["actions"] == []


def test_micro(j):
    jv, _ = j
    it = item("audio.mic")
    assert not it["locked"] and it["value"] == ""
    assert [c["value"] for c in it["choices"]] == ["", "alsa_input.usb-Generic", "alsa_input.pci-interne"]
    assert it["choices"][2]["label"] == "Micro interne (micro du système)"
    assert reglages.set("audio.mic", "alsa_input.usb-Generic") == "alsa_input.usb-Generic"
    assert jv.mic.switched == ["alsa_input.usb-Generic"] and config.MIC_FILE.read_text().strip() == "alsa_input.usb-Generic"
    with pytest.raises(SettingError, match="valeur inconnue"):
        reglages.set("audio.mic", "alsa_input.absent")
    assert not config.ASTREL_SETTINGS_FILE.exists()  # rien dans runtime/settings.json


def test_voix_filtree_par_la_langue(j):
    jv, said = j
    assert [c["value"] for c in item("audio.voice")["choices"]] == ["edge:fr-FR-HenriNeural",
                                                                   "edge:fr-FR-RemyMultilingualNeural"]
    reglages.set("audio.voice", "edge:fr-FR-HenriNeural")
    assert jv.speaker.voice == "edge:fr-FR-HenriNeural" and said == ["Voici ma nouvelle voix."]
    assert config.VOICE_FILE.read_text().strip() == "edge:fr-FR-HenriNeural"
    reglages.set("stt_language", "en")
    # anglais : Ava et Rémy (multilingue) ; Henri, la voix en cours, reste en tête
    assert [c["value"] for c in item("audio.voice")["choices"]] == ["edge:fr-FR-HenriNeural",
                                                                   "edge:fr-FR-RemyMultilingualNeural",
                                                                   "edge:en-US-AvaNeural"]


def test_langue_lue_par_la_reconnaissance():
    assert listener.stt_language() == "fr"
    reglages.set("stt_language", "es")
    assert listener.stt_language() == "es"
    with pytest.raises(SettingError):
        reglages.set("stt_language", "klingon")


def test_seuil_du_mot_de_reveil(j):
    jv, _ = j
    assert item("audio.wake")["value"] == pytest.approx(micprofile.MicProfile().wake)
    assert reglages.set("audio.wake", 0.3) == 0.3
    assert jv.mic.profile.wake == pytest.approx(0.3) and "0.3" in micprofile.FILE.read_text()
    with pytest.raises(SettingError, match="au plus"):
        reglages.set("audio.wake", 0.99)


def test_seuil_impose_par_la_variable(j, monkeypatch):
    monkeypatch.setenv("ASTREL_WAKE_THRESHOLD", "0.5")
    it = item("audio.wake")
    assert it["locked"] and "ASTREL_WAKE_THRESHOLD" in it["lock_reason"]


def test_essai_de_la_voix(j):
    jv, said = j
    res = reglages.action("audio.test", "voice", {})
    assert res["ok"] and said[-1].startswith("Bonjour, je suis Astrel")
    jv.voice_out = False
    assert reglages.action("audio.test", "voice", {})["ok"] is False


def _fake_parecord(level_amp):
    pcm = struct.pack("<1600h", *([level_amp, -level_amp] * 800))

    class P:
        def __init__(self, cmd, **_):
            self.cmd = cmd
            self.stdout = types.SimpleNamespace(read=lambda n, _c=[0]: (_c.__setitem__(0, _c[0] + 1) or pcm)
                                                if _c[0] < 5 else b"")

        def kill(self):
            pass

        def wait(self):
            return 0
    return P


def test_essai_du_micro(j, monkeypatch):
    jv, _ = j
    monkeypatch.setattr(core.subprocess, "Popen", _fake_parecord(12000))
    res = reglages.action("audio.test", "mic", {})
    assert res["ok"] and res["level"] == "ok" and "entend bien" in res["text"]
    monkeypatch.setattr(core.subprocess, "Popen", _fake_parecord(60))
    res = reglages.action("audio.test", "mic", {})
    assert res["level"] == "warn" and "Presque rien" in res["text"]
    jv._off = True  # micro coupé par l'utilisateur : pas d'écoute, même d'essai
    assert "micro coupé" in reglages.action("audio.test", "mic", {})["error"]


def test_haut_parleur(monkeypatch):
    reglages._bound.pop("audio.output", None)  # conftest : pactl débranché ; ici, le vrai code sur un faux pactl
    calls = []

    def run(cmd, **_):
        calls.append(cmd)
        out = {"-f": '[{"name": "bluez.JBL", "description": "JBL  Flip"}, {"name": "pci.hp", "description": "Haut-parleurs"}]',
               "get-default-sink": "pci.hp\n"}
        return types.SimpleNamespace(stdout=out.get(cmd[1], ""), stderr="", returncode=0)

    monkeypatch.setattr(audio.subprocess, "run", run)
    it = item("audio.output")
    assert not it["locked"] and it["value"] == "pci.hp"
    assert it["choices"] == [{"value": "bluez.JBL", "label": "JBL Flip"}, {"value": "pci.hp", "label": "Haut-parleurs"}]
    reglages.set("audio.output", "bluez.JBL")
    assert ["pactl", "set-default-sink", "bluez.JBL"] in calls
    with pytest.raises(SettingError):
        reglages.set("audio.output", "absent")


def test_haut_parleur_hors_linux(monkeypatch):
    monkeypatch.setenv("ASTREL_AUDIO_ENGINE", "sounddevice")
    it = item("audio.output")
    assert it["locked"] and "Linux" in it["lock_reason"]


def test_quelle_voix_parle_quelle_langue():
    assert core._speaks("edge:fr-FR-HenriNeural", "fr") and not core._speaks("edge:fr-FR-HenriNeural", "en")
    assert core._speaks("edge:fr-FR-VivienneMultilingualNeural", "de") and core._speaks("gemini:Kore", "it")


# ------------------------------------------------------------------ écoute prolongée (20/10, demande de l'utilisateur)

def test_ecoute_prolongee_interrupteur_et_duree(j, published):
    """Réglages › Voix : durée libre (5 s retenue), 0 = coupée, rallumer = dernière durée ;
    runtime/followup.txt garde son format (un nombre) et reste le seul état qui fait foi."""
    jv, _ = j
    assert item("audio.followup")["value"] is True and item("audio.followup_s")["value"] == 8
    assert item("audio.followup_s")["min"] == 1 and item("audio.followup_s")["max"] == 30
    assert reglages.set("audio.followup_s", 5) == 5
    assert jv._followup_setting == 5 and config.FOLLOWUP_FILE.read_text().strip() == "5"
    assert [e["seconds"] for e in of(published, "followup")][-1] == 5  # le badge de mode suit
    reglages.set("audio.followup", False)
    assert jv._followup_setting == 0 and jv._followup_s() == 0
    assert config.FOLLOWUP_FILE.read_text().strip() == "0"
    assert item("audio.followup")["value"] is False and item("audio.followup_s")["value"] == 5
    reglages.set("audio.followup_s", 12)  # coupée : retenue, pas appliquée
    assert jv._followup_setting == 0 and config.FOLLOWUP_LAST_FILE.read_text().strip() == "12"
    reglages.set("audio.followup", True)
    assert jv._followup_setting == 12 and config.FOLLOWUP_FILE.read_text().strip() == "12"
    for bad in (0, 31, 2.5):
        with pytest.raises(SettingError):
            reglages.set("audio.followup_s", bad)
    assert not config.ASTREL_SETTINGS_FILE.exists()  # rien dans runtime/settings.json


def test_ecoute_prolongee_depuis_le_badge(j):
    """Le menu du badge (commande « followup ») et les Réglages lisent le même état."""
    jv, _ = j
    assert jv.on_command({"cmd": "followup", "seconds": 20})["seconds"] == 20
    assert item("audio.followup_s")["value"] == 20 and item("audio.followup")["value"] is True
    jv.on_command({"cmd": "followup", "seconds": 0})
    assert item("audio.followup")["value"] is False and item("audio.followup_s")["value"] == 20
    reglages.set("audio.followup", True)
    assert jv._followup_setting == 20
    assert jv.on_command({"cmd": "followup", "seconds": "abc"})["ok"] is False


def test_ecoute_prolongee_relue_au_demarrage():
    """Ancien fichier (un nombre) : relu tel quel ; coupée, la dernière durée vient du fichier voisin."""
    assert core._load_followup_last(5.0) == 5.0
    assert core._load_followup_last(0.0) == config.FOLLOWUP_TIMEOUT_S  # rien de retenu : 8 s
    config.FOLLOWUP_LAST_FILE.write_text("15\n", encoding="utf-8")
    assert core._load_followup_last(0.0) == 15.0
    config.FOLLOWUP_LAST_FILE.write_text("0\n", encoding="utf-8")
    assert core._load_followup_last(0.0) == config.FOLLOWUP_TIMEOUT_S
    config.FOLLOWUP_FILE.write_text("5\n", encoding="utf-8")
    assert core._load_followup() == 5.0


def test_ecoute_prolongee_grisee_sans_le_coeur():
    assert item("audio.followup")["locked"] and item("audio.followup_s")["locked"]
    with pytest.raises(SettingError, match="Astrel tourne"):
        reglages.set("audio.followup", False)
