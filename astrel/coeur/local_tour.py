"""Tour LOCAL (04/11/2026, lot 1 du moteur local, docs/chantiers/HYBRIDE-QWEN.md) : une demande que l'utilisateur
veut traiter sur sa machine (« en local : … », canal Local) part chez Qwen (`moteur_local`), jamais chez Claude.

- Le cœur décide (`routeur.forced_engine`), jamais un modèle ; Claude reste vivant à côté, sans rien voir.
- Rien ne sort : réponse dite par Piper seulement (`speaker` `local_only`), jamais Edge ; sans Piper, affichée.
- Rien n'est gardé ailleurs qu'en mémoire : ni `attente.py`, ni l'historique du CLI (~/.claude/projects,
  history.db), ni capture WATCH, ni amorce de relève ; le journal ne note que des longueurs.
- Demande locale venue de WhatsApp : refusée (la réponse repartirait par Internet).
- Claude parle déjà ou travaille : la réponse locale est affichée, pas dite (« à lire »).
- Pièces jointes : pas encore lues en local (lot 1) ; refusées avec la raison, rendues à la barre.
- 04/11 (lot 4) : une demande qui a seulement besoin de LIRE passe par `outils_local` (CLI sur le modèle local,
  Read/Grep/Glob, hook du garde-fou) quand ses outils sont ouverts ; son process compte parmi ceux de Claude.

Mixin de `Astrel` (core.py), comme les autres de `astrel/coeur/`.
"""
from __future__ import annotations

import threading
import time
import uuid

from .. import config, langue, moteur_local, outils_local, routeur, state
from ..channels import channels
from ..events import bus
from ..voice import SentenceSplitter
from ..core import log  # aides du cœur (le paquet charge core d'abord)

REFUS_WHATSAPP = ("Une demande « en local » ne se fait pas depuis WhatsApp : la réponse repartirait par Internet. "
                  "Redemande-la sur le PC.")
REFUS_PIECES = ("Le moteur local ne lit pas encore les pièces jointes : elles sont rendues à la barre. "
                "Envoie la demande sans elles, ou à Claude.")
OCCUPE = "Le moteur local répond déjà à une demande : attends la fin ou arrête-la (■)."


