"""Groupe « WhatsApp » de la page « Réglages » (17/10, plan P4, lot 9) : état du pont et
commandes existantes (`whatsapp on|off|absent|present`). 18/10 (plan P5) : préparer le
pont, relier par QR ou par code, confirmer le numéro, délier (mot « délier »)."""
import time

import pytest

from astrel import audio, micprofile
from astrel.settings import reglages
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)
from test_whatsapp import wa_env  # noqa: F401  (fixture)


class FakeWhatsApp:
    def __init__(self, linked=True, enabled=True, away=False, state="open", linking="", ready=True):
        self.d = {"ok": True, "linked": linked, "enabled": enabled, "state": state, "away": away,
                  "linking": linking, "ready": ready}
        self.calls = []

    def link(self, phone=""):
        self.calls.append(("link", phone))
        self.d["linking"] = "code" if phone else "qr"
        return {"ok": True}

    def link_confirm(self, ok):
        self.calls.append(("confirm", ok))
        return {"ok": True}

    def link_cancel(self):
        self.calls.append("cancel")
        self.d["linking"] = ""
        return {"ok": True}

    def unlink(self):
        self.calls.append("unlink")
        self.d.update(linked=False, state="need_link", away=False)
        return {"ok": True, "phone": True}

    def publish(self):
        pass

    def describe(self):
        return dict(self.d)

    def command(self, arg):
        self.calls.append(arg)
        if arg in ("on",) and not self.d["linked"]:
            return {"ok": False, "error": "WhatsApp n'est pas relié : ./run.sh whatsapp link"}
        if arg in ("absent", "present"):
            self.d["away"] = arg == "absent"
        if arg in ("on", "off"):
            self.d["enabled"] = arg == "on"
        return self.describe()


@pytest.fixture
def j(astrel, monkeypatch, tmp_path):
    j, _, _ = astrel
    j.mic = type("M", (), {"source": "", "profile": micprofile.MicProfile()})()
    j.speaker = type("S", (), {"voice": "edge:fr-FR-RemyMultilingualNeural"})()
    j.whatsapp = FakeWhatsApp()
    monkeypatch.setattr(audio, "list_mics", lambda: [])
    j._bind_settings()
    return j


def view():
    return next(i for g in reglages.describe()["groups"] if g["id"] == "whatsapp" for i in g["items"])


def test_sans_le_coeur():
    assert view()["value"]["lines"][0]["text"] == "indisponible ici" and view()["actions"] == []


def test_relie_et_present(j):
    it = view()
    lines = {line["label"]: line for line in it["value"]["lines"]}
    assert lines["Liaison"]["level"] == "ok" and lines["Pont"]["text"] == "connecté"
    assert [a["id"] for a in it["actions"]] == ["absent", "off", "unlink"]
    res = reglages.action("whatsapp", "absent", {})
    assert res["ok"] and j.whatsapp.calls == ["absent"]
    assert [a["id"] for a in view()["actions"]] == ["present", "off", "unlink"]


def test_non_relie_relier_par_qr_ou_par_code(j):
    j.whatsapp = FakeWhatsApp(linked=False, enabled=False, state="off")
    it = view()
    assert [a["id"] for a in it["actions"]] == ["link", "link_phone"]
    assert next(a for a in it["actions"] if a["id"] == "link_phone")["input"]["type"] == "tel"
    assert reglages.action("whatsapp", "on", {})["ok"] is False  # bouton absent : refusé
    assert reglages.action("whatsapp", "unlink", {"confirm": "délier"})["ok"] is False
    assert reglages.action("whatsapp", "link_phone", {})["ok"] is False  # numéro manquant
    assert reglages.action("whatsapp", "link_phone", {"value": "+33 6 12 34 56 78"})["ok"]
    assert j.whatsapp.calls == [("link", "33612345678")]


def test_appairage_en_cours_boutons_de_l_etape(j):
    j.whatsapp = FakeWhatsApp(linked=False, enabled=False, state="off", linking="qr")
    it = view()
    assert it["value"]["link_box"] is True and [a["id"] for a in it["actions"]] == ["link_cancel"]
    assert reglages.action("whatsapp", "link_confirm", {})["ok"] is False  # pas encore de compte détecté
    j.whatsapp.d["linking"] = "linked"
    assert [a["id"] for a in view()["actions"]] == ["link_confirm", "link_cancel"]
    assert reglages.action("whatsapp", "link_confirm", {})["text"] == "Numéro confirmé."
    assert j.whatsapp.calls == [("confirm", True)]


