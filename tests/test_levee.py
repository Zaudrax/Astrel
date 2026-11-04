"""Levée du garde-fou (18/10, plan P7) : classement des actions levables (`guard`),
qui peut lever ou rétablir (`totalgate`), état en mémoire, journal, ESTOP et
surveillance des fichiers (`core`, `levee`)."""
import importlib.util
import io
import json
import sys
import threading
import time

import pytest

from astrel import config, confirm, core, guard, levee, totalgate
from astrel.events import bus
from astrel.settings import reglages
from conftest import mode_prive
from test_lot_hud import make_astrel

R = config.ROOT


def verdict(tool, target_or_cmd, mode="autonome"):
    inp = {"command": target_or_cmd} if tool == "Bash" else {"file_path": str(target_or_cmd)}
    return guard.assess(tool, inp, mode=mode, cwd=str(R))


# ================================================================== guard : ce qui est levable

@pytest.mark.parametrize("path", [
    R / "hud" / "src" / "main.js", R / "hud" / "index.html", R / "hud" / "dist" / "app.js",
    R / "astrel" / "bubble.py", R / "astrel" / "minichat.py", R / "astrel" / "whatsapp.py",
    R / "whatsapp" / "bridge.mjs", R / "astrel" / "guard.py", R / "astrel" / "confirm.py",
    R / "hooks" / "permission_hook.py",
])
@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_ecriture_levable(tool, path):
    v = verdict(tool, path)
    assert v.needs_confirm and v.guard and not v.keys and v.lift, (tool, path)
    assert not totalgate.total_grants(True, v.guard, False)  # Total ne l'accorde toujours pas


@pytest.mark.parametrize("path", [
    R / "astrel" / "totalgate.py", R / "astrel" / "state.py", R / "astrel" / "server.py",
    R / "hooks" / "empreinte.json", R / "hooks" / "stop_hook.py", R / "runtime" / "settings.json",
    R / "runtime" / "autonomy.json", R / "runtime" / "grants.json", R / "runtime" / "ESTOP",
    R / "runtime" / "mission.json", R / "runtime" / "plans" / "P7.json", R / "runtime" / "hud-token",
    R / "logs" / "audit.jsonl", R / "logs" / "garde-fou-leve.log", R / "logs" / "autonome-total.log",
])
def test_reste_dur(path):
    v = verdict("Edit", path)
    assert v.needs_confirm and v.guard and not v.lift, path


def test_jamais_leve_en_prudent():
    v = verdict("Edit", R / "hud" / "src" / "main.js", mode="prudent")
    assert v.needs_confirm and v.guard and not v.lift


@pytest.mark.parametrize("cmd", [
    "cp /tmp/x.js hud/src/main.js",
    "sed -i 's/a/b/' hud/src/main.js",
    "echo 'x' > hud/dist/app.js",
    "python3 -c \"open('hud/src/x.js','w').write('1')\"",
    "cp -r /tmp/build hud/",
    "git checkout -- astrel/minichat.py",
])
def test_bash_levable(cmd):
    v = verdict("Bash", cmd)
    assert v.needs_confirm and v.guard and v.lift, cmd


@pytest.mark.parametrize("cmd, extra", [
    ("git push && cp /tmp/x.js hud/src/main.js", "push"),               # le reste n'est plus jeté
    ("cp /tmp/x.js hud/src/main.js && sed -i s/a/b/ astrel/state.py", "state.py"),
    ("cp /tmp/x.js hud/src/main.js; sudo ls", "administrateur"),
])
def test_commande_mixte_ni_levee_ni_cachee(cmd, extra):
    v = verdict("Bash", cmd)
    assert v.needs_confirm and v.guard and not v.lift, cmd
    assert "main.js" in v.desc and extra in v.desc, v.desc  # l'utilisateur voit TOUT ce qui est grave


