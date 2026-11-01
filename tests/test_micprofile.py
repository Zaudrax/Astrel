import json

from astrel import micprofile


def _write(tmp_path, monkeypatch, data):
    f = tmp_path / "mic_profiles.json"
    f.write_text(json.dumps(data))
    monkeypatch.setattr(micprofile, "FILE", f)
    for var in ("ASTREL_MIC_GAIN", "ASTREL_VAD_THRESHOLD", "ASTREL_WAKE_THRESHOLD"):
        monkeypatch.delenv(var, raising=False)
    return f


def test_le_profil_le_plus_precis_gagne_et_complete_le_defaut(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {
        "default": {"gain": 2.0, "vad": 0.35, "wake": 0.4},
        "profiles": {"usb": {"gain": 3.0}, "usb-generic": {"gain": 1.5, "vad": 0.3}},
    })
    p = micprofile.resolve("alsa_input.usb-Autre_Mic-00.mono-fallback")
    assert (p.key, p.gain, p.vad, p.wake) == ("usb", 3.0, 0.35, 0.4)
    p = micprofile.resolve("alsa_input.usb-Generic", "Micro USB-Generic")
    assert (p.key, p.gain, p.vad) == ("usb-generic", 1.5, 0.3)


def test_micro_inconnu_ou_fichier_absent_donne_le_defaut(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"default": {"gain": 5.0}, "profiles": {}})
    assert micprofile.resolve("autre").gain == 5.0
    monkeypatch.setattr(micprofile, "FILE", tmp_path / "absent.json")
    assert micprofile.resolve("autre").key == ""


def test_l_environnement_reste_prioritaire(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"profiles": {"x": {"gain": 9.0}}})
    monkeypatch.setenv("ASTREL_MIC_GAIN", "1.5")
    assert micprofile.resolve("x").gain == 1.5


def test_save_cree_puis_met_a_jour_le_meme_profil(tmp_path, monkeypatch):
    f = _write(tmp_path, monkeypatch, {})
    p = micprofile.save("alsa_input.pci-mic", gain=6.0)
    assert (p.key, p.gain) == ("alsa_input.pci-mic", 6.0)
    micprofile.save("alsa_input.pci-mic", vad=0.3)
    saved = json.loads(f.read_text())["profiles"]["alsa_input.pci-mic"]
    assert saved == {"gain": 6.0, "vad": 0.3}


def test_selection_des_canaux_ignore_le_mort_et_le_sature():
    import numpy as np

    from astrel import audio

    mic = audio.MicStream(source="a", gain=1.0)
    mic._n_in, mic._channels = 4, (0, 2)
    frames = np.array([[1000, 0, 3000, 30000]] * 3, dtype=np.int16)
    out = np.frombuffer(mic._select_channels(frames.tobytes()), dtype=np.int16)
    assert out.tolist() == [2000, 2000, 2000]


def test_profil_lit_les_canaux(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"profiles": {"skl": {"gain": 0.5, "channels": [0, 2, "x", -1, 0]}}})
    p = micprofile.resolve("alsa_input.skl_hda")
    assert p.channels == (0, 2) and "canaux 0+2" in p.describe()
