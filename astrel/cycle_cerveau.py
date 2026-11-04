"""Cycle de vie du cerveau (04/11, carte T46 « cerveau qui tombe proprement »).

Avant : un cerveau mort (`_eof`) était relancé toutes les ~2 s, sans compteur ni recul, avec carillon
et phrase à chaque fois ; un `Popen` qui levait laissait le cerveau mort en silence ; Claude Code
déconnecté faisait échouer chaque message bruyamment. Ici, sans rien importer du cœur :

- états : `ok`, `relance` (process mort, relance programmée), `auth` (Claude Code non connecté, sonde de
  `claude auth status`), `absent` (commande `claude` introuvable), `arret` (Astrel s'arrête) ;
- recul : `BRAIN_RESTART_DELAY_S` puis `BRAIN_RESTART_BACKOFF_S` (2, 5, 15, 60 s, puis 60 s) ; remis au
  premier palier après un tour réussi ou un process qui a vécu `RELANCE_STABLE_S` ;
- annonces par ÉPISODE : 1re chute = carillon, phrase et notice ; 2e et 3e = notice ; 4e = alerte « retombe
  sans cesse » ; ensuite journal seul ; tour réussi = « moteur reparti » ;
- `generation` : toute relance manuelle (session neuve, modèle, réglages) ou l'arrêt périme les relances
  programmées ; `lock` = verrou unique du cycle (jamais d'évènement publié en le tenant) ;
- messages gardés pendant la relance (`garder`), rendus quand le cerveau repart (`reprendre`), périmés
  au-delà de leur délai (`perimes`).

Le cœur (`astrel/coeur/cycle.py`) applique les décisions : ce module ne publie rien, ne lance rien.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from . import config

OK, RELANCE, AUTH, ABSENT, ARRET = "ok", "relance", "auth", "absent", "arret"


def planifier(delai: float, fn: Callable[[], Any]) -> None:
    """Lance `fn` dans `delai` secondes (fil à part). Remplacé dans les tests : aucun Timer réel,
    sinon une relance programmée lancerait un VRAI CLI une fois le test fini."""
    t = threading.Timer(max(0.0, delai), fn)
    t.daemon = True
    t.name = "relance-cerveau"
    t.start()


def paliers() -> tuple[float, ...]:
    return (float(config.BRAIN_RESTART_DELAY_S), *(float(d) for d in config.BRAIN_RESTART_BACKOFF_S))


@dataclass
class Decision:
    """Ce que le cœur fait après une chute : relancer dans `delai` s (génération `generation`) et
    annoncer selon `annonce` : « complete » (carillon, phrase, notice), « notice », « alerte » ou ""."""
    delai: float
    generation: int
    rang: int
    annonce: str


class CycleCerveau:
    def __init__(self, horloge: Callable[[], float] = time.monotonic) -> None:
        self.lock = threading.RLock()
        self.horloge = horloge
        self.etat = OK
        self.echecs = 0          # chutes de l'épisode en cours
        self.generation = 0
        self.cause_auth = ""     # « refusée » (le CLI dit non connecté) ou « absente » (auth status)
        self.sonde_at = 0.0      # dernière sonde (auth, absent)
        self.essai_auth_at = 0.0  # dernière relance faite alors que `auth status` disait connecté
        self._garde: list[dict] = []
        self._garde_annoncee = False

    # --- chutes et reprises ---

    def tombe(self, vecu_s: float | None = None) -> Decision | None:
        """Process mort (`_eof`) ou lancement raté. None si Astrel s'arrête."""
        with self.lock:
            if self.etat == ARRET:
                return None
            if vecu_s is not None and vecu_s >= config.RELANCE_STABLE_S:
                self.echecs = 0  # a vécu longtemps : pas une panne qui se répète
            p = paliers()
            delai = p[min(self.echecs, len(p) - 1)]
            self.echecs += 1
            self.generation += 1
            if self.etat not in (AUTH, ABSENT):
                self.etat = RELANCE
            n = self.echecs
            annonce = "complete" if n == 1 else "notice" if n <= 3 else "alerte" if n == 4 else ""
            return Decision(delai, self.generation, n, annonce)

    def relance_due(self, generation: int) -> bool:
        """La relance programmée de cette génération est-elle encore à faire ?"""
        with self.lock:
            return generation == self.generation and self.etat == RELANCE

    def reparti(self) -> None:
        """Le process est relancé (pas encore de tour réussi : l'épisode continue)."""
        with self.lock:
            if self.etat in (RELANCE, AUTH, ABSENT):
                self.etat = OK

    def reussi(self) -> bool:
        """Un tour a abouti : fin d'épisode. Vrai si un épisode de pannes vient de se clore."""
        with self.lock:
            fin = self.echecs > 0 or bool(self.cause_auth)
            self.echecs, self.cause_auth, self._garde_annoncee = 0, "", False
            if self.etat in (RELANCE, AUTH, ABSENT):
                self.etat = OK
            return fin

    def auth(self, cause: str = "refusée") -> bool:
        """Claude Code non connecté. Vrai la première fois de l'épisode (annonce à faire)."""
        with self.lock:
            if self.etat == ARRET:
                return False
            premier = not self.cause_auth
            self.cause_auth = cause or "refusée"
            self.etat = AUTH
            self.generation += 1  # plus de relance en boucle : seule la sonde relance
            return premier

    def absent(self) -> bool:
        """Commande `claude` introuvable. Vrai la première fois de l'épisode."""
        with self.lock:
            if self.etat == ARRET:
                return False
            premier = self.etat != ABSENT
            self.etat = ABSENT
            self.generation += 1
            return premier

    def annuler(self) -> None:
        """Relance manuelle (session neuve, modèle, réglages) : les relances programmées sont périmées."""
        with self.lock:
            self.generation += 1
            if self.etat == RELANCE:
                self.etat = OK

    def arret(self) -> None:
        with self.lock:
            self.etat = ARRET
            self.generation += 1

    def bloque(self) -> bool:
        """Un message envoyé maintenant ne partirait pas (relance en attente, compte déconnecté, CLI absent)."""
        with self.lock:
            return self.etat in (RELANCE, AUTH, ABSENT)

    def sonde_due(self, intervalle: float, maintenant: bool = False) -> bool:
        """Une sonde (auth, absent) toutes les `intervalle` s ; `maintenant` : au plus une par 15 s."""
        with self.lock:
            now = self.horloge()
            delai = min(15.0, intervalle) if maintenant else intervalle
            if now - self.sonde_at < delai:
                return False
            self.sonde_at = now
            return True

    def essai_auth_permis(self) -> bool:
        """`auth status` dit connecté mais le CLI refusait : une relance au plus par `AUTH_ESSAI_S`."""
        with self.lock:
            now = self.horloge()
            if self.cause_auth == "absente" or now - self.essai_auth_at >= config.AUTH_ESSAI_S:
                self.essai_auth_at = now
                return True
            return False

    # --- messages gardés pendant une relance ---

    def garder(self, item: dict) -> bool:
        """Garde un message (mid, text, source, channel, shown, joined, utilisateur). Vrai si c'est le premier
        de l'épisode (une seule notice « il partira quand le moteur est prêt »)."""
        with self.lock:
            self._garde.append({**item, "at": self.horloge()})
            premier, self._garde_annoncee = not self._garde_annoncee, True
            return premier

    def reprendre(self) -> list[dict]:
        """Le cerveau est reparti : messages gardés à envoyer, dans l'ordre."""
        with self.lock:
            items, self._garde = self._garde, []
            return items

    def perimes(self) -> list[dict]:
        """Messages gardés trop longtemps : l'utilisateur `RELANCE_GARDE_S`, automatiques `RELANCE_GARDE_AUTO_S`."""
        with self.lock:
            now = self.horloge()
            garde, out = [], []
            for it in self._garde:
                limite = config.RELANCE_GARDE_S if it.get("utilisateur") else config.RELANCE_GARDE_AUTO_S
                (out if now - it["at"] >= limite else garde).append(it)
            self._garde = garde
            return out

    def gardes(self) -> int:
        with self.lock:
            return len(self._garde)