@pytest.mark.parametrize("cmd", [
    "rm astrel/guard.py", "mv astrel/confirm.py /tmp/", "git checkout -- astrel/guard.py",  # 21/10 : suppression, jamais levée
    "rm hud/src/main.js", "rm -rf hud/dist",          # suppressions : jamais levées
    "cp /tmp/b astrel/",                              # dossier qui contient totalgate.py
    "./run.sh --send settings_action garde_fou_levee lever",
    "./run.sh --send 'settings_action garde_fou_levee retablir'",
    "echo x >> logs/audit.jsonl", "echo x >> logs/garde-fou-leve.log",
    "sed -i s/a/b/ astrel/totalgate.py",
])
def test_bash_reste_dur(cmd):
    v = verdict("Bash", cmd)
    assert v.needs_confirm and v.guard and not v.lift, cmd


@pytest.mark.parametrize("cmd", [
    "sed -i s/a/b/ astrel/guard.py",                                   # 21/10 : levable comme l'outil d'édition
    "cp /tmp/x astrel/confirm.py",
    "grep -n x astrel/guard.py | head; python3 - <<'X'\nprint(1)\nX",   # heredoc qui cite un fichier levable
    "echo $(wc -l astrel/guard.py)",
])
def test_bash_citant_fichier_levable_est_levable(cmd):
    v = verdict("Bash", cmd)
    assert v.needs_confirm and v.lift, cmd  # sans levée du HUD, le cœur demande toujours


def test_lectures_et_build_toujours_libres():
    for cmd in ("tail -3 logs/audit.jsonl", "cat logs/garde-fou-leve.log", "cd hud && npm run build",
                "grep -n lift astrel/guard.py"):
        assert not verdict("Bash", cmd).needs_confirm, cmd


def test_hors_levee_rien_ne_change_pour_la_commande_seule():
    # Une action levable seule : même question qu'avant (guard, pas de « 30 min »).
    v = verdict("Bash", "sed -i 's/a/b/' hud/src/main.js")
    assert v.desc == "une modification de main.js, fichier protégé d'Astrel" and v.keys == ()


# ================================================================== totalgate : qui a le droit

def test_qui_peut_lever_ou_retablir():
    assert totalgate.may_enable_lift({"_via": "hud", "_from_claude": False})
    for cmd in ({}, {"_via": "socket", "_from_claude": False}, {"_via": "hud", "_from_claude": True},
                {"_via": "hud"}, {"_via": "mcp", "_from_claude": False}, {"_via": "hud", "_from_claude": 0}):
        assert not totalgate.may_enable_lift(cmd), cmd
    assert totalgate.may_disable_lift({"_via": "socket", "_from_claude": False})  # terminal de l'utilisateur
    for cmd in ({}, {"_via": "hud", "_from_claude": True}, {"_via": "socket", "_from_claude": True}):
        assert not totalgate.may_disable_lift(cmd), cmd
    assert totalgate.lift_grants(True, True, False)
    assert not totalgate.lift_grants(True, True, True)    # ESTOP
    assert not totalgate.lift_grants(False, True, False)  # pas levé
    assert not totalgate.lift_grants(True, False, False)  # action non levable


# ================================================================== cœur

@pytest.fixture
def lj(monkeypatch, tmp_path):
    """Astrel nu, réglage branché, surveillance sur un seul fichier du test."""
    watched = tmp_path / "surveille.js"
    watched.write_text("a")
    monkeypatch.setattr(levee, "watched", lambda: [watched])
    j = make_astrel(monkeypatch, tmp_path)
    reglages.bind("garde_fou_levee", view=j._lift_view, run=j._lift_action)
    j.watched = watched
    yield j
    if j._lift:
        j.set_lift(False, "test")


def _action(action, **kw):
    cmd = {"cmd": "settings_action", "key": "garde_fou_levee", "action": action}
    if action == "lever":
        cmd["confirm"] = "lever"
    return {**cmd, **kw}


def test_eteinte_au_demarrage(lj, monkeypatch, tmp_path):
    assert lj._lift is False and make_astrel(monkeypatch, tmp_path)._lift is False


