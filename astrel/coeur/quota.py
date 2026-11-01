"""Quota de l'abonnement et reprise après coupure : relevé, limite atteinte, réflexe de
reprise, rejeu d'une panne passagère.

Mixin de `Astrel` (core.py), découpé du cœur le 29/10/2026 : méthodes déplacées telles quelles.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from .. import config
from .. import quota_releve
from .. import reprise
from .. import state
from ..brain import BrainEvent
from ..channels import TurnRef, channels
from ..events import bus
from ..settings import reglages
from ..core import QuotaTrend, _UTILISATEUR, log, quota_windows  # aides du cœur (le paquet charge core d'abord)


class QuotaCoeur:
    """Quota de l'abonnement et reprise après coupure : relevé, limite atteinte, réflexe de"""

    def _publish_quota(self, q: dict) -> None:
        """Évènement quota du HUD. Fenêtres en liste et prévision au rythme observé
        (contrat du 15/10) ; `brain.quota` garde le format du CLI (réflexes
        `quota_above`). « type » (five_hour…) écrasait le type de l'événement : le HUD
        recevait un « five_hour » et son panneau quota ne bougeait pas. Renommé
        `limit_type`. Fenêtre fermée : `limited`, `reset_at` de la limite et
        `forecast` « limite atteinte, reprise prévue à 16 h 10 »."""
        trend = self.__dict__.setdefault("_quota_trend", QuotaTrend())
        data = {k: v for k, v in q.items() if k != "type"}
        extra: dict[str, Any] = {"limited": False}
        if reprise.state.closed():
            extra = {"limited": True, "reset_at": float(reprise.state.info().get("reset_at") or 0)}
        bus.publish("quota", **{**data, "limit_type": q.get("type"), "windows": quota_windows(q),
                                "forecast": reprise.state.forecast() or trend.forecast(), **extra})

    def _quota_refresh_allowed(self) -> str:
        """Raison de NE PAS relever le quota maintenant ("" : permis). Abonnement Claude seulement."""
        eff = getattr(self.brain, "fournisseur", None)
        if eff is not None and getattr(eff, "genre", "abonnement") != "abonnement":
            return "relevé du quota : pas d'abonnement Claude en cours"
        if config.ESTOP_FILE.exists():
            return "relevé du quota : arrêt d'urgence en cours"
        return ""

    def _quota_tick(self, now: float | None = None) -> None:
        """`_mission_loop`, chaque seconde : relevé AUTOMATIQUE du quota, au plus une fois par
        `quota_releve.PERIODE_S` (10 min), seulement si le dernier relevé (flux compris) a plus de
        10 min, jamais pendant un tour (de l'utilisateur ou automatique), un agent, une carte de
        confirmation. Réglage `quota_refresh` (oui par défaut). Le relevé part dans un fil à lui."""
        now = time.time() if now is None else now
        last = self.__dict__.setdefault("_quota_refresh_last", now - quota_releve.PERIODE_S + 60)  # 1 min après le démarrage
        with self._tasks_lock:
            agents = any(t.get("kind") == "agent" for t in self._tasks.values())
        fast = self._quota_arret.periode(agents)
        if fast:
            # Plan P34 : des agents tournent près du seuil de note → `/usage` seul (0 jeton, jamais la
            # sonde haiku) toutes les 120 s, 60 s au-delà du seuil, même pendant un tour ou un agent.
            if (now - last < fast or now - float(getattr(self.brain, "quota_at", 0.0) or 0.0) < fast
                    or self._quota_refresh_allowed() or self.__dict__.get("_quota_refreshing")):
                return
            self._quota_refresh_last = now
            threading.Thread(target=self._quota_refresh, kwargs={"sonde": False}, name="quota-releve",
                             daemon=True).start()
            return
        if now - last < quota_releve.PERIODE_S or now - float(getattr(self.brain, "quota_at", 0.0) or 0.0) < quota_releve.PERIODE_S:
            return
        if not reglages.value("quota_refresh") or self._quota_refresh_allowed():
            return
        with self._tasks_lock:
            busy = self._turn_active or bool(self._tasks) or self._pending_confirm is not None
        if busy or self.__dict__.get("_quota_refreshing"):
            return
        self._quota_refresh_last = now
        threading.Thread(target=self._quota_refresh, name="quota-releve", daemon=True).start()

    def _quota_refresh(self, manual: bool = False, sonde: bool = True) -> dict:
        """Un relevé (`quota_releve.relever`) puis le chemin du flux : `brain.quota`, `QuotaTrend`,
        évènement `quota` du HUD, `_guard_quota`. Un seul à la fois."""
        lock = self.__dict__.setdefault("_quota_refresh_lock", threading.Lock())
        if not lock.acquire(blocking=False):
            return {"ok": False, "error": "relevé du quota déjà en cours"}
        self._quota_refreshing = True
        try:
            t0 = time.time()
            res = quota_releve.relever(connu=dict(getattr(self.brain, "quota", None) or {}).get("windows"), sonde=sonde)
            if not res.get("ok"):
                log.warning("%s", res.get("error"))
                return {"ok": False, "error": str(res.get("error") or "relevé du quota impossible")}
            with self.__dict__.setdefault("_quota_lock", threading.Lock()):
                # Le relevé dure jusqu'à 30 s (60 avec la sonde) : un `rate_limit_event` du flux
                # arrivé entre-temps est plus récent, il fait foi (relecture du 22/10).
                stale = float(getattr(self.brain, "quota_at", 0.0) or 0.0) >= t0
                q = dict(self.brain.quota) if stale else self.brain.apply_rate_limit(res["info"])
            if stale:
                log.info("quota relevé (%s) ignoré : le flux en a donné un plus récent pendant le relevé",
                         "bouton" if manual else "automatique")
                return {"ok": True, "source": res["source"], "tokens": res.get("jetons", 0),
                        "windows": quota_windows(q), "stale": True}
            log.info("quota relevé (%s, %s, %d jeton(s)) : %s", "bouton" if manual else "automatique", res["source"],
                     res.get("jetons", 0), ", ".join(f"{w['name']} {w['pct']} %" for w in quota_windows(q)))
            self._on_brain_event(BrainEvent("quota", data=q))
            return {"ok": True, "source": res["source"], "tokens": res.get("jetons", 0), "windows": quota_windows(q)}
        finally:
            self._quota_refreshing = False
            lock.release()

    def _quota_refresh_command(self) -> dict:
        """Bouton ↻ du panneau « Abonnement » (commande `quota_refresh`) : relevé tout de suite,
        sans la condition des 10 min ni le réglage automatique ; 30 s au moins entre deux."""
        why = self._quota_refresh_allowed()
        if why:
            return {"ok": False, "error": why}
        now = time.time()
        if now - self.__dict__.get("_quota_manual_at", 0.0) < quota_releve.MANUEL_ECART_S:
            return {"ok": False, "error": "quota relevé il y a moins de 30 s"}
        self._quota_manual_at = now
        self._quota_refresh_last = now  # le relevé automatique repart de là
        return self._quota_refresh(manual=True)

    def _limit_from_error(self, text: str) -> None:
        """Erreur « You've hit your session limit · resets 4:10pm (Europe/Paris) » :
        l'heure vient du dernier `rate_limit_event` refusé, sinon du texte."""
        q = dict(getattr(self.brain, "quota", None) or {})
        now = time.time()
        known = q.get("reset_at") if isinstance(q.get("reset_at"), (int, float)) else 0.0
        reset = known if q.get("status") == "rejected" and known > now else 0.0
        reset = reset or reprise.parse_reset(text, now) or (known if known > now else 0.0)
        self._limit_reached(reset, reprise.limit_kind(text) or str(q.get("limit_kind") or ""))

    def _limit_reached(self, reset_at: float, kind: str = "", publish: bool = True) -> None:
        """Limite atteinte : enregistrée (`runtime/reprise.json`), UN réflexe de reprise
        programmé, UNE alerte sans voix. Revue (tours de fond qui échouent l'un après
        l'autre, relance d'Astrel) : rien de plus."""
        now = time.time()
        self._retry_job = None  # un rejeu programmé se heurterait à la limite
        if reset_at <= now:
            if now - self._limit_unknown_at > 1800:
                self._limit_unknown_at = now
                log.warning("limite atteinte, heure de réinitialisation inconnue : pas de reprise automatique")
                bus.publish("notice", level="warning", limit=True,
                            text="Limite atteinte, heure de reprise inconnue : pas de reprise automatique.")
            return
        if not reprise.state.on_limit(reset_at, kind, now):
            return
        when = reprise.heure(reset_at, now)
        log.warning("limite %s atteinte : réinitialisation à %s, reprise programmée", kind or "de l'abonnement", when)
        self._schedule_reprise(reset_at)
        then = ("je reprendrai seul depuis CHANTIERS-EN-COURS." if reglages.value("reprise_auto") else
                "reprise automatique coupée (Réglages) : je te préviendrai seulement.")
        bus.publish("notice", level="warning", limit=True, reset_at=reset_at,
                    text=f"Limite atteinte, reprise prévue à {when}. Les réflexes qui réveillent le modèle "
                         f"attendent jusque-là ; {then}")
        if publish:
            self._publish_quota(dict(getattr(self.brain, "quota", None) or {}))

    @staticmethod
    def _schedule_reprise(reset_at: float, at: float | None = None) -> None:
        """Pose (ou déplace) LE réflexe de reprise : id fixe, jamais deux."""
        from ..senses import reflex

        rule = reprise.reflex_rule(reset_at)
        if at is not None:
            rule["when"]["ts"] = round(at)
        try:
            reflex.create_rule(rule)
        except (OSError, ValueError) as exc:
            log.warning("réflexe de reprise non posé : %s", exc)

    @staticmethod
    def _unschedule_reprise() -> None:
        from ..senses import reflex

        try:
            if any(r.get("id") == reprise.REFLEX_ID for r in reflex.load_rules()):
                reflex.delete_rule(reprise.REFLEX_ID)
        except (LookupError, OSError, ValueError) as exc:
            log.warning("réflexe de reprise non retiré : %s", exc)

    def _reprise_boot(self) -> None:
        """Après une relance : le fichier fait foi. Reprise en attente sans réflexe →
        reposé ; réflexe sans reprise en attente (déjà lancée, annulée) → retiré."""
        from ..senses import reflex

        info = reprise.state.info()
        present = any(r.get("id") == reprise.REFLEX_ID for r in reflex.load_rules())
        if info.get("pending") and not present:
            self._schedule_reprise(float(info.get("reset_at") or 0))
        elif not info.get("pending") and present:
            self._unschedule_reprise()

    def _reflex_cascade(self) -> str:
        """Raison du refus si le tour en cours vient d'un réflexe (vide sinon) : un
        réflexe ne crée ni ne réactive un autre réflexe (15/10, HERMES D7), sinon une
        consigne « tous les lundis… » se reprogrammerait en boucle. Si l'utilisateur parle
        pendant ce tour, il devient le sien (`_begin_turn`) : permis de nouveau."""
        if self._turn_active and self._turn_source == "réflexe":
            return ("refusé : un tour lancé par un réflexe ne crée ni ne réactive de réflexe "
                    "(pas de cascade). Propose-le à l'utilisateur dans ton compte rendu.")
        return ""

    def _reflex_gate(self, text: str, channel: str, rid: str) -> dict | None:
        """Réflexe `ask` arrivé par `say` : celui de la reprise est vérifié ici, les
        autres sont GELÉS pendant la fenêtre fermée (gardés, relancés après). None :
        il passe normalement."""
        if rid == reprise.REFLEX_ID:
            return self._reprise_fire()
        if reprise.state.hold(text, channel, rid):
            log.info("limite atteinte : réflexe « %s » gelé jusqu'à la reprise", rid or text[:40])
            return {"ok": True, "held": True}
        return None

    def _reprise_fire(self) -> dict:
        """Le réflexe de reprise est parti. La reprise n'est lancée qu'UNE fois, effacée
        avant l'envoi ; rien si l'utilisateur a repris la main (annulée) ; repoussée d'une
        minute si un tour tourne ou s'il a la parole."""
        info = reprise.state.info()
        now = time.time()
        if not info.get("pending"):
            self._unschedule_reprise()  # réflexe orphelin : reprise déjà lancée ou annulée
            return {"ok": True, "reprise": "rien à reprendre"}
        reset = float(info.get("reset_at") or 0)
        if now < reset:
            self._schedule_reprise(reset)
            return {"ok": True, "reprise": "pas encore"}
        why = ("arrêt d'urgence" if config.ESTOP_FILE.exists() else
               "tour en cours" if self._turn_active else self._floor_taken())
        if why:
            log.info("reprise repoussée d'une minute : %s", why)
            self._schedule_reprise(reset, at=now + 60)
            self._reprise_next_try = now + 60
            return {"ok": True, "reprise": "repoussée"}
        if not reprise.state.launch(now):
            return {"ok": True, "reprise": "déjà lancée"}
        self._unschedule_reprise()
        if self._local_active():
            # Plan P8 (choix recommandé n° 4) : retour sur Claude automatique à la réinitialisation.
            back = self._local_switch(False, by="reprise", force=True)
            if not back.get("ok"):
                log.warning("retour sur Claude impossible : %s", back.get("error"))
        if not reglages.value("reprise_auto"):
            log.info("limite levée : reprise automatique coupée par le réglage")
            bus.publish("notice", level="info", text="Limite levée : Claude répond de nouveau. Reprise automatique "
                                                     "coupée (Réglages), je ne relance rien.")
            return {"ok": True, "reprise": "coupée"}
        log.warning("limite levée : reprise depuis CHANTIERS-EN-COURS")
        bus.publish("notice", level="info",
                    text="Limite levée : je reprends depuis CHANTIERS-EN-COURS, en vérifiant d'abord l'état réel.")
        threading.Thread(target=self.handle, args=(reprise.PROMPT, "réflexe"), name="reprise", daemon=True).start()
        return {"ok": True, "reprise": "lancée"}

    def _reprise_user_back(self) -> None:
        """Un tour de l'utilisateur a abouti : il a repris la main, pas de reprise automatique."""
        if reprise.state.cancel():
            self._unschedule_reprise()
            log.info("reprise automatique annulée : l'utilisateur a repris la conversation")
            bus.publish("notice", level="info", text="Reprise automatique annulée : tu as repris la main.")

    def _reprise_tick(self) -> None:
        """Chaque seconde (`_mission_loop`) : relance UN réflexe gelé quand la fenêtre
        est rouverte, la reprise partie (ou passée depuis 2 min) et tout calme."""
        if not config.REPRISE_FILE.exists():
            return
        info = reprise.state.info()
        reset = float(info.get("reset_at") or 0)
        now = time.time()
        if info.get("pending") and now >= max(reset + config.REPRISE_DELAY_S, self._reprise_next_try):
            # 19/10 (plan P8) : la reprise part d'ici, sans le capteur REFLEX (éteint par défaut,
            # chez un ami elle ne partait jamais). Le réflexe `at` reste en doublon : `launch` ne
            # vaut qu'une fois, le second appel ne fait rien.
            self._reprise_fire()
            info = reprise.state.info()
        if not info.get("held"):
            return
        if info.get("pending") and time.time() < reset + config.REPRISE_DELAY_S + 120:
            return  # fenêtre fermée, ou la reprise doit passer d'abord
        if (config.ESTOP_FILE.exists() or self._turn_active or self._pending_confirm is not None
                or self._floor_taken()):
            return
        item = reprise.state.take_held()
        if item and item.get("text"):
            log.info("réflexe gelé relancé : %s", item.get("reflex") or str(item["text"])[:40])
            self.handle(str(item["text"]), source="réflexe", channel=str(item.get("channel") or ""))

    def _retry_or_warn(self, text: str) -> bool:
        """529, 5xx, réseau : rejoue le tour 15, 30 puis 60 s plus tard, SEULEMENT si
        rien n'a été dit, écrit ni exécuté. Vrai si l'erreur est prise en charge ici
        (rejeu programmé, ou alerte « des actions ont peut-être tourné », sans voix)."""
        short = " ".join(str(text).split())[:120]
        if self._turn_acted:
            log.warning("panne passagère après des outils : pas de rejeu (%s)", short)
            self._chime("error")
            bus.publish("notice", level="warning",
                        text=f"Panne passagère du service ({short}) en plein travail : je ne rejoue pas. "
                             f"Des actions ont peut-être déjà tourné, vérifie avant de relancer.")
            return True
        err = str(text or "").strip()
        content = "".join(self._reply).replace(err, "") if err else "".join(self._reply)
        said = any(line.strip() and not line.strip().lower().startswith("api error")
                   for line in content.splitlines())
        sent = list(self._turn_sent or [])
        attempt = self._turn_retry
        delays = config.RETRY_DELAYS_S
        if (said or not sent or self._turn_source not in (*_UTILISATEUR, "réflexe")
                or attempt >= len(delays) or config.ESTOP_FILE.exists() or reprise.state.closed()):
            if attempt >= len(delays):
                log.warning("panne passagère : %d rejeux sans succès, abandon", attempt)
            return False
        with channels.lock:
            turn = channels.turn
        images = [img for _, imgs in sent for img in (imgs or [])]
        job = {"prompt": "\n\n".join(p for p, _ in sent), "images": images, "source": self._turn_source,
               "channel": turn.channel, "reply_to": turn.reply_to, "floating": turn.floating,
               "attempt": attempt + 1,
               "failed_at": time.time()}
        self._retry_job = job
        delay = delays[attempt]
        log.warning("panne passagère (%s) : rejeu %d sur %d dans %.0f s", short, attempt + 1, len(delays), delay)
        bus.publish("notice", level="warning",
                    text=f"Panne passagère du service ({short}) : rien n'a encore été fait, je rejoue la demande "
                         f"dans {delay:.0f} s (essai {attempt + 1} sur {len(delays)}).")
        self._later(delay, job)
        return True

    def _later(self, delay: float, job: dict) -> None:
        timer = threading.Timer(delay, self._replay, args=(job,))
        timer.daemon = True
        timer.start()

    def _cancel_retry(self, why: str) -> None:
        if self._retry_job is not None:
            self._retry_job = None
            log.info("rejeu annulé : %s", why)
            bus.publish("notice", level="info", text=f"Rejeu annulé : {why}.")

    def _replay(self, job: dict) -> None:
        """Rejoue le tour tombé en panne (même texte, même canal), sans le réafficher."""
        if self._retry_job is not job:
            return  # annulé (nouveau message, limite) ou remplacé
        why = ("arrêt d'urgence" if config.ESTOP_FILE.exists() else
               "limite atteinte" if reprise.state.closed() else
               "tu as envoyé un nouveau message" if self._last_user_at > job["failed_at"] else "")
        if why:
            self._cancel_retry(why)
            return
        with self._send_lock:
            if self._retry_job is not job:
                return
            if self._turn_active or self._pending_confirm is not None or self._floor_taken():
                self._later(5.0, job)  # un tour de fond, une question, l'utilisateur qui parle : on attend
                return
            self._retry_job = None
            self._begin_turn(job["source"])
            self._turn_retry = job["attempt"]
            self._turn_sent = [(job["prompt"], job["images"] or None)]
            with channels.lock:
                channels.turn = TurnRef(job["channel"], job["reply_to"], floating=bool(job.get("floating")))
            log.info("rejeu du tour après une panne passagère (essai %d)", job["attempt"])
            self.status.set(state.THINKING, unless=(state.ASKING, state.SPEAKING, state.LISTENING))
            try:
                self.brain.send(job["prompt"], images=job["images"] or None)
            except Exception as exc:
                log.exception("rejeu impossible : %s", exc)
                self._chime("error")
                self.say(config.SAY_ERROR, raw=True, live=True)
                self._end_turn(followup=False)
