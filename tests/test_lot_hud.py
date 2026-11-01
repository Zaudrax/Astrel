"""Lot HUD / autonomie (14/10) : micro coupé, modes d'usage, confirmation qui
attend la fin de la parole, commandes réservées à l'utilisateur, arrêt d'urgence
gardé, canaux créés depuis le HUD ou la voix, autorisations 30 min."""
import json
import os
import socket
import threading
import time

import pytest

from astrel import config, core, guard, state
from astrel.channels import Channels
from astrel.events import bus
from astrel.floor import floor
from conftest import remplacer_coeur


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


class FakeStatus:
    current = "sleeping"

    def set(self, *a, **kw):
        return True


class FakeMic:
    def wait_open(self, timeout=None):
        return True


def make_astrel(monkeypatch, tmp_path):
    j = object.__new__(core.Astrel)
    j.voice_out = True
    j.keyboard = False
    j.speaker, j.status, j.mic = FakeSpeaker(), FakeStatus(), FakeMic()
    j._running, j._off, j._in_conversation, j._followup, j._force_wake = True, False, False, False, False
    j._turn_active, j._turn_source = False, ""
    j._pending_confirm, j._confirm_evt, j._confirm_lock = None, threading.Event(), threading.Lock()
    j.recorder = None
    return j


# ------------------------------------------------------------------ micro coupé pendant l'écoute

def test_couper_le_micro_pendant_l_ecoute_jette_la_phrase(monkeypatch, tmp_path):
    """Bug de l'audit (3.4) : 🎙 coupé pendant l'écoute, la phrase partait quand même chez Claude."""
    j = make_astrel(monkeypatch, tmp_path)
    sent = []

    def listen_once(timeout=None, abort=None):
        assert not abort.is_set()
        j.on_command({"cmd": "mute"})
        assert abort.is_set()  # l'enregistrement s'arrête aussitôt
        return "Merci, Mare."

    j.listen_once = listen_once
    j.handle = lambda text, *a, **kw: sent.append(text)
    j.conversation(followup=True)
    assert sent == [] and j._off


def test_fermer_l_ecoute_sans_couper_le_micro(monkeypatch, tmp_path):
    j = make_astrel(monkeypatch, tmp_path)
    sent = []

    def listen_once(timeout=None, abort=None):
        j.on_command({"cmd": "endlisten"})
        assert abort.is_set()
        return "bruit de fond"

    j.listen_once = listen_once
    j.handle = lambda text, *a, **kw: sent.append(text)
    j.conversation(followup=True)
    assert sent == [] and not j._off  # « Astrel » marche toujours


# ------------------------------------------------------------------ modes d'usage

@pytest.mark.parametrize("text, mode", [
    ("Mode silencieux.", "silencieux"),
    ("Passe en mode dictée", "dictee"),
    ("Astrel, mode conversation", "conversation"),
    ("fin de dictée", "conversation"),
    ("Tu peux reparler", "conversation"),
])
def test_bascule_de_mode_reconnue(text, mode):
    assert core.mode_phrase(text) == mode


@pytest.mark.parametrize("text", [
    "Explique-moi pourquoi le mode silencieux de mon téléphone ne marche plus",
    "Le mode conversation de l'app Boutique est cassé",
    "Silencieux comme une souris, le chat est entré",
])
def test_une_vraie_demande_n_est_pas_une_bascule(text):
    assert core.mode_phrase(text) == ""


def test_silencieux_ne_dit_rien_sauf_demande_expresse(monkeypatch, tmp_path):
    j = make_astrel(monkeypatch, tmp_path)
    j._usage, j._followup_setting = "conversation", 8.0
    assert j.set_usage("silencieux")["ok"]
    assert config.USAGE_FILE.read_text().strip() == "silencieux"  # retenu d'une relance à l'autre
    j.say("Voici la réponse.")
    assert j.speaker.said == []
    j.say("Compte rendu demandé.", force=True)  # clic sur 🔊 Lire
    assert j.speaker.said == ["Compte rendu demandé."]
    assert j._followup_s() == 0  # pas de fenêtre d'écoute pour une réponse non dite


def test_mode_tape_au_clavier_ne_part_pas_chez_claude(monkeypatch, tmp_path):
    j = make_astrel(monkeypatch, tmp_path)
    j._usage, j._followup_setting = "conversation", 8.0
    j.handle = lambda *a, **kw: pytest.fail("envoyé à Claude")
    r = j.on_command({"cmd": "say", "text": "mode silencieux"})
    assert r["ok"] and r["usage"] == "silencieux"


def test_ponctuation_dictee():
    assert core.dictation_clean("bonjour virgule ça va point d'interrogation") == "bonjour, ça va?"
    assert core.dictation_clean("première ligne à la ligne deuxième") == "première ligne\ndeuxième"


# ------------------------------------------------------------------ confirmation et parole

