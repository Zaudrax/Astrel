"""Relève automatique de session (contexte trop gros, Astrel au repos) et arrêt d'urgence."""
import json
import queue
import threading
import time

import pytest

from astrel import audio, config, core, relief
from astrel.brain import Brain, BrainEvent
from astrel.events import bus
from astrel.floor import Held, backlog, floor


@pytest.fixture(autouse=True)
def _note_isolee(tmp_path, monkeypatch):
    """La note des chantiers et les dépôts git : jamais ceux de la vraie machine."""
    monkeypatch.setattr(relief, "NOTE_FILE", tmp_path / "CHANTIERS-EN-COURS.md")
    monkeypatch.setattr(relief, "git_repos", lambda base=None, depth=3: [])


class _Speaker:
    busy = False
    paused = False

    def interrupt(self):
        pass


class _Status:
    current = "sleeping"

    def set(self, *a, **k):
        return True


class _FakeBrain:
    def __init__(self, tokens=0):
        self.context_tokens = tokens
        self.sent, self.restarts, self.interrupts, self.stopped = [], [], 0, []
        self.alive = True
        self.model, self.session_id, self.quota = "sonnet", "", {}

    def send(self, text, images=None):
        self.sent.append(text)

    def restart(self, fresh=False):
        self.restarts.append(fresh)
        self.context_tokens = 0

    def interrupt(self):
        self.interrupts += 1

    def stop_task(self, tid):
        self.stopped.append(tid)
        return True


def make_astrel(monkeypatch, tokens=160_000, immediate=True):
    monkeypatch.setattr(audio, "chime", lambda *_: None)
    monkeypatch.setattr(config, "CONTEXT_RELIEF_TOKENS", 150_000)
    monkeypatch.setattr(config, "RELIEF_IDLE_S", 60.0)
    monkeypatch.setattr(config, "RELIEF_IMMEDIATE", immediate)
    j = object.__new__(core.Astrel)
    j.voice_out = False
    j.speaker, j.status = _Speaker(), _Status()
    j.brain = _FakeBrain(tokens)
    j._off = j._in_conversation = j._followup = j._recording = False
    j._turn_active, j._turn_source, j._last_event = False, "", 0.0
    j._last_turn = time.time() - 600
    j._turn_idle = threading.Event()
    j._turn_lock, j._send_lock, j._tasks_lock = threading.Lock(), threading.Lock(), threading.Lock()
    j._tasks = {}
    from astrel.voice import SentenceSplitter
    j._splitter, j._spoken_any, j._reply, j._hushed = SentenceSplitter(), False, [], False
    said = []
    j.say = lambda text, raw=False, **_: said.append(text)
    j.relief = relief.Relief(j)
    return j, said


def end_turn(j, text="Note à jour."):
    j._on_stream(BrainEvent("message_start"))
    j._on_stream(BrainEvent("text", text=text + " "))
    j._on_stream(BrainEvent("turn_end", text=text))


def test_releve_complete_au_repos(monkeypatch):
    j, said = make_astrel(monkeypatch)
    assert j.relief.tick() == "save"
    assert "CHANTIERS-EN-COURS" in j.brain.sent[0] and "160 k" in j.brain.sent[0]
    assert j._turn_active and j._turn_source == "relève"
    assert j.relief.tick() == ""            # la sauvegarde tourne
    end_turn(j)
    assert said == []                        # jamais dit
    assert j.relief.tick() == "renew"
    assert j.brain.restarts == [True]        # session neuve
    assert config.RELIEF_AMORCE_FILE.exists()
    # Le premier message de l'utilisateur porte l'amorce, une seule fois.
    j.handle("Où en est le build ?", source="clavier")
    assert j.brain.sent[-1].startswith("[Nouvelle session : relève automatique")
    assert j.brain.sent[-1].endswith("Où en est le build ?")
    end_turn(j, "Il tourne.")
    j.handle("Merci", source="clavier")
    assert j.brain.sent[-1] == "Merci"


_HARD = [
    (lambda j: setattr(j, "_turn_active", True), "tour en cours"),
    (lambda j: setattr(j, "_in_conversation", True), "conversation en cours"),
    (lambda j: setattr(j, "_pending_confirm", {"id": "c"}), "confirmation en cours"),
    (lambda j: floor.heard(), "L'utilisateur vient de parler"),
    (lambda j: config.ESTOP_FILE.write_text("1"), "arrêt d'urgence"),
]
# Blocages « mous » (plan P9) : levés en relève immédiate, gardés dans l'ancien comportement.
_SOFT = [
    (lambda j: j._tasks.update(t1={"id": "t1"}), "tâche de fond en cours"),
    (lambda j: setattr(j, "_last_turn", time.time()), "échange récent"),
    (lambda j: backlog.add(Held("annonce", ["x"])), "messages en attente"),
]


@pytest.mark.parametrize("immediate", [True, False])
@pytest.mark.parametrize("setup, reason", _HARD)
def test_jamais_pendant_un_travail(monkeypatch, setup, reason, immediate):
    j, _ = make_astrel(monkeypatch, immediate=immediate)
    setup(j)
    assert j.relief.busy(soft=not immediate)
    assert j.relief.tick() == "" and j.brain.sent == [] and j.brain.restarts == []


@pytest.mark.parametrize("setup, reason", _SOFT)
def test_ancien_comportement_attend_le_calme(monkeypatch, setup, reason):
    j, _ = make_astrel(monkeypatch, immediate=False)
    setup(j)
    assert j.relief.busy() == reason
    assert j.relief.tick() == "" and j.brain.sent == [] and j.brain.restarts == []


@pytest.mark.parametrize("setup, reason", _SOFT)
def test_releve_immediate_n_attend_pas_le_calme(monkeypatch, setup, reason):
    j, _ = make_astrel(monkeypatch)
    setup(j)
    assert j.relief.busy(soft=False) == ""
    assert j.relief.tick() in ("stop", "save")


