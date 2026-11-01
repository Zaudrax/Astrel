"""Relève automatique de session : repartir d'un contexte vide SANS rien perdre.

Chaque tour renvoie tout le contexte au modèle : à 150 k tokens, une simple
question coûte 150 k tokens de lecture de cache. Quand le contexte du cerveau
(mesuré par `Brain.context_tokens`, depuis les usages du flux stream-json)
dépasse `CONTEXT_RELIEF_TOKENS` ET qu'Astrel est au repos, la relève se fait en
deux temps :

1. « save » : un tour silencieux (source « relève », jamais dit) demande au
   cerveau de mettre à jour la note CHANTIERS-EN-COURS ;
2. ce tour fini, si RIEN n'a bougé entre-temps (l'utilisateur n'a pas parlé, aucun
   agent ni tâche de fond, aucune confirmation), le cerveau repart à neuf.
   Le message d'amorçage (« relis CHANTIERS-EN-COURS ») est joint au PREMIER
   message suivant, pas envoyé tout de suite : une session neuve ne coûte rien
   tant qu'on ne lui parle pas, et la note n'est relue que si on en a besoin.
   L'amorce la présente comme un RÉSUMÉ FIGÉ (rien de ce qu'elle cite ne se
   relance sans que l'utilisateur le redemande, 15/10, idée 8) et ajoute le nombre
   de commits faits depuis sa mise à jour et sa taille si elle dépasse
   `RELIEF_NOTE_MAX_BYTES`. Champs fixes par chantier : vault/GABARIT-CHANTIER.md.

JAMAIS pendant un tour, une parole, une conversation, une confirmation ou un
arrêt d'urgence : on attend ; si quelque chose bouge pendant la sauvegarde, la
relève est abandonnée et retentée plus tard (`RELIEF_RETRY_S`). Ne remplace pas
la remise à zéro après `SESSION_IDLE_RESET_S` d'inactivité, qui reste telle quelle.

Relève IMMÉDIATE (19/10, plan P9, `RELIEF_IMMEDIATE`, demande de l'utilisateur) : ni les
agents ou tâches de fond, ni la file d'annonces, ni `RELIEF_IDLE_S` ne la retardent.
Une phase « stop » la précède : chaque tâche est arrêtée (`stop_task`, après au plus
`RELIEF_STOP_WAIT_S` d'attente d'une écriture de fichier en cours) et notée par le
cœur dans `RELIEF_REPRISE_FILE` (consigne, fichiers touchés, dernière étape) ; la
session neuve reçoit AUSSITÔT l'amorce et cette liste, et relance ces agents-là
seulement. Un message de l'utilisateur (ou un réflexe) pendant la relève est GARDÉ
(`held`) et part ensuite, le premier joint à la reprise ; relève abandonnée : il
repart dans l'ancienne session, avec la liste ; arrêt d'urgence : la liste est jetée.

Reprise identique pour TOUT métier (27/10, demande de l'utilisateur : un planificateur coupé avait été
relancé à zéro). Le cœur écrit lui-même la consigne de reprise de chaque agent (`relance_prompt` :
consigne d'origine entière entre deux lignes repères, ce qui est déjà fait, ordre de vérifier l'état
laissé) ; « déjà fait » compte les dépôts par les outils d'Astrel (plan, passation, fil, courrier,
relus sur le disque) et la liste TodoWrite (`deja_fait`). Le cerveau n'a qu'à la recopier dans
l'outil Agent ; chaque relance est vue (`vu_relance`) et un agent oublié est redemandé UNE fois à
la fin du tour (`_verifier_relances`), puis signalé. Une commande de fond d'un sous-agent est jointe
à son agent (`_rattacher`). Le plafond de relances suit la consigne d'ORIGINE (`_key`).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, quota_arret
from .events import bus
from .floor import backlog
from .settings import reglages

log = logging.getLogger("astrel.relief")

SAVE_PROMPT = (
    "[Relève automatique d'Astrel : ton contexte atteint {k} k tokens et tout est calme ; l'utilisateur n'a "
    "rien demandé.] Mets à jour MAINTENANT la note CHANTIERS-EN-COURS de la mémoire (memory_write) pour "
    "qu'une session neuve reprenne sans rien perdre. Pour chaque chantier ouvert, les champs fixes de la "
    "note GABARIT-CHANTIER : objectif ; état ; essayé sans succès et pourquoi ; décisions de l'utilisateur ; "
    "étape suivante exacte ; ABANDONNÉ (raison, condition de retour). Ajoute ce qui s'est dit d'important "
    "dans cette conversation et qui n'y est pas encore. Elle fait {size} Ko : garde-la sous {cap} Ko "
    "(resserre, le détail ancien va dans une archive). Les tâches de chaque projet vivent dans la todo "
    "(outils todo_*) : n'y recopie pas ses cartes ; une tâche nouvelle de cette session absente de la todo "
    "passe par todo_add, la note garde règles, décisions, abandons et rappels. "
    "Ne lance aucun agent ni aucune commande de fond. "
    "{agents}Un savoir DURABLE de cette session (préférence, décision, procédure) sans note à lui : "
    "propose-la avec memory_propose (kind nouvelle, une au plus), sans l'écrire toi-même. "
    "Réponds ensuite seulement « Note à jour. »"
)
# Ajouté à SAVE_PROMPT quand la phase « stop » a arrêté des tâches (plan P9).
SAVE_AGENTS = (
    "{n} agent(s) ou tâche(s) de fond viennent d'être arrêtés par la relève : Astrel garde leur liste à part "
    "et les relancera lui-même dans la session neuve. Ne relance rien ; dans la note, marque-les seulement "
    "« interrompus par la relève, relancés automatiquement ». "
)
# Amorce, second bloc (plan P9) : la liste du cœur fait foi, jamais la note.
# 27/10 (demande de l'utilisateur : « le planificateur doit être coupé comme les autres agents et reprendre là où il
# en était ») : le cœur écrit LUI-MÊME la consigne de reprise de chaque agent, quel que soit son métier
# (`relance_prompt`) ; le cerveau ne fait que la recopier dans l'outil Agent (le CLI n'offre pas au cœur de
# lancer un sous-agent).
REPRISE_BLOCK = (
    "[Agents interrompus par la relève : le cœur d'Astrel les relance MAINTENANT, tous, quel que soit leur "
    "métier, ceux de cette liste seulement (rien de ce que cite la note des chantiers). Pour chaque AGENT : "
    "outil Agent, en arrière-plan (run_in_background vrai), avec le subagent_type et la description indiqués, "
    "et pour prompt tout le texte entre <<<REPRISE et REPRISE>>>, recopié TEL QUEL, sans résumer, couper ni "
    "réécrire : il porte la consigne d'origine, ce qui est déjà fait et l'ordre de vérifier l'état laissé. "
    "Pour chaque COMMANDE de fond : vérifie d'abord l'état qu'elle a laissé, puis relance-la telle quelle (Bash, "
    "arrière-plan) si elle sert encore ; une simple attente ou un sondage ne se relance pas.]\n{items}\n"
)
REPRISE_GO = "Relance maintenant les agents de cette liste, puis réponds seulement « Agents relancés. »"
# Rappel UNIQUE (27/10) : le tour de relance est fini et des agents de la liste n'ont pas été relancés.
REPRISE_RAPPEL = "[Relance incomplète : ces agents de la liste n'ont pas été relancés.] "
# Consigne de reprise d'un agent : la consigne d'origine est entre ces deux lignes (relue par `decouper`).
ORIGINE_DEBUT = "<<<CONSIGNE D'ORIGINE"
ORIGINE_FIN = "CONSIGNE D'ORIGINE>>>"
REPRISE_TETE = (
    "[Reprise après la relève de session d'Astrel : tu as été arrêté en plein travail par Astrel (pas par une "
    "erreur). Reprends la MÊME mission là où elle en était ; ce n'est pas une nouvelle mission.]"
)
REPRISE_VERIFIER = (
    "Avant de refaire quoi que ce soit, vérifie l'état laissé : relis les fichiers écrits (git status et git "
    "diff s'il y a un dépôt) ; un plan déjà déposé ne se redépose pas (plan_list ; nouvelle version avec son "
    "plan_id seulement s'il est incomplet) ; une passation déjà déposée ne se refait pas ; une écriture ou une "
    "commande coupée peut être partielle. Reprends ensuite à l'étape suivante. Si tout est déjà fait, rends "
    "seulement ton rapport final, sans rien redéposer."
)
_AGENT_KINDS_SANS_TYPE = ("", "agent", "tâche")  # pas de subagent_type connu : celui par défaut du CLI
_COMMANDES = ("commande", "local_bash")
ABANDON_PREFIX = "[Relève abandonnée, la session continue.] "
HELD_NOTE = "[l'utilisateur a écrit pendant la relève : sa demande prime, ne relance que la liste] "
HELD_NOTICE = "Message gardé : il part dès la relève de session finie."
# Sources gardées pendant une relève (l'utilisateur, comme `core._UTILISATEUR`, les réflexes, et le relais
# de l'équipe d'agents, plan P13 : un codeur fini de lui-même pendant la relève ne la dérange pas).
UTILISATEUR = ("voix", "clavier", "whatsapp")
HELD_SOURCES = (*UTILISATEUR, "réflexe", "equipe", "quota")
# Caractères d'une consigne recopiée dans l'amorce (27/10 : 8 000 coupait les longues consignes de
# planificateur ; une consigne coupée n'est plus la consigne d'origine).
_CONSIGNE_MAX = 20_000
# Lecture-réécriture du fichier de reprise : boucle principale (`_write_list`) et fil du cerveau (`task_ended`).
_LIST_LOCK = threading.Lock()
_courante: "Relief | None" = None  # la relève du cœur en marche (une par process)
AMORCE = (
    "[Nouvelle session : relève automatique, l'ancien contexte était trop gros. Avant de répondre, relis "
    "la note CHANTIERS-EN-COURS (memory_read) pour reprendre le fil. C'est un résumé figé d'une session "
    "précédente : toute commande ou tâche citée est périmée tant que l'utilisateur ne la redemande pas ; ne "
    "retente pas ce qu'elle note en échec, ne repropose pas ce qui est abandonné. {extra}Pour un détail "
    "d'une conversation passée, utilise history_search plutôt que de tout relire.] "
)
# La note relue à chaque session neuve (coffre de la mémoire, `tools/memory.py`).
NOTE_FILE = config.ROOT / "vault" / "CHANTIERS-EN-COURS.md"
# Dossiers jamais parcourus pour trouver les dépôts git du dossier de travail.
_SKIP_DIRS = {"archives", "node_modules", "reference", "runtime", "logs"}


# Raisons d'attendre (au plus RELIEF_CALM_WAIT_S) après une sauvegarde réussie, au lieu d'abandonner :
# de la parole captée, une transcription, Astrel qui parle, des annonces en attente.
_PATIENCE = ("L'utilisateur a parlé il y a", "écoute en cours", "transcription en cours", "Astrel parle",
             "messages en attente", "L'utilisateur vient de parler")


class Relief:
    def __init__(self, core: Any) -> None:
        self.core = core
        self.phase = ""            # « » au repos, « stop » (arrêt des agents), « save » (tour de sauvegarde)
        self.started = 0.0
        self.retry_after = 0.0
        self.saved_turn_seen = False
        self.save_done = False     # le tour de sauvegarde a fini sans erreur (posé par le cœur)
        self.calm_since = 0.0      # sauvegarde finie, on attend que le bruit ou la parole se calme
        # Plan P9 : messages gardés pendant la relève, agents arrêtés (tid → entrée de la liste),
        # nombre de relances par consigne (plafond RELIEF_MAX_RELAUNCH, en mémoire).
        self.held: list[dict] = []
        self.stopping: dict[str, dict] = {}
        self.relaunched: dict[str, int] = {}
        self._stop_sent: set[str] = set()
        self._lock = threading.Lock()  # `held` et `phase` : `handle` tourne dans d'autres fils
        # 27/10 : agents de la liste envoyée au cerveau, pas encore vus relancés (clé → entrée), et
        # l'heure de l'envoi ; un seul rappel, à la fin du tour qui devait les relancer.
        self.attendus: dict[str, dict] = {}
        self.attendus_at = 0.0
        self.rappel_fait = False
        global _courante
        _courante = self  # `take_amorce` (fonction du module, appelée par `Astrel.handle`) le prévient

    # --- conditions ---

    def busy(self, idle_needed: bool = True, soft: bool = True) -> str:
        """Pourquoi ce n'est pas le moment (vide : Astrel est au repos).
        `soft` faux (relève immédiate, plan P9) : tâches de fond, file d'annonces et
        `RELIEF_IDLE_S` ne retiennent plus ; le reste (arrêt d'urgence, tour, confirmation,
        parole, écoute, voix) si."""
        j = self.core
        if config.ESTOP_FILE.exists():
            return "arrêt d'urgence"
        if j._turn_active:
            return "tour en cours"
        if soft and j._tasks:
            return "tâche de fond en cours"
        if j._pending_confirm is not None:
            return "confirmation en cours"
        reason = j._floor_taken()
        if reason:
            return reason
        if j._recording:
            return "écoute en cours"
        if j.speaker.busy:
            return "Astrel parle"
        if soft and backlog:
            return "messages en attente"
        if soft and idle_needed and time.time() - j._last_turn < config.RELIEF_IDLE_S:
            return "échange récent"
        return ""

    def due(self) -> bool:
        limit = reglages.value("context_relief_tokens")  # page « Réglages » du HUD, à chaud
        return limit > 0 and self.core.brain.context_tokens >= limit

    def absorbing(self) -> bool:
        """Relève immédiate en cours : un tour relancé seul par le CLI (fin d'un agent
        arrêté) est un tour de relève, jamais dit ni compte rendu « à lire »."""
        return config.RELIEF_IMMEDIATE and self.phase in ("stop", "save")

    # --- messages gardés (plan P9) ---

    def hold(self, item: dict) -> bool:
        """Garde un message (`handle` : text, source, channel, shown, joined, mid) jusqu'à
        la fin de la relève. Faux si aucune relève immédiate n'est en cours."""
        with self._lock:
            if not self.absorbing():
                return False
            self.held.append(item)
        log.info("relève : message %s gardé jusqu'à la fin de la relève", item.get("source"))
        return True

    def _close(self) -> list[dict]:
        """Fin de phase (sous verrou) : plus rien n'est gardé ; rend ce qui l'était."""
        with self._lock:
            self.phase = ""
            held, self.held = self.held, []
        self.calm_since = 0.0
        self._stop_sent = set()
        return held

    # --- cycle ---

    def tick(self) -> str:
        """Appelé souvent par la boucle principale. Rend l'étape franchie (tests, journal)."""
        now = time.time()
        immediate = config.RELIEF_IMMEDIATE
        if not self.phase and self.attendus:
            done = self._verifier_relances()
            if done:
                return done
        if not self.phase:
            if now < self.retry_after or not self.due() or self.busy(soft=not immediate):
                return ""
            if quota_arret.ordre_actif(now):
                # Plan P34 : pas de relève pendant la note ou l'arrêt pour quota (elle relancerait
                # les agents dans la session neuve, à 98 % de la fenêtre).
                return ""
            if immediate and self.core._tasks:
                return self._start_stop(now)
            return self._start_save(now)
        if self.phase == "stop":
            return self._stop_step(now)
        if self.phase == "save":
            j = self.core
            if j._turn_active:
                self.saved_turn_seen = True
                if j._turn_source != "relève":
                    return self._abort("L'utilisateur a parlé pendant la sauvegarde")
                if now - self.started > config.RELIEF_SAVE_TIMEOUT_S:
                    return self._abort("sauvegarde trop longue")
                return ""
            if j._last_user_at > self.started:
                return self._abort("L'utilisateur a parlé pendant la sauvegarde")
            if not self.saved_turn_seen and now - self.started < config.RELIEF_SAVE_TIMEOUT_S:
                return ""  # le tour n'a pas encore commencé
            if not self.save_done:
                return self._abort("la sauvegarde n'a pas abouti")
            reason = self.busy(idle_needed=False, soft=not immediate)
            if reason:
                # 19/10 : un bruit capté par le micro 2 s avant la fin de la sauvegarde annulait
                # toute la relève (nouvel essai 15 min plus tard, et une sauvegarde de plus à
                # 270 k tokens). Un simple bruit de parole ou de voix d'Astrel s'attend un peu.
                if reason.startswith(_PATIENCE):
                    self.calm_since = self.calm_since or now
                    if now - self.calm_since < config.RELIEF_CALM_WAIT_S:
                        return ""
                return self._abort(reason)
            return self._renew()
        return ""

    def _start_stop(self, now: float) -> str:
        """Phase « stop » (plan P9) : les agents et tâches de fond sont arrêtés et notés."""
        k = self.core.brain.context_tokens // 1000
        n = len(self.core._tasks)
        log.info("relève immédiate : contexte de %d k tokens, %d tâche(s) de fond à arrêter", k, n)
        with self._lock:
            self.phase = "stop"
        self.started, self.calm_since = now, 0.0
        self.stopping, self._stop_sent = {}, set()
        bus.publish("notice", level="info",
                    text=f"Contexte de {k} k tokens : j'arrête {n} tâche(s) en cours, je mets à jour la note des "
                         "chantiers, puis je repars à neuf et je les relance.")
        return "stop"

    def _stop_step(self, now: float) -> str:
        j = self.core
        if config.ESTOP_FILE.exists():
            return self._abort("arrêt d'urgence")
        if j._pending_confirm is not None:
            return self._abort("confirmation en cours")
        with j._tasks_lock:
            tasks = {tid: dict(t) for tid, t in j._tasks.items()}
        to_stop = []
        for tid, task in tasks.items():
            if tid in self._stop_sent:
                continue
            if j._agent_writing(task.get("tool_use_id", "")) and now - self.started < config.RELIEF_STOP_WAIT_S:
                continue  # écriture de fichier en cours : on la laisse finir (plafonné)
            self.stopping[tid] = j._task_journal(tid) or _entry_from_task(task)
            self._stop_sent.add(tid)
            to_stop.append(tid)
        if to_stop:
            self._stop(to_stop)
        late = now - self.started >= config.RELIEF_STOP_MAX_S
        if (not tasks or late) and not j._turn_active:
            if tasks:
                log.warning("relève : %d tâche(s) toujours là après %.0f s ; la session neuve les coupera",
                            len(tasks), config.RELIEF_STOP_MAX_S)
            self._write_list()
            return self._start_save(now)
        if now - self.started > config.RELIEF_STOP_MAX_S + config.RELIEF_SAVE_TIMEOUT_S:
            return self._abort("arrêt des agents trop long")
        return ""

    def _stop(self, tids: list[str]) -> None:
        """`stop_task` attend jusqu'à 5 s la réponse du CLI : hors de la boucle principale."""
        def run() -> None:
            for tid in tids:
                ok = self.core.brain.stop_task(tid)
                log.info("relève : arrêt de la tâche %s %s", tid, "demandé" if ok else "refusé par le CLI")

        threading.Thread(target=run, name="relief-stop", daemon=True).start()

    def task_ended(self, tid: str, status: str) -> bool:
        """Fin d'une tâche vue par le cœur : rend vrai si la relève l'avait arrêtée. Une
        tâche finie d'elle-même (« completed ») n'est pas à relancer : retirée de la liste."""
        entry = self.stopping.get(tid)
        if entry is None:
            return False
        if status == "completed":
            self.stopping.pop(tid, None)
            with _LIST_LOCK:  # lecture puis réécriture : `_write_list` tourne dans un autre fil
                listed = load_list()
                if any(e.get("tid") == tid for e in listed):
                    save_list([e for e in listed if e.get("tid") != tid])
            log.info("relève : la tâche %s a fini d'elle-même, pas de relance", tid)
            return False
        return True

    def _start_save(self, now: float) -> str:
        k = self.core.brain.context_tokens // 1000
        log.info("relève : contexte de %d k tokens, au repos ; sauvegarde des chantiers", k)
        with self._lock:
            self.phase = "save"
        self.started, self.saved_turn_seen, self.save_done = now, False, False
        self.calm_since = 0.0
        stopped = len(load_list())
        if not stopped:
            bus.publish("notice", level="info",
                        text=f"Contexte de {k} k tokens : je mets à jour la note des chantiers, puis je repars à neuf.")
        size = note_bytes()
        self.core.handle(SAVE_PROMPT.format(k=k, size=_ko(size) if size is not None else "?",
                                            cap=_ko(config.RELIEF_NOTE_MAX_BYTES),
                                            agents=SAVE_AGENTS.format(n=stopped) if stopped else ""),
                         source="relève")
        if not self.core._turn_active:
            # Envoi impossible (cerveau mort) : `handle` a déjà fini le tour.
            return self._abort("envoi impossible")
        self.saved_turn_seen = True
        return "save"

    def _abort(self, why: str) -> str:
        log.info("relève abandonnée : %s ; nouvel essai dans %.0f min", why, config.RELIEF_RETRY_S / 60)
        estop = why == "arrêt d'urgence"
        if self.stopping and not estop:
            self._write_list()
        held = self._close()
        self.retry_after = time.time() + config.RELIEF_RETRY_S
        if estop:
            clear_list()
            self.stopping = {}
            # Seuls les messages de l'utilisateur partent sous ESTOP, jamais un réflexe gardé.
            self._replay([h for h in held if h.get("source") in UTILISATEUR], "", None)
        else:
            self._release(held, renewed=False)
        return "abort"

    def estop(self) -> None:
        """« Stop tout » pendant une relève : abandon, la liste est jetée (rien n'est relancé) ;
        les messages gardés de l'utilisateur partent quand même (il peut toujours parler à Astrel)."""
        self.attendus = {}  # 27/10 : plus de rappel de relance sous ESTOP
        if not self.phase and not self.held:
            clear_list()
            return
        self._abort("arrêt d'urgence")

    def _renew(self) -> str:
        k = self.core.brain.context_tokens // 1000
        # Tâche encore là (lancée pendant la relève, ou qui ne s'arrêtait pas) : la session
        # neuve va la couper, elle est donc notée aussi.
        with self.core._tasks_lock:
            left = {tid: dict(t) for tid, t in self.core._tasks.items() if tid not in self.stopping}
        for tid, task in left.items():
            self.stopping[tid] = self.core._task_journal(tid) or _entry_from_task(task)
        if left:
            self._write_list()
        held = self._close()
        self.retry_after = time.time() + config.RELIEF_RETRY_S
        agents = load_list()
        log.info("relève : nouvelle session (l'ancienne faisait %d k tokens), %d agent(s) à relancer, "
                 "%d message(s) gardé(s)", k, len(agents), len(held))
        more = f" Je relance les {len(agents)} tâche(s) interrompue(s)." if agents else ""
        self.core.reset_session(notice=f"Relève faite : l'ancien contexte faisait {k} k tokens. Je repars à neuf, "
                                       f"le fil est dans la note des chantiers.{more}")
        set_amorce(True)
        self._release(held, renewed=True)
        return "renew"

    def _release(self, held: list[dict], renewed: bool) -> None:
        """Après la relève (`renewed`) ou son abandon : la liste des agents interrompus part
        TOUT DE SUITE (jointe au premier message gardé de l'utilisateur s'il y en a un), puis les
        autres messages gardés, dans l'ordre. Session neuve sans liste ni message : l'amorce
        attend le message suivant (comportement d'avant)."""
        agents = load_list()
        first = next((i for i, h in enumerate(held) if h.get("source") in UTILISATEUR), None)
        prefix = HELD_NOTE if agents else ""
        if agents:
            self._count_relaunch(agents)
            self.attendre(agents)  # avant l'envoi : le flux du cerveau tourne dans un autre fil
        if agents and not renewed:
            block = ABANDON_PREFIX + list_block(agents)
            clear_list()
            if first is None:
                self.core.handle(block + REPRISE_GO, source="relève", shown=_shown(len(agents)))
            else:
                prefix = block + prefix
        elif agents and first is None:
            self.core.handle(take_amorce() + REPRISE_GO, source="relève", shown=_shown(len(agents)))
        self.stopping = {}
        self._replay(held, prefix, first)

    # --- relance vérifiée (27/10) ---

    def attendre(self, agents: list[dict]) -> None:
        """La liste vient de partir au cerveau : chaque AGENT (pas les commandes, qui peuvent ne plus
        servir) doit être vu relancé (`vu_relance`) avant la fin du tour ; sinon UN rappel."""
        self.attendus = {_key(a): a for a in agents if _is_agent(a) and str(a.get("consigne") or "").strip()}
        self.attendus_at = time.time()
        self.rappel_fait = False

    def vu_relance(self, prompt: str) -> None:
        """Appel Agent du cerveau principal (`Astrel._note_agent_tool`) : relance d'un agent attendu ?"""
        if not self.attendus:
            return
        origine, _avant = decouper(prompt)
        flat = " ".join(str(prompt or "").split())
        for key in list(self.attendus):
            if (origine and _same(" ".join(origine.split()), key)) or (key and key in flat):
                entry = self.attendus.pop(key, None) or {}
                log.info("relève : agent relancé (« %s »)", entry.get("desc") or key[:80])

    def _verifier_relances(self) -> str:
        """Tour de relance fini (aucun tour en cours, un tour clos depuis l'envoi) : les agents
        attendus qui manquent sont redemandés UNE fois ; jamais sous arrêt d'urgence."""
        j = self.core
        if config.ESTOP_FILE.exists():
            self.attendus = {}
            return ""
        if j._turn_active or float(getattr(j, "_last_turn", 0.0) or 0.0) <= self.attendus_at:
            return ""
        missing, self.attendus = list(self.attendus.values()), {}
        if self.rappel_fait:
            n = len(missing)
            log.warning("relève : %d agent(s) toujours pas relancé(s) après le rappel", n)
            bus.publish("notice", level="warning",
                        text=f"Relève : {n} agent(s) interrompu(s) non relancé(s), à toi de voir.")
            return "rappel"
        log.warning("relève : %d agent(s) de la liste non relancé(s), rappel au cerveau", len(missing))
        self.attendre(missing)  # avant l'envoi : le flux du cerveau tourne dans un autre fil
        self.rappel_fait = True
        self.core.handle(REPRISE_RAPPEL + list_block(missing) + REPRISE_GO, source="relève",
                         shown=_shown(len(missing)))
        return "rappel"

    def _replay(self, held: list[dict], prefix: str, first: int | None = None) -> None:
        for i, h in enumerate(held):
            self.core.handle(h["text"], source=h["source"], channel=h.get("channel", ""), shown=h.get("shown"),
                             joined=h.get("joined"), held_mid=h.get("mid", ""),
                             prefix=prefix if i == first else "")

    def _write_list(self) -> None:
        """Écrit `RELIEF_REPRISE_FILE` : liste encore non lue fusionnée, doublons retirés,
        et au plus RELIEF_MAX_RELAUNCH relances d'une même consigne."""
        with _LIST_LOCK:
            self._write_list_locked()

    def _write_list_locked(self) -> None:
        # Doublons cherchés dans la liste DÉJÀ écrite seulement (relève précédente non lue) :
        # deux agents du même arrêt sont toujours deux entrées, même à préambule commun.
        merged = load_list()
        before = [(e.get("tid"), _key(e)) for e in merged]
        for entry in _rattacher(list(self.stopping.values())):  # `task_ended` (fil du cerveau) peut en retirer
            key = _key(entry)
            if any(entry.get("tid") == tid or _same(key, k) for tid, k in before):
                continue
            if self._relaunch_count(key) >= config.RELIEF_MAX_RELAUNCH:
                log.warning("relève : « %s » déjà relancé %d fois, pas de nouvelle relance", entry.get("desc"),
                            config.RELIEF_MAX_RELAUNCH)
                bus.publish("notice", level="warning", channel=entry.get("canal", ""),
                            text=f"« {entry.get('desc') or 'tâche'} » interrompu {config.RELIEF_MAX_RELAUNCH} fois "
                                 "par la relève : je ne le relance plus, à toi de voir.")
                continue
            merged.append(entry)
        save_list(merged)

    def _relaunch_count(self, key: str) -> int:
        return max((n for k, n in self.relaunched.items() if _same(key, k)), default=0)

    def _count_relaunch(self, agents: list[dict]) -> None:
        for entry in agents:
            key = _key(entry)
            old = next((k for k in self.relaunched if _same(key, k)), key)
            self.relaunched[old] = self.relaunched.get(old, 0) + 1


