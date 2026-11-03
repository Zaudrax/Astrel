"""Durcissement du garde-fou, partie A (15/10, docs/DURCISSEMENT-GARDE-FOU.md) :
jeton du HUD hors de `ps`, origine des commandes (cgroup, pid introuvable, port
du HUD), « oui pour 30 min » par étiquette, délais et demandes abandonnées,
`cwd` du hook, mode « Autonome TOTAL », faux positif « lecture du jeton »."""
import chemins_machine  # chemins de la machine qui lance la suite
import asyncio
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time

import aiohttp
import pytest

from astrel import config, confirm, core, guard, server, state
from astrel.events import bus
from astrel.minichat import MiniChat
from astrel.platform import proc

from test_lot_hud import make_astrel
from conftest import mode_prive


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "MODE_FILE", tmp_path / "autonomy.json")
    monkeypatch.setattr(guard, "GRANTS_FILE", tmp_path / "grants.json")


def run(coro):
    return asyncio.run(coro)


# ================================================================== 1. jeton du HUD hors de `ps`

@pytest.fixture(scope="module")
def hud(tmp_path_factory):
    old_port, old_file = config.HUD_PORT, config.HUD_TOKEN_FILE
    config.HUD_PORT = 47191
    config.HUD_TOKEN_FILE = tmp_path_factory.mktemp("hud2") / "hud-token"
    claude = {"pid": None}
    srv = server.HudServer(claude_pid=lambda: claude["pid"])
    received = []
    bus.set_command_handler(lambda cmd: received.append(cmd) or {"ok": True})
    srv.start()
    yield srv, received, claude
    srv.stop()
    bus.set_command_handler(None)
    config.HUD_PORT, config.HUD_TOKEN_FILE = old_port, old_file


BASE = "http://127.0.0.1:47191"
ORIGIN = {"Origin": BASE}


def test_nouveau_jeton_a_chaque_demarrage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HUD_TOKEN_FILE", tmp_path / "hud-token")
    a = server._new_token()
    b = server._new_token()
    assert a != b and server.read_token() == b
    assert mode_prive((tmp_path / "hud-token"), 0o600)


def test_ticket_a_usage_unique_puis_cookie(hud):
    srv, received, _ = hud

    async def go():
        jar = aiohttp.CookieJar(unsafe=True)
        async with aiohttp.ClientSession(cookie_jar=jar) as s:
            async with s.post(f"{BASE}/api/ticket", headers={"X-Astrel-Token": "faux"}) as r:
                refused = r.status
            async with s.post(f"{BASE}/api/ticket", headers={"X-Astrel-Token": srv.token}) as r:
                ticket = (await r.json())["ticket"]
            async with s.get(f"{BASE}/login?k={ticket}", allow_redirects=False) as r:
                status, cookie = r.status, r.headers.get("Set-Cookie", "")
            async with s.get(f"{BASE}/login?k={ticket}", allow_redirects=False) as r:
                second = r.status  # déjà servi
            async with s.get(f"{BASE}/api/session") as r:
                session = r.status
            # Le WebSocket passe avec le seul cookie, sans jeton.
            async with s.ws_connect(f"{BASE}/ws", headers=ORIGIN) as ws:
                hello = json.loads((await ws.receive()).data)
                await ws.send_json({"id": 7, "cmd": "wake", "_from_claude": True, "_via": "x"})
                while json.loads((await ws.receive()).data).get("type") != "ack":
                    pass
        async with aiohttp.ClientSession() as s:  # sans cookie ni jeton
            async with s.get(f"{BASE}/api/session") as r:
                anonymous = r.status
            try:
                async with s.ws_connect(f"{BASE}/ws", headers=ORIGIN):
                    ws_anon = "connecté"
            except aiohttp.WSServerHandshakeError as exc:
                ws_anon = exc.status
        return refused, status, cookie, second, session, hello, anonymous, ws_anon

    refused, status, cookie, second, session, hello, anonymous, ws_anon = run(go())
    assert refused == 401
    assert status == 303 and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert srv.token not in cookie  # le cookie n'est pas le jeton
    assert second == 403 and session == 200 and hello["type"] == "hello"
    assert anonymous == 401 and ws_anon == 401
    got = [c for c in received if c.get("id") == 7][-1]
    # L'origine est établie par le serveur, pas déclarée par le client.
    assert got["_via"] == "hud" and got["_from_claude"] is False


