"""Le serveur du HUD ne doit obéir qu'à la machine, et qu'avec le bon jeton."""
import asyncio
import json

import aiohttp
import pytest

from astrel import config
from astrel.events import bus
from astrel.server import HudServer


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    # Port et jeton à part : les tests peuvent tourner pendant qu'Astrel tourne.
    config.HUD_PORT = 47190
    config.HUD_TOKEN_FILE = tmp_path_factory.mktemp("hud") / "hud-token"
    srv = HudServer()
    received = []
    bus.set_command_handler(lambda cmd: received.append(cmd) or {"ok": True})
    srv.start()
    yield srv, received
    srv.stop()


def run(coro):
    return asyncio.run(coro)


def test_jeton_obligatoire(server):
    srv, _ = server

    async def go():
        async with aiohttp.ClientSession() as s:
            async with s.post(f"http://127.0.0.1:{config.HUD_PORT}/api/command", json={"cmd": "wake"}) as r:
                return r.status

    assert run(go()) == 401


def test_origine_etrangere_refusee(server):
    srv, _ = server

    async def go():
        async with aiohttp.ClientSession() as s:
            try:
                async with s.ws_connect(f"http://127.0.0.1:{config.HUD_PORT}/ws?t={srv.token}",
                                        headers={"Origin": "https://site-malveillant.example"}):
                    return "connecté"
            except aiohttp.WSServerHandshakeError as exc:
                return exc.status

    assert run(go()) == 401


def test_websocket_instantane_et_commande(server):
    srv, received = server
    bus.publish("state", state="sleeping")

    async def go():
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(f"http://127.0.0.1:{config.HUD_PORT}/ws?t={srv.token}",
                                    headers={"Origin": f"http://127.0.0.1:{config.HUD_PORT}"}) as ws:
                hello = json.loads((await ws.receive()).data)
                await ws.send_json({"id": 1, "cmd": "wake"})
                while True:
                    msg = json.loads((await ws.receive()).data)
                    if msg["type"] == "ack":
                        return hello, msg

    hello, ack = run(go())
    assert hello["type"] == "hello"
    assert any(e["type"] == "state" for e in hello["sticky"])
    assert ack["result"] == {"ok": True}
    got = [c for c in received if c.get("cmd") == "wake" and c.get("id") == 1]
    assert got and got[-1]["_via"] == "hud"  # origine établie par le serveur
    assert got[-1]["_from_claude"] is False  # le test lui-même est le cœur (même process)


def test_jeton_en_0600(server):
    mode = config.HUD_TOKEN_FILE.stat().st_mode & 0o777
    assert mode == 0o600
