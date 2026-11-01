"""Un seul HUD à l'écran (19/10, demande : « HUD » sur la bulle ou « Ouvrir le HUD »
ouvrait une DEUXIÈME fenêtre au lieu de ramener la première).

`./hud.sh` (cli.open_hud) note les pages connectées (`hud_pages`), ouvre la nouvelle
fenêtre comme avant, attend qu'elle soit connectée puis demande `hud_replace {before}` :
le cœur publie `hud_close`, chaque page chargée avant se ferme (hud/src/remplacement.js).
Tout est simulé : aucun Chrome, aucune fenêtre, aucun vrai Astrel."""
import asyncio
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrel import cli, config, core, server, state
from astrel.events import bus

HUD = Path(__file__).resolve().parent.parent / "hud"


# ---------------------------------------------------------------- page du HUD (JavaScript)

@pytest.mark.skipif(not shutil.which("node"), reason="Node absent")
def test_regle_de_fermeture_node():
    r = subprocess.run(["node", "--test", str(HUD / "tests" / "remplacement.test.mjs")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert re.search(r"^# fail 0$", r.stdout, re.M)


def test_la_page_ferme_sa_fenetre_sur_hud_close():
    conn = (HUD / "src" / "conn.js").read_text(encoding="utf-8")
    imp = re.search(r'import \{([^}]*)\} from "\./remplacement\.js"', conn)
    assert imp and {"replacedBy", "stashForReplace", "restoreAfterReplace"} <= {s.strip() for s in imp[1].split(",")}
    block = conn[conn.index('ev.type === "hud_close"'):]
    block = block[:block.index("emit(ev.type, ev)")]
    assert "replacedBy(ev, performance.timeOrigin)" in block and "window.close()" in block
    # Le texte non envoyé est mis à l'abri AVANT la fermeture, la page se signale à la connexion.
    assert block.index("stashForReplace(localStorage, ev.before)") < block.index("window.close()")
    assert 'cmd: "hud_here", time_origin: performance.timeOrigin / 1000' in conn
    assert 'keepOnReplace("ask", askKeeper(document))' in conn
    assert 'addEventListener("storage"' in conn
    plans = (HUD / "src" / "plans.js").read_text(encoding="utf-8")
    assert 'keepOnReplace("plans"' in plans


def test_le_hud_compile_contient_la_fermeture():
    app = (HUD / "dist" / "app.js").read_text(encoding="utf-8")
    # `npm run build` refait après la modif
    assert "hud_close" in app and "timeOrigin" in app and "hud_here" in app and "astrel-hud-relais:" in app


# ---------------------------------------------------------------- cœur

def _astrel(pages: int = 0):
    j = object.__new__(core.Astrel)
    j.hud = SimpleNamespace(_clients={object() for _ in range(pages)})
    return j


@pytest.fixture
def published():
    seen = []
    unsubscribe = bus.subscribe(lambda ev: seen.append(ev) if ev.get("type") == "hud_close" else None)
    yield seen
    unsubscribe()


def test_coeur_retient_la_page_la_plus_recente():
    j = _astrel(2)
    now = time.time()
    assert j._on_command({"cmd": "hud_here", "time_origin": now - 300, "_from_claude": False}) == {"ok": True}
    assert j._on_command({"cmd": "hud_here", "time_origin": now - 2})["ok"] is True
    assert j._on_command({"cmd": "hud_here", "time_origin": now - 300})["ok"] is True  # reconnexion d'une ancienne
    assert j._on_command({"cmd": "hud_pages"})["newest"] == now - 2
    # Page ouverte par Claude, ou heure illisible / dans le futur : ne compte pas.
    assert j._on_command({"cmd": "hud_here", "time_origin": now, "_from_claude": True})["ok"] is True
    for bad in (None, "x", True, -1, now + 3600):
        assert j._on_command({"cmd": "hud_here", "time_origin": bad})["ok"] is False, bad
    assert j._on_command({"cmd": "hud_pages"})["newest"] == now - 2
    assert _astrel(1)._on_command({"cmd": "hud_pages"})["newest"] == 0.0


def test_coeur_compte_les_pages_du_hud():
    assert _astrel(2)._on_command({"cmd": "hud_pages", "_from_claude": False}) == \
        {"ok": True, "pages": 2, "replace": True, "newest": 0.0}
    assert _astrel(0)._on_command({"cmd": "hud_pages"})["pages"] == 0
    # Lancé par Claude : il peut compter, pas fermer.
    assert _astrel(1)._on_command({"cmd": "hud_pages", "_from_claude": True})["replace"] is False


def test_coeur_publie_hud_close(published):
    res = _astrel(2)._on_command({"cmd": "hud_replace", "before": 1760000000.5, "_from_claude": False})
    assert res == {"ok": True}
    assert len(published) == 1 and published[0]["before"] == 1760000000.5


def test_hud_replace_refuse_a_claude_et_illisible(published):
    j = _astrel(2)
    assert j._on_command({"cmd": "hud_replace", "before": 1760000000.0, "_from_claude": True})["ok"] is False
    for before in (None, "1760000000", True, 0, -3):
        assert j._on_command({"cmd": "hud_replace", "before": before})["ok"] is False, before
    assert published == []


def test_hud_close_ni_rejoue_ni_garde():
    """Un HUD qui se connecte APRÈS ne doit pas recevoir l'ordre de se fermer."""
    from astrel import events

    assert "hud_close" not in events.STICKY and "hud_close" not in events.HISTORY


# ---------------------------------------------------------------- vrai serveur du HUD

def test_serveur_compte_et_diffuse(tmp_path, monkeypatch, published):
    """`len(hud._clients)` (lu par le cœur) suit les WebSocket, et `hud_close` y part."""
    import aiohttp

    monkeypatch.setattr(config, "HUD_TOKEN_FILE", tmp_path / "hud-token")  # jamais le vrai jeton
    monkeypatch.setattr(config, "HUD_PORT", 47194)
    srv = server.HudServer(claude_pid=lambda: None)
    srv.start()
    j = _astrel()
    j.hud = srv
    try:
        async def go():
            base = "http://127.0.0.1:47194"
            async with aiohttp.ClientSession() as s:
                async with s.ws_connect(f"{base}/ws?t={srv.token}", headers={"Origin": base}) as ws:
                    await ws.receive()  # hello
                    pages = j._on_command({"cmd": "hud_pages"})["pages"]
                    j._on_command({"cmd": "hud_replace", "before": 123.0})
                    while True:
                        ev = json.loads((await asyncio.wait_for(ws.receive(), 5)).data)
                        if ev.get("type") == "hud_close":
                            return pages, ev

        pages, ev = asyncio.run(go())
        assert pages == 1 and ev["before"] == 123.0
        deadline = time.time() + 5
        while srv._clients and time.time() < deadline:
            time.sleep(0.05)
        assert j._on_command({"cmd": "hud_pages"})["pages"] == 0
    finally:
        srv.stop()


# ---------------------------------------------------------------- ./hud.sh (cli.open_hud)

class FakeCore:
    """Faux canal de contrôle : `pages` pages connectées ; quand Chrome est « lancé », la
    nouvelle page se signale (`newest` = son heure de chargement, après le lancement)."""

    def __init__(self, pages: int, replace: bool = True, new_page_connects: bool = True,
                 old_page_reconnects: bool = False):
        self.pages, self.replace, self.connects = pages, replace, new_page_connects
        self.newest = time.time() - 600 if pages else 0.0  # anciennes pages : chargées il y a 10 min
        self.reconnects = old_page_reconnects
        self.calls: list[dict] = []
        self.order: list[str] = []

    def send(self, text: str, timeout: float = 3.0):
        cmd = json.loads(text) if text.startswith("{") else {"cmd": text}
        self.calls.append(cmd)
        if cmd["cmd"] == "hud_pages":
            return json.dumps({"ok": True, "pages": self.pages, "replace": self.replace, "newest": self.newest})
        if cmd["cmd"] == "hud_replace":
            self.order.append("replace")
            return json.dumps({"ok": self.replace})
        return json.dumps({"ok": False})

    def popen(self, args, **kw):
        self.order.append("popen")
        self.launched_at = time.time()
        if self.reconnects:
            self.pages += 1  # une ANCIENNE page se reconnecte : le compte monte, `newest` non
        if self.connects:
            self.pages += 1
            self.newest = time.time() + 0.05


@pytest.fixture
def hud_sh(tmp_path, monkeypatch):
    def make(**kw):
        fake = FakeCore(**kw)
        token = tmp_path / "hud-token"
        token.write_text("x" * 40)
        monkeypatch.setattr(config, "HUD_TOKEN_FILE", token)
        monkeypatch.setattr(server, "request_ticket", lambda token=None, timeout=5.0: "TICKET")
        monkeypatch.setattr(state, "send_command", fake.send)
        monkeypatch.setattr(subprocess, "Popen", fake.popen)
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(cli, "HUD_REPLACE_WAIT_S", 0.5)
        monkeypatch.setattr(cli, "HUD_REPLACE_POLL_S", 0.01)
        return fake
    return make


def test_hud_deja_ouvert_la_nouvelle_fenetre_remplace_l_ancienne(hud_sh):
    fake = hud_sh(pages=1)
    assert cli.open_hud() == 0
    # Fenêtre ouverte D'ABORD (Chrome ne doit pas quitter pendant la demande), puis fermeture.
    assert fake.order == ["popen", "replace"]
    replace = next(c for c in fake.calls if c["cmd"] == "hud_replace")
    assert replace["before"] <= fake.launched_at  # la nouvelle page, chargée après, reste


def test_aucun_hud_ouvert_comme_avant(hud_sh):
    fake = hud_sh(pages=0)
    t0 = time.monotonic()
    assert cli.open_hud() == 0
    assert fake.order == ["popen"] and time.monotonic() - t0 < 0.3  # ni attente ni fermeture


def test_lance_par_claude_ne_ferme_rien(hud_sh):
    fake = hud_sh(pages=1, replace=False)
    assert cli.open_hud() == 0
    assert fake.order == ["popen"]


def test_nouvelle_page_jamais_connectee_ne_ferme_rien(hud_sh):
    """Mieux vaut deux fenêtres qu'aucune."""
    fake = hud_sh(pages=1, new_page_connects=False)
    assert cli.open_hud() == 0
    assert fake.order == ["popen"]


def test_ancienne_page_qui_se_reconnecte_ne_passe_pas_pour_la_nouvelle(hud_sh):
    """Relecture du 19/10 : le comptage se trompait ; l'heure de chargement, non."""
    fake = hud_sh(pages=1, new_page_connects=False, old_page_reconnects=True)
    assert cli.open_hud() == 0
    assert fake.order == ["popen"]


def test_astrel_ancien_ou_muet_comme_avant(hud_sh, monkeypatch):
    fake = hud_sh(pages=1)
    for reply in (None, "commande inconnue", json.dumps({"ok": False}), json.dumps([1])):
        monkeypatch.setattr(state, "send_command", lambda text, timeout=3.0, r=reply: r)
        fake.order.clear()
        assert cli.open_hud() == 0
        assert fake.order == ["popen"], reply