class TourLocalCoeur:
    """Demandes traitées par le moteur local, à côté du cerveau Claude."""

    @property
    def _moteur_local(self) -> moteur_local.MoteurLocal:
        m = self.__dict__.get("_moteur_local_obj")
        if m is None:
            m = self.__dict__.setdefault("_moteur_local_obj", moteur_local.MoteurLocal())
        return m

    @property
    def _outils_locaux(self) -> outils_local.OutilsLocaux:
        o = self.__dict__.get("_outils_locaux_obj")
        if o is None:
            pids = self.__dict__.setdefault("_pids_outils_locaux", set())
            o = self.__dict__.setdefault("_outils_locaux_obj", outils_local.OutilsLocaux(
                suivre=outils_local.compter_pids(pids),
                reconnu=lambda pid: state.from_claude(pid, self._claude_pid())))
        return o

    def _outils_locaux_raison(self) -> str:
        """Pourquoi les outils de lecture du local restent fermés, "" s'ils sont ouverts (lot 4)."""
        try:
            return outils_local.raison_indisponible(self._outils_locaux.base, bool(getattr(self, "_total", False)),
                                                    bool(getattr(self, "_lift", False)))
        except Exception as exc:
            return f"outils du modèle local indisponibles ({exc})"

    def _local_etat(self) -> dict:
        """Verrou (un tour local à la fois), arrêt (■) et « chut » du tour local en cours."""
        e = self.__dict__.get("_local_tour_etat")
        if e is None:
            e = self.__dict__.setdefault("_local_tour_etat", {
                "verrou": threading.Lock(), "stop": threading.Event(), "chut": False, "actif": False})
        return e

    def _moteur_force(self, text: str, channel: str = "") -> tuple[str, str]:
        """(moteur demandé EXPRESSÉMENT, texte pour lui) : `routeur.forced_engine` avec les canaux Local."""
        try:
            locaux = channels.local_ids()
        except Exception:
            locaux = frozenset()
        return routeur.forced_engine(text, channel, locaux)

    def _local_actif(self) -> bool:
        return bool(self._local_etat()["actif"])

    def _local_arreter(self) -> None:
        """■ Stop : la réponse locale en cours s'arrête (le texte déjà reçu reste affiché)."""
        e = self._local_etat()
        if e["actif"]:
            e["stop"].set()

    def _local_chut(self) -> None:
        e = self._local_etat()
        if e["actif"]:
            e["chut"] = True

    def _tour_local(self, text: str, demande: str, source: str, channel: str = "", shown: str | None = None,
                    joined=None, outils: bool = False) -> None:
        """Traite `demande` en local. `text` : ce que l'utilisateur a écrit ou dit (affiché tel quel)."""
        ch, routed = channels.route(text, requested=channel, follow=True)
        mid = uuid.uuid4().hex[:12]
        bus.publish("transcript", who="user", text=text if shown is None else shown, source=source, channel=ch,
                    mid=mid, routed=routed, engine="local")
        if source == "whatsapp":
            log.info("demande locale venue de WhatsApp refusée (%d caractères)", len(text))
            try:
                self.whatsapp.send_text("🔷 " + langue.parle(REFUS_WHATSAPP))
            except Exception:
                log.exception("refus WhatsApp non envoyé")
            return
        if joined is not None and (joined.ids or joined.shown):
            ids = list(joined.ids)
            if ids:
                self._release_attachments(ids)
            bus.publish("notice", level="warning", channel=ch, text=REFUS_PIECES)
            return
        e = self._local_etat()
        if not e["verrou"].acquire(blocking=False):
            bus.publish("notice", level="warning", channel=ch, text=OCCUPE)
            return
        # Posé AVANT le fil : la boucle de la voix (`_wait_reply_start`) voit tout de suite le tour local.
        e["stop"].clear()
        e["chut"], e["actif"] = False, True
        threading.Thread(target=self._tour_local_fil, args=(demande, source, ch, mid, outils), name="tour-local",
                         daemon=True).start()

    def _tour_local_fil(self, demande: str, source: str, ch: str, mid: str, outils: bool = False) -> None:
        e = self._local_etat()
        try:
            self._tour_local_reponse(demande, source, ch, mid, outils)
        finally:
            e["actif"] = False
            e["verrou"].release()

    def _local_flux(self, demande: str, ch: str, outils: bool, stop: threading.Event):
        """Réponse du local : avec ses outils de lecture (lot 4) s'ils sont TOUJOURS ouverts au moment de
        répondre (Total, levée : refermés entre-temps), sinon sans outil (lot 1)."""
        lang = "en" if langue.anglais() else "fr"
        if outils and not self._outils_locaux_raison():
            try:
                projet = channels.workdir_of(ch) or config.WORKDIR
            except Exception:
                projet = config.WORKDIR
            return self._outils_locaux.repondre(demande, lang, projet=projet, stop=stop), True
        return self._moteur_local.repondre(demande, lang, stop=stop), False

    def _tour_local_reponse(self, demande: str, source: str, ch: str, mid: str, outils: bool = False) -> None:
        e = self._local_etat()
        # Claude parle ou travaille déjà : la réponse locale est à lire, pas dite par-dessus.
        dire = not (self._turn_active or self.speaker.busy)
        self.status.set(state.THINKING, text="", unless=(state.ASKING, state.SPEAKING, state.LISTENING))
        splitter, morceaux, dit = SentenceSplitter(), [], False
        flux, avec_outils = self._local_flux(demande, ch, outils, e["stop"])
        log.info("tour local (%s, %d caractères%s) → %s", source, len(demande), ", outils de lecture" if avec_outils
                 else "", self._moteur_local.tag)
        t0 = time.monotonic()
        try:
            for morceau in flux:
                morceaux.append(morceau)
                bus.publish("reply_delta", text=morceau, channel=ch, reply_to=mid, engine="local")
                if dire and not e["chut"]:
                    for phrase in splitter.feed(morceau):
                        self.say(phrase, local_only=True)
                        dit = True
            if dire and not e["chut"] and not e["stop"].is_set():
                for phrase in splitter.flush():
                    self.say(phrase, local_only=True)
                    dit = True
        except moteur_local.ErreurLocale as exc:
            bus.publish("notice", level="warning", channel=ch, text=f"Moteur local : {exc}")
            log.warning("tour local en échec : %s", exc)
        except Exception:
            log.exception("tour local en échec")
            bus.publish("notice", level="warning", channel=ch, text="Moteur local : panne inattendue (voir le journal)")
        finally:
            texte = "".join(morceaux).strip()
            if texte and avec_outils and not e["stop"].is_set():
                self._moteur_local._garder(demande, texte)  # la suite de la conversation locale s'en souvient
            if texte:
                bus.publish("reply", text=texte, channel=ch, reply_to=mid, mid=uuid.uuid4().hex[:12],
                            engine="local")
            log.info("tour local fini en %.1f s (%d caractères%s)", time.monotonic() - t0, len(texte),
                     ", arrêté" if e["stop"].is_set() else "")
            if dit and source == "voix" and not self._in_conversation and self._followup_s() > 0:
                self._followup = True  # comme après une réponse de Claude dite : il peut enchaîner
            if not self.speaker.busy and not self._in_conversation and not self._turn_active:
                self.status.set(self._rest_state(), only=(state.THINKING, state.WORKING))
