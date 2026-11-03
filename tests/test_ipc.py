"""Canal local (`astrel/ipc.py`) et examen des process (`astrel/platform/proc.py`),
plan P6 lot A. Tourne sur Linux (CI comprise) : le mode TCP de Windows est forcé
(`ASTREL_IPC=tcp`) et la voie psutil de macOS/Windows est prise en remplaçant
`SYSTEM` ; aucun de ces tests ne prouve le comportement sur une vraie machine
Mac ou Windows (non testé sur vraie machine)."""
from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
import threading
import tempfile
import time

import psutil
from pathlib import Path

import pytest

from astrel import config, confirm, ipc, state
from astrel.events import bus
from astrel.platform import proc

POSIX_SOCKETS = hasattr(socket, "AF_UNIX") and sys.platform != "win32"


@pytest.fixture
def tcp(monkeypatch):
    """Mode TCP forcé : celui de Windows, sur la machine de test."""
    monkeypatch.setenv("ASTREL_IPC", "tcp")


@pytest.fixture
def faux_systeme(monkeypatch):
    """Remplace le système vu par `proc` (voie psutil) et `ipc`."""
    def poser(nom: str) -> None:
        monkeypatch.setattr(proc, "SYSTEM", nom)
        monkeypatch.setattr(ipc, "SYSTEM", nom)
    return poser


def _enfant(env: dict | None = None) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], env=env)


def _pid_mort() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


# ------------------------------------------------------------------ mode

def test_mode(monkeypatch, faux_systeme):
    monkeypatch.setenv("ASTREL_IPC", "tcp")
    assert ipc.mode() == "tcp"
    if POSIX_SOCKETS:
        monkeypatch.setenv("ASTREL_IPC", "unix")
        assert ipc.mode() == "unix"
    monkeypatch.delenv("ASTREL_IPC")
    faux_systeme("windows")
    assert ipc.mode() == "tcp"
    if POSIX_SOCKETS:
        faux_systeme("macos")
        assert ipc.mode() == "unix"


# ------------------------------------------------------------------ mode Unix

@pytest.mark.skipif(not POSIX_SOCKETS, reason="socket Unix absente (Windows)")
def test_unix_socket_privee_et_pid_du_client(tmp_path):
    # Chemin court : le dossier temporaire de macOS (/var/folders/…) dépasse la limite de sun_path (repli TCP).
    path = Path(tempfile.mkdtemp(prefix="ipc-", dir="/tmp")) / "c.sock"
    lst = ipc.listen(path, 1)
    try:
        assert lst.mode == "unix"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        lst.sock.settimeout(2)
        with ipc.connect(path, 2):
            conn, _ = lst.sock.accept()
            with conn:
                assert lst.admit(conn)
                assert ipc.peer_pid(conn) == os.getpid()
    finally:
        lst.close()
    assert not path.exists()


@pytest.mark.skipif(not POSIX_SOCKETS, reason="socket Unix absente (Windows)")
def test_unix_auto_test(tmp_path):
    assert ipc.self_test(tmp_path) == []


def test_auto_test_signale_un_pid_faux(tmp_path, monkeypatch):
    monkeypatch.setattr(ipc, "peer_pid", lambda conn: 0)
    problems = ipc.self_test(tmp_path)
    assert any("ne retrouve pas le process client" in p for p in problems)


# ------------------------------------------------------------------ mode TCP (faux Windows)

def test_tcp_rendez_vous_et_auto_test(tcp, tmp_path):
    path = tmp_path / "control.sock"
    lst = ipc.listen(path, 1)
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        assert set(info) == {"port", "token", "pid"} and info["pid"] == os.getpid()
        assert info["port"] == lst.sock.getsockname()[1]
        if os.name == "posix":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
    finally:
        lst.close()
    assert not path.exists()
    assert ipc.self_test(tmp_path) == []