def test_la_confirmation_attend_que_utilisateur_ait_fini_de_parler(monkeypatch, tmp_path):
    """14/10 : une question de confirmation lui a coupé la parole (elle prend le micro)."""
    monkeypatch.setattr(config, "SPEECH_GRACE_S", 0.3)
    j = make_astrel(monkeypatch, tmp_path)
    j._usage = "conversation"
    seen = []

    def by_voice(question, box, answered):
        seen.append((floor.speaking(), j._confirm_evt.is_set()))
        box.update(allow=True, by="voix")
        answered.set()

    j._confirm_by_voice = by_voice
    floor.heard()  # il parle à l'instant
    t0 = time.monotonic()
    assert j._ask_confirmation("Bash", "un push", "git push")
    assert time.monotonic() - t0 >= 0.25  # la question a attendu son silence
    assert seen == [("", True)]  # posée une fois le silence revenu, micro pris à ce moment-là seulement


def test_la_confirmation_reste_cliquable_pendant_qu_il_parle(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SPEECH_GRACE_S", 30.0)
    j = make_astrel(monkeypatch, tmp_path)
    j._usage = "conversation"
    j._confirm_by_voice = lambda *a: pytest.fail("posée à l'oral pendant qu'il parle")
    floor.heard()
    result = []
    t = threading.Thread(target=lambda: result.append(j._ask_confirmation("Bash", "un push", "git push")))
    t.start()
    for _ in range(100):
        if j._pending_confirm:
            break
        time.sleep(0.01)
    assert not j._confirm_evt.is_set()  # son écoute n'est pas coupée
    assert j._answer_confirmation(j._pending_confirm["id"], True)["ok"]  # clic sur « Oui »
    t.join(2)
    assert result == [True]


def test_en_silencieux_la_question_ne_prend_jamais_le_micro(monkeypatch, tmp_path):
    j = make_astrel(monkeypatch, tmp_path)
    j._usage = "dictee"
    j._confirm_by_voice = lambda *a: pytest.fail("question dite en mode dictée")
    result = []
    t = threading.Thread(target=lambda: result.append(j._ask_confirmation("Bash", "un push", "git push")))
    t.start()
    for _ in range(100):
        if j._pending_confirm:
            break
        time.sleep(0.01)
    assert not j._confirm_evt.is_set()  # la dictée en cours continue
    j._answer_confirmation(j._pending_confirm["id"], False)
    t.join(2)
    assert result == [False]


# ------------------------------------------------------------------ réservé à l'utilisateur

@pytest.mark.parametrize("cmd", [
    {"cmd": "estop", "mode": "off"},
    {"cmd": "estop off"},
    {"cmd": "autonomy", "mode": "autonome"},
    {"cmd": "confirm", "id": "x", "allow": True},
    {"cmd": "mission", "action": "start", "objective": "x", "done_when": "y"},
])
def test_claude_ne_desserre_pas_le_garde_fou(monkeypatch, tmp_path, cmd):
    j = make_astrel(monkeypatch, tmp_path)
    monkeypatch.setattr(guard, "set_mode", lambda m: pytest.fail("mode changé par Claude"))
    r = j.on_command({**cmd, "_from_claude": True})
    assert not r["ok"] and "réservée à l'utilisateur" in r["error"]


def test_claude_peut_encore_arreter_et_se_taire(monkeypatch, tmp_path):
    j = make_astrel(monkeypatch, tmp_path)
    j._loops = None
    assert not j._loosens_guard("estop", {"mode": "on"})
    assert not j._loosens_guard("pause", {"on": True})
    assert not j._loosens_guard("report", {"text": "x"})
    assert not j._loosens_guard("mission", {"action": "stop"})


def test_arret_d_urgence_repose_s_il_est_efface(monkeypatch, tmp_path):
    """Un script de Claude qui efface runtime/ESTOP sans le nommer : le garde-fou
    ne le voit pas, le cœur le remet."""
    j = make_astrel(monkeypatch, tmp_path)
    config.ESTOP_FILE.write_text("x")
    j._estop_on = None
    j._estop_watch()  # premier passage : lit l'état
    config.ESTOP_FILE.unlink()
    j._estop_watch()
    assert config.ESTOP_FILE.exists()
    j._estop_on = False  # « Reprendre tout » de l'utilisateur
    config.ESTOP_FILE.unlink()
    j._estop_watch()
    assert not config.ESTOP_FILE.exists()


def test_descendance_des_process():
    assert state.descends_from(os.getpid(), os.getppid())
    assert state.descends_from(os.getpid(), os.getpid())
    assert not state.descends_from(os.getppid(), os.getpid())
    assert not state.descends_from(os.getpid(), None)


def test_le_canal_de_controle_reconnait_claude(monkeypatch, tmp_path):
    """Le canal de contrôle marque `_from_claude` d'après le numéro de process du
    client, quoi que le client écrive lui-même."""
    sock_path = tmp_path / "control.sock"
    monkeypatch.setattr(config, "CONTROL_SOCKET", sock_path)
    got = []
    bus.set_command_handler(lambda cmd: got.append(cmd) or {"ok": True})
    try:
        for claude in (os.getpid(), None):
            server = state.ControlServer(claude_pid=lambda c=claude: c)
            server.start()
            try:
                state.send_command(json.dumps({"cmd": "estop", "mode": "off", "_from_claude": False}))
            finally:
                server.stop()
        assert [c["_from_claude"] for c in got] == [True, False]
    finally:
        bus.set_command_handler(None)


# ------------------------------------------------------------------ autorisations 30 min

def test_autorisations_visibles_et_revocables(monkeypatch, tmp_path):
    monkeypatch.setattr(guard, "GRANTS_FILE", tmp_path / "grants.json")
    j = make_astrel(monkeypatch, tmp_path)
    guard.grant("bash:une publication git (push)")
    r = j.on_command({"cmd": "grants"})
    assert [g["key"] for g in r["grants"]] == ["bash:une publication git (push)"]
    assert j.on_command({"cmd": "grants", "action": "revoke", "key": ""})["grants"] == []


def test_oui_pour_30_min_a_l_ecran_ouvre_l_autorisation(monkeypatch, tmp_path):
    monkeypatch.setattr(guard, "GRANTS_FILE", tmp_path / "grants.json")
    j = make_astrel(monkeypatch, tmp_path)
    j._usage = "silencieux"
    result = []
    t = threading.Thread(target=lambda: result.append(
        j._ask_confirmation("Bash", "une publication git (push)", "git push", key="bash:push")))
    t.start()
    for _ in range(100):
        if j._pending_confirm:
            break
        time.sleep(0.01)
    j._answer_confirmation(j._pending_confirm["id"], True, always=True)
    t.join(2)
    assert result == [True] and guard.granted("bash:push")
    # La question suivante du même type ne revient pas.
    assert j._ask_confirmation("Bash", "une publication git (push)", "git push", key="bash:push")
    assert j._pending_confirm is None  # aucune question posée


# ------------------------------------------------------------------ canaux

@pytest.fixture
def chans(tmp_path, monkeypatch):
    work = tmp_path / "Divers"
    (work / "Trading").mkdir(parents=True)
    (work / "Studio" / "Astrel").mkdir(parents=True)
    monkeypatch.setattr(config, "WORKDIR", work)
    monkeypatch.setattr(config, "CHANNELS_FILE", tmp_path / "channels.json")
    monkeypatch.setattr(config, "CHANNEL_FILE", tmp_path / "channel.txt")
    (tmp_path / "channels.json").write_text(json.dumps(
        {"default": "general", "_aide": "gardé", "channels": [{"id": "general", "label": "Général"}]}))
    return Channels(), work


def test_creer_un_canal_relie_a_son_dossier(chans):
    c, work = chans
    r = c.create("Trading")
    assert r["ok"] and r["workdir"] == str((work / "Trading").resolve())
    assert c.workdir_of("trading") == r["workdir"]
    assert not c.create("trading")["ok"]  # existe déjà
    data = json.loads(config.CHANNELS_FILE.read_text())
    assert data["_aide"] == "gardé"  # le fichier édité à la main garde ses autres champs


def test_renommer_relier_supprimer(chans):
    c, work = chans
    c.create("Bourse")
    assert c.edit("bourse", label="Trading")["ok"]
    assert c.find("Trading") == "bourse"  # l'id reste : les messages rangés suivent
    assert c.edit("bourse", workdir="Trading")["ok"]  # relatif au dossier de travail
    assert c.workdir_of("bourse") == str((work / "Trading").resolve())
    assert not c.edit("bourse", workdir="/nulle/part")["ok"]
    assert not c.delete("general")["ok"]  # le canal par défaut reste
    assert c.delete("Trading")["ok"] and c.find("bourse") == ""


def test_canal_cree_par_la_commande_du_hud(chans, monkeypatch, tmp_path):
    c, _ = chans
    remplacer_coeur(monkeypatch, "channels", c)
    j = make_astrel(monkeypatch, tmp_path)
    r = j.on_command({"cmd": "channel_create", "label": "Trading"})
    assert r["ok"] and r["channel"] == "trading"
    assert j.on_command({"cmd": "channel_delete", "channel": "trading"})["ok"]


def test_dossier_du_canal_rappele_a_claude(chans, monkeypatch, tmp_path):
    """Un message tapé dans un canal relié à un dossier : la note part UNE fois."""
    c, work = chans
    remplacer_coeur(monkeypatch, "channels", c)
    c.create("Trading")
    j = make_astrel(monkeypatch, tmp_path)
    j._usage, j._note_channel = "conversation", ""
    j._send_lock = threading.Lock()
    j._turn_idle = threading.Event()
    j._reply, j._hushed = [], False
    sent = []

    class Brain:
        def send(self, prompt, images=None):
            sent.append(prompt)

    j.brain = Brain()
    monkeypatch.setattr(core.relief, "take_amorce", lambda: "")
    j.handle("où en est le backtest ?", source="clavier", channel="trading")
    j._turn_active = False
    j.handle("et les frais ?", source="clavier", channel="trading")
    assert sent[0].startswith(f"[Canal trading : dossier du projet {(work / 'Trading').resolve()}]")
    assert not sent[1].startswith("[Canal")


# ------------------------------------------------------------------ bulle : mini-chat et question

def test_mini_chat_de_la_bulle():
    from astrel.minichat import MiniChat

    chat = MiniChat(size=4)
    chat.apply({"type": "transcript", "text": "Où en est le build ?", "mid": "a"})
    chat.apply({"type": "reply_delta", "text": "Il est "})
    assert chat.snapshot()[-1].live
    chat.apply({"type": "reply_delta", "text": "en file."})
    chat.apply({"type": "reply", "text": "Il est en file.", "mid": "b"})
    assert [(ln.who, ln.text, ln.live) for ln in chat.snapshot()] == [
        ("user", "Où en est le build ?", False), ("astrel", "Il est en file.", False)]
    assert not chat.apply({"type": "reply", "text": "Il est en file.", "mid": "b"})  # HUD reconnecté : pas de doublon
    chat.apply({"type": "report", "text": "Compte rendu.", "mid": "c", "replay": True})
    assert chat.snapshot()[-1].text == "Compte rendu."


def test_question_de_la_bulle_et_sa_reponse():
    from astrel.minichat import MiniChat

    chat = MiniChat()
    assert chat.answer_command(True) is None  # rien en attente : rien n'est envoyé
    chat.apply({"type": "confirm", "id": "c1", "pending": True, "question": "Je continue ?"})
    assert chat.confirm["id"] == "c1"
    assert chat.answer_command(True, always=True) == {"cmd": "confirm", "confirm_id": "c1", "allow": True,
                                                      "always": True}
    chat.apply({"type": "confirm", "id": "c2", "pending": True})
    chat.apply({"type": "confirm", "id": "c2", "pending": False, "allow": False})  # répondue ailleurs
    assert chat.confirm is None
    chat.apply({"type": "usage", "mode": "dictee"})
    assert chat.usage == "dictee"


def test_reponse_de_la_bulle_acceptee_par_le_coeur(monkeypatch, tmp_path):
    j = make_astrel(monkeypatch, tmp_path)
    box, ev = {"allow": None, "by": "", "always": False}, threading.Event()
    j._pending_confirm = {"id": "c1", "event": ev, "box": box, "question": "?"}
    assert j.on_command({"cmd": "confirm", "confirm_id": "c1", "allow": True, "always": True})["ok"]
    assert box == {"allow": True, "by": "écran", "always": True} and ev.is_set()


# ------------------------------------------------------------------ pièces jointes

@pytest.fixture
def att_dir(tmp_path, monkeypatch):
    from astrel import attachments

    monkeypatch.setattr(attachments, "ATTACH_DIR", tmp_path / "attachments")
    return attachments


def _png(w, h):
    import cv2
    import numpy as np

    img = np.random.default_rng(1).integers(0, 255, (h, w, 3), dtype=np.uint8)  # du bruit : se compresse mal
    return cv2.imencode(".png", img)[1].tobytes()


def test_image_jointe_reduite_a_256_ko(att_dir):
    import base64

    raw = _png(1200, 900)
    assert len(raw) > 2 * 1024 * 1024
    p = att_dir.prepare([{"name": "capture.png", "type": "image/png", "data": base64.b64encode(raw).decode()}])
    sent = base64.b64decode(p.images[0]["data"])
    assert len(sent) <= 256 * 1024 and p.images[0]["media_type"] == "image/jpeg"
    saved = list((att_dir.ATTACH_DIR).iterdir())
    assert len(saved) == 1 and saved[0].name.endswith("capture.jpg") and oct(saved[0].stat().st_mode)[-3:] == "600"
    # 19/10 : `file` = le nom enregistré (jamais un chemin), pour la vignette du fil du HUD
    assert p.shown == [{"name": "capture.jpg", "image": True, "file": saved[0].name}]


def test_petite_image_envoyee_telle_quelle(att_dir):
    import base64

    raw = _png(40, 30)
    p = att_dir.prepare([{"name": "x.png", "type": "image/png", "data": base64.b64encode(raw).decode()}])
    assert base64.b64decode(p.images[0]["data"]) == raw and p.images[0]["media_type"] == "image/png"


def test_fichier_joint_par_son_chemin(att_dir):
    import base64

    p = att_dir.prepare([{"name": "../../etc/notes.txt", "type": "text/plain",
                          "data": base64.b64encode(b"bonjour").decode()}])
    assert p.images == [] and "Read" in p.notes[0]
    path = p.notes[0].split("l'utilisateur : ", 1)[1].split(" (", 1)[0]
    assert path.startswith(str(att_dir.ATTACH_DIR)) and path.endswith("notes.txt")  # pas de sortie du dossier


def test_pieces_jointes_refusees(att_dir, tmp_path):
    import base64

    with pytest.raises(ValueError, match="hors de"):
        att_dir.prepare([{"path": "/etc/passwd"}])
    with pytest.raises(ValueError, match="Mo au plus"):
        att_dir.prepare([{"name": "gros.bin", "data": base64.b64encode(b"0" * (6 * 1024 * 1024)).decode()}])
    with pytest.raises(ValueError, match="au plus"):
        att_dir.prepare([{"name": f"{i}.txt", "data": "eA=="} for i in range(7)])
    fake = base64.b64encode(b"pas une image").decode()
    p = att_dir.prepare([{"name": "faux.png", "type": "image/png", "data": fake}])
    assert p.images == [] and p.shown == [{"name": "faux.png", "image": False}]  # jointe comme fichier


def test_message_avec_image_part_chez_claude(att_dir, monkeypatch, tmp_path):
    import base64

    j = make_astrel(monkeypatch, tmp_path)
    got = []
    j.handle = lambda text, source, channel, joined=None: got.append((text, joined))
    r = j.on_command({"cmd": "say", "text": "", "attachments": [
        {"name": "c.png", "type": "image/png", "data": base64.b64encode(_png(50, 50)).decode()}]})
    for _ in range(100):
        if got:
            break
        time.sleep(0.01)
    shown = r["attachments"]
    assert r["ok"] and len(shown) == 1 and shown[0]["name"] == "c.png" and shown[0]["image"] is True
    assert "/" not in shown[0]["file"] and (att_dir.ATTACH_DIR / shown[0]["file"]).is_file()
    text, joined = got[0]
    assert text == "Regarde ce que je te joins." and len(joined.images) == 1
    r = j.on_command({"cmd": "say", "text": "x", "attachments": [{"path": "/etc/passwd"}]})
    assert not r["ok"] and "hors de" in r["error"]  # le refus revient au HUD


def test_image_du_fil_liste_blanche(att_dir, tmp_path):
    """19/10 : seules les IMAGES posées directement dans runtime/attachments sortent par
    `hud_image` (vignette du fil) ; ni chemin, ni lien, ni autre fichier."""
    import base64

    png = _png(30, 20)
    p = att_dir.prepare([{"name": "vue.png", "type": "image/png", "data": base64.b64encode(png).decode()},
                         {"name": "note.txt", "type": "text/plain", "data": base64.b64encode(b"secret").decode()}])
    img, txt = p.shown
    assert img["image"] and "file" in img and "file" not in txt
    assert att_dir.hud_image(img["file"]) == (png, "image/png")
    note = next(f.name for f in att_dir.ATTACH_DIR.iterdir() if f.name.endswith("note.txt"))
    assert att_dir.hud_image(note) is None  # pas une image
    # un texte déguisé en .png reste refusé : le type vient des octets, pas du nom
    (att_dir.ATTACH_DIR / "faux.png").write_bytes(b"pas une image")
    assert att_dir.hud_image("faux.png") is None
    # une image HORS du dossier, même atteinte par lien symbolique, ne sort pas
    outside = tmp_path / "dehors.png"
    outside.write_bytes(png)
    (att_dir.ATTACH_DIR / "lien.png").symlink_to(outside)
    assert att_dir.hud_image("lien.png") is None
    for bad in ("../dehors.png", "/etc/passwd", str(outside), ".cache.png", "", "a/b.png", "x" * 200):
        assert att_dir.hud_image(bad) is None, bad


def test_image_du_fil_servie_au_hud(att_dir, monkeypatch, tmp_path):
    """19/10 : /api/attachment/<file> rend la vignette avec la session (ou le jeton) du HUD,
    404 pour tout le reste, 401 sans jeton."""
    import asyncio
    import base64

    import aiohttp

    from astrel.server import HudServer

    # Piège du CLAUDE.md : jeton (et clé de session) à part, port à part.
    monkeypatch.setattr(config, "HUD_TOKEN_FILE", tmp_path / "hud-token")
    monkeypatch.setattr(config, "HUD_PORT", 47193)
    png = _png(30, 20)
    shown = att_dir.prepare([{"name": "vue.png", "type": "image/png", "data": base64.b64encode(png).decode()}]).shown
    (att_dir.ATTACH_DIR / "note.txt").write_bytes(b"secret")
    (tmp_path / "dehors.png").write_bytes(png)
    srv = HudServer()
    srv.start()
    try:
        async def get(path, token=True, origin=None):
            headers = {"X-Astrel-Token": srv.token} if token else {}
            if origin:
                headers["Origin"] = origin
            async with aiohttp.ClientSession() as s:
                async with s.get(f"http://127.0.0.1:47193{path}", headers=headers) as r:
                    return r.status, r.headers.get("Content-Type"), await r.read()

        async def go():
            return {
                "ok": await get(f"/api/attachment/{shown[0]['file']}"),
                # 19/10 (relecture) : même vérification d'origine que /api/command et /ws
                "etranger": await get(f"/api/attachment/{shown[0]['file']}", origin="https://site-malveillant.example"),
                "meme_origine": await get(f"/api/attachment/{shown[0]['file']}", origin="http://127.0.0.1:47193"),
                "localhost": await get(f"/api/attachment/{shown[0]['file']}", origin="http://localhost:47193"),
                "sans_jeton": await get(f"/api/attachment/{shown[0]['file']}", token=False),
                "texte": await get("/api/attachment/note.txt"),
                "remonte": await get("/api/attachment/..%2Fdehors.png"),
                "absent": await get("/api/attachment/rien.png"),
            }

        got = asyncio.run(go())
    finally:
        srv.stop()
    assert got["ok"][0] == 200 and got["ok"][1] == "image/png" and got["ok"][2] == png
    # Sans Origin (balise <img> de même origine, client local) : accepté, comme /ws et /api/snapshot.
    assert got["meme_origine"][0] == got["localhost"][0] == 200
    assert got["etranger"][0] == 403 and got["etranger"][2] != png
    assert got["sans_jeton"][0] == 401
    assert got["texte"][0] == got["remonte"][0] == got["absent"][0] == 404


def test_vignette_du_fil_dans_le_hud():
    """19/10 : le fil rend une vignette cliquable (visionneuse) pour une image partie, en DOM
    sûr ; repli sur la pastille du nom si l'image n'est plus là."""
    js = (config.ROOT / "hud" / "src" / "main.js").read_text(encoding="utf-8")
    fn = js[js.index("function sentAttachment"):js.index("// 17/10 (demande : « j'avais collé un lien")]
    assert 'el("button", "att-view att-thumb")' in fn and "openImage(url, name)" in fn
    assert "/api/attachment/${encodeURIComponent(file)}" in fn and "ATT_FILE.test(file)" in fn
    assert 'addEventListener("error", () => view.replaceWith(chip)' in fn
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML"):
        assert bad not in fn


def test_icones_des_reglages():
    """19/10 (demande : « des icônes dans les Réglages ») : chaque section et chaque réglage
    du cœur a une icône du jeu icons.js ; ids et clés inchangés."""
    import re

    from astrel import settings

    src = config.ROOT / "hud" / "src"
    js = (src / "settings.js").read_text(encoding="utf-8")
    names = set(re.findall(r'^  "?([\w-]+)"?: \[', (src / "icons.js").read_text(encoding="utf-8"), re.M))

    def table(const):
        body = js[js.index(f"const {const} = {{"):]
        body = body[:body.index("};")]
        return dict(re.findall(r'"?([\w.]+)"?: "([\w-]+)"', body))

    groups, items = table("GROUP_ICONS"), table("ITEM_ICONS")
    assert {g for g, _t in settings.GROUPS} <= set(groups)
    assert set(settings.ITEMS) <= set(items)
    assert set(groups.values()) | set(items.values()) | {"chevron", "settings"} <= names


# ---------------------------------------------------------------- mise en page (15/10)
# L'utilisateur : « les parties sont trop collées, pas de marge en haut ; fais une page plus
# grande ». Le rendu est contrôlé en Chrome headless (tools/hud_check_rendu.py, 1280/1440/1920) ;
# ici, ce qui ne doit pas se défaire sans qu'on le voie.

def test_hud_ouvert_maximise(monkeypatch, tmp_path):
    import shutil
    import subprocess

    from astrel import cli, server

    token_file = tmp_path / "hud-token"
    token_file.write_text("x" * 40)
    monkeypatch.setattr(config, "HUD_TOKEN_FILE", token_file)
    monkeypatch.setattr(server, "request_ticket", lambda token=None, timeout=5.0: "TICKET")
    launched = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: launched.append(args))
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
    assert cli.open_hud() == 0
    assert "--start-maximized" in launched[0]
    size = next(a for a in launched[0] if a.startswith("--window-size="))
    w, h = map(int, size.split("=")[1].split(","))
    assert w >= 1600 and h >= 1000