def test_contexte_petit_rien(monkeypatch):
    j, _ = make_astrel(monkeypatch, tokens=40_000)
    assert j.relief.tick() == "" and j.brain.sent == []
    monkeypatch.setattr(config, "CONTEXT_RELIEF_TOKENS", 0)  # désactivée
    j.brain.context_tokens = 10**6
    assert j.relief.tick() == ""


def test_utilisateur_parle_pendant_la_sauvegarde(monkeypatch):
    """Ancien comportement (RELIEF_IMMEDIATE faux) : son message abandonne la relève."""
    j, said = make_astrel(monkeypatch, immediate=False)
    j.relief.tick()
    j.handle("Au fait, tu peux regarder mes mails ?", source="voix")
    assert j._turn_source == "voix"
    assert j.relief.tick() == "abort"
    end_turn(j, "Je regarde.")
    assert said == ["Je regarde."]            # sa réponse est dite normalement
    assert j.brain.restarts == []
    assert j.relief.tick() == ""              # pas de nouvel essai tout de suite


def test_sauvegarde_en_erreur_pas_de_releve(monkeypatch):
    j, _ = make_astrel(monkeypatch)
    j._on_turn_error = lambda ev: None
    j.relief.tick()
    j._on_stream(BrainEvent("error", text="limite atteinte"))
    assert j.relief.tick() == "abort" and j.brain.restarts == []


def test_bruit_a_la_fin_de_la_sauvegarde_on_attend(monkeypatch):
    """19/10 : un bruit capté par le micro juste avant la fin de la sauvegarde ne doit pas
    annuler la relève pour 15 min : on attend un instant calme (RELIEF_CALM_WAIT_S)."""
    j, _ = make_astrel(monkeypatch)
    j.relief.tick()
    end_turn(j)
    floor.heard()                              # bruit capté à l'instant
    assert j.relief.tick() == ""               # on attend, pas d'abandon
    assert j.relief.phase == "save" and j.brain.restarts == []
    monkeypatch.setattr(config, "SPEECH_GRACE_S", 0.0)   # le bruit est passé
    assert j.relief.tick() == "renew" and j.brain.restarts == [True]


def test_attente_du_calme_a_une_limite(monkeypatch):
    j, _ = make_astrel(monkeypatch)
    monkeypatch.setattr(config, "RELIEF_CALM_WAIT_S", 0.0)
    j.relief.tick()
    end_turn(j)
    floor.heard()
    assert j.relief.tick() == "abort" and j.brain.restarts == []


def test_agent_lance_apres_sauvegarde_pas_d_attente(monkeypatch):
    """Ancien comportement : un vrai travail (tâche de fond) n'est pas un bruit, abandon immédiat."""
    j, _ = make_astrel(monkeypatch, immediate=False)
    j.relief.tick()
    end_turn(j)
    j._tasks["t1"] = {"id": "t1"}
    assert j.relief.tick() == "abort"


def test_agent_lance_pendant_la_sauvegarde(monkeypatch):
    j, _ = make_astrel(monkeypatch, immediate=False)
    j.relief.tick()
    j._tasks["t1"] = {"id": "t1"}
    end_turn(j)
    assert j.relief.tick() == "abort" and j.brain.restarts == []


def test_contexte_mesure_depuis_le_flux():
    b = Brain()
    b._events = queue.Queue()
    usage = {"input_tokens": 10, "cache_read_input_tokens": 120_000, "cache_creation_input_tokens": 5_000}
    b._events.put({"type": "assistant", "message": {"usage": usage, "content": []}})
    # Un sous-agent ne compte pas.
    b._events.put({"type": "assistant", "parent_tool_use_id": "x",
                   "message": {"usage": {"cache_read_input_tokens": 999_999}, "content": []}})
    out = []
    for ev in b.events(idle_s=0.01):
        if ev.kind == "idle":
            break
        out.append(ev)
    assert [e.kind for e in out] == ["context"] and out[0].data["tokens"] == 125_010
    assert b.context_tokens == 125_010


def test_contexte_sur_la_fixture_reelle():
    from pathlib import Path
    b = Brain()
    b._events = queue.Queue()
    for line in (Path(__file__).parent / "fixtures" / "claude-stream-fond.jsonl").read_text().splitlines():
        b._events.put(json.loads(line))
    sizes = []
    for ev in b.events(idle_s=0.01):
        if ev.kind == "idle":
            break
        if ev.kind == "context":
            sizes.append(ev.data["tokens"])
    assert sizes and sizes[0] == 10 + 3916 + 26073


# --- note structurée et amorce figée (15/10, idée 8) ---

_GIT_REPOS = relief.git_repos  # la vraie, avant le remplacement par la fixture


def _git(repo, *args, at=None):
    """git dans un dépôt jetable ; `at` : date du commit (epoch s, auteur et committer)."""
    import os
    import subprocess
    env = dict(os.environ)
    if at is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{int(at)} +0000"
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True, env=env)


def test_sauvegarde_demande_les_champs_fixes(monkeypatch):
    relief.NOTE_FILE.write_text("x" * 9_500, encoding="utf-8")
    j, _ = make_astrel(monkeypatch)
    j.relief.tick()
    sent = j.brain.sent[0]
    for field in ("objectif", "état", "essayé sans succès et pourquoi", "décisions de l'utilisateur",
                  "étape suivante exacte", "ABANDONNÉ (raison, condition de retour)", "GABARIT-CHANTIER"):
        assert field in sent
    assert "Elle fait 9,5 Ko : garde-la sous 8 Ko" in sent


