"""Mission autonome bornée (`mission.py`) : commandes du HUD, outil `mission_start` (T25),
relance des tours, fin et arrêt d'urgence.

Mixin de `Astrel` (core.py), découpé du cœur le 29/10/2026 : méthodes déplacées telles quelles.

02/11 (plan P43) : vérification du critère de fin (`preuve.py`) lancée par la boucle de la mission
dans un fil court (`mission-preuve`, un seul à la fois), et décisions de l'utilisateur sur une vérification
bloquée (revérifier, remettre au travail, accepter sans preuve, approuver le critère d'Astrel).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from .. import config
from .. import gel
from .. import mission as mission_mod
from .. import preuve
from .. import totalgate
from ..channels import channels
from ..events import bus
from ..mission import mission
from ..core import _UTILISATEUR, log  # aides du cœur (le paquet charge core d'abord)


# P43 : décisions de l'utilisateur sur une vérification (mots lus par `totalgate.mission_action`).
_ACCEPTER = ("accept", "accepter", "accepte")
_REVERIFIER = ("reverify", "reverifier", "revérifier", "verifier", "vérifier")
_AU_TRAVAIL = ("back_to_work", "reprendre_travail", "travail", "au_travail")
_APPROUVER = ("approve", "approuver", "approuve")


class MissionCoeur:
    """Mission autonome bornée (`mission.py`) : commandes du HUD, outil `mission_start` (T25),"""

    def _publish_mission(self) -> None:
        bus.publish("mission", **mission.describe())

    def _mission_command(self, cmd: dict) -> dict:
        # L'action est lue comme le contrôle « réservé à l'utilisateur » la lit (`totalgate`) :
        # jamais deux lectures qui divergent (15/10).
        action = totalgate.mission_action(cmd) or "status"
        if (action.startswith(("start", "lance")) or action in ("resume", "reprendre", "reprise")
                or action in _REVERIFIER or action in _AU_TRAVAIL) and config.ESTOP_FILE.exists():
            return {"ok": False, "error": "arrêt d'urgence en cours : « Reprendre tout » d'abord"}
        if action.startswith(("start", "lance")):
            spec = dict(cmd.get("spec") or cmd)
            spec.setdefault("channel", channels.active)
            workdir = channels.workdir_of(str(spec.get("channel") or "")) or ""
            spec["base"] = workdir or str(config.WORKDIR)  # chemins relatifs du périmètre
            if not spec.get("scope") and workdir:
                spec["scope"] = [workdir]  # périmètre par défaut : le dossier relié au canal
            r = mission.start(spec, origine="utilisateur")  # P43 : origine fixée ici, jamais lue dans la spec
            if r.get("ok"):
                bus.publish("notice", level="info", text=f"Mission lancée : {spec.get('objective')}")
        elif action == "pause":
            r = mission.pause()
        elif action in ("resume", "reprendre", "reprise"):
            r = mission.resume()
        elif action in ("stop", "arret", "arrêt", "arrete", "arrête", "fin"):
            r = mission.stop()
            if r.get("ok"):
                if self._turn_active and self._turn_source == "mission":
                    self.brain.interrupt()
                self._mission_finished()
        # P43 : décisions sur une vérification. Hors de `_MISSION_TIGHTEN` : refusées à Claude par
        # `totalgate.loosens_guard` avant d'arriver ici.
        elif action in _ACCEPTER:
            r = mission.accept()
            if r.get("ok"):
                self._mission_finished()
        elif action in _REVERIFIER:
            r = mission.reverify()
        elif action in _AU_TRAVAIL:
            r = mission.back_to_work()
        elif action in _APPROUVER:
            r = mission.approve()
        else:
            r = {"ok": True, "mission": mission.describe()}
        self._publish_mission()
        return r

    def _mission_auto(self, cmd: dict) -> dict:
        """T25 (29/10, décision du 28/10) : Astrel lance LUI-MÊME une mission pour un objectif
        long (outil MCP `mission_start`). Plus strict que le bouton du HUD : seulement dans un tour lancé
        par l'utilisateur (jamais un réflexe, une tâche de fond, un agent), ni sous ESTOP ni avec un fournisseur
        autre que Claude ; périmètre obligatoire et DANS ses projets (`config.WORKDIR`, jamais tout le
        dossier) ; budget au plus celui par défaut. Une mission n'accorde aucun droit : elle ajoute la
        barrière d'écriture du garde-fou (`guard.mission_fence`). Reprise après une pause : l'utilisateur seul."""
        if not (self._turn_active and self._turn_source in _UTILISATEUR):
            return {"ok": False, "error": "une mission ne se lance que pendant un échange lancé par l'utilisateur"}
        if config.ESTOP_FILE.exists():
            return {"ok": False, "error": "arrêt d'urgence en cours : pas de mission"}
        if self._non_claude():
            return {"ok": False, "error": "jamais de mission avec un fournisseur autre que Claude"}
        channel = str(cmd.get("channel") or "") or channels.turn.channel or channels.active
        workdir = channels.workdir_of(channel) or ""
        base = workdir or str(config.WORKDIR)
        racine = config.WORKDIR.resolve()
        scope = mission_mod._abs_paths(cmd.get("scope") or ([workdir] if workdir else []), base)
        dehors = [p for p in scope if not (Path(p).resolve().is_relative_to(racine) and Path(p).resolve() != racine)]
        if not scope or dehors:
            dossiers = ", ".join(dehors) or "aucun dossier donné"
            return {"ok": False, "error": f"périmètre à choisir DANS les projets de l'utilisateur ({racine}), sans le "
                                          f"dossier entier : {dossiers}"}
        def plafond(cle: str, defaut: float) -> float:
            try:
                v = float(cmd.get(cle) or 0)
            except (TypeError, ValueError):
                v = 0
            return min(v, defaut) if v > 0 else defaut
        spec = {"objective": cmd.get("objective"), "done_when": cmd.get("done_when"), "scope": scope,
                "verification": cmd.get("verification"),
                "forbidden": cmd.get("forbidden"), "forbidden_paths": cmd.get("forbidden_paths"),
                "channel": channel, "base": base,
                "max_turns": plafond("max_turns", config.MISSION_MAX_TURNS),
                "max_minutes": plafond("max_minutes", config.MISSION_MAX_MINUTES),
                "max_tokens": config.MISSION_MAX_TOKENS}
        r = mission.start(spec, origine="claude")  # P43 : critère typé obligatoire, jamais approuvé d'office
        if r.get("ok"):
            d = r["mission"]
            objectif, dossiers, tours, minutes = d["objective"], ", ".join(d["scope"]), d["max_turns"], round(d["max_minutes"])
            log.warning("mission lancée par Astrel : %s (périmètre %s)", objectif, dossiers)
            bus.publish("notice", level="warning",
                        text=f"Astrel a lancé une mission : {objectif} (périmètre : {dossiers} ; {tours} tours, "
                             f"{minutes} min au plus). Pause ou Arrêter dans le bandeau violet.")
            self._publish_mission()
            r = {**r, "message": "Mission lancée : chaque tour finit par « MISSION : CONTINUE | FINIE | BLOQUÉE » ; "
                                 "FINIE déclenche la vérification du critère par Astrel. Dis-le à l'utilisateur en une phrase."}
        return r

    def _mission_finished(self) -> None:
        """Fin de mission (finie, bloquée, budget, arrêtée) : un compte rendu posé
        dans le HUD, dit seulement selon le réglage des comptes rendus."""
        text = mission.report()
        if not text:
            return
        bus.publish("notice", level="info", text=text)
        try:
            self._post_report(text, mission.describe().get("channel", ""))
        except Exception:
            log.exception("compte rendu de mission non posé")

    def _mission_estop(self) -> None:
        """Arrêt d'urgence : la mission (active ou en pause) est ARRÊTÉE, pas mise en
        pause (15/10, demande de l'utilisateur) ; elle ne repart que si on la relance.
        Rien n'est dit : seulement une notice."""
        if mission.status not in (mission_mod.ACTIVE, mission_mod.PAUSED):
            return
        mission.stop("arrêt d'urgence", status=mission_mod.STOPPED)
        self._publish_mission()
        text = mission.report()
        if text:
            bus.publish("notice", level="warning", text=text)

    def _mission_observe(self, ev) -> None:
        """Lot C (03/11) : chaque outil du flux (session principale ET sous-agents) passe par la
        mission, qui ne garde que la commande observée attendue et le repère de dernière écriture."""
        if not mission.active:
            return
        if ev.kind == "tool_start":
            mission.observe_tool_start(ev.tool, ev.tool_id, ev.data.get("input"))
        elif ev.kind == "tool_result":
            mission.observe_tool_result(ev.tool_id, bool(ev.data.get("is_error")), ev.text, bool(ev.data.get("fond")))

    def _mission_turn_end(self, text: str, data: dict | None) -> None:
        if not mission.awaiting:
            return
        status, _reason = mission.after_turn(text, (data or {}).get("usage"),
                                             by_mission=self._turn_source == "mission")
        self._publish_mission()
        if status in mission_mod.ENDED:  # P43 : « verification » n'est pas une fin
            self._mission_finished()

    def _mission_tick(self) -> None:
        """Relance le tour suivant d'une mission quand tout est calme : aucun tour,
        aucune confirmation, personne qui parle (un vrai message de l'utilisateur passe avant)."""
        if not mission.active:
            return
        if config.ESTOP_FILE.exists():
            self._mission_estop()
            return
        if self._local_active():
            return  # mode léger (plan P8) : la mission attend le retour sur Claude
        if self._turn_active or self._pending_confirm is not None or self._floor_taken():
            return
        phase = mission.phase
        if phase == mission_mod.VERIFICATION:
            if mission.verification_stuck(preuve.DUREE_MAX_S + 30):  # revue P43 : jamais coincée
                self._publish_mission()
                self._mission_waits_for_utilisateur()
                return
            self._mission_start_verification()
            return
        if phase == mission_mod.VERIF_BLOCKED:
            return  # aucun tour : l'utilisateur décide
        if not mission.awaiting:
            budget = mission.over_budget()
            if budget:
                mission.stop(budget, status="budget")
                self._publish_mission()
                self._mission_finished()
                return
        prompt = mission.take_prompt()
        if not prompt:
            return
        d = mission.describe()
        self._publish_mission()
        self.handle(prompt, source="mission", channel=str(d.get("channel") or ""),
                    shown=f"🎯 Mission, tour {d.get('turns')} sur {d.get('max_turns')} : {d.get('objective')}")

    # ------------------------------------------------------------------ preuve de fin (P43)

    def _mission_start_verification(self) -> None:
        """Phase « verification » : un contrôle à la fois, dans un fil court, pour que la boucle
        (ESTOP, tâches, quota) ne l'attende jamais. Critère libre : la mission attend l'utilisateur."""
        inst = mission.begin_verification()
        if inst is None:
            if mission.active and mission.phase == mission_mod.VERIF_BLOCKED:
                self._publish_mission()
                self._mission_waits_for_utilisateur()
            return
        self._publish_mission()
        threading.Thread(target=self._mission_verify, args=(inst,), name="mission-preuve", daemon=True).start()

    def _mission_verify(self, inst: dict) -> None:
        """Le contrôle lui-même (lecture seule), puis son application ATOMIQUE par `mission.py`,
        qui jette un résultat obsolète. `doit_arreter` : ESTOP, ou la mission a changé."""
        try:
            rapport = preuve.verifier(
                inst, doit_arreter=lambda: config.ESTOP_FILE.exists() or not mission.still_current(inst))
        except Exception:
            log.exception("vérification de la mission")
            rapport = {"verification_id": inst.get("verification_id"), "statut": preuve.BLOQUEE, "verifie": False,
                       "controles": [{"type": "erreur", "cible": "", "etat": preuve.BLOQUE, "code": "erreur",
                                      "infos": {}}]}
        etat, _raison = mission.apply_verification(inst, rapport)
        if not etat:
            return  # obsolète : jeté, l'état n'a pas bougé
        self._publish_mission()
        if etat in mission_mod.ENDED:
            self._mission_finished()
        elif etat == mission_mod.VERIF_BLOCKED:
            self._mission_waits_for_utilisateur()

    def _mission_waits_for_utilisateur(self) -> None:
        d = mission.describe()
        text = f"La mission attend ta décision : {d.get('objective', '')}. {d.get('reason', '')}"
        bus.publish("notice", level="warning", text=text)
        try:
            self._post_report(text, d.get("channel", ""))
        except Exception:
            log.exception("compte rendu de mission non posé")

    def _mission_loop(self) -> None:
        """Toutes les secondes : la mission, et la garde de l'arrêt d'urgence."""
        while self._running:
            time.sleep(1.0)
            gel.chien.battre("taches")  # 01/11 : chien de garde des boucles (`gel.py`)
            try:
                self._estop_watch()
                self._mission_tick()
                self._tasks_tick()
                self._reprise_tick()
                self._local_tick()
            except Exception:
                log.exception("mission")
            try:
                self._muettes_tick()  # 01/11 : sous-agent sans nouvelles, alerte seule
            except Exception:
                log.exception("tâches muettes")
            try:
                self._memoire_tick()  # plan P20 : toutes les 5 s, pas de thread de plus
            except Exception:
                log.exception("mémoire")
            try:
                self._courrier_tick()  # plan P33 : courrier entre agents, toutes les 5 s
            except Exception:
                log.exception("courrier")
            try:
                self._todo_tick()  # plan P38 lot 3 : « prêt à clore » suit les fiches de passation
            except Exception:
                log.exception("todo")
            try:
                self._quota_tick()  # 22/10 : quota relevé hors des tours (Claude Code utilisé ailleurs)
            except Exception:
                log.exception("relevé du quota")
            try:
                self._quota_arret.tick()  # plan P34 : arrêt des agents sur quota, puis reprise
            except Exception:
                log.exception("arrêt pour quota")
