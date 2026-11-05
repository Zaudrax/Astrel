"""Barrière réseau du navigateur du modèle local (05/11/2026, lot 7, docs/chantiers/HYBRIDE-QWEN.md).

Le Playwright MCP du local (`playwright_local`, `mcp_web_local.declaration_playwright`) n'avait pas de vraie
barrière : `--blocked-origins` n'est « pas une frontière de sécurité » selon ses auteurs et ne couvre que la boucle
locale. Ce module en est une : un mandataire HTTP/HTTPS minimal, sur 127.0.0.1 et un port choisi par le noyau,
lancé pour la durée d'UN tour local. Chrome y envoie TOUT (`--proxy-server` + `--proxy-bypass-list=<-loopback>` :
même la boucle locale passe par ici) et chaque connexion est jugée avec les règles de `web_local`, sans recopie :
- `CONNECT hôte:port` (https, wss) : tunnel d'octets, AUCUN déchiffrement TLS ;
- `GET http://hôte/…` (http en clair) : requête relayée vers l'hôte, `Connection: close` (une requête par connexion) ;
- requête directe au mandataire (`GET / HTTP/1.1`), autre schéma, en-tête mal formé : refusés.
Pour chaque connexion : arrêt d'urgence et `hors_ligne.verifier()` ; plafonds du tour (connexions, simultanées,
octets) ; ports 80 et 443 seulement ; nom vérifié (`web_local.verifier_hote` : boucle locale, réseaux privés,
169.254, 100.64/10, IPv6 locales, IPv4 cachées dans IPv6, `.local`/`.lan`…, IPv4 écrite autrement) ; résolution
UNE fois ici (`web_local.adresses_publiques` : toutes les réponses publiques) puis connexion vers l'IP VÉRIFIÉE
(Chrome ne résout rien lui-même : pas de rebinding possible entre le contrôle et la connexion).
Mode « tout hôte public » par défaut (décision du 05/11 : Qwen a Internet en permanence) ; mode
« adresses vues » (`hotes_permis`, `permettre`) disponible : seuls les hôtes de la liste du tour passent. La
règle stricte des adresses EXACTES reste celle de `web_local.lire` (lire_page).
Journal : hôte, port, verdict, octets (jamais le chemin ni les paramètres) en mémoire et dans
`logs/proxy-local.jsonl` (0600)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Awaitable, Callable, Iterable

from . import config, hors_ligne, web_local

log = logging.getLogger("astrel.proxy_local")

HOTE_ECOUTE = "127.0.0.1"
PORTS_PERMIS = (80, 443)
# Bornes d'un tour (05/11) : une page moderne ouvre 20 à 150 connexions (Chrome les garde ouvertes et les
# réutilise) ; 30 pages par tour au plus côté lire_page.
CONNEXIONS_MAX = 600
SIMULTANEES_MAX = 48
OCTETS_MAX = 300 * 1024 * 1024        # deux sens confondus
TETE_MAX = 32 * 1024                  # en-tête d'une requête faite au mandataire
DELAI_TETE_S = 10.0
DELAI_CONNEXION_S = web_local.DELAI_CONNEXION_S
DELAI_RESOLUTION_S = 6.0
DELAI_INACTIF_S = 120.0               # aucun octet dans AUCUN des deux sens
VEILLE_S = 2.0                        # arrêt d'urgence et « rien ne sort » relus pendant les tunnels ouverts
BLOC = 65536
JOURNAL_NOM = "proxy-local.jsonl"           # dans config.LOGS (lu au lancement : les tests le déplacent)
JOURNAL_MAX = 2_000_000               # au-delà : renommé en .1
EVENEMENTS_MAX = 2000
METHODES = ("GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS", "PATCH")
# En-têtes propres à UNE connexion : jamais relayés (le mandataire ferme après chaque requête http).
_SAUT = {"connection", "proxy-connection", "keep-alive", "proxy-authorization", "proxy-authenticate", "te",
         "trailer", "upgrade", "host"}
_AUTORITE = re.compile(r"(?:\[(?P<v6>[0-9a-f:.]{2,45})\]|(?P<nom>[a-z0-9_](?:[a-z0-9_.-]{0,252})))"
                       r"(?::(?P<port>[0-9]{1,5}))?")
_JETON = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")

Ouvrir = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


class Refus(web_local.Refus):
    """Connexion refusée par le mandataire (`statut` = code HTTP rendu à Chrome)."""

    def __init__(self, message: str, statut: int = 403):
        super().__init__(message)
        self.statut = statut


def lire_autorite(texte: str, port_defaut: int | None = None, *, verifier_port: bool = True) -> tuple[str, int]:
    """« hôte[:port] » (ou « [IPv6]:port ») → (hôte en minuscules, port), ports 80/443 seulement. Strict : ni
    identifiants (@), ni chemin, ni caractère hors ASCII (Chrome envoie les noms en punycode)."""
    m = _AUTORITE.fullmatch(str(texte or "").strip().lower())
    if not m:
        raise Refus("adresse mal formée", 400)
    hote = (m["v6"] or m["nom"] or "").rstrip(".")
    port = int(m["port"]) if m["port"] else port_defaut
    if port is None:
        raise Refus("port manquant", 400)
    if verifier_port:
        verifier_port_permis(port)
    return hote, port


def verifier_port_permis(port: int) -> None:
    if port not in PORTS_PERMIS:
        raise Refus(f"port {port} refusé (80 et 443 seulement)")


def hote_de(adresse: str) -> str | None:
    """Hôte d'une adresse (« https://x.fr/a » → « x.fr ») ou d'un nom seul ; None si illisible."""
    s = str(adresse or "").strip()
    try:
        if "://" in s:
            import httpx

            return httpx.URL(web_local.normaliser(s)).host.lower().rstrip(".") or None
        return lire_autorite(s, 443)[0]
    except (web_local.Refus, ValueError):
        return None


def hotes_de(adresses: web_local.AdressesVues | Iterable[str]) -> set[str]:
    """Hôtes d'un registre `web_local.AdressesVues` (ou d'une liste d'adresses) : pour le mode « adresses vues »."""
    urls = adresses.origines if isinstance(adresses, web_local.AdressesVues) else adresses
    return {h for h in (hote_de(u) for u in urls) if h}


