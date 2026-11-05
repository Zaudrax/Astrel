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
- 05/11 (essais réels de l'utilisateur) : faits exacts ajoutés au message du LOCAL (`sensible.faits`), dossiers cités
  ouverts en lecture (`outils_local.dossiers_cites`) ; demande sensible gardée elle aussi pour « Envoyer à Claude »
  (bornée, en mémoire) : elle repasse par la carte du lot 3.
- 05/11 (lot 8) : message du local = `moteur_local.message_local` (faits exacts, R2) ; sortie filtrée par le cœur
  (`sortie_locale`, dans les moteurs : affiché ET dit) ; adresses non vues dans le tour marquées (R1).
- 05/11 (lot 6) : « Envoyer à Claude » sous une réponse locale (commande `local_vers_claude {mid}`, HUD de
  l'utilisateur SEUL) : la DEMANDE d'origine (jamais la réponse de Qwen), gardée EN MÉMOIRE seulement (les
  `MEMO_MAX` dernières, jamais sur disque ; rien pour une demande faite hors ligne), repasse par la barrière du
  lot 3 : sensible → carte « routage » (rien ne part sans le clic), sinon `handle(route="claude")`.
- 05/11 (lot 7 partie B, décision de l'utilisateur : Qwen a Internet en permanence, sauf « rien ne sort ») : un tour
  AVEC outils a aussi les outils web (recherche_web, lire_page) quand leurs épreuves du banc ont réussi, et le
  navigateur pour une demande qui vise un site interactif (`navigateur=True`, routeur) ; SearXNG lancé au besoin
  (`searxng_local`) ; chaque page lue ou ouverte est montrée dans le fil (notice, puis ligne « Pages ouvertes »
  sous la réponse) ; Stop tout et « rien ne sort » coupent le réseau du navigateur (`_local_web_couper`).

Mixin de `Astrel` (core.py), comme les autres de `astrel/coeur/`.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict

from .. import config, hors_ligne, langue, moteur_local, outils_local, routeur, searxng_local, sensible, state
from ..channels import channels
from ..events import bus
from ..voice import SentenceSplitter
from ..core import log  # aides du cœur (le paquet charge core d'abord)

REFUS_WHATSAPP = ("Une demande « en local » ne se fait pas depuis WhatsApp : la réponse repartirait par Internet. "
                  "Redemande-la sur le PC.")
REFUS_PIECES = ("Le moteur local ne lit pas encore les pièces jointes : elles sont rendues à la barre. "
                "Envoie la demande sans elles, ou à Claude.")
OCCUPE = "Le moteur local répond déjà à une demande : attends la fin ou arrête-la (■)."
# 05/11 (lot 6) : « Envoyer à Claude » sous une réponse locale.
MEMO_MAX = 20  # demandes locales gardées en mémoire pour un renvoi à Claude
VC_OUBLIEE = "demande plus en mémoire (déjà envoyée, ou Astrel a redémarré) : retape-la"
VC_HORS_LIGNE = "mode « rien ne sort » : Claude est arrêté, rien ne part"
VC_NOTICE = "Ta demande d'origine part chez Claude (pas la réponse du modèle local)."
# 05/11 (lot 7B) : pages lues ou ouvertes par le modèle local, visibles dans le fil (décision de l'utilisateur).
PAGE_OUVERTE = "Le modèle local a ouvert : {url}"
WEB_INDISPONIBLE = "Recherche web du modèle local indisponible : {raison}"
PAGES_TITRE = {"fr": "Pages ouvertes par le modèle local :", "en": "Pages opened by the local model:"}
PAGES_MAX = 12  # adresses listées sous la réponse

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
                suivre=outils_local.compter_pids(pids, outils_local.PIDS_CLI),  # 05/11 : lus par le hook (zones)
                reconnu=lambda pid: state.from_claude(pid, self._claude_pid())))
        return o

    def _outils_locaux_raison(self) -> str:
        """Pourquoi les outils de lecture du local restent fermés, "" s'ils sont ouverts (lot 4)."""
        try:
            return outils_local.raison_indisponible(self._outils_locaux.base, bool(getattr(self, "_total", False)),
                                                    bool(getattr(self, "_lift", False)))
        except Exception as exc:
            return f"outils du modèle local indisponibles ({exc})"

    def _outils_locaux_actions_raison(self) -> str:
        """05/11 (lot 5) : pourquoi les commandes autorisées et les propositions du local restent fermées."""
        try:
            return outils_local.raison_actions_indisponible(self._outils_locaux.base,
                                                            bool(getattr(self, "_total", False)),
                                                            bool(getattr(self, "_lift", False)))
        except Exception as exc:
            return f"outils du modèle local indisponibles ({exc})"

    def _outils_locaux_web_raison(self) -> str:
        """05/11 (lot 7B) : pourquoi les outils web du local restent fermés, "" s'ils sont ouverts."""
        try:
            return outils_local.raison_web_indisponible(self._outils_locaux.base, bool(getattr(self, "_total", False)),
                                                        bool(getattr(self, "_lift", False)))
        except Exception as exc:
            return f"outils web du modèle local indisponibles ({exc})"

    def _outils_locaux_navigateur_raison(self) -> str:
        """05/11 (lot 7B) : pourquoi le navigateur du local reste fermé, "" s'il peut s'ouvrir."""
        try:
            return outils_local.raison_navigateur_indisponible(
                self._outils_locaux.base, bool(getattr(self, "_total", False)), bool(getattr(self, "_lift", False)))
        except Exception as exc:
            return f"navigateur du modèle local indisponible ({exc})"

    def _local_web_couper(self, raison: str = "Stop tout") -> None:
        """05/11 (lot 7B) : Stop tout, « rien ne sort » : le navigateur du tour local perd le réseau tout de suite."""
        o = self.__dict__.get("_outils_locaux_obj")
        if o is not None:
            o.couper(raison)

    def _local_estop(self) -> None:
        """05/11 (lot 7B) : « Stop tout » arrête aussi le tour local en cours et coupe son navigateur."""
        self._local_web_couper("arrêt d'urgence posé : rien ne sort")
        self._local_arreter()

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
                    joined=None, outils: bool = False, reflexion: bool | None = None,
                    navigateur: bool = False) -> None:
        """Traite `demande` en local. `text` : ce que l'utilisateur a écrit ou dit (affiché tel quel).
        05/11 (lot 8) : `reflexion=True` = réflexion À LA DEMANDE pour cette question (commande « /reflechir » du
        chat) ; None = défaut du moteur (`moteur_local.REFLEXION` : coupée, décision de l'utilisateur).
        05/11 (lot 7B) : `navigateur` = la demande vise un site interactif (routeur) : navigateur du local, s'il est
        ouvert, en plus des outils web."""
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
        threading.Thread(target=self._tour_local_fil, args=(demande, source, ch, mid, outils, reflexion, navigateur),
                         name="tour-local", daemon=True).start()

    def _tour_local_fil(self, demande: str, source: str, ch: str, mid: str, outils: bool = False,
                        reflexion: bool | None = None, navigateur: bool = False) -> None:
        e = self._local_etat()
        try:
            self._tour_local_reponse(demande, source, ch, mid, outils, reflexion, navigateur)
        finally:
            e["actif"] = False
            e["verrou"].release()

    def _local_flux(self, demande: str, ch: str, outils: bool, stop: threading.Event,
                    reflexion: bool | None = None, navigateur: bool = False, sur_page=None):
        """Réponse du local : avec ses outils de lecture (lot 4) s'ils sont TOUJOURS ouverts au moment de
        répondre (Total, levée : refermés entre-temps), sinon sans outil (lot 1).
        05/11 (essais réels) : faits exacts (`sensible.faits` : IBAN, carte, NIR) ajoutés au message du LOCAL
        seulement (jamais gardés pour « Envoyer à Claude ») ; dossiers cités ouverts en lecture.
        05/11 (lot 7B) : outils web à chaque tour avec outils s'ils sont ouverts (revérifiés ici), navigateur si la
        demande le vise et qu'il est ouvert ; SearXNG lancé au besoin."""
        lang = "en" if langue.anglais() else "fr"
        # 05/11 (lot 8, R2) : `moteur_local.message_local` = demande + faits exacts du cœur (`faits_local` : ceux de
        # `sensible`, plus dates, calculs, carte fausse) ; même message que le banc de qualité.
        message = moteur_local.message_local(demande, lang)
        if outils and not self._outils_locaux_raison():
            try:
                projet = channels.workdir_of(ch) or config.WORKDIR
            except Exception:
                projet = config.WORKDIR
            # 05/11 (lot 5) : commandes autorisées et propositions, revérifiées elles aussi au moment de répondre.
            actions = not self._outils_locaux_actions_raison()
            try:
                lecture = outils_local.dossiers_cites(demande, projet)
            except Exception:
                log.exception("dossiers cités non lus")
                lecture = []
            web = not self._outils_locaux_web_raison()
            navigateur = bool(navigateur and web and not self._outils_locaux_navigateur_raison())
            if web:
                try:
                    raison = searxng_local.assurer()
                except Exception as exc:  # jamais un tour perdu pour le moteur de recherche
                    raison = f"{type(exc).__name__}"
                if raison:  # les outils restent : lire_page marche sans SearXNG, recherche_web le dira au modèle
                    log.warning("SearXNG indisponible pour le tour local : %s", raison)
                    bus.publish("notice", level="warning", channel=ch, text=WEB_INDISPONIBLE.format(raison=raison))
            kw = {"web": True, "navigateur": navigateur, "sur_page": sur_page} if web else {}
            return self._outils_locaux.repondre(message, lang, projet=projet, stop=stop, actions=actions,
                                                canal=ch, lecture=lecture, **kw), True
        # 05/11 (lot 8) : réflexion à la demande pour CETTE question (`reflexion=True`), sinon le défaut du moteur
        # (coupée) ; relecture : le défaut du moteur (active).
        return self._moteur_local.repondre(message, lang, stop=stop, reflexion=reflexion), False

    def _tour_local_reponse(self, demande: str, source: str, ch: str, mid: str, outils: bool = False,
                            reflexion: bool | None = None, navigateur: bool = False) -> None:
        e = self._local_etat()
        # Claude parle ou travaille déjà : la réponse locale est à lire, pas dite par-dessus.
        dire = not (self._turn_active or self.speaker.busy)
        self.status.set(state.THINKING, text="", unless=(state.ASKING, state.SPEAKING, state.LISTENING))
        splitter, morceaux, dit = SentenceSplitter(), [], False
        pages: list[str] = []

        def sur_page(url: str) -> None:
            # 05/11 (lot 7B, décision de l'utilisateur) : chaque adresse ouverte est visible ; jamais au journal en clair
            pages.append(url)
            bus.publish("notice", level="info", channel=ch, engine="local", text=PAGE_OUVERTE.format(url=url))

        flux, avec_outils = self._local_flux(demande, ch, outils, e["stop"], reflexion, navigateur, sur_page)
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
                # 05/11 (lot 8, R1) : une adresse que le tour n'a pas VUE (demande, faits, résultats d'outils) est
                # marquée « non vérifiée » à l'affichage ; l'historique du local garde le texte sans la marque.
                try:
                    lang = "en" if langue.anglais() else "fr"
                    vues = self._outils_locaux.derniere.get("urls_vues", []) if avec_outils else []
                    texte = moteur_local.marquer_urls(texte, [moteur_local.message_local(demande, lang), *vues], lang)
                except Exception:
                    log.exception("adresses de la réponse locale non contrôlées")
                if pages:  # 05/11 (lot 7B) : dans le fil, sous la réponse (affiché, jamais dit)
                    lang = "en" if langue.anglais() else "fr"
                    liste = "\n".join(f"- {u}" for u in pages[:PAGES_MAX]) + (
                        f"\n- … (+{len(pages) - PAGES_MAX})" if len(pages) > PAGES_MAX else "")
                    texte += f"\n\n{PAGES_TITRE[lang]}\n{liste}"
                # 05/11 (lot 6) : demande gardée en mémoire pour « Envoyer à Claude », jamais une demande hors ligne
                hl = hors_ligne.actif()
                garde = not hl and source in ("voix", "clavier") and self._local_memo_noter(mid, demande, ch)
                bus.publish("reply", text=texte, channel=ch, reply_to=mid, mid=uuid.uuid4().hex[:12],
                            engine="local", **({"hors_ligne": True} if hl else {}),
                            **({"vers_claude": True} if garde else {}))
            log.info("tour local fini en %.1f s (%d caractères%s)", time.monotonic() - t0, len(texte),
                     ", arrêté" if e["stop"].is_set() else "")
            if dit and source == "voix" and not self._in_conversation and self._followup_s() > 0:
                self._followup = True  # comme après une réponse de Claude dite : il peut enchaîner
            if not self.speaker.busy and not self._in_conversation and not self._turn_active:
                self.status.set(self._rest_state(), only=(state.THINKING, state.WORKING))

    # --- 05/11 (lot 6) : « Envoyer à Claude » sous une réponse locale

    def _local_memo(self) -> tuple[threading.Lock, OrderedDict]:
        """(verrou, demandes) : mid de la demande → {demande, channel, at}, EN MÉMOIRE seulement."""
        m = self.__dict__.get("_local_memo_d")
        if m is None:
            m = self.__dict__.setdefault("_local_memo_d", (threading.Lock(), OrderedDict()))
        return m

    def _local_memo_noter(self, mid: str, demande: str, ch: str) -> bool:
        if not mid or not str(demande or "").strip():
            return False
        verrou, memo = self._local_memo()
        with verrou:
            memo[mid] = {"demande": demande, "channel": ch, "at": time.time()}
            memo.move_to_end(mid)
            while len(memo) > MEMO_MAX:
                memo.popitem(last=False)
        return True

    @staticmethod
    def _local_hud_refus(name: str, cmd: dict) -> str:
        """HUD de l'utilisateur SEUL : refusé à Claude, à la bulle, au canal de contrôle et à WhatsApp."""
        if cmd.get("_from_claude") is True or cmd.get("_via") != "hud":
            log.warning("%s refusé : hors du HUD de l'utilisateur (%s)", name, cmd.get("_via") or "?")
            return f"{name} est réservé au HUD de l'utilisateur"  # même texte que history_query
        return ""

    def _cmd_local_demandes(self, name: str, cmd: dict) -> dict:
        """Demandes locales encore en mémoire (leurs mid, jamais le texte) : le HUD grise les autres boutons."""
        refus = self._local_hud_refus(name, cmd)
        if refus:
            return {"ok": False, "error": refus}
        verrou, memo = self._local_memo()
        with verrou:
            mids = list(memo)
        return {"ok": True, "mids": mids, "hors_ligne": hors_ligne.actif()}

    def _cmd_local_vers_claude(self, name: str, cmd: dict) -> dict:
        refus = self._local_hud_refus(name, cmd)
        if refus:
            return {"ok": False, "error": refus}
        return self._local_vers_claude(str(cmd.get("mid") or "").strip())

    def _local_vers_claude(self, mid: str) -> dict:
        """Renvoie à Claude la demande locale `mid` (celle de l'utilisateur) par la barrière du lot 3."""
        if hors_ligne.actif():
            return {"ok": False, "error": VC_HORS_LIGNE, "raison": "hors_ligne"}
        verrou, memo = self._local_memo()
        with verrou:
            item = memo.pop(mid, None)
        if item is None:
            return {"ok": False, "error": VC_OUBLIEE, "raison": "oubliee"}
        demande, ch = item["demande"], item["channel"]
        raisons = tuple(sensible.scan(demande, ()))
        if raisons:  # même règle que « avec Claude : » sur une donnée sensible : carte, rien ne part sans le clic
            dec = routeur.Decision(routeur.CLAUDE, "renvoi", demande, raisons, routeur.CARTE_SENSIBLE, routeur.CLAUDE,
                                   explicite=True)
            self._routage_journal(dec, "clavier")
            self._routage_retenir(dec, demande, "clavier", ch, None, None, avec_rester=False)
            return {"ok": True, "mid": mid, "carte": True}
        self._routage_journal(routeur.Decision(routeur.CLAUDE, "renvoi", demande, explicite=True), "clavier")
        log.info("demande locale renvoyée à Claude (%d caractères)", len(demande))
        bus.publish("notice", level="info", channel=ch, text=VC_NOTICE)
        threading.Thread(target=self.handle, args=(demande, "clavier", ch), kwargs={"route": routeur.CLAUDE},
                         name="local-vers-claude", daemon=True).start()
        return {"ok": True, "mid": mid, "envoye": True}