def test_amorce_resume_fige_sans_git_ni_note():
    text = relief.amorce_text()
    assert text.startswith("[Nouvelle session : relève automatique") and text.endswith("] ")
    assert ("résumé figé d'une session précédente : toute commande ou tâche citée est périmée tant que "
            "l'utilisateur ne la redemande pas") in text
    assert "commit" not in text and "Ko" not in text  # rien à dire : omis
    assert "{" not in text


def test_amorce_compte_les_commits_et_signale_la_taille(tmp_path, monkeypatch):
    import os
    work = tmp_path / "work"
    repo = work / "Projet" / "app"
    repo.mkdir(parents=True)
    (work / "archives" / "vieux").mkdir(parents=True)
    _git(work / "archives" / "vieux", "init", "-q")   # dossier d'archives : ignoré
    _git(repo, "init", "-q")
    monkeypatch.setattr(config, "WORKDIR", work)
    assert _GIT_REPOS() == [repo]
    # Note mise à jour il y a une heure : seuls les commits faits depuis comptent.
    stamp = time.time() - 3600
    relief.NOTE_FILE.write_text("y" * 8_200, encoding="utf-8")
    os.utime(relief.NOTE_FILE, (stamp, stamp))
    for n, at in ((1, stamp - 600), (2, stamp + 60), (3, stamp + 120)):
        (repo / "a.txt").write_text(str(n))
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-qm", f"c{n}", at=at)
    monkeypatch.setattr(relief, "git_repos", lambda base=None, depth=3: [repo])
    text = relief.amorce_text()
    assert "2 commits depuis la dernière mise à jour de la note (app 2)." in text
    assert "La note fait 8,2 Ko, au-delà de 8 : resserre-la" in text


def test_commits_tolerant_sans_git(tmp_path, monkeypatch):
    plain = tmp_path / "pas-un-depot"
    plain.mkdir()
    assert relief.commits_since(0, [plain]) is None
    monkeypatch.setattr(relief.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("git")))
    assert relief.commits_since(0, [plain]) is None


def test_gabarit_et_procedure_dans_le_coffre():
    from pathlib import Path
    vault = Path(relief.__file__).resolve().parent.parent / "vault"
    if not (vault / "CHANTIERS-EN-COURS.md").exists():
        pytest.skip("coffre de mémoire personnel, absent du dépôt public")
    gabarit = (vault / "GABARIT-CHANTIER.md").read_text(encoding="utf-8")
    for field in ("Objectif", "État", "Essayé sans succès", "Décisions de l'utilisateur", "Étape suivante exacte",
                  "ABANDONNÉ", "8 Ko", "RÉSUMÉ FIGÉ"):
        assert field in gabarit
    assert "GABARIT-CHANTIER" in (vault / "CHANTIERS-EN-COURS.md").read_text(encoding="utf-8")
    proc = (vault / "procedures" / "procedure-mails-et-contenus-non-fiables.md").read_text(encoding="utf-8")
    lines = proc.strip().splitlines()
    assert lines[0].startswith("Quand l'utiliser :") and len(lines) <= 10


# --- arrêt d'urgence ---

def test_stop_tout(monkeypatch):
    j, _ = make_astrel(monkeypatch)
    j._tasks = {"t1": {"id": "t1"}, "t2": {"id": "t2"}}
    j._begin_turn("voix")
    published = []
    unsub = bus.subscribe(published.append)
    monkeypatch.setattr(core.threading, "Thread",
                        type("T", (threading.Thread,), {"start": lambda self: self.run()}))
    try:
        r = j.on_command({"cmd": "estop"})
    finally:
        unsub()
    assert r["ok"] and r["estop"] and config.ESTOP_FILE.exists()
    assert j.brain.interrupts == 1 and sorted(j.brain.stopped) == ["t1", "t2"]
    assert any(e["type"] == "estop" and e["on"] for e in published)
    # Plus de relève ni de message discret.
    j._end_turn()
    j._tasks = {}
    assert j.relief.tick() == ""
    j._nudge("[Alerte automatique d'Astrel] x")
    assert j.brain.sent == []
    # Un tour relancé seul par le CLI est interrompu.
    j._on_stream(BrainEvent("message_start"))
    assert j.brain.interrupts == 2
    # Levé par l'utilisateur.
    assert j.on_command({"cmd": "estop off"})["estop"] is False and not config.ESTOP_FILE.exists()


def test_reflexes_suspendus_par_l_arret_d_urgence(monkeypatch):
    from astrel.senses import reflex
    rules = [{"id": "d", "name": "Disque", "enabled": True,
              "when": {"type": "disk_below", "path": "/", "gb": 10**9}, "do": {"type": "announce", "text": "x"}}]
    monkeypatch.setattr(reflex, "load_rules", lambda: rules)
    sent = []
    engine = reflex.Reflex(command=sent.append, presence=lambda: (False, False, 0.0))
    config.ESTOP_FILE.write_text("1")
    assert engine.tick() == [] and sent == []
    config.ESTOP_FILE.unlink()
    assert engine.tick() == ["d"]


# --- relève immédiate : arrêt et reprise des agents (19/10, plan P9) ---

class _SyncThread(threading.Thread):
    def start(self):
        self.run()


def _agent(j, tid, tool_use_id, prompt, desc, kind="codeur"):
    """Un agent de fond lancé par Astrel : l'appel Agent (consigne), puis la tâche du CLI."""
    j._on_brain_event(BrainEvent("tool_start", tool="Agent", tool_id=tool_use_id, text=desc,
                                 data={"input": {"prompt": prompt, "subagent_type": kind, "description": desc,
                                                 "run_in_background": True}}))
    j._on_task({"id": tid, "phase": "start", "kind": "agent", "desc": desc, "background": True,
                "tool_use_id": tool_use_id})


