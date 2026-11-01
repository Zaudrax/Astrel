"""16/10, deux demandes de l'utilisateur.

1. Pièce jointe collée dans le HUD puis phrase DITE : la phrase partait seule et la
   pièce restait dans la barre (il fallait Entrée). Le HUD dépose ses pièces en attente
   au cœur (`attach_stage`) ; la phrase dite suivante part AVEC elles, en un seul
   message, et une pièce partie ne repart jamais une seconde fois (Entrée ensuite).
2. Trois coupures dans la barre : micro seul (`mute`, inchangé), voix seule
   (`voice_mute` = mode Silencieux), les deux (`mute_all`). Refusées à Claude, comme la
   réactivation du micro ; Claude ne peut pas non plus faire reparler une voix coupée."""
import base64
import threading

import pytest

from astrel import attachments, audio, config, core, state
from astrel.events import bus


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


class FakeMic:
    muted = False

    def wait_open(self, timeout=None):
        return True


class FakeRecorder:
    def record(self, mic, initial_timeout=None, abort=None, on_voice=None):
        return b"\x01\x00" * 16000


class FakeStt:
    """Une seule phrase entendue, puis le silence (la conversation s'arrête)."""

    def __init__(self, text):
        self.heard = [text]

    def transcribe(self, pcm):
        return self.heard.pop(0) if self.heard else ""