# --- liste des agents interrompus (plan P9) ---

def _entry_from_task(task: dict) -> dict:
    """Entrée minimale quand le cœur n'a pas de journal pour la tâche."""
    return {"tid": task.get("id", ""), "type": task.get("kind") or "tâche", "desc": task.get("desc", ""),
            "consigne": "", "canal": task.get("channel", ""), "fichiers": [], "etape": task.get("step", ""),
            "outil_en_cours": "", "note": "", "tuid": task.get("tool_use_id", "")}


def _key(entry: dict) -> str:
    """Consigne ENTIÈRE, blancs normalisés : un préambule commun ne confond pas deux agents.
    Un agent déjà relancé par la relève porte la consigne de reprise : sa clé est la consigne
    d'ORIGINE qu'elle contient (`origine`), sinon le plafond de relances ne le reconnaîtrait pas."""
    return " ".join(str(entry.get("origine") or entry.get("consigne") or entry.get("desc") or "").split())


def _is_agent(entry: dict) -> bool:
    """Un sous-agent (tout métier), par opposition à une commande de fond (Bash)."""
    return str(entry.get("type") or "") not in _COMMANDES


def _rattacher(entries: list[dict]) -> list[dict]:
    """27/10 : une commande de fond lancée par un SOUS-AGENT arrêté avec lui n'est pas relancée à part
    (le 27/10 : trois « local_bash » sans consigne dans la liste, un banc jamais relancé) : elle est
    jointe à son agent (« commandes de fond coupées »). Seule, son agent fini, elle reste une entrée.
    Copies : la liste peut être écrite plusieurs fois pendant une relève."""
    out = [dict(e) for e in entries]
    by_tuid = {str(e.get("tuid") or ""): e for e in out if e.get("tuid")}
    kept = []
    for e in out:
        parent = by_tuid.get(str(e.get("parent") or ""))
        if parent is not None and parent is not e:
            cmd = str(e.get("consigne") or e.get("desc") or "").strip()
            if cmd:
                parent["commandes"] = [*(parent.get("commandes") or []), cmd]
            continue
        kept.append(e)
    return kept