def _tool(j, agent, tool_id, tool, path="", done=False):
    if done:
        j._on_brain_event(BrainEvent("tool_result", tool=tool, tool_id=tool_id, data={"agent": agent}))
    else:
        j._on_brain_event(BrainEvent("tool_start", tool=tool, tool_id=tool_id, text=path.rsplit("/", 1)[-1],
                                     data={"input": {"file_path": path, "command": "npm test"}, "agent": agent}))


def _killed(j, *tids, status="killed"):
    for tid in tids:
        j._on_task({"id": tid, "phase": "end", "status": status})


@pytest.fixture
def astrel_p9(monkeypatch):
    monkeypatch.setattr(relief.threading, "Thread", _SyncThread)
    j, said = make_astrel(monkeypatch)
    _agent(j, "t1", "tu1", "Écris le lot A du plan P9.", "Codeur P9")
    _agent(j, "t2", "tu2", "Cherche les appels de task_info.", "Chercheur", kind="chercheur")
    _tool(j, "tu1", "w1", "Edit", "/data/x/relief.py")
    _tool(j, "tu1", "w1", "Edit", done=True)
    return j, said


def test_releve_immediate_avec_deux_agents(astrel_p9):
    j, said = astrel_p9
    assert j.relief.tick() == "stop"
    assert j.relief.tick() == ""                       # arrêt demandé, on attend leur fin
    assert sorted(j.brain.stopped) == ["t1", "t2"] and j.brain.sent == []
    _killed(j, "t1", "t2")
    assert j.relief.tick() == "save"
    assert "2 agent(s) ou tâche(s) de fond viennent d'être arrêtés" in j.brain.sent[0]
    listed = relief.load_list()
    assert [a["tid"] for a in listed] == ["t1", "t2"]
    assert listed[0]["consigne"] == "Écris le lot A du plan P9." and listed[0]["type"] == "codeur"
    assert listed[0]["fichiers"] == ["/data/x/relief.py"]
    end_turn(j)
    assert j.relief.tick() == "renew" and j.brain.restarts == [True]
    # La session neuve reçoit TOUT DE SUITE l'amorce et la liste, en deux blocs.
    sent = j.brain.sent[-1]
    assert sent.startswith("[Nouvelle session : relève automatique")
    assert "résumé figé" in sent and "[Agents interrompus par la relève" in sent
    assert sent.index("résumé figé") < sent.index("[Agents interrompus")
    assert "« Codeur P9 » (codeur" in sent and "<<<CONSIGNE D'ORIGINE\nÉcris le lot A du plan P9.\n" in sent
    assert "fichiers écrits : /data/x/relief.py" in sent and "« Chercheur » (chercheur" in sent
    assert j._turn_source == "relève" and said == []
    assert not config.RELIEF_REPRISE_FILE.exists() and not config.RELIEF_AMORCE_FILE.exists()


def test_ecriture_en_cours_attendue_puis_plafond(astrel_p9, monkeypatch):
    j, _ = astrel_p9
    _tool(j, "tu1", "w2", "Write", "/data/x/nouveau.py")    # écriture commencée, pas rendue
    assert j.relief.tick() == "stop"
    j.relief.tick()
    assert j.brain.stopped == ["t2"]                        # t1 écrit : on attend
    _tool(j, "tu1", "w2", "Write", done=True)
    j.relief.tick()
    assert j.brain.stopped == ["t2", "t1"]
    # Plafond : une écriture qui ne finit jamais n'empêche pas l'arrêt.
    j2, _ = make_astrel(monkeypatch)
    _agent(j2, "t9", "tu9", "x", "Bloqué")
    _tool(j2, "tu9", "w9", "Edit", "/data/x/a.py")
    assert j2.relief.tick() == "stop"
    j2.relief.tick()
    assert j2.brain.stopped == []
    monkeypatch.setattr(config, "RELIEF_STOP_WAIT_S", 0.0)
    j2.relief.tick()
    assert j2.brain.stopped == ["t9"]
    assert j2.relief.stopping["t9"]["outil_en_cours"] == "Edit a.py"   # coupé en pleine écriture : noté


def test_message_garde_puis_joint_a_la_reprise(astrel_p9):
    j, said = astrel_p9
    published = []
    unsub = bus.subscribe(published.append)
    try:
        j.relief.tick()
        j.relief.tick()
        j.handle("Et le build Expo ?", source="clavier")    # l'utilisateur écrit pendant la relève
        assert j.brain.sent == [] and not j._turn_active
        _killed(j, "t1", "t2")
        j.relief.tick()
        assert len(j.brain.sent) == 1                        # la sauvegarde seule
        end_turn(j)
        assert j.relief.tick() == "renew"
    finally:
        unsub()
    shown = [e for e in published if e["type"] == "transcript" and e.get("text") == "Et le build Expo ?"]
    assert len(shown) == 1                                   # affiché une seule fois, tout de suite
    assert any(e["type"] == "notice" and e["text"] == relief.HELD_NOTICE for e in published)
    sent = j.brain.sent[-1]
    assert len(j.brain.sent) == 2                            # amorce + liste + son message : UN envoi
    assert sent.startswith("[Nouvelle session") and "[Agents interrompus" in sent
    assert sent.endswith(relief.HELD_NOTE + "Et le build Expo ?")
    assert j._turn_source == "clavier"                       # sa réponse sera dite normalement


def test_abandon_rend_les_messages_a_l_ancienne_session(astrel_p9):
    j, _ = astrel_p9
    j._on_turn_error = lambda ev: None
    j.relief.tick()
    j.relief.tick()
    j.handle("Tu peux regarder mes mails ?", source="voix")
    _killed(j, "t1", "t2")
    j.relief.tick()
    j._on_stream(BrainEvent("error", text="limite atteinte"))   # la sauvegarde échoue
    assert j.relief.tick() == "abort" and j.brain.restarts == []
    sent = j.brain.sent[-1]
    assert sent.startswith(relief.ABANDON_PREFIX + "[Agents interrompus")  # agents relancés ici
    assert sent.endswith(relief.HELD_NOTE + "Tu peux regarder mes mails ?")
    assert not config.RELIEF_REPRISE_FILE.exists()