def test_tcp_canal_de_controle(tcp, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONTROL_SOCKET", tmp_path / "control.sock")
    got = []
    bus.set_command_handler(lambda cmd: got.append(cmd) or {"ok": True})
    srv = state.ControlServer(claude_pid=lambda: None)
    srv.start()
    try:
        reply = state.send_command(json.dumps({"cmd": "status", "_from_claude": True, "_via": "hud"}))
    finally:
        srv.stop()
        bus.set_command_handler(None)
    assert json.loads(reply) == {"ok": True}
    # Le pid du client est retrouvé dans la table TCP : le cœur lui-même n'est pas Claude.
    assert got[0]["_from_claude"] is False and got[0]["_via"] == "socket"


def test_tcp_canal_de_confirmation_et_hook_parti(tcp, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIRM_SOCKET", tmp_path / "confirm.sock")
    alive_seen = []

    def ask(tool, desc, cmd, key, **kw):
        alive_seen.append(kw["alive"]())
        return True

    srv = confirm.ConfirmServer(ask)
    srv.start()
    try:
        assert confirm.request("Bash", "d", "git push", "k") is True
    finally:
        srv.stop()
    assert alive_seen == [True]


def _handshake(port: int, token: bytes) -> socket.socket:
    s = socket.create_connection(("127.0.0.1", port), timeout=3)
    s.sendall(b"0" * 32 + b"\n")
    proof = b""
    while not proof.endswith(b"\n"):
        proof += s.recv(1)
    s.sendall(token + b"\n")
    return s


def _admettre_en_fond(lst) -> tuple[threading.Thread, list]:
    """Le serveur répond à la poignée de main pendant que le test joue le client."""
    verdicts: list = []

    def servir():
        conn, _ = lst.sock.accept()
        with conn:
            verdicts.append(lst.admit(conn))

    t = threading.Thread(target=servir, daemon=True)
    t.start()
    return t, verdicts


def test_tcp_jeton_refuse(tcp, tmp_path):
    path = tmp_path / "c.sock"
    lst = ipc.listen(path, 2)
    info = json.loads(path.read_text())
    lst.sock.settimeout(3)
    try:
        t, verdicts = _admettre_en_fond(lst)
        with _handshake(info["port"], b"mauvais-jeton"):
            t.join(3)
        assert verdicts == [False]
        # Le bon jeton passe par la même poignée de main.
        t, verdicts = _admettre_en_fond(lst)
        with _handshake(info["port"], info["token"].encode()):
            t.join(3)
        assert verdicts == [True]
        # Sans nonce lisible (le jeton envoyé tout de suite, à l'ancienne) : refusé.
        t, verdicts = _admettre_en_fond(lst)
        with socket.create_connection(("127.0.0.1", info["port"]), timeout=3) as s:
            s.sendall(info["token"].encode() + b"\n")
            t.join(3)
        assert verdicts == [False]
    finally:
        lst.close()


def test_tcp_jeton_refuse_aucune_commande(tcp, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONTROL_SOCKET", tmp_path / "control.sock")
    got = []
    bus.set_command_handler(lambda cmd: got.append(cmd) or {"ok": True})
    srv = state.ControlServer(claude_pid=lambda: None)
    srv.start()
    try:
        port = json.loads(config.CONTROL_SOCKET.read_text())["port"]
        with _handshake(port, b"faux") as s:
            s.sendall(b"wake")
            s.settimeout(3)
            try:
                assert s.recv(100) == b""  # fermée sans réponse
            except ConnectionResetError:
                pass  # Windows : fermée sans réponse, données non lues → RST au lieu d'une fin propre
    finally:
        srv.stop()
        bus.set_command_handler(None)
    assert got == []


def test_tcp_client_muet_ne_bloque_pas_le_canal(tcp, tmp_path, monkeypatch):
    """Un client qui se connecte sans rien envoyer retenait le canal 3 s (par octet)."""
    monkeypatch.setattr(config, "CONTROL_SOCKET", tmp_path / "control.sock")
    bus.set_command_handler(lambda cmd: {"ok": True})
    srv = state.ControlServer(claude_pid=lambda: None)
    srv.start()
    try:
        port = json.loads(config.CONTROL_SOCKET.read_text())["port"]
        with socket.create_connection(("127.0.0.1", port), timeout=3):
            time.sleep(0.1)  # accepté par le serveur avant la vraie commande
            t0 = time.monotonic()
            reply = state.send_command("status", timeout=3)
            took = time.monotonic() - t0
    finally:
        srv.stop()
        bus.set_command_handler(None)
    assert json.loads(reply) == {"ok": True}
    assert took < ipc.ADMIT_TIMEOUT_S + 0.8


def test_tcp_rendez_vous_perime_pid_mort(tcp, tmp_path):
    path = tmp_path / "c.sock"
    path.write_text(json.dumps({"port": 9, "token": "x" * 40, "pid": _pid_mort()}))
    t0 = time.monotonic()
    with pytest.raises(OSError, match="périmé"):
        ipc.connect(path, 3)
    assert time.monotonic() - t0 < 1


def test_tcp_port_repris_ne_recoit_ni_jeton_ni_commande(tcp, tmp_path):
    """Fichier périmé dont le port est repris par un autre programme (pid vivant) :
    il ne prouve pas connaître le jeton, le client n'envoie rien d'autre que son nonce."""
    imposteur = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    imposteur.bind(("127.0.0.1", 0))
    imposteur.listen(1)
    imposteur.settimeout(3)
    recu = bytearray()

    def servir():
        conn, _ = imposteur.accept()
        with conn:
            conn.settimeout(2)
            while not recu.endswith(b"\n"):
                recu.extend(conn.recv(1))
            conn.sendall(b"0" * 64 + b"\n")
            try:
                while chunk := conn.recv(100):
                    recu.extend(chunk)
            except OSError:
                pass

    t = threading.Thread(target=servir, daemon=True)
    t.start()
    token = "jeton-secret-" + "y" * 30
    path = tmp_path / "c.sock"
    path.write_text(json.dumps({"port": imposteur.getsockname()[1], "token": token, "pid": os.getpid()}))
    try:
        with pytest.raises(OSError, match="prouvé"):
            ipc.connect(path, 2)
        t.join(3)
    finally:
        imposteur.close()
    assert recu.count(b"\n") == 1 and len(recu) == 33  # le nonce seul
    assert token.encode() not in recu


def test_tcp_rendez_vous_illisible(tcp, tmp_path):
    path = tmp_path / "c.sock"
    path.write_text("pas du json")
    with pytest.raises(OSError):
        ipc.connect(path, 1)
    with pytest.raises(OSError):
        ipc.connect(tmp_path / "absent.sock", 1)


# ------------------------------------------------------------------ hook parti (peer_alive)

def test_peer_alive_sur_tcp():
    srv = socket.create_server(("127.0.0.1", 0))
    cli = socket.create_connection(srv.getsockname(), timeout=2)
    conn, _ = srv.accept()
    try:
        conn.settimeout(5)
        t0 = time.monotonic()
        assert ipc.peer_alive(conn)
        assert time.monotonic() - t0 < 0.5  # ne bloque pas sur le délai de la socket
        cli.close()
        time.sleep(0.05)
        assert not ipc.peer_alive(conn)
    finally:
        conn.close()
        srv.close()


@pytest.mark.skipif(sys.platform == "win32", reason="numéros de descripteurs POSIX")
def test_peer_alive_descripteur_au_dela_de_1024():
    """select() levait ValueError pour un fd ≥ 1024 : le hook passait pour parti,
    la question était retirée et l'action refusée."""
    import resource

    high = 1500
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft <= high:
        if hard != resource.RLIM_INFINITY and hard <= high:
            pytest.skip("limite de fichiers ouverts trop basse")
        resource.setrlimit(resource.RLIMIT_NOFILE, (high + 100, hard))
    a, b = socket.socketpair()
    try:
        os.dup2(a.fileno(), high)
        a.close()
        a = socket.socket(fileno=high)
        assert a.fileno() >= 1024
        a.settimeout(5)
        t0 = time.monotonic()
        assert ipc.peer_alive(a)
        assert time.monotonic() - t0 < 0.5
        assert confirm.peer_alive(a)  # nom public d'origine
        b.close()
        assert not ipc.peer_alive(a)
    finally:
        a.close()
        b.close()
        if soft <= high:
            resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


def test_peer_alive_socket_fermee():
    a, b = socket.socketpair()
    a.close()
    assert not ipc.peer_alive(a)
    b.close()


# ------------------------------------------------------------------ process, faux Mac et faux Windows (psutil)

@pytest.mark.parametrize("systeme", ["macos", "windows"])
def test_proc_voie_psutil(faux_systeme, systeme):
    faux_systeme(systeme)
    me = os.getpid()
    assert proc.exists(me) and not proc.exists(_pid_mort()) and not proc.exists(0)
    assert proc.parent(me) == os.getppid()
    nom = psutil.Process(me).name()
    assert proc.name(me) == (nom[:-4] if nom.lower().endswith(".exe") else nom)  # sans « .exe » (Windows)
    assert proc.descends_from(me, os.getppid())
    assert not proc.uses_cgroup() and proc.cgroup_of(me) == ""
    assert proc.parent(_pid_mort()) is None


@pytest.mark.parametrize("systeme", ["macos", "windows"])
def test_marqueur_du_cerveau(faux_systeme, systeme, monkeypatch):
    """Sans cgroup, un process détaché du cerveau est reconnu par son marqueur."""
    faux_systeme(systeme)
    marque = _enfant(env=proc.brain_env())
    neutre = _enfant(env={k: v for k, v in os.environ.items() if k != proc.BRAIN_VAR})
    try:
        time.sleep(0.2)
        assert proc.brain_marker(marque.pid) is True
        assert proc.brain_marker(neutre.pid) is False
        # claude_pid inconnu : seul le marqueur décide.
        assert state.from_claude(marque.pid, None) is True
        assert state.from_claude(neutre.pid, None) is False
        assert state.from_claude(neutre.pid, os.getpid()) is True  # descendant de « Claude »
        monkeypatch.setattr(proc, "brain_marker", lambda pid: None)
        assert state.from_claude(neutre.pid, None) is True  # environnement illisible : suspect
    finally:
        marque.kill()
        neutre.kill()
        marque.wait()
        neutre.wait()


@pytest.mark.parametrize("systeme", ["macos", "windows"])
def test_tcp_owner_psutil(faux_systeme, systeme, monkeypatch):
    faux_systeme(systeme)
    srv = socket.create_server(("127.0.0.1", 0))
    cli = socket.create_connection(srv.getsockname(), timeout=2)
    conn, _ = srv.accept()
    try:
        cport, sport = cli.getsockname()[1], srv.getsockname()[1]
        assert proc.tcp_owner(cport, sport) == os.getpid()
        assert ipc.peer_pid(conn) == os.getpid()
        assert proc.tcp_owner(1, sport) == 0
        if systeme == "macos":
            # La table du système demande root sous macOS : repli process par process.
            def refuse(*a, **k):
                raise psutil.AccessDenied()
            monkeypatch.setattr(psutil, "net_connections", refuse)
            assert proc.tcp_owner(cport, sport) == os.getpid()
    finally:
        cli.close()
        conn.close()
        srv.close()


def test_auto_test_faux_windows(faux_systeme, monkeypatch, tmp_path):
    """Windows simulé : TCP, pid du client par psutil, marqueur lisible. Hors du
    profil de l'utilisateur, le jeton serait lisible par d'autres comptes : signalé."""
    monkeypatch.delenv("ASTREL_IPC", raising=False)
    faux_systeme("windows")
    assert ipc.mode() == "tcp"
    home = tmp_path / "profil"
    runtime = home / "Astrel" / "runtime"
    runtime.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Path.home() sous un vrai Windows
    assert ipc.self_test(runtime) == []
    dehors = tmp_path / "racine"
    dehors.mkdir()
    problems = ipc.self_test(dehors)
    assert len(problems) == 1 and "hors du profil" in problems[0]


# ------------------------------------------------------------------ protégés par le garde-fou

@pytest.mark.parametrize("rel", ["astrel/ipc.py", "astrel/platform/proc.py"])
def test_canal_protege_en_ecriture_lecture_libre(rel):
    """Ils décident qui est Claude : écrire = action sur le garde-fou (même en Total,
    même pendant une levée) ; les lire reste libre."""
    from astrel import guard

    full = (config.ROOT / rel).as_posix()  # en « / » : Git Bash mangerait les « \\ » (Windows)
    v = guard.assess("Edit", {"file_path": full, "old_string": "a", "new_string": "b"}, mode="total")
    assert v.needs_confirm and v.guard is True and not v.lift
    assert guard.assess("Bash", {"command": f"sed -i s/a/b/ {full}"}, mode="autonome").needs_confirm
    assert not guard.assess("Bash", {"command": f"grep -n peer {full} | head"}, mode="autonome").needs_confirm


# ------------------------------------------------------------------ cgroup (Linux seulement)

@pytest.mark.skipif(sys.platform != "linux", reason="cgroup v2 : Linux seulement")
def test_cgroup_linux():
    cg = proc.cgroup_of(os.getpid())
    if not cg:
        pytest.skip("cgroup v2 illisible ici")
    assert cg.startswith("/") and proc.uses_cgroup()
    assert state.cgroup_of(os.getpid()) == cg


def test_chemin_de_socket_trop_long_passe_en_tcp(tmp_path, monkeypatch):
    """03/11 (CI macOS) : au-delà de la limite de sun_path, le canal passe en TCP, serveur ET client."""
    monkeypatch.delenv("ASTREL_IPC", raising=False)
    profond = tmp_path / ("d" * 60) / ("e" * 60) / "control.sock"
    profond.parent.mkdir(parents=True)
    assert ipc.mode(profond) == "tcp"
    if hasattr(ipc.socket, "AF_UNIX") and ipc.SYSTEM != "windows":
        assert ipc.mode(tmp_path / "c.sock") == ("unix" if len(str(tmp_path / "c.sock")) <= ipc._UNIX_MAX else "tcp")
    lst = ipc.Listener(profond)
    try:
        assert lst.mode == "tcp" and "port" in json.loads(profond.read_text())  # rendez-vous TCP (même que Windows)
    finally:
        lst.close()
