"""Garde « vérifier avant de finir » (01/11/2026, `hooks/stop_hook.py`, idée 7 de
HERMES-agent-coeur.md) : un tour (ou un sous-agent) qui a modifié du code sans rien vérifier
ensuite est relancé, au plus deux fois ; jamais un tour automatique ; ESTOP et variable : rien.
Et l'indice d'échec Debian de `hooks/suivi_outils.py`."""

import importlib.util
import json

import pytest

from astrel import config

_spec = importlib.util.spec_from_file_location("stop_hook_v", config.ROOT / "hooks" / "stop_hook.py")
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)
_spec2 = importlib.util.spec_from_file_location("suivi_outils_v", config.ROOT / "hooks" / "suivi_outils.py")
suivi = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(suivi)


@pytest.fixture(autouse=True)
def _isole(tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "STATE_FILE", tmp_path / "stop-guard.json")
    monkeypatch.setattr(hook, "VERIF_FILE", tmp_path / "stop-verif.json")
    monkeypatch.setattr(hook, "LOG_FILE", tmp_path / "stop-guard.jsonl")
    monkeypatch.setattr(hook, "MAX_RELANCES", 2)
    for var in ("ASTREL_STOP_GUARD", "ASTREL_STOP_VERIF", "ASTREL_STOP_VERIF_AGENTS"):
        monkeypatch.delenv(var, raising=False)


def U(name, **inp):
    return {"type": "tool_use", "name": name, "input": inp}


EDIT = U("Edit", file_path="/data/p/astrel/core.py", old_string="a", new_string="b")


# --- détection (fonction pure) -------------------------------------------------------------

@pytest.mark.parametrize("uses", [
    [EDIT],
    [U("Write", file_path="/data/p/hud/src/main.js")],
    [U("Bash", command="sed -i 's/a/b/' astrel/core.py")],
    [U("Bash", command="cat > outil.sh <<'EOF'\necho\nEOF")],
    [U("Bash", command=".venv/bin/python -m pytest -q tests/x.py"), EDIT],  # test AVANT la modification
    [EDIT, U("Bash", command="git diff"), U("Agent", subagent_type="codeur", description="Corrige")],
])
def test_a_verifier(uses):
    assert hook.a_verifier(uses, "/data/p")


@pytest.mark.parametrize("uses", [
    [],
    [U("Edit", file_path="/data/p/README.md"), U("Write", file_path="/data/p/config/x.json")],  # prose, réglages
    [U("Write", file_path="/tmp/essai.py")],  # brouillon
    [EDIT, U("Bash", command=".venv/bin/python -m pytest -q tests/test_core.py")],
    [EDIT, U("Bash", command="cd hud && npm run build")],
    [EDIT, U("Bash", command="python3 -m py_compile astrel/core.py")],
    [EDIT, U("Bash", command="node --test --test-concurrency=1")],
    [EDIT, U("Bash", command="bash -n run.sh")],
    [EDIT, U("Bash", command="timeout 300 .venv/bin/python tools/hud_check_rendu.py")],
    [EDIT, U("Bash", command=".venv/bin/python -c 'import astrel.core'")],
    [EDIT, U("Bash", command="server/node_modules/.bin/tsx tools/x.ts report")],
    [U("Write", file_path="/data/p/outil.sh"), U("Bash", command="./outil.sh --essai")],
    [EDIT, U("Agent", subagent_type="testeur", description="Teste le lot")],
    [EDIT, U("Agent", description="Relecture du correctif")],
])
def test_rien_a_verifier(uses):
    assert hook.a_verifier(uses, "/data/p") == []


# --- décision ----------------------------------------------------------------------------

def _user(text, uid, pid=None):
    return {"type": "user", "uuid": uid, "promptId": pid or "p-" + uid, "isSidechain": False,
            "message": {"role": "user", "content": text}}


def _assistant(*blocks, side=False):
    return {"type": "assistant", "isSidechain": side, "message": {"role": "assistant", "content": list(blocks)}}


def _write(tmp_path, recs, name="t.jsonl"):
    p = tmp_path / name
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
    return p


def _payload(path, active=False, **extra):
    return {"session_id": "s", "transcript_path": str(path), "cwd": "/data/p", "hook_event_name": "Stop",
            "stop_hook_active": active, "last_assistant_message": "C'est corrigé.", "background_tasks": [], **extra}


TOUR = [_user("Corrige le bug du micro", "u1"), _assistant(EDIT), _assistant({"type": "text", "text": "C'est corrigé."})]


def test_relance_deux_fois_puis_plafond(tmp_path):
    path = _write(tmp_path, TOUR)
    reason = hook.decide(_payload(path))
    assert reason and reason.startswith("[Garde d'Astrel] Tu as modifié du code (core.py)")
    assert hook.decide(_payload(path, active=True)) == reason
    assert hook.decide(_payload(path, active=True)) is None
    log = [json.loads(x) for x in hook.LOG_FILE.read_text().splitlines()]
    assert [(e["kind"], e["action"]) for e in log] == [("verif", "relance"), ("verif", "relance"), ("verif", "plafond")]
    assert len(hook.RAISON_VERIF) < 320  # ≈ 70 tokens par relance