def _reponse(statut: int, message: str) -> bytes:
    raisons = {400: "Bad Request", 403: "Forbidden", 405: "Method Not Allowed", 429: "Too Many Requests",
               502: "Bad Gateway", 503: "Service Unavailable", 504: "Gateway Timeout"}
    corps = f"Astrel (navigateur du modèle local) : {message}\n".encode("utf-8")
    return (f"HTTP/1.1 {statut} {raisons.get(statut, 'Error')}\r\nContent-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(corps)}\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n").encode() + corps


class _Connexion:
    __slots__ = ("hote", "port", "genre", "octets", "debut", "activite", "ecrivains")

    def __init__(self):
        self.hote, self.port, self.genre = "", 0, ""
        self.octets = 0
        self.debut = self.activite = time.monotonic()
        self.ecrivains: list[asyncio.StreamWriter] = []


class Mandataire:
    """Mandataire d'UN tour : `start()` rend le port, `stop()` ferme tout (aussi en `with`). Un fil à lui (boucle
    asyncio) ; `permettre`, `bilan`, `couper` s'appellent depuis n'importe quel fil."""

    def __init__(self, *, hotes_permis: Iterable[str] | None = None, resolveur: web_local.Resolveur | None = None,
                 ouvrir: Ouvrir | None = None, connexions_max: int = CONNEXIONS_MAX,
                 simultanees_max: int = SIMULTANEES_MAX, octets_max: int = OCTETS_MAX,
                 journal: Path | None | bool = True):
        self._verrou = threading.Lock()
        self._permis: set[str] | None = None
        if hotes_permis is not None:
            self._permis = set()
            self.permettre(*hotes_permis)
        self._resoudre = resolveur
        self._ouvrir = ouvrir or (lambda ip, port: asyncio.open_connection(ip, port))
        self.connexions_max, self.simultanees_max, self.octets_max = connexions_max, simultanees_max, octets_max
        self.journal = (config.LOGS / JOURNAL_NOM) if journal is True else (Path(journal) if journal else None)
        self.port = 0
        self.connexions = 0
        self.refus = 0
        self.octets = 0
        self.plafond = ""                 # premier plafond atteint (connexions, octets)
        self.hotes: Counter[str] = Counter()
        self.evenements: list[dict] = []
        self._actives: set[_Connexion] = set()
        self._coupe = ""                  # raison d'une coupure générale (arrêt d'urgence, hors ligne, couper())
        self._loop: asyncio.AbstractEventLoop | None = None
        self._fil: threading.Thread | None = None
        self._serveur: asyncio.base_events.Server | None = None

    # ---------------------------------------------------------------- cycle de vie

    @property
    def adresse(self) -> str:
        return f"http://{HOTE_ECOUTE}:{self.port}"

    @property
    def mode(self) -> str:
        return "public" if self._permis is None else "vues"

    def start(self) -> int:
        if self._fil is not None:
            return self.port
        pret, erreur = threading.Event(), []

        def tourner():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                self._serveur = loop.run_until_complete(
                    asyncio.start_server(self._client, HOTE_ECOUTE, 0, limit=TETE_MAX))
                self.port = self._serveur.sockets[0].getsockname()[1]
                veille = loop.create_task(self._veiller())
            except Exception as exc:  # port impossible à ouvrir : rendu à start()
                erreur.append(exc)
                pret.set()
                loop.close()
                return
            self._loop = loop
            pret.set()
            try:
                loop.run_forever()
            finally:
                veille.cancel()
                restantes = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for t in restantes:
                    t.cancel()
                loop.run_until_complete(asyncio.gather(*restantes, return_exceptions=True))
                loop.close()

        self._fil = threading.Thread(target=tourner, name="astrel-proxy-local", daemon=True)
        self._fil.start()
        pret.wait(10)
        if erreur or not self.port:
            self._fil = None
            raise RuntimeError(f"mandataire du navigateur local impossible à lancer : {erreur[:1]}")
        log.info("mandataire du navigateur local sur %s (mode %s)", self.adresse, self.mode)
        return self.port

    def stop(self) -> None:
        loop, fil = self._loop, self._fil
        if loop is None or fil is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._fermer("mandataire arrêté"), loop).result(5)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)
        fil.join(5)
        self._loop = self._fil = None

    def __enter__(self) -> "Mandataire":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def couper(self, raison: str = "coupé par Astrel") -> None:
        """Ferme toutes les connexions ouvertes et refuse les suivantes (Stop tout, « rien ne sort »)."""
        self._coupe = raison
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._fermer_actives)

    # ---------------------------------------------------------------- mode « adresses vues »

    def permettre(self, *adresses: str) -> int:
        """Ajoute des hôtes (adresses ou noms) à la liste du tour ; sans effet en mode « tout hôte public »."""
        if self._permis is None:
            return 0
        n = 0
        with self._verrou:
            for a in adresses:
                h = hote_de(a)
                if h and h not in self._permis:
                    self._permis.add(h)
                    n += 1
        return n

    def bilan(self) -> dict:
        return {"adresse": self.adresse, "mode": self.mode, "connexions": self.connexions, "refus": self.refus,
                "octets": self.octets, "plafond": self.plafond, "hotes": dict(self.hotes)}

    # ---------------------------------------------------------------- une connexion de Chrome

    async def _client(self, lecteur: asyncio.StreamReader, ecrivain: asyncio.StreamWriter) -> None:
        cnx = _Connexion()
        cnx.ecrivains.append(ecrivain)
        self._actives.add(cnx)
        verdict, raison = "ok", ""
        try:
            try:
                tete = await asyncio.wait_for(lecteur.readuntil(b"\r\n\r\n"), DELAI_TETE_S)
            except (asyncio.IncompleteReadError, ConnectionError):
                return  # connexion ouverte puis fermée sans requête (Chrome le fait) : rien à noter
            except asyncio.LimitOverrunError:
                raise Refus("en-tête trop long", 400) from None
            except asyncio.TimeoutError:
                raise Refus("requête trop lente", 400) from None
            self._admettre()
            methode, cible, entetes = self._analyser(tete)
            # Le NOM d'abord (refus le plus parlant, et l'hôte au journal), le port ensuite.
            if methode == "CONNECT":
                cnx.genre = "tunnel"
                cnx.hote, cnx.port = lire_autorite(cible, verifier_port=False)
            else:
                cnx.genre = "http"
                cnx.hote, cnx.port, chemin = self._cible_http(cible)
            self._juger_hote(cnx.hote)
            verifier_port_permis(cnx.port)
            ips = await self._resoudre_verifie(cnx.hote, cnx.port)
            amont_l, amont = await self._connecter(ips, cnx.port)
            cnx.ecrivains.append(amont)
            if methode == "CONNECT":
                ecrivain.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            else:
                amont.write(self._requete(methode, chemin, cnx.hote, cnx.port, entetes))
            await asyncio.gather(ecrivain.drain(), amont.drain())
            self.hotes[cnx.hote] += 1
            await self._relayer(lecteur, ecrivain, amont_l, amont, cnx)
        except (Refus, web_local.Refus, hors_ligne.HorsLigne) as exc:
            verdict, raison = "refus", str(exc)[:200]
            self.refus += 1
            statut = getattr(exc, "statut", 403)
            try:
                ecrivain.write(_reponse(statut, raison))
                await asyncio.wait_for(ecrivain.drain(), 2)
            except Exception:
                pass
        except asyncio.CancelledError:
            verdict, raison = "coupe", self._coupe or "mandataire arrêté"
            raise
        except Exception as exc:  # jamais un fil tombé pour une connexion
            verdict, raison = "echec", type(exc).__name__
        finally:
            if verdict == "ok" and self._coupe:
                verdict, raison = "coupe", self._coupe
            self._actives.discard(cnx)
            for w in cnx.ecrivains:
                try:
                    w.close()
                except Exception:
                    pass
            if cnx.genre or verdict != "ok":
                self._noter(cnx, verdict, raison)

    def _admettre(self) -> None:
        """Avant tout : arrêt d'urgence, « rien ne sort », coupure, plafonds du tour."""
        if config.ESTOP_FILE.exists():
            raise Refus("arrêt d'urgence posé : rien ne sort", 503)
        hors_ligne.verifier("navigateur du modèle local")
        if self._coupe:
            raise Refus(self._coupe, 503)
        if len(self._actives) > self.simultanees_max:
            raise Refus(f"plus de {self.simultanees_max} connexions ouvertes à la fois", 429)
        if self.octets >= self.octets_max:
            self.plafond = self.plafond or "octets"
            raise Refus(f"plafond du tour atteint ({self.octets_max // (1024 * 1024)} Mio)", 429)
        if self.connexions >= self.connexions_max:
            self.plafond = self.plafond or "connexions"
            raise Refus(f"plafond du tour atteint ({self.connexions_max} connexions)", 429)
        self.connexions += 1

    @staticmethod
    def _analyser(tete: bytes) -> tuple[str, str, list[tuple[str, str]]]:
        try:
            texte = tete.decode("ascii")
        except UnicodeDecodeError:
            raise Refus("requête mal formée (octets hors ASCII)", 400) from None
        lignes = texte[:-4].split("\r\n")
        parties = lignes[0].split(" ")
        if len(parties) != 3 or not parties[2].startswith("HTTP/1.") or not _JETON.fullmatch(parties[0]):
            raise Refus("requête mal formée", 400)
        methode, cible = parties[0].upper(), parties[1]
        if not cible or any(ord(c) < 33 or ord(c) > 126 for c in cible):
            raise Refus("requête mal formée", 400)
        if methode != "CONNECT" and methode not in METHODES:
            raise Refus(f"méthode {methode[:20]} refusée", 405)
        entetes: list[tuple[str, str]] = []
        for ligne in lignes[1:]:
            nom, deux, valeur = ligne.partition(":")
            if not deux or not _JETON.fullmatch(nom) or any(ord(c) < 32 and c != "\t" for c in valeur):
                raise Refus("en-tête mal formé", 400)
            entetes.append((nom, valeur.strip()))
        noms = {n.lower() for n, _ in entetes}
        if "content-length" in noms and "transfer-encoding" in noms:
            raise Refus("en-têtes de longueur contradictoires", 400)
        return methode, cible, entetes

    @staticmethod
    def _cible_http(cible: str) -> tuple[str, int, str]:
        """Forme absolue « http://hôte[:port]/chemin » (la seule que Chrome envoie en clair à un mandataire)."""
        m = re.match(r"(?i)http://([^/?#]*)([^#]*)", cible)
        if not m:
            if cible.startswith("/"):
                raise Refus("requête directe au mandataire refusée", 400)
            raise Refus("seules les adresses http:// passent en clair (https : par CONNECT)", 400)
        hote, port = lire_autorite(m.group(1), 80, verifier_port=False)
        if port in PORTS_PERMIS:
            try:  # mêmes règles d'adresse que lire_page (schéma, identifiants) ; le port est jugé après le nom
                web_local.normaliser(cible)
            except web_local.Refus:
                raise Refus("adresse refusée", 400) from None
        chemin = m.group(2) or "/"
        if chemin.startswith("?"):
            chemin = "/" + chemin
        return hote, port, chemin

    def _juger_hote(self, hote: str) -> None:
        web_local.verifier_hote(hote)
        if self._permis is not None:
            with self._verrou:
                vu = hote in self._permis
            if not vu:
                raise Refus(f"{hote} : site jamais vu dans ce tour (mode « adresses vues »)")

    async def _resoudre_verifie(self, hote: str, port: int) -> list[str]:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(web_local.adresses_publiques, hote, port, self._resoudre), DELAI_RESOLUTION_S)
        except asyncio.TimeoutError:
            raise Refus(f"nom introuvable à temps : {hote}", 504) from None

    async def _connecter(self, ips: list[str], port: int):
        derniere = "injoignable"
        for ip in ips:
            if not web_local.ip_publique(ip):  # déjà vérifié : seconde garde, l'IP est celle qu'on joint
                raise Refus(f"adresse locale ou privée refusée : {ip}")
            hors_ligne.verifier("navigateur du modèle local")
            try:
                return await asyncio.wait_for(self._ouvrir(ip, port), DELAI_CONNEXION_S)
            except asyncio.TimeoutError:
                derniere = "délai de connexion dépassé"
            except OSError as exc:
                derniere = type(exc).__name__
        raise Refus(f"site injoignable ({derniere})", 502)

    @staticmethod
    def _requete(methode: str, chemin: str, hote: str, port: int, entetes: list[tuple[str, str]]) -> bytes:
        nommes = set()
        for nom, valeur in entetes:
            if nom.lower() == "connection":
                nommes |= {v.strip().lower() for v in valeur.split(",")}
        h = f"[{hote}]" if ":" in hote else hote
        autorite = h if port == 80 else f"{h}:{port}"
        lignes = [f"{methode} {chemin} HTTP/1.1", f"Host: {autorite}"]
        lignes += [f"{n}: {v}" for n, v in entetes if n.lower() not in _SAUT and n.lower() not in nommes]
        lignes.append("Connection: close")
        return ("\r\n".join(lignes) + "\r\n\r\n").encode("latin-1")

    async def _relayer(self, lecteur, ecrivain, amont_l, amont, cnx: _Connexion) -> None:
        """Les deux sens en même temps ; fin dès qu'un côté ferme (HTTP : une requête par connexion)."""
        taches = [asyncio.ensure_future(self._pomper(lecteur, amont, cnx)),
                  asyncio.ensure_future(self._pomper(amont_l, ecrivain, cnx))]
        try:
            _fini, reste = await asyncio.wait(taches, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in taches:
                t.cancel()
            await asyncio.gather(*taches, return_exceptions=True)

    async def _pomper(self, src: asyncio.StreamReader, dst: asyncio.StreamWriter, cnx: _Connexion) -> None:
        while True:
            try:
                bloc = await asyncio.wait_for(src.read(BLOC), VEILLE_S * 5)
            except asyncio.TimeoutError:
                if time.monotonic() - cnx.activite < DELAI_INACTIF_S:
                    continue  # l'autre sens travaille (téléchargement long) : on attend
                return
            except (ConnectionError, OSError):
                return
            if not bloc:
                return
            cnx.activite = time.monotonic()
            cnx.octets += len(bloc)
            self.octets += len(bloc)
            if self.octets > self.octets_max:
                self.plafond = self.plafond or "octets"
                return
            dst.write(bloc)
            try:
                await dst.drain()
            except (ConnectionError, OSError):
                return

    # ---------------------------------------------------------------- coupure, veille, journal

    def _fermer_actives(self) -> None:
        for cnx in list(self._actives):
            for w in cnx.ecrivains:
                try:
                    w.close()
                except Exception:
                    pass

    async def _fermer(self, raison: str) -> None:
        self._coupe = self._coupe or raison
        if self._serveur is not None:
            self._serveur.close()
        self._fermer_actives()

    async def _veiller(self) -> None:
        """Arrêt d'urgence ou « rien ne sort » posé PENDANT le tour : les tunnels déjà ouverts sont fermés."""
        while True:
            await asyncio.sleep(VEILLE_S)
            if not self._actives:
                continue
            try:
                if config.ESTOP_FILE.exists():
                    self._coupe = "arrêt d'urgence posé : rien ne sort"
                elif hors_ligne.actif():
                    self._coupe = hors_ligne.REFUS
                else:
                    continue
            except Exception:
                continue
            self._fermer_actives()

    def _noter(self, cnx: _Connexion, verdict: str, raison: str) -> None:
        """Hôte, port, verdict, octets, durée : JAMAIS le chemin ni les paramètres d'une adresse."""
        evt = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "hote": cnx.hote[:253], "port": cnx.port,
               "genre": cnx.genre or "?", "verdict": verdict, "octets": cnx.octets,
               "duree_s": round(time.monotonic() - cnx.debut, 2)}
        if raison:
            evt["raison"] = raison
        if len(self.evenements) < EVENEMENTS_MAX:
            self.evenements.append(evt)
        if verdict == "refus":
            log.info("navigateur local : %s:%s refusé (%s)", cnx.hote or "?", cnx.port, raison)
        if self.journal is None:
            return
        try:
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            if self.journal.exists() and self.journal.stat().st_size > JOURNAL_MAX:
                os.replace(self.journal, self.journal.with_suffix(".jsonl.1"))
            fd = os.open(self.journal, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(json.dumps(evt, ensure_ascii=False) + "\n")
        except OSError:
            pass


def demarrer(**options) -> Mandataire:
    """Mandataire lancé, prêt pour un tour : `with demarrer() as m: … m.port …` (ou `m.stop()` à la fin)."""
    m = Mandataire(**options)
    m.start()
    return m


if __name__ == "__main__":  # essai à la main : python -m astrel.proxy_local (Ctrl+C pour finir)
    logging.basicConfig(level=logging.INFO)
    with demarrer() as _m:
        print(_m.adresse, flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            print(json.dumps(_m.bilan(), ensure_ascii=False))