def test_estop_pendant_la_releve_jette_la_liste(astrel_p9, monkeypatch):
    j, _ = astrel_p9
    monkeypatch.setattr(core.threading, "Thread", _SyncThread)
    j.relief.tick()
    j.relief.tick()
    j.handle("Stop, attends", source="clavier")
    _killed(j, "t1", "t2")
    j.relief.tick()
    assert config.RELIEF_REPRISE_FILE.exists() and j.relief.phase == "save"
    j.on_command({"cmd": "estop"})
    assert j.relief.phase == "" and not config.RELIEF_REPRISE_FILE.exists()
    assert j.brain.restarts == []
    assert j.brain.sent[-1].endswith("Stop, attends")        # son message part, rien n'est relancé
    assert "[Agents interrompus" not in "".join(j.brain.sent)


def test_agent_fini_entre_temps_pas_relance(astrel_p9):
    j, _ = astrel_p9
    j.relief.tick()
    j.relief.tick()
    _killed(j, "t1", status="completed")                    # a fini de lui-même
    _killed(j, "t2")
    j.relief.tick()
    assert [a["tid"] for a in relief.load_list()] == ["t2"]


def test_relais_equipe_garde_pendant_la_releve(astrel_p9):
    """Plan P13 : le codeur fini de lui-même déclenche le relais testeur/relecteur ; pendant la
    relève, ce message « equipe » est GARDÉ (comme un réflexe), il ne part pas dans la session."""
    j, _ = astrel_p9
    j.relief.tick()
    j.relief.tick()
    j._agent_log["tu1"]["files"].append("/tmp/x.py")       # le codeur a écrit
    _killed(j, "t1", status="completed")
    assert [h["source"] for h in j.relief.held] == ["equipe"]
    assert not any("[Équipe" in s for s in j.brain.sent)


def test_tour_du_cli_absorbe_pendant_la_releve(astrel_p9):
    j, said = astrel_p9
    j.relief.tick()
    j.relief.tick()
    _killed(j, "t1", "t2")
    j._on_stream(BrainEvent("message_start"))               # le CLI relance seul un tour
    assert j._turn_source == "relève"
    assert j.relief.tick() == ""                            # on attend sa fin avant la sauvegarde
    end_turn(j, "L'agent a été arrêté.")
    assert said == []
    assert j.relief.tick() == "save"


def test_deux_releves_de_suite_fusion_et_plafond(monkeypatch):
    monkeypatch.setattr(relief.threading, "Thread", _SyncThread)
    j, _ = make_astrel(monkeypatch)
    # Liste d'une relève précédente jamais lue : fusionnée, sans doublon.
    relief.save_list([{"tid": "old", "desc": "Ancien", "consigne": "Écris le lot A du plan P9."}])
    _agent(j, "t1", "tu1", "Écris le lot A du plan P9.", "Codeur P9")
    _agent(j, "t3", "tu3", "Range les captures.", "Rangement")
    j.relief.stopping = {"t1": j._task_journal("t1"), "t3": j._task_journal("t3")}
    j.relief._write_list()
    assert [a["tid"] for a in relief.load_list()] == ["old", "t3"]
    # Plafond : une consigne déjà relancée RELIEF_MAX_RELAUNCH fois n'est plus remise dans la liste.
    relief.clear_list()
    j.relief.relaunched = {"Range les captures.": 2}
    j.relief._write_list()
    assert [a["tid"] for a in relief.load_list()] == ["t1"]
    j.relief._count_relaunch(relief.load_list())
    assert j.relief.relaunched["Écris le lot A du plan P9."] == 1


def test_agent_lance_pendant_la_sauvegarde_note_au_renouvellement(monkeypatch):
    monkeypatch.setattr(relief.threading, "Thread", _SyncThread)
    j, _ = make_astrel(monkeypatch)
    assert j.relief.tick() == "save"
    _agent(j, "t5", "tu5", "Vérifie le build.", "Build")
    end_turn(j)
    assert j.relief.tick() == "renew"
    assert "« Build » (codeur" in j.brain.sent[-1]


def test_task_info_garde_l_appel_d_outil():
    from astrel.brain import task_info
    info = task_info("task_started", {"task_id": "a1", "tool_use_id": "toolu_1", "description": "x"})
    assert info["tool_use_id"] == "toolu_1"


def test_deux_agents_a_preambule_commun_tous_deux_ecrits(monkeypatch):
    """Relecture P9 : un préambule commun de 250 caractères ne fusionne pas deux agents."""
    j, _ = make_astrel(monkeypatch)
    preambule = "Tu es le codeur d'Astrel. " * 10            # 270 caractères
    assert len(preambule) >= 250
    _agent(j, "t1", "tu1", preambule + "Corrige relief.py.", "Codeur A")
    _agent(j, "t2", "tu2", preambule + "Corrige core.py.", "Codeur B")
    j.relief.stopping = {"t1": j._task_journal("t1"), "t2": j._task_journal("t2")}
    j.relief._write_list()
    assert [a["tid"] for a in relief.load_list()] == ["t1", "t2"]
    j.relief._count_relaunch(relief.load_list())
    assert sorted(j.relief.relaunched.values()) == [1, 1]   # un compteur chacun


def test_estop_ne_rejoue_pas_un_reflexe_garde(astrel_p9, monkeypatch):
    j, _ = astrel_p9
    monkeypatch.setattr(core.threading, "Thread", _SyncThread)
    j.relief.tick()
    j.relief.tick()
    j.handle("Rappel : vérifier le disque", source="réflexe")
    j.handle("Attends", source="clavier")
    assert j.brain.sent == [] and len(j.relief.held) == 2
    j.on_command({"cmd": "estop"})
    assert j.brain.sent == ["Attends"]
    assert j.relief.held == []