def decouper(prompt: str) -> tuple[str, str]:
    """Consigne de reprise écrite par `relance_prompt` → (consigne d'origine, « déjà fait » noté alors) ;
    (« », « ») pour une consigne ordinaire."""
    text = str(prompt or "")
    start = text.find(ORIGINE_DEBUT + "\n")
    end = text.rfind("\n" + ORIGINE_FIN)
    if start < 0 or end <= start:
        return "", ""
    origine = text[start + len(ORIGINE_DEBUT) + 1:end]
    head = text[:start]
    avant = " ; ".join(ln[2:].strip() for ln in head.splitlines() if ln.startswith("- "))
    return origine, avant


def relance_prompt(entry: dict) -> str:
    """Consigne de reprise d'un agent, écrite par le cœur : en-tête, ce qui est déjà fait (relevé par
    le cœur, sans la coopération de l'agent), l'ordre de vérifier l'état laissé, puis la consigne
    d'ORIGINE inchangée entre deux lignes repères."""
    consigne = str(entry.get("origine") or entry.get("consigne") or "")
    if len(consigne) > _CONSIGNE_MAX:
        consigne = consigne[:_CONSIGNE_MAX] + " […]"
    faits = []
    for label, value in (("dernière étape", entry.get("etape")),
                         ("fichiers écrits", ", ".join(entry.get("fichiers") or [])),
                         ("coupé pendant", entry.get("outil_en_cours")),
                         ("tes commandes de fond coupées avec toi (relance celles qui servent encore)",
                          " | ".join(entry.get("commandes") or []))):
        if value:
            faits.append(f"- {label} : {value}")
    faits += [f"- {d}" for d in entry.get("deja") or [] if d]
    if entry.get("note"):
        faits.append(f"- note : {entry['note']}")
    if entry.get("avant"):
        faits.append(f"- avant la relance précédente : {str(entry['avant'])[:2000]}")
    if not faits:
        faits.append("- rien de noté par le cœur (agent en lecture seule ou arrêté tôt)")
    return "\n".join([REPRISE_TETE, "Déjà fait avant l'arrêt (relevé par le cœur d'Astrel) :", *faits,
                      REPRISE_VERIFIER, ORIGINE_DEBUT, consigne, ORIGINE_FIN])