def test_verifie_ensuite_rien(tmp_path):
    recs = TOUR[:2] + [_assistant(U("Bash", command=".venv/bin/python -m pytest -q")), TOUR[2]]
    assert hook.decide(_payload(_write(tmp_path, recs))) is None


@pytest.mark.parametrize("opener", [
    "[Réflexe programmé, l'utilisateur n'a rien demandé à l'instant.] Range le code.",
    "[Mission autonome lancée par l'utilisateur, tour 2 sur 8] Continue.",
    "<task-notification>\n<task-id>abc</task-id>",
])
def test_jamais_un_tour_automatique(tmp_path, opener):
    recs = [_user(opener, "a1")] + TOUR[1:]
    assert hook.decide(_payload(_write(tmp_path, recs))) is None


def test_estop_et_variables(tmp_path, monkeypatch):
    path = _write(tmp_path, TOUR)
    config.ESTOP_FILE.write_text("")
    assert hook.decide(_payload(path)) is None
    config.ESTOP_FILE.unlink()
    monkeypatch.setenv("ASTREL_STOP_VERIF", "0")
    assert hook.decide(_payload(path)) is None
    monkeypatch.setenv("ASTREL_STOP_VERIF", "1")
    monkeypatch.setenv("ASTREL_STOP_GUARD", "0")  # la garde d'annonce coupée n'empêche pas celle-ci
    assert hook.decide(_payload(path))


def test_continuation_d_un_autre_hook(tmp_path):
    assert hook.decide(_payload(_write(tmp_path, TOUR), active=True)) is None


def test_annonce_d_abord(tmp_path):
    recs = TOUR[:2] + [_assistant({"type": "text", "text": "Je vais maintenant lancer les tests."})]
    payload = _payload(_write(tmp_path, recs), last_assistant_message="Je vais maintenant lancer les tests.")
    assert hook.decide(payload) == hook.RAISON_ANNONCE
    # Relancé par NOUS (annonce) : la garde de vérification peut suivre dans la continuation.
    assert hook.decide(_payload(_write(tmp_path, recs), active=True)).startswith("[Garde d'Astrel] Tu as modifié")


# --- sous-agent ----------------------------------------------------------------------------

def _agent_payload(tmp_path, agent_recs, parent=None, **extra):
    main = _write(tmp_path, parent or [_user("Corrige le bug", "u1"), _assistant(U("Agent", subagent_type="auditeur"))])
    agent = _write(tmp_path, agent_recs, "agent-a1.jsonl")
    return {"session_id": "s", "transcript_path": str(main), "agent_transcript_path": str(agent), "agent_id": "a1",
            "agent_type": "general-purpose", "cwd": "/data/p", "hook_event_name": "SubagentStop",
            "stop_hook_active": False,
            **extra}


def _side_user(text, pid="p-u1"):
    return {"type": "user", "isSidechain": True, "uuid": "x", "promptId": pid,
            "message": {"role": "user", "content": text}}


def test_sous_agent(tmp_path):
    recs = [_side_user("Corrige core.py"), _assistant(EDIT, side=True),
            _side_user("<task-notification>\n<task-id>b1</task-id>"),  # tâche de fond finie : même travail
            _assistant({"type": "text", "text": "Fait."}, side=True)]
    reason = hook.decide(_agent_payload(tmp_path, recs))
    assert reason and "core.py" in reason
    assert hook.decide(_agent_payload(tmp_path, recs, stop_hook_active=True)) == reason
    assert hook.decide(_agent_payload(tmp_path, recs, stop_hook_active=True)) is None  # plafond


def test_sous_agent_qui_a_teste(tmp_path):
    recs = [_side_user("Corrige core.py"), _assistant(EDIT, side=True),
            _assistant(U("Bash", command="python3 -m pytest -q tests/test_core.py"), side=True)]
    assert hook.decide(_agent_payload(tmp_path, recs)) is None


def test_sous_agent_d_un_tour_automatique_ou_coupe(tmp_path, monkeypatch):
    recs = [_side_user("Corrige core.py"), _assistant(EDIT, side=True)]
    parent = [_user("[Réflexe programmé, l'utilisateur n'a rien demandé à l'instant.] x", "u1")]
    assert hook.decide(_agent_payload(tmp_path, recs, parent=parent)) is None
    monkeypatch.setenv("ASTREL_STOP_VERIF_AGENTS", "0")
    assert hook.decide(_agent_payload(tmp_path, recs)) is None


def test_main_accepte_subagentstop(tmp_path, monkeypatch, capsys):
    import io
    import sys
    recs = [_side_user("Corrige core.py"), _assistant(EDIT, side=True)]
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_agent_payload(tmp_path, recs))))
    assert hook.main() == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "block"