def test_p39_passation_en_cours_attendue_par_la_releve(astrel_p9, monkeypatch):
    """Plan P39 : la fiche de passation (et le courrier) en cours est attendue comme une écriture."""
    j, _ = astrel_p9
    _tool(j, "tu1", "p1", "mcp__astrel__passation")
    assert j._agent_writing("tu1") and not j._agent_busy("tu1")
    assert j.relief.tick() == "stop"
    j.relief.tick()
    assert j.brain.stopped == ["t2"]  # t1 dépose sa fiche : on attend
    _tool(j, "tu1", "p1", "mcp__astrel__passation", done=True)
    j.relief.tick()
    assert j.brain.stopped == ["t2", "t1"]
    # Un Bash en cours n'est PAS attendu par la relève (seul l'arrêt pour quota lui donne une grâce).
    j2, _ = make_astrel(monkeypatch)
    _agent(j2, "t9", "tu9", "x", "Occupé")
    _tool(j2, "tu9", "b9", "Bash", "")
    assert j2._agent_busy("tu9") and not j2._agent_writing("tu9")
    assert j2.relief.tick() == "stop"
    j2.relief.tick()
    assert j2.brain.stopped == ["t9"]


# --- 27/10 : reprise identique pour TOUS les métiers (demande de l'utilisateur, le planificateur compris) ---

@pytest.fixture
def equipe_isolee(tmp_path, monkeypatch):
    from astrel import equipe
    monkeypatch.setattr(equipe, "EQUIPE_DIR", tmp_path / "equipe")
    monkeypatch.setattr(relief.threading, "Thread", _SyncThread)
    return equipe


def _outil(j, agent, tool_id, tool, inp, rendu=True, erreur=False):
    """Un outil d'un sous-agent : début (avec son entrée), puis son résultat si `rendu`."""
    j._on_brain_event(BrainEvent("tool_start", tool=tool, tool_id=tool_id, text=tool, data={"input": inp, "agent": agent}))
    if rendu:
        j._on_brain_event(BrainEvent("tool_result", tool=tool, tool_id=tool_id, data={"agent": agent, "is_error": erreur}))


def _releve_complete(j, *tids):
    """Arrêt, sauvegarde, session neuve : rend le message envoyé à la session neuve."""
    assert j.relief.tick() == "stop"
    j.relief.tick()
    assert sorted(j.brain.stopped) == sorted(tids)
    _killed(j, *tids)
    assert j.relief.tick() == "save"
    end_turn(j)
    assert j.relief.tick() == "renew"
    return j.brain.sent[-1]


def _prompts(sent):
    """Les consignes de reprise à recopier (entre <<<REPRISE et REPRISE>>>)."""
    out, rest = [], sent
    while "<<<REPRISE\n" in rest:
        rest = rest.split("<<<REPRISE\n", 1)[1]
        body, rest = rest.split("\nREPRISE>>>", 1)
        out.append(body)
    return out


def _depose_plan(j, agent, tool_id, titre, rendu=True):
    from astrel.plans import plans
    inp = {"title": titre, "body": "## Étapes\n1. A0\n2. A1"}
    j._on_brain_event(BrainEvent("tool_start", tool="mcp__astrel__plan_submit", tool_id=tool_id, text="plan",
                                 data={"input": inp, "agent": agent}))
    r = plans.submit(titre, inp["body"], "demande de l'utilisateur")      # le serveur MCP passe par le cœur
    if rendu:
        j._on_brain_event(BrainEvent("tool_result", tool="mcp__astrel__plan_submit", tool_id=tool_id,
                                     data={"agent": agent, "is_error": False}))
    return r["id"]


@pytest.mark.parametrize("kind", ["planificateur", "relecteur", "testeur", "codeur", "chercheur", "avocat-du-diable"])
def test_tout_metier_relance_par_le_coeur_avec_sa_consigne(monkeypatch, equipe_isolee, kind):
    """Chaque métier : même traitement (consigne d'origine intacte, déjà fait, vérifier d'abord), son
    propre dépôt relevé par le cœur, et la relance vue sans rappel."""
    j, said = make_astrel(monkeypatch)
    consigne = f"Mission P50 pour le {kind}. " + "Contexte long. " * 700   # > 8 000 caractères : jamais coupée
    _agent(j, "t1", "tu1", consigne, f"Travail du {kind}", kind=kind)
    attendu = {
        "planificateur": "plan P1 « Images multi-comptes » déjà déposé",
        "testeur": "passation déjà déposée : fiche 1 (mission p50, de testeur à relecteur)",
        "relecteur": "courrier déjà envoyé à codeur (mission P50)",
        "codeur": "fichiers écrits : /data/x/a.py",
        "chercheur": "liste d'étapes de l'agent : faites : lire relief.py ; en cours : lire core.py",
        "avocat-du-diable": "rien de noté par le cœur",
    }[kind]
    if kind == "planificateur":
        _depose_plan(j, "tu1", "p1", "Images multi-comptes")
    elif kind == "testeur":
        _outil(j, "tu1", "p1", "mcp__astrel__passation", {"de": "testeur", "a": "relecteur", "mission": "P50"}, rendu=False)
        equipe_isolee.deposer("testeur", "relecteur", "tests passés", mission="P50")   # le serveur MCP écrit
        j._on_brain_event(BrainEvent("tool_result", tool="mcp__astrel__passation", tool_id="p1",
                                     data={"agent": "tu1", "is_error": False}))
    elif kind == "relecteur":
        _outil(j, "tu1", "c1", "mcp__astrel__courrier_ecrire", {"mission": "P50", "de": "relecteur", "a": "codeur"})
    elif kind == "codeur":
        _outil(j, "tu1", "w1", "Edit", {"file_path": "/data/x/a.py"})
    elif kind == "chercheur":
        _outil(j, "tu1", "td", "TodoWrite", {"todos": [{"content": "lire relief.py", "status": "completed"},
                                                       {"content": "lire core.py", "status": "in_progress"}]})
    sent = _releve_complete(j, "t1")
    assert said == [] and j._turn_source == "relève"
    sous_type = "celui par défaut" if kind == "agent" else kind
    assert f"« Travail du {kind} » ({kind}" in sent and f"subagent_type {sous_type}" in sent
    assert f"description « Reprise : Travail du {kind} »" in sent and "run_in_background vrai" in sent
    [prompt] = _prompts(sent)
    assert prompt.startswith(relief.REPRISE_TETE) and relief.REPRISE_VERIFIER in prompt
    assert attendu in prompt
    assert relief.decouper(prompt)[0] == consigne                  # consigne d'origine, entière et inchangée
    assert set(j.relief.attendus) == {relief._key({"consigne": consigne})}
    # Le cerveau recopie : la relance est vue, le journal garde la consigne d'ORIGINE.
    j._on_brain_event(BrainEvent("tool_start", tool="Agent", tool_id="tu2", text="Reprise",
                                 data={"input": {"prompt": prompt, "subagent_type": kind, "run_in_background": True}}))
    assert j.relief.attendus == {} and j._agent_log["tu2"]["origine"] == consigne
    end_turn(j, "Agents relancés.")
    assert j.relief.tick() == "" and len(j.brain.sent) == 2          # aucun rappel


