"""Cerveau : modèle et effort, consommation, fournisseur, offre d'un modèle plus léger près
de la limite, mode léger (modèle local).

Mixin de `Astrel` (core.py), découpé du cœur le 29/10/2026 : méthodes déplacées telles quelles.
"""
from __future__ import annotations

import contextlib
import threading
import time
from typing import Any

from .. import agents as agents_mod
from .. import config
from .. import consommation
from .. import fournisseurs
from .. import hors_ligne  # 04/11 : mode « rien ne sort »
from .. import modeles_locaux
from .. import quota_releve
from .. import reprise
from ..brain import model_label
from ..channels import channels
from ..choices import choices
from ..events import bus
from ..mission import mission
from ..settings import ITEMS as SETTINGS_ITEMS, SettingError, reglages
from ..core import effort_label, log  # aides du cœur (le paquet charge core d'abord)


class CerveauCoeur:
    """Cerveau : modèle et effort, consommation, fournisseur, offre d'un modèle plus léger près"""

    def _set_model(self, model: str) -> dict:
        """Change le modèle principal (HUD ou `--send "model <alias>"`, jamais
        Claude lui-même) et retient le choix. À chaud d'abord ; si le CLI refuse,
        repli sur une relance du cerveau avec --resume, mais JAMAIS pendant
        qu'une tâche tourne : la relance la tuerait."""
        if model not in config.CLAUDE_MODELS:
            return {"ok": False, "error": f"modèle inconnu : {model} (au choix : {', '.join(config.CLAUDE_MODELS)})"}
        if model == self.brain.model:
            self._publish_model()
            return {"ok": True, "model": model}
        eff = getattr(self.brain, "fournisseur", None)
        if eff is not None and eff.genre in ("passerelle", "cli") and not self._local_active():
            # Plan P11 : derrière la passerelle, tous les alias visent le modèle choisi (P27 : CLI aussi).
            self._publish_model()
            return {"ok": False, "error": f"cerveau sur {eff.libelle()} : le modèle se choisit dans "
                                          "Réglages › Fournisseur"}
        if self._local_active():
            # Mode léger (plan P8) : le process est Ollama, `set_model` y viserait un modèle
            # Claude. Le choix est retenu et pris au retour sur Claude.
            self.brain.model = model
            try:
                config.MODEL_FILE.write_text(model + "\n", encoding="utf-8")
            except OSError:
                log.warning("choix du modèle non retenu : %s", config.MODEL_FILE)
            bus.publish("notice", level="info", text=f"{model_label(model)} sera pris au retour sur Claude "
                                                     "(mode léger en cours).")
            return {"ok": True, "model": model, "local": True}
        was_haiku = "haiku" in str(self.brain.model or "").lower()
        try:
            self.brain.set_model(model)
        except Exception as exc:
            log.warning("changement de modèle à chaud impossible (%s)", exc)
            if self._turn_active or self._tasks:
                return {"ok": False, "error": f"changement à chaud refusé ({exc}), et une tâche tourne : "
                                              "réessaie quand elle sera finie"}
            with self._turn_lock:
                self.brain.model = model
                self._cerveau_relancer()  # reprend la session (--resume) avec --model et --effort
        else:
            if was_haiku:
                self._effort_after_haiku()
        try:
            config.MODEL_FILE.write_text(model + "\n", encoding="utf-8")
        except OSError:
            log.warning("choix du modèle non retenu : %s", config.MODEL_FILE)
        self._publish_model()
        bus.publish("notice", level="info", text=f"Modèle principal : {model_label(model)}. "
                                                 "Le prochain échange relira la conversation sans cache.")
        return {"ok": True, "model": model}

    def _publish_model(self, resolved: str = "") -> None:
        """Badge du HUD : l'alias choisi, et l'identifiant exact dès que le CLI
        l'annonce (son `init`, redonné à chaque tour)."""
        model = self.brain.model
        if hors_ligne.actif():  # 04/11 : le badge et la pastille « Hors ligne » du HUD
            tag = self._moteur_local.tag
            bus.publish("model", active=model, label=f"Hors ligne · {tag}", id=tag, offline=True)
            return
        if self._local_active():  # plan P8 : le badge dit le mode léger, l'alias Claude reste choisi
            tag = modeles_locaux.local.active_tag or self.brain.local
            bus.publish("model", active=model, label=f"Mode léger · {tag}", id=resolved or tag, local=True)
            return
        eff = getattr(getattr(self, "brain", None), "fournisseur", None)
        if eff is not None and eff.genre in ("passerelle", "cli"):
            # Plan P11, lot 3 : badge marqué (`ext`) tant qu'un autre fournisseur que Claude tourne.
            label = eff.libelle() + ("" if eff.outils else " · sans outil")
            bus.publish("model", active=model, label=label, id=resolved or eff.modele, ext=True, fournisseur=eff.id)
        elif eff is not None and eff.genre == "cle_claude":
            bus.publish("model", active=model, label=f"{model_label(model)} · clé API", id=resolved, cle=True)
        else:
            bus.publish("model", active=model, label=model_label(model), id=resolved)
        self._announce_fournisseur()
        self._publish_effort()

    def _effort_reason(self) -> str:
        """Pourquoi l'effort du cerveau ne s'applique pas maintenant : « local » (mode léger),
        « ext » (autre fournisseur que Claude), « haiku » (modèle sans effort) ; « » sinon."""
        brain = getattr(self, "brain", None)
        if brain is None:
            return ""
        if self._local_active():
            return "local"
        eff = getattr(brain, "fournisseur", None)
        if eff is not None and eff.genre in ("passerelle", "cli"):
            return "ext"
        if "haiku" in str(getattr(brain, "model", "") or "").lower():
            return "haiku"
        return ""

    def _publish_effort(self) -> None:
        """Évènement `effort` (collant) : le niveau choisi pour le cerveau, les niveaux possibles,
        `reason` s'il ne s'applique pas, `locked` si ASTREL_CLAUDE_EFFORT l'impose. Le HUD en fait
        le sélecteur à côté du modèle."""
        try:
            active = str(reglages.value("brain_effort") or "")
            lock = SETTINGS_ITEMS["brain_effort"].lock_reason()
        except Exception:  # noqa: BLE001 — un réglage illisible ne casse pas le badge du modèle
            log.exception("effort non publié")
            return
        bus.publish("effort", active=active, levels=list(agents_mod.EFFORT_LEVELS),
                    reason=self._effort_reason(), locked=bool(lock), lock_reason=lock)

    def _effort_after_haiku(self) -> None:
        """Sortie de Haiku À CHAUD (relecture P28) : un effort choisi pendant Haiku n'a pas été
        envoyé, et le CLI garde le niveau d'avant Haiku alors que le badge dit le nouveau. Renvoyé
        ici, sauf si rien n'a changé (`Brain.effort_sent`), si ASTREL_CLAUDE_EFFORT l'impose (le
        process l'a reçu au démarrage) ou si l'effort ne s'applique pas (mode léger, autre fournisseur)."""
        try:
            if self._effort_reason() or SETTINGS_ITEMS["brain_effort"].lock_reason():
                return
            effort = str(reglages.value("brain_effort") or "")
        except Exception:  # noqa: BLE001 — un réglage illisible ne casse pas le changement de modèle
            log.exception("effort non renvoyé après Haiku")
            return
        if effort == getattr(self.brain, "effort_sent", None):
            return
        self._apply_effort(notify=False)  # la notice du modèle dit déjà « relira sans cache »

    def _apply_effort(self, notify: bool = True) -> None:
        """`brain_effort` vient de changer (page Réglages ou sélecteur du HUD) : appliqué À CHAUD
        (`Brain.set_effort`). Si le CLI refuse (ou « Réglage du CLI » après un autre niveau : jamais
        de `null`, voir `Brain.set_effort`) : relance avec --resume, JAMAIS pendant une tâche
        (comme `_set_model`). Mode léger, Haiku : retenu, pris plus tard. `notify` faux : pas de
        notice de succès (appel par `_effort_after_haiku`)."""
        effort = str(reglages.value("brain_effort") or "")
        label = effort_label(effort)
        reason = self._effort_reason()
        if reason == "local":
            bus.publish("notice", level="info", text=f"Effort {label} : pris au retour sur Claude (mode léger en cours).")
        elif reason == "haiku":
            bus.publish("notice", level="info", text=f"Effort {label} : Haiku n'a pas de réglage d'effort, "
                                                     "il sera pris avec Sonnet ou Opus.")
        else:
            try:
                self.brain.set_effort(effort)
            except Exception as exc:
                log.warning("changement d'effort à chaud impossible (%s)", exc)
                if self._turn_active or self._tasks:
                    bus.publish("notice", level="warning",
                                text=f"Effort {label} non appliqué à chaud ({exc}) : pris au prochain démarrage du cerveau.")
                    self._publish_effort()
                    return
                with self._turn_lock:
                    self._cerveau_relancer()  # reprend la session (--resume) avec --effort
            if notify:
                bus.publish("notice", level="info", text=f"Effort de réflexion : {label}. "
                                                         "Le prochain échange peut relire la conversation sans cache.")
        self._publish_effort()

    def _set_agent_effort(self, agent: str, effort: str | None) -> dict:
        """Commande `agent_effort` (plan P28) : l'effort des PROCHAINS agents du métier. Un agent
        en cours garde le sien. Rechargé à chaud dans le CLI (`Brain.reload_agents`), sinon pris
        au prochain démarrage du cerveau ; `applique` le dit."""
        try:
            now = agents_mod.set_agent_effort(agent, effort)
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        applied = False
        if self._effort_reason() not in ("local", "ext"):
            try:
                applied = bool(self.brain.reload_agents())
            except Exception:  # noqa: BLE001 — le choix est écrit, pris au prochain démarrage
                log.exception("agents non rechargés")
        label = effort_label(now)
        if applied:
            bus.publish("notice", level="info", text=f"Effort de l'agent {agent} : {label}, pour ses prochains lancements.")
        else:
            bus.publish("notice", level="info", text=f"Effort de l'agent {agent} : {label}, pris au prochain "
                                                     "démarrage du cerveau.")
        return {"ok": True, "agent": agent, "effort": now, "applique": applied, **agents_mod.effort_info(agent)}

    def _note_depense(self, data: dict | None) -> list[dict] | None:
        """Fin d'un tour. Plan P19 : consommation comptée pour TOUTE connexion (`_note_consommation`).
        Clé API (plan P11) : dépense estimée du jour, ALERTE seule au-delà du seuil, jamais d'arrêt ;
        facturé à l'usage seulement (Grok par abonnement, forfait, n'est jamais compté en dollars).
        04/11 (T50) : rend les ajouts du compteur (sous-agents compris), None si rien n'a été compté :
        le budget de mission les lit."""
        if not isinstance(data, dict):
            return None
        eff = getattr(getattr(self, "brain", None), "fournisseur", None)
        ajouts = self._note_consommation(eff, data)
        if eff is None or eff.facturation != "usage" or self._local_active():
            return ajouts
        pid = getattr(getattr(self.brain, "_proc", None), "pid", 0) or 0
        try:
            seuil = float(reglages.value("depense_alerte_usd"))
        except Exception:  # noqa: BLE001
            seuil = float(config.DEPENSE_ALERTE_USD)
        spent = fournisseurs.depense.noter(data.get("total_cost_usd"), pid, seuil)
        if spent is not None:
            log.warning("alerte de dépense : %.2f $ estimés aujourd'hui (seuil %.2f $)", spent, seuil)
            bus.publish("notice", level="warning",
                        text=f"Dépense estimée aujourd'hui avec ta clé API : environ {spent:.2f} $ (seuil {seuil:.2f} $). "
                             "Rien n'est arrêté ; le seuil se règle dans Réglages › Fournisseur.")
        return ajouts

    def _compteur(self) -> consommation.Compteur:
        """Compteur du cœur (seul écrivain de `runtime/consommation.json`). Chemin : celui de
        `config.CONSOMMATION_FILE` s'il existe, sinon à côté de la dépense du jour (`runtime/`) ;
        les tests isolent `DEPENSE_FILE` (conftest), donc jamais le vrai fichier. Créé à la
        demande : il garde les cumuls déjà comptés (delta par process) d'un tour à l'autre."""
        path = getattr(config, "CONSOMMATION_FILE", None) or config.DEPENSE_FILE.with_name("consommation.json")
        cpt = self.__dict__.get("_conso")
        if cpt is None or cpt.path != path:
            cpt = self._conso = consommation.Compteur(path)
        return cpt

    def _note_consommation(self, eff: "fournisseurs.Effectif | None", data: dict) -> list[dict] | None:
        """`modelUsage` du `result` (cumulé par process) ET `usage` du tour, toujours passés : c'est
        `Compteur.noter` qui arbitre (modelUsage prime ; `usage` SEULEMENT si modelUsage n'a aucune
        ligne exploitable ou un cumul nul, donc jamais compté deux fois, fiches 6 et 9 du journal
        p19). Puis l'évènement `consommation`. Une panne ici ne casse jamais le tour."""
        try:
            local = self._local_active()
            process = data.get("process")
            if not isinstance(process, int) or isinstance(process, bool) or process <= 0:
                # Sans id (rejeu sans process) : -1, jamais 0 (`Compteur._process` démarre à 0).
                process = -1
            init = getattr(self.brain, "init_info", None) or {}
            modele = str((self.brain.local if local else "") or getattr(eff, "modele", "") or
                         init.get("model") or getattr(self.brain, "model", "") or "")
            ajouts = self._compteur().noter(
                consommation.fournisseur_de(eff, local), data.get("modelUsage"), process=process,
                usage=data.get("usage"), modele=modele)
        except Exception:  # noqa: BLE001 — le compteur est un affichage, pas une raison d'échouer
            log.exception("consommation non comptée")
            return None
        if ajouts:
            self._publish_consommation()
        return ajouts

    def _seuil_mois(self) -> float:
        """Réglage `depense_alerte_mois_usd` (lot suivant, settings.py) ; absent ou illisible : 0."""
        try:
            return float(reglages.value("depense_alerte_mois_usd"))
        except Exception:  # noqa: BLE001 — réglage pas encore créé
            return 0.0

    def _publish_consommation(self) -> None:
        """Évènement `consommation` (sticky) : mode du panneau, jour et mois par fournisseur et
        modèle, fiabilité du coût, en DONNÉES seules (le HUD met en mots). Relu sur le disque : le
        HUD ne montre jamais un ajout que l'écriture n'a pas retenu."""
        try:
            eff = getattr(getattr(self, "brain", None), "fournisseur", None)
            local = self._local_active()
            vue = consommation.vue(consommation.mode_de(eff, local), donnees=self._compteur().donnees(),
                                   seuil_mois_usd=self._seuil_mois())
            bus.publish("consommation", fournisseur=consommation.fournisseur_de(eff, local), **vue)
        except Exception:  # noqa: BLE001
            log.exception("consommation non publiée")

    def _non_claude(self) -> bool:
        """Fournisseur autre que Claude en cours OU choisi (plan P11) : ni Total, ni levée."""
        eff = getattr(getattr(self, "brain", None), "fournisseur", None)
        return (eff is not None and eff.genre in ("passerelle", "cli")) or fournisseurs.non_claude_choisi()

    def _announce_fournisseur(self) -> None:
        """Une notice quand le fournisseur pris au démarrage change (ou n'a pas pu être pris)."""
        eff = getattr(getattr(self, "brain", None), "fournisseur", None)
        if eff is None:
            return
        key = f"{eff.session_key}|{eff.note}"
        if key == self._fournisseur_dit:
            return
        first = not self._fournisseur_dit
        self._fournisseur_dit = key
        if eff.note:
            bus.publish("notice", level="warning", text=f"Fournisseur : {eff.note}.")
        if eff.genre in ("passerelle", "cli"):  # P27 : CLI d'un autre fournisseur aussi
            bus.publish("notice", level="warning", text=(
                f"Cerveau sur {eff.libelle()}, sans Claude : "
                + ("outils en mode prudent." if eff.outils else "conversation seule, sans outil ni mémoire.")
                + " Ni mode Sans confirmation ni levée du garde-fou."))
        elif eff.genre == "cle_claude":
            bus.publish("notice", level="info", text="Cerveau sur ta clé API Claude : facturé à l'usage.")
        elif not first and not eff.note:
            bus.publish("notice", level="info", text="Cerveau sur l'abonnement Claude.")

    @staticmethod
    def _quota_alert() -> float:
        """Part de la fenêtre de 5 h à partir de laquelle on sauvegarde le fil (85 %
        par défaut, réglable à chaud depuis la page « Réglages » du HUD)."""
        return reglages.value("quota_alert_pct") / 100

    @staticmethod
    def _quota_level(q: dict) -> float:
        """Remplissage de la fenêtre de l'abonnement la plus pleine, de 0 à 1."""
        level = 0.0
        for w in (q.get("windows") or {}).values():
            if isinstance(w, dict):
                u = w.get("utilization")
                if isinstance(u, (int, float)):
                    level = max(level, u / 100 if u > 1 else u)
        if q.get("status") not in (None, "", "allowed"):
            level = max(level, 1.0)
        return level

    @staticmethod
    def _same_window(key: str, seen: Any) -> bool:
        """Clé « une fois par fenêtre » (`resets_at`) déjà vue : égale, ou à moins de 5 min
        (22/10 : l'heure du texte de /usage, « 8:19pm », et celle du flux, 20:20:00, diffèrent
        d'une minute ; sans tolérance, deux points de reprise dans la même fenêtre)."""
        if seen is None:
            return False
        if key == str(seen):
            return True
        from ..brain import epoch_s  # import local : la ligne d'imports est partagée avec d'autres chantiers
        a, b = epoch_s(key), epoch_s(seen)
        return bool(a and b) and abs(a - b) <= quota_releve.RECALAGE_S

    def _guard_quota(self, q: dict) -> None:
        """Limite de session de 5 h proche (13/10) : une fois par fenêtre, demande à Claude
        d'écrire le point de reprise des chantiers avant la coupure."""
        level = self._quota_level(q)
        mission.quota = level  # une mission s'arrête d'elle-même au-delà de MISSION_QUOTA_STOP
        try:
            self._quota_arret.observe(q)  # plan P34 : note puis arrêt des agents (fenêtre de 5 h)
        except Exception:
            log.exception("arrêt pour quota")
        if self._local_active():
            # Mode léger : le relevé au repos lit encore le quota ; le tour « point de reprise » serait
            # gelé puis rejoué plein contexte au retour sur Claude (relecture du 22/10, fiche 6).
            return
        if reprise.state.closed():
            return  # limite déjà atteinte : le cerveau ne répondrait pas (reprise programmée, idée 6)
        self._offer_model(q, level)
        key = str(q.get("resets_at") or "")
        if level < self._quota_alert() or self._same_window(key, getattr(self, "_quota_saved_for", None)):
            return
        if q.get("status") == "rejected":
            return  # limite atteinte sans heure connue (sinon `closed()` plus haut) : le tour échouerait
        if config.ESTOP_FILE.exists():
            return  # arrêt d'urgence : rien ne tourne que l'utilisateur n'ait demandé
        self._quota_saved_for = key
        log.info("limite de session proche (%.0f %%) : demande du point de reprise", level * 100)
        self.handle(
            "La limite de session de 5 h approche. Mets à jour MAINTENANT le fichier "
            "CHANTIERS-EN-COURS.md de la mémoire (voir ta consigne) : chantiers ouverts, "
            "agents en cours, décisions prises, suite exacte à faire. Réponds ensuite en une phrase.",
            source="réflexe")

    def _lighter_model(self) -> tuple[str, str]:
        """(cible, nom) plus économe que le modèle en cours : Opus → Sonnet, Sonnet → Haiku,
        Haiku → le modèle local s'il est choisi, installé et qu'Ollama répond ; ("", "") sinon."""
        current = str(getattr(self.brain, "model", "") or "")
        if current == "opus":
            return "sonnet", "Sonnet"
        if current == "sonnet":
            return "haiku", "Haiku"
        if current == "haiku":
            tag = modeles_locaux.local.ready()
            if tag:
                return "local", f"le mode léger ({tag})"
        return "", ""

    def _offer_model(self, q: dict, level: float) -> None:
        """À `quota_offer_pct` % de la fenêtre la plus pleine, UNE carte par fenêtre (clé
        `resets_at`) propose un modèle plus économe. Jamais appliquée seule : il faut le clic
        (`_offer_answer`) ; « Ne plus proposer » coupe le réglage `quota_offer`."""
        if reglages.value("quota_offer") != "propose" or level < reglages.value("quota_offer_pct") / 100:
            return
        key = str(q.get("resets_at") or "")
        if not self._offer_loaded:  # fenêtre déjà proposée avant une relance : pas de seconde carte
            self._offer_loaded = True
            seen = modeles_locaux.local.state().get("offer_for")
            self._offer_for = str(seen) if isinstance(seen, str) else self._offer_for
        if self._same_window(key, self._offer_for) or config.ESTOP_FILE.exists() or self._local_active():
            return
        target, name = self._lighter_model()
        if not target:
            return
        self._offer_for = key
        try:
            modeles_locaux.local.remember(offer_for=key)
        except OSError as exc:
            log.warning("offre de modèle non retenue : %s", exc)
        reset = q.get("reset_at")
        when = f", réinitialisée à {reprise.heure(float(reset))}" if isinstance(reset, (int, float)) and reset else ""
        if target == "local":
            cost = ("Mode léger : discussion seulement, sans outil ni mémoire, et la conversation repart à neuf ; "
                    "retour sur Claude quand la limite se lève.")
            go = "Passer en mode léger"
        else:
            cost = "Le prochain échange relira la conversation sans cache."
            go = f"Passer sur {name}"
        stay = f"Rester sur {model_label(self.brain.model)}"
        never = "Ne plus proposer"
        question = (f"Tu as utilisé {round(level * 100)} % de ta limite d'utilisation{when}. Passer sur {name}, "
                    f"plus économe ? {cost} Réglable dans Réglages › Limite de l'abonnement.")
        res = choices.ask(question, [go, stay, never], channel=channels.attention(), kind="offre",  # P41 : canal ouvert
                          meta={"actions": {go: f"switch:{target}", stay: "stay", never: "never"}})
        log.info("offre de modèle (%.0f %%) : %s → %s (%s)", level * 100, self.brain.model, target,
                 res.get("id") or res.get("error"))

    def _offer_answer(self, result: dict) -> dict:
        """Clic de l'utilisateur sur la carte d'offre : traité ici, SANS tour de Claude."""
        action = str(((result.get("meta") or {}).get("actions") or {}).get(result.get("value"), "stay"))
        out: dict[str, Any] = {"ok": True, "id": result.get("id"), "offre": action}
        if action.startswith("switch:"):
            target = action.partition(":")[2]
            res = self._local_switch(True, by="carte") if target == "local" else self._set_model(target)
            if not res.get("ok"):
                bus.publish("notice", level="warning", text=f"Changement de modèle non fait : {res.get('error')}")
                out["error"] = res.get("error")
        elif action == "never":
            try:
                reglages.set("quota_offer", "off")
            except SettingError as exc:
                out["error"] = exc.message
            else:
                reglages.publish()
                bus.publish("notice", level="info", text="Plus de proposition de modèle : réactivable dans "
                                                         "Réglages › Limite de l'abonnement.")
        log.info("offre de modèle : %s", action)
        return out

    def _local_active(self) -> bool:
        return bool(getattr(getattr(self, "brain", None), "local", ""))

    def _local_switch(self, on: bool, by: str = "HUD", force: bool = False) -> dict:
        """Bascule du cerveau entre Claude et le modèle local : une RELANCE du process (le
        contexte est perdu en local ; au retour, la session Claude d'avant est reprise). Jamais
        pendant une tâche, sauf `force` (retour sur Claude à la réinitialisation) ; jamais avec
        Autonome TOTAL ni la levée du garde-fou. Refusé à Claude par ses appelants."""
        local = modeles_locaux.local
        if on and hors_ligne.actif():  # 04/11 : le moteur local répond déjà à tout, Claude est arrêté
            return {"ok": False, "error": "mode « rien ne sort » actif : le modèle local répond déjà à tout."}
        if on == self._local_active():
            self._publish_model()
            return {"ok": True, "local": on}
        busy_error = {"ok": False, "error": "une tâche tourne : réessaie quand elle sera finie (la bascule relance "
                                            "le cerveau)."}
        unsafe_error = {"ok": False, "error": "coupe d'abord le mode Sans confirmation et rétablis le garde-fou : jamais avec "
                                              "un modèle local."}
        if not force and self._local_busy():
            return busy_error
        name = tag = ""
        if on:
            with self._local_lock:
                if self._local_switching:
                    return {"ok": False, "error": "passage en mode léger déjà en cours."}
                if self._total or self._lift:
                    return unsafe_error
                self._local_switching = True  # Autonome TOTAL et levée refusés jusqu'à la fin
        try:
            if on:
                tag = local.ready()
                if not tag:
                    return {"ok": False, "error": "aucun modèle local prêt : choisis un modèle installé dans "
                                                  "Réglages › Modèle local, avec Ollama en marche."}
                name = local.prepare(tag)  # jusqu'à 120 s (`/api/create`) : tout est revérifié après
            # Sous les verrous de tour et d'envoi : aucun message ne part vers l'ancien cerveau
            # pendant la relance, et rien n'a pu démarrer depuis les vérifications.
            with getattr(self, "_turn_lock", None) or contextlib.nullcontext(), \
                    getattr(self, "_send_lock", None) or contextlib.nullcontext():
                if not force and self._local_busy():
                    return busy_error
                with self._local_lock:
                    if on and (self._total or self._lift):
                        return unsafe_error
                    self.brain.local = name  # dès ici, `_local_active()` : TOTAL et levée refusés
                self._cerveau_relancer(fresh=on)  # 04/11 (T46) : périme une relance programmée
                self._end_turn(followup=False)
        finally:
            if on:
                self._local_switching = False
        back_at = self._local_return_at() if on else 0.0
        local.set_active(on, derived=name, tag=tag, return_at=back_at)
        log.warning("mode léger %s (%s)%s", "ACTIVÉ" if on else "coupé", by,
                    f" : {name}, retour prévu à {reprise.heure(back_at)}" if on else "")
        self._publish_model()
        bus.publish("notice", level="info", text=(
            f"Mode léger : je discute avec {tag or name}, sans aucun outil ; la conversation repart à "
            f"neuf. Retour sur Claude à {reprise.heure(back_at)}, ou depuis Réglages › Modèle local." if on else
            f"Retour sur Claude ({model_label(self.brain.model)})."))
        threading.Thread(target=reglages.publish, name="settings-local", daemon=True).start()
        return {"ok": True, "local": on, "model": name or self.brain.model}

    def _local_busy(self) -> bool:
        """Un tour ou une tâche de fond en cours : la relance du cerveau les tuerait."""
        with self._tasks_lock:
            return self._turn_active or bool(self._tasks)

    def _local_return_at(self) -> float:
        """Heure du retour automatique sur Claude, fixée à l'ENTRÉE en mode léger (relecture du
        19/10 : sans limite réellement atteinte, rien ne ramenait sur Claude). Dans l'ordre : la
        reprise en attente, la réinitialisation annoncée par le dernier évènement de quota, sinon
        `LOCAL_RETURN_FALLBACK_S` (5 h)."""
        now = time.time()
        info = reprise.state.info()
        pending = float(info.get("reset_at") or 0) if info.get("pending") else 0.0
        if pending > now:
            return pending
        q = getattr(self.brain, "quota", None) or {}
        reset = q.get("reset_at") if isinstance(q, dict) else None
        if isinstance(reset, (int, float)) and reset > now:
            return float(reset)
        return now + config.LOCAL_RETURN_FALLBACK_S

    def _local_return_tick(self) -> None:
        """Mode léger : à l'heure retenue (+ `REPRISE_DELAY_S`), retour sur Claude quand tout est
        calme. Une reprise en attente s'en charge elle-même (`_reprise_fire`)."""
        at = modeles_locaux.local.return_at
        if not at or time.time() < at + config.REPRISE_DELAY_S or reprise.state.info().get("pending"):
            return
        if (config.ESTOP_FILE.exists() or self._turn_active or self._pending_confirm is not None
                or self._floor_taken()):
            return
        res = self._local_switch(False, by="heure de reprise")
        if not res.get("ok"):
            log.info("retour sur Claude repoussé : %s", res.get("error"))

    def _local_freeze(self, text: str, source: str, channel: str, shown: str | None,
                      pourquoi: str = "Mode léger") -> None:
        """Mode léger (ou « rien ne sort », 04/11 : `pourquoi`) : un message automatique (réflexe, plan,
        mission) attend le retour sur Claude (gardé en mémoire, 20 au plus) ; une relève est abandonnée."""
        if source == "relève":
            log.info("%s : relève abandonnée", pourquoi)
            return
        frozen = [f for f in (self._local_frozen or []) if f["text"] != text]
        frozen.append({"text": text, "source": source, "channel": channel, "shown": shown})
        self._local_frozen = frozen[-20:]
        self._local_keep_frozen()  # un plan validé en mode léger survit à une relance
        log.info("%s : message %s gelé jusqu'au retour sur Claude (%d caractères)", pourquoi, source, len(text))
        bus.publish("notice", level="info", channel=channel, text=(
            f"Mode léger : un message automatique ({source}) attend le retour sur Claude." if pourquoi == "Mode léger"
            else f"Mode « rien ne sort » : un message automatique ({source}) attend le retour en ligne."))

    def _local_keep_frozen(self) -> None:
        try:
            modeles_locaux.local.remember(frozen=list(self._local_frozen or []))
        except OSError as exc:
            log.warning("messages gelés du mode léger non retenus : %s", exc)

    def _local_tick(self) -> None:
        """Chaque seconde : en mode léger, retour sur Claude à l'heure retenue ; de retour sur
        Claude, relance UN message gelé quand tout est calme."""
        if self._local_active():
            self._local_return_tick()
            return
        if not self._local_frozen or hors_ligne.actif():  # 04/11 : rien ne repart vers Claude hors ligne
            return
        if (config.ESTOP_FILE.exists() or self._turn_active or self._pending_confirm is not None
                or self._floor_taken()):
            return
        item = self._local_frozen.pop(0)
        self._local_keep_frozen()  # retiré du fichier AVANT l'envoi : jamais relancé deux fois
        log.info("message gelé en mode léger relancé (%s)", item.get("source"))
        self.handle(str(item["text"]), source=str(item.get("source") or "réflexe"),
                    channel=str(item.get("channel") or ""), shown=item.get("shown"))

    def _local_view(self) -> dict:
        """Ligne « Mode léger » de Réglages › Modèle local (genre `action`)."""
        local = modeles_locaux.local
        tag = local.chosen
        if self._local_active():
            tag = local.active_tag or tag
            info = reprise.state.info()
            back = float(info.get("reset_at") or 0) if info.get("pending") else local.return_at
            when = f" à {reprise.heure(back)}" if back else ""
            lines = [{"label": "État", "text": f"MODE LÉGER : {tag or self.brain.local}, sans aucun outil",
                      "level": "warn"},
                     {"label": "Retour", "level": "info",
                      "text": f"sur Claude automatiquement{when}, ou par le bouton"}]
            return {"value": {"lines": lines}, "actions": [{"id": "local_off", "label": "Revenir sur Claude"}]}
        lines = [{"label": "État", "text": f"Claude ({model_label(self.brain.model)})", "level": "ok"},
                 {"label": "Modèle local", "level": "info",
                  "text": tag or "aucun choisi : télécharges-en un dans « Modèles locaux » ci-dessous"}]
        action = {"id": "local_on", "label": "Passer en mode léger",
                  "confirm": "Discussion seulement, sans outil ni mémoire ; la conversation avec le modèle local "
                             "repart à neuf. Retour sur Claude quand la limite se lève, ou par « Revenir sur Claude »."}
        with self._tasks_lock:
            busy = self._turn_active or bool(self._tasks)
        why = ("une tâche tourne" if busy else
               "Mode Sans confirmation ou garde-fou levé : jamais avec un modèle local" if self._total or self._lift else
               "" if local.ready() else "aucun modèle local installé et choisi, ou Ollama arrêté")
        if why:
            action.update(disabled=True, reason=why)
        return {"value": {"lines": lines}, "actions": [action]}

    def _local_action(self, action: str, cmd: dict) -> dict:
        if cmd.get("_from_claude") is True:
            return {"ok": False, "error": "le mode léger se règle depuis le HUD ou le terminal, jamais par Claude"}
        if action not in ("local_on", "local_off"):
            return {"ok": False, "error": f"action inconnue : {action}"}
        res = self._local_switch(action == "local_on", by="HUD" if cmd.get("_via") == "hud" else "terminal")
        if res.get("ok"):
            res = {**res, "text": "Mode léger activé." if action == "local_on" else "De retour sur Claude."}
        return res

    def _local_command(self, name: str, cmd: dict) -> dict:
        """`--send "local on|off|status"` (ou `local_on`, `local_off`) : refusé à Claude."""
        arg = name.partition("_")[2] or str(cmd.get("text") or cmd.get("action") or "").strip().lower()
        if arg in ("", "status", "etat", "état"):
            return {"ok": True, "local": self._local_active(), "model": modeles_locaux.local.chosen}
        if cmd.get("_from_claude") is True:
            log.warning("mode léger refusé : la commande vient de Claude")
            return {"ok": False, "error": "le mode léger se règle depuis le HUD ou le terminal, jamais par Claude"}
        if arg not in ("on", "off"):
            return {"ok": False, "error": "attendu : on, off ou status"}
        return self._local_switch(arg == "on", by="HUD" if cmd.get("_via") == "hud" else "terminal")

    def _local_boot(self) -> None:
        """Au démarrage : mode léger retenu (`runtime/local-model.json`) → repris s'il est encore
        prêt, sinon oublié (le cerveau part sur Claude). Les messages gelés sont relus : ils
        partiront au retour sur Claude (`_local_tick`)."""
        local = modeles_locaux.local
        frozen = local.state().get("frozen")
        if isinstance(frozen, list):
            self._local_frozen = [f for f in frozen if isinstance(f, dict) and f.get("text")][-20:] or None
        if not local.active:
            return
        tag = local.ready()
        if tag:
            self.brain.local = str(local.state().get("derived") or tag)
            if not local.return_at:  # mode léger d'avant le correctif : sans heure de retour
                local.remember(return_at=self._local_return_at())
            log.info("mode léger repris au démarrage : %s (retour sur Claude à %s)", self.brain.local,
                     reprise.heure(local.return_at))
        else:
            local.set_active(False)
            log.warning("mode léger retenu mais modèle local indisponible : démarrage sur Claude")
            bus.publish("notice", level="warning", text="Mode léger retenu, mais le modèle local ne répond pas : "
                                                        "démarrage sur Claude.")
