"""Couche plateforme (P2, lots 4 et 5) : Linux garde son code d'origine, macOS et
Windows ont leurs moteurs, le reste dit « indisponible ». Les moteurs macOS et
Windows sont testés avec des commandes SIMULÉES (pas de vraie machine ici)."""
import json
import subprocess
import sys
import time
import wave

import pytest

from astrel import audio, config, platform
from astrel.platform import Indisponible, degrade, macos, minuteur, son, windows
from astrel.tools import desktop, reminders, screenshot


# --- détection ---

@pytest.mark.parametrize("raw,want", [("linux", "linux"), ("darwin", "macos"), ("win32", "windows"),
                                      ("cygwin", "windows"), ("freebsd14", "autre")])
def test_detect(raw, want):
    assert platform.detect(raw) == want


def test_moteur_du_bureau_par_systeme():
    assert platform.desktop_backend("linux").__name__ == "astrel.platform.linux"
    assert platform.desktop_backend("macos") is macos
    assert platform.desktop_backend("windows") is windows
    assert platform.desktop_backend("autre") is degrade


def test_facade_garde_les_noms_d_origine():
    impl = platform.desktop_backend()
    for name in ("open_url", "open_path", "open_app", "list_apps", "notify", "set_volume", "get_volume",
                 "set_brightness", "set_dark_mode", "lock_screen", "media_control", "clipboard_read",
                 "clipboard_write", "wifi_toggle", "power_action", "windows_summary"):
        assert getattr(desktop, name) is getattr(impl, name)
        assert callable(getattr(macos, name)) and callable(getattr(windows, name))
        assert callable(getattr(degrade, name))


def test_degrade_dit_indisponible():
    with pytest.raises(Indisponible, match="indisponible"):
        degrade.set_volume(level=10)
    assert json.loads(degrade.windows_summary())["disponible"] is False
    assert degrade.list_apps() == []


def test_python_exe_reste_celui_de_la_venv(monkeypatch, tmp_path):
    fake = tmp_path / "python"
    fake.write_text("")
    monkeypatch.setattr(config, "VENV_PYTHON", fake)
    assert platform.python_exe() == str(fake)
    monkeypatch.setattr(config, "VENV_PYTHON", tmp_path / "absent")
    assert platform.python_exe() == sys.executable


def test_pilote_camera():
    cv2 = pytest.importorskip("cv2")
    assert platform.camera_api("linux") == cv2.CAP_V4L2
    assert platform.camera_api("macos") == cv2.CAP_AVFOUNDATION
    assert platform.camera_api("windows") == cv2.CAP_DSHOW


# --- macOS (simulé) ---

@pytest.fixture
def mac_calls(monkeypatch):
    calls = []

    def fake_run(cmd, timeout=10.0, check=True, input=None, env=None):
        calls.append(("run", cmd, input))
        if cmd[0] == "osascript" and any("get volume settings" in c for c in cmd):
            return "45,false"
        if cmd[0] == "osascript" and any("is running" in c for c in cmd):
            return "true" if any('"Music"' in c for c in cmd) else "false"
        return ""

    monkeypatch.setattr(macos, "run", fake_run)
    monkeypatch.setattr(macos, "spawn", lambda cmd: calls.append(("spawn", cmd, None)))
    return calls


def test_macos_texte_en_argument_jamais_dans_le_script(mac_calls):
    title = 'x" & do shell script "rm -rf ~" & "'
    macos.notify(title, "corps")
    _, cmd, _ = mac_calls[-1]
    script = [cmd[i + 1] for i, c in enumerate(cmd) if c == "-e"]
    assert all(title not in line for line in script)
    assert cmd[-2:] == [title, "corps"]


def test_macos_volume_plafonne_a_100(mac_calls):
    assert macos.set_volume(level=140) == "Volume à 45 %."
    assert any("set volume output volume 100" in c for _, cmd, _ in mac_calls for c in cmd)


def test_macos_media_sur_le_lecteur_ouvert(mac_calls):
    assert macos.media_control("next") == "next sur Music."
    with pytest.raises(ValueError):
        macos.media_control("formatte")


