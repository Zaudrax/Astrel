"""Le HUD se reconnecte seul après une relance d'Astrel (15/10,
docs/DURCISSEMENT-GARDE-FOU.md, section « Reconnexion du HUD »).

Avant : sessions en mémoire seulement, la page ouverte devenait « hors ligne » à
chaque relance. Désormais le cookie est signé par une clé stable (fichier 0600 à
côté du jeton) : il survit à la relance, garde sa marque « ouvert par Claude », et
le jeton reste nouveau à chaque démarrage."""
import asyncio
import json
import os
import time

import aiohttp
import pytest

from astrel import config, guard, server
from astrel.events import bus
from conftest import mode_prive

PORT = 47195
BASE = f"http://127.0.0.1:{PORT}"
ORIGIN = {"Origin": BASE}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    # Jeton ET clé dans un dossier temporaire : jamais ceux du vrai Astrel.
    monkeypatch.setattr(config, "HUD_TOKEN_FILE", tmp_path / "hud-token")
    monkeypatch.setattr(config, "HUD_PORT", PORT)
    monkeypatch.setattr(guard, "MODE_FILE", tmp_path / "autonomy.json")
    monkeypatch.setattr(guard, "GRANTS_FILE", tmp_path / "grants.json")
    received = []
    bus.set_command_handler(lambda cmd: received.append(cmd) or {"ok": True})
    yield received
    bus.set_command_handler(None)


def run(coro):
    return asyncio.run(coro)


def start(claude_pid=None) -> server.HudServer:
    srv = server.HudServer(claude_pid=lambda: claude_pid)
    srv.start()
    return srv


async def _login(ticket: str) -> str:
    """Comme le navigateur : /login?k=ticket, puis le cookie posé par le serveur."""
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{BASE}/login?k={ticket}", allow_redirects=False) as r:
            assert r.status == 303
            return r.cookies[server.COOKIE].value


async def _ws_command(cookie: str, ident: int) -> dict:
    async with aiohttp.ClientSession() as s:
        headers = {**ORIGIN, "Cookie": f"{server.COOKIE}={cookie}"}
        async with s.ws_connect(f"{BASE}/ws", headers=headers) as ws:
            hello = json.loads((await ws.receive()).data)
            await ws.send_json({"id": ident, "cmd": "wake"})
            while json.loads((await ws.receive()).data).get("type") != "ack":
                pass
    return hello


async def _status(path: str, cookie: str | None) -> int:
    headers = {"Cookie": f"{server.COOKIE}={cookie}"} if cookie else {}
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{BASE}{path}", headers=headers) as r:
            return r.status


def test_la_page_ouverte_survit_a_une_relance(isolated):
    first = start()
    try:
        token1 = first.token
        cookie = run(_login(first.issue_ticket()))
    finally:
        first.stop()
    second = start()  # « relance » : nouveau serveur, nouveau jeton, même clé
    try:
        assert second.token != token1
        assert run(_status("/api/session", cookie)) == 200
        hello = run(_ws_command(cookie, 31))
    finally:
        second.stop()
    assert hello["type"] == "hello"
    got = [c for c in isolated if c.get("id") == 31][-1]
    assert got["_via"] == "hud" and got["_from_claude"] is False
    assert token1 not in cookie and second.token not in cookie


def test_session_de_claude_reste_marquee_apres_relance(isolated):
    first = start()
    try:
        cookie = run(_login(first.issue_ticket(claude=True)))  # ticket demandé par un process de Claude
    finally:
        first.stop()
    second = start()
    try:
        run(_ws_command(cookie, 32))  # puis utilisé depuis un process qui n'est pas Claude
    finally:
        second.stop()
    got = [c for c in isolated if c.get("id") == 32][-1]
    assert got["_from_claude"] is True


def test_cookie_modifie_ou_fabrique_refuse():
    srv = start()
    try:
        good = srv._sign(True)
        flipped = "0" + good[1:]                          # marque « Claude » retirée à la main
        forged = "0.%d.abc.%s" % (time.time(), "0" * 64)  # signature inventée
        assert run(_status("/api/session", good)) == 200
        for bad in (flipped, forged, "x", good + "0", ""):
            assert run(_status("/api/session", bad)) == 401, bad
    finally:
        srv.stop()


def test_cookie_trop_vieux_refuse():
    srv = start()
    try:
        old = srv._sign(False, now=time.time() - server.SESSION_MAX_S - 10)
        future = srv._sign(False, now=time.time() + server.CLOCK_SKEW_S + 60)
        assert run(_status("/api/session", old)) == 401
        assert run(_status("/api/session", future)) == 401
    finally:
        srv.stop()


def test_cookie_renouvele_et_marque_claude_gardee():
    srv = start(claude_pid=os.getpid())  # le test joue Claude : ses connexions viennent de Claude
    try:
        aged = srv._sign(False, now=time.time() - server.RENEW_AFTER_S - 10)

        async def session():
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{BASE}/api/session", headers={"Cookie": f"{server.COOKIE}={aged}"}) as r:
                    return r.status, r.cookies.get(server.COOKIE)
        status, fresh = run(session())
    finally:
        srv.stop()
    assert status == 200 and fresh is not None
    claude, issued = srv._check(fresh.value)
    assert issued > time.time() - 60
    assert claude is True  # utilisée depuis un process de Claude : la marque est posée, jamais retirée
    assert "httponly" in fresh.output().lower() and "samesite=strict" in fresh.output().lower()


