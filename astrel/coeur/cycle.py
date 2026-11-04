"""Cerveau qui tombe proprement (04/11, carte T46 ; décisions dans `astrel/cycle_cerveau.py`).

- Chute (`_eof`, lancement raté) : relance avec recul 2/5/15/60 s, annonces par épisode (une phrase et un
  carillon à la première chute seulement), jamais dans le fil `_pump` (minuteur `cycle_cerveau.planifier`).
- Claude Code non connecté (`error: authentication_failed` du CLI, ou son stderr) : plus aucune relance en
  boucle ; une notice par épisode ; `claude auth status` relu toutes les `AUTH_SONDE_S` (et tout de suite à un
  message de l'utilisateur) ; connecté → relance, les messages gardés partent. Jamais `.credentials.json`.
- Commande `claude` introuvable : une notice, une sonde par minute.
- Message arrivé pendant une relance : affiché tout de suite, gardé (`RELANCE_GARDE_S` pour l'utilisateur,
  `RELANCE_GARDE_AUTO_S` pour un réflexe ou l'équipe), envoyé dès que le moteur repart, sinon « non envoyé »
  (l'utilisateur, avec « Renvoyer ») ou abandonné avec une notice (automatique). Jamais rejoué après coup.

Mixin de `Astrel` (core.py), comme les autres de `astrel/coeur/`.
"""
from __future__ import annotations

import contextlib
import shutil
import threading
import uuid

from .. import config
from .. import cycle_cerveau
from .. import hors_ligne  # 04/11 : mode « rien ne sort »
from ..attente import attente
from ..cycle_cerveau import ABSENT, AUTH, OK, CycleCerveau
from ..events import bus
from ..core import _UTILISATEUR, log  # aides du cœur (le paquet charge core d'abord)

ABSENT_SONDE_S = 60.0


def _lancer_sonde(fn) -> None:
    """La sonde de `claude auth status` (jusqu'à 15 s) tourne dans un fil à part ; remplacée dans les tests."""
    threading.Thread(target=fn, name="sonde-auth", daemon=True).start()