def deja_fait(book: dict) -> list[str]:
    """27/10 : ce qu'un agent a DÉPOSÉ par les outils d'Astrel (invisibles comme fichiers : plan,
    passation, fil d'un plan, courrier) et sa liste d'étapes TodoWrite, d'après son journal tenu par
    le cœur (`Astrel._agent_log`, champ `depots` : outil, entrée utile, `ok` None = coupé en cours).
    Numéro du plan et de la fiche relus sur le disque (`plans`, `equipe`)."""
    out: list[str] = []
    todos = [t for t in book.get("todos") or [] if isinstance(t, dict)]
    if todos:
        parts = []
        for status, label in (("completed", "faites"), ("in_progress", "en cours"), ("pending", "à faire")):
            items = [str(t.get("text") or "") for t in todos if t.get("status") == status]
            if items:
                parts.append(f"{label} : " + ", ".join(items))
        out.append("liste d'étapes de l'agent : " + " ; ".join(parts))
    started = float(book.get("started") or 0.0) - 5.0
    for d in book.get("depots") or []:
        if not isinstance(d, dict) or d.get("ok") is False:
            continue  # dépôt refusé : rien n'a été écrit
        try:
            text = _depot(d, started)
        except Exception:  # la relève ne tombe jamais pour un plan ou une fiche illisible
            log.exception("relève : dépôt %s non relu", d.get("outil"))
            text = ""
        if text:
            out.append(text)
    return out


