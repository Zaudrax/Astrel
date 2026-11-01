"""Passerelle locale vers un fournisseur à clé API (20/10, plan P11, lot 2).

Le CLI `claude` parle l'API Messages d'Anthropic ; cette passerelle la traduit vers
OpenAI, Gemini, OpenRouter ou Grok (xAI) avec la BIBLIOTHÈQUE LiteLLM (`litellm.anthropic.messages`,
la même traduction que son proxy, sans ses 700 Mo de dépendances). Elle tourne dans
une venv À PART (`config.PASSERELLE_VENV`, sous /data chez l'utilisateur), avec le Python de
cette venv : ce fichier n'importe donc RIEN de `astrel` (bibliothèque standard,
litellm et aiohttp seulement). Lancée et arrêtée par `astrel/passerelle.py`.

Sécurité :
- écoute 127.0.0.1 seulement, sur un port libre choisi par le système (annoncé sur
  stdout : « PORT <n> ») ;
- chaque requête doit porter le jeton local (en-tête x-api-key ou Authorization), lu
  sur l'entrée standard au démarrage : ni ligne de commande, ni environnement ;
- la clé du fournisseur est lue dans le fichier des secrets (son NOM seul est passé en
  argument) et gardée en mémoire : jamais dans l'environnement, la ligne de commande
  ou un journal (tout message d'erreur est masqué).
"""
from __future__ import annotations

import os
import sys

# Lancé comme script, son dossier (astrel/) passerait devant la bibliothèque standard :
# `astrel/platform` masquerait `platform` (vu le 20/10 : aiohttp ne s'importait plus).
# Le lanceur passe aussi `-I` ; ceci couvre un lancement à la main.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.curdir) != _HERE]

import argparse  # noqa: E402
import asyncio  # noqa: E402
import hashlib  # noqa: E402
import hmac  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402

# Pas de téléchargement de la table des prix au démarrage (réseau inutile, lent).
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

PREFIXES = {"openai": "openai/", "gemini": "gemini/", "openrouter": "openrouter/",
            # Grok (21/10, plan P15) : clé API seulement ; LiteLLM ne prend sa connexion OAuth
            # (abonnement) que sur `use_xai_oauth`, jamais passé ici.
            "xai": "xai/",
            # Grok par abonnement (absent de ce dépôt) : même traduction, le jeton
            # (`--oauth`, relu à chaque requête) passé en `api_key`, jamais `use_xai_oauth`.
            "xaiabo": "xai/",
            # Banc d'essai seulement (tools/banc_passerelle.py) : un modèle local d'Ollama, avec un
            # contexte assez grand pour le prompt du CLI. Pas proposé dans les Réglages (mode léger).
            "ollama": "ollama_chat/"}
OLLAMA_NUM_CTX = 32768
MAX_BODY = 64 * 1024 * 1024  # une conversation avec images peut être grosse
# Mode `--oauth` (plan P16) : sur un 401, le cœur (seul rafraîchisseur) reçoit `XAI_REFUSE 401`
# sur notre stderr, rafraîchit et réécrit le fichier ; on attend le NOUVEAU jeton au plus
# REJEU_ATTENTE_S, puis on rejoue UNE fois. Nous ne rafraîchissons jamais nous-mêmes.
REJEU_ATTENTE_S = 20.0
MARQUEUR_REFUS = "XAI_REFUSE"


