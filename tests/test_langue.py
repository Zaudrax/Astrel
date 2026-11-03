"""Conversation en anglais (02/11, demande de l'utilisateur) : réglage `conversation_language`, reconnaissance
vocale, phrases dites par le code, commandes vocales anglaises, consignes de Claude en anglais (mêmes
règles, même budget), session neuve au message suivant sans casser une tâche de fond, voix anglaise."""
import chemins_machine  # chemins de la machine qui lance la suite
import json
import re
import threading

import pytest

from astrel import brain, config, core, eveil, floor, guard, langue, listener
from astrel.events import bus
from astrel.settings import reglages
from astrel.voice.text import SentenceSplitter, clean_for_speech
from test_budget import PROMPT_MAX_CHARS
from test_whatsapp import wa, wa_env  # noqa: F401  (fixtures)


@pytest.fixture
def anglais():
    reglages.set("conversation_language", "en")
    yield
    reglages.set("conversation_language", "fr")


# --------------------------------------------------------------------------- réglage, écoute

def test_reglage_par_defaut_francais_puis_anglais(monkeypatch):
    assert langue.conversation() == "fr" and listener.stt_language() == "fr"
    reglages.set("conversation_language", "en")
    assert langue.anglais() and listener.stt_language() == "en"
    reglages.set("stt_language", "es")
    assert listener.stt_language() == "en"  # la conversation anglaise impose l'anglais
    reglages.set("conversation_language", "fr")
    assert listener.stt_language() == "es"  # sinon « Langue parlée », comme avant
    monkeypatch.setenv("ASTREL_CONVERSATION_LANGUAGE", "en")
    assert langue.conversation() == "en"


def test_lexique_de_whisper_dans_la_langue(anglais, monkeypatch):
    monkeypatch.setattr(config, "WORKDIR", config.ROOT / "absent")
    assert listener.stt_prompt().startswith("Vocabulary: Astrel")
    reglages.set("conversation_language", "fr")
    assert listener.stt_prompt().startswith("Vocabulaire : Astrel")


def test_hallucinations_de_whisper_jetees(anglais):
    class Seg:
        def __init__(self, text):
            self.text = text

    class Model:
        def __init__(self, text):
            self.text, self.lang = text, None

        def transcribe(self, audio, language=None, **_kw):
            self.lang = language
            return [Seg(self.text)], None

    t = listener.Transcriber()
    t._model = Model(" Thanks for watching! ")
    assert t.transcribe(b"\x01\x00" * 1600) == ""
    assert t._model.lang == "en"
    t._model = Model("Thanks for watching the build, it passed.")
    assert t.transcribe(b"\x01\x00" * 1600) == "Thanks for watching the build, it passed."  # vraie phrase gardée
    assert langue.hallucination("Sous-titres réalisés par la communauté d'Amara.org", "fr")
    assert not langue.hallucination("Merci", "fr")


def test_mot_d_eveil_dans_une_transcription_anglaise():
    noms = eveil.noms("Astrel")
    for text in ("Astrel, what's the weather like today?", "Hey Astrel", "OK Astrel, set a timer",
                 "Astral, are you there?"):
        assert eveil.trouve(text, noms), text
    assert not eveil.trouve("The stars are bright tonight", noms)
    assert not eveil.trouve("Down under in the austral summer", noms)


# --------------------------------------------------------------------------- phrases dites par le code

def test_phrases_du_code_dites_en_anglais(anglais):
    for fr in (config.SAY_READY, config.SAY_NOT_UNDERSTOOD, config.SAY_ERROR, *config.GOODBYES,
               "C'est oui ou non ?", "D'accord.", "J'annule.", "Je laisse la question à l'écran."):
        en = langue.parle(fr)
        assert en != fr and not re.search(r"[àâçéèêëîïôûù]", en), (fr, en)
    assert langue.parle("Attention, git push vers origin/main. Je continue ?").startswith("Heads up:")
    assert langue.parle("Texte de Claude, inconnu du catalogue.") == "Texte de Claude, inconnu du catalogue."
    reglages.set("conversation_language", "fr")
    assert langue.parle(config.SAY_READY) == config.SAY_READY


def test_toutes_les_phrases_dites_ont_leur_anglais():
    from tools import i18n_inventaire as inv
    from astrel import i18n

    spoken = [t for f, _l, t in inv.inventaire() if t in {*config.GOODBYES, config.SAY_READY, "C'est oui ou non ?",
                                                             "Voici ma nouvelle voix."}]
    assert len(spoken) >= 7 and all(i18n.covered(t) for t in spoken)