def test_tete_du_hud_reserve_sa_hauteur():
    html = (config.ROOT / "hud" / "index.html").read_text(encoding="utf-8")
    css = (config.ROOT / "hud" / "styles.css").read_text(encoding="utf-8")
    js = (config.ROOT / "hud" / "src" / "main.js").read_text(encoding="utf-8")
    # barre et bandeaux dans le même bloc : un bandeau ne peut plus chevaucher la barre
    head = html[html.index('<div id="head">'):html.index("<!-- Panneau droit")]
    for ident in ('id="topbar"', 'id="total-banner"', 'id="mission-banner"'):
        assert ident in head
    assert head.count('class="tgroup') == 3  # modes / actions / outils
    # sa hauteur est mesurée, et les panneaux, le fil et les notifications se placent dessous
    assert "--head-h" in js and "ResizeObserver" in js
    assert "top: calc(var(--head-h) + var(--gap))" in css
    assert "max-height: calc(100vh - var(--head-h)" in css


def test_ligne_total_levee_ne_pousse_rien():
    """19/10 (demande : les bandeaux Total et levée s'empilaient et poussaient les panneaux) :
    une seule ligne en surimpression, hors de la hauteur mesurée de la tête."""
    html = (config.ROOT / "hud" / "index.html").read_text(encoding="utf-8")
    css = (config.ROOT / "hud" / "styles.css").read_text(encoding="utf-8")
    js = (config.ROOT / "hud" / "src" / "main.js").read_text(encoding="utf-8")
    line = html[html.index('<div id="alert-line"'):html.index('<div id="lift-frame"')]
    for ident in ('id="total-banner"', 'id="total-off"', 'id="lift-banner"', 'id="lift-off"', 'id="lift-files"'):
        assert ident in line
    rule = css[css.index("#head > #alert-line {"):]
    rule = rule[:rule.index("}")]
    assert "position: fixed" in rule and "top: calc(var(--head-h) + var(--gap))" in rule
    assert "left: var(--col-l); right: var(--col-r)" in rule  # entre les panneaux
    assert "body.alert-on #dialog { max-height:" in css and "body.alert-on #toasts" in css
    assert "function syncAlertLine()" in js and js.count("syncAlertLine();") >= 2