def test_macos_refuse_un_fichier_par_open_url(mac_calls):
    with pytest.raises(ValueError):
        macos.open_url("file:///etc/passwd")
    assert macos.open_url("https://example.org") == "Ouvert : https://example.org"
    assert mac_calls[-1] == ("spawn", ["open", "https://example.org"], None)


def test_macos_presse_papiers(mac_calls):
    macos.clipboard_write("é ü")
    assert mac_calls[-1][1] == ["pbcopy"] and mac_calls[-1][2] == "é ü"


# --- Windows (simulé) ---

@pytest.fixture
def win_calls(monkeypatch):
    calls = []

    def fake_ps(script, env=None, input=None, timeout=15.0):
        calls.append(("ps", script, env, input))
        return ""

    monkeypatch.setattr(windows, "_ps", fake_ps)
    monkeypatch.setattr(windows, "_press", lambda vk, times=1: calls.append(("key", vk, times, None)))
    monkeypatch.setattr(windows, "spawn", lambda cmd: calls.append(("spawn", cmd, None, None)))
    return calls


def test_windows_notification_par_variables(win_calls):
    windows.notify("Titre'; Remove-Item C:\\", "corps")
    _, script, env, _ = win_calls[-1]
    assert "Remove-Item" not in script
    assert env == {"ASTREL_TITRE": "Titre'; Remove-Item C:\\", "ASTREL_TEXTE": "corps"}


def test_windows_volume_par_touches(win_calls):
    assert windows.set_volume(level=30) == "Volume réglé à environ 30 %."
    assert win_calls[-2][1:3] == (windows.VK_VOLUME_DOWN, 50)
    assert win_calls[-1][1:3] == (windows.VK_VOLUME_UP, 15)
    with pytest.raises(Indisponible):
        windows.get_volume()


def test_windows_presse_papiers_par_l_entree(win_calls):
    windows.clipboard_write("texte ; exit")
    _, script, _, given = win_calls[-1]
    assert "texte" not in script and given == "texte ; exit"


def test_windows_wifi_et_alimentation(win_calls):
    with pytest.raises(Indisponible, match="administrateur"):
        windows.wifi_toggle(False)
    windows.power_action("reboot")
    assert win_calls[-1][1] == ["shutdown", "/r", "/t", "0"]
    with pytest.raises(ValueError):
        windows.power_action("format")


# --- capture d'écran ---

def test_capture_systeme_inconnu():
    with pytest.raises(Indisponible):
        screenshot.capture("autre")


def test_capture_macos(monkeypatch):
    def fake_run(cmd, **_kw):
        open(cmd[-1], "wb").write(b"\x89PNG")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(screenshot.subprocess, "run", fake_run)
    path = screenshot.capture("macos")
    try:
        assert path.read_bytes() == b"\x89PNG"
    finally:
        path.unlink()
    path, legende = screenshot.capturer("macos", ecran="LG")  # plan P24 : choix d'écran Linux seulement
    path.unlink()
    assert "non pris en charge" in legende


# --- capture d'écran, plusieurs écrans (22/10, plan P24) : portail SIMULÉ, aucune vraie capture ---

def _png(w: int, h: int, couleur: int = 0) -> bytes:
    import cv2
    import numpy as np

    ok, buf = cv2.imencode(".png", np.full((h, w, 3), couleur, np.uint8))
    assert ok
    return buf.tobytes()


@pytest.fixture
def portail(monkeypatch, tmp_path):
    """Portail ScreenCast simulé : `etat["streams"]` = flux rendus par Start (jeton déjà accordé :
    le portable seul, comme le 20/10), images tirées par nœud. Écrans = disposition mesurée."""
    pytest.importorskip("gi")  # portail ScreenCast : PyGObject vient du système (absent de la CI)
    from astrel import ecrans
    from astrel.platform import linux_capture as lc
    from test_ecrans import ETAT_MESURE

    etat = {
        "streams": [(51, {"position": (0, 1080), "size": (1704, 1065), "source_type": 1})],
        "images": {51: _png(2560, 1600, 200), 52: _png(1920, 1080, 90)},
        "options": None, "tires": [], "closed": False,
    }

    class FauxPortail:
        def request(self, method, args, signature, timeout_s=120):
            if method == "CreateSession":
                return {"session_handle": "/session/1"}
            if method == "SelectSources":
                etat["options"] = {k: v.unpack() for k, v in args[1].items()}
                return {}
            return {"streams": etat["streams"], "restore_token": "jeton-neuf"}

        def close(self, session):
            etat["closed"] = True

    def tirer(_portal, _session, node):
        etat["tires"].append(node)
        return etat["images"][node]

    monkeypatch.setattr(lc, "Portal", FauxPortail)
    monkeypatch.setattr(lc, "_tirer", tirer)
    monkeypatch.setattr(lc, "TOKEN_FILE", tmp_path / "screencast-token")
    (tmp_path / "screencast-token").write_text("jeton-du-20-10")
    monkeypatch.setattr(ecrans, "lister", lambda: ecrans.depuis_etat(ETAT_MESURE))
    return lc, etat