class CycleCoeur:
    """Relance du cerveau avec recul, authentification, messages gardés pendant une relance."""

    @property
    def _cycle(self) -> CycleCerveau:
        # Paresseux : les tests montent `Astrel` par `object.__new__`, sans `__init__`.
        c = self.__dict__.get("_cycle_obj")
        if c is None:
            c = self.__dict__.setdefault("_cycle_obj", CycleCerveau())
        return c

    # --- chutes ---

    def _cerveau_tombe(self, ev=None, *, annoncer: bool = True, vecu_s: float | None = None) -> None:
        """Process du cerveau mort (ou lancement raté) : relance programmée, recul croissant."""
        if hors_ligne.actif():  # 04/11 : cerveau arrêté exprès (mode « rien ne sort ») : rien à relancer
            return
        data = getattr(ev, "data", None) or {}
        if data.get("auth"):
            self._cerveau_auth("refusée")
            return
        d = self._cycle.tombe(vecu_s if vecu_s is not None else data.get("vecu_s"))
        if d is None:
            return  # Astrel s'arrête
        log.warning("cerveau mort (chute %d de l'épisode, code %s) : relance dans %.0f s", d.rang, data.get("rc"),
                    d.delai)
        if annoncer:
            secondes = round(d.delai)
            if d.annonce == "complete":
                bus.publish("notice", level="error", text=f"Le moteur s'est arrêté : je le relance dans {secondes} s.")
                self._chime("error")
                self.say("Le moteur s'est arrêté, je le relance.", raw=True)
            elif d.annonce == "notice":
                bus.publish("notice", level="warning", text=f"Le moteur est retombé : nouvel essai dans {secondes} s.")
            elif d.annonce == "alerte":
                bus.publish("notice", level="warning",
                            text="Le moteur retombe sans cesse : nouvel essai chaque minute, sans autre annonce. "
                                 "Détail dans logs/astrel.log.")
        cycle_cerveau.planifier(d.delai, lambda g=d.generation: self._cerveau_relance_due(g))

    def _cerveau_relance_due(self, generation: int) -> None:
        """Minuteur : relance si rien ne l'a rendue inutile (relance manuelle, arrêt, authentification)."""
        if not getattr(self, "_running", True) or not self._cycle.relance_due(generation):
            return
        self._cerveau_lancer()

    def _cerveau_lancer(self, fresh: bool = False) -> bool:
        """Relance par le cycle (minuteur, sonde) : sous le verrou d'envoi, puis les messages gardés partent."""
        if hors_ligne.actif():  # 04/11
            return False
        try:
            with getattr(self, "_send_lock", None) or contextlib.nullcontext():
                self.brain.restart(fresh=fresh)
        except FileNotFoundError:
            self._cerveau_absent()
            return False
        except Exception:
            log.exception("relance du cerveau impossible")
            self._cerveau_tombe(vecu_s=0.0)  # palier suivant du recul
            return False
        self._cycle.reparti()
        log.info("cerveau relancé")
        self._cycle_vider()
        return True

    def _cerveau_relancer(self, fresh: bool = False) -> None:
        """Relance MANUELLE (session neuve, modèle, effort, réglages, mode léger) : appelée sous les verrous de
        l'appelant ; périme les relances programmées. Les messages gardés partent au tic suivant."""
        self._cycle.annuler()
        if hors_ligne.actif():  # 04/11 : modèle, effort, réglages changés hors ligne : pris au retour en ligne
            return
        self.brain.restart(fresh=fresh)
        self._cycle.reparti()

    def _cerveau_demarrer(self, fresh: bool = False) -> bool:
        """Premier démarrage (`_boot_brain`, sous `_send_lock`) : un lancement raté ne casse plus le démarrage."""
        if hors_ligne.actif():  # 04/11
            return False
        try:
            self.brain.start(fresh=fresh)
        except FileNotFoundError:
            self._cerveau_absent()
            return False
        except Exception:
            log.exception("démarrage du cerveau impossible")
            self._cerveau_tombe(vecu_s=0.0)
            return False
        return True

    def _cerveau_arreter(self) -> None:
        self._cycle.arret()

    # --- authentification et commande introuvable ---

    def _cerveau_auth(self, cause: str = "refusée") -> None:
        premier = self._cycle.auth(cause)
        log.warning("Claude Code non connecté (%s) : plus de relance en boucle, sonde de `claude auth status`", cause)
        if premier:
            bus.publish("notice", level="warning",
                        text="Claude Code n'est plus connecté à ton compte : je ne relance plus le moteur en boucle. "
                             "Dans un terminal : claude puis /login, ou Réglages › Compte › Se connecter ; je "
                             "reprends seul dès que c'est fait.")
            self._chime("error")
            self.say("Claude Code n'est plus connecté à ton compte : regarde le HUD.", raw=True)
        self._cycle_sonder(maintenant=True)

    def _cerveau_absent(self) -> None:
        if self._cycle.absent():
            log.error("commande %s introuvable : cerveau non lancé", config.CLAUDE_BIN)
            bus.publish("notice", level="error",
                        text="Claude Code est introuvable (commande claude) : installe-le ou vérifie le PATH ; "
                             "je réessaie chaque minute.")

    def _cycle_sonder(self, maintenant: bool = False) -> None:
        """Claude Code déconnecté : relit `claude auth status` (fil à part, une sonde à la fois)."""
        c = self._cycle
        if c.etat != AUTH or not c.sonde_due(config.AUTH_SONDE_S, maintenant):
            return
        _lancer_sonde(self._cycle_sonde_auth)

    def _cycle_sonde_auth(self) -> None:
        genre = getattr(getattr(self.brain, "fournisseur", None), "genre", "abonnement")
        try:
            if genre == "abonnement":
                from .. import compte as compte_mod

                compte_mod.compte.refresh(wait=True)
                st = compte_mod.compte.state() or {}
                if not st.get("loggedIn"):
                    return
            # Autre fournisseur (clé, passerelle, CLI tiers) : pas de sonde de compte, un essai de temps en temps.
            if not self._cycle.essai_auth_permis():
                return
        except Exception:
            log.exception("sonde de l'authentification")
            return
        if self._cycle.etat != AUTH or not getattr(self, "_running", True):
            return
        log.info("Claude Code connecté : relance du cerveau")
        with self._cycle.lock:
            self._cycle.etat = cycle_cerveau.RELANCE  # relance voulue (pas une chute)
        if self._cerveau_lancer():
            bus.publish("notice", level="info", text="Claude Code est de nouveau connecté : moteur relancé.")

    # --- messages pendant une relance ---

    def _cycle_garder(self, text: str, source: str, channel: str, shown, joined) -> bool:
        """Appelé par `handle` : vrai si le message est gardé (moteur en relance, déconnecté ou introuvable)."""
        c = self._cycle
        if not c.bloque():
            return False
        ch, routed, _floating = self._route_message(text, channel, source)
        mid = uuid.uuid4().hex[:12]
        self._attente_noter(mid, text, source, ch, len(joined.ids) if joined else 0)
        extra = {"attachments": joined.shown} if joined and joined.shown else {}
        bus.publish("transcript", who="user", text=text if shown is None else shown, source=source,
                    channel=ch, mid=mid, routed=routed, **extra)
        utilisateur = source in _UTILISATEUR
        premier = c.garder({"mid": mid, "text": text, "source": source, "channel": ch, "shown": shown,
                            "joined": joined, "utilisateur": utilisateur})
        log.info("message (%s) gardé : moteur %s", source, c.etat)
        if utilisateur and premier:
            if c.etat == AUTH:
                bus.publish("notice", level="info", channel=ch,
                            text="Claude Code n'est pas connecté : ton message partira dès qu'il l'est.")
            elif c.etat == ABSENT:
                bus.publish("notice", level="info", channel=ch,
                            text="Claude Code est introuvable : ton message partira dès qu'il est de retour.")
            else:
                bus.publish("notice", level="info", channel=ch,
                            text="Le moteur redémarre : ton message partira dès qu'il est prêt.")
        if utilisateur:
            self._cycle_sonder(maintenant=True)
        return True

    def _cycle_vider(self) -> None:
        """Le moteur est reparti : les messages gardés partent, dans l'ordre (déjà affichés)."""
        for it in self._cycle.reprendre():
            try:
                self.handle(it["text"], it["source"], channel=it["channel"], shown=it.get("shown"),
                            joined=it.get("joined"), held_mid=it["mid"])
            except Exception:
                log.exception("message gardé non envoyé")

    def _cycle_perime(self, it: dict) -> None:
        mid = it["mid"]
        if it.get("utilisateur"):
            log.warning("message gardé non envoyé : le moteur n'est pas reparti à temps")
            bus.update_history(lambda ev: ev.get("type") == "transcript" and ev.get("mid") == mid, unsent=True)
            joined = it.get("joined")
            lost = list(joined.ids) if joined else []
            if lost:
                self._release_attachments(lost)
            bus.publish("unsent", mid=mid, error="le moteur n'est pas reparti à temps",
                        **({"attachments": lost} if lost else {}))
            attente.oublier(mid)  # le fil le montre « non envoyé » avec « Renvoyer »
        else:
            log.warning("message automatique (%s) abandonné : moteur non reparti", it.get("source"))
            bus.publish("notice", level="warning",
                        text="Message automatique abandonné : le moteur n'est pas reparti à temps.")

    def _cycle_tick(self) -> None:
        """Chaque seconde (`_mission_loop`) : messages gardés trop longtemps, sondes, envoi des gardés."""
        c = self._cycle
        for it in c.perimes():
            self._cycle_perime(it)
        if c.etat == AUTH:
            self._cycle_sonder()
        elif c.etat == ABSENT:
            if c.sonde_due(ABSENT_SONDE_S) and shutil.which(config.CLAUDE_BIN):
                log.info("commande %s retrouvée : relance du cerveau", config.CLAUDE_BIN)
                with c.lock:
                    c.etat = cycle_cerveau.RELANCE
                self._cerveau_lancer()
        elif c.etat == OK and c.gardes() and getattr(self.brain, "alive", False):
            self._cycle_vider()

    def _cycle_reussi(self) -> None:
        """Fin d'un tour réussi : fin d'épisode, recul remis au premier palier."""
        if self._cycle.reussi():
            log.info("moteur reparti : épisode de pannes clos")
            bus.publish("notice", level="info", text="Le moteur est reparti.")
