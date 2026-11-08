"""Plan P27 (22/10) : fournisseurs d'IA par leur CLI officiel (Codex, Grok Build), lot 1 (socle).

Aucun vrai CLI : `tests/faux_cli.py` imite leurs formats SUPPOSÉS (documentation, non prouvés ;
le lot 0 les vérifiera). Ce qui est éprouvé ici : la traduction vers le flux de Claude (que
`Brain._translate` lit sans changement), un outil, le REFUS du hook de bout en bout, l'annulation,
la reprise, `from_claude` vrai pour un « serveur MCP » lancé par le CLI, le choix refusé tant que
rien n'est prouvé, l'environnement épuré, et le dossier `runtime/cli/` gardé par le garde-fou."""
from __future__ import annotations

import importlib.util
import json
import os
import queue
import shlex
import stat
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrel import brain, config, core, doctor, fournisseurs, fournisseurs_cli, guard, state
from astrel.brain import Brain
from astrel.fournisseurs import Effectif
from astrel.fournisseurs_cli import base
from astrel.fournisseurs_cli.base import Capacites, EtatTour, SessionCli
from astrel.settings import reglages
from conftest import mode_prive

ROOT = Path(__file__).resolve().parent.parent
FAUX = Path(__file__).resolve().parent / "faux_cli.py"
HOOK = ROOT / "hooks" / "permission_hook_cli.py"
CONSIGNE = "CONSIGNE-DE-TEST : réponds en français."
_COMMANDE_HOOK = base.FournisseurCli.commande_hook