def _ouvre(path):
    from astrel.platform import linux_capture as lc

    try:
        return lc._png_taille(path.read_bytes())
    finally:
        path.unlink()


def test_capture_principal_par_defaut_avec_le_jeton(portail):
    lc, etat = portail
    path, legende = lc.capture()
    assert _ouvre(path) == (2560, 1600)
    assert etat["options"]["multiple"] is True and etat["options"]["restore_token"] == "jeton-du-20-10"
    assert legende.startswith("Écran vu : Affichage intégré (eDP-1, 2560x1600, principal, portable, en bas).")
    assert "Autre écran : LG FULL HD (HDMI-1, en haut)" in legende
    assert etat["closed"] and lc.TOKEN_FILE.read_text() == "jeton-neuf"


def test_ecran_demande_non_autorise_erreur_claire(portail):
    lc, etat = portail
    with pytest.raises(RuntimeError) as err:
        lc.capture("LG")
    msg = str(err.value)
    assert "LG FULL HD (HDMI-1)" in msg and "n'est pas couvert" in msg and "--autoriser" in msg
    assert "Affichage intégré (eDP-1)" in msg  # ce qui est partagé
    assert etat["tires"] == [] and etat["closed"]  # rien de tiré, session refermée


def test_ecran_par_defaut_non_autorise_repli(portail):
    """WATCH avec le réglage sur le LG et un jeton limité au portable : une image quand même."""
    lc, _etat = portail
    path, legende = lc.capture("LG", explicite=False)
    assert _ouvre(path) == (2560, 1600)
    assert "LG FULL HD (HDMI-1) non partagé : Affichage intégré montré à la place" in legende


def test_capture_du_lg_une_fois_autorise(portail):
    lc, etat = portail
    etat["streams"] = [(51, {"position": (0, 1080), "size": (1704, 1065)}), (52, {"position": (0, 0), "size": (1920, 1080)})]
    path, legende = lc.capture("LG")
    assert _ouvre(path) == (1920, 1080) and etat["tires"] == [52]  # seul le flux voulu est tiré
    assert legende.startswith("Écran vu : LG FULL HD (HDMI-1, 1920x1080, en haut).")


def test_tous_assembles_comme_sur_le_bureau(portail):
    import cv2
    import numpy as np

    lc, etat = portail
    etat["streams"] = [(52, {"position": (0, 0)}), (51, {"position": (0, 1080)})]
    path, legende = lc.capture("tous")
    img = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    path.unlink()
    assert img.shape[:2] == (1080 + 1065, 1920)  # LG au-dessus, portable ramené à sa taille logique
    assert img[10, 10, 0] == 90 and img[1500, 10, 0] == 200 and img[1500, 1800, 0] == 0
    assert legende.startswith("Écrans vus, assemblés comme sur le bureau : LG FULL HD")


def test_tous_avec_un_seul_ecran_autorise(portail):
    lc, _etat = portail
    path, legende = lc.capture("tous")
    assert _ouvre(path) == (2560, 1600)
    assert "Non partagé : LG FULL HD (HDMI-1)" in legende and "--autoriser" in legende


def test_flux_sans_position_reconnu_par_la_taille_de_l_image(portail):
    """Repli du plan P24 : propriétés du portail absentes → taille de l'image tirée."""
    lc, etat = portail
    etat["streams"] = [(51, {}), (52, {})]
    path, legende = lc.capture("HDMI-1")
    assert _ouvre(path) == (1920, 1080) and "Écran vu : LG FULL HD" in legende