class Speaker:
    paused = False

    def __init__(self):
        self.said = []

    def say(self, text, raw=False, **_kw):
        self.said.append((text, raw))


def _coeur_qui_parle():
    j = object.__new__(core.Astrel)
    j.speaker, j.voice_out, j._usage = Speaker(), True, "conversation"
    j.whatsapp = type("W", (), {"away": False})()
    j._away = lambda: False
    return j


def test_say_traduit_les_phrases_du_code_seulement(anglais):
    j = _coeur_qui_parle()
    j.say(config.SAY_NOT_UNDERSTOOD, raw=True)
    j.say("Je n'ai pas compris.")  # texte de Claude (non brut) : jamais touché
    assert j.speaker.said == [("I didn't catch that.", True), ("Je n'ai pas compris.", False)]


def test_annonce_groupee_et_voix_du_texte(anglais):
    assert floor.intro(3) == "Three messages came in while you were talking."
    assert "see the code on screen" in clean_for_speech("Here:\n```js\nx()\n```")
    assert "the link" in clean_for_speech("See https://example.com/a/b")
    reglages.set("conversation_language", "fr")
    assert floor.intro(3).startswith("Trois messages")
    assert "voir le code" in clean_for_speech("Regarde :\n```js\nx()\n```")


def test_decoupage_des_phrases_anglaises():
    s = SentenceSplitter()
    out = s.feed("Mr. Smith called about the build. It failed on e.g. the tests, so I fixed them. ")
    assert out == ["Mr. Smith called about the build.", "It failed on e.g. the tests, so I fixed them."]


# --------------------------------------------------------------------------- commandes vocales anglaises

def _stop(text):
    return core.Astrel.is_stop(object.__new__(core.Astrel), text)


def test_formules_d_adieu_anglaises_mot_entier(anglais):
    for t in ("Goodbye.", "OK, thanks, that's all.", "That's all for now", "Stop, Astrel.", "Thank you.",
              "Good night!", "You can stop listening now"):
        assert _stop(t), t
    for t in ("Can you look at the goodbyes file?", "See you later we need to deploy the app tomorrow morning and "
              "check the logs", "Stop the music", "au revoir"):
        assert not _stop(t), t


def test_adieux_francais_inchanges():
    assert _stop("Au revoir") and _stop("C'est bon, tu peux t'arrêter")
    assert not _stop("goodbye") and not _stop("on verra ça plus tard, lance le build et les tests du projet")


def test_modes_dits_en_anglais(anglais):
    assert core.mode_phrase("Switch to silent mode") == "silencieux"
    assert core.mode_phrase("Astrel, dictation mode please") == "dictee"
    assert core.mode_phrase("End dictation") == "conversation"
    assert core.mode_phrase("You can talk again") == "conversation"
    assert core.mode_phrase("Switch to silent mode and then send the report to Paul") == ""
    assert core.mode_phrase("mode silencieux") == ""  # en anglais, seules les tournures anglaises
    reglages.set("conversation_language", "fr")
    assert core.mode_phrase("Passe en mode silencieux") == "silencieux"
    assert core.mode_phrase("silent mode") == ""


def test_absence_dite_en_anglais():
    assert langue.away_phrase_en("I'm heading out") == "absent"
    assert langue.away_phrase_en("OK Astrel, I'm back") == "present"
    assert langue.away_phrase_en("I'm heading out, send the email to Paul when the build is done") == ""


def test_oui_pour_30_min_et_oui_non_anglais(anglais):
    assert core._wants_30("Yes, for half an hour")
    assert core._wants_30("yes to everything")
    assert not core._wants_30("yes")
    for t, want in (("yes", True), ("yeah, go ahead", True), ("okay do it", True), ("no", False),
                    ("nope", False), ("don't", False), ("not now", False), ("I'm not sure", None),
                    ("cancel that", False), ("oui", True), ("non", False)):
        assert guard.decision_from_speech(t, english=True) is want, t
    assert guard.decision_from_speech("maybe", english=True) is None  # indécidable : refus en fin de compte
    assert guard.decision_from_speech("maybe later", english=True) is False  # « later » : un refus


