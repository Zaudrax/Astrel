"""Moteur local (04/11/2026, lot 1, docs/chantiers/HYBRIDE-QWEN.md) : Qwen par Ollama, EN DIRECT (/api/chat),
pour une demande que l'utilisateur veut traiter sur sa machine. Aucun outil, rien n'est envoyé ailleurs.

Différent du mode léger (`modeles_locaux`, plan P8), qui REMPLACE le cerveau Claude par le CLI branché sur
Ollama : ici Claude reste vivant, la demande locale passe à côté. Choix mesurés au lot 0 (RTX 4060, 8 Go) :
- `num_ctx` FIXE (un autre contexte recharge le modèle, ~9 s) ;
- `num_gpu` = toutes les couches sur la carte : ×4,5 à 32 k (Ollama, trop prudent, en laisse sur le CPU) ;
  sur une erreur de mémoire de la carte, repli sur le réglage d'Ollama pour le reste de la session ;
- réflexion (`think`) coupée : réponses dites, attente courte ; `ThinkFilter` en filet.
L'historique de la conversation locale vit EN MÉMOIRE seulement (jamais sur disque) et est borné."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from typing import Any, Callable, Iterator
from urllib.parse import urlparse

from . import config

log = logging.getLogger(__name__)

MODELE_DEFAUT = "qwen3.5:9b"
NUM_CTX = 32_768
NUM_GPU_TOUT = 999         # « toutes les couches » : Ollama borne au nombre réel
KEEP_ALIVE = "30m"         # gardé chargé une demi-heure après la dernière demande
HISTORIQUE_MAX = 12        # messages (6 échanges)
HISTORIQUE_CARS = 60_000   # et ~15 k tokens au plus : la place reste pour la demande et la réponse
RAM_MIN_GO = 1.5           # RAM disponible exigée pour CHARGER le modèle (déjà chargé : rien à vérifier)
DELAI_LECTURE_S = 300.0
_HOTES_LOCAUX = {"127.0.0.1", "localhost", "::1"}
_MEMOIRE_GPU = ("out of memory", "cudamalloc", "cuda error", "insufficient memory", "unable to allocate",
                "memory layout cannot be allocated")

SYSTEME = {
    "fr": ("Tu es Astrel, l'assistant de l'utilisateur, en mode LOCAL : tu tournes sur sa machine (Qwen par Ollama), "
           "tu n'as aucun outil, aucun accès au web ni aux fichiers, et rien de cette conversation ne sort de la "
           "machine. Réponds en français, clairement, en phrases complètes : ta réponse peut être dite à voix "
           "haute. Explique ton raisonnement quand c'est utile. Tu peux te tromper : dis-le quand tu n'es pas "
           "sûr, et propose comment vérifier (test, outil, source). En cybersécurité, tu expliques pour "
           "apprendre, défendre et tester ce que l'utilisateur a le droit de tester ; tu ne donnes pas de quoi nuire "
           "à un système qui n'est pas le sien."),
    "en": ("You are Astrel, the user's assistant, in LOCAL mode: you run on his machine (Qwen through Ollama), "
           "you have no tools, no web or file access, and nothing in this conversation leaves the machine. "
           "Answer in English, clearly, in full sentences: your answer may be read aloud. Explain your "
           "reasoning when useful. You can be wrong: say so when unsure and suggest how to check (test, tool, "
           "source). In security, you explain to learn, defend and test what the user is allowed to test; you "
           "never give what would harm a system that is not his."),
}


class ErreurLocale(Exception):
    """Refus ou panne du moteur local : le message (en français) est montré à l'utilisateur."""


def url_locale(url: str) -> bool:
    """L'adresse d'Ollama est-elle sur CETTE machine ? Une adresse distante ferait sortir la demande."""
    try:
        p = urlparse(url)
    except ValueError:
        return False
    return p.scheme in ("http", "https") and (p.hostname or "") in _HOTES_LOCAUX