def _say(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


class Passerelle:
    def __init__(self, fournisseur: str, modele: str, cle: str, jeton: str, base: str = "",
                 oauth: tuple[str, str] | None = None) -> None:
        """`oauth` : (fichier des secrets, nom de l'objet OAuth) ; le jeton d'accès y est RELU à
        chaque requête (le cœur le rafraîchit), `cle` n'est alors que le premier lu."""
        if fournisseur not in PREFIXES:
            raise ValueError(f"fournisseur inconnu : {fournisseur}")
        self.fournisseur = fournisseur
        self.modele = PREFIXES[fournisseur] + modele
        self.cle, self.jeton, self.base = cle, jeton, base
        self.oauth = oauth
        self.etat = ""  # fichier d'état NON secret du cœur (`--etat`) : « refusé » arrête l'attente du rejeu
        self._vus: list[str] = []  # jetons OAuth déjà lus (masqués dans tout message), en mémoire seulement
        self.requetes = 0

    def cle_courante(self) -> str:
        """Clé à envoyer maintenant : la clé fixe, ou le jeton OAuth relu ("" : connexion absente)."""
        if not self.oauth:
            return self.cle
        try:
            jeton = lire_jeton_oauth(*self.oauth)
        except (OSError, ValueError):
            jeton = ""
        if jeton and jeton not in self._vus:
            self._vus = (self._vus + [jeton])[-4:]
        return jeton

    def masque(self, text: object) -> str:
        """Texte sans la clé ni le jeton (messages d'erreur du fournisseur ou de LiteLLM)."""
        out = str(text)
        for secret in (self.cle, self.jeton, *self._vus):
            if secret:
                out = out.replace(secret, "***")
        return " ".join(out.split())[:500]

    def autorise(self, headers) -> bool:
        given = headers.get("x-api-key") or ""
        auth = headers.get("Authorization") or ""
        if not given and auth.lower().startswith("bearer "):
            given = auth[7:]
        return bool(self.jeton) and hmac.compare_digest(given.strip().encode(), self.jeton.encode())

    def appel(self, body: dict, cle: str | None = None) -> dict:
        """Arguments de `litellm.anthropic.messages.acreate` : le modèle demandé par le CLI
        (alias sonnet, haiku…) est TOUJOURS remplacé par celui choisi dans Astrel."""
        kwargs = {k: v for k, v in body.items() if k not in ("model", "stream")}
        kwargs.setdefault("max_tokens", 4096)
        kwargs.update(model=self.modele, api_key=self.cle if cle is None else cle, stream=bool(body.get("stream")))
        if self.base:
            kwargs["api_base"] = self.base
        if self.fournisseur == "ollama":
            kwargs["num_ctx"] = OLLAMA_NUM_CTX
        if "thinking" in kwargs and not self.reflechit():
            # Le CLI demande la réflexion ; un modèle qui ne sait pas la faire répond 400
            # (vu au banc du 20/10 : « does not support thinking »).
            kwargs.pop("thinking")
        return kwargs

    def reflechit(self) -> bool:
        import litellm

        try:
            return bool(litellm.supports_reasoning(model=self.modele))
        except Exception:  # noqa: BLE001 — modèle inconnu de LiteLLM : sans réflexion
            return False


def _erreur(status: int, message: str):
    from aiohttp import web

    kind = {400: "invalid_request_error", 401: "authentication_error", 403: "permission_error",
            404: "not_found_error", 429: "rate_limit_error"}.get(status, "api_error")
    return web.json_response({"type": "error", "error": {"type": kind, "message": message}}, status=status)


def application(p: Passerelle):
    import litellm
    from aiohttp import web

    litellm.suppress_debug_info = True
    litellm.drop_params = True  # paramètre propre à Anthropic inconnu du fournisseur : retiré
    # OpenAI passe sinon par son API « Responses » : on garde chat/completions partout, le
    # chemin le plus ancien de LiteLLM, celui que le banc d'essai a mesuré (20/10).
    litellm.use_chat_completions_url_for_anthropic_messages = True
    try:
        litellm.telemetry = False
    except Exception:  # noqa: BLE001 — selon la version
        pass

    async def messages(request: web.Request) -> web.StreamResponse:
        if not p.autorise(request.headers):
            return _erreur(401, "jeton local de la passerelle absent ou faux")
        try:
            body = await request.json()
        except ValueError:
            return _erreur(400, "corps JSON illisible")
        if not isinstance(body, dict):
            return _erreur(400, "corps JSON illisible")
        p.requetes += 1
        t0 = time.monotonic()
        stream = bool(body.get("stream"))
        cle = p.cle_courante()
        if p.oauth and not cle:
            return _erreur(401, "connexion xAI absente ou effacée : Réglages › Fournisseur")
        essai = 0
        while True:
            essai += 1
            try:
                resp = await litellm.anthropic.messages.acreate(**p.appel(body, cle))
                break
            except Exception as exc:  # noqa: BLE001 — rendue au CLI en erreur Anthropic, sans la clé
                status = int(getattr(exc, "status_code", 0) or 502)
                _say(f"requête {p.requetes} : erreur {status} ({type(exc).__name__})")
                if p.oauth and status in (401, 403):
                    # Le cœur rafraîchit (401) ou passe en « refusé » ; l'empreinte (jamais le jeton) lui
                    # dit QUEL jeton a été refusé : un refus d'un jeton déjà remplacé est ignoré.
                    _say(f"{MARQUEUR_REFUS} {status} {empreinte(cle)}")
                    if status == 401 and essai == 1:
                        nouveau = await _nouveau_jeton(p, cle)
                        if nouveau:
                            cle = nouveau
                            continue  # UN rejeu, avec le jeton rafraîchi par le cœur
                return _erreur(status if 400 <= status < 600 else 502, p.masque(exc))
        if not stream:
            data = resp if isinstance(resp, dict) else getattr(resp, "model_dump", lambda: resp)()
            _say(f"requête {p.requetes} : {time.monotonic() - t0:.1f} s")
            return web.json_response(data, dumps=lambda d: json.dumps(d, default=str))
        out = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await out.prepare(request)
        try:
            async for chunk in resp:
                if isinstance(chunk, (bytes, bytearray)):
                    raw = bytes(chunk)
                elif isinstance(chunk, str):
                    raw = chunk.encode()
                else:
                    ev = chunk if isinstance(chunk, dict) else getattr(chunk, "model_dump", lambda: {})()
                    raw = f"event: {ev.get('type', 'message')}\ndata: {json.dumps(ev, default=str)}\n\n".encode()
                await out.write(raw)
        except (ConnectionResetError, asyncio.CancelledError):
            raise
        except Exception as exc:  # noqa: BLE001 — erreur en plein flux : un évènement d'erreur
            _say(f"requête {p.requetes} : erreur en flux ({type(exc).__name__})")
            err = {"type": "error", "error": {"type": "api_error", "message": p.masque(exc)}}
            await out.write(f"event: error\ndata: {json.dumps(err)}\n\n".encode())
        _say(f"requête {p.requetes} : flux de {time.monotonic() - t0:.1f} s")
        await out.write_eof()
        return out

    async def count_tokens(request: web.Request) -> web.Response:
        """Estimation (4 caractères ≈ 1 token) : le CLI s'en sert pour jauger le contexte."""
        if not p.autorise(request.headers):
            return _erreur(401, "jeton local de la passerelle absent ou faux")
        raw = await request.read()
        return web.json_response({"input_tokens": max(1, len(raw) // 4)})

    async def sante(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "fournisseur": p.fournisseur, "requetes": p.requetes})

    app = web.Application(client_max_size=MAX_BODY)
    app.router.add_post("/v1/messages", messages)
    app.router.add_post("/v1/messages/count_tokens", count_tokens)
    app.router.add_get("/sante", sante)
    return app


def lire_cle(secrets: str, nom: str) -> str:
    with open(secrets, encoding="utf-8") as fh:
        data = json.load(fh)
    return str((data or {}).get(nom) or "").strip() if isinstance(data, dict) else ""


def lire_jeton_oauth(secrets: str, nom: str) -> str:
    """Jeton d'accès de l'objet OAuth `nom` ({access_token, refresh_token…}) ; "" s'il manque.
    Le refresh token n'est jamais lu ici : seul le cœur rafraîchit."""
    with open(secrets, encoding="utf-8") as fh:
        data = json.load(fh)
    obj = data.get(nom) if isinstance(data, dict) else None
    return str(obj.get("access_token") or "").strip() if isinstance(obj, dict) else ""


def empreinte(jeton: str) -> str:
    """Même calcul que `xai_oauth._empreinte` : 16 caractères de sha256, pas le jeton."""
    return hashlib.sha256(jeton.encode()).hexdigest()[:16]


def refuse_par_le_coeur(etat: str) -> bool:
    """Le cœur a conclu « refusé » (fichier d'état NON secret de `xai_oauth`) : inutile d'attendre."""
    if not etat:
        return False
    try:
        with open(etat, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("etat") == "refuse"


async def _nouveau_jeton(p: Passerelle, ancien: str) -> str:
    """Attend que le cœur ait écrit un AUTRE jeton (rafraîchissement) ; "" : rien de neuf."""
    fin = time.monotonic() + REJEU_ATTENTE_S
    while time.monotonic() < fin:
        await asyncio.sleep(0.2)
        jeton = p.cle_courante()
        if not jeton:
            return ""  # effacé (refus, déconnexion) : pas de rejeu
        if jeton != ancien:
            return jeton
        if refuse_par_le_coeur(p.etat):
            return ""
    return ""


async def _serve(p: Passerelle, port: int) -> None:
    from aiohttp import web

    runner = web.AppRunner(application(p), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    real = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    print(f"PORT {real}", flush=True)
    # Fin quand le parent ferme notre entrée standard (le cœur meurt, ou `stop`).
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, sys.stdin.read)
    await runner.cleanup()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Passerelle locale d'Astrel (API Messages → fournisseur à clé)")
    ap.add_argument("--fournisseur", required=True, choices=sorted(PREFIXES))
    ap.add_argument("--modele", required=True)
    ap.add_argument("--secrets", required=True, help="fichier JSON des secrets (config/secrets.json)")
    ap.add_argument("--cle", required=True, help="NOM de la clé dans ce fichier (jamais sa valeur)")
    ap.add_argument("--base", default="", help="adresse du fournisseur (tests, serveur compatible)")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--oauth", action="store_true",
                    help="`--cle` nomme un objet OAuth dont le jeton d'accès est relu à chaque requête (plan P16)")
    ap.add_argument("--etat", default="", help="fichier d'état NON secret de la connexion OAuth (plan P16)")
    args = ap.parse_args(argv)
    jeton = sys.stdin.readline().strip()
    if len(jeton) < 16:
        _say("jeton local absent")
        return 2
    try:
        cle = lire_jeton_oauth(args.secrets, args.cle) if args.oauth else lire_cle(args.secrets, args.cle)
    except (OSError, ValueError) as exc:
        _say(f"fichier des secrets illisible ({type(exc).__name__})")
        return 2
    if not cle:
        _say(f"clé {args.cle} absente du fichier des secrets")
        return 2
    p = Passerelle(args.fournisseur, args.modele, cle, jeton, args.base,
                   oauth=(args.secrets, args.cle) if args.oauth else None)
    if args.oauth:
        p._vus, p.etat = [cle], args.etat
    try:
        asyncio.run(_serve(p, args.port))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
