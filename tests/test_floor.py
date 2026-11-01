"""La parole est à l'utilisateur (13/10) : il se faisait couper quand une tâche de
fond finissait pendant qu'il parlait (réglage « direct ») — le compte rendu
était lu aussitôt, le micro devenait sourd et la suite de sa phrase était
perdue. Désormais tout ce qu'il n'a pas demandé attend qu'il ait fini : écoute
ouverte, transcription en cours, ou moins de SPEECH_GRACE_S depuis sa dernière
parole. Puis c'est dit d'un bloc, après ce qu'il a dit entre-temps."""
import threading
import time

import pytest

from astrel import config, core, state
from astrel.floor import Backlog, Held, backlog, floor, intro
from astrel.reports import reports
from test_listener import FakeMic, n, recorder
from test_reports import ch, fond_turn, astrel, of, published  # noqa: F401 (fixtures)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(floor, "_clock", c)
    return c


@pytest.fixture
def direct(monkeypatch):
    monkeypatch.setattr(reports, "_mode", "direct")


def flush(j):
    """Ce que fait la boucle de la file quand plus rien ne la retient."""
    assert j._backlog_blocked() == ""
    return j._flush_backlog()


# ------------------------------------------------------------------ Floor

def test_ecoute_transcription_et_delai_de_grace(clock):
    assert floor.busy() == ""
    with floor.listening():
        assert floor.busy() == "écoute en cours"
    with floor.transcribing():
        assert floor.busy() == "transcription en cours"
    assert floor.busy() == ""
    floor.heard()
    clock.t += 2.9
    assert floor.busy().startswith("L'utilisateur a parlé il y a 2.9")
    clock.t += 0.2
    assert floor.busy() == ""


def test_delai_de_grace_reglable(clock, monkeypatch):
    monkeypatch.setattr(config, "SPEECH_GRACE_S", 6.0)
    floor.heard()
    clock.t += 5.0
    assert floor.busy()
    clock.t += 1.1
    assert floor.busy() == ""


def test_valeur_par_defaut_du_delai(monkeypatch):
    """3 s par défaut, ASTREL_SPEECH_GRACE_S pour le changer."""
    import inspect

    assert 'SPEECH_GRACE_S = _env_float("ASTREL_SPEECH_GRACE_S", 3.0)' in inspect.getsource(config)
    monkeypatch.setenv("ASTREL_SPEECH_GRACE_S", "4.5")
    assert config._env_float("ASTREL_SPEECH_GRACE_S", 3.0) == 4.5


def test_file_ordonnee_et_vidage():
    b = Backlog()
    assert not b and not b.wait(0)
    b.add(Held("fond", ["Un."], mids=["a"]))
    b.add(Held("annonce", ["Deux."]))
    b.add(Held("fond", []))  # tour vide : rien à dire
    assert len(b) == 3 and b.wait(0)
    b.discard_mid("a")
    assert [it.sentences for it in b.take()] == [["Deux."]]
    assert not b and not b.wait(0)
    assert intro(2) == "Deux messages sont arrivés pendant que tu parlais."


# ------------------------------------------------------------------ l'écoute signale la parole

def test_enregistreur_signale_la_parole():
    probs = [0.0] * 3 + [0.9] * n(1) + [0.30] * 4 + [0.0] * n(1.2)
    mic = FakeMic(probs)
    beats = []
    recorder(mic).record(mic, on_voice=lambda: beats.append(mic.i))
    # Parole au seuil, puis au-dessus du seuil de fin (0,24) : la syllabe faible compte.
    assert beats == list(range(3, 3 + n(1) + 4))


def test_listen_once_tient_la_parole(monkeypatch):
    j = object.__new__(core.Astrel)
    j._ear = threading.Lock()
    j._off = False  # 15/10 : `listen_once` regarde le micro coupé (rien d'écouté ni transcrit)
    j.mic = object()
    j.status = type("S", (), {"set": lambda self, *a, **k: True})()
    seen = {}

    class Rec:
        def record(self, mic, initial_timeout=None, abort=None, on_voice=None):
            seen["record"] = floor.busy()
            on_voice()
            return b"pcm"

    class Stt:
        def transcribe(self, pcm):
            seen["stt"] = floor.busy()
            return "bonjour"

    j.recorder, j.stt = Rec(), Stt()
    assert j.listen_once() == "bonjour"
    assert seen == {"record": "écoute en cours", "stt": "transcription en cours"}
    assert floor.busy().startswith("L'utilisateur a parlé")  # délai de grâce après sa phrase