def test_dictee_anglaise(anglais):
    assert core.dictation_clean("hello comma world full stop new line next question mark") == \
        "hello, world.\nnext?"
    assert core.dictation_clean("virgule", lang="fr") == ","


# --------------------------------------------------------------------------- consignes de Claude

# Repères qui doivent se retrouver dans les deux versions : outils, marqueurs écrits par le code, notes.
_REPERES = re.compile(r"\[SILENT\]|\b[a-z]+_[a-z_]+\b|CHANTIERS-EN-COURS|GABARIT-CHANTIER|--send restart|\bopus\b|\bhaiku\b"
                      r"|\bsonnet\b|Plan Pn validé|Révision du plan Pn|\[Alerte automatique d'Astrel…\]"
                      r"|\[Relève automatique…\]|\[Équipe…\]|playwright\d?|\bdecision\b|\bblocage\b|\bresultat\b")


def test_consignes_anglaises_equivalentes_et_dans_le_budget():
    fr, en = brain.VOICE_SYSTEM_PROMPT, brain.VOICE_SYSTEM_PROMPT_EN
    assert len(en) <= PROMPT_MAX_CHARS
    manquants = set(_REPERES.findall(fr)) - set(_REPERES.findall(en))
    assert not manquants, manquants
    assert "ONLY in English" in en and "UNIQUEMENT en" in fr
    assert brain.voice_system_prompt(langue="en") == en  # même texte d'un démarrage à l'autre (cache)
    # mêmes lignes de règles (« - … ») : rien d'oublié dans la traduction
    assert abs(fr.count("\n- ") - en.count("\n- ")) == 0


class FakePopen:
    cmds = []

    def __init__(self, cmd, **kw):
        FakePopen.cmds.append(cmd)
        self.stdout, self.stderr, self.stdin = iter(()), iter(()), None
        self.pid = 4242

    def poll(self):
        return 0


