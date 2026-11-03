"""Mission autonome EXPOSÉE (15/10, décision de l'utilisateur : la garder et l'ajouter
au HUD) : lancée et reprise par l'utilisateur seul, pause et arrêt permis à tous,
périmètre déclaré mis dans le garde-fou, arrêt d'urgence qui l'arrête.

Et la question de confirmation qui se tait dès que l'utilisateur a répondu à l'écran
(`speaker.cancel`), sans couper une autre parole."""
import chemins_machine  # chemins de la machine qui lance la suite
import json
import threading
import time
from pathlib import Path

import pytest

from astrel import config, core, guard, totalgate
from astrel import mission as mission_mod
from astrel.mission import Mission
from astrel.voice import speaker as speaker_mod
from conftest import CheminBash, remplacer_coeur

W = config.WORKDIR.resolve()
PROJ = W / "ProjetMission"          # périmètre de la mission (n'a pas besoin d'exister)
SECRET = PROJ / "secret"           # dossier interdit, DANS le périmètre
AUTRE = CheminBash(W / "AutreProjet")  # projet autorisé d'habitude, hors du périmètre
# 03/11 (CI Windows) : dans une commande Bash, un chemin s'écrit en « / » (Git Bash mange les « \ » hors guillemets).
PROJ_BASH = CheminBash(PROJ)


def wait_for(cond, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.005)
    return False


# ------------------------------------------------------------------ réservé à l'utilisateur

@pytest.mark.parametrize("cmd", [
    {"action": "start"}, {"action": "resume"}, {"text": "start"}, {"action": "lancer"},
    {"action": "reprendre"}, {"action": "n'importe quoi"},
    # lu d'une seule façon : « mode » ne masque plus « action »
    {"mode": "pause", "action": "start"}, {"mode": "stop", "action": "resume"},
])
def test_lancer_ou_reprendre_desserre_le_garde_fou(cmd):
    assert totalgate.loosens_guard("mission", cmd)
    assert totalgate.refused_to_claude("mission", {**cmd, "_from_claude": True})


@pytest.mark.parametrize("cmd", [{}, {"action": "status"}, {"action": "pause"}, {"action": "stop"},
                                 {"text": "arrête"}, {"action": "pause", "mode": "start"}])
def test_pause_arret_et_etat_restent_permis_a_claude(cmd):
    # `{"action": "pause", "mode": "start"}` : le cœur lit « pause », le contrôle aussi.
    assert not totalgate.loosens_guard("mission", cmd)
    assert totalgate.mission_action(cmd) in ("", "status", "pause", "stop", "arrête")


@pytest.fixture
def jm(monkeypatch, tmp_path):
    """Un Astrel minimal, la mission globale remise à zéro (fichier isolé par conftest)."""
    monkeypatch.setattr(core.mission, "data", {})
    monkeypatch.setattr(core.mission, "awaiting", False)
    monkeypatch.setattr(core.mission, "next_prompt", "")
    monkeypatch.setattr(core.channels, "workdir_of", lambda cid: str(PROJ) if cid == "projet" else "")
    j = object.__new__(core.Astrel)
    j.voice_out = False
    j._turn_active, j._turn_source = False, ""
    j._pending_confirm = None
    j._post_report = lambda text, channel="": None
    return j


SPEC = {"objective": "faire passer les tests", "done_when": "pytest -q rend 0", "channel": "projet"}


def test_claude_ne_lance_ni_ne_reprend_une_mission(jm):
    r = jm.on_command({"cmd": "mission", "action": "start", **SPEC, "_from_claude": True, "_via": "socket"})
    assert not r["ok"] and "réservée à l'utilisateur" in r["error"] and not core.mission.active
    # le piège « mode: pause + action: start » ne passe plus
    r = jm.on_command({"cmd": "mission", "mode": "pause", "action": "start", **SPEC, "_from_claude": True})
    assert not r["ok"] and not core.mission.active
    assert jm.on_command({"cmd": "mission", "action": "start", **SPEC, "_from_claude": False, "_via": "hud"})["ok"]
    assert jm.on_command({"cmd": "mission", "action": "pause", "_from_claude": True})["ok"]  # se mettre en pause : permis
    assert core.mission.status == mission_mod.PAUSED
    r = jm.on_command({"cmd": "mission", "action": "resume", "_from_claude": True})
    assert not r["ok"] and core.mission.status == mission_mod.PAUSED
    assert jm.on_command({"cmd": "mission", "action": "stop", "_from_claude": True})["ok"]  # s'arrêter : permis
    assert core.mission.status == mission_mod.STOPPED