# ------------------------------------------------------------------ tours de fond

def test_compte_rendu_retenu_pendant_que_utilisateur_parle(astrel, published, direct, clock):
    """Le cas du 13/10 : l'utilisateur parle, une tâche finit. Rien n'est dit."""
    j, said, chimes = astrel
    with floor.listening():
        floor.heard()
        fond_turn(j)
        assert said == [] and chimes == []
        assert j._backlog_blocked() == "écoute en cours"
    reply = of(published, "reply")[-1]
    assert reply["report"] and reply["pending_read"]  # 🔊 en attendant
    assert not j._followup
    clock.t += 1.0
    assert j._backlog_blocked().startswith("L'utilisateur a parlé")  # délai de grâce
    clock.t += 2.5
    assert flush(j)
    assert chimes == ["confirm"]
    assert said == ["La recherche est finie.", "Trois pistes sérieuses, je te les détaille quand tu veux."]
    assert of(published, "report_read")[-1]["mid"] == reply["mid"]  # passé à « Relire »
    assert j._followup  # comme un compte rendu dit aussitôt : il peut enchaîner
    assert not j._flush_backlog()  # rien de dit deux fois


def test_retenu_pendant_la_transcription(astrel, direct):
    j, said, _ = astrel
    with floor.transcribing():
        fond_turn(j)
    assert said == [] and backlog


def test_retenu_dans_le_delai_de_grace(astrel, direct, clock):
    """Phrase finie et transcrite, mais il vient de parler : il reprend peut-être."""
    j, said, _ = astrel
    floor.heard()
    clock.t += 2.0
    fond_turn(j)
    assert said == []
    clock.t += 1.5
    assert flush(j) and said


def test_retenu_pendant_la_conversation(astrel, direct):
    """Fenêtre d'écoute ouverte après une réponse (il peut enchaîner)."""
    j, said, _ = astrel
    j._in_conversation = True
    fond_turn(j)
    assert said == [] and j._backlog_blocked() == "conversation en cours"
    j._in_conversation = False
    assert flush(j) and said


def test_dit_aussitot_quand_personne_ne_parle(astrel, published, direct):
    j, said, chimes = astrel
    fond_turn(j)
    assert said and chimes == ["confirm"] and not backlog
    assert not of(published, "reply")[-1]["pending_read"]


def test_ce_que_utilisateur_dit_entre_temps_passe_d_abord(astrel, published, direct, clock):
    j, said, _ = astrel
    with floor.listening():
        floor.heard()
        fond_turn(j, "Le build Android est terminé. ")
    # Sa phrase est transcrite et envoyée : on lui répond, le compte rendu attend.
    j.handle("Et la météo demain ?", "voix", "general")
    j._on_stream(core.BrainEvent("text", text="Demain, soleil et vingt degrés. "))
    assert said == ["Demain, soleil et vingt degrés."]
    clock.t += 10
    assert j._backlog_blocked() == "tour en cours"
    j._on_stream(core.BrainEvent("turn_end"))
    assert flush(j)
    assert said == ["Demain, soleil et vingt degrés.", "Le build Android est terminé."]