def test_levee_par_le_hud_seulement(lj):
    for refused in (_action("lever"),  # appel interne sans origine
                    _action("lever", _via="socket", _from_claude=False),  # terminal, MCP
                    _action("lever", _via="hud", _from_claude=True)):  # HUD ouvert par Claude
        r = lj.on_command(refused)
        assert not r["ok"] and lj._lift is False, refused
    r = lj.on_command(_action("lever", _via="hud", _from_claude=False, confirm="non"))
    assert not r["ok"] and lj._lift is False  # le mot « lever » est exigé
    assert lj.on_command(_action("lever", _via="hud", _from_claude=False))["ok"] and lj._lift
    ev = bus.last("levee")
    assert ev["on"] is True and ev["since"] > 0 and ev["changed"] == []


def test_claude_ne_retablit_pas(lj):
    lj.on_command(_action("lever", _via="hud", _from_claude=False))
    r = lj.on_command(_action("retablir", _via="socket", _from_claude=True))
    assert not r["ok"] and lj._lift
    assert lj.on_command(_action("retablir", _via="socket", _from_claude=False))["ok"] and not lj._lift
    assert bus.last("levee")["on"] is False


def test_accorde_seulement_le_levable_et_journalise(lj):
    lj.on_command(_action("lever", _via="hud", _from_claude=False))
    assert lj._ask_confirmation("Edit", "une modification de main.js", "hud/src/main.js", guard=True, lift=True)
    # Action non levable : demandée (ici le hook a raccroché : refus).
    assert lj._ask_confirmation("Bash", "un push", "git push", alive=lambda: False) is False
    assert lj._ask_confirmation("Edit", "x", "astrel/state.py", guard=True, lift=False,
                                alive=lambda: False) is False
    lines = config.LIFT_LOG.read_text().splitlines()
    assert lines[0].split("\t")[1:3] == ["LEVÉE", "activée"]
    assert lines[-1].split("\t")[1:3] == ["Edit", "hud/src/main.js"]
    assert mode_prive(config.LIFT_LOG)


def test_sans_levee_rien_n_est_accorde(lj):
    assert lj._ask_confirmation("Edit", "x", "hud/src/main.js", guard=True, lift=True, alive=lambda: False) is False


def test_estop_coupe_la_levee(lj):
    lj.on_command(_action("lever", _via="hud", _from_claude=False))
    lj._tasks, lj._tasks_lock = {}, threading.Lock()
    lj.brain = type("B", (), {"stop_task": lambda self, tid: True, "interrupt": lambda self: None})()
    lj._drop_backlog = lambda why: None
    lj.emergency_stop(True)
    assert lj._lift is False
    assert lj.on_command(_action("lever", _via="hud", _from_claude=False))["ok"] is False  # pas sous ESTOP
    assert lj._ask_confirmation("Edit", "x", "hud/src/main.js", guard=True, lift=True, alive=lambda: False) is False


def test_levee_fait_passer_la_question_affichee(lj):
    lj._usage = "silencieux"
    result = []
    t = threading.Thread(target=lambda: result.append(lj._ask_confirmation(
        "Edit", "une modification de main.js", "hud/src/main.js", guard=True, lift=True)))
    t.start()
    for _ in range(200):
        if lj._pending_confirm:
            break
        time.sleep(0.01)
    assert lj._pending_confirm is not None
    lj.on_command(_action("lever", _via="hud", _from_claude=False))
    t.join(2)
    assert result == [True]


def test_surveillance_et_bilan(lj):
    lj.on_command(_action("lever", _via="hud", _from_claude=False))
    lj.watched.write_text("b")
    lj._lift_watch.check()
    assert bus.last("levee")["changed"] == [str(lj.watched)]  # hors du dossier d'Astrel : chemin entier
    seen = []
    off = bus.subscribe(lambda ev: seen.append(ev) if ev.get("type") == "notice" else None)
    try:
        lj.on_command(_action("retablir", _via="hud", _from_claude=False))
    finally:
        off()
    text = " ".join(e.get("text", "") for e in seen)
    assert "doctor --accept" in text and "surveille.js" in text
    assert "coupée" in config.LIFT_LOG.read_text()


