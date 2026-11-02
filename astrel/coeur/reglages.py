"""Page « Réglages » du HUD : liaison des réglages, capteurs, WhatsApp, micro et voix,
commandes `settings_*`.

Mixin de `Astrel` (core.py), découpé du cœur le 29/10/2026 : méthodes déplacées telles quelles.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading

from .. import audio
from .. import config
from .. import fournisseurs
from .. import limites
from .. import mcp_config
from .. import micprofile
from .. import totalgate
from ..events import bus
from ..plans import plans
from ..sensors import sensors
from ..settings import SettingError, reglages
from ..voice import available_voices
from ..core import _mic_probe, _settings_on_sensor, _speaks, log  # aides du cœur (le paquet charge core d'abord)


class ReglagesCoeur:
    """Page « Réglages » du HUD : liaison des réglages, capteurs, WhatsApp, micro et voix,"""

    def _bind_settings(self) -> None:
        """Branche sur la page « Réglages » ce qui vit dans le cœur : micro, voix, seuil de
        « Hey Jarvis » du micro en cours, boutons d'essai (plan P4, lot 5)."""
        reglages.bind("audio.mic", get=lambda: self.mic.source or "", put=self._set_mic, options=self._mic_options)
        reglages.bind("audio.voice", get=lambda: self.speaker.voice, put=self._set_voice, options=self._voice_options)
        reglages.bind("audio.wake", get=lambda: round(float(self.mic.profile.wake), 2),
                      put=lambda v: self._set_mic_profile(f"wake={v}"))
        # 20/10 (demande de l'utilisateur) : fenêtre de suite, même état que le menu du badge de mode.
        reglages.bind("audio.followup", get=lambda: self._followup_setting > 0, put=self._followup_switch)
        reglages.bind("audio.followup_s", get=lambda: max(1, int(round(self._followup_last))),
                      put=self._followup_duration)
        # 02/11 (demande de l'utilisateur) : assistant de calibrage du micro, ouvert par le HUD lui-même.
        reglages.bind("audio.calibrage", view=self._calibrage_view,
                      run=lambda _a, _c: {"ok": False, "error": "le calibrage se lance depuis le HUD (bouton « Calibrer ce micro »)"})
        reglages.bind("audio.test", view=lambda: {"value": {"lines": []}, "actions": [
            {"id": "voice", "label": "Tester la voix"}, {"id": "mic", "label": "Tester le micro (3 s)"}]},
            run=self._audio_test)
        reglages.bind("whatsapp", view=self._whatsapp_view, run=self._whatsapp_action)
        reglages.bind("garde_fou_levee", view=self._lift_view, run=self._lift_action)  # 18/10 (plan P7)
        # 24/10 (P29 lot B) : cases de confirmation (résumé et boutons, puis une grille par groupe).
        reglages.bind("confirmations", view=self._cases_view, run=self._cases_action)
        for gid, _title in limites.GROUPES_CASES:
            reglages.bind(f"confirmations.{gid}", get=lambda gid=gid: self._cases_get(gid),
                          put=lambda value, gid=gid: self._cases_put(gid, value))
        reglages.bind("local.mode", view=self._local_view, run=self._local_action)  # 19/10 (plan P8)
        reglages.bind("capteurs", view=self._capteurs_view, run=self._capteurs_action)  # 23/10 : panneau allégé
        # Un capteur changé ailleurs (carte WATCH, voix, Claude) : la page Réglages suit.
        if _settings_on_sensor not in sensors._listeners:  # une fois (les tests créent plusieurs cœurs)
            sensors.on_change(_settings_on_sensor)

    def _capteurs_view(self) -> dict:
        from ..capacites import _etat_capteur

        lines, actions = [], []
        for item in sensors.snapshot():
            raw = str(item.get("name") or "")
            nom = self._CAPTEURS_NOMS.get(raw, raw)
            etat = _etat_capteur(item)
            lines.append({"label": nom, "text": etat,
                          "level": "ok" if etat == "actif" else "warn" if etat == "allumé, indisponible" else "info"})
            on = bool(item.get("on"))
            actions.append({"id": f"{'off' if on else 'on'}:{raw}", "label": f"{'Éteindre' if on else 'Allumer'} {nom}"})
        return {"value": {"lines": lines}, "actions": actions}

    def _capteurs_action(self, action: str, cmd: dict) -> dict:
        verb, _, raw = action.partition(":")
        if verb not in ("on", "off") or raw not in self._CAPTEURS_NOMS and raw not in {
                str(i.get("name")) for i in sensors.snapshot()}:
            return {"ok": False, "error": f"action inconnue : {action}"}
        res = sensors.set(raw, verb == "on")
        nom = self._CAPTEURS_NOMS.get(raw, raw)
        if res.get("ok", True):
            return {"ok": True, "text": f"{nom} : {'allumé' if verb == 'on' else 'éteint'}."}
        return res

    def _settings_follow(self, ev: dict) -> None:
        if ev.get("type") == "whatsapp":
            threading.Thread(target=reglages.publish, name="settings-whatsapp", daemon=True).start()

    def _whatsapp_view(self) -> dict:
        """Groupe « WhatsApp » (plan P4, lot 9 ; plan P5) : préparer le pont, relier par QR ou
        par code, confirmer le numéro, activer, Absent, délier. Seuls les boutons de l'état
        en cours sont listés (`reglages.action` refuse les autres)."""
        d = self.whatsapp.describe()
        conn = str(d.get("state") or "off")
        lines = [{"label": "Liaison", "text": "reliée à ton téléphone" if d.get("linked") else "non reliée",
                  "level": "ok" if d.get("linked") else "warn"}]
        value: dict = {"lines": lines}
        actions: list[dict] = []
        stage = str(d.get("linking") or "")
        if stage:
            lines.append({"label": "Appairage", "level": "info", "text": self._WA_STAGES.get(stage, stage)})
            value["link_box"] = True  # le HUD place ici le QR ou le code (évènement `whatsapp_link`)
            if stage == "linked":
                actions.append({"id": "link_confirm", "label": "Oui, c'est mon numéro"})
                actions.append({"id": "link_cancel", "label": "Non, annuler", "danger": True})
            elif stage != "confirming":
                actions.append({"id": "link_cancel", "label": "Annuler l'appairage"})
            return {"value": value, "actions": actions}
        if not d.get("linked"):
            prep = getattr(self, "_wa_prepare", None) or {}
            if prep.get("state") == "running":
                lines.append({"label": "Préparation", "level": "info",
                              "text": "installation des dépendances du pont (npm ci), une à deux minutes…"})
            elif prep.get("state") == "error":
                lines.append({"label": "Préparation", "level": "error", "text": str(prep.get("text") or "échec")})
            if not d.get("ready", True):
                if mcp_config._node() is None:
                    lines.append({"label": "Pont", "level": "error",
                                  "text": "Node.js introuvable : installe Node.js 20 ou plus, puis relance Astrel"})
                    return {"value": value, "actions": actions}
                lines.append({"label": "Pont", "level": "warn",
                              "text": "à préparer une fois : télécharge les dépendances du pont WhatsApp"})
                if prep.get("state") != "running":
                    actions.append({"id": "prepare", "label": "Préparer WhatsApp"})
                return {"value": value, "actions": actions}
            lines.append({"label": "Pour relier", "level": "info",
                          "text": "relie ton compte avec le QR (scanné depuis ton téléphone) ou, si la caméra "
                                  "ne le lit pas, avec un code à 8 caractères"})
            actions.append({"id": "link", "label": "Relier avec un QR"})
            actions.append({"id": "link_phone", "label": "Relier avec un code",
                            "input": {"label": "Ton numéro WhatsApp, indicatif compris, chiffres seulement "
                                               "(ex. 33612345678)", "type": "tel"}})
            return {"value": value, "actions": actions}
        lost = conn in ("logged_out", "need_link")
        lines.append({"label": "Pont", "level": "ok" if conn == "open" else ("info" if conn == "off" else "warn"),
                      "text": {"open": "connecté", "off": "coupé", "starting": "connexion…",
                               "closed": "coupure, nouvel essai bientôt",
                               "absent": "introuvable (node ou dépendances)"}.get(
                          conn, "session perdue : délie puis relie" if lost else conn)})
        lines.append({"label": "Mode", "level": "info",
                      "text": "absent : réponses, fins de tâche et questions partent sur WhatsApp" if d.get("away")
                      else "présent : Astrel te répond ici"})
        if lost:
            pass  # rien à activer : la session est perdue, il faut délier puis relier
        elif d.get("enabled"):
            actions.append({"id": "present", "label": "Je suis de retour"} if d.get("away")
                           else {"id": "absent", "label": "Je m'absente"})
            actions.append({"id": "off", "label": "Couper WhatsApp", "danger": True,
                            "confirm": "Astrel ne lira ni n'écrira plus sur WhatsApp jusqu'à ce que tu le réactives."})
        else:
            actions.append({"id": "on", "label": "Activer WhatsApp"})
        actions.append({"id": "unlink", "label": "Délier WhatsApp", "danger": True, "word": "délier",
                        "confirm": "L'appareil « Astrel » sera retiré de ton téléphone et ses identifiants effacés "
                                   "de cette machine. Pour reprendre, il faudra relier de nouveau."})
        return {"value": value, "actions": actions}

    def _whatsapp_action(self, action: str, cmd: dict) -> dict:
        wa = self.whatsapp
        if action == "prepare":
            return self._whatsapp_prepare()
        if action in ("link", "link_phone"):
            phone = re.sub(r"\D", "", str(cmd.get("value") or "")) if action == "link_phone" else ""
            if action == "link_phone" and not phone:
                return {"ok": False, "error": "il faut ton numéro, indicatif compris (ex. 33612345678)"}
            res = wa.link(phone)
            if res.get("ok") is False:
                return res
            return {"ok": True, "level": "info",
                    "text": "Le code arrive dans quelques secondes." if phone else "Le QR arrive dans quelques secondes."}
        if action == "link_confirm":
            res = wa.link_confirm(cmd.get("ok", True) is not False)
            return res if res.get("ok") is False else {"ok": True, "level": "ok", "text": "Numéro confirmé."}
        if action == "link_cancel":
            res = wa.link_cancel()
            return res if res.get("ok") is False else {"ok": True, "level": "info", "text": "Appairage annulé."}
        if action == "unlink":
            res = wa.unlink()
            if res.get("ok") is False:
                return res
            if res.get("phone"):
                return {"ok": True, "level": "ok", "text": "WhatsApp délié : l'appareil « Astrel » est retiré de "
                                                           "ton téléphone et effacé d'ici."}
            return {"ok": True, "level": "warn",
                    "text": "Effacé de cette machine, mais le téléphone n'a pas pu être prévenu : dans WhatsApp, "
                            "Réglages › Appareils connectés, retire « Astrel »."}
        if action not in ("on", "off", "absent", "present"):
            return {"ok": False, "error": f"action inconnue : {action}"}
        res = wa.command(action)
        if res.get("ok") is False:
            return res
        return {"ok": True, "level": "ok", "text": {"on": "WhatsApp activé.", "off": "WhatsApp coupé.",
                                                    "absent": "Absent : la suite part sur WhatsApp.",
                                                    "present": "De retour : Astrel te répond ici."}[action]}

    def _whatsapp_prepare(self) -> dict:
        """« Préparer WhatsApp » : `npm ci` dans whatsapp/ (versions FIXÉES du package-lock),
        dans un fil ; la page suit (« en cours », puis le bouton « Relier »)."""
        if (getattr(self, "_wa_prepare", None) or {}).get("state") == "running":
            return {"ok": False, "error": "préparation déjà en cours"}
        import shutil
        from pathlib import Path

        node = mcp_config._node()
        npm = shutil.which("npm") or (str(Path(node).parent / "npm") if node else "")
        if not node or not npm or not Path(npm).exists():
            return {"ok": False, "error": "Node.js et npm introuvables : installe Node.js 20 ou plus"}
        self._wa_prepare = {"state": "running"}

        def run() -> None:
            try:
                proc = subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=str(config.WHATSAPP_BRIDGE_DIR),
                                      capture_output=True, text=True, timeout=600)
                ok = proc.returncode == 0
                tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] if not ok else []
                text = tail[0][:200] if tail else ("" if ok else f"npm ci a échoué (code {proc.returncode})")
            except (OSError, subprocess.TimeoutExpired) as exc:
                ok, text = False, f"npm ci impossible : {exc}"[:200]
            self._wa_prepare = {"state": "ok" if ok else "error", "text": text}
            log.info("préparation du pont WhatsApp : %s", "faite" if ok else text)
            self.whatsapp.publish()  # la page « Réglages » suit (`_settings_follow`)

        threading.Thread(target=run, name="whatsapp-prepare", daemon=True).start()
        return {"ok": True, "level": "info", "text": "Préparation lancée (une à deux minutes, réseau nécessaire)."}

    def _mic_options(self) -> list[tuple[str, str]]:
        out = [("", "Micro du système")]
        for m in audio.list_mics():
            out.append((m["name"], m["label"] + (" (micro du système)" if m.get("default") else "")))
        if self.mic.source and self.mic.source not in {v for v, _ in out}:
            out.append((self.mic.source, f"{self.mic.source} (débranché ?)"))
        return out

    def _voice_options(self) -> list[tuple[str, str]]:
        """Voix qui parlent la langue choisie (« Langue parlée ») ; la voix en cours reste dans la liste.
        02/11 : conversation en anglais, les voix anglaises (`langue.stt`)."""
        from .. import langue

        lang = langue.stt()
        out = [(v.spec, v.label) for v in available_voices() if _speaks(v.spec, lang)]
        current = self.speaker.voice
        if current and current not in {v for v, _ in out}:
            out.insert(0, (current, current))
        return out

    def _audio_test(self, action: str, cmd: dict) -> dict:
        if action == "voice":
            if not self.voice_out:
                return {"ok": False, "error": "voix coupée au démarrage (option --no-voice-out)"}
            self.say("Bonjour, je suis Astrel. Si tu m'entends, la sortie du son est bonne.", raw=True, live=True,
                     force=True)
            return {"ok": True, "level": "info", "text": "Phrase d'essai envoyée : tu dois l'entendre."}
        if action == "mic":
            if self._off:
                return {"ok": False, "error": "micro coupé : rallume-le d'abord (bouton du micro)"}
            return _mic_probe(self.mic.source)
        return {"ok": False, "error": f"essai inconnu : {action}"}

    def _set_mic_profile(self, text: str) -> dict:
        """Règle le micro en cours (`gain=4 vad=0.3 wake=0.45`), retenu dans son
        profil et appliqué tout de suite. Sans argument : relit le fichier et donne le profil actif."""
        values: dict[str, float] = {}
        channels: list[int] | None = None
        for part in text.replace(",", ".").split():
            key, _, raw = part.partition("=")
            try:
                if key == "channels":  # channels=0+2 ; « channels=tous » rétablit le mélange par défaut
                    channels = [] if raw in ("", "tous", "aucun") else [int(c) for c in raw.split("+")]
                else:
                    values[key] = float(raw)
            except ValueError:
                return {"ok": False, "error": f"réglage illisible : {part} (attendu gain=… vad=… wake=… channels=0+2)"}
        if unknown := set(values) - {"gain", "vad", "wake"}:
            return {"ok": False, "error": f"réglage inconnu : {', '.join(sorted(unknown))}"}
        bounds = {"gain": (0.2, 20.0), "vad": (0.05, 0.95), "wake": (0.05, 0.95)}
        for key, val in values.items():
            if not bounds[key][0] <= val <= bounds[key][1]:
                return {"ok": False, "error": f"{key} doit être entre {bounds[key][0]:g} et {bounds[key][1]:g}"}
        name, label = self.mic.identity()
        if values or channels is not None:
            micprofile.save(name, label, channels=channels, **values)
        self.mic.load_profile()  # sans argument : relit le fichier (après `--calibrate`)
        p = self.mic.profile
        return {"ok": True, "mic": label or name, "profil": p.key or "par défaut",
                "gain": p.gain, "vad": p.vad, "wake": p.wake, "channels": list(p.channels)}

    def _settings_command(self, name: str, cmd: dict) -> dict:
        """`settings_get` / `settings_set {key, value}` / `settings_apply` / `settings_action
        {key, action, confirm?}` (contrat docs/CONTRAT-REGLAGES-2026-10-15.md). Refusées à
        Claude : ces réglages (connecteurs, compte, effacements, système…) ne sont qu'à l'utilisateur."""
        if cmd.get("_from_claude") is True:
            # 21/10 (fiche P17-5) : `settings_get` n'est qu'une LECTURE, et tout HUD la fait à chaque
            # connexion (`askTheme`, apparence.js) : un Chrome lancé par un agent déclenchait la
            # notice vers tous les HUD à chaque `hello`. Refusée quand même, sans notice ; la notice
            # reste pour ce qui changerait quelque chose (set, apply, action).
            if name == "settings_get":
                log.info("commande %r refusée sans notice : elle vient de Claude", name)
            else:
                log.warning("commande %r refusée : elle vient de Claude", name)
                bus.publish("notice", level="warning",
                            text=f"Claude a tenté « {name} » : refusé, les réglages sont à toi seul.")
            return {"ok": False, "error": "réglages réservés à l'utilisateur (HUD ou terminal), refusés à Claude"}
        if name == "settings_apply":
            return self._settings_apply()
        if name == "settings_action":
            # 17/10 (plan P4) : bouton d'une ligne info/action {key, action, confirm?} (jamais `id`).
            # Terminal : --send "settings_action compte refresh".
            key, action = str(cmd.get("key") or "").strip(), str(cmd.get("action") or "").strip()
            if not key and cmd.get("text"):
                key, _, action = str(cmd["text"]).strip().partition(" ")
                action = action.strip()
            res = reglages.action(key, action, cmd)
            return {**res, **reglages.publish()}
        if name == "settings_set":
            key, value = str(cmd.get("key") or "").strip(), cmd.get("value")
            if not key and cmd.get("text"):
                # Terminal : --send "settings_set brain_effort medium" (valeur en JSON ou en texte).
                key, _, raw = str(cmd["text"]).strip().partition(" ")
                try:
                    value = json.loads(raw)
                except ValueError:
                    value = raw.strip()
            try:
                if key.startswith("confirmations") and not totalgate.may_change_confirmations(cmd):
                    # 24/10 (P29 lot B) : cases de confirmation, l'utilisateur depuis le HUD seulement.
                    raise SettingError(key, "les cases de confirmation ne se changent que depuis les Réglages du HUD")
                if key == "fournisseur":  # plan P11 : clé, passerelle, Total, levée, mode prudent
                    why = fournisseurs.refus_choix(str(value or ""), self._total, self._lift)
                    if why:
                        raise SettingError(key, why)
                if key == "brain_effort" and self._effort_reason() == "ext":
                    # Plan P28 : comme `_set_model`, l'effort n'existe que pour Claude.
                    raise SettingError(key, "l'effort de réflexion ne s'applique qu'à Claude ; le cerveau "
                                            "tourne sur un autre fournisseur (Réglages › Fournisseur)")
                before = reglages.value(key) if key in ("brain_effort", "conversation_language") else None
                reglages.set(key, value)
            except SettingError as exc:
                err = {"key": exc.key, "message": exc.message}
                log.info("réglage %s refusé : %s", exc.key, exc.message)
                bus.publish("notice", level="warning", text=f"Réglage refusé ({exc.key}) : {exc.message}",
                            setting_error=err)
                return {"ok": False, "error": exc.message, "setting_error": err, **reglages.publish()}
            if key == "brain_effort" and reglages.value(key) != before:
                self._apply_effort()
            if key == "plan_mode":
                plans.publish()  # le sélecteur Manuel/Auto de la page « Plans » suit
            if key == "conversation_language" and reglages.value(key) != before:
                self._langue_changee()  # 02/11 : voix et écoute tout de suite, session au message suivant
            if key == "history_keep_days":  # 17/10 (plan P4) : l'oubli s'applique tout de suite
                from .. import history

                threading.Thread(target=history.purge, args=(reglages.value(key),), name="history-purge",
                                 daemon=True).start()
        return {"ok": True, **reglages.publish()}

    def _settings_apply(self) -> dict:
        """« Appliquer maintenant » : réécrit `mcp.json` et relance le cerveau en
        reprenant la conversation (`--resume`). Les tâches de fond du CLI meurent
        avec son process : le HUD a demandé confirmation et l'a annoncé."""
        if not reglages.restart_needed():
            return {"ok": True, "applied": False, **reglages.publish()}
        if not self._settings_apply_lock.acquire(blocking=False):
            return {"ok": True, "applied": False, "busy": True}  # une application est déjà en cours
        with self._tasks_lock:
            count = len(self._tasks)
        threading.Thread(target=self._apply_settings_guarded, args=(count,), name="settings-apply", daemon=True).start()
        return {"ok": True, "applied": True, "tasks": count}

    def _apply_settings_guarded(self, count: int = 0) -> None:
        try:
            self._apply_settings(count)
        finally:
            self._settings_apply_lock.release()

    def _apply_settings(self, count: int = 0) -> None:
        log.info("réglages appliqués : réécriture des serveurs MCP et relance du cerveau (%d tâche(s) coupée(s))", count)
        try:
            with self._turn_lock:
                mcp_config.write()
                self.brain.restart()  # reprend la session : la conversation reste
                self._end_turn(followup=False)
                with self._tasks_lock:
                    self._tasks.clear()
                    done_list = self._done_list()  # plan P18 : les tâches finies restent cliquables
                bus.publish("tasks", items=[], done=done_list)
        except Exception as exc:
            log.exception("réglages non appliqués")
            bus.publish("notice", level="error", text=f"Réglages non appliqués : {exc}")
            reglages.publish()
            return
        self._publish_context()
        reglages.publish()
        cut = f", {count} tâche(s) de fond coupée(s)" if count else ""
        bus.publish("notice", level="info", text=f"Réglages appliqués : cerveau redémarré{cut}, conversation reprise.")