def test_pont_a_preparer(j, monkeypatch):
    from astrel import mcp_config

    j.whatsapp = FakeWhatsApp(linked=False, enabled=False, state="off", ready=False)
    monkeypatch.setattr(mcp_config, "_node", lambda: None)
    it = view()
    assert it["actions"] == [] and "Node.js" in it["value"]["lines"][-1]["text"]
    monkeypatch.setattr(mcp_config, "_node", lambda: "/usr/bin/node")
    assert [a["id"] for a in view()["actions"]] == ["prepare"]


def test_delier_exige_le_mot(j):
    it = view()
    unlink = next(a for a in it["actions"] if a["id"] == "unlink")
    assert unlink["word"] == "délier" and unlink["danger"]
    assert reglages.action("whatsapp", "unlink", {})["ok"] is False and "unlink" not in j.whatsapp.calls
    res = reglages.action("whatsapp", "unlink", {"confirm": "Délier"})
    assert res["ok"] and "retiré" in res["text"] and j.whatsapp.calls == ["unlink"]
    assert [a["id"] for a in view()["actions"]] == ["link", "link_phone"]


def test_session_perdue_seul_delier(j):
    j.whatsapp = FakeWhatsApp(state="logged_out")
    it = view()
    assert [a["id"] for a in it["actions"]] == ["unlink"]
    assert "délie puis relie" in it["value"]["lines"][1]["text"]


def test_le_qr_jamais_dans_les_reglages(j, wa_env, monkeypatch, caplog):
    """Vrai `WhatsApp` + faux pont d'appairage : pendant l'appairage, ni la page
    « Réglages » (rejouée), ni l'historique, ni le journal ne portent le QR ou le code."""
    import json
    import logging
    import sys

    from astrel import whatsapp as wa_mod
    from astrel.events import bus
    from test_whatsapp import FAKE_HUD_LINK, wait_for

    caplog.set_level(logging.DEBUG)
    (wa_env / "whatsapp-auth" / "self.json").unlink()
    script = wa_env / "faux_lien_hud.py"
    script.write_text(FAKE_HUD_LINK, encoding="utf-8")
    monkeypatch.setenv("FAKE_LINK_MODE", "hang")
    monkeypatch.setattr(wa_mod, "bridge_argv", lambda link=False, phone="": [
        sys.executable, str(script), "--auth", str(wa_env / "whatsapp-auth")] + (["--phone", phone] if phone else []))
    j.whatsapp = wa_mod.WhatsApp(j, gap_s=0)
    try:
        for action, cmd, secret in (("link", {}, "QRSECRET2"), ("link_phone", {"value": "33612345678"}, "CODE8XYZ")):
            assert reglages.action("whatsapp", action, cmd)["ok"]
            assert wait_for(lambda: secret in json.dumps(bus.last("whatsapp_link") or {}))
            assert view()["value"]["link_box"] is True
            page = json.dumps(reglages.describe(), ensure_ascii=False)
            assert "QRSECRET" not in page and "CODE8XYZ" not in page
            assert reglages.action("whatsapp", "link_cancel", {})["ok"]
            assert wait_for(lambda: not j.whatsapp.linker.active)
        text = caplog.text + json.dumps(bus.snapshot()["history"], ensure_ascii=False)
        assert "QRSECRET" not in text and "CODE8XYZ" not in text
        assert bus.last("whatsapp_link") is None
    finally:
        if j.whatsapp.linker.active:
            j.whatsapp.linker.cancel()


def test_coupe_puis_active(j):
    reglages.action("whatsapp", "off", {})
    assert [a["id"] for a in view()["actions"]] == ["on", "unlink"]
    assert reglages.action("whatsapp", "on", {})["text"] == "WhatsApp activé."


def test_refuse_a_claude(j, published):
    res = j.on_command({"cmd": "settings_action", "key": "whatsapp", "action": "off", "_from_claude": True})
    assert res["ok"] is False and j.whatsapp.calls == []


def test_la_page_suit_l_evenement(j, published):
    j._settings_follow({"type": "whatsapp", "linked": True})
    deadline = time.time() + 2
    while not of(published, "settings") and time.time() < deadline:
        time.sleep(0.02)
    assert of(published, "settings")
    n = len(of(published, "settings"))
    j._settings_follow({"type": "settings"})  # jamais en boucle sur sa propre publication
    time.sleep(0.1)
    assert len(of(published, "settings")) == n