def test_rappel_discret(lj):
    lj.on_command(_action("lever", _via="hud", _from_claude=False))
    seen = []
    off = bus.subscribe(lambda ev: seen.append(ev))
    try:
        lj._lift_remind()
    finally:
        off()
    assert [e["type"] for e in seen] == ["notice"] and "levé depuis" in seen[0]["text"]


def test_watch_detecte_ajout_et_disparition(tmp_path, monkeypatch):
    a, b = tmp_path / "a.js", tmp_path / "b.js"
    a.write_text("1")
    files = [a]
    monkeypatch.setattr(levee, "watched", lambda: list(files))
    got = []
    w = levee.Watch(on_change=got.append, check_s=3600)
    w.start()
    try:
        assert w.check() == []
        b.write_text("2")
        files.append(b)
        a.unlink()
        assert len(w.check()) == 2 and got
    finally:
        assert len(w.stop()) == 2


def test_fichiers_surveilles_reels_sans_node_modules():
    files = levee.watched()
    assert R / "astrel" / "guard.py" in files and R / "astrel" / "totalgate.py" in files
    assert not any("node_modules" in p.parts for p in files)


# ================================================================== hook et canal de confirmation

def test_le_hook_transmet_lift(monkeypatch):
    spec = importlib.util.spec_from_file_location("permission_hook", R / "hooks" / "permission_hook.py")
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    sent = []
    monkeypatch.setattr(confirm, "request", lambda *a, **kw: sent.append((a, kw)) or True)
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(R / "hud" / "src" / "main.js")}, "cwd": str(R)}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert hook.main() == 0
    assert sent[0][1]["lift"] is True and sent[0][1]["guard"] is True


def test_socket_transmet_lift_et_ancien_hook_sur(monkeypatch, tmp_path):
    """04/11 (lot B, T43.5-A) : `lift` est RECALCULÉ par le cœur depuis l'entrée de l'outil ; ce que le
    client en dit est ignoré, et une demande à l'ancien format (sans entrée) est refusée sans carte."""
    monkeypatch.setattr(config, "CONFIRM_SOCKET", tmp_path / "confirm.sock")
    monkeypatch.setattr(guard, "MODE_FILE", tmp_path / "autonomy.json")
    monkeypatch.setattr(guard, "GRANTS_FILE", tmp_path / "grants.json")
    calls = []
    srv = confirm.ConfirmServer(lambda *a, **kw: calls.append(kw) or True)
    srv.start()
    edit = {"file_path": str(R / "hud" / "src" / "main.js")}
    try:
        # le client dit « non levable » : le cœur recalcule, c'est levable (écriture de hud/src)
        assert confirm.request("Edit", "d", "hud/src/main.js", guard=True, lift=False, tool_input=edit) is True
        assert confirm.request("Edit", "d", "hud/src/main.js", guard=True, lift=True) is False  # ancien format
        # le client dit « levable » pour un push : le cœur recalcule, non
        assert confirm.request("Bash", "d", "git push", guard=True, lift=True,
                               tool_input={"command": "git push"}) is True
    finally:
        srv.stop()
    assert [c["lift"] for c in calls] == [True, False]
    assert calls[0]["guard"] is True and calls[1]["guard"] is False


def test_reglage_jamais_ecrit_dans_le_fichier(lj):
    lj.on_command(_action("lever", _via="hud", _from_claude=False))
    assert not config.ASTREL_SETTINGS_FILE.exists()
    item = next(i for g in reglages.describe()["groups"] for i in g["items"] if i["key"] == "garde_fou_levee")
    assert [a["id"] for a in item["actions"]] == ["retablir"]