def test_planificateur_dont_le_plan_est_deja_depose(monkeypatch, equipe_isolee):
    """Le cas du 27/10 : deux plans demandés, le premier déposé avant la relève. La reprise dit lequel
    est déjà là (numéro relu sur le disque), sans le redéposer ; le second reste à faire."""
    j, _ = make_astrel(monkeypatch)
    _agent(j, "t1", "tu1", "Mission : deux plans, (A) images et (B) vidéo.", "Plans images et vidéo", kind="planificateur")
    pid = _depose_plan(j, "tu1", "p1", "Images : lots 5-6 révisés")
    _outil(j, "tu1", "r1", "Read", {"file_path": "/data/x/P14.md"}, rendu=False)    # coupé en pleine lecture
    sent = _releve_complete(j, "t1")
    [prompt] = _prompts(sent)
    assert f"plan {pid} « Images : lots 5-6 révisés » déjà déposé (version 1, statut pending) : ne le redépose pas" in prompt
    assert "coupé pendant : Read" in prompt and "Si tout est déjà fait, rends seulement ton rapport final" in prompt
    assert relief.decouper(prompt)[0] == "Mission : deux plans, (A) images et (B) vidéo."


def test_planificateur_coupe_en_plein_depot_attendu_puis_note(monkeypatch, equipe_isolee):
    """Le dépôt du plan est l'écriture du planificateur : la relève l'attend (comme Write) ; un dépôt
    coupé quand même (plafond) est noté « à vérifier avec plan_list »."""
    j, _ = make_astrel(monkeypatch)
    _agent(j, "t1", "tu1", "Fais le plan P60.", "Plan P60", kind="planificateur")
    j._on_brain_event(BrainEvent("tool_start", tool="mcp__astrel__plan_submit", tool_id="p1", text="plan",
                                 data={"input": {"title": "Plan vidéo"}, "agent": "tu1"}))
    assert j._agent_writing("tu1")
    assert j.relief.tick() == "stop"
    j.relief.tick()
    assert j.brain.stopped == []                                    # dépôt en cours : attendu
    monkeypatch.setattr(config, "RELIEF_STOP_WAIT_S", 0.0)
    j.relief.tick()
    assert j.brain.stopped == ["t1"]
    assert any("dépôt du plan « Plan vidéo » coupé en cours : vérifie avec plan_list" in d
               for d in j.relief.stopping["t1"]["deja"])


def test_planificateur_qui_a_rendu_pas_de_doublon(monkeypatch, equipe_isolee):
    """Un agent qui a fini de lui-même pendant l'arrêt (« completed ») n'est ni relancé ni attendu."""
    j, _ = make_astrel(monkeypatch)
    _agent(j, "t1", "tu1", "Fais le plan P61.", "Plan P61", kind="planificateur")
    _agent(j, "t2", "tu2", "Relis le lot 1.", "Relecture", kind="relecteur")
    _depose_plan(j, "tu1", "p1", "Plan P61")
    assert j.relief.tick() == "stop"
    j.relief.tick()
    _killed(j, "t1", status="completed")
    _killed(j, "t2")
    assert j.relief.tick() == "save"
    end_turn(j)
    assert j.relief.tick() == "renew"
    sent = j.brain.sent[-1]
    assert "Plan P61" not in sent and "« Relecture » (relecteur" in sent
    assert list(j.relief.attendus) == ["Relis le lot 1."]


def test_agent_non_relance_un_seul_rappel_puis_alerte(monkeypatch, equipe_isolee):
    j, _ = make_astrel(monkeypatch)
    _agent(j, "t1", "tu1", "Teste le lot 2.", "Tests lot 2", kind="testeur")
    _agent(j, "t2", "tu2", "Fais le plan P62.", "Plan P62", kind="planificateur")
    sent = _releve_complete(j, "t1", "t2")
    prompts = _prompts(sent)
    # Le cerveau ne relance que le testeur.
    j._on_brain_event(BrainEvent("tool_start", tool="Agent", tool_id="tu3", text="x",
                                 data={"input": {"prompt": prompts[0], "subagent_type": "testeur"}}))
    end_turn(j, "Agents relancés.")
    assert j.relief.tick() == "rappel"
    rappel = j.brain.sent[-1]
    assert rappel.startswith(relief.REPRISE_RAPPEL) and "« Plan P62 » (planificateur" in rappel
    assert "Tests lot 2" not in rappel
    end_turn(j, "Rien.")                                            # toujours pas relancé : pas de 2e rappel
    published = []
    unsub = bus.subscribe(published.append)
    try:
        assert j.relief.tick() == "rappel"
    finally:
        unsub()
    assert j.brain.sent[-1] == rappel
    assert any(e["type"] == "notice" and "non relancé" in e["text"] for e in published)
    assert j.relief.attendus == {} and j.relief.tick() == ""