def test_barre_du_haut_allegee():
    """19/10 (demande : « la barre du haut du HUD est chargée ») : mise en page seule."""
    html = (config.ROOT / "hud" / "index.html").read_text(encoding="utf-8")
    js = (config.ROOT / "hud" / "src" / "main.js").read_text(encoding="utf-8")
    bar = html[html.index('<header id="topbar">'):html.index("</header>")]
    # 23/10 (plan P28, demande de l'utilisateur) : graphe 3D, Recadrer / Orbite et compteurs retirés
    assert "data-action" not in html and 'id="graph-tools"' not in html and 'id="graph"' not in html
    assert 'id="stats"' not in html and 'id="brain-btn"' not in html
    assert "graph." not in js and "graph3d" not in js and "data-action" not in js
    assert not (config.ROOT / "hud" / "src" / "graph3d.js").exists()
    # connexion = pastille du logo, plus de ligne de texte à part
    brand = bar[bar.index('<div class="brand">'):bar.index('<div class="top-actions">')]
    assert 'id="conn"' in brand and "lastChild" not in js[js.index('on("connection"'):js.index('on("state"')]
    # Stop tout au bout de la barre (Éteindre retiré le 28/10, T10) ; même interrupteur Total
    assert bar.rstrip().endswith("</div>") and 'id="estop-chip"' in bar
    assert 'id="total-chip"' in bar and "Sans confirmation" in bar
    # 23/10 (demande de l'utilisateur) : panneau gauche (recherche, Inspecteur, Plus sollicités, Forces) et
    # Filtres retirés ; la place va à la conversation (--col-l = le bord)
    for gone in ('id="left"', 'id="search"', 'id="inspector"', 'id="top-hubs"', 'id="forces"', 'id="filters"'):
        assert gone not in html, gone
    for gone in ('$("#search")', '$("#forces")', '$("#filters")', "showInspector", "renderTopHubs"):
        assert gone not in js, gone
    css = (config.ROOT / "hud" / "styles.css").read_text(encoding="utf-8")
    assert "--col-l: var(--edge);" in css and "#left" not in css


