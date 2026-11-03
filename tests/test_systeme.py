"""Groupe « Système » de la page « Réglages » (17/10, plan P4, lot 8) : démarrage avec la
session (systemd --user), bulle (autostart), raccourcis GNOME (hotkeys.sh). Ni systemctl ni
gsettings réels : dossier personnel du test, commandes simulées."""
import types
from pathlib import Path

import pytest

from astrel import settings as settings_mod
from astrel.platform import Indisponible, degrade, linux, macos, windows
from astrel.settings import SettingError, reglages
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)

FUNCS = ("autostart_state", "set_autostart", "hotkeys_state", "set_hotkeys")
REAL_SYS_STATE = settings_mod._sys_state  # pris à l'import, avant que conftest le débranche


@pytest.fixture
def machine(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".config" / "systemd" / "user").mkdir(parents=True)
    (home / ".config" / "systemd" / "user" / "astrel.service").write_text("[Unit]\n")
    monkeypatch.setattr(linux.Path, "home", lambda: home)
    state = {"enabled": "enabled", "systemd": True, "gnome": True, "keys": "['/x/astrel-wake/']"}
    ran = []
    monkeypatch.setattr(linux, "_has_systemd", lambda: state["systemd"])
    monkeypatch.setattr(linux, "_gnome", lambda: state["gnome"])
    state["bulle"] = "disabled"

    def is_enabled(cmd, **k):
        cle = "bulle" if cmd[-1] == "astrel-bubble.service" else "enabled"
        return types.SimpleNamespace(stdout=state[cle] + "\n", returncode=0)

    monkeypatch.setattr(linux.subprocess, "run", is_enabled)

    def run(cmd, timeout=10.0, check=True):
        ran.append(cmd)
        if cmd[:2] == ["gsettings", "get"]:
            return state["keys"]
        if "astrel-bubble.service" in cmd:
            state["bulle"] = "enabled" if "enable" in cmd else "disabled"
        elif cmd[-1] in ("enable", "disable") or "astrel.service" in cmd:
            state["enabled"] = "enabled" if "enable" in cmd else "disabled"
        return ""

    monkeypatch.setattr(linux, "_run", run)
    # la page lit le VRAI moteur (conftest le débranche) ; ici, Linux simulé
    monkeypatch.setattr(settings_mod, "_sys_state", REAL_SYS_STATE)
    monkeypatch.setattr(settings_mod, "_SYS_CACHE", {})
    from astrel import platform

    monkeypatch.setattr(platform, "desktop_backend", lambda system=None: linux)
    return home, state, ran


def item(key):
    return next(i for g in reglages.describe()["groups"] for i in g["items"] if i["key"] == key)


def test_memes_fonctions_partout():
    for mod in (linux, macos, windows, degrade):
        for name in FUNCS:
            assert callable(getattr(mod, name)), (mod.__name__, name)
    with pytest.raises(Indisponible, match="indisponible"):
        degrade.autostart_state()
    with pytest.raises(Indisponible, match="indisponible"):
        degrade.set_hotkeys(True)


def test_demarrage_avec_la_session(machine):
    home, state, ran = machine
    it = item("systeme.autostart")
    assert not it["locked"] and it["value"] is True
    reglages.set("systeme.autostart", False)
    assert ["systemctl", "--user", "disable", "astrel.service"] in ran
    assert item("systeme.autostart")["value"] is False
    reglages.set("systeme.autostart", True)
    assert ["systemctl", "--user", "enable", "astrel.service"] in ran
    assert not any("start" in c or "stop" in c for c in ran)  # ni lancé ni arrêté maintenant


def test_bulle_au_demarrage(machine):
    # 29/10 : la bulle démarre par SON service systemd ; l'ancien autostart GNOME (qui ouvrait une
    # seconde bulle) est désactivé, jamais réécrit
    home, state, ran = machine
    ancien = home / ".config" / "autostart" / "astrel-bubble.desktop"
    ancien.parent.mkdir(parents=True)
    ancien.write_text("[Desktop Entry]\nExec=x/bubble.sh\nX-GNOME-Autostart-enabled=true\n")
    assert item("systeme.bubble")["value"] is False
    reglages.set("systeme.bubble", True)
    unit = home / ".config" / "systemd" / "user" / "astrel-bubble.service"
    text = unit.read_text()
    assert "@ROOT@" not in text and "-m astrel.bubble" in text
    assert ["systemctl", "--user", "daemon-reload"] in ran and ["systemctl", "--user", "enable", "astrel-bubble.service"] in ran
    assert "X-GNOME-Autostart-enabled=false" in ancien.read_text() and "=true" not in ancien.read_text()
    assert item("systeme.bubble")["value"] is True and state["enabled"] == "enabled"  # Astrel lui-même intact
    reglages.set("systeme.bubble", False)
    assert ["systemctl", "--user", "disable", "astrel-bubble.service"] in ran and item("systeme.bubble")["value"] is False


