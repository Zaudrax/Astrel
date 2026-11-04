"""Serveur local du HUD : fichiers statiques + WebSocket branché sur le bus.

Sécurité :
- écoute sur 127.0.0.1 UNIQUEMENT, jamais sur le réseau ;
- jeton aléatoire de 256 bits, NOUVEAU à chaque démarrage (15/10), écrit en 0600
  dans `runtime/hud-token` ; il ne quitte plus ce fichier (bulle, `./hud.sh`) ;
- 15/10 : le jeton ne passe plus dans la ligne de commande de Chrome
  (`--app=…#t=jeton`, visible de tout process dans `ps`). `./hud.sh` échange le
  jeton contre un TICKET à usage unique valable 60 s (`/api/ticket`), ouvre
  `/login?k=ticket`, et le serveur pose un cookie de session HttpOnly,
  SameSite=Strict, qui reconnaît la page après un rechargement ;
- 15/10 (reconnexion seule) : le cookie est SIGNÉ (HMAC) par une clé stable,
  `runtime/hud-session-key` (0600, protégée par le garde-fou comme le jeton). Il
  survit donc à une relance d'Astrel : la page déjà ouverte se reconnecte seule.
  Il porte la marque « ouvert par Claude » (signée, donc infalsifiable sans la
  clé) et sa date ; valable SESSION_MAX_S, renouvelé à chaque chargement de la
  page ou vérification de session. Effacer la clé ferme toutes les sessions ;
- l'origine du WebSocket est vérifiée : une page web ouverte dans le navigateur
  ne peut pas se connecter à ws://127.0.0.1 pour piloter Astrel ;
- 15/10 : chaque connexion est rapportée à son process (`state.tcp_peer_pid`)
  et marquée `_from_claude` comme sur le canal de contrôle ; un ticket demandé
  par Claude donne une session marquée « Claude » pour toujours ;
- comparaison du jeton en temps constant.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable

from aiohttp import WSMsgType, web

from . import config, state
from .platform import systeme
from .events import bus

log = logging.getLogger("astrel.server")

CLIENT_QUEUE = 2000
DROPPABLE = {"level"}  # sous pression, on perd un niveau sonore plutôt qu'un événement
TICKET_S = 60.0         # durée de vie d'un ticket d'ouverture du HUD
COOKIE = "astrel_hud"
SESSION_MAX_S = 30 * 86400.0  # un cookie non renouvelé depuis 30 jours n'ouvre plus rien
RENEW_AFTER_S = 3600.0        # renouvelé (nouvelle date) au plus une fois par heure
CLOCK_SKEW_S = 300.0          # date du cookie dans le futur : tolérance d'horloge


def _new_token() -> str:
    # 15/10 : NOUVEAU jeton à chaque démarrage (avant, il survivait aux relances
    # et traînait dans la ligne de commande de Chrome, lisible par `ps`).
    token = secrets.token_urlsafe(32)
    path = config.HUD_TOKEN_FILE
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    return token


def session_key_file() -> Path:
    """À côté du jeton : un HUD de démo ou de test (`config.HUD_TOKEN_FILE` redirigé
    vers un dossier temporaire) n'utilise jamais la vraie clé."""
    return config.HUD_TOKEN_FILE.with_name(config.HUD_SESSION_KEY_FILE.name)


