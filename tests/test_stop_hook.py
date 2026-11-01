"""Hook Stop « annonce sans action » (15/10, `hooks/stop_hook.py`) : relance un tour
de l'utilisateur qui finit sur une annonce ou un fragment, jamais un tour automatique,
au plus deux fois par message."""

import importlib.util
import io
import json
import sys

import pytest

from astrel import config

_spec = importlib.util.spec_from_file_location("stop_hook", config.ROOT / "hooks" / "stop_hook.py")
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)


@pytest.fixture(autouse=True)
def _isole(tmp_path, monkeypatch):
    """Ni le compteur ni le journal de la vraie machine (runtime/, logs/)."""
    monkeypatch.setattr(hook, "STATE_FILE", tmp_path / "stop-guard.json")
    monkeypatch.setattr(hook, "VERIF_FILE", tmp_path / "stop-verif.json")
    monkeypatch.setattr(hook, "LOG_FILE", tmp_path / "stop-guard.jsonl")
    monkeypatch.setattr(hook, "MAX_RELANCES", 2)
    monkeypatch.delenv("ASTREL_STOP_GUARD", raising=False)


# --- Le texte seul -----------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Je vais maintenant vérifier ton agenda.",
    "D'accord. Je vais tout de suite regarder les mails de ce matin.",
    "C'est noté. Je m'en occupe.",
    "Le premier point est réglé. Ensuite, je passe au second.",
    "Bien reçu, je vais lancer les tests.",
    "OK, je vais le corriger.",
    "Je vais l'ouvrir.",
    "Laisse-moi vérifier.",
    "Je commence par le serveur.",
    "Très bien, c’est parti.",
])
def test_annonce(text):
    assert hook.evaluate(text, tools_used=False, background=False) == "annonce"
    assert hook.evaluate(text, tools_used=True, background=False) == "annonce"


@pytest.mark.parametrize("text", [
    "Je vais maintenant vérifier ton agenda, tu veux ?",
    "Tu veux que je regarde ça ensuite ?",
    "Si tu veux, je vais lancer les tests.",
    "Donne-moi une phrase complète sur le sujet et je m'y mets.",
    "Oui, envoie-moi le lien, je vais tenter de récupérer la transcription.",
    "C'est lancé en fond. Je te préviens quand c'est fini.",
    "Le codeur travaille. Je vais attendre qu'il ait fini.",
    "Je vais laisser tourner.",
    "Je vais te laisser tranquille.",
    "Je vais être franc : ça ne marchera pas.",
    "Dis-moi quand c'est fait, et je lance la suite.",
    "Je m'en occupe demain matin.",
    "Je regarderai ensuite côté serveur si le paiement est rattaché.",
    "Nous sommes le jeudi 15 octobre.",
    "",
])
def test_pas_annonce(text):
    assert hook.evaluate(text, tools_used=False, background=False) == ""


def test_annonce_longue_ignoree():
    text = "Voici le bilan détaillé. " * 20 + "Je vais maintenant vérifier ton agenda."
    assert len(text) > hook.ANNONCE_MAX
    assert hook.evaluate(text, tools_used=False, background=False) == ""


def test_tache_de_fond_annonce_faible_ignoree():
    """Un agent lancé en fond fait le travail : « je m'en occupe » est vrai."""
    assert hook.evaluate("C'est lancé, je m'en occupe.", tools_used=True, background=True) == ""
    # Une annonce explicite et immédiate reste relancée.
    assert hook.evaluate("Agent lancé. Je vais maintenant lire ton agenda.",
                         tools_used=True, background=True) == "annonce"


@pytest.mark.parametrize("text", [
    "Voici ce que j'ai trouvé :",
    "J'ai lu les trois fichiers et",
    "Le build est passé, il reste à vérifier la",
    "Résultat des tests sans erreur pour",
])
def test_fragment_apres_outils(text):
    assert hook.evaluate(text, tools_used=True, background=False) == "fragment"
    assert hook.evaluate(text, tools_used=False, background=False) == ""  # sans outil : rien


