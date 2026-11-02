"""Confirmations des actions sensibles (oral, HUD, clavier) et ce qui les règle :
mode Sans confirmation (Total), levée du garde-fou, cases de confirmation.

Mixin de `Astrel` (core.py), découpé du cœur le 29/10/2026 : méthodes déplacées telles quelles.
"""
from __future__ import annotations

import os
import re
import threading
import time
import uuid
from typing import Any, Callable

from .. import langue  # 02/11 : oui anglais en conversation anglaise
from .. import audit
from .. import config
from .. import guard
from .. import guard as guard_mod
from .. import levee
from .. import limites
from .. import state
from .. import totalgate
from ..cases_confirmation import cases as cases_confirmation
from ..events import bus
from ..floor import floor
from ..settings import reglages
from ..core import _Abort, _cats_texte, _wants_30, log  # aides du cœur (le paquet charge core d'abord)


class ConfirmationsCoeur:
    """Confirmations des actions sensibles (oral, HUD, clavier) et ce qui les règle :"""

    def set_total(self, on: bool, by: str) -> dict:
        on = bool(on)
        with self._local_lock:  # pas d'activation pendant une bascule vers le mode léger (plan P8)
            if on and (self._local_active() or self._local_switching):
                return {"ok": False, "error": "jamais le mode Sans confirmation avec un modèle local : reviens d'abord sur Claude"}
            if on and self._non_claude():
                return {"ok": False, "error": "jamais le mode Sans confirmation avec un fournisseur autre que Claude : "
                                              "repasse d'abord sur Claude (Réglages › Fournisseur)"}
            changed = on != self._total
            self._total = on
            self._total_since = time.time() if on else 0.0
        if changed:
            log.warning("mode AUTONOME TOTAL %s (%s)", "ACTIVÉ" if on else "coupé", by)
            self._total_log("MODE", "activé" if on else "coupé", by)
            bus.publish("notice", level="warning" if on else "info",
                        text="⚠ Mode Sans confirmation activé : plus aucune confirmation (sauf le garde-fou)."
                        if on else "Mode Sans confirmation coupé : les actions graves sont de nouveau confirmées.")
        bus.publish("total", on=on, since=self._total_since, by=by)
        if on:
            pending = self._pending_confirm
            if pending and not pending.get("guard") and pending["box"]["allow"] is None:
                pending["box"].update(allow=True, by="total")  # la question affichée passe aussi
                pending["event"].set()
        return {"ok": True, "total": on}

    def _total_command(self, cmd: dict) -> dict:
        # Qui a le droit : `totalgate` (fichier protégé, 15/10).
        want = totalgate.total_request(cmd)
        if not want:
            return {"ok": True, "total": self._total, "since": self._total_since}
        if want == "off":
            return self.set_total(False, "HUD" if cmd.get("_via") == "hud" else "terminal")  # resserrer : toujours permis
        if want != "on":
            return {"ok": False, "error": "attendu : on ou off"}
        if not totalgate.may_enable_total(cmd):
            log.warning("Autonome TOTAL refusé : %s", {k: cmd.get(k) for k in ("_via", "_from_claude")})
            return {"ok": False, "error": "Le mode Sans confirmation ne s'active que par l'interrupteur du HUD ou à la voix"}
        return self.set_total(True, "HUD")

    @staticmethod
    def _total_log(tool: str, what: str, extra: str = "") -> None:
        """Journal en AJOUT SEUL (logs/autonome-total.log, 0600) : date, outil,
        commande tronquée. Protégé dans le garde-fou (modification critique)."""
        line = "\t".join((time.strftime("%Y-%m-%d %H:%M:%S"), tool, re.sub(r"\s+", " ", what)[:200], extra))
        try:
            fd = os.open(config.TOTAL_LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(line.rstrip("\t") + "\n")
        except OSError as exc:
            log.warning("journal Autonome TOTAL non écrit : %s", exc)

    def set_lift(self, on: bool, by: str) -> dict:
        with self._lift_lock, self._local_lock:  # `_local_lock` : pas pendant une bascule vers le mode léger
            return self._set_lift(bool(on), by)

    def _set_lift(self, on: bool, by: str) -> dict:
        if on and config.ESTOP_FILE.exists():
            return {"ok": False, "error": "arrêt d'urgence en cours : « Reprendre tout » d'abord."}
        if on and (self._local_active() or self._local_switching):
            return {"ok": False, "error": "jamais de levée du garde-fou avec un modèle local : reviens d'abord sur Claude."}
        if on and self._non_claude():
            return {"ok": False, "error": "jamais de levée du garde-fou avec un fournisseur autre que Claude : repasse "
                                          "d'abord sur Claude (Réglages › Fournisseur)."}
        if on == self._lift:
            self._publish_lift()
            return {"ok": True, "lift": on}
        who = "utilisateur" if by in ("HUD", "terminal") else "astrel"
        if on:
            self._lift, self._lift_since, self._lift_changed = True, time.time(), []
            watch = levee.Watch(on_change=self._lift_files, on_remind=self._lift_remind)
            try:
                watch.start()
            except Exception:
                log.exception("levée : surveillance non lancée")
            self._lift_watch = watch
            log.warning("garde-fou LEVÉ (%s)", by)
            self._lift_log("LEVÉE", "activée", by)
            audit.record("levee", who, "Levée du garde-fou activée", by)
            text = ("⚠ Garde-fou levé : Claude modifie sans question le code du HUD, de la bulle, de WhatsApp et "
                    "du garde-fou. sudo, push, envois, suppressions, paiements, déploiements restent demandés.")
            bus.publish("notice", level="warning", text=text)
        else:
            watch, self._lift_watch = self._lift_watch, None
            files = watch.stop() if watch is not None else list(self._lift_changed)
            self._lift, self._lift_since, self._lift_changed = False, 0.0, []
            log.warning("garde-fou rétabli (%s) ; modifiés : %s", by, ", ".join(files) or "aucun")
            self._lift_log("LEVÉE", "coupée", f"{by} ; modifiés : {', '.join(files) or 'aucun'}")
            audit.record("levee", who, "Levée du garde-fou coupée", f"{by} ; {len(files)} fichier(s) modifié(s)")
            bus.publish("notice", level="warning" if files else "info", text=levee.summary(files))
        self._publish_lift()
        if on:
            pending = self._pending_confirm
            if pending and pending.get("lift") and pending["box"]["allow"] is None:
                pending["box"].update(allow=True, by="levée")  # la question affichée passe aussi
                pending["event"].set()
        threading.Thread(target=reglages.publish, name="settings-levee", daemon=True).start()
        return {"ok": True, "lift": on, "text": "Garde-fou levé." if on else "Garde-fou rétabli."}

    def _publish_lift(self) -> None:
        bus.publish("levee", on=self._lift, since=self._lift_since, changed=list(self._lift_changed))

    def _lift_files(self, files: list[str]) -> None:
        """Surveillance (`levee.Watch`) : fichiers du garde-fou ou du HUD changés depuis la levée."""
        if self._lift:
            self._lift_changed = list(files)
            self._publish_lift()

    def _lift_remind(self) -> None:
        if self._lift:
            since = time.strftime("%H:%M", time.localtime(self._lift_since))
            bus.publish("notice", level="warning",
                        text=f"Rappel : le garde-fou est levé depuis {since}. « Rétablir » quand tu as fini.")

    def _lift_view(self) -> dict:
        """Ligne « Levée du garde-fou » de la page Réglages (genre `action`)."""
        if self._lift:
            since = time.strftime("%H:%M", time.localtime(self._lift_since))
            lines = [{"label": "État", "text": f"LEVÉ depuis {since}", "level": "warn"}]
            if self._lift_changed:
                lines.append({"label": "Modifiés depuis", "level": "warn",
                              "text": ", ".join(self._lift_changed[:8])
                              + (f" (+{len(self._lift_changed) - 8})" if len(self._lift_changed) > 8 else "")})
            lines.append({"label": "Après", "level": "info",
                          "text": "rétablis, relis les modifications, puis « ./run.sh doctor --accept » dans ton terminal."})
            return {"value": {"lines": lines}, "actions": [{"id": "retablir", "label": "Rétablir le garde-fou"}]}
        lines = [{"label": "État", "text": "en place", "level": "ok"},
                 {"label": "Levé", "level": "info",
                  "text": "Claude modifie sans question le code du HUD, de la bulle, de WhatsApp et du garde-fou. "
                          "sudo, push, envois, suppressions, paiements, déploiements, journal d'audit, arrêt "
                          "d'urgence restent demandés. Retombe à « en place » à chaque relance."}]
        action = {"id": "lever", "label": "Lever le garde-fou", "danger": True, "word": "lever",
                  "confirm": "Claude pourra réécrire le HUD, la bulle, WhatsApp et le garde-fou lui-même sans te "
                             "demander. Un bandeau orange reste affiché tant que c'est levé."}
        if config.ESTOP_FILE.exists():
            action.update(disabled=True, reason="arrêt d'urgence en cours : « Reprendre tout » d'abord.")
        return {"value": {"lines": lines}, "actions": [action]}

    def _cases_get(self, gid: str) -> dict:
        coches = cases_confirmation.coches()
        return {c.id: coches[c.id] for c in limites.cases_du_groupe(gid)}

    def _cases_put(self, gid: str, value: dict) -> dict:
        ids = {c.id for c in limites.cases_du_groupe(gid)}
        return self._cases_change({k: v for k, v in value.items() if k in ids}, "L'utilisateur (HUD)")

    def _cases_change(self, nouvelles: dict, who: str) -> dict:
        try:
            changes = cases_confirmation.changer(nouvelles, who)
        except (ValueError, OSError) as exc:
            log.warning("cases de confirmation non changées : %s", exc)
            return {"ok": False, "error": f"cases non enregistrées : {exc}"}
        if changes:
            now = cases_confirmation.coches()
            audit.record("confirmations", who, "Cases de confirmation changées",
                         ", ".join(f"{k} {'cochée' if now.get(k) else 'décochée'}" for k in changes))
            # Prise d'effet immédiate : la carte affichée passe si ses catégories sont maintenant décochées.
            pending = self._pending_confirm
            if (pending and pending["box"]["allow"] is None and totalgate.confirmation_free(
                    pending.get("cats"), cases_confirmation.coches(), config.ESTOP_FILE.exists(), self._non_claude())):
                pending["box"].update(allow=True, by="case décochée")
                pending["event"].set()
            threading.Thread(target=reglages.publish, name="settings-cases", daemon=True).start()
        return {"ok": True}

    def _cases_view(self) -> dict:
        coches, raison = cases_confirmation.etat()
        lines = [{"label": "État", "text": limites.resume(coches),
                  "level": "warn" if limites.tout_decoche(coches) else "ok"}]
        if raison and raison != "absent":
            lines.append({"label": "Fichier", "level": "warn",
                          "text": f"fichier des cases {raison} : valeurs par défaut (sensible coché)"})
        if config.ESTOP_FILE.exists():
            lines.append({"label": "Arrêt d'urgence", "level": "warn",
                          "text": "en cours : les cases décochées ne s'appliquent pas tant qu'il dure."})
        actions = [
            {"id": "tout_decocher", "label": "Tout décocher", "danger": True,
             "confirm": "Plus aucune carte de confirmation : Claude agira sans te demander, garde-fou compris. "
                        "« Stop tout » arrête toujours tout."},
            {"id": "tout_cocher", "label": "Tout cocher"},
            {"id": "defauts", "label": "Valeurs par défaut"},
        ]
        return {"value": {"lines": lines}, "actions": actions}

    def _cases_action(self, action: str, cmd: dict) -> dict:
        if not totalgate.may_change_confirmations(cmd):
            log.warning("cases de confirmation : changement refusé (%s)",
                        {k: cmd.get(k) for k in ("_via", "_from_claude")})
            return {"ok": False, "error": "les cases de confirmation ne se changent que depuis les Réglages du HUD"}
        wanted = {"tout_decocher": limites.coches_toutes(False), "tout_cocher": limites.coches_toutes(True),
                  "defauts": limites.coches_defaut()}.get(action)
        if wanted is None:
            return {"ok": False, "error": f"action inconnue : {action}"}
        res = self._cases_change(wanted, "L'utilisateur (HUD)")
        if res.get("ok") is False:
            return res
        now = cases_confirmation.coches()
        return {"ok": True, "text": limites.resume(now), "level": "warn" if limites.tout_decoche(now) else "ok"}

    def _lift_action(self, action: str, cmd: dict) -> dict:
        """Boutons « Lever » / « Rétablir ». Qui a le droit : `totalgate` (fichier protégé)."""
        if action == "lever":
            if not totalgate.may_enable_lift(cmd):
                log.warning("levée du garde-fou refusée : %s", {k: cmd.get(k) for k in ("_via", "_from_claude")})
                return {"ok": False, "error": "la levée du garde-fou ne s'active que depuis les Réglages du HUD"}
            return self.set_lift(True, "HUD")
        if action == "retablir":
            if not totalgate.may_disable_lift(cmd):
                log.warning("rétablissement du garde-fou refusé : %s",
                            {k: cmd.get(k) for k in ("_via", "_from_claude")})
                return {"ok": False, "error": "seul l'utilisateur rétablit le garde-fou (HUD ou terminal)"}
            return self.set_lift(False, "HUD" if cmd.get("_via") == "hud" else "terminal")
        return {"ok": False, "error": f"action inconnue : {action}"}

    @staticmethod
    def _lift_log(tool: str, what: str, extra: str = "") -> None:
        """Journal en AJOUT SEUL (logs/garde-fou-leve.log, 0600, protégé par le garde-fou) :
        activation, coupure, et chaque action passée sans question grâce à la levée."""
        line = "\t".join((time.strftime("%Y-%m-%d %H:%M:%S"), tool, re.sub(r"\s+", " ", what)[:200], extra))
        try:
            fd = os.open(config.LIFT_LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(line.rstrip("\t") + "\n")
        except OSError as exc:
            log.warning("journal de la levée du garde-fou non écrit : %s", exc)

    def _ask_confirmation(self, tool: str, desc: str, cmd: str, key: str = "", *,
                          keys: list[str] | None = None, guard: bool = False, cwd: str = "",
                          received_at: float | None = None, alive: Callable[[], bool] | None = None,
                          lift: bool = False, cats: list[str] | None = None) -> bool:
        """Enveloppe de `_confirm_decision` : chaque question et qui l'a tranchée vont
        au journal d'audit (plan P3, lot E). 24/10 (P29 lot B) : `cats` = catégories de l'action
        (`Verdict.cats`, du hook ; None : ancien hook), notées au journal."""
        allow, by, always = self._confirm_decision(tool, desc, cmd, key, keys=keys, guard=guard, cwd=cwd,
                                                   received_at=received_at, alive=alive, lift=lift, cats=cats)
        audit.record("confirmation", "claude", f"{tool} : {desc} | {cmd}",
                     (by or "délai") + (" (oui pour 30 min)" if always else "") + (" [garde-fou]" if guard else "")
                     + (f" [{_cats_texte(cats)}]" if cats else ""),  # 25/10 : non-texte sans plantage
                     ok=allow)
        return allow

    def _confirm_decision(self, tool: str, desc: str, cmd: str, key: str = "", *,
                          keys: list[str] | None = None, guard: bool = False, cwd: str = "",
                          received_at: float | None = None,
                          alive: Callable[[], bool] | None = None, lift: bool = False,
                          cats: list[str] | None = None) -> tuple[bool, str, bool]:
        """(permis, qui a tranché, « oui pour 30 min ») — appelé par `_ask_confirmation`.

        Appelé par le hook (thread socket). À l'oral ET sur le HUD et la bulle :
        la première réponse l'emporte.

        14/10 (demande de l'utilisateur) : la question RESTE jusqu'à une réponse
        explicite, cliquée ou dite. Avant, elle s'annulait après deux essais à
        l'oral et 25 s d'écran. Désormais, après les essais à l'oral, le micro est
        rendu (« Hey Jarvis » remarche) et la question reste affichée jusqu'à
        CONFIRM_TIMEOUT_S (≈ 12 h depuis le 15/10) ; un « oui » ou un « non » dit plus tard compte
        encore (`conversation`). Seul ce délai, ou le hook lui-même, la clôt.

        15/10 : `keys` = TOUTES les autorisations « 30 min » nécessaires (une par
        étiquette) ; le délai compte depuis l'ARRIVÉE de la demande
        (`received_at`), pas depuis la fin de la question précédente ; une
        demande dont le hook a raccroché (`alive()` faux) n'est pas posée, ou est
        retirée de l'écran. Autonome TOTAL : accordé d'office, sauf `guard`."""
        keys = [k for k in (keys if keys is not None else ([key] if key else []))]
        grantable = bool(keys) and all(keys)
        received_at = received_at or time.time()
        deadline = received_at + config.CONFIRM_TIMEOUT_S  # le hook, lui, abandonne (et refuse) peu après
        alive = alive or (lambda: True)

        def covered() -> bool:
            return grantable and all(guard_mod.granted(k) for k in keys)

        non_claude = self._non_claude()

        def free_ok() -> bool:
            # 24/10 (P29 lot B, demande de l'utilisateur) : toutes les catégories de l'action sont DÉCOCHÉES
            # dans Réglages › Garde-fou : pas de carte (tout décoché = aucune carte, sauf ESTOP).
            if totalgate.confirmation_free(cats, cases_confirmation.coches(), config.ESTOP_FILE.exists(),
                                           non_claude):
                log.info("case décochée : accordé sans carte (%s, %s) : %s", desc, _cats_texte(cats), cmd)
                return True
            return False

        def total_ok() -> bool:
            # 24/10 (P29 lot B) : les catégories partent aussi à Total (plafond et familles jamais libres).
            if totalgate.total_grants(self._total, guard, config.ESTOP_FILE.exists(), cats=cats,
                                      non_claude=non_claude):
                self._total_log(tool, cmd or desc, desc)
                log.info("Autonome TOTAL : accordé sans question (%s) : %s", desc, cmd)
                return True
            return False

        def lift_ok() -> bool:
            # 18/10 (plan P7) : garde-fou levé et action ENTIÈREMENT levable (`Verdict.lift`).
            # 24/10 (P29 lot B) : et toutes ses catégories levables (« git push && sed -i guard » : carte).
            if totalgate.lift_grants(self._lift, lift, config.ESTOP_FILE.exists(), cats=cats, non_claude=non_claude):
                self._lift_log(tool, cmd or desc, desc)
                log.info("garde-fou levé : accordé sans question (%s) : %s", desc, cmd)
                return True
            return False

        if free_ok():
            return True, "case décochée", False
        if total_ok():
            return True, "total", False
        if lift_ok():
            return True, "levée", False
        if covered():
            # Plusieurs agents demandent la même chose : chacun avait jugé AVANT
            # le « oui pour 30 min » donné à la question précédente.
            log.info("déjà autorisé pour 30 min (%s) : %s", ", ".join(keys), cmd)
            return True, "30 min", False
        with self._confirm_lock:
            if free_ok():  # 24/10 (P29 lot B) : case décochée pendant l'attente
                return True, "case décochée", False
            if total_ok():
                return True, "total", False
            if lift_ok():
                return True, "levée", False
            if covered():
                log.info("autorisé pendant l'attente (%s) : %s", ", ".join(keys), cmd)
                return True, "30 min", False
            if time.time() >= deadline or not alive():
                # Restée en file derrière une autre question : le hook a déjà
                # abandonné (ou va le faire), la poser maintenant ne servirait à rien.
                log.info("demande périmée, non posée (%s) : %s | %s", "délai" if alive() else "hook parti",
                         desc, cmd)
                return False, "délai" if alive() else "abandon", False
            cid = uuid.uuid4().hex[:10]
            answered = threading.Event()
            # `tag` : étiquette de la question dite (`speaker.cancel`), pour la faire taire seule.
            box: dict[str, Any] = {"allow": None, "by": "", "always": False, "tag": f"confirm-{cid}"}
            question = f"Attention, {desc}. Je continue ?"
            self._pending_confirm = {"id": cid, "event": answered, "box": box, "question": question,
                                     "guard": guard, "lift": lift, "cats": cats}
            log.info("confirmation %s : %s | %s | %s%s", cid, tool, desc, cmd, f" | dans {cwd}" if cwd else "")
            previous = self.status.current
            self.status.set(state.ASKING, text=question, detail=desc)
            # `guard` : sur WhatsApp, une action sur le garde-fou ne se valide que par « oui garde-fou » (17/10).
            bus.publish("confirm", id=cid, pending=True, tool=tool, desc=desc, cmd=cmd, question=question,
                        grantable=grantable, expires=deadline, cwd=cwd, guard=bool(guard))
            stop_watch = threading.Event()

            def watch_hook() -> None:
                # Le hook raccroche (délai du CLI, tour interrompu) : la question disparaît.
                while not stop_watch.wait(0.5):
                    if not alive():
                        if box["allow"] is None:
                            box.update(allow=False, by="abandon")
                            answered.set()
                        return

            threading.Thread(target=watch_hook, name="confirm-watch", daemon=True).start()

            def announce_answer() -> None:
                # 15/10 : la question disparait de TOUS les ecrans (HUD, bulle) a l'instant de la
                # reponse. Avant, `pending=False` n'etait publie qu'apres la fin de la question
                # dite et de l'ecoute a l'oral (5 a 10 s) : il fallait repondre deux fois.
                # 15/10 : et la question DITE se tait au même instant (l'écoute à
                # l'oral, elle, s'arrête par son `abort`, posé par la même réponse).
                while not stop_watch.is_set():
                    if answered.wait(0.2) and box["allow"] is not None:
                        if box["by"] != "voix":
                            self._silence_question(box["tag"])
                        if not box.get("announced"):
                            box["announced"] = True
                            bus.publish("confirm", id=cid, pending=False, allow=box["allow"] is True,
                                        by=box["by"] or "ecran", desc=desc)
                        return

            threading.Thread(target=announce_answer, name="confirm-announce", daemon=True).start()
            try:
                if self.keyboard:
                    self._confirm_by_keyboard(question, cmd, box, answered)
                else:
                    # 14/10 : la question est À L'ÉCRAN tout de suite (HUD, bulle), mais
                    # elle n'est dite, et ne prend le micro, qu'une fois que l'utilisateur a
                    # fini de parler. Avant, elle coupait son écoute en pleine phrase.
                    self._wait_until_silent(answered, deadline)
                    # Absent (plan P1) : pas de question dite dans une pièce vide, elle part sur WhatsApp.
                    if box["allow"] is None and not self._voice_muted() and not self._away():
                        self._confirm_evt.set()  # la question prend le micro le temps des essais à l'oral
                        try:
                            self._confirm_by_voice(question, box, answered)
                        finally:
                            self._confirm_evt.clear()
                    if box["allow"] is None:
                        self.status.set(state.ASKING, text=question, detail=f"{desc} · réponds à l'écran",
                                        unless=(state.LISTENING, state.SPEAKING))
                if box["allow"] is None:
                    answered.wait(max(0.0, deadline - time.time()))
            finally:
                if box["allow"] is not None and box["by"] != "voix":
                    self._silence_question(box["tag"])  # au cas où le fil d'annonce n'est pas passé
                stop_watch.set()
                self._pending_confirm = None
                self._confirm_evt.clear()
            if box["allow"] is None:
                box.update(allow=False, by="délai")
                self.say("Pas de réponse à ma question, j'annule.", raw=True, live=True)
            allow = box["allow"] is True
            if box["by"] == "abandon":
                log.info("confirmation %s retirée : le hook a abandonné", cid)
            if box["by"] == "total":
                self._total_log(tool, cmd or desc, desc)
            elif box["by"] == "levée":
                self._lift_log(tool, cmd or desc, desc)
            if allow and box.get("always") and grantable:
                # « Oui pour 30 min » : chaque étiquette de la commande passera sans question.
                for k in keys:
                    guard_mod.grant(k)
                bus.publish("notice", level="info", text=f"Autorisé pour 30 minutes : {desc}.")
                self._publish_grants()
            if not box.get("announced"):
                bus.publish("confirm", id=cid, pending=False, allow=allow, by=box["by"] or "délai", desc=desc)
            if box["by"] not in ("voix", "délai", "abandon", "whatsapp") and self.voice_out:
                self.say("D'accord." if allow else "J'annule.", raw=True, live=True)
            # L'écoute a pu reprendre dès `_pending_confirm = None` : ne pas l'écraser.
            self.status.set(state.WORKING if allow else (previous if previous != state.ASKING else state.THINKING),
                            unless=(state.LISTENING,))
            return allow, str(box["by"] or "délai"), bool(allow and box.get("always") and grantable)

    def _wait_until_silent(self, answered: threading.Event, deadline: float = float("inf"),
                           poll_s: float = 0.1) -> None:
        """Attend que l'utilisateur ne parle plus (voix entendue il y a moins de
        SPEECH_GRACE_S, ou phrase en transcription), au plus jusqu'à `deadline`.
        Pendant ce temps la question reste à l'écran, un clic y répond."""
        logged = False
        while self._running and not answered.is_set() and time.time() < deadline:
            why = floor.speaking()
            if not why:
                return
            if not logged:
                log.info("confirmation : attend que l'utilisateur ait fini (%s)", why)
                logged = True
            answered.wait(poll_s)

    def _answer_confirmation(self, cid: str, allow: bool, always: bool = False, by: str = "écran",
                             guard_phrase: bool = False) -> dict:
        """Réponse cliquée (HUD, bulle) ou, appel INTERNE seulement, écrite sur
        WhatsApp (`by="whatsapp"`, plan P1) : jamais « pour 30 min ». Action sur le
        garde-fou (17/10, accord de l'utilisateur) : un « oui » WhatsApp ne vaut que si
        `whatsapp.py` a lu « oui garde-fou » (`guard_phrase`) ; « non » vaut
        toujours. La commande `confirm` reste refusée à Claude."""
        pending = self._pending_confirm
        if not pending or pending["id"] != cid:
            return {"ok": False, "error": "aucune confirmation en attente avec cet identifiant"}
        if by == "whatsapp":
            always = False
            if pending.get("guard") and allow and not guard_phrase:
                return {"ok": False, "error": "action sur le garde-fou : écris « oui garde-fou », "
                                              "ou réponds sur le PC (HUD ou bulle)"}
        if pending["box"]["allow"] is None:
            pending["box"].update(allow=allow, by=by, always=allow and always)
            pending["event"].set()
        return {"ok": True}

    def _confirm_by_voice(self, question: str, box: dict, answered: threading.Event) -> None:
        if self._voice_muted():
            return  # silencieux ou dictée : la question est à l'écran (HUD, bulle) seulement
        # Laisser finir ce qu'Astrel disait, puis poser la question.
        tag = str(box.get("tag") or "")
        self.speaker.wait_idle(20.0)
        if answered.is_set():
            return  # répondu à l'écran pendant l'attente : rien à dire
        if not self.voice_out:
            print(f"\n⚠️  {question}\n", flush=True)
        else:
            self._chime("confirm")
            self.say_and_wait(question, tag=tag, stop=answered)
        if self._off or self.speaker.paused or self.recorder is None:
            return  # micro coupé, ou voix en pause (question jamais entendue) : réponse à l'écran seulement
        for attempt in range(2):
            if answered.is_set() or self.speaker.paused or self._off:
                return  # voix en pause ou micro coupé pendant la question : réponse à l'écran seulement
            if attempt:
                self.say_and_wait("C'est oui ou non ?", tag=tag, stop=answered)
            if answered.is_set():
                return  # répondu à l'écran pendant la question : l'écoute orale n'ouvre pas
            gen = self._mute_gen
            heard = self.listen_once(timeout=9.0, abort=_Abort(lambda: answered.is_set() or self.speaker.paused
                                                               or self._off))
            if self.speaker.paused or self._muted_since(gen):
                return  # micro coupé pendant l'écoute de la réponse : pas de « oui » entendu
            # 02/11 : oui anglais seulement en conversation anglaise (refus des deux langues toujours)
            decision = guard.decision_from_speech(heard, english=langue.anglais()) if heard else None
            if decision is not None and langue.question(heard):
                decision = None  # 02/11 : une question n'est pas une réponse (« okay, what's that file? ») : reposée
            if decision is not None and box["allow"] is None:
                box.update(allow=decision, by="voix", always=decision and _wants_30(heard))
                answered.set()
                self.say("D'accord." if decision else "J'annule.", raw=True, live=True)
                return
        if box["allow"] is None and not answered.is_set():
            # Plus d'annulation d'office : la question reste à l'écran.
            self.say("Je laisse la question à l'écran.", raw=True, live=True)

    def _answer_by_speech(self, text: str) -> bool:
        """Un « oui » ou un « non » dit pendant qu'une question attend à l'écran
        (après les essais à l'oral). Phrase courte seulement : une vraie demande
        à Claude ne doit pas être prise pour une réponse."""
        pending = self._pending_confirm
        # 02/11 (contre-relecture) : 4 mots au plus (8 avant : « okay, what time is it » validait) et jamais une
        # question ; sinon la phrase suit son cours normal (Claude).
        if pending is None or len(text.split()) > 4 or langue.question(text):
            return False
        decision = guard.decision_from_speech(text, english=langue.anglais())
        if decision is None or pending["box"]["allow"] is not None:
            return False
        pending["box"].update(allow=decision, by="oral", always=decision and _wants_30(text))
        pending["event"].set()
        log.info("confirmation %s répondue à voix haute : %r", pending.get("id"), text)
        return True

    def _confirm_by_keyboard(self, question: str, cmd: str, box: dict, answered: threading.Event) -> None:
        print(f"\n⚠️  {question}\n    → {cmd}", flush=True)
        try:
            decision = guard.decision_from_speech(input("   oui / non : "))
        except EOFError:
            decision = False
        if box["allow"] is None:
            box.update(allow=decision is True, by="clavier")
            answered.set()