def test_le_perimetre_par_defaut_est_le_dossier_du_canal(jm):
    r = jm.on_command({"cmd": "mission", "action": "start", "spec": {**SPEC, "forbidden_paths": "secret"},
                       "_from_claude": False, "_via": "hud"})
    assert r["ok"], r
    d = json.loads(config.MISSION_FILE.read_text())
    assert d["scope"] == [str(PROJ)] and d["forbidden_paths"] == [str(SECRET)]  # relatif : depuis le canal
    assert d["status"] == "active" and d["in_turn"] is False


def test_sans_perimetre_pas_de_mission(jm):
    r = jm.on_command({"cmd": "mission", "action": "start", "spec": {**SPEC, "channel": "general"}})
    assert not r["ok"] and "périmètre" in r["error"]
    r = jm.on_command({"cmd": "mission", "action": "start", "spec": {**SPEC, "scope": "/"}})
    assert not r["ok"] and "trop large" in r["error"]


def test_l_arret_d_urgence_arrete_la_mission(jm):
    jm.on_command({"cmd": "mission", "action": "start", "spec": SPEC})
    jm._total = False
    jm._tasks, jm._tasks_lock = {}, threading.Lock()
    jm.brain = type("B", (), {"stop_task": lambda self, tid: True, "interrupt": lambda self: None})()
    jm.speaker = type("S", (), {"interrupt": lambda self: None})()
    jm._drop_backlog = lambda why: None
    jm.emergency_stop(True)
    assert core.mission.status == mission_mod.STOPPED and core.mission.data["reason"] == "arrêt d'urgence"
    # pas de relance pendant l'arrêt d'urgence
    r = jm.on_command({"cmd": "mission", "action": "start", "spec": SPEC})
    assert not r["ok"] and "arrêt d'urgence" in r["error"]


def test_la_boucle_arrete_aussi_une_mission_sous_estop(jm):
    jm.on_command({"cmd": "mission", "action": "start", "spec": SPEC})
    config.ESTOP_FILE.write_text("x")  # posé par un autre chemin (--send estop)
    jm._mission_tick()
    assert core.mission.status == mission_mod.STOPPED


def test_tour_en_cours_ecrit_pour_le_hook(tmp_path):
    m = Mission()
    assert m.start({**SPEC, "scope": [str(PROJ)]})["ok"]
    assert m.take_prompt()
    assert json.loads(config.MISSION_FILE.read_text())["in_turn"] is True
    m.pause()  # en plein tour : la barrière reste jusqu'à la fin du tour
    assert json.loads(config.MISSION_FILE.read_text())["in_turn"] is True
    m.after_turn("travail fait\nMISSION : CONTINUE", {})
    d = json.loads(config.MISSION_FILE.read_text())
    assert d["status"] == "paused" and d["in_turn"] is False
    assert "Périmètre" in m._prompt(first=True) and str(PROJ) in m._prompt()


# ------------------------------------------------------------------ barrière du garde-fou

def start_mission(status="active", in_turn=False):
    m = Mission()
    assert m.start({**SPEC, "scope": [str(PROJ)], "forbidden_paths": [str(SECRET)]})["ok"]
    if status == "paused":
        if in_turn:
            m.take_prompt()
        m.pause()
    return m


def asks(tool, mode="autonome", cwd=str(PROJ), **inp):
    return guard.assess(tool, inp, mode=mode, cwd=cwd)


@pytest.fixture
def grants(monkeypatch, tmp_path):
    monkeypatch.setattr(guard, "GRANTS_FILE", tmp_path / "grants.json")


def test_sans_mission_rien_ne_change(grants):
    assert not asks("Write", file_path=str(AUTRE / "x.py")).needs_confirm
    assert not asks("Bash", command=f"echo x > {AUTRE}/x.txt").needs_confirm