def test_wsl2_sans_systemd(machine):
    _, state, _ = machine
    state["systemd"] = False
    it = item("systeme.autostart")
    assert it["locked"] and "systemd absent" in it["lock_reason"]
    with pytest.raises(SettingError, match="systemd"):
        reglages.set("systeme.autostart", True)


def test_service_non_installe(machine):
    home, *_ = machine
    (home / ".config" / "systemd" / "user" / "astrel.service").unlink()
    assert "install.py" in item("systeme.autostart")["lock_reason"]


def test_raccourcis(machine):
    _, state, ran = machine
    assert item("systeme.hotkeys")["value"] is True
    reglages.set("systeme.hotkeys", False)
    assert ran[-1][-1] == "--remove" and ran[-1][0].endswith("hotkeys.sh")
    reglages.set("systeme.hotkeys", True)
    assert ran[-1][0].endswith("hotkeys.sh") and len(ran[-1]) == 1
    state["gnome"] = False
    settings_mod._SYS_CACHE.clear()
    it = item("systeme.hotkeys")
    assert it["locked"] and "GNOME" in it["lock_reason"]


def test_refuse_a_claude(astrel, published, machine):
    j, _, _ = astrel
    _, _, ran = machine
    res = j.on_command({"cmd": "settings_set", "key": "systeme.autostart", "value": False, "_from_claude": True})
    assert res["ok"] is False and not any("disable" in c for c in ran)


# --- 03/11 (portage) : démarrage automatique et raccourcis de Windows et macOS ---

def test_windows_demarrage_par_le_dossier_demarrage(tmp_path, monkeypatch):
    dem = tmp_path / "Startup"
    pyw = tmp_path / "pythonw.exe"
    pyw.write_text("")
    monkeypatch.setattr(windows, "_demarrage", lambda: dem)
    monkeypatch.setattr(windows, "_pythonw", lambda: pyw)
    lances = []

    def faux_powershell(cmd, env, **k):
        lances.append((env["ASTREL_CIBLE"], env["ASTREL_ARGS"]))
        Path(env["ASTREL_LIEN"]).write_text("lnk")
    monkeypatch.setattr(windows.subprocess, "run", faux_powershell)
    assert windows.autostart_state() == {"service": False, "bubble": False}
    assert "démarrera" in windows.set_autostart(True)
    assert "bulle" in windows.set_autostart(True, bubble=True)
    assert windows.autostart_state() == {"service": True, "bubble": True}
    assert lances == [(str(pyw), "-m astrel"), (str(pyw), "-m astrel.bulle_qt")]
    windows.set_autostart(False)
    assert windows.autostart_state() == {"service": False, "bubble": True}
    pyw.unlink()
    with pytest.raises(Indisponible, match="install.ps1"):
        windows.autostart_state()


def test_macos_demarrage_par_launchagents(tmp_path, monkeypatch):
    import plistlib
    monkeypatch.setattr(macos.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(macos.subprocess, "run", lambda *a, **k: None)
    assert macos.autostart_state() == {"service": False, "bubble": False}
    macos.set_autostart(True)
    macos.set_autostart(True, bubble=True)
    agent = plistlib.loads((tmp_path / "Library" / "LaunchAgents" / "fr.astrel.coeur.plist").read_bytes())
    assert agent["RunAtLoad"] is True and agent["ProgramArguments"][1:] == ["-m", "astrel"]
    assert agent["KeepAlive"] == {"SuccessfulExit": False}
    macos.set_autostart(False, bubble=False)
    assert macos.autostart_state() == {"service": True, "bubble": False}


def test_raccourcis_retenus_et_ecoutes(tmp_path, monkeypatch):
    from astrel.platform import raccourcis
    monkeypatch.setattr(raccourcis, "FICHIER", tmp_path / "raccourcis.json")
    monkeypatch.setattr(raccourcis.config, "RUNTIME", tmp_path)
    ecoute = []
    monkeypatch.setattr(raccourcis, "demarrer", lambda: ecoute.append("on") or True)
    monkeypatch.setattr(raccourcis, "arreter", lambda: ecoute.append("off"))
    assert not windows.hotkeys_state()
    assert "Ctrl+Alt+J" in windows.set_hotkeys(True)
    assert macos.hotkeys_state() and ecoute == ["on"]
    windows.set_hotkeys(False)
    assert not windows.hotkeys_state() and ecoute == ["on", "off"]
    assert set(raccourcis.TOUCHES.values()) == {"wake", "toggle", "stop", "pause toggle", "hud"}