def test_cerveau_en_anglais_et_session_par_langue(tmp_path, monkeypatch, anglais):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "s.json")
    monkeypatch.setattr(config, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(config, "MCP_CONFIG_FILE", tmp_path / "absent.json")
    monkeypatch.setattr(brain.subprocess, "Popen", FakePopen)
    FakePopen.cmds = []
    b = brain.Brain()
    b.start(fresh=True)
    cmd = FakePopen.cmds[-1]
    assert cmd[cmd.index("--append-system-prompt") + 1] == brain.VOICE_SYSTEM_PROMPT_EN and b.langue == "en"
    b._proc, b.session_id = FakePopen(["x"]), "11111111-1111-4111-8111-111111111111"
    b._remember_session()
    assert json.loads(config.SESSION_FILE.read_text())["langue"] == "en"
    assert brain.Brain._recent_session("claude") == b.session_id
    reglages.set("conversation_language", "fr")
    assert brain.Brain._recent_session("claude") == ""  # née en anglais : pas reprise en français


def test_ancienne_session_sans_langue_vaut_francais(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SESSION_FILE", tmp_path / "session.json")
    import time

    sid = "22222222-2222-4222-8222-222222222222"
    config.SESSION_FILE.write_text(json.dumps({"id": sid, "at": time.time(), "fournisseur": "claude"}))
    assert brain.Brain._recent_session("claude") == sid
    reglages.set("conversation_language", "en")
    try:
        assert brain.Brain._recent_session("claude") == ""
    finally:
        reglages.set("conversation_language", "fr")


# --------------------------------------------------------------------------- cœur : session, voix

def _coeur_session(langue_session="fr"):
    j = object.__new__(core.Astrel)
    j.brain = type("B", (), {"langue": langue_session})()
    j._tasks, j._tasks_lock, j._turn_active = {}, threading.Lock(), False
    j.resets = []
    j.reset_session = lambda notice="": j.resets.append(notice)
    return j


def test_session_neuve_au_message_suivant_sans_tache(anglais):
    j = _coeur_session("fr")
    assert j._langue_suivre("voix") == ""
    assert j.resets == ["Conversation en anglais : nouvelle session avec les consignes en anglais."]
    assert j._langue_suivre("réflexe") == ""  # un tour automatique n'y touche pas


def test_tache_de_fond_gardee_note_en_attendant(anglais):
    j = _coeur_session("fr")
    j._tasks = {"t1": {}}
    note = j._langue_suivre("clavier")
    assert note.startswith("[the user switched the conversation to English") and j.resets == []
    j._tasks, j._turn_active = {}, True
    assert j._langue_suivre("clavier") and j.resets == []  # un tour en cours : pas de remise à zéro non plus
    assert _coeur_session("en")._langue_suivre("voix") == ""
    assert _coeur_session(None)._langue_suivre("voix") == ""  # autre fournisseur, mode léger : non suivi


def test_handle_envoie_la_note_devant_le_message(anglais, monkeypatch):
    from test_reports import make_astrel

    j, _said = make_astrel(monkeypatch)
    monkeypatch.setattr(core.sensors, "is_on", lambda name: False)
    sent = []
    j.brain.send = lambda text, images=None: sent.append(text)
    j.brain.langue = "fr"
    j._tasks = {"t1": {}}
    j._send_lock = threading.Lock()
    j.handle("What's left on the Boutique build?", source="clavier")
    assert sent and sent[0].startswith("[the user switched the conversation to English")
    assert sent[0].rstrip().endswith("What's left on the Boutique build?")


class Voix:
    def __init__(self):
        self.voice, self.fallback_spec = "edge:fr-FR-RemyMultilingualNeural", ""

    def set_voice(self, spec):
        self.voice = spec
        return spec

    def set_fallback(self, spec):
        self.fallback_spec = spec
        return spec


def test_changement_de_langue_change_voix_et_secours(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VOICE_EN_FILE", tmp_path / "voice-en.txt")
    seen = []
    monkeypatch.setattr(bus, "publish", lambda kind, **d: seen.append((kind, d)) or d)
    j = object.__new__(core.Astrel)
    j.speaker = Voix()
    reglages.set("conversation_language", "en")
    try:
        j._langue_changee()
        for t in threading.enumerate():
            if t.name == "langue-voix":
                t.join(5)
        assert j.speaker.voice == config.TTS_ENGINE_EN_DEFAULT == "edge:en-US-AndrewNeural"
        assert j.speaker.fallback_spec == "piper:en_US-joe-medium"
        assert any(k == "notice" and d["text"].startswith("Conversation en anglais") for k, d in seen)
    finally:
        reglages.set("conversation_language", "fr")


def test_voix_retenue_par_langue(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VOICE_FILE", tmp_path / "voice.txt")
    monkeypatch.setattr(config, "VOICE_EN_FILE", tmp_path / "voice-en.txt")
    monkeypatch.delenv("ASTREL_TTS_ENGINE", raising=False)
    monkeypatch.delenv("ASTREL_TTS_ENGINE_EN", raising=False)
    assert langue.voix("en") == "edge:en-US-AndrewNeural" and langue.voix("fr") == "edge:fr-FR-RemyMultilingualNeural"
    langue.retenir_voix("edge:en-GB-RyanNeural", "en")
    assert langue.voix("en") == "edge:en-GB-RyanNeural" and not config.VOICE_FILE.exists()
    monkeypatch.setenv("ASTREL_TTS_ENGINE_EN", "edge:en-US-EmmaNeural")
    assert langue.voix("en") == "edge:en-US-EmmaNeural"
    assert langue.voix_secours("en") == "piper:en_US-joe-medium" and langue.voix_secours("fr") == config.TTS_FALLBACK


def test_voix_anglaises_proposees_en_anglais(anglais, monkeypatch):
    j = object.__new__(core.Astrel)
    j.speaker = Voix()
    j.speaker.voice = "edge:en-US-AndrewNeural"
    values = [v for v, _l in j._voice_options()]
    assert "edge:en-US-AndrewNeural" in values and "edge:en-GB-RyanNeural" in values
    assert "edge:fr-FR-HenriNeural" not in values


def test_voix_anglaise_de_secours_installee():
    if not (config.MODELS / "piper").is_dir():  # dépôt neuf : modèles pas encore téléchargés
        pytest.skip("modèles absents : tools/fetch_models.py")
    assert (config.MODELS / "piper" / "en_US-joe-medium.onnx").exists()


# --------------------------------------------------------------------------- relecture du 02/11 : oui / non

REFUS_EN = ("I wouldn't do it", "never do it", "you shouldn't do it", "don’t do it", "Don’t.", "no, don't",
            "I'd rather you didn't do it", "can't you just not do it", "wait, okay hold on", "do it later",
            "don't do it yet, okay", "I won't approve that", "hang on", "cancel that", "isn’t that risky?",
            "nope", "yes, but don’t", "won’t", "Okay — wait")
OUI_EN = ("yes", "Yes.", "Yeah.", "yep", "Okay.", "confirmed", "go ahead", "yes for half an hour")
INDECIS_EN = ("Do it.", "Go for it.", "approved", "sure", "Thank you.", "nah", "not sure", "I don’t know")
REFUS_FR = ("ne valide pas", "je ne confirme pas", "pas d'accord", "non", "surtout pas", "jamais",
            "ne fais rien sans me demander", "attends", "c'est ok pas maintenant", "oui mais plus tard",
            "n’y touche pas", "ne l’envoie pas")
INDECIS_FR = ("je ne sais pas", "je sais pas", "demande-moi à chaque fois", "toujours ?", "euh")
OUI_FR = ("oui", "Oui.", "ouais", "vas-y", "d'accord", "ok d'accord", "confirme", "oui pour 30 minutes", "oui à tout")


def test_negations_jamais_lues_comme_un_oui():
    """Refus des deux langues partout ; oui anglais seulement en conversation anglaise ; ’ comme '."""
    for english in (False, True):
        for t in REFUS_EN + REFUS_FR:
            assert guard.decision_from_speech(t, english=english) is False, (t, english)
        for t in OUI_FR:
            assert guard.decision_from_speech(t, english=english) is True, (t, english)
        for t in INDECIS_EN + INDECIS_FR:
            assert guard.decision_from_speech(t, english=english) is None, (t, english)
    for t in OUI_EN:
        assert guard.decision_from_speech(t, english=True) is True, t
        assert guard.decision_from_speech(t) is None, t  # en français : pas un oui (« yes » compris)


def test_listes_de_reponses_dans_le_garde_fou_seulement():
    assert not hasattr(config, "YES_WORDS") and not hasattr(config, "NO_WORDS")
    assert "yes" in guard.YES_WORDS_EN and "yes" not in guard.YES_WORDS_FR


def _questions():
    descs = [v for k, v in vars(guard).items() if k.startswith("L_") and isinstance(v, str)]
    for cmd in (f"rm -rf {chemins_machine.HOME}/Documents/x", "git push origin main", "sudo apt install x",
                "curl -X POST https://example.com -d @x"):
        descs.append(guard.classify("Bash", {"command": cmd}, cwd="/tmp").desc)
    from astrel import i18n

    for d in descs:
        fr = f"Attention, {d}. Je continue ?"
        yield fr
        yield i18n.tr(fr, "en")
    yield "Je continue ?"
    yield i18n.tr("Je continue ?", "en")


def test_aucune_question_de_confirmation_ne_vaut_un_oui():
    """Un écho de la question (micro qui entend la voix d'Astrel) ne doit jamais valider."""
    seen = list(_questions())
    assert len(seen) >= 70 and "Should I continue?" in seen[-1]
    for q in seen:
        for english in (False, True):
            assert guard.decision_from_speech(q, english=english) is not True, q


def test_oui_pour_30_min_seul_apres_le_oui():
    reglages.set("conversation_language", "fr")
    assert core._wants_30("oui à chaque fois") and core._wants_30("Oui, toujours") and core._wants_30("oui à tout")
    assert not core._wants_30("oui mais demande-moi à chaque fois")
    assert not core._wants_30("oui")
    reglages.set("conversation_language", "en")
    try:
        assert core._wants_30("yes, every time") and core._wants_30("yes, always")
        assert not core._wants_30("Yes, but ask me every time")
        assert not core._wants_30("yes, don't ask again")
    finally:
        reglages.set("conversation_language", "fr")


@pytest.mark.parametrize("lang,cases", [
    ("en", (("I wouldn't do it", False), ("don’t do it", False), ("yes", True), ("oui", True))),
    ("fr", (("je ne confirme pas", False), ("n’y touche pas", False), ("oui", True), ("yes", None))),
])
def test_reponse_par_la_voix(lang, cases):
    """Chemin 1 : la question dite, réponse entendue (`_confirm_by_voice`), en français et en anglais."""
    reglages.set("conversation_language", lang)
    try:
        _par_la_voix(cases)
    finally:
        reglages.set("conversation_language", "fr")


def _par_la_voix(cases):
    for heard, want in cases:
        j = object.__new__(core.Astrel)
        j._usage, j.voice_out, j._off, j.recorder, j._mute_gen = "conversation", False, False, object(), 0
        j.speaker = type("S", (), {"paused": False, "wait_idle": lambda self, t=None: True})()
        j._chime = lambda kind: None
        j.say = lambda *a, **k: None
        j.listen_once = lambda timeout=None, abort=None, h=heard: h
        j._muted_since = lambda gen: False
        box, answered = {"allow": None, "by": "", "always": False, "tag": "t"}, threading.Event()
        j._confirm_by_voice("Heads up: a git push. Should I continue?", box, answered)
        assert box["allow"] is want and box["always"] is False, heard


@pytest.mark.parametrize("lang,cases", [
    ("en", (("I won't approve that", False), ("never do it", False), ("yeah", True), ("do it later", False),
            ("Don’t.", False))),
    ("fr", (("pas d'accord", False), ("ne l’envoie pas", False), ("d'accord", True), ("yeah", None))),
])
def test_reponse_dite_pendant_la_question_a_l_ecran(lang, cases):
    """Chemin 2 : « oui »/« non » dit pendant qu'une question attend à l'écran (`_answer_by_speech`)."""
    reglages.set("conversation_language", lang)
    try:
        for heard, want in cases:
            j = object.__new__(core.Astrel)
            ev = threading.Event()
            j._pending_confirm = {"id": "c1", "event": ev, "box": {"allow": None, "by": "", "always": False}}
            assert j._answer_by_speech(heard) is (want is not None), heard
            assert j._pending_confirm["box"]["allow"] is want, heard
    finally:
        reglages.set("conversation_language", "fr")


def test_reponse_ecrite_sur_whatsapp(wa):
    """Chemin 3 : réponse écrite sur WhatsApp (3 mots au plus, `WhatsApp._answer`)."""
    import time as _time

    from test_whatsapp import msg

    wa.host._turn_source = "whatsapp"
    # WhatsApp : oui français seulement (même en conversation anglaise), refus des deux langues.
    reglages.set("conversation_language", "en")
    for i, (text, want) in enumerate((("don’t do it", False), ("never do it", False), ("Do it.", None),
                                      ("yes", None), ("n’envoie pas", False), ("oui", True))):
        bus.publish("confirm", id=f"w{i}", pending=True, desc="push", cmd="git push", guard=False)
        wa.host.answers.clear()
        wa.on_line(msg(text, ts=_time.time()))
        got = wa.host.answers[0][1] if wa.host.answers else None
        assert got is want, text
        if want is None:
            bus.publish("confirm", id=f"w{i}", pending=False, allow=False, by="écran", desc="push")
    reglages.set("conversation_language", "fr")


def test_faux_adieux_anglais(anglais):
    for t in ("write a goodbye email to Paul", "check if nothing else is running", "can my laptop go to sleep",
              "I'm done, send it", "tell him you can stop", "remind me to call him, see you later"):
        assert not _stop(t), t
    for t in ("Goodbye.", "OK, goodbye Astrel", "No, nothing else, thanks", "I'm done.", "Okay, go to sleep",
              "See you later!", "you can stop"):
        assert _stop(t), t


def test_pas_de_session_neuve_pendant_une_mission(anglais, monkeypatch):
    from astrel import mission as mission_mod

    j = _coeur_session("fr")
    monkeypatch.setattr(type(mission_mod.mission), "status", property(lambda self: mission_mod.ACTIVE))
    assert j._langue_suivre("voix").startswith("[the user switched") and j.resets == []
    monkeypatch.setattr(type(mission_mod.mission), "status", property(lambda self: mission_mod.PAUSED))
    assert j._langue_suivre("voix") and j.resets == []
    monkeypatch.setattr(type(mission_mod.mission), "status", property(lambda self: mission_mod.DONE))
    assert j._langue_suivre("voix") == "" and len(j.resets) == 1


# --------------------------------------------------------------------------- contre-relecture du 02/11

def test_negations_generiques_en_nt():
    for t in ("I couldn't say yes", "yes, but it wasn't what I asked", "it hasn't changed", "yes ain't nothing wrong",
              "yes, it isn’t", "Can’t", "you mustn't"):
        for english in (False, True):
            assert guard.decision_from_speech(t, english=english) is False, t
    assert guard.decision_from_speech("I don't know", english=True) is None  # indécis : reposée
    assert guard.decision_from_speech("yes", english=True) is True and guard.decision_from_speech("oui") is True


def test_une_question_n_est_jamais_une_reponse():
    for t in ("okay, what time is it?", "okay, what time is it", "c'est quoi ce fichier", "oui, pourquoi ?",
              "is it safe", "can you do it", "do you want that", "ok, où ça", "est-ce grave"):
        assert langue.question(t), t
    for t in ("yes", "oui vas-y", "do it", "do that", "I won't approve that", "ok", "oui pour 30 minutes",
              "never do it", "non"):
        assert not langue.question(t), t


@pytest.mark.parametrize("lang", ["fr", "en"])
def test_reponse_a_l_ecran_courte_et_jamais_une_question(lang):
    """`_answer_by_speech` : 4 mots au plus, jamais une question : la phrase suit son cours (Claude)."""
    reglages.set("conversation_language", lang)
    try:
        cases = (("okay, what time is it?", None), ("okay what time is it", None), ("is it safe", None),
                 ("yes go ahead please now", None), ("yeah", True), ("ok", True), ("oui pour 30 minutes", True),
                 ("oui, c'est quoi ?", None), ("I couldn't say yes", False), ("ne l'envoie pas", False))
        for text, want in cases:
            if lang == "fr" and text in ("yeah",):
                want = None  # oui anglais : pas en français
            j = object.__new__(core.Astrel)
            j._pending_confirm = {"id": "c1", "event": threading.Event(),
                                  "box": {"allow": None, "by": "", "always": False}}
            assert j._answer_by_speech(text) is (want is not None), (lang, text)
            assert j._pending_confirm["box"]["allow"] is want, (lang, text)
    finally:
        reglages.set("conversation_language", "fr")


@pytest.mark.parametrize("lang,cases", [
    ("en", (("okay, what time is it?", None), ("yes", True))),
    ("fr", (("ok, c'est quoi ce fichier ?", None), ("oui", True))),
])
def test_question_dite_pendant_la_question_parlee_reposee(lang, cases):
    """`_confirm_by_voice` avait le même défaut : une question entendue n'est plus un oui (question reposée)."""
    reglages.set("conversation_language", lang)
    try:
        _par_la_voix(cases)
    finally:
        reglages.set("conversation_language", "fr")


def test_tournures_neutres_jamais_un_oui_seules():
    """Accord de l'utilisateur (02/11) : « pas de souci », « no problem »… retirées avant les refus ; un vrai oui reste
    exigé à côté, et une négation ailleurs dans la phrase refuse toujours."""
    d = guard.decision_from_speech
    for t in ("pas de souci", "pas de problème", "ça me va", "pourquoi pas", "ça ne me dérange pas", "no problem",
              "no worries", "not a problem"):
        assert d(t) is None and d(t, english=True) is None, t
    for t in ("oui, pas de souci", "oui pas de problème", "ok pas de soucis", "d'accord, ça me va",
              "oui, ça ne me dérange pas", "vas-y, pourquoi pas"):
        assert d(t) is True and d(t, english=True) is True, t
    assert d("yes, no problem", english=True) is True and d("yes, no problem") is None  # « yes » : anglais seulement
    assert d("okay, no worries", english=True) is True
    for t in ("non, pas de souci", "oui pas de souci mais ne le supprime pas", "oui, pas de souci, attends",
              "yes, no problem, but don't push", "no problem, cancel it", "pas de souci pas de souci oui jamais"):
        assert d(t) is False and d(t, english=True) is False, t


def test_invite_anglaise_sans_prenom_dit_the_user(monkeypatch):
    """02/11 : sans prénom au profil, l'invite anglaise dit « the user », jamais « l'utilisateur's »."""
    monkeypatch.setattr(config, "USER_NAME", config.USER_NAME_DEFAULT)
    en = brain.voice_system_prompt(langue="en")
    assert "the user's personal assistant" in en and "l'utilisateur" not in en
    fr = brain.voice_system_prompt(langue="fr")
    assert "l'utilisateur" in fr and "the user" not in fr


def test_invite_anglaise_avec_prenom_inchangee(monkeypatch):
    monkeypatch.setattr(config, "USER_NAME", "Camille")
    en = brain.voice_system_prompt(langue="en")
    assert "Camille's personal assistant" in en and "the user" not in en
    assert "Camille" in brain.voice_system_prompt(langue="fr")