def _session_key() -> bytes:
    """Clé qui signe les cookies du HUD, STABLE d'un démarrage à l'autre (sinon la
    page ouverte devenait « hors ligne » à chaque relance). Refaite si le fichier
    manque, est illisible, n'est pas un fichier ordinaire à nous ou est lisible
    par d'autres que l'utilisateur : toutes les sessions tombent alors."""
    path = session_key_file()
    try:
        st = os.lstat(path)
        if systeme.proprietaire_seul(st):  # 03/11 : Windows n'a ni uid ni O_NOFOLLOW (platform/systeme.py)
            fd = systeme.ouvrir_sans_lien(path, os.O_RDONLY)
            with os.fdopen(fd, encoding="ascii") as f:
                key = bytes.fromhex(f.read().strip())
            if len(key) >= 32:
                return key
    except (OSError, ValueError):
        pass
    key = secrets.token_bytes(32)
    path.unlink(missing_ok=True)
    fd = systeme.ouvrir_sans_lien(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(key.hex())
    log.info("nouvelle clé de session du HUD : les pages ouvertes devront être rouvertes")
    return key


def read_token() -> str:
    try:
        return config.HUD_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def hud_version() -> str:
    """Empreinte des fichiers du HUD, envoyée dans « hello » : une page ouverte
    qui se reconnecte avec une empreinte différente se recharge. Sans ça, elle
    gardait son ancien code après une relance d'Astrel (vécu le 26/09)."""
    parts = []
    for name in ("index.html", "styles.css", "dist/app.js"):
        try:
            st = (config.HUD_DIR / name).stat()
            parts.append(f"{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append("-")
    return "|".join(parts)


def base_url() -> str:
    return f"http://{config.HUD_HOST}:{config.HUD_PORT}"


def login_url(ticket: str) -> str:
    """Adresse d'ouverture du HUD : un ticket à usage unique (60 s), jamais le jeton."""
    return f"{base_url()}/login?k={ticket}"


def request_ticket(token: str | None = None, timeout: float = 5.0) -> str:
    """Côté `./hud.sh` : échange le jeton (lu dans le fichier 0600) contre un
    ticket. Le jeton voyage dans un en-tête HTTP, jamais dans une ligne de
    commande. Vide si Astrel ne répond pas."""
    import urllib.request

    token = token or read_token()
    if not token:
        return ""
    req = urllib.request.Request(f"{base_url()}/api/ticket", method="POST", data=b"{}",
                                 headers={"X-Astrel-Token": token, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 127.0.0.1 : jamais par un proxy
    try:
        with opener.open(req, timeout=timeout) as resp:
            return str(json.loads(resp.read().decode("utf-8")).get("ticket") or "")
    except Exception:
        return ""


class HudServer:
    def __init__(self, claude_pid: Callable[[], int | None] | None = None) -> None:
        self.token = _new_token()
        self._key = _session_key()  # signe les cookies : stable, la page se reconnecte après une relance
        self._claude_pid = claude_pid or (lambda: None)
        self._auth_lock = threading.Lock()
        self._tickets: dict[str, tuple[float, bool]] = {}   # ticket → (expiration, demandé par Claude)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: web.AppRunner | None = None
        self._thread: threading.Thread | None = None
        self._clients: set[asyncio.Queue] = set()
        self._ready = threading.Event()
        self._unsubscribe = None

    # --- cycle de vie ---

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="hud-server", daemon=True)
        self._thread.start()
        self._ready.wait(10)
        self._unsubscribe = bus.subscribe(self._on_event)

    def stop(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
        if self._loop and self._runner:
            fut = asyncio.run_coroutine_threadsafe(self._runner.cleanup(), self._loop)
            try:
                fut.result(timeout=5)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        app = web.Application(middlewares=[self._security_headers])
        app.router.add_get("/ws", self._ws)
        app.router.add_post("/api/command", self._api_command)
        app.router.add_get("/api/snapshot", self._api_snapshot)
        app.router.add_post("/api/ticket", self._api_ticket)
        app.router.add_get("/api/session", self._api_session)
        app.router.add_get("/api/attachment/{file}", self._api_attachment)
        app.router.add_get("/api/media/{id}", self._api_media)
        app.router.add_get("/login", self._login)
        app.router.add_get("/", self._index)
        if config.HUD_DIR.exists():
            app.router.add_static("/", str(config.HUD_DIR), show_index=False, follow_symlinks=False)
        self._runner = web.AppRunner(app, access_log=None)
        self._loop.run_until_complete(self._runner.setup())
        site = web.TCPSite(self._runner, config.HUD_HOST, config.HUD_PORT)
        try:
            self._loop.run_until_complete(site.start())
            log.info("HUD sur http://%s:%d", config.HUD_HOST, config.HUD_PORT)
        except OSError as exc:
            log.error("HUD impossible sur le port %d : %s", config.HUD_PORT, exc)
            self._ready.set()
            return
        self._ready.set()
        self._loop.run_forever()

    # --- sécurité ---

    @web.middleware
    async def _security_headers(self, request: web.Request, handler):
        # 04/11 (lot B, M4) : en-tête Host limité au HUD lui-même (127.0.0.1 ou localhost, sur SON port) :
        # défense en profondeur contre un DNS rebinding ; absent ou autre = refusé, avant toute route.
        if not self._host_ok(request):
            log.warning("HUD : requête refusée, en-tête Host inattendu (%r)", request.headers.get("Host"))
            resp = web.Response(status=421, text="Hôte refusé par le HUD d'Astrel.", content_type="text/plain")
            self._add_headers(resp)
            return resp
        try:
            resp = await handler(request)
        except web.HTTPException as exc:  # 04/11 : les refus (401, 403, 404…) portent les mêmes en-têtes
            self._add_headers(exc)
            raise
        self._add_headers(resp)
        return resp

    @staticmethod
    def _add_headers(resp: web.StreamResponse) -> None:
        if getattr(resp, "prepared", False):
            return  # WebSocket fermé, réponse déjà partie : plus rien à poser
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Cache-Control", "no-store")
        # 04/11 (lot B, M4) : jamais dans un cadre (habillage de clic) ; la CSP du <meta> de index.html ne peut
        # pas porter `frame-ancestors`, d'où l'en-tête. Une CSP déjà posée (médias : sandbox) le reçoit en plus.
        resp.headers["X-Frame-Options"] = "DENY"
        csp = resp.headers.get("Content-Security-Policy", "")
        if "frame-ancestors" not in csp:
            resp.headers["Content-Security-Policy"] = f"{csp}; frame-ancestors 'none'" if csp else "frame-ancestors 'none'"

    @staticmethod
    def _host_ok(request: web.Request) -> bool:
        """Host = 127.0.0.1:<port> ou localhost:<port>, le port étant celui où la connexion est arrivée."""
        host = (request.headers.get("Host") or "").strip().lower()
        sock = request.transport.get_extra_info("sockname") if request.transport else None
        try:
            port = int(sock[1])
        except (TypeError, ValueError, IndexError):
            port = config.HUD_PORT
        return host in {f"{config.HUD_HOST}:{port}", f"127.0.0.1:{port}", f"localhost:{port}"}

    def _token_ok(self, candidate: str | None) -> bool:
        return bool(candidate) and hmac.compare_digest(candidate.encode(), self.token.encode())

    def issue_ticket(self, claude: bool = False) -> str:
        """Ticket à usage unique, valable TICKET_S secondes."""
        ticket = secrets.token_urlsafe(24)
        now = time.time()
        with self._auth_lock:
            self._tickets = {k: v for k, v in self._tickets.items() if v[0] > now}
            self._tickets[ticket] = (now + TICKET_S, claude)
        return ticket

    def _redeem(self, ticket: str) -> str | None:
        """Consomme le ticket (une seule fois) et rend le cookie de session ; None
        s'il est inconnu, déjà servi ou périmé."""
        with self._auth_lock:
            entry = self._tickets.pop(ticket, None) if ticket else None
        if entry is None or entry[0] < time.time():
            return None
        return self._sign(entry[1])

    def _mac(self, body: str) -> str:
        return hmac.new(self._key, f"astrel-hud-1|{body}".encode(), hashlib.sha256).hexdigest()

    def _sign(self, claude: bool, now: float | None = None) -> str:
        """Cookie « marque.date.aléa.signature » : la marque « ouvert par Claude » et la
        date sont signées, le client ne peut ni les changer ni en fabriquer."""
        body = f"{int(bool(claude))}.{int(now if now is not None else time.time())}.{secrets.token_urlsafe(18)}"
        return f"{body}.{self._mac(body)}"

    def _check(self, value: str) -> tuple[bool, float] | None:
        """(ouvert par Claude ?, date) d'un cookie bien signé et pas trop vieux ; sinon None."""
        body, _sep, mac = value.rpartition(".")
        parts = body.split(".")
        if len(parts) != 3 or parts[0] not in ("0", "1") or not parts[1].isdigit():
            return None
        if not hmac.compare_digest(mac.encode(), self._mac(body).encode()):
            return None
        issued, now = float(parts[1]), time.time()
        if issued < now - SESSION_MAX_S or issued > now + CLOCK_SKEW_S:
            return None
        return parts[0] == "1", issued

    def _session(self, request: web.Request) -> bool | None:
        """Session du cookie : None si absente ou invalide, sinon « ouverte par Claude ? »."""
        found = self._check(request.cookies.get(COOKIE, ""))
        return None if found is None else found[0]

    def _renew(self, request: web.Request, resp: web.StreamResponse, claude: bool | None = None) -> None:
        """Nouvelle date sur un cookie valide (au plus une fois par heure) : une page
        utilisée ne perd jamais sa session. La marque « Claude » ne se perd jamais."""
        found = self._check(request.cookies.get(COOKIE, ""))
        if found is None or (time.time() - found[1] < RENEW_AFTER_S and not (claude and not found[0])):
            return
        self._set_cookie(resp, self._sign(found[0] or bool(claude)))

    @staticmethod
    def _set_cookie(resp: web.StreamResponse, value: str) -> None:
        # Sans date d'expiration : le navigateur l'oublie à sa fermeture ; la limite
        # de durée est dans la signature (SESSION_MAX_S).
        resp.set_cookie(COOKIE, value, httponly=True, samesite="Strict", path="/")

    def _authorized(self, request: web.Request, candidate: str | None) -> bool:
        return self._token_ok(candidate) or self._session(request) is not None

    async def _peer_from_claude(self, request: web.Request) -> bool:
        """La connexion vient-elle de Claude ? Même contrôle que le canal de
        contrôle (`state.from_claude`), le process étant retrouvé dans /proc."""
        peer = request.transport.get_extra_info("peername") if request.transport else None
        try:
            port = int(peer[1])
        except (TypeError, ValueError, IndexError):
            return True
        loop = asyncio.get_running_loop()
        claude_pid = self._claude_pid()

        def check() -> bool:
            return state.from_claude(state.tcp_peer_pid(port, config.HUD_PORT), claude_pid)

        try:
            return await loop.run_in_executor(None, check)
        except Exception:
            log.exception("origine de la connexion au HUD illisible")
            return True

    async def _from_claude(self, request: web.Request) -> bool:
        return bool(self._session(request)) or await self._peer_from_claude(request)

    def _origin_ok(self, request: web.Request) -> bool:
        origin = request.headers.get("Origin")
        if origin is None:  # client local hors navigateur (CLI, tests)
            return True
        allowed = {f"http://{config.HUD_HOST}:{config.HUD_PORT}", f"http://localhost:{config.HUD_PORT}"}
        return origin in allowed

    # --- routes ---

    async def _index(self, request: web.Request) -> web.StreamResponse:
        index = config.HUD_DIR / "index.html"
        if index.exists():
            resp = web.FileResponse(index)
            self._renew(request, resp)
            return resp
        return web.Response(text="HUD non installé.", content_type="text/plain")

    async def _api_snapshot(self, request: web.Request) -> web.Response:
        if not self._authorized(request, request.headers.get("X-Astrel-Token")):
            raise web.HTTPUnauthorized()
        return web.json_response(bus.snapshot())

    async def _api_command(self, request: web.Request) -> web.Response:
        if not self._origin_ok(request) or not self._authorized(request, request.headers.get("X-Astrel-Token")):
            raise web.HTTPUnauthorized()
        try:
            cmd = await request.json()
        except Exception:
            raise web.HTTPBadRequest()
        result = await self._dispatch(cmd, await self._from_claude(request))
        return web.json_response(result)

    async def _api_ticket(self, request: web.Request) -> web.Response:
        """Jeton (en-tête) → ticket d'ouverture. Demandé par Claude (`./hud.sh`
        lancé par lui), le ticket n'ouvre qu'une session marquée « Claude »."""
        if not self._origin_ok(request) or not self._token_ok(request.headers.get("X-Astrel-Token")):
            raise web.HTTPUnauthorized()
        claude = await self._peer_from_claude(request)
        ticket = self.issue_ticket(claude=claude)
        return web.json_response({"ticket": ticket, "expires_in": TICKET_S})

    async def _api_session(self, request: web.Request) -> web.Response:
        """La page sait-elle encore se faire reconnaître ? (sinon : session expirée ou
        clé refaite). Appelé par la page à chaque coupure : renouvelle le cookie, et
        une session utilisée depuis un process de Claude en garde la marque."""
        claude = self._session(request)
        if claude is None:
            raise web.HTTPUnauthorized()
        resp = web.json_response({"ok": True})
        self._renew(request, resp, claude or await self._peer_from_claude(request))
        return resp

    async def _api_attachment(self, request: web.Request) -> web.Response:
        """19/10 : vignette d'une image jointe, pour le fil du HUD (session du HUD ou jeton).
        Seules les IMAGES de runtime/attachments sortent (`attachments.hud_image`) ; tout
        autre nom rend 404, sans dire pourquoi. Même vérification d'origine que /api/command et
        /ws (relecture du 19/10) : une page étrangère qui l'appelle (en-tête Origin) a 403 ; la
        balise <img> du HUD (même origine, sans Origin) et un client local passent."""
        if not self._origin_ok(request):
            raise web.HTTPForbidden()
        if not self._authorized(request, request.headers.get("X-Astrel-Token")):
            raise web.HTTPUnauthorized()
        from . import attachments

        loop = asyncio.get_running_loop()
        found = await loop.run_in_executor(None, attachments.hud_image, request.match_info.get("file", ""))
        if found is None:
            raise web.HTTPNotFound()
        data, media = found
        return web.Response(body=data, content_type=media, headers={
            "Content-Disposition": "inline", "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, max-age=3600"})

    async def _api_media(self, request: web.Request) -> web.StreamResponse:
        """22/10 (plan P26) : média publié par `post_report(files)`, `show_media` ou
        `ask_choice(images)`, pour le fil du HUD. Mêmes contrôles d'origine et de session
        que `/api/attachment`. Seules les COPIES de runtime/medias sortent, par leur id
        (`medias.serve`), avec le type lu dans leurs octets ; tout le reste rend 404.
        `?v=thumb` : miniature d'une image ; `?v=page&n=3` : page d'un PDF en PNG. Range
        permis (FileResponse) pour l'audio et la vidéo. Jamais interprété par le navigateur
        comme une page : CSP `default-src 'none'; sandbox`, nosniff, inline sans nom."""
        if not self._origin_ok(request):
            raise web.HTTPForbidden()
        if not self._authorized(request, request.headers.get("X-Astrel-Token")):
            raise web.HTTPUnauthorized()
        from . import medias

        loop = asyncio.get_running_loop()
        found = await loop.run_in_executor(None, medias.serve, request.match_info.get("id", ""),
                                           request.query.get("v", ""), request.query.get("n", ""))
        if found is None:
            raise web.HTTPNotFound()
        path, media = found
        return web.FileResponse(path, headers={
            "Content-Type": media, "Content-Disposition": "inline", "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox", "Cache-Control": "private, max-age=3600"})

    async def _login(self, request: web.Request) -> web.StreamResponse:
        sid = self._redeem(request.query.get("k", ""))
        if sid is None:
            return web.Response(status=403, content_type="text/plain", charset="utf-8",
                                text="Ticket du HUD expiré ou déjà utilisé : rouvre le HUD (Ctrl+Alt+H ou ./hud.sh).")
        resp = web.Response(status=303, headers={"Location": "/"})
        self._set_cookie(resp, sid)
        return resp

    async def _ws(self, request: web.Request) -> web.StreamResponse:
        if not self._origin_ok(request) or not self._authorized(request, request.query.get("t")):
            raise web.HTTPUnauthorized()
        from_claude = await self._from_claude(request)
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=8 * 1024 * 1024)
        await ws.prepare(request)
        queue: asyncio.Queue = asyncio.Queue(maxsize=CLIENT_QUEUE)
        self._clients.add(queue)
        sender = asyncio.create_task(self._pump(ws, queue))
        # Plan P26 : une page qui lit un son ou une vidéo retient le micro (`media_playing`) ;
        # elle se ferme ou se coupe → le micro de CETTE connexion est relâché.
        conn = secrets.token_hex(6)
        played = False
        try:
            await ws.send_json({"type": "hello", "hud_version": hud_version(), **bus.snapshot()}, dumps=_dumps)
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    cmd = json.loads(msg.data)
                except ValueError:
                    continue
                if not isinstance(cmd, dict):
                    continue
                played = played or cmd.get("cmd") == "media_playing"
                result = await self._dispatch(cmd, from_claude, conn)
                if cmd.get("id") is not None:
                    await ws.send_json({"type": "ack", "id": cmd["id"], "result": result}, dumps=_dumps)
        finally:
            self._clients.discard(queue)
            sender.cancel()
            if played:
                await self._dispatch({"cmd": "media_playing", "playing": False, "_gone": True}, False, conn)
        return ws

    async def _pump(self, ws: web.WebSocketResponse, queue: asyncio.Queue) -> None:
        while True:
            ev = await queue.get()
            try:
                await ws.send_str(_dumps(ev))
            except (ConnectionResetError, RuntimeError):
                return

    async def _dispatch(self, cmd: dict, from_claude: bool = True, conn: str = "") -> Any:
        # Ce que le client écrit dans ces champs est écrasé : l'origine est
        # établie ici (cookie, process de la connexion), pas déclarée par lui.
        # `_conn` (plan P26) : connexion WebSocket, vide pour /api/command.
        cmd = {**cmd, "_from_claude": bool(from_claude), "_via": "hud", "_conn": conn}
        # Le gestionnaire de commandes peut bloquer (changer de voix charge un
        # modèle) : on le sort de la boucle réseau.
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, bus.command, cmd)
        except Exception as exc:
            log.exception("commande %r en échec", cmd)
            return {"ok": False, "error": str(exc)}

    # --- diffusion ---

    def _on_event(self, ev: dict) -> None:
        loop = self._loop
        if loop is None or not self._clients:
            return
        loop.call_soon_threadsafe(self._fanout, ev)

    def _fanout(self, ev: dict) -> None:
        for queue in list(self._clients):
            if queue.full():
                if ev.get("type") in DROPPABLE:
                    continue
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(ev)


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