def _depot(d: dict, since: float) -> str:
    kind, cut = d.get("outil"), d.get("ok") is None
    if kind == "plan":
        from .plans import plans

        titre = " ".join(str(d.get("titre") or "").split())[:160]
        pid = str(d.get("plan_id") or "").strip().upper()
        found = None
        for p in plans.describe().get("items", []):
            if (pid and p.get("id") == pid) or (not pid and p.get("title") == titre
                                                  and float(p.get("created") or 0) >= since):
                found = p
                break  # `describe` : du plus récent au plus ancien
        if found is not None:
            return (f"plan {found['id']} « {found.get('title')} » déjà déposé (version {found.get('version')}, "
                    f"statut {found.get('status')}) : ne le redépose pas")
        # Coupé en plein dépôt, ou refusé par le cœur (son refus n'est pas une erreur d'outil).
        return (f"dépôt du plan « {titre} » {'coupé en cours' if cut else 'tenté, numéro non retrouvé'} : "
                "vérifie avec plan_list s'il existe avant de le déposer")
    if kind == "passation":
        from . import equipe

        mission = equipe.slug(d.get("mission"))
        de = str(d.get("de") or "").strip().lower()
        fiches = [f for f in equipe.read(mission) if str(f.get("de") or "").strip().lower() == de
                  and float(f.get("at") or 0) >= since]
        if fiches:
            f = fiches[-1]
            return (f"passation déjà déposée : fiche {f['n']} (mission {mission}, de {f.get('de')} à {f.get('a')}) : "
                    "ne la refais pas, complète-la seulement si le travail a avancé")
        return (f"passation {'coupée en cours' if cut else 'tentée, fiche non retrouvée'} (mission {mission}) : "
                "vérifie le journal d'équipe avant de la déposer")
    if kind == "fil" and not cut:
        return f"réponse déjà posée dans le fil du plan {d.get('plan_id')}"
    if kind == "courrier" and not cut:
        return f"courrier déjà envoyé à {d.get('a')} (mission {d.get('mission')})"
    return ""


