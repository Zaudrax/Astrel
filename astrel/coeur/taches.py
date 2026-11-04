"""Agents et tâches de fond : journal des outils des agents, un seul écrivain par fichier,
relais d'équipe, coût et alertes, todo, courrier, mémoire du PC, détail d'un agent.

Mixin de `Astrel` (core.py), découpé du cœur le 29/10/2026 : méthodes déplacées telles quelles.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .. import agents as agents_mod
from .. import boucles
from .. import config
from .. import courrier
from .. import detail_agent
from .. import equipe
from .. import memoire
from .. import quota_arret
from .. import relief
from .. import todo as todo_mod
from ..brain import BrainEvent
from ..channels import channels
from ..events import bus
from ..reports import reports
from ..core import TODO_POINT_ATTENTE_S, TODO_POINT_S, _AGENT_LOG_MAX, _AWAITED_TOOLS, _DEPOTS, _WRITE_TOOLS, _alert_limits, _count, _todo_refs, _tokens_phrase, log  # aides du cœur (le paquet charge core d'abord)


# 04/11 (T50) : une ligne par sous-agent fini (coût, modèle, dépôts) : rendement par métier
# (`tools/rendement_metiers.py`), jointure exacte avec les fiches par `depots`. Comme `route.jsonl`.
COUTS_LOG = Path(os.environ.get("ASTREL_COUTS_LOG") or config.LOGS / "agents-couts.jsonl")
COUTS_LOG_MAX = 5 * 1024 * 1024


def noter_cout(done: dict, entry: dict, status: str) -> None:
    """Ligne de `COUTS_LOG` à la fin d'un sous-agent ; rotation en `.1` au-delà de 5 Mo. Jamais d'échec."""
    ligne = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "fin": time.time(), "tuid": str(done.get("tool_use_id") or ""),
        "type": str(entry.get("type") or ""), "desc": str(done.get("desc") or "")[:120],
        "modele_demande": str(entry.get("model") or ""), "effort": str(entry.get("effort") or ""),
        "statut": status, "requetes": int(done.get("requests") or 0),
        "travail": int(done.get("tokens") or 0), "cache_lu": int(done.get("cache_lu") or 0),
        "total": int(done.get("tokens_total") or done.get("tokens") or 0), "estime": bool(done.get("estime", True)),
        "duree_s": float(done.get("elapsed_s") or 0.0),
        "depots": [{k: d.get(k) for k in ("outil", "mission", "de", "a", "ok")} for d in entry.get("depots") or []],
    }
    try:
        COUTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        if COUTS_LOG.exists() and COUTS_LOG.stat().st_size > COUTS_LOG_MAX:
            COUTS_LOG.replace(COUTS_LOG.with_name(COUTS_LOG.name + ".1"))
        with COUTS_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ligne, ensure_ascii=False) + "\n")
    except OSError:
        log.debug("coût d'agent non noté", exc_info=True)


class TachesCoeur:
    """Agents et tâches de fond : journal des outils des agents, un seul écrivain par fichier,"""

    @property
    def _agent_desc(self) -> dict[str, str]:
        """Id de l'appel Agent → sa description, pour nommer l'agent d'une alerte de boucle."""
        if self._agent_desc_map is None:
            self._agent_desc_map = {}
        return self._agent_desc_map

    @property
    def _agent_log(self) -> dict[str, dict]:
        """Journal d'agent tenu par le cœur, sans la coopération de l'agent (plan P9) : id de
        l'appel Agent (ou Bash de fond) → consigne, type, fichiers écrits, outils en cours.
        Sert à la relève immédiate (`relief.py`) pour noter où en était un agent arrêté."""
        if self._agent_log_map is None:
            self._agent_log_map = {}
        return self._agent_log_map

    def _note_agent_tool(self, ev: BrainEvent, scope: str) -> None:
        """`tool_start` : la consigne d'un agent lancé par Astrel, ou un outil d'un agent."""
        tool_input = ev.data.get("input") if isinstance(ev.data.get("input"), dict) else {}
        book = self._agent_log
        if scope == boucles.MAIN:
            if ev.tool in ("Agent", "Task"):
                kind = str(tool_input.get("subagent_type") or "agent")
                consigne = str(tool_input.get("prompt") or "")
            elif ev.tool == "Bash" and tool_input.get("run_in_background"):
                kind, consigne = "commande", str(tool_input.get("command") or "")
            else:
                return
            # Plan P18 (détail d'agent) : début, dernières actions, échecs, liste TodoWrite.
            book[ev.tool_id] = {"type": kind, "consigne": consigne, "files": [], "pending": {},
                                "started": time.time(), "actions": [], "errors": 0, "last_errors": [],
                                "todos": None}
            if ev.tool in ("Agent", "Task"):
                # Plan P28 : effort que cet agent a reçu (celui de sa définition chargée par le CLI).
                book[ev.tool_id]["effort"] = agents_mod.effort_at_launch(kind)
                # 27/10 : modèle demandé, et consigne d'ORIGINE si c'est une reprise de la relève (plafond
                # de relances et « déjà fait » d'avant) ; la relève apprend qu'il est relancé.
                book[ev.tool_id]["model"] = str(tool_input.get("model") or "")
                origine, avant = relief.decouper(consigne)
                if origine:
                    book[ev.tool_id].update(origine=origine, avant=avant)
                r = getattr(self, "relief", None)
                if isinstance(r, relief.Relief):
                    r.vu_relance(consigne)
            self._trim_agent_log()
            return
        entry = book.get(scope)
        if entry is None:
            return
        if ev.tool == "mcp__astrel__passation":
            self._quota_arret.ack(scope)  # plan P34 : l'agent a noté son état (accusé)
        entry["pending"][ev.tool_id] = (ev.tool, ev.text)
        detail_agent.note_action(entry, ev.tool_id, ev.tool, ev.text, tool_input)
        if ev.tool in _DEPOTS:
            # 27/10 : plan, passation, fil, courrier déposés par l'agent (`ok` posé à son résultat).
            entry.setdefault("depots", []).append({
                "id": ev.tool_id, "outil": _DEPOTS[ev.tool], "ok": None,
                "titre": str(tool_input.get("title") or "")[:200], "plan_id": str(tool_input.get("plan_id") or ""),
                "mission": str(tool_input.get("mission") or ""), "de": str(tool_input.get("de") or ""),
                "a": str(tool_input.get("a") or "")})
            del entry["depots"][:-20]
        if ev.tool == "Bash" and tool_input.get("run_in_background"):
            # 27/10 : commande de fond d'un SOUS-AGENT : sa tâche du CLI a sa propre consigne (la
            # commande) et son agent (`parent`), pour être jointe à lui si la relève les arrête.
            book[ev.tool_id] = {"type": "commande", "consigne": str(tool_input.get("command") or ""),
                                "parent": scope, "files": [], "pending": {}, "started": time.time(),
                                "actions": [], "errors": 0, "last_errors": [], "todos": None}
            self._trim_agent_log()
        if ev.tool in _WRITE_TOOLS:
            path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
            if path and path not in entry["files"] and len(entry["files"]) < 50:
                entry["files"].append(path)
                self._owners_check(scope, path)

    def _trim_agent_log(self) -> None:
        """Garde `_AGENT_LOG_MAX` entrées, les plus anciennes partent d'abord, SAUF celles d'une tâche
        encore en cours (27/10 : un agent qui dure, évincé par les commandes de fond lancées après lui,
        perdait sa consigne et la relève ne pouvait plus le relancer). Plafond dur : 4 fois plus."""
        book = self._agent_log
        if len(book) <= _AGENT_LOG_MAX:
            return
        live: set[str] = set()
        tasks_lock = getattr(self, "_tasks_lock", None)
        if tasks_lock is not None:
            with tasks_lock:
                live = {str(t.get("tool_use_id") or "") for t in self._tasks.values()}
        newest = next(reversed(book))  # l'appel qui vient d'arriver : sa tâche n'est pas encore annoncée
        for key in list(book):
            if len(book) <= _AGENT_LOG_MAX:
                break
            if key not in live and key != newest:
                book.pop(key, None)
        while len(book) > 4 * _AGENT_LOG_MAX:
            book.pop(next(iter(book)))

    def _owners_check(self, scope: str, path: str) -> None:
        """Plan P13 : un seul écrivain par fichier. Un autre agent ENCORE EN COURS a déjà écrit
        ce fichier : alerte au HUD et message discret à Astrel, comme `boucles.py` ; rien n'est
        arrêté."""
        if self._owners is None:
            self._owners = equipe.Owners()
        tasks_lock = getattr(self, "_tasks_lock", None)
        if tasks_lock is None:
            return
        with tasks_lock:
            live = {str(t.get("tool_use_id") or "") for t in self._tasks.values()} - {""}
        others = self._owners.check(self._agent_log, live, scope, path)
        if not others:
            return
        kinds = [str(self._agent_log[k].get("type") or "agent") for k in (*others, scope)]
        hud, nudge = equipe.owners_alert(path, kinds)
        log.warning("%s", hud)
        bus.publish("notice", level="warning", text=hud)
        threading.Thread(target=self._nudge, args=(nudge,), name="owners-nudge", daemon=True).start()

    def _agent_writing(self, tool_use_id: str) -> bool:
        """Un agent a-t-il une écriture commencée et pas encore rendue : fichier, passation ou
        courrier (plan P39) ? Appelé par la relève (`relief.py`) et l'arrêt pour quota, qui
        l'attendent `RELIEF_STOP_WAIT_S` au plus."""
        entry = self._agent_log.get(tool_use_id) if tool_use_id else None
        return bool(entry) and any(tool in _AWAITED_TOOLS for tool, _ in entry["pending"].values())

    def _agent_busy(self, tool_use_id: str) -> bool:
        """Plan P39 : un agent a-t-il un AUTRE outil en cours (Bash, recherche…) que ceux de
        `_agent_writing` ? Il ne verra l'ordre d'arrêt qu'à la fin de cet outil (`quota_arret`)."""
        entry = self._agent_log.get(tool_use_id) if tool_use_id else None
        return bool(entry) and any(tool not in _AWAITED_TOOLS for tool, _ in entry["pending"].values())

    def _task_journal(self, tid: str) -> dict:
        """Entrée de la liste des agents interrompus par la relève (`relief.py`), {} si inconnue."""
        with self._tasks_lock:
            task = dict(self._tasks.get(tid) or {})
        if not task:
            return {}
        entry = self._agent_log.get(task.get("tool_use_id") or "") or {}
        doing = "; ".join(f"{tool} {desc}".strip() for tool, desc in entry.get("pending", {}).values())
        # 27/10 : pour TOUT métier, ce qu'il a déposé (plan, passation…) et sa liste d'étapes ; son appel
        # (`tuid`) et celui de son agent (`parent`, commande de fond d'un sous-agent) ; la consigne
        # d'origine et le « déjà fait » d'une reprise précédente ; le modèle demandé.
        return {"tid": tid, "type": entry.get("type") or task.get("kind") or "tâche", "desc": task.get("desc", ""),
                "consigne": entry.get("consigne", ""), "canal": task.get("channel", ""),
                "fichiers": list(entry.get("files", [])), "etape": task.get("step", ""),
                "outil_en_cours": doing, "note": "", "tuid": str(task.get("tool_use_id") or ""),
                "parent": entry.get("parent", ""), "origine": entry.get("origine", ""),
                "avant": entry.get("avant", ""), "modele": entry.get("model", ""),
                "deja": relief.deja_fait(entry) if entry else []}

    def _on_task(self, t: dict) -> None:
        """Panneau « en cours » du HUD : sous-agents et commandes du CLI, avec leur
        coût (`tokens`, `requests`, `elapsed_s`, lus dans `brain.task_info`) et
        `alert` au-delà des seuils (15/10) : ALERTE SEULE, rien n'est arrêté."""
        tid = t.get("id") or ""
        if not tid:
            return
        done = None
        alerts: list[dict] = []
        with self._tasks_lock:
            cur = self._tasks.get(tid)
            if t["phase"] == "end":
                done = self._tasks.pop(tid, None)
                # Plan P18 : gardée 10 min (statut, résumé) pour le détail d'agent au clic.
                detail_agent.remember_done(self._tasks_done, done or {}, t, time.time())
            elif cur is None:
                cur = self._tasks[tid] = {
                    "id": tid, "kind": t.get("kind", ""), "desc": t.get("desc") or t.get("step") or "tâche",
                    "background": t.get("background", False), "started": time.time(),
                    "step": t.get("step", ""), "tool_uses": t.get("tool_uses") or 0,
                    "channel": channels.task_started(tid),
                    "tokens": 0, "requests": 0, "elapsed_s": 0.0, "alert": False,
                    "tokens_total": 0, "cache_lu": 0,  # 04/11 (T50) : `tokens` = travail, le total à part
                    "tool_use_id": t.get("tool_use_id") or "",  # clé du journal d'agent (plan P9)
                }
            else:
                cur["vu"] = time.time()  # 01/11 : nouvelles de la tâche (tâche muette, `_muettes_tick`)
                if t.get("step"):
                    cur["step"] = t["step"]
                if t.get("tool_uses"):
                    cur["tool_uses"] = t["tool_uses"]
            if cur is not None and done is None and "refs" not in cur:
                # Plan P38 lot 2 : plans et missions cités par la consigne (badge « agent en cours »
                # de la vue Tâches), calculés une fois dès que la consigne est connue.
                consigne = (self._agent_log.get(cur.get("tool_use_id") or "") or {}).get("consigne")
                if consigne:
                    cur["refs"] = _todo_refs(str(consigne))
            if cur is not None and done is None:
                # 04/11 (T50, mesures honnêtes) : `tokens` affiché et comparé au seuil = le TRAVAIL (entrée hors
                # cache + création de cache + sortie) ; le total, cache relu compris, reste dans `tokens_total`.
                # Un cerveau d'avant (sans `travail`) : son `tokens` tel quel.
                travail = _count(t.get("travail"))
                fields = {"tokens": travail if travail is not None else _count(t.get("tokens")),
                          "tokens_total": _count(t.get("tokens")), "cache_lu": _count(t.get("cache_lu")),
                          "requests": _count(t.get("requests"))}
                for key, n in fields.items():
                    if n is not None:
                        cur[key] = max(cur.get(key, 0), n)  # des cumuls : jamais en arrière
                if "estime" in t:
                    cur["estime"] = bool(t["estime"])
                reported = t.get("elapsed_s")
                if isinstance(reported, (int, float)) and not isinstance(reported, bool):
                    cur["elapsed_s"] = max(cur.get("elapsed_s", 0.0), float(reported))
                alerts = self._task_costs(time.time())
            items = sorted(self._tasks.values(), key=lambda x: x["started"])
            done_list = self._done_list()
        bus.publish("tasks", items=items, done=done_list)
        self._task_alerts(alerts)
        if t["phase"] == "end":
            # Le tour que le CLI relance ensuite s'affichera dans le canal de la tâche.
            channel = channels.task_ended(tid, bool(done and done.get("background")))
            r = getattr(self, "relief", None)
            stopped = done is not None and isinstance(r, relief.Relief) and r.task_ended(tid, str(t.get("status") or ""))
            quota_stop = done is not None and not stopped and self._quota_arret.task_ended(tid, str(t.get("status") or ""))
            # P41 : le toast d'une tâche sans projet mène au canal ouvert, pas à Général.
            seen_in = channels.attention(channel)
            if quota_stop:
                # Plan P34 : arrêté pour le quota, noté par le cœur ; ni échec ni relais d'équipe.
                bus.publish("notice", level="info", channel=seen_in,
                            text=f"Arrêté pour le quota, relance proposée à la levée de la limite : {done['desc']}")
                stopped = True
            elif stopped:
                # Arrêtée par la relève immédiate (plan P9) : pas un échec, elle sera relancée.
                bus.publish("notice", level="info", channel=seen_in,
                            text=f"Arrêté pour la relève, relancé ensuite : {done['desc']}")
            elif done is not None and done.get("background"):
                failed = t.get("status") not in ("", "completed")
                bus.publish("notice", level="error" if failed else "info", channel=seen_in,
                            text=f"{'Échec' if failed else 'Terminé'} : {done['desc']}")
            if done is not None and done.get("kind") == "agent":
                noter_cout(done, self._agent_log.get(done.get("tool_use_id") or "") or {}, str(t.get("status") or ""))
            if done is not None and not stopped:
                self._equipe_after(done, str(t.get("status") or ""), channel)

    def _equipe_after(self, done: dict, status: str, channel: str) -> None:
        """Plan P13 (équipe d'agents) : fin d'une tâche du CODEUR → message « equipe » à Astrel
        (jamais dit) pour lancer le testeur puis le relecteur. Rien sous ESTOP."""
        if config.ESTOP_FILE.exists():
            return
        relay = equipe.after_codeur(self._agent_log.get(done.get("tool_use_id") or "") or {}, done, status)
        if relay is None:
            return
        text, shown = relay
        suspendu = quota_arret.relais_suspendu(str(done.get("desc") or ""), shown)
        if suspendu is not None:
            # Plan P39 : ordre de quota en cours (note ou arrêt), le suivant ne naît pas (26/10 : un
            # testeur lancé à 96 % a été coupé en pleine commande) ; Astrel le note dans CHANTIERS.
            text, shown = suspendu
            log.info("équipe : relais suspendu (ordre de quota en cours) après « %s »", done.get("desc", ""))
        else:
            log.info("équipe : relais testeur puis relecteur après « %s »", done.get("desc", ""))
        threading.Thread(target=self.handle, args=(text, "equipe", channel), kwargs={"shown": shown},
                         name="equipe-turn", daemon=True).start()

    def _task_costs(self, now: float) -> list[dict]:
        """Met à jour `elapsed_s` et `alert` de chaque tâche (sous `_tasks_lock`).
        Rend les tâches qui viennent de franchir un seuil : une alerte chacune."""
        max_tokens, max_s = _alert_limits()
        crossed = []
        for task in self._tasks.values():
            task["elapsed_s"] = round(max(task.get("elapsed_s", 0.0), now - task["started"]), 1)
            if not task.get("alert") and (task.get("tokens", 0) >= max_tokens or task["elapsed_s"] >= max_s):
                task["alert"] = True
                crossed.append(dict(task))
        return crossed

    def _task_alerts(self, crossed: list[dict]) -> None:
        """UN message par tâche qui s'emballe, dans le fil du HUD, sans voix (choix
        de l'utilisateur, comme `boucles.py`) : il décide lui-même de l'arrêter (Stop)."""
        for task in crossed:
            minutes = round(task["elapsed_s"] / 60)
            travail, relus = _tokens_phrase(task.get("tokens", 0)), _tokens_phrase(task.get("cache_lu", 0))
            text = (f"Agent qui s'emballe ? « {task['desc']} » : {travail} tokens de travail (hors cache relu ; "
                    f"{relus} relus), "
                    f"{task.get('requests', 0)} requêtes, {minutes} min. Rien n'est arrêté : bouton Stop du "
                    f"panneau « en cours » si besoin.")
            log.warning("%s", text)
            bus.publish("notice", level="warning", channel=channels.attention(task.get("channel", "")), text=text,
                        task_id=task["id"], task_alert=True)

    def _todo_tick(self, now: float | None = None) -> None:
        """Plan P38 lot 3 : toutes les 5 s, une fiche de passation arrivée (stat des `fiches.jsonl`
        seulement) fait republier la vue « Tâches » : le badge « prêt à clore » y est LU."""
        now = time.time() if now is None else now
        if now - getattr(self, "_todo_last", 0.0) < 5.0:
            return
        self._todo_last = now
        t = todo_mod.todo
        canaux = [(c.id, c.label) for c in channels.items()]
        premier = getattr(self, "_todo_depuis", None) is None
        if premier:
            self._todo_depuis = now  # démarrage du cœur : délai de grâce avant tout recul
        if t.veille_fiches() or premier:
            # 28/10 (carte T31) : prochaine étape de la carte, améliorations citées → cartes.
            if not t.suivre_fiches(canaux) and not premier:  # un changement de carte publie déjà
                t.publish(canaux)
        # 28/10 (carte T31, demande de l'utilisateur) : l'état des cartes suit les agents, sans modèle.
        vivants, retenus, descs, chans = self._todo_agents()
        r = getattr(self, "relief", None)
        pause = isinstance(r, relief.Relief) and bool(r.phase)
        t.ranger(vivants, retenus, now=now, depuis=self._todo_depuis, pause=pause)
        # Point automatique quand le dernier agent d'un projet se termine (relève et quota : retenus).
        projets = set(chans)
        for consigne in [*vivants, *retenus]:
            projets |= t.projets_de(consigne)
        avant = getattr(self, "_todo_projets", set())
        self._todo_projets = projets
        if not pause and avant - projets:
            # Une minute d'attente (`_todo_point`) : le testeur lancé juste après le codeur annule le point.
            t.demander_point(avant - projets, quand=now)
        t.annuler_point(projets)
        self._todo_point(now, descs)

    def _todo_agents(self) -> tuple[list[str], list[str], list[str], set[str]]:
        """(consignes d'origine des agents vivants, consignes des agents interrompus qui seront
        relancés — liste de la relève, arrêt pour quota —, descriptions des vivants, leurs projets
        de canal). Une consigne de reprise est ramenée à la consigne d'origine qu'elle contient."""
        with self._tasks_lock:
            tasks = [dict(x) for x in self._tasks.values() if x.get("kind") == "agent"]
        def origine(e: Any) -> str:
            if not isinstance(e, dict):
                return ""
            c = str(e.get("origine") or "") or str(e.get("consigne") or "")
            return relief.decouper(c)[0] or c
        vivants = [c for c in (origine(self._agent_log.get(str(x.get("tool_use_id") or ""))) for x in tasks) if c]
        descs = [str(x.get("desc") or "") for x in tasks]
        chans = {c for c in (str(x.get("channel") or "") for x in tasks) if c and c != channels.default}
        entries: list[Any] = list(relief.load_list())
        r = getattr(self, "relief", None)
        if isinstance(r, relief.Relief):
            entries += list(r.stopping.values()) + list(r.attendus.values())
        q = self._quota_arret_obj
        if q is not None:
            entries += list(q.agents) + list((q.reprise or {}).get("agents") or [])
        retenus = [c for c in (origine(e) for e in entries) if c]
        return vivants, retenus, descs, chans

    def _todo_point(self, now: float, agents: list[str]) -> None:
        """Point automatique (28/10, demande de l'utilisateur : ne plus avoir à demander où on en est) :
        posé dans le HUD comme un compte rendu « à lire », JAMAIS dit ; un toutes les 30 min au plus,
        jamais pendant que l'utilisateur a la parole ni pendant une relève ; même texte : pas reposé."""
        t = todo_mod.todo
        voulu = t.point_demande()
        if not voulu or now - getattr(self, "_todo_point_at", 0.0) < TODO_POINT_S:
            return
        if now < t.point_quand() + TODO_POINT_ATTENTE_S:  # une minute après la demande, relève comprise
            return
        r = getattr(self, "relief", None)
        if self._floor_taken() or (isinstance(r, relief.Relief) and r.phase):
            return
        text = t.point(None if "*" in voulu else voulu, agents=agents)
        t.point_pose()
        if not text or text == getattr(self, "_todo_point_text", ""):
            return
        self._todo_point_at, self._todo_point_text = now, text
        seul = next(iter(voulu)) if len(voulu) == 1 and "*" not in voulu else ""
        ch = channels.attention(seul)
        mid = uuid.uuid4().hex[:12]
        # Comme `_post_report(quiet=True)` : en silencieux ou dictée, posé sans « à lire » (ni toast ni
        # carillon), le bouton 🔊 reste (relecture fiche 4).
        pending = not self._voice_muted()
        reports.add(mid, text, ch, pending=pending, kind="resultat")
        bus.publish("report", text=text, channel=ch, mid=mid, report=True, pending_read=pending,
                    report_kind="resultat", report_files=[])
        log.info("todo : point automatique posé (%s)", ", ".join(sorted(voulu)))

    def _courrier_tick(self, now: float | None = None) -> None:
        """Plan P33 : toutes les 5 s, courrier entre agents (LECTURE SEULE des fichiers). Une notice
        du fil, sans voix, par nouveau message ; destinataire sans agent vivant → UN message
        « equipe » à Astrel (jamais dit) qui cite métier, nombre et mission, JAMAIS le texte ;
        3 par mission, puis une notice « à toi de décider ». Rien n'est suggéré sous ESTOP."""
        now = time.time() if now is None else now
        if now - getattr(self, "_courrier_last", 0.0) < courrier.PERIODE_S:
            return
        self._courrier_last = now
        if self._courrier is None:
            self._courrier = courrier.Veille()
        live: set[str] = set()
        tasks_lock = getattr(self, "_tasks_lock", None)
        if tasks_lock is not None:
            with tasks_lock:
                ids = {str(t.get("tool_use_id") or "") for t in self._tasks.values()} - {""}
            live = {courrier.metier((self._agent_log.get(i) or {}).get("type")) for i in ids} - {""}
        out = self._courrier.tick(live, config.ESTOP_FILE.exists())
        if getattr(self._courrier, "modifie", False):  # plan P33 lot 3 : volet du HUD, si l'état a changé
            self._courrier.modifie = False
            vue = courrier.etat(now)
            if vue != getattr(self, "_courrier_vue", None):
                self._courrier_vue = vue
                bus.publish("courrier", **vue)
        for text in out["notices"]:
            bus.publish("notice", level="info", text=text)
        for text in out["decisions"]:
            log.warning("%s", text)
            bus.publish("notice", level="warning", text=text)
        for consigne, shown in out["relances"]:
            log.info("courrier : %s", shown)
            threading.Thread(target=self.handle, args=(consigne, "equipe", ""), kwargs={"shown": shown},
                             name="courrier-turn", daemon=True).start()

    def _memoire_tick(self, now: float | None = None) -> None:
        """Toutes les 5 s (`_mission_loop`, plan P20) : pression mémoire du PC, LECTURE SEULE.
        `runtime/memoire.json` réécrit à chaque échantillon (le hook le juge périmé après 30 s),
        évènement `memory` seulement si une valeur arrondie ou l'état change, une ligne par minute
        (et à chaque changement d'état) dans `logs/memoire.jsonl`."""
        now = time.time() if now is None else now
        if now - getattr(self, "_mem_last", 0.0) < memoire.PERIODE_S:
            return
        self._mem_last = now
        s = memoire.sample()
        prev = getattr(self, "_mem_state", None) or memoire.Etat()
        st = memoire.decide(s, prev, now)
        self._mem_state = st
        v = memoire.view(s, st)
        memoire.write_state(v, now)
        if v != getattr(self, "_mem_view", None):
            self._mem_view = v
            bus.publish("memory", **v)
        if st.etat != prev.etat or now - getattr(self, "_mem_journal", 0.0) >= memoire.JOURNAL_PERIODE_S:
            self._mem_journal = now
            memoire.journal(s, st, now)
        if st.etat != prev.etat:
            self._memoire_change(prev.etat, st.etat, v)

    def _memoire_change(self, before: str, after: str, v: dict) -> None:
        """Passage d'état : le blocage est DIT une fois (`_announce` : jamais pendant que l'utilisateur
        parle, voir `floor.py`) s'il est actif ; la reprise n'est qu'une notice silencieuse.
        Observation (blocage non activé) : notice seule, rien n'est refusé."""
        dispo, swap = memoire.fmt_go(v.get("ram_dispo_go")), v.get("swap_pct")
        if after == "blocage":
            log.warning("mémoire : blocage (disponible %s, swap %s %%, actif : %s)", dispo, swap, v.get("active"))
            if v.get("active"):
                bus.publish("notice", level="warning", text=(
                    f"Mémoire du PC presque pleine (RAM disponible {dispo}, swap {swap} %) : plus de nouvel agent "
                    "ni de navigateur de test jusqu'à ce qu'elle redescende. Rien n'a été fermé."))
                self._announce("La mémoire du PC est presque pleine : je ne lance plus de nouvel agent ni de "
                               "navigateur de test tant qu'elle ne redescend pas. Rien n'a été fermé.")
            else:
                bus.publish("notice", level="warning", text=(
                    f"Mémoire du PC presque pleine (RAM disponible {dispo}, swap {swap} %) : blocage en "
                    "observation, rien n'est refusé."))
        elif before == "blocage":
            log.info("mémoire : fin du blocage (disponible %s, swap %s %%)", dispo, swap)
            bus.publish("notice", level="info", text=(
                f"Mémoire redescendue (RAM disponible {dispo}, swap {swap} %) : nouveaux agents et navigateur "
                "de test de nouveau permis."))

    def _tasks_tick(self) -> None:
        """Chaque seconde (`_mission_loop`) : une tâche qui dure franchit le seuil de
        durée même si elle ne donne plus de nouvelles. Publié seulement si ça change, y compris
        quand une tâche finie sort de la liste (plan P18 : 10 min passées, même sans tâche vivante)."""
        tasks_lock = getattr(self, "_tasks_lock", None)
        if tasks_lock is None:
            return
        now = time.time()
        with tasks_lock:
            expired = False
            if self._tasks_done:
                before = len(self._tasks_done)
                detail_agent.prune_done(self._tasks_done, now)
                expired = len(self._tasks_done) != before
            if not self._tasks and not expired:
                return
            crossed = self._task_costs(now) if self._tasks else []
            changed = bool(crossed) or expired
            items = sorted(self._tasks.values(), key=lambda x: x["started"]) if changed else []
            done_list = detail_agent.done_items(self._tasks_done) if changed else []
        if changed:
            bus.publish("tasks", items=items, done=done_list)
            self._task_alerts(crossed)

    def _stop_task(self, tid: str) -> dict:
        """Bouton Stop d'une tâche du HUD : control_request « stop_task » du CLI,
        comme « Stop tout » mais pour une seule ; la session reste."""
        with self._tasks_lock:
            task = dict(self._tasks.get(tid) or {})
        if not task:
            return {"ok": False, "error": f"tâche inconnue ou déjà finie : {tid or '(vide)'}"}

        def stop() -> None:
            # Jusqu'à 5 s d'attente de la réponse du CLI : hors du fil de la commande.
            ok = self.brain.stop_task(tid)
            log.info("arrêt de la tâche %s demandé par l'utilisateur : %s", tid, "fait" if ok else "refusé par le CLI")
            bus.publish("notice", level="info" if ok else "error", channel=task.get("channel", ""),
                        text=f"{'Arrêt demandé' if ok else 'Arrêt impossible'} : {task.get('desc', 'tâche')}")

        threading.Thread(target=stop, name="stop-task", daemon=True).start()
        return {"ok": True, "task_id": tid}

    @property
    def _tasks_done(self) -> dict[str, dict]:
        """Plan P18 : tâches finies (10 dernières, 10 min), id → tâche + `ended`, `status`,
        `summary`. En mémoire seulement : vidé à la relance du cœur."""
        if self._tasks_done_map is None:
            self._tasks_done_map = {}
        return self._tasks_done_map

    def _done_list(self) -> list[dict]:
        """Tâches finies en bref, champ `done` de l'évènement `tasks` (sous `_tasks_lock`)."""
        detail_agent.prune_done(self._tasks_done, time.time())
        return detail_agent.done_items(self._tasks_done)

    def _task_detail(self, tid: str) -> dict:
        """Commande HUD `task_detail` (plan P18) : détail d'agent d'une tâche en cours ou finie
        depuis moins de 10 min. Données seules (le HUD les met en mots), ≤ 6 Ko, ni journalisées
        ni écrites : la consigne et les erreurs d'un agent ne vont qu'au HUD qui les demande."""
        now = time.time()
        with self._tasks_lock:
            task = dict(self._tasks.get(tid) or {})
            live = bool(task)
            if not live:
                detail_agent.prune_done(self._tasks_done, now)
                task = dict(self._tasks_done.get(tid) or {})
            others = [dict(t) for k, t in [*self._tasks.items(), *self._tasks_done.items()] if k != tid] if task else []
        if not task:
            # Fiche expirée (10 min) ou cœur relancé : le HUD le dit, ce n'est pas une erreur.
            return {"ok": False, "error": "unknown_task", "task_id": detail_agent.echo_id(tid)}
        entry = dict(self._agent_log.get(task.get("tool_use_id") or "") or {})
        kind = str(entry.get("type") or "").strip()
        found, ambiguous = None, False
        if kind and kind not in ("commande", "agent"):
            # Passation de CET agent seulement : bornée par les autres agents du même métier ;
            # deux en parallèle → rien n'est joint (on ne montre pas la fiche de l'autre).
            start = float(task.get("started") or now)
            end = now if live else float(task.get("ended") or now)
            same = [(float(o.get("started") or 0.0), float(o.get("ended") or now)) for o in others
                    if str((self._agent_log.get(o.get("tool_use_id") or "") or {}).get("type") or "").strip() == kind]
            window = detail_agent.passation_window(start, end, same)
            ambiguous = window is None
            found = detail_agent.passation_for(kind, window, now)
        effort = {"effort": str(entry.get("effort") or ""), **agents_mod.effort_info(kind)} if kind else None
        return detail_agent.assemble(task, entry, found, now, live, passation_ambiguous=ambiguous, effort=effort)
