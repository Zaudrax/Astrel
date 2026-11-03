"""Refus fermes et disjoncteur du garde-fou (01/11/2026, `hooks/permission_hook.py`) : au 3e refus
d'affilée dans un même message, arrêter et demander à l'utilisateur ; ensuite, plus aucune carte avant
le message suivant. Jamais un accord de plus. Compteur isolé par conftest (`guard.REFUS_DIR`)."""

import importlib.util
import io
import json
import sys

import pytest

from astrel import config, confirm, guard
from conftest import mode_prive

R = config.ROOT
CRITIQUE = {"command": "git push origin main"}  # critique en autonome : une carte


def _hook():
    spec = importlib.util.spec_from_file_location("permission_hook_dj", R / "hooks" / "permission_hook.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def run(monkeypatch):
    hook = _hook()
    asked = []
    answers = []

    def request(*a, **kw):
        asked.append(a)
        return answers.pop(0) if answers else False

    monkeypatch.setattr(confirm, "request", request)
    monkeypatch.setattr(guard, "get_mode", lambda: "autonome")

    def call(tool="Bash", inp=CRITIQUE, prompt="p1", agent="", answer=...):
        if answer is not ...:
            answers.append(answer)
        payload = {"session_id": "s", "prompt_id": prompt, "tool_name": tool, "tool_input": inp, "cwd": str(R)}
        if agent:
            payload["agent_id"] = agent
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        out = io.StringIO()
        monkeypatch.setattr(sys, "stdout", out)
        assert hook.main() == 0
        o = json.loads(out.getvalue())["hookSpecificOutput"]
        return o["permissionDecision"], o["permissionDecisionReason"]

    call.asked = asked
    return call


def test_refus_ferme(run):
    decision, reason = run()
    assert decision == "deny" and "refusé par l'utilisateur" in reason
    assert "Ne la relance pas" in reason and "autre moyen" in reason and "Disjoncteur" not in reason


def test_troisieme_refus_puis_disjoncteur_ouvert(run):
    assert "Disjoncteur" not in run()[1]
    assert "Disjoncteur" not in run()[1]
    decision, reason = run()
    assert decision == "deny" and "[Disjoncteur du garde-fou] 3 refus d'affilée" in reason and "ARRÊTE" in reason
    assert len(run.asked) == 3
    # Ouvert : refusé SANS carte, même pour une autre action qui demanderait une question.
    decision, reason = run(inp={"command": "sudo ls"})
    assert decision == "deny" and "disjoncteur du garde-fou ouvert" in reason and len(run.asked) == 3
    # Une action libre passe toujours (le disjoncteur ne juge que ce qui demanderait une question).
    assert run(inp={"command": "ls"})[0] == "allow"


def test_nouveau_message_remet_a_zero(run):
    for _ in range(3):
        run(prompt="p1")
    assert "disjoncteur du garde-fou ouvert" in run(prompt="p1")[1]
    decision, reason = run(prompt="p2", answer=True)
    assert decision == "allow" and len(run.asked) == 4


def test_un_oui_remet_la_serie_a_zero(run):
    run()
    run()
    assert run(answer=True)[0] == "allow"
    assert "Disjoncteur" not in run()[1]  # 1er refus d'une nouvelle série


def test_series_par_agent(run):
    for _ in range(3):
        run(agent="a1")
    assert "ouvert" in run(agent="a1")[1]
    assert "Disjoncteur" not in run(agent="a2")[1] and "ouvert" not in run()[1]


def test_politique_et_injoignable_comptent(run, monkeypatch):
    monkeypatch.setattr(guard, "refused", lambda tool: "outil refusé par la politique." if tool == "X" else "")
    run(tool="X", inp={})
    decision, reason = run(answer=None)  # Astrel injoignable
    assert decision == "deny" and "ce n'est pas un accord" in reason
    assert "[Disjoncteur du garde-fou] 3" in run(tool="X", inp={})[1]


def test_garde_fou_jamais_desserre(run, monkeypatch):
    """Disjoncteur en panne (dossier remplacé par un lien) : il se tait, le refus reste un refus."""
    d = guard.REFUS_DIR
    d.parent.mkdir(parents=True, exist_ok=True)
    (d.parent / "ailleurs").mkdir()
    d.symlink_to(d.parent / "ailleurs")
    for _ in range(4):
        decision, reason = run()
        assert decision == "deny" and "Disjoncteur" not in reason
    assert len(run.asked) == 4 and not any((d.parent / "ailleurs").iterdir())


def test_compteur_hors_de_portee_de_claude():
    for tool, inp in (("Write", {"file_path": str(R / "runtime" / "garde-refus" / "series.json")}),
                      ("Bash", {"command": "rm -rf runtime/garde-refus"}),
                      ("Bash", {"command": "echo '{}' > runtime/garde-refus/series.json"})):
        v = guard.assess(tool, inp, mode="autonome", cwd=str(R))
        assert v.needs_confirm and v.guard and not v.keys, (tool, inp)


def test_compteur_borne_et_prive(run):
    for i in range(60):
        run(prompt=f"p{i}")
    f = guard.REFUS_DIR / "series.json"
    assert len(json.loads(f.read_text())["series"]) == 50
    assert mode_prive(f, 0o600) and mode_prive(guard.REFUS_DIR, 0o700)


def test_deleguer_ne_referme_pas_le_disjoncteur(run):
    """Relecture du 01/11 (point 5) : ouvert dans le fil principal = ouvert pour ses sous-agents du
    même message ; un sous-agent d'un AUTRE message n'en hérite pas."""
    for _ in range(3):
        run()
    decision, reason = run(agent="a9")
    assert decision == "deny" and "disjoncteur du garde-fou ouvert" in reason and len(run.asked) == 3
    assert run(agent="a9", prompt="p2", answer=True)[0] == "allow"


def test_sous_agent_n_ouvre_pas_le_fil_principal(run):
    for _ in range(3):
        run(agent="a1")
    assert "ouvert" not in run(answer=True)[1]  # le fil principal reste jugé normalement