def test_rappel_jamais_sous_estop(monkeypatch, equipe_isolee):
    j, _ = make_astrel(monkeypatch)
    _agent(j, "t1", "tu1", "Relis le lot 3.", "Relecture 3", kind="relecteur")
    _releve_complete(j, "t1")
    n = len(j.brain.sent)
    end_turn(j, "Rien relancé.")
    config.ESTOP_FILE.write_text("1", encoding="utf-8")
    j.relief.tick()
    assert len(j.brain.sent) == n and j.relief.attendus == {}


def test_estop_entre_releve_et_amorce_rien_relance(monkeypatch):
    """Liste en attente de l'amorce (démarrage, ou message suivant) : sous ESTOP, elle est jetée."""
    relief.save_list([{"tid": "t1", "type": "planificateur", "desc": "Plan", "consigne": "Fais le plan."}])
    relief.set_amorce(True)
    config.ESTOP_FILE.write_text("1", encoding="utf-8")
    text = relief.take_amorce()
    assert text.startswith("[Nouvelle session") and "[Agents interrompus" not in text
    assert not config.RELIEF_REPRISE_FILE.exists()


def test_plafond_de_deux_relances_avec_la_consigne_de_reprise(monkeypatch, equipe_isolee):
    """Un agent déjà relancé porte la consigne de REPRISE : sa clé reste la consigne d'origine, le
    plafond (2 par consigne) le reconnaît, et le « déjà fait » d'avant le suit."""
    j, _ = make_astrel(monkeypatch)
    _agent(j, "t1", "tu1", "Fais le plan P63.", "Plan P63", kind="planificateur")
    _depose_plan(j, "tu1", "p1", "Plan P63 A")
    for n, (old, new, tuid) in enumerate((("t1", "t2", "tu2"), ("t2", "t3", "tu3")), 1):
        sent = _releve_complete(j, old)
        [prompt] = _prompts(sent)
        assert relief.decouper(prompt)[0] == "Fais le plan P63."
        assert "plan P1 « Plan P63 A » déjà déposé" in prompt
        if n == 2:
            assert "- avant la relance précédente : " in prompt
        j._on_brain_event(BrainEvent("tool_start", tool="Agent", tool_id=tuid, text="Reprise",
                                     data={"input": {"prompt": prompt, "subagent_type": "planificateur",
                                                     "run_in_background": True}}))
        j._on_task({"id": new, "phase": "start", "kind": "agent", "desc": "Reprise : Plan P63",
                    "background": True, "tool_use_id": tuid})
        end_turn(j, "Agents relancés.")
        j.relief.retry_after = 0
        j.brain.context_tokens, j.brain.stopped = 160_000, []
    assert j.relief.relaunched == {"Fais le plan P63.": 2}
    # Troisième relève : plus de relance, l'utilisateur prévenu.
    assert j.relief.tick() == "stop"
    j.relief.tick()
    _killed(j, "t3")
    assert j.relief.tick() == "save"
    assert relief.load_list() == []


def test_commandes_de_fond_d_un_agent_jointes_a_lui(monkeypatch, equipe_isolee):
    """27/10 : trois « local_bash » sans consigne dans la liste, un banc jamais relancé. La commande
    de fond lancée par un sous-agent est jointe à son agent ; seule, elle garde sa commande."""
    j, _ = make_astrel(monkeypatch)
    _agent(j, "t1", "tu1", "Lot 6 d'Astrel.", "Astrel lot 6", kind="codeur")
    _outil(j, "tu1", "bash1", "Bash", {"command": "python banc.py --seuil 0.3", "run_in_background": True})
    j._on_task({"id": "b1", "phase": "start", "kind": "local_bash", "desc": "Banc 0,3", "background": True,
                "tool_use_id": "bash1"})
    j._on_brain_event(BrainEvent("tool_start", tool="Bash", tool_id="bash2", text="x",
                                 data={"input": {"command": "sleep 600; ls", "run_in_background": True}}))
    j._on_task({"id": "b2", "phase": "start", "kind": "local_bash", "desc": "Attente", "background": True,
                "tool_use_id": "bash2"})
    sent = _releve_complete(j, "t1", "b1", "b2")
    [prompt] = _prompts(sent)
    assert "relance celles qui servent encore) : python banc.py --seuil 0.3" in prompt
    assert "« Banc 0,3 »" not in sent                               # pas d'entrée à part
    assert "« Attente » (commande" in sent and "commande : sleep 600; ls" in sent
    assert list(j.relief.attendus) == ["Lot 6 d'Astrel."]          # une commande n'est pas « attendue »


def test_journal_d_un_agent_vivant_jamais_evince(monkeypatch):
    j, _ = make_astrel(monkeypatch)
    _agent(j, "t1", "tu1", "Long travail.", "Long", kind="codeur")
    for n in range(150):
        j._on_brain_event(BrainEvent("tool_start", tool="Bash", tool_id=f"bg{n}", text="x",
                                     data={"input": {"command": f"echo {n}", "run_in_background": True}}))
    assert j._agent_log["tu1"]["consigne"] == "Long travail."
    assert len(j._agent_log) <= core._AGENT_LOG_MAX