def test_barre_sans_lire_simple_eteindre_mission():
    """28/10 (T10, décision de l'utilisateur) : « Lire aussitôt », « Simple », « Éteindre » et « Mission »
    retirés de la barre ; Autonome, Sans confirmation, Journal, Activité gardés. La sécurité reste
    visible (garde-fou levé, bandeau de mission, Stop tout) ; le mode Simple marche sans bouton."""
    root = config.ROOT / "hud"
    html = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "src" / "main.js").read_text(encoding="utf-8")
    css = (root / "styles.css").read_text(encoding="utf-8")
    langues = (root / "src" / "langues.js").read_text(encoding="utf-8")
    gone = ("reports-chip", "affichage-chip", "power-chip", "mission-chip")
    for ident in gone:
        # ni bouton, ni écouteur sur un élément absent (TypeError au chargement), ni règle CSS
        assert ident not in html and ident not in js and ident not in css, ident
    for ident in ("autonomy-chip", "total-chip", "audit-chip", "log-btn", "lift-chip", "estop-chip",
                  "mission-banner", "mission-banner-pause", "mission-banner-stop", "total-banner", "lift-banner"):
        assert f'id="{ident}"' in html, ident
    # mission : pause et arrêt par le bandeau, « Nouvelle mission » du panneau après une mission finie
    assert '$("#mission-banner-pause").onclick' in js and '$("#mission-banner-stop").onclick' in js
    assert 'btn("target", t("mission.new"), "", startMission)' in js
    # Simple : codé en CSS (data-affichage), posé par apparence.js et theme-boot.js, réglage dans Réglages
    assert ':root[data-affichage="simple"] :is(#autonomy-chip, #plans-chip, #todo-chip, #audit-chip)' in css
    assert "applyAffichage();" in js and "onAffichage(" in js
    # traductions des boutons retirés supprimées des deux dictionnaires (« mission.chip » sert au panneau ;
    # la boîte d'extinction « power.confirm_* » sert à Réglages › Système)
    for key in ('"top.reports', '"top.simple"', '"top.simple_', '"power.aria"', '"power.title"',
                '"mission.chip_', '"mission.menu_'):
        assert key not in langues and key not in js, key
    assert langues.count('"mission.chip": "Mission"') == 2 and 'data-i18n="mission.chip"' in html
    # accès repris dans les Réglages (décision du 28/10) : boutons exécutés par le HUD lui-même, qui envoie
    # ses commandes habituelles (mêmes droits) ; icônes des trois lignes
    settings_js = (root / "src" / "settings.js").read_text(encoding="utf-8")
    assert "export function onHudAction(name, fn)" in settings_js and "if (a.hud) {" in settings_js
    assert 'reports: "volume", mission: "target", "systeme.eteindre": "power"' in settings_js
    power = js[js.index('onHudAction("power"'):]
    power = power[:power.index("\n});")]
    assert 'title: t("power.confirm_title")' in power and "danger: true" in power and 'command("quit")' in power
    assert 'onHudAction("mission_start", startMission);' in js
    assert langues.count('"power.confirm_title"') == 2 and langues.count('"power.ok"') == 2