def test_flux_impossible_a_identifier(portail):
    lc, etat = portail
    etat["streams"] = [(51, {})]
    etat["images"][51] = _png(1000, 1000)  # ni la taille ni le rapport d'aucun écran
    with pytest.raises(RuntimeError, match="impossible de savoir"):
        lc.capture("LG")
    path, legende = lc.capture()  # écran par défaut : le flux est montré, avec la réserve
    assert _ouvre(path) == (1000, 1000) and "non identifié" in legende


def test_autoriser_ignore_le_jeton(portail):
    lc, etat = portail
    path, _legende = lc.capture(autoriser=True)
    path.unlink()
    assert "restore_token" not in etat["options"] and etat["options"]["persist_mode"] == 2
    assert lc.TOKEN_FILE.read_text() == "jeton-neuf"


def test_liste_des_ecrans_illisible_comme_avant(portail, monkeypatch):
    from astrel import ecrans

    lc, etat = portail
    monkeypatch.setattr(ecrans, "lister", lambda: [])
    etat["streams"] = [(52, {}), (51, {})]
    path, legende = lc.capture("LG")
    assert _ouvre(path) == (1920, 1080)  # premier flux
    assert "premier écran partagé" in legende and "2 écrans" in legende


def test_screenshot_premiere_ligne_chemin_puis_legende(monkeypatch, capsys, tmp_path):
    pytest.importorskip("gi")
    from astrel import ecrans

    recu = {}

    def faux(ecran="", autoriser=False, explicite=True):
        recu.update(ecran=ecran, autoriser=autoriser, explicite=explicite)
        return tmp_path / "x.png", "Écran vu : LG\nFULL HD."

    monkeypatch.setattr("astrel.platform.linux_capture.capture", faux)
    monkeypatch.setattr(ecrans, "ecran_par_defaut", lambda: "portable")
    monkeypatch.setattr(screenshot, "SYSTEM", "linux")
    assert screenshot.main(["--ecran", "LG"]) == 0
    assert capsys.readouterr().out.splitlines() == [str(tmp_path / "x.png"), "Écran vu : LG FULL HD."]
    assert recu == {"ecran": "LG", "autoriser": False, "explicite": True}
    screenshot.main([])
    assert recu == {"ecran": "portable", "autoriser": False, "explicite": False}  # écran du réglage
    screenshot.main(["--autoriser"])
    assert recu["autoriser"] is True


def test_screenshot_erreur(monkeypatch, capsys):
    def panne(*_a, **_k):
        raise RuntimeError("aucun écran partagé")

    monkeypatch.setattr(screenshot, "capturer", panne)
    assert screenshot.main([]) == 1
    sortie = capsys.readouterr()
    assert sortie.out == "" and "ERREUR: aucun écran partagé" in sortie.err


# --- rappels ---

@pytest.fixture
def index(tmp_path, monkeypatch):
    monkeypatch.setattr(reminders, "INDEX", tmp_path / "reminders.json")
    return tmp_path / "reminders.json"


def test_rappel_systemd_inchange(index, monkeypatch):
    calls = []
    monkeypatch.setattr(reminders, "use_systemd", lambda: True)
    monkeypatch.setattr(reminders.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    out = reminders.create("sortir le linge", in_minutes=5)
    assert "remarque" not in out
    cmd = calls[-1]
    assert cmd[:2] == ["systemd-run", "--user"] and "--on-active=300s" in cmd and cmd[-3:-1] == ["/bin/sh", "-c"]
    assert json.loads(index.read_text())[out["id"]]["unit"].startswith(reminders.PREFIX)


def test_rappel_minuteur_interne(index, monkeypatch):
    launched = []

    class FakeProc:
        pid = 4242

    monkeypatch.setattr(reminders, "use_systemd", lambda: False)
    monkeypatch.setattr(reminders.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd) or FakeProc())
    out = reminders.create("appeler Paul", in_minutes=1)
    assert out["remarque"] == reminders.NOTE_MINUTEUR
    cmd = launched[-1]
    assert cmd[1:3] == ["-m", reminders.MINUTEUR] and cmd[3] == out["id"] and cmd[-1] == "appeler Paul"
    assert float(cmd[4]) == pytest.approx(time.time() + 60, abs=5)
    assert json.loads(index.read_text())[out["id"]]["pid"] == 4242

    monkeypatch.setattr(reminders, "_minuteur_alive", lambda uid, pid: True)
    assert [r["id"] for r in reminders.list_all()] == [out["id"]]
    monkeypatch.setattr(reminders, "_minuteur_alive", lambda uid, pid: False)
    assert reminders.list_all() == []  # échu : retiré de l'index