def test_utilisateur_coupe_un_tour_de_fond_en_cours(astrel, published, direct, clock):
    """Il parle pendant que le tour de fond s'écrit : la suite est retenue avec
    lui, la phrase inachevée n'est pas collée à la réponse, il reste « à lire »."""
    j, said, _ = astrel
    j._on_stream(core.BrainEvent("message_start"))
    j._on_stream(core.BrainEvent("text", text="Première partie dite. "))
    assert said == ["Première partie dite."]
    floor.heard()  # « Astrel… »
    j._on_stream(core.BrainEvent("text", text="Deuxième partie retenue. Et une phrase inachev"))
    assert said == ["Première partie dite."]
    j.handle("Attends, lance plutôt les tests", "voix", "general")
    j._on_stream(core.BrainEvent("text", text="\nJe lance les tests. "))
    j._on_stream(core.BrainEvent("turn_end"))
    first = of(published, "reply")[-2]
    assert first["report"] and first["partial"] and first["pending_read"]
    assert said == ["Première partie dite.", "Je lance les tests."]
    clock.t += 5
    assert flush(j)
    assert said[-1] == "Deuxième partie retenue."
    assert not any("inachev" in s for s in said)


def test_plusieurs_taches_finies_regroupees(astrel, published, direct, clock):
    j, said, chimes = astrel
    floor.heard()
    fond_turn(j, "L'audit de Boutique est fini. ")
    fond_turn(j, "Le build iOS est en ligne. ")
    j.on_command({"cmd": "announce", "text": "Rappel : appeler Paul."})
    time.sleep(0.05)  # l'annonce part dans un fil
    assert said == [] and len(backlog) == 3
    clock.t += 4
    assert flush(j)
    assert chimes == ["confirm"]  # un seul carillon pour le tout
    assert said == ["Trois messages sont arrivés pendant que tu parlais.", "L'audit de Boutique est fini.",
                    "Le build iOS est en ligne.", "Rappel : appeler Paul."]
    assert len(of(published, "report_read")) == 2


def test_reflexe_retenu_aussi(astrel, clock):
    j, said, _ = astrel
    floor.heard()
    j.handle("Dis bonjour", "réflexe", "general")
    j._on_stream(core.BrainEvent("text", text="Il est l'heure de la pause. "))
    j._on_stream(core.BrainEvent("turn_end"))
    assert said == []
    clock.t += 4
    assert flush(j) and said == ["Il est l'heure de la pause."]


def test_a_la_demande_inchange(astrel, published):
    """Le défaut (« demande ») ne dit rien, même une fois le silence revenu."""
    j, said, chimes = astrel
    floor.heard()
    fond_turn(j)
    assert not backlog and said == [] and chimes == []
    assert of(published, "reply")[-1]["pending_read"]


def test_passe_a_la_demande_entre_temps(astrel, direct, clock, monkeypatch):
    j, said, _ = astrel
    floor.heard()
    fond_turn(j)
    j.on_command({"cmd": "announce", "text": "Rappel : réunion."})
    time.sleep(0.05)
    monkeypatch.setattr(reports, "_mode", "demande")
    clock.t += 4
    assert flush(j)
    assert said == ["Rappel : réunion."]  # le compte rendu reste à lire, le rappel est dit


def test_chut_vide_la_file_mais_garde_a_lire(astrel, published, direct, clock):
    j, said, _ = astrel
    j.speaker.interrupt = lambda: None
    floor.heard()
    fond_turn(j)
    j.on_command({"cmd": "hush"})
    clock.t += 4
    assert not j._flush_backlog() and said == []
    mid = of(published, "reply")[-1]["mid"]
    assert reports.text(mid) and of(published, "reply")[-1]["pending_read"]


def test_lu_au_clic_pas_redit(astrel, published, direct, clock):
    j, said, _ = astrel
    floor.heard()
    fond_turn(j)
    mid = of(published, "reply")[-1]["mid"]
    j.on_command({"cmd": "read_report", "mid": mid})
    n_said = len(said)
    clock.t += 4
    assert not j._flush_backlog() and len(said) == n_said


def test_voix_en_pause_attend(astrel, direct, clock):
    j, said, _ = astrel
    floor.heard()
    fond_turn(j)
    clock.t += 4
    j.speaker.paused = True
    assert j._backlog_blocked() == "voix en pause"
    j.speaker.paused = False
    assert flush(j)


def test_confirmation_en_cours_retient(astrel, direct):
    j, said, _ = astrel
    j._pending_confirm = {"id": "x"}
    fond_turn(j)
    assert said == [] and j._backlog_blocked() == "confirmation en cours"