def test_visionneuse_d_image():
    """19/10 (demande : « une fois l'image collée je ne peux pas l'ouvrir en grand ») : la
    miniature de la barre ouvre l'image en grand ; rendu DOM sûr, rien d'ouvert hors du HUD."""
    src = config.ROOT / "hud" / "src"
    viewer = (src / "viewer.js").read_text(encoding="utf-8")
    js = (src / "main.js").read_text(encoding="utf-8")
    css = (config.ROOT / "hud" / "styles.css").read_text(encoding="utf-8")
    # repose sur openPanel (rôle dialog, Échap, croix, clic à côté, focus rendu), nom accessible
    assert 'from "./modal.js"' in viewer and "openPanel(" in viewer and '"aria-label"' in viewer
    # jamais de HTML injecté, ni de boîte du navigateur, ni de fenêtre hors du HUD
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "window.open", "confirm(", "prompt(", "alert("):
        assert bad not in viewer
    # source : URL de données d'image, ou blob:/chemin de la même origine seulement
    assert "u.origin === location.origin" in viewer and "data:image" in viewer
    # la miniature est un bouton qui ne prend pas le focus de la barre de saisie
    thumb = js[js.index("function renderAttachments"):js.index("function stageAttachments")]
    # 22/10 : par viewAttachment, qui garde l'URL d'objet valide tant que la visionneuse est ouverte
    view = js[js.index("function viewAttachment"):js.index("function warnVoiceWithoutPending")]
    assert 'el("button", "att-view")' in thumb and "viewAttachment(a)" in thumb
    assert "openImage(url, a.name)" in view and "viewedUrls.set(url, v.closed)" in view
    assert 'addEventListener("mousedown", (e) => e.preventDefault())' in thumb
    assert ".modal.wide.viewer" in css and "object-fit: contain" in css