def _hook_module():
    spec = importlib.util.spec_from_file_location("permission_hook_cli_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """Faux CLI à la place de `codex`/`grok`, dossiers et socket de confirmation à part : le hook
    lancé par le faux CLI ne joint JAMAIS le vrai Astrel (aucune carte chez l'utilisateur)."""
    monkeypatch.setattr(base, "DOSSIER", tmp_path / "cli")
    monkeypatch.setattr(config, "WORKDIR", tmp_path / "projets")
    monkeypatch.setattr(config, "SESSION_FILE", tmp_path / "session.json")
    (tmp_path / "projets").mkdir()
    ligne = f"{shlex.quote(sys.executable)} {shlex.quote(str(FAUX))}"
    monkeypatch.setenv("ASTREL_CLI_CODEX", ligne)
    monkeypatch.setenv("ASTREL_CLI_GROKBUILD", ligne)
    for c in fournisseurs_cli.CLIS.values():
        monkeypatch.setattr(c, "_det", None)
    absent = tmp_path / "confirm-absent.sock"

    def hook_isole(self, mode):  # le VRAI hook, socket de confirmation absente
        code = ("import runpy, sys; from pathlib import Path; sys.path.insert(0, %r); "
                "from astrel import config; config.CONFIRM_SOCKET = Path(%r); "
                "sys.argv = [%r, %r, %r]; runpy.run_path(%r, run_name='__main__')"
                % (str(ROOT), str(absent), str(HOOK), self.id, mode, str(HOOK)))
        if os.name == "nt":  # 03/11 : le faux CLI lance la ligne par cmd.exe, qui ne connaît que les « "…" »
            return subprocess.list2cmdline([sys.executable, "-c", code])
        return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"

    monkeypatch.setattr(base.FournisseurCli, "commande_hook", hook_isole)
    log = tmp_path / "appels.jsonl"
    env = {**os.environ, "FAUX_CLI_LOG": str(log), "FAUX_CLI_PIDS": str(tmp_path / "pids.json"),
           "OPENAI_API_KEY": "sk-FAUSSE", "CODEX_API_KEY": "FAUSSE", "XAI_API_KEY": "xai-FAUSSE",
           "GROK_CODE_XAI_API_KEY": "xai-FAUSSE", "ANTHROPIC_API_KEY": "sk-ant-FAUSSE", "ASTREL_BRAIN": "marqueur-du-cerveau"}
    return SimpleNamespace(tmp=tmp_path, log=log, env=env, pids=tmp_path / "pids.json")


def _appels(c) -> list[dict]:
    return [json.loads(x) for x in c.log.read_text().splitlines()] if c.log.exists() else []


def _session(c, fid="codex", mode=base.MODE_CONVERSATION, sid="") -> SessionCli:
    return SessionCli(fournisseurs_cli.CLIS[fid], "defaut", mode, session_id=sid, consigne=CONSIGNE,
                      base_env=c.env, cwd=config.WORKDIR)


def _brain(sess: SessionCli) -> Brain:
    b = Brain()
    b.fournisseur = Effectif("cli", sess.f.id, "defaut", outils=sess.mode == base.MODE_OUTILS)
    b._proc = sess  # type: ignore[assignment]
    b._start_readers()
    return b


def _tour(b: Brain, texte: str, timeout: float = 20.0) -> list:
    b.send(texte)
    got, t0 = [], time.time()
    for ev in b.events(idle_s=0.2):
        if time.time() - t0 > timeout:
            raise AssertionError(f"pas de fin de tour : {[(e.kind, e.text) for e in got]}")
        if ev.kind == "idle":
            continue
        got.append(ev)
        if ev.kind in ("turn_end", "error"):
            return got
    return got


def _kinds(evs) -> list[str]:
    return [e.kind for e in evs]


# --------------------------------------------------------------------------- flux, reprise

def test_flux_codex_traduit_au_format_de_claude(cli):
    sess = _session(cli)
    b = _brain(sess)
    evs = _tour(b, "salut")
    assert _kinds(evs)[0] == "init" and evs[0].data["session_id"] == sess.session_id
    assert "message_start" in _kinds(evs)
    assert "".join(e.text for e in evs if e.kind == "text") == "Bonjour, tour 1."
    fin = evs[-1]
    assert fin.kind == "turn_end" and fin.text == "Bonjour, tour 1."
    assert fin.data["usage"] == {"input_tokens": 50, "cache_read_input_tokens": 50, "output_tokens": 10}
    assert fin.data["process"] == sess.pid and fin.data["session_id"] == sess.session_id
    assert any(e.kind == "context" and e.data["tokens"] == 100 for e in evs)
    assert json.loads(config.SESSION_FILE.read_text())["id"] == sess.session_id
    (appel,) = _appels(cli)
    assert appel["argv"][:4] == ["exec", "--json", "--skip-git-repo-check", "--sandbox"]  # après le binaire
    assert appel["argv"][4] == "read-only" and appel["argv"][-1] == "-"
    assert appel["prompt"].startswith("[Consigne d'Astrel") and CONSIGNE in appel["prompt"]
    assert appel["prompt"].endswith("[Message]\nsalut")  # consigne par stdin, jamais dans argv
    assert CONSIGNE not in " ".join(appel["argv"])
    env = appel["env"]
    assert env["OPENAI_API_KEY"] is None and env["CODEX_API_KEY"] is None and env["XAI_API_KEY"] is None
    assert env["ANTHROPIC_API_KEY"] is None
    assert env["GROK_CODE_XAI_API_KEY"] is None  # clé de Grok Build : primerait sur l'abonnement
    assert env["CODEX_HOME"] == str(cli.tmp / "cli" / "codex")
    assert env["ASTREL_BRAIN"] == "marqueur-du-cerveau"  # `from_claude` hors cgroup
    assert appel["cwd"] == str(cli.tmp / "cli" / "codex" / "espace")  # conversation : dossier vide
    b.stop()


def test_reprise_meme_conversation_puis_relance_puis_session_perdue(cli):
    sess = _session(cli)
    b = _brain(sess)
    _tour(b, "un")
    evs = _tour(b, "deux")
    assert evs[-1].text == "Bonjour, tour 2."
    deuxieme = _appels(cli)[-1]
    assert "resume" in deuxieme["argv"] and not deuxieme["prompt"].startswith("[Consigne")
    b.stop()
    # Relance d'Astrel : même id Astrel (runtime/session.json) → la conversation du CLI continue.
    sess2 = _session(cli, sid=sess.session_id)
    b2 = _brain(sess2)
    assert _tour(b2, "trois")[-1].text == "Bonjour, tour 3."
    # Conversation du CLI perdue : reprise refusée avant toute sortie → conversation neuve, consigne.
    carte = cli.tmp / "cli" / "codex" / "sessions.json"
    carte.write_text(json.dumps({sess.session_id: "id-inconnu"}))
    evs = _tour(b2, "quatre")
    assert evs[-1].kind == "turn_end" and evs[-1].text == "Bonjour, tour 1."
    assert _appels(cli)[-1]["prompt"].startswith("[Consigne d'Astrel")
    assert json.loads(carte.read_text())[sess.session_id] != "id-inconnu"
    assert mode_prive(carte, 0o600)
    b2.stop()


def test_un_outil_puis_son_resultat(cli):
    b = _brain(_session(cli))
    evs = _tour(b, "OUTIL")
    pending = [e for e in evs if e.kind == "tool_pending"]
    start = [e for e in evs if e.kind == "tool_start"]
    res = [e for e in evs if e.kind == "tool_result"]
    assert pending[0].tool == "Bash" and start[0].tool == "Bash" and start[0].text == "ls"
    assert res[0].tool == "Bash" and res[0].text == "a.txt b.txt" and res[0].data["is_error"] is False
    b.stop()


def test_flux_grok_suppose(cli):
    sess = _session(cli, fid="grokbuild")
    b = _brain(sess)
    evs = _tour(b, "OUTIL")
    assert [e.tool for e in evs if e.kind == "tool_start"] == ["bash"]
    assert evs[-1].kind == "turn_end" and evs[-1].text == "Bonjour, tour 1."
    assert evs[-1].data["usage"]["input_tokens"] == 100
    assert _tour(b, "encore")[-1].text == "Bonjour, tour 2."  # --resume
    appels = _appels(cli)
    assert "--output-format" in appels[0]["argv"] and "--resume" in appels[1]["argv"]
    assert appels[0]["env"]["GROK_HOME"] == str(cli.tmp / "cli" / "grokbuild")
    b.stop()


def test_cli_qui_plante(cli):
    b = _brain(_session(cli))
    evs = _tour(b, "PLANTE")
    assert evs[-1].kind == "error" and "code 3" in evs[-1].text and "boom" in evs[-1].text
    assert _tour(b, "ensuite")[-1].kind == "turn_end"  # la session continue
    b.stop()


# --------------------------------------------------------------------------- hook de bout en bout

def test_refus_du_hook_de_bout_en_bout(cli):
    """Le faux CLI demande au VRAI hook (comme le vrai CLI est supposé le faire) : une commande
    critique est refusée (Astrel injoignable = refus) et jamais lancée ; `ls` passe en mode outils."""
    b = _brain(_session(cli, mode=base.MODE_OUTILS))
    evs = _tour(b, "HOOK: sudo reboot", timeout=60)
    (res,) = [e for e in evs if e.kind == "tool_result"]
    assert res.data["is_error"] is True and "refusé par le hook" in res.text and "injoignable" in res.text
    evs = _tour(b, "HOOK: ls", timeout=60)
    (res,) = [e for e in evs if e.kind == "tool_result"]
    assert res.data["is_error"] is False and res.text == "exécuté"
    assert _appels(cli)[-1]["cwd"] == str(config.WORKDIR)
    b.stop()


def test_conversation_seule_le_hook_refuse_tout(cli):
    b = _brain(_session(cli))
    evs = _tour(b, "HOOK: ls", timeout=60)
    (res,) = [e for e in evs if e.kind == "tool_result"]
    assert res.data["is_error"] is True and "conversation seule" in res.text
    b.stop()


# --------------------------------------------------------------------------- annulation, MCP, file

def _attendre(pred, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _vivant(pid: int) -> bool:
    from astrel.platform import systeme

    return systeme.pid_vivant(pid)  # 03/11 : /proc sous Linux, psutil ailleurs (avant : toujours « mort » hors Linux)


def _lui_ou_descendant(pid: int, ancetre: int) -> bool:
    """03/11 (CI Windows) : le python.exe d'une venv Windows est un LANCEUR qui relance le vrai interpréteur en
    enfant ; le pid que le faux CLI écrit est alors celui de cet enfant, pas celui du Popen."""
    from astrel.platform import proc

    return proc.descends_from(pid, ancetre)  # le pid lui-même compris


def test_annulation_et_mcp_vu_comme_claude(cli):
    sess = _session(cli, mode=base.MODE_OUTILS)
    b = _brain(sess)
    got: queue.Queue = queue.Queue()

    def lire():
        for ev in b.events(idle_s=0.2):
            if ev.kind != "idle":
                got.put(ev)
            if ev.kind in ("turn_end", "error"):
                return

    import threading
    threading.Thread(target=lire, daemon=True).start()
    b.send("LENT")
    assert _attendre(cli.pids.exists)
    pids = json.loads(cli.pids.read_text())
    # `core._claude_pid` lit `brain._proc.pid` : le process du tour en cours.
    assert _lui_ou_descendant(pids["cli"], sess.pid)
    astrel_like = SimpleNamespace(brain=b)
    claude_pid = core.Astrel._claude_pid(astrel_like)
    assert state.from_claude(pids["mcp"], claude_pid, service_cg="") is True  # le « MCP » du CLI
    autre = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert state.from_claude(autre.pid, claude_pid, service_cg="") is False  # pas lancé par le CLI
    finally:
        autre.kill()
        autre.wait()
    t0 = time.time()
    b.interrupt()
    evs = []
    while True:
        ev = got.get(timeout=15)
        evs.append(ev)
        if ev.kind in ("turn_end", "error"):
            break
    assert evs[-1].kind == "turn_end" and time.time() - t0 < 10  # fin normale, sans erreur
    assert _attendre(lambda: not _vivant(pids["cli"]) and not _vivant(pids["mcp"]))  # groupe entier
    assert b.alive  # la session reste : un tour = un process
    b.stop()
    assert sess.poll() == 0 and not b.alive


def test_message_en_plein_tour_mis_en_file(cli):
    sess = _session(cli)
    b = _brain(sess)
    b.send("LENT")
    assert _attendre(cli.pids.exists)
    b.send("deuxième")
    sess.annuler()  # sans vider : le message en file part ensuite
    fins, t0 = [], time.time()
    for ev in b.events(idle_s=0.2):
        if ev.kind in ("turn_end", "error"):
            fins.append(ev)
        if len(fins) == 2 or time.time() - t0 > 20:
            break
    assert [e.kind for e in fins] == ["turn_end", "turn_end"] and fins[1].text == "Bonjour, tour 2."
    b.stop()


def test_controle_sans_effet_avec_un_cli(cli):
    b = _brain(_session(cli))
    assert b.stop_task("tache") is False
    with pytest.raises(RuntimeError, match="sans effet"):
        b.set_model("opus")
    b.stop()


# --------------------------------------------------------------------------- construction

def test_jamais_d_option_qui_approuve_tout(cli):
    for bad in ("--always-approve", "--dangerously-bypass-approvals-and-sandbox", "--yolo", "--full-auto"):
        with pytest.raises(ValueError):
            base.verifier_argv(["codex", "exec", bad])
    for fid in ("codex", "grokbuild"):
        for mode in (base.MODE_OUTILS, base.MODE_CONVERSATION):
            c = fournisseurs_cli.CLIS[fid]
            argv = c.commande("x", "sid", "gpt-5", mode).argv
            base.verifier_argv(argv)
            assert not set(argv) & base.INTERDITS


def test_config_outils_dans_le_dossier_du_cli(cli, monkeypatch):
    monkeypatch.setattr(base.FournisseurCli, "commande_hook", _COMMANDE_HOOK)  # la vraie ligne (rien n'est lancé)
    c = fournisseurs_cli.CLIS["codex"]
    c.dossier().mkdir(parents=True)
    auth = c.dossier() / "auth.json"
    auth.write_text('{"connexion": "de l utilisateur"}')
    c.preparer(base.MODE_OUTILS)
    conf = tomllib.loads((c.dossier() / "config.toml").read_text())
    assert set(conf["mcp_servers"]) == {"astrel", "connecteurs"}
    assert conf["mcp_servers"]["astrel"]["args"] == ["-m", "astrel.mcp_server"]
    assert conf["mcp_servers"]["astrel"]["env"]["ASTREL_BRAIN"]
    hooks = json.loads((c.dossier() / "hooks.json").read_text())["hooks"]["PreToolUse"]
    assert hooks[0]["matcher"] == "*" and hooks[0]["hooks"][0]["command"].endswith("codex outils")
    for f in ("config.toml", "hooks.json"):
        assert mode_prive(c.dossier() / f)
    assert mode_prive(c.dossier(), 0o700)
    c.preparer(base.MODE_CONVERSATION)
    assert "mcp_servers" not in tomllib.loads((c.dossier() / "config.toml").read_text())
    assert json.loads((c.dossier() / "hooks.json").read_text())["hooks"]["PreToolUse"][0]["hooks"][0][
        "command"].endswith("codex conversation")
    assert auth.read_text() == '{"connexion": "de l utilisateur"}'  # jamais touché
    g = fournisseurs_cli.CLIS["grokbuild"]
    g.preparer(base.MODE_OUTILS)
    data = json.loads((g.dossier() / "settings.json").read_text())
    assert set(data["mcpServers"]) == {"astrel", "connecteurs"} and data["hooks"]["PreToolUse"]


def test_commande_hook_reelle():
    c = fournisseurs_cli.CLIS["codex"]
    line = _COMMANDE_HOOK(c, "outils")
    # 23/10 (audit P29) : mode isolé comme le hook de Claude, sans « || exit 2 ».
    assert shlex.split(line) == [config.VENV_PYTHON.as_posix(), *config.hook_python_args(), HOOK.as_posix(),
                                 "codex", "outils"]


def test_detection(cli, monkeypatch):
    c = fournisseurs_cli.CLIS["codex"]
    det = c.detecter(frais=True)
    assert det.installe and det.version == "faux-cli 0.1.0" and det.connecte is True
    monkeypatch.setenv("FAUX_CLI_CONNECTE", "0")
    assert c.detecter(frais=True).connecte is False
    monkeypatch.delenv("ASTREL_CLI_CODEX")
    monkeypatch.setattr(base.shutil, "which", lambda name: None)
    assert c.detecter(frais=True).installe is False and not c.installe()


def test_traducteur_codex_cas_limites():
    c, etat = fournisseurs_cli.CLIS["codex"], EtatTour()
    assert c.traduire({"type": "thread.started", "thread_id": "t1"}, etat) == [{"type": "_session", "id": "t1"}]
    assert c.traduire({"type": "item.completed", "item": {"type": "reasoning", "text": "secret"}}, etat) == []
    out = c.traduire({"type": "item.completed", "item": {"id": "m", "type": "mcp_tool_call", "server": "astrel",
                                                          "tool": "notify", "arguments": {"t": 1}, "result": "ok"}}, etat)
    assert out[2]["message"]["content"][0]["name"] == "mcp__astrel__notify"
    fin = c.traduire({"type": "turn.failed", "error": {"message": "limite atteinte"}}, etat)
    assert fin[-1]["is_error"] is True and "limite atteinte" in fin[-1]["result"]
    assert c.traduire({"type": "turn.completed"}, etat) == []  # une seule fin par tour


# --------------------------------------------------------------------------- hook (unitaire)

def test_hook_lecture_des_charges():
    h = _hook_module()
    assert h.actions({"tool_name": "shell", "tool_input": {"command": ["bash", "-lc", "ls -la"]}}) == [
        ("Bash", {"command": "ls -la"})]
    assert h.actions({"tool_name": "exec_command", "tool_input": {"cmd": "git status"}}) == [
        ("Bash", {"command": "git status"})]
    assert h.actions({"tool_name": "Bash", "tool_input": "echo x"}) == [("Bash", {"command": "echo x"})]
    patch = "*** Begin Patch\n*** Add File: a.py\n*** Update File: b.py\n*** Delete File: c.py\n*** End Patch"
    assert h.actions({"tool_name": "apply_patch", "tool_input": {"input": patch}}) == [
        ("Write", {"file_path": "a.py"}), ("Edit", {"file_path": "b.py"}), ("Bash", {"command": "rm -- c.py"})]
    assert h.actions({"tool_name": "shell", "tool_input": {"command": ["apply_patch", patch]}})[0][0] == "Write"
    assert h.actions({"tool_name": "astrel.notify", "tool_input": {}}) == [("mcp__astrel__notify", {})]
    assert h.actions({"tool_name": "mcp__astrel__power_action", "tool_input": {}})[0][0] == "mcp__astrel__power_action"
    assert h.actions({"tool_name": "write_file", "tool_input": {"path": "/etc/x"}}) == [
        ("Write", {"file_path": "/etc/x"})]
    for bad in ({"tool_name": "outil_magique", "tool_input": {}}, {"tool_name": "Agent", "tool_input": {}},
                {"tool_name": "Task", "tool_input": {}}, {"tool_name": "autre.serveur", "tool_input": {}},
                {"tool_name": "shell", "tool_input": {"command": 42}}, {"tool_name": "write_file", "tool_input": {}},
                {"tool_name": "apply_patch", "tool_input": {"input": "rien"}}, {"tool_input": {}},
                {"tool_name": "Read", "tool_input": "pas un objet"}):
        assert h.actions(bad) is None, bad


def test_hook_decide_refus_par_defaut(monkeypatch):
    h = _hook_module()
    bash = {"tool_name": "shell", "tool_input": {"command": "ls"}, "cwd": "/tmp"}
    assert h.decider(bash, "codex", "conversation")[0] == "deny"
    assert h.decider(bash, "inconnu", "outils")[0] == "deny"
    assert h.decider(["pas", "un", "objet"], "codex", "outils")[0] == "deny"
    assert h.decider({"tool_name": "outil_magique", "tool_input": {}}, "codex", "outils")[0] == "deny"
    calls = []
    monkeypatch.setattr(h.guard, "refused", lambda tool: "")
    monkeypatch.setattr(h.guard, "assess", lambda tool, inp, cwd=None: SimpleNamespace(
        needs_confirm=tool != "Bash" or "rm" in inp["command"], desc="une action", key="k", keys=(), guard=False,
        lift=False))
    for answer, want in ((None, "deny"), (False, "deny"), (True, "allow")):
        monkeypatch.setattr(h.confirm, "request", lambda *a, _r=answer, **k: calls.append(a) or _r)
        assert h.decider({"tool_name": "shell", "tool_input": {"command": "rm x"}}, "codex", "outils")[0] == want
    assert h.decider(bash, "codex", "outils") == ("allow", "action autorisée par Astrel")
    monkeypatch.setattr(h.guard, "refused", lambda tool: "refusé par la politique")
    assert h.decider(bash, "codex", "outils") == ("deny", "refusé par la politique")


def _hook_proc(stdin: str, mode: str, tmp_path: Path) -> subprocess.CompletedProcess:
    code = ("import runpy, sys; from pathlib import Path; sys.path.insert(0, %r); from astrel import config; "
            "config.CONFIRM_SOCKET = Path(%r); sys.argv = [%r, 'codex', %r]; runpy.run_path(%r, run_name='__main__')"
            % (str(ROOT), str(tmp_path / "absent.sock"), str(HOOK), mode, str(HOOK)))
    return subprocess.run([sys.executable, "-c", code], input=stdin, capture_output=True, text=True, timeout=30)


def test_hook_sorties_refus_code_2_et_json(tmp_path):
    p = _hook_proc("pas du JSON", "outils", tmp_path)
    assert p.returncode == 2 and "illisible" in p.stderr
    out = json.loads(p.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny" and out["decision"] == "block"
    p = _hook_proc(json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(ROOT)}),
                   "outils", tmp_path)
    assert p.returncode == 0 and json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"
    p = _hook_proc(json.dumps({"tool_name": "Bash", "tool_input": {"command": "sudo reboot"}}), "outils", tmp_path)
    assert p.returncode == 2 and "injoignable" in p.stderr


def test_doctor_controle_le_hook_cli_et_son_empreinte():
    assert doctor.check_hook_cli() == (doctor.OK, "refuse sur entrée illisible, panne, conversation seule et "
                                                  "outil inconnu")
    assert "hooks/permission_hook_cli.py" in doctor.fingerprint()


# --------------------------------------------------------------------------- garde-fou : runtime/cli

def test_dossier_des_cli_garde_par_le_garde_fou():
    auth = str(config.RUNTIME / "cli" / "codex" / "auth.json")
    v = guard.classify("Read", {"file_path": auth})
    assert v.needs_confirm and v.guard
    v = guard.classify("Write", {"file_path": str(config.RUNTIME / "cli" / "codex" / "hooks.json")})
    assert v.needs_confirm and v.guard and not v.lift
    v = guard.classify("Bash", {"command": f"cat {auth}"})
    assert v.needs_confirm and v.guard
    v = guard.classify("Bash", {"command": "cat runtime/cli/codex/auth.json"}, cwd=str(config.ROOT))
    assert v.needs_confirm and v.guard
    v = guard.classify("Write", {"file_path": str(HOOK)})  # comme le hook de Claude : levable seulement
    assert v.needs_confirm and v.guard and v.lift


# --------------------------------------------------------------------------- fournisseurs.py

@pytest.fixture
def prudent(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "MODE_FILE", tmp_path / "autonomy.json")  # jamais le vrai fichier

    def regler(mode: str) -> None:
        (tmp_path / "autonomy.json").write_text(json.dumps({"mode": mode}))
    regler("prudent")
    return regler


def _prouver(monkeypatch, hook_refus: bool) -> None:
    for c in fournisseurs_cli.CLIS.values():
        monkeypatch.setattr(c, "capacites", Capacites(prouve=True, hook_refus=hook_refus))


def test_choix_seulement_si_le_cli_est_installe(cli, monkeypatch):
    vals = [v for v, _ in fournisseurs.choix()]
    assert "codex:defaut" in vals and "grokbuild:defaut" in vals and "codex:gpt-5" in vals
    assert fournisseurs.lire("codex") == ("codex", "defaut")
    assert fournisseurs.lire("grokbuild:grok-4") == ("grokbuild", "grok-4")
    monkeypatch.delenv("ASTREL_CLI_CODEX")
    monkeypatch.setattr(base.shutil, "which", lambda name: None)
    vals = [v for v, _ in fournisseurs.choix()]
    assert "codex:defaut" not in vals and "grokbuild:defaut" in vals


def test_choix_refuse_tant_que_rien_n_est_prouve(cli, prudent, monkeypatch):
    assert "pas encore prouvé" in fournisseurs.refus_choix("codex:defaut", False, False)
    eff = fournisseurs.effectif("codex:defaut")
    assert eff.genre == "abonnement" and "pas encore prouvé" in eff.note
    monkeypatch.delenv("ASTREL_CLI_GROKBUILD")
    monkeypatch.setattr(base.shutil, "which", lambda name: None)
    assert fournisseurs.refus_choix("grokbuild:defaut", False, False).startswith("installe d'abord le CLI Grok Build")
    assert "non installé" in fournisseurs.effectif("grokbuild:defaut").note


def test_regles_une_fois_prouve(cli, prudent, monkeypatch):
    _prouver(monkeypatch, hook_refus=True)
    assert fournisseurs.refus_choix("codex:defaut", False, False) == ""
    assert "Sans confirmation" in fournisseurs.refus_choix("codex:defaut", True, False)
    assert "garde-fou" in fournisseurs.refus_choix("codex:defaut", False, True)
    eff = fournisseurs.effectif("codex:gpt-5")
    assert (eff.genre, eff.id, eff.modele, eff.outils) == ("cli", "codex", "gpt-5", True)
    assert eff.session_key == "cli:codex" and eff.facturation == "forfait" and not eff.cle_api
    prudent("autonome")
    assert "PRUDENT" in fournisseurs.refus_choix("codex:defaut", False, False)
    eff = fournisseurs.effectif("codex:defaut")
    assert eff.genre == "cli" and eff.outils is False and eff.session_key == "cli:codex:sans-outil"
    reglages.set("fournisseur", "codex:defaut")
    assert fournisseurs.non_claude_choisi() and fournisseurs.prudent_exige()
    monkeypatch.setenv("FAUX_CLI_CONNECTE", "0")
    fournisseurs_cli.CLIS["codex"]._det = None
    assert "connecte d'abord" in fournisseurs.refus_choix("codex:defaut", False, False)
    assert fournisseurs.effectif("codex:defaut").genre == "abonnement"


def test_hook_non_prouve_conversation_seule(cli, prudent, monkeypatch):
    _prouver(monkeypatch, hook_refus=False)
    eff = fournisseurs.effectif("codex:defaut")
    assert eff.genre == "cli" and eff.outils is False
    assert fournisseurs.refus_choix("codex:defaut", False, False) == ""  # pas d'outils : pas d'exigence
    sess = fournisseurs_cli.lancer("codex", "defaut", True, "", CONSIGNE, cli.env, config.WORKDIR)
    assert sess.mode == base.MODE_CONVERSATION  # jamais d'outils sans refus prouvé
    sess.kill()


def test_env_cerveau_cli_sans_cle():
    env = fournisseurs.env_cerveau({"OPENAI_API_KEY": "x", "XAI_API_KEY": "y", "ANTHROPIC_API_KEY": "z",
                                    "CLE_API_OPENAI": "w", "PATH": "/bin"}, "cli", windows=False)
    assert env == {"PATH": "/bin"}


def test_lignes_reglages(cli, monkeypatch):
    lignes = {l["label"]: l for l in fournisseurs.lignes_cli()}
    assert "installé, version faux-cli 0.1.0" in lignes["CLI Codex"]["text"]
    assert "pas encore prouvé" in lignes["CLI Codex"]["text"]
    monkeypatch.delenv("ASTREL_CLI_GROKBUILD")
    monkeypatch.setattr(base.shutil, "which", lambda name: None)
    fournisseurs_cli.CLIS["grokbuild"]._det = None
    assert {l["label"]: l for l in fournisseurs.lignes_cli()}["CLI Grok Build"]["text"] == "non installé"
    fournisseurs.etat.actif = Effectif("cli", "codex", "defaut", outils=False)
    try:
        cerveau = fournisseurs.vue()["value"]["lines"][0]
        assert cerveau["text"].endswith(" par son CLI, conversation seule") and cerveau["level"] == "warn"
    finally:
        fournisseurs.etat.actif = Effectif("abonnement", fournisseurs.DEFAUT)


# --------------------------------------------------------------------------- cerveau et cœur

def test_brain_start_genre_cli(cli, monkeypatch):
    eff = Effectif("cli", "codex", "defaut", outils=False)
    monkeypatch.setattr(fournisseurs, "effectif", lambda valeur=None: eff)
    monkeypatch.setattr(brain.proc, "brain_env", lambda base=None: dict(cli.env))
    b = Brain()
    b.start(fresh=True)
    try:
        assert isinstance(b._proc, SessionCli) and b.fournisseur is eff and b.alive
        evs = _tour(b, "salut")
        assert evs[-1].text == "Bonjour, tour 1."
        assert CONSIGNE not in _appels(cli)[0]["prompt"] and "CONVERSATION SEULE" in _appels(cli)[0]["prompt"]
        saved = json.loads(config.SESSION_FILE.read_text())
        assert saved["fournisseur"] == "cli:codex:sans-outil" and saved["id"] == b.session_id
    finally:
        b.stop()
    b2 = Brain()
    b2.start()  # reprise : même id Astrel, même conversation du CLI
    try:
        assert b2._proc.session_id == saved["id"]
        assert _tour(b2, "encore")[-1].text == "Bonjour, tour 2."
    finally:
        b2.stop()


# --------------------------------------------------------------------------- correction n°2 (fiches 2 et 3, 22/10)

def _lire_tour(b: Brain, got: queue.Queue) -> None:
    for ev in b.events(idle_s=0.2):
        if ev.kind != "idle":
            got.put(ev)
        if ev.kind in ("turn_end", "error"):
            return


def _fin(got: queue.Queue, timeout: float) -> list:
    evs = []
    while True:
        ev = got.get(timeout=timeout)
        evs.append(ev)
        if ev.kind in ("turn_end", "error"):
            return evs


def test_annulation_tue_le_petit_enfant_qui_ignore_sigint(cli):
    """Fiche 2, BUG1 : le CLI meurt sur SIGINT, son petit-enfant l'ignore et garde stdout ouvert ;
    `annuler()` doit tuer le GROUPE (SIGKILL après un court délai), sinon le tour ne finit jamais."""
    import threading
    b = _brain(_session(cli, mode=base.MODE_OUTILS))
    got: queue.Queue = queue.Queue()
    threading.Thread(target=_lire_tour, args=(b, got), daemon=True).start()
    b.send("PETITENFANT")
    assert _attendre(cli.pids.exists)
    pids = json.loads(cli.pids.read_text())
    t0 = time.time()
    b.interrupt()
    evs = _fin(got, timeout=15)
    assert evs[-1].kind == "turn_end" and time.time() - t0 < 5
    assert _attendre(lambda: not _vivant(pids["petit_enfant"]), timeout=3)
    b.stop()


def test_annulation_sous_windows_tue_l_arbre(monkeypatch):
    """03/11 (CI Windows) : sans groupe, `terminate` ne tuait que le CLI ; l'ARBRE est tué (taskkill /T /F).
    Simulé sous Linux : faux `os.name`, faux process."""
    tues = []
    monkeypatch.setattr(base, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(base.systeme, "tuer_arbre", lambda pid, *a: tues.append(pid))
    p = SimpleNamespace(pid=4242, poll=lambda: None, wait=lambda timeout=None: 0, kill=lambda: tues.append("kill"))
    base.annuler(p)
    assert tues == [4242]


@pytest.mark.parametrize("fid", ["codex", "grokbuild"])
def test_traducteur_qui_leve_tue_le_cli_et_rend_une_erreur(cli, fid):
    """Fiche 2, BUG2 : un évènement illisible pour le traducteur (item/event non-objet) : erreur
    visible ET process du CLI tué (avant : l'exception remontait, le CLI restait vivant)."""
    b = _brain(_session(cli, fid=fid))
    t0 = time.time()
    evs = _tour(b, "BIZARRE", timeout=20)
    assert evs[-1].kind == "error" and "illisible" in evs[-1].text and time.time() - t0 < 10
    pid = json.loads(cli.pids.read_text())["cli"]
    assert _attendre(lambda: not _vivant(pid), timeout=5)
    b.stop()


def test_sortie_zero_sans_json_est_une_erreur(cli):
    """Fiche 2, BUG3 : code 0 avec du texte non-JSON seul = erreur visible, pas un succès vide."""
    b = _brain(_session(cli))
    evs = _tour(b, "NONJSON")
    assert evs[-1].kind == "error" and "pas du JSON" in evs[-1].text
    b.stop()


def test_reprise_en_panne_garde_la_conversation(cli):
    """Fiche 3 : une panne pendant une REPRISE (code 3, rien sur stdout) n'est pas « session
    introuvable » : erreur rendue, id du CLI GARDÉ, un seul lancement, l'historique continue."""
    sess = _session(cli)
    b = _brain(sess)
    _tour(b, "un")
    carte = cli.tmp / "cli" / "codex" / "sessions.json"
    avant = carte.read_text()
    n0 = len(_appels(cli))
    evs = _tour(b, "PLANTE")
    assert evs[-1].kind == "error" and "boom" in evs[-1].text
    assert len(_appels(cli)) - n0 == 1  # pas de second lancement en conversation neuve
    assert carte.read_text() == avant
    # La même conversation continue (le faux CLI a compté l'appel en panne : tour 3).
    assert _tour(b, "deux")[-1].text == "Bonjour, tour 3."
    cli_id = json.loads(avant)[sess.session_id]
    assert _appels(cli)[-1]["argv"][-3:] == ["resume", cli_id, "-"]
    b.stop()


def test_session_introuvable_seule_repart_a_neuf():
    c = fournisseurs_cli.CLIS["codex"]
    assert c.session_perdue("session introuvable : abc")
    assert c.session_perdue("Error: thread not found: 1234")
    assert not c.session_perdue("boom : le faux CLI plante")
    assert not c.session_perdue("")


def test_octets_non_utf8_sur_stdout_et_stderr(cli):
    """Fiche 3 : 0xFF sur stdout = remplacé, pas une exception ; sur stderr, le fil de lecture
    survit (la dernière ligne figure dans l'erreur)."""
    b = _brain(_session(cli))
    evs = _tour(b, "OCTET")
    assert evs[-1].kind == "turn_end" and evs[-1].text == "Bonjour, tour 1."
    evs = _tour(b, "OCTETPLANTE")
    assert evs[-1].kind == "error" and "derniere ligne lisible" in evs[-1].text
    b.stop()


def test_env_purge_etendue(cli):
    cli.env.update({"CLAUDE_CODE_OAUTH_TOKEN": "x", "OPENAI_API_BASE": "http://x", "XAI_BASE_URL": "http://x",
                    "ANTHROPIC_AUTH_TOKEN": "x"})
    b = _brain(_session(cli))
    _tour(b, "salut")
    env = _appels(cli)[-1]["env"]
    for k in ("CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_BASE", "XAI_BASE_URL", "ANTHROPIC_AUTH_TOKEN"):
        assert env[k] is None, k
    b.stop()


@pytest.mark.parametrize("fid", ["codex", "grokbuild"])
def test_modele_commencant_par_un_tiret_refuse(cli, fid):
    sess = SessionCli(fournisseurs_cli.CLIS[fid], "--yolo-bis", base.MODE_CONVERSATION, consigne=CONSIGNE,
                      base_env=cli.env, cwd=config.WORKDIR)
    b = _brain(sess)
    evs = _tour(b, "salut")
    assert evs[-1].kind == "error" and "modèle" in evs[-1].text
    assert _appels(cli) == []  # jamais lancé
    b.stop()
    assert fournisseurs.lire("codex:-c") == ("codex", "defaut")


def test_detection_delai_court(cli, monkeypatch):
    assert base.DETECTION_DELAI_S <= 3.0
    monkeypatch.setattr(base, "DETECTION_DELAI_S", 0.5)
    monkeypatch.setenv("FAUX_CLI_FIGE", "1")
    t0 = time.time()
    det = fournisseurs_cli.CLIS["codex"].detecter(frais=True)
    assert time.time() - t0 < 3 and det.installe and det.version == "" and det.connecte is None


# --- hook : dossier de travail et clés de chemin (fiche 3)

class _Carte:
    """Remplace `confirm.request` : aucune carte chez l'utilisateur ; note ce qui aurait été demandé."""

    def __init__(self) -> None:
        self.vues: list = []

    def __call__(self, tool, desc, short, key, **kw):
        self.vues.append((tool, desc, short, kw.get("cwd")))
        return False


def test_hook_workdir_de_la_commande_juge(monkeypatch):
    h = _hook_module()
    carte = _Carte()
    monkeypatch.setattr(h.confirm, "request", carte)
    dossier = str(config.RUNTIME / "cli" / "codex")
    p = {"tool_name": "shell", "tool_input": {"command": ["cat", "auth.json"], "workdir": dossier},
         "cwd": str(config.WORKDIR)}
    assert h.decider(p, "codex", "outils")[0] == "deny"
    assert carte.vues and carte.vues[0][2].startswith("cd ")
    # Relatif : résolu depuis le `cwd` de la charge.
    p = {"tool_name": "shell", "tool_input": {"command": ["cat", "secrets.json"], "workdir": "config"},
         "cwd": str(config.ROOT)}
    assert h.decider(p, "codex", "outils")[0] == "deny"
    # Illisible (pas une chaîne) ou relatif sans `cwd` absolu : refus sans carte.
    n = len(carte.vues)
    for wd, cwd in ((["x"], str(config.ROOT)), ("config", ""), (42, "/tmp")):
        p = {"tool_name": "shell", "tool_input": {"command": "ls", "workdir": wd}, "cwd": cwd}
        assert h.decider(p, "codex", "outils")[0] == "deny", wd
    assert len(carte.vues) == n


def test_hook_recherche_cle_de_chemin_inconnue_ou_vide(monkeypatch):
    h = _hook_module()
    carte = _Carte()
    monkeypatch.setattr(h.confirm, "request", carte)
    cfg = str(config.ROOT / "config")
    for tool, inp in (("grep_search", {"query": "CLE_API", "search_dir": cfg}),
                      ("grep_search", {"query": "CLE_API"}),
                      ("grep_search", {"query": "CLE_API", "path": ""}),
                      ("list_dir", {"folder": str(config.RUNTIME / "cli")}),
                      ("Grep", {"pattern": "x", "directory_path": cfg}),
                      ("Glob", {"pattern": "*"}),
                      ("grep_search", {"query": "x", "path": ["a"]})):
        assert h.actions({"tool_name": tool, "tool_input": inp}) is None, (tool, inp)
        assert h.decider({"tool_name": tool, "tool_input": inp, "cwd": "/tmp"}, "codex", "outils")[0] == "deny"
    assert carte.vues == []  # refus direct, sans carte
    # Clé connue : le chemin est jugé (dossier des secrets = carte).
    p = {"tool_name": "grep_search", "tool_input": {"query": "CLE_API", "directory": cfg}, "cwd": "/tmp"}
    assert h.actions(p) == [("Grep", {"pattern": "CLE_API", "path": cfg})]
    assert h.decider(p, "codex", "outils")[0] == "deny" and carte.vues
    # Le `workdir` sert de chemin à une recherche qui n'en a pas d'autre.
    p = {"tool_name": "grep_search", "tool_input": {"query": "x", "workdir": "/tmp"}, "cwd": "/tmp"}
    assert h.decider(p, "codex", "outils")[0] == "allow"
    # Lecture sous un nom de Claude sans `file_path` : refus (le garde-fou ne verrait aucun chemin).
    assert h.actions({"tool_name": "Read", "tool_input": {"fichier": "/etc/shadow"}}) is None


def test_coeur_non_claude_avec_un_cli():
    j = SimpleNamespace(brain=SimpleNamespace(fournisseur=Effectif("cli", "codex", "defaut", outils=False)))
    assert core.Astrel._non_claude(j) is True