def _same(a: str, b: str) -> bool:
    """Même consigne : l'une commence par l'autre (un agent relancé reçoit sa consigne
    d'origine, souvent suivie de ce qui est déjà fait)."""
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def load_list() -> list[dict]:
    try:
        data = json.loads(config.RELIEF_REPRISE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    agents = data.get("agents") if isinstance(data, dict) else None
    return [a for a in agents if isinstance(a, dict)] if isinstance(agents, list) else []


def save_list(agents: list[dict]) -> None:
    """Écriture atomique (fichier voisin puis `os.replace`) : la liste fait foi après une coupure."""
    path = config.RELIEF_REPRISE_FILE
    if not agents:
        clear_list()
        return
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"created": time.time(), "agents": agents}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("liste des agents interrompus non écrite : %s", exc)


def clear_list() -> None:
    try:
        config.RELIEF_REPRISE_FILE.unlink(missing_ok=True)
    except OSError:
        log.warning("liste des agents interrompus non effacée : %s", config.RELIEF_REPRISE_FILE)


def list_block(agents: list[dict]) -> str:
    """La liste pour le cerveau. Un AGENT (tout métier) : les paramètres de l'outil Agent et sa
    consigne de reprise complète (`relance_prompt`) ; une COMMANDE de fond : la commande, ce qu'on sait
    d'elle et l'ordre de vérifier d'abord."""
    lines = []
    for n, a in enumerate(_rattacher(agents), 1):
        kind = str(a.get("type") or "tâche")
        desc = str(a.get("desc") or "tâche")
        head = f"{n}. « {desc} » ({kind}" + (f", canal {a['canal']}" if a.get("canal") else "") + ")"
        consigne = str(a.get("origine") or a.get("consigne") or "").strip()
        if not _is_agent(a):
            parts = [head]
            for label, value in (("commande", consigne), ("dernière étape", a.get("etape")),
                                 ("coupé pendant", a.get("outil_en_cours")), ("note", a.get("note"))):
                if value:
                    parts.append(f"{label} : {value}")
            if not consigne:
                parts.append("commande inconnue du cœur : ne la relance pas, note-la dans CHANTIERS-EN-COURS")
            lines.append(" ; ".join(parts))
            continue
        if not consigne:
            lines.append(f"{head} ; consigne inconnue du cœur : ne le relance pas à l'aveugle, dis-le à l'utilisateur "
                         "(post_report) avec sa dernière étape : " + str(a.get("etape") or "inconnue"))
            continue
        sous_type = "celui par défaut (n'en mets pas)" if kind in _AGENT_KINDS_SANS_TYPE else kind
        relance = desc if desc.startswith("Reprise : ") else f"Reprise : {desc}"
        model = f", model {a['modele']}" if a.get("modele") else ""
        lines.append(f"{head} : outil Agent, subagent_type {sous_type}, description « {relance} »{model}, "
                     f"run_in_background vrai, prompt :\n<<<REPRISE\n{relance_prompt(a)}\nREPRISE>>>")
    return REPRISE_BLOCK.format(items="\n".join(lines))