def test_cle_de_session_0600_a_cote_du_jeton_et_refaite_si_exposee(tmp_path):
    srv = start()
    try:
        cookie = srv._sign(False)
    finally:
        srv.stop()
    key = tmp_path / config.HUD_SESSION_KEY_FILE.name
    assert server.session_key_file() == key and mode_prive(key, 0o600)
    assert start_and_check(cookie) == 200  # même clé : accepté
    key.chmod(0o644)                       # lisible par d'autres : clé refaite, sessions fermées
    assert start_and_check(cookie) == 401
    assert mode_prive(key, 0o600)


def start_and_check(cookie: str) -> int:
    srv = start()
    try:
        return run(_status("/api/session", cookie))
    finally:
        srv.stop()


def test_la_vraie_cle_n_est_jamais_touchee_par_les_tests(tmp_path):
    assert server.session_key_file().parent == tmp_path
    assert server.session_key_file() != config.HUD_SESSION_KEY_FILE


def test_cle_de_session_protegee_par_le_garde_fou():
    key = config.HUD_SESSION_KEY_FILE
    for tool, inp in (("Read", {"file_path": str(key)}),
                      ("Bash", {"command": f"cat {key}"}),
                      ("Bash", {"command": "cat runtime/" + key.name}),
                      ("Bash", {"command": f"echo 00 > {key}"}),
                      ("Write", {"file_path": str(key), "content": "00"})):
        v = guard.assess(tool, inp, mode="autonome", cwd=str(config.ROOT))
        assert v.needs_confirm and v.guard, (tool, inp, v.desc)


# Relecture du 15/10 : un mot qui PEUT désigner un secret du HUD sans le nommer en
# clair (joker, partie calculée, morceaux recollés) est une lecture du secret ; le
# profil Chrome du HUD (qui garde le cookie) est protégé comme la clé.
TOK, KEY, PROF = (config.HUD_TOKEN_FILE.name, config.HUD_SESSION_KEY_FILE.name, config.HUD_PROFILE_DIR.name)


def _bash(cmd: str, cwd=config.ROOT):
    return guard.assess("Bash", {"command": cmd}, mode="autonome", cwd=str(cwd))


@pytest.mark.parametrize("cmd, cwd", [
    (f"cat runtime/{KEY[:5]}*", config.ROOT),
    ("cat runtime/hud*", config.ROOT),
    ("cat runtime/*", config.ROOT),
    (f"cat {KEY[:4]}?{KEY[5:]}", config.RUNTIME),
    (f"cd runtime && head {TOK[:-1]}?", config.ROOT),
    (f"cat runtime/{TOK[:4]}{{{TOK[4:]},x}}", config.ROOT),             # accolades
    (f"cat runtime/{TOK[:5]}\"{TOK[5:8]}\"{TOK[8:]}", config.ROOT),       # morceaux recollés
    ("D=runtime; cat $D/h*", config.ROOT),
    (f"cat < runtime/{TOK[:5]}*", config.ROOT),
    ("cat \"$X\"/hud-*", config.ROOT),                                   # dossier inconnu
    (f"sqlite3 runtime/{PROF[:5]}*/Default/Cookies .dump", config.ROOT),
    (f"cp -r runtime/{PROF} /tmp/copie", config.ROOT),
    (f"tar czf /tmp/p.tgz -C runtime {PROF}", config.ROOT),
])
def test_secret_du_hud_designe_sans_son_nom_demande(cmd, cwd):
    v = _bash(cmd, cwd)
    assert v.needs_confirm and v.guard, (cmd, v.desc)


@pytest.mark.parametrize("cmd", [
    "ls runtime/",
    "cat runtime/*.json | head",
    "ls hud/src/*.js; cat hud/*.html | wc -l",
    f"grep -n \"{TOK}\" astrel/cli.py",                                 # motif de recherche, pas le fichier
    "grep -n 'hud.*' astrel/cli.py",
    "./hud.sh",
    "cat /tmp/*.log | tail",
])
def test_jokers_ailleurs_restent_libres(cmd):
    v = _bash(cmd)
    assert not v.needs_confirm, (cmd, v.desc)


def test_profil_chrome_du_hud_protege_lecture_et_ecriture():
    cookies = config.HUD_PROFILE_DIR / "Default" / "Cookies"
    for tool, inp in (("Read", {"file_path": str(cookies)}),
                      ("Grep", {"pattern": "astrel", "path": str(config.HUD_PROFILE_DIR)}),
                      ("Write", {"file_path": str(cookies), "content": ""}),
                      ("Edit", {"file_path": str(config.HUD_PROFILE_DIR / "Default" / "Preferences")})):
        v = guard.assess(tool, inp, mode="autonome", cwd=str(config.ROOT))
        assert v.needs_confirm and v.guard, (tool, v.desc)
    # Astrel lui-même lance Chrome sur ce profil (hors du hook) : chemin inchangé.
    assert config.HUD_PROFILE_DIR == config.RUNTIME / PROF