# --- indice d'échec Debian (hooks/suivi_outils.py) -------------------------------------------

@pytest.mark.parametrize("payload, attendu", [
    ({"error": "error: externally-managed-environment\n× This environment is externally managed"}, "PEP 668"),
    ({"error": "Exit code 127\n/bin/bash: line 1: python: command not found"}, "seulement `python3`"),
    ({"error": "zsh: command not found: python"}, "seulement `python3`"),
    ({"tool_response": {"stdout": "", "stderr": "/usr/bin/env: 'python': No such file or directory"}},
     "seulement `python3`"),
    ({"error": "bash: pip: command not found"}, "pas de `pip`"),
])
def test_indice_debian(payload, attendu):
    hint = suivi.indice({"tool_name": "Bash", "hook_event_name": "PostToolUseFailure", **payload})
    assert attendu in hint and len(hint) < 200  # ≈ 30 tokens, seulement à l'échec


@pytest.mark.parametrize("payload", [
    {"tool_name": "Bash", "error": "python3: command not found"},  # autre chose : pas d'indice python
    {"tool_name": "Bash", "error": "Exit code 1\nAssertionError"},
    {"tool_name": "Bash", "tool_response": {"stdout": "ok"}},
    {"tool_name": "Read", "error": "externally-managed-environment"},
])
def test_pas_d_indice(payload):
    assert suivi.indice(payload) == ""


def test_indice_sortie_du_hook(monkeypatch, capsys):
    import io
    import sys
    payload = {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "tool_use_id": "t",
               "error": "error: externally-managed-environment"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert suivi.main() == 0
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["hookEventName"] == "PostToolUseFailure" and "PEP 668" in out["additionalContext"]
    monkeypatch.setenv("ASTREL_INDICES", "0")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert suivi.main() == 0 and capsys.readouterr().out == ""


# --- relecture du 01/11 -------------------------------------------------------------------

@pytest.mark.parametrize("opener", [
    "[Équipe, message automatique du cœur] Le codeur a fini (« lot 2 »). Lance le testeur.",
    "[Reprise après l'arrêt pour quota, l'utilisateur n'a rien demandé.] La limite de 5 h est levée.",
    "[Relance incomplète : ces agents de la liste n'ont pas été relancés.] a1",
])
def test_marqueurs_automatiques_ajoutes(tmp_path, opener):
    """Point 3 : équipe, reprise après quota, relance incomplète : jamais relancés."""
    assert hook.source_of(opener)
    recs = [_user(opener, "a1")] + TOUR[1:]
    assert hook.decide(_payload(_write(tmp_path, recs))) is None


def test_agent_juge_sur_le_message_qui_l_a_lance(tmp_path):
    """Point 3 : l'agent a été lancé par un message automatique ; l'utilisateur a parlé depuis. Le DERNIER
    message du fil principal est le sien, mais c'est le lanceur qui compte."""
    parent = [_user("[Équipe, message automatique du cœur] Lance le testeur.", "u1"),
              _assistant(U("Agent", subagent_type="testeur")), _user("Et la météo ?", "u2")]
    recs = [_side_user("Teste le lot 2", pid="p-u1"), _assistant(EDIT, side=True)]
    assert hook.decide(_agent_payload(tmp_path, recs, parent=parent)) is None
    # Lancé par l'utilisateur (u2) : la garde s'applique.
    recs = [_side_user("Corrige core.py", pid="p-u2"), _assistant(EDIT, side=True)]
    assert hook.decide(_agent_payload(tmp_path, recs, parent=parent))


def test_agent_relance_apres_releve(tmp_path):
    recs = [_side_user("[Reprise après la relève de session d'Astrel : tu as été arrêté…] x"),
            _assistant(EDIT, side=True)]
    assert hook.decide(_agent_payload(tmp_path, recs)) is None


def test_codeur_laisse_au_relais_de_l_equipe(tmp_path, monkeypatch):
    recs = [_side_user("Corrige core.py"), _assistant(EDIT, side=True)]
    assert hook.decide(_agent_payload(tmp_path, recs, agent_type="codeur")) is None
    monkeypatch.setenv("ASTREL_EQUIPE_APRES_CODEUR", "0")  # relais coupé : la garde reprend
    assert hook.decide(_agent_payload(tmp_path, recs, agent_type="codeur"))


def test_lanceur_introuvable_rien(tmp_path):
    recs = [_side_user("Corrige core.py", pid="p-inconnu"), _assistant(EDIT, side=True)]
    assert hook.decide(_agent_payload(tmp_path, recs)) is None


def test_pas_d_indice_sur_un_succes():
    """Point 4 : un PostToolUse RÉUSSI dont la sortie contient le motif ne reçoit pas d'indice."""
    for out in ("error: externally-managed-environment (déjà vu, ignoré)", "bash: python: command not found"):
        assert suivi.indice({"tool_name": "Bash", "hook_event_name": "PostToolUse",
                             "tool_response": {"stdout": out}}) == ""