@pytest.mark.parametrize("text", [
    "C'est fait.",
    "Les 29 tests passent !",
    "Terminé, rien à signaler…",
    "```\nok\n```",
    "Voici le bilan, sans ponctuation à la fin " + "et bien long " * 20 + "fin",
    # Réponses courtes complètes sans ponctuation finale (relances inutiles avant le 16/10).
    "Plan P3 déposé",
    "Voilà, c'est fait",
    "Terminé, 3 fichiers modifiés",
    "Oui",
])
def test_pas_fragment(text):
    assert hook.evaluate(text, tools_used=True, background=False) == ""


@pytest.mark.parametrize("prompt,source", [
    ("[Réflexe programmé, l'utilisateur n'a rien demandé à l'instant.] Regarde les mails.", "réflexe"),
    ("[Alerte automatique d'Astrel, l'utilisateur n'a rien dit] L'agent X tourne en boucle.", "alerte"),
    ("[Relève automatique d'Astrel : ton contexte atteint 150 k tokens…] Mets à jour…", "relève"),
    ("[Mission autonome lancée par l'utilisateur, tour 3 sur 10] …", "mission"),
    ("<task-notification>\n<task-id>a1</task-id>\n<status>completed</status>", "fond"),
    ("[Nouvelle session : relève automatique, l'ancien contexte était trop gros. …] "
     "[Réflexe programmé, l'utilisateur n'a rien demandé à l'instant.] Briefing.", "réflexe"),
    # L'amorce de relève précède un VRAI message de l'utilisateur.
    ("[Nouvelle session : relève automatique, l'ancien contexte était trop gros.] Quelle heure ?", ""),
    ("[l'utilisateur te parle pendant que tu travailles.] Et le build ?", ""),
    ("[Mode silencieux : l'utilisateur lit ta réponse à l'écran, elle n'est pas dite.] Salut", ""),
])
def test_source(prompt, source):
    assert hook.source_of(prompt) == source


# --- Transcript et décision ---------------------------------------------------

def _user(text, uid, **extra):
    return {"type": "user", "uuid": uid, "promptId": "p-" + uid, "isSidechain": False,
            "message": {"role": "user", "content": text}, **extra}


def _assistant(*blocks):
    return {"type": "assistant", "isSidechain": False, "message": {"role": "assistant", "content": list(blocks)}}


def _text(t):
    return {"type": "text", "text": t}


def _tool(name="Bash", **inp):
    return {"type": "tool_use", "id": "toolu_1", "name": name, "input": inp}


def _result():
    return {"type": "user", "isSidechain": False,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}]}}


def _feedback():
    """Ce que le CLI écrit après un blocage (vu en réel, CLI 2.1.285)."""
    return {"type": "user", "isMeta": True, "isSidechain": False,
            "message": {"role": "user", "content": "Stop hook feedback:\n" + hook.RAISON_ANNONCE}}


def _transcript(tmp_path, recs, name="t.jsonl"):
    path = tmp_path / name
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
    return path


def _payload(path, last, active=False, **extra):
    return {"session_id": "s", "transcript_path": str(path), "cwd": "/tmp", "prompt_id": "x",
            "hook_event_name": "Stop", "stop_hook_active": active, "last_assistant_message": last,
            "background_tasks": [], "session_crons": [], **extra}


ANNONCE = "Je vais maintenant vérifier ton agenda."