# ------------------------------------------------------------------ post_report, annonces, salut

def test_post_report_direct_pendant_que_utilisateur_parle(astrel, published, direct, clock):
    j, said, _ = astrel
    floor.heard()
    r = j.on_command({"cmd": "report", "text": "L'agent a fini l'audit. Rien de bloquant."})
    assert r["ok"] and r["dit"] is False and r["en_attente"]
    assert of(published, "report")[-1]["pending_read"] and said == []
    clock.t += 4
    assert flush(j)
    assert said == ["L'agent a fini l'audit.", "Rien de bloquant."]
    assert of(published, "report_read")[-1]["mid"] == r["mid"]


def test_post_report_direct_pendant_un_tour_dit_apres(astrel, direct):
    """Appelé par Claude en pleine réponse à l'utilisateur : dit après la réponse."""
    j, said, _ = astrel
    j.handle("Où en est Studio ?", "clavier", "general")
    j._on_stream(core.BrainEvent("text", text="Studio avance bien. "))
    r = j.on_command({"cmd": "report", "text": "Au passage, l'audit est fini."})
    assert r["dit"] is False
    j._on_stream(core.BrainEvent("turn_end"))
    assert flush(j) and said == ["Studio avance bien.", "Au passage, l'audit est fini."]


def test_rappel_attend_son_tour(astrel, clock):
    j, said, chimes = astrel
    with floor.listening():
        j._announce("Rappel : sortir le linge.")
    assert said == [] and chimes == []
    clock.t += 4
    assert flush(j) and said == ["Rappel : sortir le linge."] and chimes == ["confirm"]
    j._announce("Rappel immédiat.")
    assert said[-1] == "Rappel immédiat."  # personne ne parle : dit aussitôt


def test_pas_de_salut_par_dessus_utilisateur(astrel):
    j, said, _ = astrel
    floor.heard()
    j.greet_return(3600)
    assert said == []


# ------------------------------------------------------------------ la boucle de la file

def test_boucle_dit_la_file_au_retour_du_silence(astrel, direct, monkeypatch):
    j, said, _ = astrel
    monkeypatch.setattr(config, "SPEECH_GRACE_S", 0.3)
    j._running = True
    floor.heard()
    fond_turn(j, "Le déploiement est fini. ")
    assert said == []
    t = threading.Thread(target=j._backlog_loop, daemon=True)
    t.start()
    try:
        deadline = time.time() + 3
        while not said and time.time() < deadline:
            time.sleep(0.02)
        assert said == ["Le déploiement est fini."]
    finally:
        j._running = False
        t.join(2)


def test_veille_note_la_parole(monkeypatch):
    """Boucle de veille : la parole entendue hors conversation (« Astrel »
    qui commence) tient la parole, même sans mot de réveil reconnu."""
    j = object.__new__(core.Astrel)
    j._running, j._off, j._force_wake, j._followup = True, False, False, False
    j._pending_confirm = None
    j._confirm_evt, j._in_conversation = threading.Event(), False
    j.relief = type("Rl", (), {"tick": lambda self: None})()
    j._ear = threading.Lock()
    j._last_turn, j._turn_active, j._tasks = 0.0, False, {}
    j.status = type("S", (), {"set": lambda self, *a, **k: True, "current": state.SLEEPING})()
    j.speaker = type("Sp", (), {"paused": False, "busy": False})()
    reads = []

    class Mic:
        muted = False
        profile = type("P", (), {"vad": 0.35, "wake": 0.4})()

        def read_frame(self):
            reads.append(1)
            if len(reads) >= 2:
                j._running = False
            return b"\0" * config.FRAME_BYTES

    j.mic = Mic()
    j.wake = type("W", (), {"feed": lambda self, frame, thr=None, **kw: False})()  # P36 : speech=, vad=
    j.recorder = type("R", (), {"speech_prob": lambda self, frame: 0.8})()
    j.boot = lambda: None
    j.say = lambda *a, **k: None
    monkeypatch.setattr(core.graph, "vault_changed", lambda: False)
    j.run()
    assert floor.busy().startswith("L'utilisateur a parlé")