@pytest.mark.parametrize("tool,inp", [
    ("Write", {"file_path": str(PROJ / "src" / "a.py")}),
    ("Edit", {"file_path": "src/a.py"}),  # relatif au dossier de Claude
    ("Write", {"file_path": "/tmp/essai.txt"}),
    ("Bash", {"command": "echo x > notes.txt && sed -i s/a/b/ src/a.py"}),
    ("Bash", {"command": f"rm -rf {PROJ_BASH}/build && mkdir -p /tmp/x"}),
    ("Bash", {"command": f"cat {AUTRE}/README.md; grep -rn todo {AUTRE}"}),  # lire ailleurs : libre
    ("Bash", {"command": "python -m pytest -q"}),
])
def test_dans_le_perimetre_libre(grants, tool, inp):
    start_mission()
    v = guard.assess(tool, inp, mode="autonome", cwd=str(PROJ))
    assert not v.needs_confirm, v


@pytest.mark.parametrize("tool,inp", [
    ("Write", {"file_path": str(AUTRE / "x.py")}),
    ("Edit", {"file_path": "../AutreProjet/x.py"}),
    ("Bash", {"command": f"echo x > {AUTRE}/x.txt"}),
    ("Bash", {"command": f"cp src/a.py {AUTRE}/"}),
    ("Bash", {"command": f"mv {AUTRE}/old.py src/"}),  # la source d'un déplacement compte aussi
    ("Bash", {"command": f"cd {AUTRE} && rm -rf build"}),
    ("Bash", {"command": f"rm {AUTRE}/x.txt"}),
    ("Bash", {"command": f"sed -i s/a/b/ {AUTRE}/x.py"}),
])
def test_hors_du_perimetre_demande_meme_en_autonome(grants, tool, inp):
    start_mission()
    v = guard.assess(tool, inp, mode="autonome", cwd=str(PROJ))
    assert v.needs_confirm and guard.L_MISSION_OUT in v.desc and not v.guard, v
    assert guard.K_MISSION_OUT in v.keys  # « oui pour 30 min » possible


@pytest.mark.parametrize("cmd", [
    f"mkdir -p {AUTRE}/build",
    f"git -C {AUTRE} pull",
    f"cd {AUTRE} && git commit -qam x",
    f"npm --prefix {AUTRE} install",
    f"ln -s {AUTRE} lien && echo x > lien/f.txt",
    "mkdir -p $X/y",  # chemin inconnu : demandé pendant la mission
])
def test_programmes_qui_modifient_hors_perimetre(grants, cmd):
    start_mission()
    v = guard.assess("Bash", {"command": cmd}, mode="autonome", cwd=str(PROJ))
    assert v.needs_confirm and guard.L_MISSION_OUT in v.desc, v


@pytest.mark.parametrize("cmd", [
    "mkdir -p build/out && git add -A && git commit -qm x",
    f"git -C {AUTRE} log --oneline -3; git -C {AUTRE} branch -a; git -C {AUTRE} remote -v",
    "ln -s ../ProjetMission/src lien",
    f"gzip -c {AUTRE}/x.log > /tmp/x.gz",
    "npm test",
])
def test_programmes_dans_le_perimetre_ou_en_lecture_libres(grants, cmd):
    start_mission()
    v = guard.assess("Bash", {"command": cmd}, mode="autonome", cwd=str(PROJ))
    assert guard.L_MISSION_OUT not in v.desc, v


def test_sans_mission_ces_programmes_ne_changent_rien(grants):
    for cmd in (f"mkdir -p {AUTRE}/build", f"git -C {AUTRE} pull", f"npm --prefix {AUTRE} install"):
        assert not guard.assess("Bash", {"command": cmd}, mode="autonome", cwd=str(PROJ)).needs_confirm