def test_barre_de_saisie_sur_deux_etages():
    """19/10 (l'utilisateur, « comme ChatGPT ») : le texte en haut, pleine largeur ; la rangée de
    boutons dessous, hauteur fixe ; mêmes id et data-mode (main.js inchangé côté handlers)."""
    html = (config.ROOT / "hud" / "index.html").read_text(encoding="utf-8")
    css = (config.ROOT / "hud" / "styles.css").read_text(encoding="utf-8")
    js = (config.ROOT / "hud" / "src" / "main.js").read_text(encoding="utf-8")
    comp = html[html.index('<div id="composer">'):html.index("</section>")]
    row = comp[comp.index('class="composer-row"'):]
    tools = row[row.index('class="composer-tools"'):row.index('class="composer-actions"')]
    acts = row[row.index('class="composer-actions"'):]
    assert comp.index('id="ask"') < comp.index('class="composer-row"')  # texte au-dessus
    for ident in ("mic-btn", "voice-btn", "mute-all-btn", "wake-btn", "usage", "attach-btn", "attach-input"):
        assert f'id="{ident}"' in tools
    for ident in ("pause-btn", "stop-btn", "reset-btn", "log-btn"):
        assert f'id="{ident}"' in acts
    assert [m for m in ("conversation", "silencieux", "dictee") if f'data-mode="{m}"' in tools] == [
        "conversation", "silencieux", "dictee"]
    assert "flex-direction: column" in css[css.index("#composer {"):css.index(".composer-row")]
    assert ".composer-row { display: flex;" in css and "height: 34px; flex: none;" in css
    assert "max-height: 164px" in css and "const ASK_MAX_PX = 8 * 19 + 12;" in js
    # bouton ↓ placé au-dessus du bas du fil, mesuré (la barre grandit)
    assert "bottom: var(--jump-b" in css and '"--jump-b"' in js