def test_ticket_perime_refuse(hud, monkeypatch):
    srv, _, _ = hud
    monkeypatch.setattr(server, "TICKET_S", -1.0)
    ticket = srv.issue_ticket()

    async def go():
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{BASE}/login?k={ticket}", allow_redirects=False) as r:
                return r.status

    assert run(go()) == 403


def test_hud_ouvert_sans_jeton_dans_la_ligne_de_commande(monkeypatch, tmp_path):
    from astrel import cli

    token_file = tmp_path / "hud-token"
    token_file.write_text("JETON-SECRET-0123456789-0123456789-abcdef")
    monkeypatch.setattr(config, "HUD_TOKEN_FILE", token_file)
    monkeypatch.setattr(server, "request_ticket", lambda token=None, timeout=5.0: "TICKET42")
    launched = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: launched.append(args))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    assert cli.open_hud() == 0
    line = " ".join(launched[0])
    assert "JETON-SECRET" not in line and "#t=" not in line
    assert "/login?k=TICKET42" in line


def test_ticket_demande_par_claude_donne_une_session_claude(hud):
    """`./hud.sh` lancé par Claude : la session ouverte reste marquée « Claude »,
    même si le cookie est ensuite utilisé depuis un autre process."""
    srv, received, claude = hud
    claude["pid"] = os.getpid()  # le test joue Claude : ses connexions viennent de Claude
    try:
        async def ticket():
            async with aiohttp.ClientSession() as s:
                async with s.post(f"{BASE}/api/ticket", headers={"X-Astrel-Token": srv.token}) as r:
                    return (await r.json())["ticket"]

        t = run(ticket())
    finally:
        claude["pid"] = None
    assert srv._tickets[t][1] is True

    async def go():
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as s:
            await (await s.get(f"{BASE}/login?k={t}", allow_redirects=False)).release()
            async with s.post(f"{BASE}/api/command", json={"cmd": "total", "mode": "on"}, headers=ORIGIN) as r:
                return r.status

    assert run(go()) == 200
    assert received[-1]["cmd"] == "total" and received[-1]["_from_claude"] is True


def test_connexion_au_hud_depuis_claude_marquee(hud):
    srv, received, claude = hud
    claude["pid"] = os.getpid()
    try:
        async def go():
            async with aiohttp.ClientSession() as s:
                async with s.ws_connect(f"{BASE}/ws?t={srv.token}", headers=ORIGIN) as ws:
                    await ws.receive()
                    await ws.send_json({"id": 9, "cmd": "total", "mode": "on"})
                    while json.loads((await ws.receive()).data).get("type") != "ack":
                        pass

        run(go())
    finally:
        claude["pid"] = None
    got = [c for c in received if c.get("id") == 9][-1]
    assert got["_from_claude"] is True and got["_via"] == "hud"


# ================================================================== 2. « vient de Claude »

def test_pid_introuvable_suspect():
    assert state.from_claude(0, None, service_cg="")
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    assert state.from_claude(dead.pid, None, service_cg="")


def test_le_coeur_lui_meme_n_est_pas_claude():
    assert not state.from_claude(os.getpid(), None, service_cg=state.cgroup_of(os.getpid()))