def test_relance_puis_plafond(tmp_path):
    recs = [_user("Ancienne question", "u0"), _assistant(_text("Réponse.")),
            _user("Regarde mon agenda", "u1"), _assistant(_text(ANNONCE))]
    path = _transcript(tmp_path, recs)
    assert hook.decide(_payload(path, ANNONCE)) == hook.RAISON_ANNONCE
    # Le modèle recommence (continuation de NOTRE blocage) : deuxième et dernière relance.
    recs += [_feedback(), _assistant(_text(ANNONCE))]
    path = _transcript(tmp_path, recs)
    assert hook.decide(_payload(path, ANNONCE, active=True)) == hook.RAISON_ANNONCE
    recs += [_feedback(), _assistant(_text(ANNONCE))]
    path = _transcript(tmp_path, recs)
    assert hook.decide(_payload(path, ANNONCE, active=True)) is None
    log = [json.loads(line) for line in hook.LOG_FILE.read_text(encoding="utf-8").splitlines()]
    assert [e["action"] for e in log] == ["relance", "relance", "plafond"]


def test_compteur_remis_a_zero_par_message(tmp_path):
    recs = [_user("Regarde mon agenda", "u1"), _assistant(_text(ANNONCE))]
    hook.decide(_payload(_transcript(tmp_path, recs), ANNONCE))
    hook.decide(_payload(_transcript(tmp_path, recs), ANNONCE))
    assert json.loads(hook.STATE_FILE.read_text())["count"] == 2
    assert hook.decide(_payload(_transcript(tmp_path, recs), ANNONCE)) is None  # plafond
    recs += [_user("Et mes mails ?", "u2"), _assistant(_text("Je vais maintenant lire tes mails."))]
    path = _transcript(tmp_path, recs)
    assert hook.decide(_payload(path, "Je vais maintenant lire tes mails.")) == hook.RAISON_ANNONCE
    assert json.loads(hook.STATE_FILE.read_text()) | {"at": 0} == {"key": "u2", "count": 1, "at": 0}


def test_message_en_plein_tour_compte_comme_nouveau(tmp_path):
    """Un message arrivé pendant le tour est une pièce jointe `queued_command`."""
    recs = [_user("Regarde mon agenda", "u1"), _assistant(_text(ANNONCE))]
    hook.decide(_payload(_transcript(tmp_path, recs), ANNONCE))
    hook.decide(_payload(_transcript(tmp_path, recs), ANNONCE))
    recs += [{"type": "attachment", "uuid": "q1", "attachment": {
        "type": "queued_command", "prompt": [_text("[l'utilisateur te parle pendant que tu travailles.] Et après ?")]}},
        _assistant(_text(ANNONCE))]
    assert hook.decide(_payload(_transcript(tmp_path, recs), ANNONCE)) == hook.RAISON_ANNONCE


def test_continuation_d_un_autre_hook_respectee(tmp_path):
    """stop_hook_active sans relance de notre part : on ne s'en mêle pas."""
    recs = [_user("Regarde mon agenda", "u1"), _assistant(_text(ANNONCE))]
    assert hook.decide(_payload(_transcript(tmp_path, recs), ANNONCE, active=True)) is None
    assert not hook.STATE_FILE.exists()


@pytest.mark.parametrize("opener", [
    _user("[Réflexe programmé, l'utilisateur n'a rien demandé à l'instant.] Briefing du matin.", "r1"),
    _user("[Alerte automatique d'Astrel, l'utilisateur n'a rien dit] L'agent boucle.", "a1"),
    _user("[Relève automatique d'Astrel : ton contexte atteint 151 k tokens] Mets à jour la note.", "v1"),
    _user("[Mission autonome lancée par l'utilisateur, tour 2 sur 8] Continue.", "m1"),
    _user("<task-notification>\n<task-id>abc</task-id>\n<status>completed</status>", "t1",
          origin={"kind": "task-notification", "producer": "session-task"}),
    {"type": "attachment", "uuid": "q2", "attachment": {
        "type": "queued_command", "prompt": "<task-notification>\n<task-id>abc</task-id>"}},
])
def test_tours_automatiques_ignores(tmp_path, opener):
    recs = [_user("Question", "u0"), _assistant(_text("Réponse.")), opener, _assistant(_text(ANNONCE))]
    assert hook.decide(_payload(_transcript(tmp_path, recs), ANNONCE)) is None
    assert not hook.STATE_FILE.exists()