def _shown(n: int) -> str:
    return f"Reprise après la relève : {n} tâche(s) interrompue(s) à relancer."


# --- message d'amorçage, joint au premier message de la session neuve ---

def set_amorce(on: bool) -> None:
    try:
        if on:
            config.RELIEF_AMORCE_FILE.write_text("1", encoding="utf-8")
        else:
            config.RELIEF_AMORCE_FILE.unlink(missing_ok=True)
    except OSError:
        log.warning("amorce de relève non retenue : %s", config.RELIEF_AMORCE_FILE)


def take_amorce() -> str:
    """Le texte d'amorçage s'il est dû (une seule fois), sinon « ». Deux blocs distincts
    (plan P9) : le résumé figé, puis la liste des agents interrompus s'il y en a (effacée)."""
    if not config.RELIEF_AMORCE_FILE.exists():
        return ""
    set_amorce(False)
    agents = load_list()
    clear_list()
    if agents and config.ESTOP_FILE.exists():
        # 27/10 : arrêt d'urgence posé entre la relève (ou le démarrage) et ce message : rien n'est relancé.
        log.info("relève : arrêt d'urgence, %d tâche(s) interrompue(s) non relancée(s)", len(agents))
        agents = []
    if agents and isinstance(_courante, Relief):
        _courante.attendre(agents)  # relance vérifiée à la fin du tour (un rappel au plus)
    return amorce_text() + todo_block() + (list_block(agents) if agents else "")