def test_minuteur_attend_par_tranches():
    clock = [1000.0]
    naps = []

    def sleep(s):
        naps.append(s)
        clock[0] += s

    minuteur.wait_until(1000.0 + 150, now=lambda: clock[0], sleep=sleep)
    assert naps == [60.0, 60.0, 30.0]


def test_minuteur_se_retire_de_l_index(index):
    index.write_text(json.dumps({"1": {"message": "m", "due": "x", "pid": 1}}))
    reminders.forget("1")
    assert json.loads(index.read_text()) == {}


# --- son : moteurs hors PipeWire ---

def test_moteur_audio(monkeypatch):
    monkeypatch.setenv("ASTREL_AUDIO_ENGINE", "fichier")
    assert audio.engine() == "fichier"
    monkeypatch.setenv("ASTREL_AUDIO_ENGINE", "n'importe quoi")
    assert audio.engine() == ("pulse" if sys.platform.startswith("linux") else "sounddevice")


def test_fifo():
    f = son._Fifo()
    f.put(b"abcd")
    assert f.read(2) == b"ab" and f.read1() == b"cd"
    f.put(b"xy")
    f.close()
    assert f.read(4) == b"xy"  # fermé : rend ce qui reste


def _wav(path, pcm, rate):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def test_micro_fichier_lu_par_micstream(monkeypatch, tmp_path):
    frame = bytes(range(256)) * (config.FRAME_BYTES // 256)
    _wav(tmp_path / "voix.wav", frame, config.SAMPLE_RATE)
    monkeypatch.setenv("ASTREL_AUDIO_ENGINE", "fichier")
    monkeypatch.setenv("ASTREL_AUDIO_IN", str(tmp_path / "voix.wav"))
    with audio.MicStream(source="", gain=1.0) as mic:
        assert mic.read_frame() == frame
        mic.flush(0.05)
        assert mic.read_frame() == bytes(config.FRAME_BYTES)  # fin du fichier : silence
    assert audio.list_mics() == [{"name": "fichier", "label": "Fichier audio (tests)", "default": True}]


def test_haut_parleur_fichier(monkeypatch, tmp_path):
    out = tmp_path / "sortie.raw"
    monkeypatch.setenv("ASTREL_AUDIO_ENGINE", "fichier")
    monkeypatch.setenv("ASTREL_AUDIO_OUT", str(out))
    audio.hold_output(False)
    p = audio.Player(8000)
    assert p.write(b"\x01\x00" * 100)
    p.close()
    lead = int(8000 * config.OUTPUT_LEAD_S) * 2
    assert out.read_bytes() == bytes(lead) + b"\x01\x00" * 100
    q = audio.Player(8000)
    q.kill()
    assert not q.write(b"\x00\x00")  # coupé : plus rien n'est pris


def test_wav_au_mauvais_format(tmp_path):
    _wav(tmp_path / "x.wav", b"\x00\x00" * 10, 8000)
    with pytest.raises(ValueError, match="mono 16 bits"):
        son.FileCapture(16000, path=str(tmp_path / "x.wav"))


# --- notifications Linux : nom affiché et icône du lanceur (24/10, plan P31 lot D) ---

def test_notification_linux_nom_affiche_et_icone(monkeypatch):
    from astrel.platform import linux
    calls = []
    monkeypatch.setattr(linux, "_run", lambda cmd, *a, **k: calls.append(cmd))
    monkeypatch.setenv("ASTREL_ASSISTANT_NAME", "Nova")
    linux.notify("Titre", "Corps")
    assert calls[0][:3] == ["notify-send", "--app-name=Nova", "--icon=astrel-hud"]
    assert calls[0][-2:] == ["Titre", "Corps"]