def test_sauvegardes_concurrentes_de_la_mission():
    """Écriture sous le verrou, fichier temporaire propre : le fichier final est la
    dernière version, jamais une plus ancienne, et aucun temporaire ne traîne."""
    m = start_mission()
    def hammer(k):
        for i in range(30):
            with m.lock:
                m.data["last"] = f"{k}-{i}"
                m._save()
    threads = [threading.Thread(target=hammer, args=(k,)) for k in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert json.loads(config.MISSION_FILE.read_text())["last"] == m.data["last"]
    assert not list(config.MISSION_FILE.parent.glob(".mission-*.tmp"))


def test_oui_pour_30_min_couvre_le_hors_perimetre(grants):
    start_mission()
    guard.grant(guard.K_MISSION_OUT)
    assert not asks("Write", file_path=str(AUTRE / "x.py")).needs_confirm
    assert not asks("Bash", command=f"echo x > {AUTRE}/x.txt").needs_confirm


@pytest.mark.parametrize("tool,inp", [
    ("Write", {"file_path": str(SECRET / "cle.txt")}),
    ("Bash", {"command": "echo x > secret/cle.txt"}),
    ("Bash", {"command": "cp -r modele/ secret/"}),
    ("Bash", {"command": f"rm -rf {PROJ_BASH}"}),  # le dossier qui contient l'interdit
    ("Bash", {"command": "rm -rf ./*"}),
    ("Bash", {"command": "git checkout -- secret/"}),
    ("Bash", {"command": "git clean -fdx"}),
])
def test_dossier_interdit_garde_fou_meme_en_total(grants, tool, inp):
    start_mission()
    guard.grant(guard.K_MISSION_OUT)
    v = guard.assess(tool, inp, mode="autonome", cwd=str(PROJ))
    assert v.needs_confirm and v.guard and not v.keys, v  # ni Total ni « 30 min » ne le couvrent
    assert not totalgate.total_grants(True, v.guard, False)


def test_pause_hors_tour_leve_la_barriere(grants):
    start_mission(status="paused", in_turn=True)
    assert asks("Write", file_path=str(AUTRE / "x.py")).needs_confirm  # le tour de mission n'est pas fini
    Mission().load()  # relance d'Astrel : plus de tour en cours
    assert not asks("Write", file_path=str(AUTRE / "x.py")).needs_confirm


def test_mission_finie_plus_de_barriere(grants):
    m = start_mission()
    m.stop()
    assert not asks("Write", file_path=str(AUTRE / "x.py")).needs_confirm


def test_fichier_de_mission_illisible_pas_de_barriere_ni_de_plantage(grants):
    config.MISSION_FILE.write_text("{pas du json")
    assert not asks("Write", file_path=str(AUTRE / "x.py")).needs_confirm
    config.MISSION_FILE.write_text(json.dumps({"status": "active", "scope": "/data", "forbidden_paths": 3}))
    assert not asks("Write", file_path=str(AUTRE / "x.py")).needs_confirm


def test_les_regles_existantes_restent(grants):
    """La barrière s'ajoute, elle ne remplace rien : un push reste critique dans le périmètre."""
    start_mission()
    v = asks("Bash", command="git push origin main")
    assert v.needs_confirm and "push" in v.desc
    assert asks("Write", file_path=str(config.ROOT / "astrel" / "guard.py")).guard


# ------------------------------------------------------------------ HUD (contrôle complet : tools/hud_check_rendu.py)

def test_le_hud_expose_la_mission_et_une_barre_multiligne():
    html = (config.ROOT / "hud" / "index.html").read_text(encoding="utf-8")
    js = (config.ROOT / "hud" / "src" / "main.js").read_text(encoding="utf-8")
    # 28/10 (T10, décision de l'utilisateur) : plus de bouton « Mission » dans la barre ; bandeau et panneau
    assert '<textarea id="ask"' in html and 'id="mission-banner"' in html and 'id="mission"' in html
    assert 'command("mission", { action: "start", spec })' in js
    assert "e.isComposing || composing" in js and "e.shiftKey" in js  # Maj+Entrée, composition IME
    assert "_from_claude" not in js  # l'origine est posée par le serveur, jamais par la page


# ------------------------------------------------------------------ la question se tait (lot B)

class GateEngine:
    """Synthèse en trois blocs ; la question bloque après son premier bloc."""
    name, sample_rate, online = "fake", 1000, False

    def __init__(self):
        self.gate = threading.Event()
        self.started = threading.Event()

    def load(self):
        pass

    def synthesize(self, text):
        yield f"[{text}|1]".encode()
        if text.startswith("Attention"):
            self.started.set()
            self.gate.wait(5)
        yield f"[{text}|2]".encode()
        yield f"[{text}|3]".encode()


class Player:
    all: list = []

    def __init__(self, rate, on_level=None):
        self.rate, self.accepted, self.stopped = rate, bytearray(), False
        Player.all.append(self)

    def accept(self, pcm):
        if not self.stopped:
            self.accepted += pcm

    def push(self, pcm):
        return not self.stopped

    def cut(self):
        self.stopped = True
        return b""

    def kill(self):
        self.stopped = True

    def close(self, wait=True):
        self.stopped = True


@pytest.fixture
def sp(monkeypatch):
    Player.all = []
    engine = GateEngine()
    monkeypatch.setattr(speaker_mod, "build_engine", lambda spec: engine)
    monkeypatch.setattr(speaker_mod, "Player", Player)
    s = speaker_mod.Speaker("fake", fallback="fake")
    s.load()
    yield s, engine
    engine.gate.set()
    s.close()


def played() -> bytes:
    return b"".join(bytes(p.accepted) for p in Player.all)


def test_cancel_coupe_la_question_en_cours_et_rien_d_autre(sp):
    s, engine = sp
    s.say("Attention, un push. Je continue ?", raw=True, tag="confirm-1")
    s.say("Voici la suite de ma réponse.", raw=True)  # sans rapport : doit être dite
    assert engine.started.wait(2)
    assert s.cancel("confirm-1")
    engine.gate.set()
    assert wait_for(lambda: s._idle.is_set() and b"suite de ma r" in played(), 3)
    out = played().decode()
    assert "[Attention, un push. Je continue ?|1]" in out and "Je continue ?|2]" not in out  # coupée net
    assert "[Voici la suite de ma réponse.|1][Voici la suite de ma réponse.|2][Voici la suite de ma réponse.|3]" in out


def test_cancel_retire_une_question_encore_en_file(sp):
    s, engine = sp
    s.say("Attention, première. Je continue ?", raw=True)  # sans étiquette : bloque la file
    assert engine.started.wait(2)
    s.say("Attention, un commit. Je continue ?", raw=True, tag="confirm-2")
    s.say("Et ensuite.", raw=True)
    assert s.cancel("confirm-2") and not s.cancel("confirm-2")
    s.say("C'est oui ou non ?", raw=True, tag="confirm-2")  # la relance de la même question : jetée
    engine.gate.set()
    assert wait_for(lambda: s._idle.is_set() and b"Et ensuite.|3]" in played(), 3)
    out = played().decode()
    assert "un commit" not in out and "oui ou non" not in out and "[Attention, première. Je continue ?|3]" in out


def test_une_phrase_etiquetee_n_est_pas_fondue_avec_la_suivante(sp):
    s, engine = sp
    s.say("Attention, bloque. Je continue ?", raw=True)
    assert engine.started.wait(2)
    s.say("Question courte.", raw=True, tag="q")
    s.say("Réponse courte.", raw=True)
    engine.gate.set()
    assert wait_for(lambda: s._idle.is_set() and b"courte.|3]" in played(), 3)
    out = played().decode()
    assert "[Question courte.|1]" in out and "[Réponse courte.|1]" in out  # deux synthèses distinctes


class InstantEngine:
    name, sample_rate, online = "fake", 1000, False

    def load(self):
        pass

    def synthesize(self, text):
        yield f"[{text}]".encode()  # synthèse instantanée


class SlowPlayer(Player):
    """Le son sort pendant 3 s après la fin de l'écriture (close attend), sauf s'il est tué."""

    def __init__(self, rate, on_level=None):
        super().__init__(rate, on_level)
        self.killed = threading.Event()

    def kill(self):
        self.stopped = True
        self.killed.set()

    def close(self, wait=True):
        if wait:
            self.killed.wait(3)
        self.stopped = True


@pytest.fixture
def slow(monkeypatch):
    Player.all = []
    monkeypatch.setattr(speaker_mod, "build_engine", lambda spec: InstantEngine())
    monkeypatch.setattr(speaker_mod, "Player", SlowPlayer)
    s = speaker_mod.Speaker("fake", fallback="fake")
    s.load()
    yield s
    for p in Player.all:
        p.killed.set()
    s.close()


@pytest.mark.parametrize("then", [None, "Voici la suite."])
def test_cancel_coupe_la_question_dont_le_son_joue_encore(slow, then):
    s = slow
    s.say("Attention, un push. Je continue ?", raw=True, tag="confirm-1")
    if then:
        s.say(then, raw=True)  # sans rapport : son propre lecteur, après la question
    assert wait_for(lambda: Player.all and Player.all[0].accepted and not s._speaking_tag, 2)
    time.sleep(0.5 if then is None else 0.1)  # synthèse finie, le son sort encore
    t0 = time.monotonic()
    assert s.cancel("confirm-1")
    question = Player.all[0]
    assert question.killed.is_set()
    if then:
        assert wait_for(lambda: len(Player.all) == 2 and b"suite" in Player.all[1].accepted, 2)
        assert not Player.all[1].killed.is_set() and b"Attention" not in Player.all[1].accepted
    assert wait_for(lambda: s._idle.is_set() or then, 2) and time.monotonic() - t0 < 2.5


class VoiceSpeaker:
    """Orateur du cœur : la question « dure » tant qu'elle n'est pas coupée."""
    paused = busy = pending = False

    def __init__(self):
        self.said, self.cancelled = [], []
        self.speaking = ""

    def say(self, text, raw=False, tag=""):
        self.said.append((text, tag))
        if tag:
            self.speaking = tag

    def cancel(self, tag):
        self.cancelled.append(tag)
        hit = self.speaking == tag
        self.speaking = ""
        return hit

    def wait_idle(self, timeout=None):
        if self.speaking:
            time.sleep(min(timeout or 0, 0.05))
            return False
        return True

    def interrupt(self):
        self.speaking = ""


def make_voice_astrel(monkeypatch):
    j = object.__new__(core.Astrel)
    j.voice_out, j.keyboard = True, False
    j.speaker = VoiceSpeaker()
    j.status = type("St", (), {"current": "sleeping", "set": lambda self, *a, **kw: True})()
    j.mic = type("M", (), {"wait_open": lambda self, timeout=None: True})()
    j._running, j._off, j._usage = True, False, "conversation"
    j._turn_active, j._turn_source = False, ""
    j._pending_confirm, j._confirm_evt, j._confirm_lock = None, threading.Event(), threading.Lock()
    j.recorder = object()  # le micro existe : l'écoute orale s'ouvrirait après la question
    j._chime = lambda kind: None
    j.listen_once = lambda *a, **kw: pytest.fail("écoute orale ouverte alors que l'utilisateur a répondu à l'écran")
    return j


def test_reponse_a_l_ecran_fait_taire_la_question_et_n_ouvre_pas_l_ecoute(monkeypatch):
    j = make_voice_astrel(monkeypatch)
    result = []
    t = threading.Thread(target=lambda: result.append(j._ask_confirmation("Bash", "un push", "git push")))
    t.start()
    assert wait_for(lambda: j.speaker.speaking, 2)  # la question est en train d'être dite
    tag = j.speaker.said[-1][1]
    assert tag.startswith("confirm-")
    t0 = time.monotonic()
    assert j._answer_confirmation(j._pending_confirm["id"], True)["ok"]  # clic sur « Oui » (HUD ou bulle)
    t.join(3)
    assert result == [True] and time.monotonic() - t0 < 1.5
    assert tag in j.speaker.cancelled  # la question s'est tue
    texts = [s for s, _ in j.speaker.said]
    assert "C'est oui ou non ?" not in texts and "D'accord." in texts


def test_reponse_a_la_voix_ne_coupe_rien(monkeypatch):
    j = make_voice_astrel(monkeypatch)
    j.speaker.wait_idle = lambda timeout=None: True  # question dite jusqu'au bout
    j.listen_once = lambda *a, **kw: "oui"
    assert j._ask_confirmation("Bash", "un push", "git push") is True
    assert j.speaker.cancelled == []


# ---------------------------------------------------------------- T25 : mission lancée par Astrel (29/10)

AUTO = {"cmd": "mission_auto", "objective": "migrer le site", "done_when": "npm test rend 0", "channel": "projet",
        "verification": {"controles": [{"type": "fichier_existe", "chemin": str(PROJ / "FAIT.md"), "genre": "fichier"}]},
        "_from_claude": True, "_via": "socket"}


@pytest.fixture
def ja(jm, monkeypatch):
    monkeypatch.setattr(core.Astrel, "_non_claude", lambda self: False)
    remplacer_coeur(monkeypatch, "bus", type("Bus", (), {"publish": lambda self, *a, **k: None})())
    jm._turn_active, jm._turn_source = True, "voix"
    return jm


def test_astrel_lance_une_mission_bornee_pendant_un_echange(ja):
    r = ja.on_command({**AUTO, "max_turns": 500, "max_minutes": 9999})
    assert r["ok"], r
    d = json.loads(config.MISSION_FILE.read_text())
    assert d["lancee_par"] == "astrel" and d["scope"] == [str(PROJ)] and d["status"] == "active"
    assert d["max_turns"] == config.MISSION_MAX_TURNS and d["max_minutes"] == config.MISSION_MAX_MINUTES  # plafonnés
    assert d["max_tokens"] == config.MISSION_MAX_TOKENS
    # la reprise après une pause reste à l'utilisateur
    assert ja.on_command({"cmd": "mission", "action": "pause", "_from_claude": True})["ok"]
    assert not ja.on_command({"cmd": "mission", "action": "resume", "_from_claude": True})["ok"]
    # une mission tourne déjà : pas de seconde
    ja.on_command({"cmd": "mission", "action": "stop"})
    assert ja.on_command({**AUTO, "max_turns": 3})["mission"]["max_turns"] == 3  # plus court : permis
    assert "tourne déjà" in ja.on_command(AUTO)["error"]


@pytest.mark.parametrize("source", ["fond", "réflexe", "equipe", "plan", "mission", "relève", ""])
def test_jamais_hors_d_un_echange_de_utilisateur(ja, source):
    ja._turn_source = source
    r = ja.on_command(AUTO)
    assert not r["ok"] and "échange lancé par l'utilisateur" in r["error"] and not core.mission.active


@pytest.mark.parametrize("scope", [["/"], [str(W)], ["/etc"], [str(Path.home())], [str(W.parent)]])
def test_perimetre_dans_les_projets_seulement(ja, scope):
    r = ja.on_command({**AUTO, "scope": scope})
    assert not r["ok"] and "DANS les projets" in r["error"] and not core.mission.active


def test_ni_sous_estop_ni_autre_fournisseur(ja, monkeypatch):
    config.ESTOP_FILE.write_text("1")
    assert "arrêt d'urgence" in ja.on_command(AUTO)["error"]
    config.ESTOP_FILE.unlink()
    monkeypatch.setattr(core.Astrel, "_non_claude", lambda self: True)
    assert "fournisseur" in ja.on_command(AUTO)["error"] and not core.mission.active


def test_outil_mcp_mission_start(monkeypatch):
    from astrel import mcp_server

    sent = []
    monkeypatch.setattr(mcp_server, "_core", lambda cmd, ensure_ascii=True: sent.append(cmd) or {"ok": True})
    critere = {"controles": [{"type": "fichier_existe", "chemin": f"{chemins_machine.PROJETS}/Site/dist", "genre": "dossier"}]}
    mcp_server.mission_start("migrer", "npm test rend 0", f"{chemins_machine.PROJETS}/Site", max_tours=5, verification=critere)
    assert sent[-1]["cmd"] == "mission_auto" and sent[-1]["scope"] == [f"{chemins_machine.PROJETS}/Site"]
    assert sent[-1]["max_turns"] == 5 and sent[-1]["verification"] == critere
    # P43 : sans critère typé, l'outil refuse avant même de joindre le cœur
    n = len(sent)
    with pytest.raises(Exception, match="verification"):
        mcp_server.mission_start("migrer", "npm test rend 0", f"{chemins_machine.PROJETS}/Site")
    assert len(sent) == n