def todo_block() -> str:
    """Plan P38 lot 3 : instantané de la todo par projet joint à l'amorce (« » si elle est vide ou
    les automatismes coupés) ; la todo, pas la note des chantiers, fait foi pour les tâches."""
    try:
        from .todo import todo

        todo.demander_point(None)  # 28/10 (carte T31) : point automatique au début de la session neuve
        return todo.instantane()
    except Exception:  # la relève ne tombe jamais pour la todo
        log.exception("instantané de la todo non joint à l'amorce")
        return ""


def amorce_text() -> str:
    """L'amorce, avec ce que Python sait mieux que la note : les commits faits
    depuis sa dernière mise à jour, et sa taille si elle déborde."""
    extra = ""
    size = note_bytes()
    try:
        since = NOTE_FILE.stat().st_mtime
    except OSError:
        since = None
    if since is not None:
        counts = commits_since(since)
        if counts is not None:
            n = sum(counts.values())
            detail = ", ".join(f"{name} {c}" for name, c in counts.items() if c)
            extra += (f"{n} commit{'s' if n > 1 else ''} depuis la dernière mise à jour de la note"
                      + (f" ({detail})" if detail else "") + ". ")
    if size is not None and size > config.RELIEF_NOTE_MAX_BYTES:
        extra += (f"La note fait {_ko(size)} Ko, au-delà de {_ko(config.RELIEF_NOTE_MAX_BYTES)} : "
                  "resserre-la à ta prochaine mise à jour. ")
    return AMORCE.format(extra=extra)


# --- démarrage d'Astrel : reprendre la conversation, ou session neuve si elle est trop grosse ---

def boot_decision(sid: str, context: int | None, local: bool = False) -> bool:
    """Au démarrage (19/10, demande de l'utilisateur) : vrai s'il faut repartir d'une session
    neuve au lieu de reprendre `sid` (`--resume`), parce que son contexte `context` atteint
    `BOOT_FRESH_TOKENS`. Le cœur pose alors l'amorce de la relève (la liste des agents
    interrompus, s'il y en a, lui est jointe par `take_amorce` ; jetée ici sous arrêt d'urgence). Anti-boucle :
    au plus une session neuve de démarrage par `BOOT_FRESH_COOLDOWN_S`. Journalise UNE ligne."""
    limit = int(config.BOOT_FRESH_TOKENS)  # ASTREL_BOOT_FRESH_TOKENS (page « Réglages » : hud/src protégé)
    now = time.time()
    fresh, why = False, ""
    if local:
        why = "mode léger, la session de Claude reste pour le retour"
    elif not sid:
        why = "aucune session à reprendre"
    elif context is None:
        why = "contexte inconnu"
    elif limit <= 0:
        why = "seuil désactivé"
    elif context >= limit:
        last = _boot_fresh_at()
        if 0 <= now - last < config.BOOT_FRESH_COOLDOWN_S:
            why = f"anti-boucle : session neuve déjà faite au démarrage il y a {int((now - last) // 60)} min"
        elif not _boot_fresh_mark(now, sid, context):
            why = "anti-boucle : marque de démarrage non écrite, reprise par prudence"
        else:
            fresh = True
    if fresh:
        agents = load_list()
        if agents and config.ESTOP_FILE.exists():
            clear_list()
            why = f"arrêt d'urgence : {len(agents)} tâche(s) interrompue(s) non relancée(s)"
        elif agents:
            why = f"{len(agents)} tâche(s) interrompue(s) jointe(s) à l'amorce"
    decision = "session neuve" if fresh or local or not sid else "reprise"
    log.info("démarrage : contexte repris %d k, seuil %s, décision : %s%s", (context or 0) // 1000,
             f"{limit // 1000} k" if limit > 0 else "aucun", decision, f" ({why})" if why else "")
    return fresh


def _boot_fresh_at() -> float:
    try:
        data = json.loads(config.BOOT_FRESH_FILE.read_text(encoding="utf-8"))
        return float(data.get("at", 0)) if isinstance(data, dict) else 0.0
    except (OSError, ValueError, TypeError):
        return 0.0


def _boot_fresh_mark(now: float, sid: str, context: int) -> bool:
    try:
        config.BOOT_FRESH_FILE.write_text(json.dumps({"at": now, "from": sid, "context": context}),
                                          encoding="utf-8")
        return True
    except OSError:
        log.warning("session neuve de démarrage non notée : %s", config.BOOT_FRESH_FILE)
        return False


def note_bytes() -> int | None:
    try:
        return NOTE_FILE.stat().st_size
    except OSError:
        return None


def _ko(size: int) -> str:
    return f"{size / 1000:.1f}".rstrip("0").rstrip(".").replace(".", ",")


def git_repos(base: Path | None = None, depth: int = 3) -> list[Path]:
    """Dépôts git du dossier de travail (3 niveaux au plus, sans entrer dans un
    dépôt ni dans les dossiers cachés, d'archives ou de dépendances)."""
    found: list[Path] = []

    def walk(folder: Path, level: int) -> None:
        if (folder / ".git").exists():
            found.append(folder)
            return
        if level >= depth:
            return
        try:
            children = sorted(p for p in folder.iterdir() if p.is_dir())
        except OSError:
            return
        for child in children:
            if not child.name.startswith((".", "_")) and child.name not in _SKIP_DIRS:
                walk(child, level + 1)

    walk(base or config.WORKDIR, 0)
    return found


def commits_since(since: float, repos: list[Path] | None = None) -> dict[str, int] | None:
    """Commits des branches locales depuis `since` (epoch s), par dépôt ; None
    si aucun dépôt n'a pu être lu (git absent, aucun dépôt) : rien à dire."""
    stamp = datetime.fromtimestamp(since).isoformat(timespec="seconds")
    counts: dict[str, int] = {}
    for repo in repos if repos is not None else git_repos():
        try:
            out = subprocess.run(["git", "-C", str(repo), "rev-list", "--count", f"--since={stamp}", "--branches"],
                                 capture_output=True, text=True, timeout=2)  # sur le chemin d'un message
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0 and out.stdout.strip().isdigit():
            counts[repo.name] = int(out.stdout.strip())
    return counts or None