def test_fragment_apres_outils_dans_le_transcript(tmp_path):
    recs = [_user("Lis le journal", "u1"), _assistant(_text("Je regarde."), _tool(command="tail log")),
            _result(), _assistant(_text("Voici ce que j'ai trouvé :"))]
    assert hook.decide(_payload(_transcript(tmp_path, recs), "Voici ce que j'ai trouvé :")) == hook.RAISON_FRAGMENT


def test_agent_lance_en_fond_rend_l_annonce_faible_legitime(tmp_path):
    recs = [_user("Corrige le bug", "u1"), _assistant(_tool("Agent", description="Codeur", prompt="…")),
            _result(), _assistant(_text("C'est lancé, je m'en occupe."))]
    assert hook.decide(_payload(_transcript(tmp_path, recs), "C'est lancé, je m'en occupe.")) is None
    # Même chose quand le CLI signale une tâche de fond encore en cours.
    recs = [_user("Et l'autre ?", "u2"), _assistant(_text("Je m'en occupe."))]
    path = _transcript(tmp_path, recs)
    assert hook.decide(_payload(path, "Je m'en occupe.", background_tasks=[{"id": "b1"}])) is None
    assert hook.decide(_payload(path, "Je m'en occupe.")) == hook.RAISON_ANNONCE


def test_texte_repris_du_transcript_si_absent(tmp_path):
    recs = [_user("Regarde mon agenda", "u1"), _assistant(_text(ANNONCE))]
    payload = _payload(_transcript(tmp_path, recs), ANNONCE)
    del payload["last_assistant_message"]
    assert hook.decide(payload) == hook.RAISON_ANNONCE


def test_rien_en_cas_de_doute(tmp_path, monkeypatch):
    recs = [_user("Regarde mon agenda", "u1"), _assistant(_text(ANNONCE))]
    path = _transcript(tmp_path, recs)
    assert hook.decide(_payload(tmp_path / "absent.jsonl", ANNONCE)) is None
    # Aucun message ouvrant le tour dans le transcript.
    assert hook.decide(_payload(_transcript(tmp_path, [_assistant(_text(ANNONCE))], "v.jsonl"), ANNONCE)) is None
    config.ESTOP_FILE.write_text("")  # arrêt d'urgence (chemin isolé par conftest)
    assert hook.decide(_payload(path, ANNONCE)) is None
    config.ESTOP_FILE.unlink()
    monkeypatch.setenv("ASTREL_STOP_GUARD", "0")
    assert hook.decide(_payload(path, ANNONCE)) is None


def test_gros_transcript_lu_par_la_fin(tmp_path):
    """Le message de l'utilisateur est loin de la fin (lignes lourdes, images) : lecture élargie."""
    big = _assistant(_text("x" * 300_000))
    recs = [_user("Regarde mon agenda", "u1"), big, big, _assistant(_text(ANNONCE))]
    turn = hook.read_turn(_transcript(tmp_path, recs))
    assert turn["key"] == "u1" and turn["last_text"] == ANNONCE and not turn["tools_used"]


def _run_main(monkeypatch, capsys, stdin):
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    assert hook.main() == 0
    return capsys.readouterr().out


def test_main_sortie_json(tmp_path, monkeypatch, capsys):
    recs = [_user("Regarde mon agenda", "u1"), _assistant(_text(ANNONCE))]
    out = _run_main(monkeypatch, capsys, json.dumps(_payload(_transcript(tmp_path, recs), ANNONCE)))
    assert json.loads(out) == {"decision": "block", "reason": hook.RAISON_ANNONCE}


def test_main_entree_illisible(monkeypatch, capsys):
    assert _run_main(monkeypatch, capsys, "pas du json") == ""
    assert _run_main(monkeypatch, capsys, json.dumps({"hook_event_name": "PreToolUse"})) == ""