def test_process_detache_reconnu_par_son_cgroup():
    """Double fork + setsid : le process ne DESCEND plus du cœur (contournement
    du 14/10), mais il reste dans le cgroup du service."""
    if not state.cgroup_of(os.getpid()):
        pytest.skip("cgroup v2 illisible ici")
    code = ("import os, subprocess, sys;"
            "p = subprocess.Popen(['sleep', '5'], start_new_session=True,"
            " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); print(p.pid)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    orphan = int(out.strip())
    try:
        time.sleep(0.1)
        assert not state.descends_from(orphan, os.getpid())  # l'ancien test était contourné
        assert state.from_claude(orphan, os.getpid(), service_cg=state.cgroup_of(os.getpid()))
        assert not state.from_claude(orphan, os.getpid(), service_cg="/user.slice/autre.service")
        assert not state.from_claude(orphan, os.getpid(), service_cg="")  # lancé hors service
    finally:
        try:
            os.kill(orphan, 9)
        except OSError:
            pass


def test_service_reconnu_seulement_sous_son_nom(monkeypatch):
    monkeypatch.setattr(state, "cgroup_of", lambda pid: "/user.slice/app.slice/astrel.service")
    assert state.service_cgroup() == "/user.slice/app.slice/astrel.service"
    monkeypatch.setattr(state, "cgroup_of", lambda pid: "/user.slice/app.slice/vte-spawn-1.scope")
    assert state.service_cgroup() == ""  # lancé depuis un terminal : le shell de l'utilisateur n'est pas Claude


def test_process_d_une_connexion_tcp_retrouve():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(2)
    port = srv.getsockname()[1]
    cli = socket.create_connection(("127.0.0.1", port))
    conn, peer = srv.accept()
    try:
        assert state.tcp_peer_pid(peer[1], port) == os.getpid()
        child = subprocess.Popen([sys.executable, "-c",
                                  f"import socket, time; s = socket.create_connection(('127.0.0.1', {port}));"
                                  "print(s.getsockname()[1], flush=True); time.sleep(5)"],
                                 stdout=subprocess.PIPE, text=True)
        try:
            child_port = int(child.stdout.readline())
            # 03/11 (CI Windows) : le python.exe d'une venv Windows est un lanceur, le vrai interpréteur (qui tient
            # la connexion) est son enfant ; il descend toujours du process lancé (ce que `from_claude` regarde).
            assert proc.descends_from(state.tcp_peer_pid(child_port, port), child.pid)
        finally:
            child.kill()
        assert state.tcp_peer_pid(1, port) == 0  # aucune connexion : introuvable
    finally:
        cli.close()
        conn.close()
        srv.close()


def test_canal_de_controle_ecrase_l_origine_declaree(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONTROL_SOCKET", tmp_path / "control.sock")
    got = []
    bus.set_command_handler(lambda cmd: got.append(cmd) or {"ok": True})
    srv = state.ControlServer(claude_pid=lambda: None)
    srv.start()
    try:
        state.send_command(json.dumps({"cmd": "total", "mode": "on", "_via": "hud", "_from_claude": False}))
    finally:
        srv.stop()
        bus.set_command_handler(None)
    assert got[0]["_via"] == "socket"


@pytest.mark.parametrize("cmd", [
    "systemd-run --user --scope bash -c 'rm runtime/x'",
    "echo 'python3 x.py' | at now + 1 minute",
    "at -f /tmp/s.sh 10:00",
    "crontab -e",
    "crontab /tmp/tab",
    "bash -c \"systemd-run sleep 1\"",
    "busctl --user call org.freedesktop.systemd1 /org/freedesktop/systemd1 "
    "org.freedesktop.systemd1.Manager StartTransientUnit x",
])
def test_planificateurs_critiques(cmd):
    v = guard.assess("Bash", {"command": cmd}, mode="autonome")
    assert v.needs_confirm and v.level == guard.CRITICAL, cmd


@pytest.mark.parametrize("cmd", ["crontab -l", "crontab -l | grep backup", "grep -rn systemd-run astrel/",
                                 "echo rendez-vous at noon"])
def test_planificateurs_faux_amis(cmd):
    assert not guard.assess("Bash", {"command": cmd}, mode="autonome").needs_confirm, cmd


# ================================================================== 3. « oui pour 30 min » par étiquette

def test_trente_minutes_par_etiquette():
    """Avant : « oui pour 30 min » sur un push couvrait `git push && sudo …`."""
    cmd = {"command": "git push origin main && sudo apt install htop"}
    v = guard.assess("Bash", cmd, mode="autonome")
    assert v.needs_confirm and set(v.keys) == {"bash:une commande administrateur", "bash:une publication git (push)"}
    assert "push" in v.desc and "administrateur" in v.desc
    guard.grant("bash:une publication git (push)")
    assert not guard.assess("Bash", {"command": "git push origin main"}, mode="autonome").needs_confirm
    assert guard.assess("Bash", cmd, mode="autonome").needs_confirm  # sudo non couvert
    guard.grant("bash:une commande administrateur")
    assert not guard.assess("Bash", cmd, mode="autonome").needs_confirm


def test_en_prudent_les_etiquettes_sensibles_comptent_aussi():
    cmd = {"command": "git commit -m x && git push"}
    v = guard.assess("Bash", cmd, mode="prudent")
    assert set(v.keys) == {"bash:une opération git", "bash:une publication git (push)"}
    assert guard.assess("Bash", cmd, mode="autonome").keys == ("bash:une publication git (push)",)


def test_garde_fou_jamais_autorisable():
    v = guard.assess("Bash", {"command": "git push && rm runtime/ESTOP"}, mode="autonome")
    assert v.needs_confirm and v.keys == () and v.guard


def test_le_coeur_accorde_toutes_les_etiquettes(monkeypatch, tmp_path):
    j = make_astrel(monkeypatch, tmp_path)
    j._usage = "silencieux"
    keys = ["bash:une publication git (push)", "bash:une commande administrateur"]
    result = []
    t = threading.Thread(target=lambda: result.append(j._ask_confirmation(
        "Bash", "x", "git push && sudo x", key=keys[0], keys=keys)))
    t.start()
    for _ in range(200):
        if j._pending_confirm:
            break
        time.sleep(0.01)
    j._answer_confirmation(j._pending_confirm["id"], True, always=True)
    t.join(2)
    assert result == [True] and all(guard.granted(k) for k in keys)
    # Une seule clé déjà couverte ne suffit pas : la question revient.
    guard.revoke(keys[1])
    t = threading.Thread(target=lambda: result.append(j._ask_confirmation("Bash", "x", "y", keys=keys)))
    t.start()
    for _ in range(200):
        if j._pending_confirm:
            break
        time.sleep(0.01)
    assert j._pending_confirm is not None
    j._answer_confirmation(j._pending_confirm["id"], False)
    t.join(2)
    assert result == [True, False]


# ================================================================== 7. délais et demandes abandonnées

def test_demande_perimee_non_posee(monkeypatch, tmp_path):
    j = make_astrel(monkeypatch, tmp_path)
    seen = []
    off = bus.subscribe(lambda ev: ev.get("type") == "confirm" and seen.append(ev))
    try:
        old = time.time() - config.CONFIRM_TIMEOUT_S - 1  # arrivée il y a plus que le délai
        assert j._ask_confirmation("Bash", "un push", "git push", received_at=old) is False
        assert j._ask_confirmation("Bash", "un push", "git push", alive=lambda: False) is False
    finally:
        off()
    assert seen == [] and j._pending_confirm is None  # rien n'est apparu à l'écran


def test_question_retiree_quand_le_hook_raccroche(monkeypatch, tmp_path):
    j = make_astrel(monkeypatch, tmp_path)
    j._usage = "silencieux"
    hook = {"alive": True}
    seen = []
    off = bus.subscribe(lambda ev: ev.get("type") == "confirm" and seen.append(ev))
    result = []
    t = threading.Thread(target=lambda: result.append(
        j._ask_confirmation("Bash", "un push", "git push", alive=lambda: hook["alive"])))
    try:
        t.start()
        for _ in range(200):
            if j._pending_confirm:
                break
            time.sleep(0.01)
        hook["alive"] = False
        t.join(3)
    finally:
        off()
    assert result == [False]
    assert seen[-1]["pending"] is False and seen[-1]["by"] == "abandon"
    assert j.speaker.said == []  # pas de « j'annule » pour une question que personne n'attend plus


def test_le_delai_compte_depuis_l_arrivee(monkeypatch, tmp_path):
    j = make_astrel(monkeypatch, tmp_path)
    j._usage = "silencieux"
    seen = []
    off = bus.subscribe(lambda ev: ev.get("type") == "confirm" and ev.get("pending") and seen.append(ev))
    arrived = time.time() - 100
    t = threading.Thread(target=lambda: j._ask_confirmation("Bash", "x", "y", received_at=arrived))
    try:
        t.start()
        for _ in range(200):
            if j._pending_confirm:
                break
            time.sleep(0.01)
        j._answer_confirmation(j._pending_confirm["id"], False)
        t.join(2)
    finally:
        off()
    assert abs(seen[0]["expires"] - (arrived + config.CONFIRM_TIMEOUT_S)) < 0.01


def test_socket_de_confirmation_transmet_tout(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIRM_SOCKET", tmp_path / "confirm.sock")
    calls = []

    def ask(tool, desc, cmd, key, **kw):
        calls.append((tool, key, kw))
        return True

    srv = confirm.ConfirmServer(ask)
    srv.start()
    try:
        t0 = time.time()
        assert confirm.request("Bash", "d", "git push", "k1", keys=["k1", "k2"], guard=True, cwd="/tmp/x") is True
    finally:
        srv.stop()
    tool, key, kw = calls[0]
    assert kw["keys"] == ["k1", "k2"] and kw["guard"] is True and kw["cwd"] == "/tmp/x"
    assert t0 - 1 <= kw["received_at"] <= time.time() and callable(kw["alive"])


def test_hook_parti_detecte():
    a, b = socket.socketpair()
    assert confirm.peer_alive(a)
    b.close()
    assert not confirm.peer_alive(a)
    a.close()


# ================================================================== 8. cwd du hook

def test_chemins_relatifs_juges_depuis_le_cwd_de_claude():
    assert guard.assess("Write", {"file_path": "hosts"}, mode="autonome", cwd="/etc").needs_confirm
    assert not guard.assess("Write", {"file_path": "hosts"}, mode="autonome",
                            cwd=str(config.WORKDIR)).needs_confirm
    assert guard.assess("Bash", {"command": "echo x > passwd"}, mode="autonome", cwd="/etc").needs_confirm
    root = str(config.ROOT)
    assert guard.assess("Edit", {"file_path": "astrel/guard.py"}, mode="autonome", cwd=root).guard


def test_le_hook_transmet_le_cwd(monkeypatch):
    spec = importlib.util.spec_from_file_location("permission_hook", config.ROOT / "hooks" / "permission_hook.py")
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    sent = []
    monkeypatch.setattr(confirm, "request", lambda *a, **kw: sent.append((a, kw)) or False)
    payload = {"tool_name": "Write", "tool_input": {"file_path": "hosts"}, "cwd": "/etc"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    assert hook.main() == 0
    assert sent and sent[0][1]["cwd"] == "/etc"  # demandé : /etc/hosts, pas un fichier du projet
    assert json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"] == "deny"


# ================================================================== Autonome TOTAL

def _hud_cmd(**kw):
    return {"cmd": "total", "_via": "hud", "_from_claude": False, **kw}


def test_total_active_par_le_hud_seulement(monkeypatch, tmp_path):
    j = make_astrel(monkeypatch, tmp_path)
    for refused in ({"cmd": "total", "mode": "on"},  # appel interne sans origine
                    {"cmd": "total", "mode": "on", "_via": "socket", "_from_claude": False},  # terminal, MCP
                    _hud_cmd(mode="on", _from_claude=True)):  # HUD ouvert par Claude
        r = j.on_command(refused)
        assert not r["ok"] and not j._total, refused
    assert j.on_command(_hud_cmd(mode="on"))["total"] and j._total
    # Couper : toujours permis, même à Claude.
    assert j.on_command({"cmd": "total", "mode": "off", "_via": "socket", "_from_claude": True})["ok"]
    assert not j._total


def test_total_en_memoire_seulement(monkeypatch, tmp_path):
    j = make_astrel(monkeypatch, tmp_path)
    written = []
    real_open, real_write = os.open, type(tmp_path).write_text
    monkeypatch.setattr(os, "open", lambda path, *a, **kw: written.append(str(path)) or real_open(path, *a, **kw))
    monkeypatch.setattr(type(tmp_path), "write_text",
                        lambda self, *a, **kw: written.append(str(self)) or real_write(self, *a, **kw))
    j.on_command(_hud_cmd(mode="on"))
    assert j._total
    assert written == [str(config.TOTAL_LOG)]  # seul le journal est écrit, jamais l'état
    assert not guard.MODE_FILE.exists()
    assert make_astrel(monkeypatch, tmp_path)._total is False  # « relance » : retombe à normal


def test_total_accorde_sans_question_et_journalise(monkeypatch, tmp_path):
    j = make_astrel(monkeypatch, tmp_path)
    j.on_command(_hud_cmd(mode="on"))
    long_cmd = "git push origin main " + "x" * 500
    assert j._ask_confirmation("Bash", "une publication git (push)", long_cmd, key="bash:push")
    assert j._pending_confirm is None
    lines = config.TOTAL_LOG.read_text().splitlines()
    assert lines[0].split("\t")[1:3] == ["MODE", "activé"]
    date, tool, cmd = lines[-1].split("\t")[:3]
    assert tool == "Bash" and cmd.startswith("git push") and len(cmd) <= 200
    assert mode_prive(config.TOTAL_LOG, 0o600)


def test_total_n_ouvre_pas_le_garde_fou(monkeypatch, tmp_path):
    j = make_astrel(monkeypatch, tmp_path)
    j._usage = "silencieux"
    j.on_command(_hud_cmd(mode="on"))
    result = []
    t = threading.Thread(target=lambda: result.append(j._ask_confirmation(
        "Bash", "une action sur le garde-fou d'Astrel", "rm runtime/ESTOP", guard=True)))
    t.start()
    for _ in range(200):
        if j._pending_confirm:
            break
        time.sleep(0.01)
    assert j._pending_confirm is not None  # la question est posée malgré Total
    j._answer_confirmation(j._pending_confirm["id"], False)
    t.join(2)
    assert result == [False]


def test_total_suspendu_pendant_l_arret_d_urgence(monkeypatch, tmp_path):
    j = make_astrel(monkeypatch, tmp_path)
    j._total = True
    config.ESTOP_FILE.write_text("x")
    assert j._ask_confirmation("Bash", "x", "y", alive=lambda: False) is False


def test_stop_tout_coupe_le_total(monkeypatch, tmp_path):
    j = make_astrel(monkeypatch, tmp_path)
    j.on_command(_hud_cmd(mode="on"))
    j._tasks, j._tasks_lock = {}, threading.Lock()
    j.brain = type("B", (), {"stop_task": lambda self, tid: True, "interrupt": lambda self: None})()
    j._drop_backlog = lambda why: None
    assert not core.mission.active  # mission isolée par conftest
    j.emergency_stop(True)
    assert not j._total


def test_total_fait_passer_la_question_affichee(monkeypatch, tmp_path):
    j = make_astrel(monkeypatch, tmp_path)
    j._usage = "silencieux"
    result = []
    t = threading.Thread(target=lambda: result.append(j._ask_confirmation("Bash", "un push", "git push")))
    t.start()
    for _ in range(200):
        if j._pending_confirm:
            break
        time.sleep(0.01)
    j.on_command(_hud_cmd(mode="on"))
    t.join(2)
    assert result == [True]


@pytest.mark.parametrize("text, want", [
    ("Mode autonome total", "on"),
    ("Astrel, passe en autonomie totale", "on"),
    ("Astrel, passe en autonomie totale", "on"),  # alias « Hey Jarvis »
    ("mode total", "on"),
    ("Fin du mode total", "off"),
    ("désactive le mode autonome total", "off"),
    ("Explique-moi le mode total de mon appareil photo", ""),
    ("le total de la facture", ""),
])
def test_total_a_la_voix(text, want):
    assert core.total_phrase(text) == want


def test_claude_ne_peut_pas_activer_le_total_par_bash():
    for cmd in ('./run.sh --send "total on"', "echo '{\"cmd\": \"total\", \"mode\": \"on\"}' | socat - UNIX-CONNECT:x"):
        v = guard.assess("Bash", {"command": cmd}, mode="autonome")
        assert v.needs_confirm and v.guard, cmd
    log = str(config.ROOT / "logs" / "autonome-total.log")
    assert guard.assess("Write", {"file_path": log}, mode="autonome").guard
    assert guard.assess("Bash", {"command": f"echo x > {log}"}, mode="autonome").guard
    assert not guard.assess("Bash", {"command": "tail -20 logs/autonome-total.log"}, mode="autonome").needs_confirm


def test_indicateur_total_de_la_bulle():
    chat = MiniChat()
    assert chat.apply({"type": "total", "on": True}) and chat.total
    chat.apply({"type": "total", "on": False})
    assert not chat.total


# ================================================================== faux positif « lecture du jeton »

@pytest.mark.parametrize("cmd", [
    'grep -n "hook\\|timeout" astrel/brain.py | head -40; sed -n 180,240p astrel/cli.py; '
    'grep -rn "hud_url\\|read_token\\|hud-token\\|HUD_TOKEN" astrel tools',
    "grep -rn hud-token astrel/",
    "sed -n '/hud-token/p' astrel/cli.py",
    "rg -e 'hud-token' docs",
])
def test_motif_de_recherche_n_est_pas_une_lecture_du_jeton(cmd):
    assert not guard.assess("Bash", {"command": cmd}, mode="autonome").needs_confirm, cmd


@pytest.mark.parametrize("cmd", [
    "cat runtime/hud-token",
    "grep . runtime/hud-token",
    "grep -f runtime/hud-token astrel/cli.py",
    "grep -e x runtime/hud-token",
    f"head -c 64 {chemins_machine.RACINE}/runtime/hud-token",
    "python3 -c 'print(open(\"runtime/hud-token\").read())'",
    "curl -H \"X-Astrel-Token: $(cat runtime/hud-token)\" http://127.0.0.1:4719/api/ticket",
])
def test_lecture_du_jeton_toujours_demandee(cmd):
    v = guard.assess("Bash", {"command": cmd}, mode="autonome")
    assert v.needs_confirm and v.guard, cmd


# ================================================================== code qui décide de Total (15/10)
# Relecteur : en autonome (donc en Total), Claude pouvait réécrire core.py,
# state.py, server.py, le HUD ou la bulle, puis relancer Astrel et se donner
# Total. Décision de l'utilisateur : protéger le module de décision (`totalgate`) et
# ces fichiers, PAS core.py (développement courant).

from astrel import totalgate  # noqa: E402

R = config.ROOT


@pytest.mark.parametrize("path", [
    R / "astrel" / "totalgate.py", R / "astrel" / "state.py", R / "astrel" / "server.py",
    R / "astrel" / "bubble.py", R / "astrel" / "minichat.py",
    R / "hud" / "src" / "main.js", R / "hud" / "src" / "conn.js", R / "hud" / "dist" / "app.js",
    R / "hud" / "dist" / "app.js.map", R / "hud" / "index.html",
])
@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
def test_code_de_total_protege_en_ecriture(tool, path):
    v = guard.assess(tool, {"file_path": str(path)}, mode="autonome")
    assert v.needs_confirm and v.guard and not v.keys, (tool, path)  # pas de « 30 min » possible
    assert not totalgate.total_grants(True, v.guard, False)  # Total ne l'accorde pas d'office


def test_code_de_total_chemin_relatif_resolu_depuis_le_cwd():
    v = guard.assess("Edit", {"file_path": "state.py"}, mode="autonome", cwd=str(R / "astrel"))
    assert v.needs_confirm and v.guard
    assert not guard.assess("Edit", {"file_path": "state.py"}, mode="autonome", cwd="/tmp/autre-projet").needs_confirm


@pytest.mark.parametrize("path", [
    R / "astrel" / "core.py", R / "astrel" / "brain.py", R / "tests" / "test_guard.py", R / "run.sh",
    R / "astrel" / "channels.py", R / "hud" / "styles.css", R / "docs" / "DURCISSEMENT-GARDE-FOU.md",
])
def test_core_et_le_reste_libres_en_autonome(path):
    assert not guard.assess("Edit", {"file_path": str(path)}, mode="autonome").needs_confirm, path


@pytest.mark.parametrize("cmd", [
    "sed -i 's/a/b/' astrel/state.py",
    "cd astrel && sed -i 's/a/b/' server.py",
    "pushd hud/src && perl -pi -e 's/x/y/' main.js",
    "echo 'x' > hud/dist/app.js",
    "cat /tmp/x.js >> hud/src/main.js",
    "cp /tmp/autre.html hud/index.html",
    "tee astrel/bubble.py < /tmp/b.py",
    "git checkout -- astrel/minichat.py",
    f"python3 - <<EOF\nopen('{R}/astrel/state.py', 'w').write('x')\nEOF",
    "sed -i 's/False/True/' totalgate.py",  # nom exact, d'où qu'il soit lancé
    "npx esbuild src/main.js --bundle --outfile=dist/app.js",  # hors `npm run build` : écrit dans dist
])
def test_code_de_total_protege_par_bash(cmd):
    cwd = str(R / "hud") if "esbuild" in cmd else str(R)
    v = guard.assess("Bash", {"command": cmd}, mode="autonome", cwd=cwd)
    assert v.needs_confirm and v.guard, cmd


@pytest.mark.parametrize("cmd, cwd", [
    ("cd hud && npm run build", R),                       # dist régénéré : pas de question en boucle
    ("npm run build 2>&1 | tail -5", R / "hud"),
    ("sed -n 1,40p astrel/state.py", R),                   # lecture libre
    ("grep -n from_claude astrel/state.py astrel/server.py", R),
    ("cat hud/dist/app.js | head -c 200", R),
    ("sed -i 's/a/b/' astrel/core.py", R),                 # core.py reste libre
    (".venv/bin/python -m pytest -q tests/test_server.py", R),
    ("sed -i 's/a/b/' server.py state.py", "/tmp/autre-projet"),  # homonymes d'un autre projet
])
def test_build_lecture_et_core_libres_par_bash(cmd, cwd):
    assert not guard.assess("Bash", {"command": cmd}, mode="autonome", cwd=str(cwd)).needs_confirm, cmd


@pytest.mark.parametrize("answer, decision", [(True, "allow"), (False, "deny")])
def test_modification_confirmee_reste_possible(monkeypatch, answer, decision):
    """l'utilisateur peut toujours faire modifier ces fichiers : la question est posée
    (guard, donc même en Total) et son « oui » laisse passer."""
    spec = importlib.util.spec_from_file_location("permission_hook", R / "hooks" / "permission_hook.py")
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    sent = []
    monkeypatch.setattr(confirm, "request", lambda *a, **kw: sent.append((a, kw)) or answer)
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(R / "astrel" / "server.py")}, "cwd": str(R)}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    assert hook.main() == 0
    assert sent and sent[0][1]["guard"] is True and "server.py" in sent[0][0][1]
    assert json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"] == decision


def test_totalgate_decisions():
    assert totalgate.may_enable_total({"_via": "hud", "_from_claude": False})
    for cmd in ({}, {"_via": "socket", "_from_claude": False}, {"_via": "hud", "_from_claude": True},
                {"_via": "hud"}, {"_via": "hud", "_from_claude": 0}):
        assert not totalgate.may_enable_total(cmd), cmd
    assert totalgate.total_request({"on": True}) == "on" and totalgate.total_request({"mode": "off"}) == "off"
    assert totalgate.total_request({}) == "" and totalgate.total_request({"text": "peut-être"}) == "?"
    assert totalgate.refused_to_claude("total", {"mode": "on", "_from_claude": True})
    assert totalgate.refused_to_claude("estop", {"mode": "off", "_from_claude": True})
    assert totalgate.refused_to_claude("confirm", {"allow": True, "_from_claude": True})
    assert not totalgate.refused_to_claude("total", {"mode": "off", "_from_claude": True})  # resserrer : permis
    assert not totalgate.refused_to_claude("estop", {"_from_claude": True})  # poser l'arrêt : permis
    assert totalgate.estop_wanted({}) and not totalgate.estop_wanted({"text": "lever"})
    assert not totalgate.total_grants(True, False, True) and totalgate.total_grants(True, False, False)


def test_core_delegue_a_totalgate():
    assert core.total_phrase is totalgate.total_phrase
    assert core.Astrel._loosens_guard is totalgate.loosens_guard
