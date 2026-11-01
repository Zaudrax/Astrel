import json
import subprocess

from astrel import audio


def test_switch_sans_flux_retient_la_source():
    mic = audio.MicStream(source="a")
    mic.switch("b")
    assert mic.source == "b" and mic._proc is None


def test_list_mics_ecarte_les_moniteurs(monkeypatch):
    sources = [
        {"name": "usb.mic", "description": "Generic   Mono", "monitor_source": ""},
        {"name": "hp.monitor", "description": "Monitor of HP"},
        {"name": "x", "description": None, "monitor_source": "hp"},
    ]

    def fake_run(cmd, **_kw):
        out = json.dumps(sources) if "list" in cmd else "usb.mic\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=out)

    monkeypatch.setattr(audio.subprocess, "run", fake_run)
    assert audio.list_mics() == [{"name": "usb.mic", "label": "Generic Mono", "default": True}]