@pytest.fixture
def j(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(audio, "chime", lambda *_: None)
    monkeypatch.setattr(attachments, "ATTACH_DIR", tmp_path / "attachments")
    j = object.__new__(core.Astrel)
    j.voice_out = True
    j.keyboard = False
    j.speaker, j.mic, j.status = FakeSpeaker(), FakeMic(), state.StatePublisher()
    j.status.set(state.SLEEPING)
    j._running, j._off, j._in_conversation, j._followup, j._force_wake = True, False, False, False, False
    j._turn_active, j._turn_source, j._recording = False, "", False
    j._pending_confirm, j._confirm_evt, j._confirm_lock = None, threading.Event(), threading.Lock()
    j._ear = threading.Lock()
    j._usage, j._followup_setting, j._dictation_text = "conversation", 8.0, ""
    j.recorder = FakeRecorder()
    j.sent = []
    j.handle = lambda text, *a, **kw: j.sent.append((text, kw.get("joined")))
    return j


@pytest.fixture
def events():
    got = []
    stop = bus.subscribe(got.append)
    yield got
    stop()


def piece(aid, name="note.txt", data=b"bonjour"):
    return {"id": aid, "name": name, "type": "text/plain", "data": base64.b64encode(data).decode()}


def hud(cmd, **kw):
    return {"cmd": cmd, "_via": "hud", "_from_claude": False, **kw}


def claude(cmd, **kw):
    return {"cmd": cmd, "_via": "control", "_from_claude": True, **kw}


def dire(j, phrase):
    j.stt = FakeStt(phrase)
    j.conversation(followup=True)


# ------------------------------------------------------------------ pièce jointe + phrase dite

def test_phrase_dite_part_avec_les_pieces_en_attente(j, events):
    assert j.on_command(hud("attach_stage", attachments=[piece("a1"), piece("a2", "plan.txt")]))["staged"] == 2
    dire(j, "Regarde ces deux fichiers.")
    assert len(j.sent) == 1  # UN seul message : la phrase et les pièces ensemble
    text, joined = j.sent[0]
    assert text == "Regarde ces deux fichiers."
    assert [s["name"] for s in joined.shown] == ["note.txt", "plan.txt"]
    assert len(joined.notes) == 2 and all("Read" in n for n in joined.notes)  # chemins enregistrés pour Claude
    assert [e["ids"] for e in events if e["type"] == "attachments_sent"] == [["a1", "a2"]]  # le HUD vide sa barre
    dire(j, "Et maintenant ?")
    assert j.sent[1] == ("Et maintenant ?", None)  # déjà parties : pas une seconde fois


def test_sans_piece_en_attente_rien_ne_change(j, events):
    dire(j, "Quelle heure est-il ?")
    assert j.sent == [("Quelle heure est-il ?", None)]
    assert not [e for e in events if e["type"] == "attachments_sent"]


def test_barre_videe_par_le_hud_plus_rien_en_attente(j):
    j.on_command(hud("attach_stage", attachments=[piece("a1")]))
    assert j.on_command(hud("attach_stage", attachments=[]))["staged"] == 0  # pièce retirée (croix) ou page fermée
    dire(j, "Bonjour.")
    assert j.sent == [("Bonjour.", None)]


def test_entree_apres_la_phrase_dite_ne_renvoie_pas_la_piece(j):
    j.on_command(hud("attach_stage", attachments=[piece("a1")]))
    dire(j, "Lis ça.")
    # Le HUD n'a pas encore reçu `attachments_sent` et l'utilisateur appuie sur Entrée.
    j.handle = lambda *a, **kw: pytest.fail("pièce envoyée deux fois")
    r = j.on_command(hud("say", text="", attachments=[piece("a1")]))
    assert r["ok"] and r["already_sent"]


def test_entree_retire_les_pieces_du_depot(j, monkeypatch):
    j.on_command(hud("attach_stage", attachments=[piece("a1")]))
    done = threading.Event()
    typed = []
    j.handle = lambda text, source, channel, joined=None: (typed.append((text, joined)), done.set())
    r = j.on_command(hud("say", text="tiens", attachments=[piece("a1")]))
    assert r["ok"] and r["attachments"] == [{"name": "note.txt", "image": False}]
    assert done.wait(2) and typed[0][0] == "tiens"
    j.handle = lambda text, *a, **kw: j.sent.append((text, kw.get("joined")))
    j.on_command(hud("attach_stage", attachments=[piece("a1")]))  # dépôt en retard (ordre non garanti) : ignoré
    dire(j, "Autre chose.")
    assert j.sent == [("Autre chose.", None)]


def test_piece_refusee_la_phrase_part_seule_et_la_piece_reste(j, events):
    gros = piece("big", "gros.bin", b"0" * (attachments.MAX_FILE_BYTES + 1))
    j.on_command(hud("attach_stage", attachments=[gros]))
    dire(j, "Regarde.")
    assert j.sent == [("Regarde.", None)]
    assert any(e["type"] == "notice" and "sans les pièces jointes" in e["text"] for e in events)
    assert not [e for e in events if e["type"] == "attachments_sent"]
    assert [i["id"] for i in j._attach["staged"]] == ["big"] and "big" not in j._attach["spent"]


def test_bascule_ou_adieu_dits_n_emportent_pas_les_pieces(j):
    j.on_command(hud("attach_stage", attachments=[piece("a1")]))
    dire(j, "Mode silencieux.")
    assert j.sent == [] and [i["id"] for i in j._attach["staged"]] == ["a1"]


def test_depot_refuse_a_claude(j):
    r = j.on_command(claude("attach_stage", attachments=[piece("a1")]))
    assert not r["ok"] and j._attach["staged"] == []


def test_depot_illisible_ou_trop_de_pieces(j):
    assert not j.on_command(hud("attach_stage", attachments="x"))["ok"]
    assert not j.on_command(hud("attach_stage", attachments=[piece(str(i)) for i in range(7)]))["ok"]
    # sans `id`, ou avec un chemin : ignoré (le dépôt ne prend que le contenu envoyé par le HUD)
    assert j.on_command(hud("attach_stage", attachments=[{"name": "x", "data": "eA=="}]))["staged"] == 0
    j.on_command(hud("attach_stage", attachments=[{"id": "p", "path": "/etc/passwd"}]))
    assert "path" not in j._attach["staged"][0]


# ------------------------------------------------------------------ trois coupures

def test_voix_seule_coupee_puis_reactivee(j):
    r = j.on_command(hud("voice_mute", on=True))
    assert r["ok"] and j.usage == "silencieux" and r["voice_cut"] and not j._off  # il écoute toujours
    assert config.USAGE_FILE.read_text().strip() == "silencieux"  # retenu comme le mode
    j.say("Réponse.")
    assert j.speaker.said == []
    r = j.on_command(hud("voice_mute", on=False))
    assert r["ok"] and j.usage == "conversation" and not r["voice_cut"]


def test_voix_seule_bascule_sans_argument(j):
    j.on_command({"cmd": "voice_mute"})  # CLI : --send voice_mute
    assert j.usage == "silencieux"
    j.on_command({"cmd": "voice_mute", "text": "off"})
    assert j.usage == "conversation"
    assert not j.on_command({"cmd": "voice_mute", "text": "peut-être"})["ok"]


def test_couper_les_deux_puis_tout_reactiver(j):
    r = j.on_command(hud("mute_all", on=True))
    assert r["ok"] and r["muted"] and r["voice_cut"]
    assert j._off and j.usage == "silencieux" and j.status.muted and core.MUTE_FILE.exists()
    assert j.status.current == state.OFF
    assert j.listen_once() == ""  # plus rien n'est écouté
    r = j.on_command(hud("mute_all", on=False))
    assert r["ok"] and not r["muted"] and not r["voice_cut"]
    assert not j._off and j.usage == "conversation" and not core.MUTE_FILE.exists()


def test_couper_les_deux_quand_le_micro_l_est_deja(j):
    j.on_command(hud("mute"))
    gen = j._mute_gen
    j.on_command(hud("mute_all", on=True))
    assert j._off and j.usage == "silencieux" and j._mute_gen == gen  # pas de seconde coupure
    j.on_command(hud("mute_all"))  # bascule : les deux étaient coupés → tout revient
    assert not j._off and j.usage == "conversation"


def test_dictee_puis_couper_les_deux(j):
    j._usage = "dictee"
    j.on_command(hud("mute_all", on=True))
    assert j._off and j.usage == "silencieux"


@pytest.mark.parametrize("cmd", ["voice_mute", "mute_all"])
@pytest.mark.parametrize("on", [True, False])
def test_coupures_refusees_a_claude(j, cmd, on):
    j._usage, j._off = ("silencieux", True) if not on else ("conversation", False)
    before = (j._usage, j._off)
    r = j.on_command(claude(cmd, on=on))
    assert not r["ok"] and "l'utilisateur" in r["error"]
    assert (j._usage, j._off) == before


def test_claude_ne_fait_pas_reparler_une_voix_coupee(j):
    j.on_command(hud("voice_mute", on=True))
    r = j.on_command(claude("usage", mode="conversation"))
    assert not r["ok"] and j.usage == "silencieux"
    r = j.on_command(claude("say", text="mode conversation"))  # tapé par Claude : même refus
    assert not r["ok"] and j.usage == "silencieux"
    assert j.on_command(hud("usage", mode="conversation"))["ok"] and j.usage == "conversation"  # the user : oui


def test_claude_peut_toujours_passer_en_silencieux(j):
    """Couper, c'est resserrer : la règle ne vise que ce qui fait REPARLER."""
    assert j.on_command(claude("usage", mode="silencieux"))["ok"] and j.usage == "silencieux"