def modele() -> str:
    """Modèle du moteur local : `ASTREL_MOTEUR_LOCAL`, sinon celui du mode léger s'il est choisi, sinon qwen3.5:9b."""
    choisi = os.environ.get("ASTREL_MOTEUR_LOCAL", "").strip()
    if not choisi:
        try:
            from .modeles_locaux import local

            choisi = local.chosen
        except Exception:
            choisi = ""
    return choisi or MODELE_DEFAUT


def refus_modele(tag: str) -> str:
    """Raison de refuser ce modèle, ou "" : un tag `:cloud` d'Ollama s'exécute chez Ollama, pas ici."""
    t = str(tag or "").strip().lower()
    if not t:
        return "aucun modèle local choisi"
    if t.endswith(":cloud") or "-cloud" in t.rpartition(":")[2]:
        return f"{tag} est un modèle en ligne d'Ollama : la demande sortirait de la machine"
    return ""


def _memoire_gpu(texte: str) -> bool:
    t = str(texte or "").lower()
    return any(m in t for m in _MEMOIRE_GPU)


class MoteurLocal:
    """Un client par cœur. `repondre` est appelée par UN tour local à la fois (verrou du mixin)."""

    def __init__(self, url: str | None = None, tag: str | None = None,
                 client: Callable[[], Any] | None = None,
                 memoire: Callable[[], dict] | None = None) -> None:
        self.url = (url or config.OLLAMA_URL).rstrip("/")
        self._tag = tag
        self._client = client
        self._memoire = memoire
        self._num_gpu = True       # toutes les couches sur la carte, jusqu'à une erreur de mémoire
        self._hist: deque[dict[str, str]] = deque()
        self._lock = threading.Lock()
        self.derniere: dict[str, Any] = {}   # mesures du dernier tour (premier mot, vitesse)

    @property
    def tag(self) -> str:
        return self._tag or modele()

    # --- historique (mémoire seulement) ---

    def oublier(self) -> None:
        with self._lock:
            self._hist.clear()

    def _garder(self, demande: str, reponse: str) -> None:
        with self._lock:
            self._hist.append({"role": "user", "content": demande})
            self._hist.append({"role": "assistant", "content": reponse})
            while len(self._hist) > HISTORIQUE_MAX or (
                    self._hist and sum(len(m["content"]) for m in self._hist) > HISTORIQUE_CARS):
                self._hist.popleft()
            while self._hist and self._hist[0]["role"] != "user":
                self._hist.popleft()

    # --- vérifications avant d'envoyer ---

    def _verifier_cible(self) -> None:
        """Avant même d'ouvrir une connexion : adresse sur cette machine, modèle qui tourne ici."""
        url = self.url
        if not url_locale(url):
            raise ErreurLocale(f"adresse d'Ollama hors de cette machine ({url}) : refusé, rien n'est envoyé")
        raison = refus_modele(self.tag)
        if raison:
            raise ErreurLocale(raison)

    def _verifier_memoire(self, http: Any) -> None:
        if self._charge(http):
            return
        try:
            s = (self._memoire or _echantillon)()
        except Exception:
            return  # mesure impossible : Ollama dira lui-même s'il manque de place
        dispo = float(s.get("ram_dispo") or 0) / 2**30
        if dispo and dispo < RAM_MIN_GO:
            tag, mini = self.tag, f"{RAM_MIN_GO:.1f}"
            raise ErreurLocale(f"mémoire trop basse pour charger {tag} ({dispo:.1f} Go disponibles, "
                               f"{mini} Go au moins) : ferme une application et réessaie")

    def _charge(self, http: Any) -> bool:
        """Le modèle est-il déjà chargé dans Ollama ? (rien à vérifier côté mémoire)."""
        try:
            r = http.get(self.url + "/api/ps", timeout=3.0)
            noms = {str(m.get("name") or m.get("model") or "") for m in (r.json().get("models") or [])}
        except Exception:
            return False
        return self.tag in noms

    # --- tour ---

    def corps(self, demande: str, langue: str = "fr") -> dict[str, Any]:
        with self._lock:
            hist = list(self._hist)
        options: dict[str, Any] = {"num_ctx": NUM_CTX}
        if self._num_gpu:
            options["num_gpu"] = NUM_GPU_TOUT
        return {"model": self.tag, "stream": True, "think": False, "keep_alive": KEEP_ALIVE, "options": options,
                "messages": [{"role": "system", "content": SYSTEME.get(langue, SYSTEME["fr"])}, *hist,
                             {"role": "user", "content": demande}]}

    def repondre(self, demande: str, langue: str = "fr", stop: threading.Event | None = None) -> Iterator[str]:
        """Morceaux de la réponse (réflexion retirée), au fil de l'eau. Lève `ErreurLocale`."""
        from .brain import ThinkFilter

        self._verifier_cible()
        http = (self._client or _client_http)()
        try:
            self._verifier_memoire(http)
            for essai in (1, 2):
                filtre, morceaux, t0 = ThinkFilter(), [], time.monotonic()
                premier = None
                try:
                    for ligne in self._flux(http, self.corps(demande, langue)):
                        if stop is not None and stop.is_set():
                            break
                        if ligne.get("error"):
                            raise ErreurLocale(str(ligne["error"]))
                        texte = filtre.feed(str((ligne.get("message") or {}).get("content") or ""))
                        if texte:
                            if premier is None:
                                premier = time.monotonic() - t0
                            morceaux.append(texte)
                            yield texte
                        if ligne.get("done"):
                            self._mesurer(ligne, premier)
                            break
                except ErreurLocale as exc:
                    if essai == 1 and not morceaux and self._num_gpu and _memoire_gpu(str(exc)):
                        log.warning("moteur local : mémoire de la carte insuffisante avec toutes les couches "
                                    "(%s) : repli sur le réglage d'Ollama", exc)
                        self._num_gpu = False
                        continue
                    raise
                reponse = "".join(morceaux).strip()
                if reponse and not (stop is not None and stop.is_set()):
                    self._garder(demande, reponse)
                return
        finally:
            close = getattr(http, "close", None)
            if close:
                close()

    def _flux(self, http: Any, corps: dict) -> Iterator[dict]:
        import httpx

        try:
            with http.stream("POST", self.url + "/api/chat", json=corps,
                             timeout=httpx.Timeout(DELAI_LECTURE_S, connect=5.0)) as r:
                if r.status_code >= 400:
                    r.read()
                    try:
                        err = r.json().get("error") or r.text
                    except ValueError:
                        err = r.text
                    code = r.status_code
                    raise ErreurLocale(str(err or f"Ollama a répondu {code}"))
                for ligne in r.iter_lines():
                    if ligne.strip():
                        try:
                            yield json.loads(ligne)
                        except ValueError:
                            continue
        except httpx.ConnectError:
            raise ErreurLocale("Ollama ne répond pas : lance-le (« ollama serve » ou le service ollama)") from None
        except httpx.TimeoutException:
            raise ErreurLocale("Ollama ne répond plus (délai dépassé)") from None

    def _mesurer(self, fin: dict, premier: float | None) -> None:
        n, d = int(fin.get("eval_count") or 0), float(fin.get("eval_duration") or 0) / 1e9
        self.derniere = {"modele": self.tag, "premier_mot_s": round(premier or 0.0, 2),
                         "tokens": n, "tokens_s": round(n / d, 1) if d else 0.0,
                         "charge_s": round(float(fin.get("load_duration") or 0) / 1e9, 1),
                         "num_gpu": self._num_gpu}
        log.info("moteur local %s : premier mot %.1f s, %d tokens à %.1f t/s", self.tag,
                 self.derniere["premier_mot_s"], n, self.derniere["tokens_s"])


def _client_http() -> Any:
    import httpx

    return httpx.Client(trust_env=False)  # jamais de proxy du système pour une adresse locale


def _echantillon() -> dict:
    from . import memoire

    return memoire.sample()
