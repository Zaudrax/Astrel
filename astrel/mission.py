"""Mission autonome BORNÉE (14/10, demande de l'utilisateur : « un vrai mode autonome »).

Avant, « autonome » ne voulait dire qu'une chose : le garde-fou ne demande
plus pour le non-critique. Une mission, c'est autre chose : Astrel enchaîne
les tours SEUL vers un objectif, dans un cadre fixé d'avance par l'utilisateur.

    objectif      ce qu'il faut obtenir, en une phrase
    fini quand    critère DÉCIDABLE (une commande qui réussit, un fichier qui
                  existe…) : sans lui, pas de mission (loop-design-check d'ECC :
                  « done-criterion is machine-verifiable »)
    périmètre     dossiers où la mission peut ÉCRIRE (15/10 : barrière dans le
                  garde-fou, `guard.mission_fence`) ; /tmp en plus
    interdits     en clair pour Claude, et des DOSSIERS interdits en écriture,
                  même dans le périmètre (garde-fou aussi, demandé même en Total)
    budget        tours, minutes, tokens de la session principale ; arrêt aussi
                  si la fenêtre de 5 h de l'abonnement dépasse MISSION_QUOTA_STOP
    essais        N tours de suite sans progrès (« BLOQUÉE » ou ligne finale
                  absente) → arrêt et escalade à l'utilisateur

Pas de juge à part (HERMES `goals.py` en appelle un, soit 1 à 1,5 k tokens par
tour) : chaque tour finit par une ligne « MISSION : CONTINUE | FINIE | BLOQUÉE »
(comme LOOP_COMPLETE de HERMES `loops.py`), lue ici sans modèle. La relance est
un message ordinaire, le prompt système n'est pas touché : le cache reste.
Un vrai message de l'utilisateur passe toujours avant la relance suivante.

⚠️ Lancée par l'utilisateur (HUD, `--send`) ; depuis le 29/10 (carte T25, décision du 28/10)
aussi par Astrel LUI-MÊME, par l'outil MCP `mission_start` (`Astrel._mission_auto`), sous des
conditions plus strictes : seulement dans un tour lancé par l'utilisateur, périmètre DANS ses projets,
budget au plus celui par défaut, `lancee_par` = « astrel » (bandeau). REPRISE par l'utilisateur SEUL :
`runtime/mission.json` est dans les fichiers protégés du garde-fou, et le cœur refuse
`mission start|resume` venu d'un process de Claude (`totalgate.loosens_guard`). Pause et arrêt restent
permis à tous (resserrer). Une mission active au démarrage repart EN PAUSE :
écrire le fichier ne lance donc rien. Le fichier est aussi lu par le HOOK
(process à part) : `status`, `in_turn`, `scope`, `forbidden_paths` y sont la
barrière du garde-fou pendant la mission. L'arrêt d'urgence ARRÊTE la mission.

02/11 (plan P43, décision de l'utilisateur) : « MISSION : FINIE » n'est qu'une DÉCLARATION. Elle fait
passer la mission (toujours « active », barrière comprise) en phase « verification » : le cœur
contrôle le critère TYPÉ (`preuve.py`, sans commande ni shell) et seul un résultat positif donne
« done ». Échec : retour au travail, 3 vérifications ratées au plus (`MISSION_MAX_VERIFS`), puis
« verification_failed ». Contrôle bloqué ou critère libre : phase « verification_blocked », aucun
tour, l'utilisateur décide (revérifier, remettre au travail, accepter SANS preuve = « accepted_unverified »,
arrêter). Chaque transition fait avancer `generation` : un résultat arrivé après une pause, un arrêt,
un redémarrage ou une autre tentative est jeté. Le critère vient de `spec["verification"]` ; son
ORIGINE est fixée par le cœur (bouton du HUD ou outil `mission_start`), jamais lue dans la spec.

04/11 (demande : une mission d'essai finie le 02/11 restait dans le panneau après relance) : une
mission TERMINÉE se FERME (× du HUD, commande `mission_close`, l'utilisateur seul) : champ `closed_at`
ajouté, rien d'effacé. Finie depuis plus de 24 h, elle est close à l'affichage sans rien écrire.
Une mission close est publiée comme absente (`vue_publiee`). Jamais pour une mission active ou en
pause : la barrière du hook ne lit pas ce champ.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from . import config
from . import preuve

log = logging.getLogger("astrel.mission")

ACTIVE, PAUSED, DONE, BLOCKED, STOPPED, BUDGET = "active", "paused", "done", "blocked", "stopped", "budget"
# 02/11 (plan P43) : « done » = critère VÉRIFIÉ par `preuve.py` ; l'utilisateur peut ACCEPTER sans preuve
# (critère libre, contrôle bloqué), ce qui n'est jamais présenté comme vérifié ; vérifié et faux = échec.
ACCEPTED, VERIF_FAILED = "accepted_unverified", "verification_failed"
ENDED = (DONE, BLOCKED, STOPPED, BUDGET, ACCEPTED, VERIF_FAILED)
# Phases d'une mission ACTIVE. Le statut reste « active » pendant la vérification : la barrière du
# garde-fou (`guard.mission_fence`, qui ne lit que le statut) tient, et aucun tour n'est relancé
# hors de la phase « travail » (`take_prompt`).
TRAVAIL, VERIFICATION, VERIF_BLOCKED = "travail", "verification", "verification_blocked"
SCHEMA = 2
# 04/11 (T50) : comment `tokens` est compté. « travail » : entrée hors cache + création de cache + sortie,
# sous-agents compris (consommation du `result`) ; « ancienne » (mission d'avant) : `usage` de la session
# principale, cache relu compris, gardé jusqu'à sa fin.
MESURE_TRAVAIL, MESURE_ANCIENNE = "travail", "ancienne"
# 04/11 : une mission finie depuis plus longtemps est close à l'affichage (aucune écriture).
FERMETURE_AUTO_S = 24 * 3600
# Lot C (03/11) : outils qui n'écrivent rien. Tout AUTRE outil (Bash, Write, Edit, MCP…) lancé après la
# commande observée la rend « périmée » : dans le doute, une écriture. Les outils d'un sous-agent
# arrivent dans le même flux et comptent aussi.
_SANS_ECRITURE = frozenset({"Read", "Grep", "Glob", "LS", "WebFetch", "WebSearch", "TodoWrite", "Agent", "Task",
                            "ToolSearch", "TaskOutput", "BashOutput", "TaskList", "TaskGet"})
MAX_OBSERVATIONS = 20

# Dernière ligne d'un tour de mission. Accents et casse indifférents.
_MARKER = re.compile(r"MISSION\s*:\s*(CONTINUE|FINIE?|TERMIN[ÉE]E?|BLOQU[ÉE]E?|DONE|BLOCKED)\b\s*[-—–:]*\s*(.*)",
                     re.IGNORECASE)


def _num(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _lines(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.splitlines()
    return [str(v).strip() for v in (value or []) if str(v).strip()]


def _abs_paths(value: Any, base: str | Path) -> list[str]:
    """Dossiers écrits par l'utilisateur (un par ligne, ou séparés par des virgules) →
    chemins ABSOLUS normalisés, tels que le garde-fou les comparera. Relatifs :
    depuis `base` (dossier du canal, sinon le dossier de travail)."""
    if isinstance(value, str):
        value = re.split(r"[\n,;]", value)
    out: list[str] = []
    for raw in _lines(value):
        p = Path(raw.strip("'\"")).expanduser()
        if not p.is_absolute():
            p = Path(base) / p
        s = str(p.resolve())
        if s not in out:
            out.append(s)
    return out


def travail_de(ajouts: Any) -> tuple[int, int] | None:
    """04/11 (T50) : (travail, cache relu) des ajouts du compteur de consommation (`Compteur.noter` :
    une ligne par modèle, sous-agents compris). Travail = entrée hors cache + création de cache +
    sortie ; le cache relu (tout le contexte, relu à chaque requête) est rendu à part. None : rien
    d'exploitable."""
    if not isinstance(ajouts, list) or not ajouts:
        return None
    travail = relu = 0
    for a in ajouts:
        if isinstance(a, dict):
            travail += sum(int(a.get(k) or 0) for k in ("entree", "cache_ecriture", "sortie"))
            relu += int(a.get("cache_lecture") or 0)
    return travail, relu


def travail_usage(usage: Any) -> tuple[int, int]:
    """Repli sans `modelUsage` : le `usage` du tour (session principale SEULE)."""
    if not isinstance(usage, dict):
        return 0, 0
    def n(k: str) -> int:
        v = usage.get(k)
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0
    return n("input_tokens") + n("cache_creation_input_tokens") + n("output_tokens"), n("cache_read_input_tokens")


def tokens_of(usage: Any) -> int:
    """Tokens traités par la session principale pendant un tour (entrée, cache
    lu et écrit, sortie). Les sous-agents n'y sont pas : leur coût se voit dans
    la fenêtre de 5 h, d'où l'arrêt sur le quota en plus.
    04/11 (T50) : seulement pour une mission d'avant (`mesure` « ancienne ») ; les nouvelles comptent
    le TRAVAIL, sous-agents compris (`Mission.compter`)."""
    if not isinstance(usage, dict):
        return 0
    total = 0
    for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"):
        v = usage.get(key)
        if isinstance(v, (int, float)):
            total += int(v)
    return total


def verdict(text: str) -> tuple[str, str]:
    """« continue », « finie » ou « bloquee » (+ raison), d'après la DERNIÈRE ligne
    « MISSION : … » du tour. Vide si Claude ne l'a pas écrite."""
    found = ("", "")
    for line in (text or "").splitlines():
        m = _MARKER.search(line)
        if m:
            word = m.group(1).upper().replace("É", "E")
            kind = "continue" if word == "CONTINUE" else "finie" if word.startswith(("FINI", "TERMIN", "DONE")) \
                else "bloquee"
            found = (kind, m.group(2).strip())
    return found


class Mission:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.data: dict[str, Any] = {}
        self.awaiting = False       # un tour de mission est parti, sa fin n'est pas encore lue
        self.next_prompt = ""       # relance prête, envoyée dès que tout est calme
        self.quota = 0.0            # fenêtre de 5 h, 0..1
        self._vue_close: bool | None = None  # 04/11 : dernière vue publiée close ? (None : jamais publiée)

    # --- état ---

    def load(self) -> None:
        """Au démarrage : une mission active repart EN PAUSE (l'utilisateur la relance). P43 : la
        génération avance (aucun résultat d'avant la coupure ne peut s'appliquer) et une
        vérification en cours est oubliée (refaite à la reprise)."""
        try:
            data = json.loads(config.MISSION_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict) or not data.get("objective"):
            data = {}
        if data:
            _completer(data)
            data["generation"] += 1
            data["verif_en_cours"] = None
        if data.get("status") == ACTIVE:
            data["status"] = PAUSED
            data["reason"] = "Astrel a redémarré : mission en pause, à relancer depuis le HUD."
        with self.lock:
            self.data = data
            self.awaiting, self.next_prompt = False, ""
        if data:
            self._save()

    @property
    def status(self) -> str:
        with self.lock:
            return str(self.data.get("status", ""))

    @property
    def phase(self) -> str:
        with self.lock:
            return str(self.data.get("phase") or TRAVAIL) if self.data else ""

    @property
    def generation(self) -> int:
        with self.lock:
            return int(self.data.get("generation") or 0)

    @property
    def active(self) -> bool:
        return self.status == ACTIVE

    def _bump(self) -> None:
        """Toute transition fait avancer la génération (sous le verrou) : un résultat de
        vérification lancé avant elle sera jeté."""
        self.data["generation"] = int(self.data.get("generation") or 0) + 1

    def _save(self) -> None:
        with self.lock:
            if self.data:
                # Lu par le hook : un tour de mission encore en cours (mission mise en
                # pause en plein tour) reste sous la barrière jusqu'à sa fin.
                self.data["in_turn"] = bool(self.awaiting)
            payload = json.dumps(self.data, ensure_ascii=False, indent=1)
            # Écrit SOUS le verrou, par un fichier temporaire propre à cet appel : une
            # sauvegarde plus ancienne ne peut pas finir après une plus récente.
            tmp = None
            try:
                fd, tmp = tempfile.mkstemp(prefix=".mission-", suffix=".tmp", dir=config.MISSION_FILE.parent)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(payload)
                os.replace(tmp, config.MISSION_FILE)
                tmp = None
            except OSError as exc:
                log.warning("mission non enregistrée : %s", exc)
            finally:
                if tmp:
                    with contextlib.suppress(OSError):
                        os.unlink(tmp)

    def describe(self) -> dict[str, Any]:
        with self.lock:
            d = json.loads(json.dumps(self.data)) if self.data else {}
        if not d:
            return {"status": ""}
        elapsed = (d.get("ended_at") or time.time()) - float(d.get("started_at") or time.time())
        d["elapsed_min"] = round(max(0.0, elapsed) / 60.0, 1)
        d["approuve"] = _approuve(d)
        d["closed"] = est_close(d)
        return d

    def vue_publiee(self) -> dict[str, Any]:
        """04/11 : ce que publie l'évènement `mission` (HUD, bulle). Une mission CLOSE (× du HUD, ou
        finie depuis plus de 24 h) est publiée comme absente : aucun écran ne la remontre."""
        with self.lock:
            d = self.describe()
            self._vue_close = bool(d.get("closed"))
        if d.get("closed"):
            return {"status": "", "closed": True, "id": str(d.get("id") or ""),
                    "closed_at": float(d.get("closed_at") or 0.0)}
        return d

    def fermeture_a_publier(self) -> bool:
        """04/11 : la mission vient de passer les 24 h après sa fin alors que la dernière vue publiée
        la montrait encore : à republier (le HUD la retire sans attendre une autre transition)."""
        with self.lock:
            return self._vue_close is False and est_close(self.data)

    # --- commandes de l'utilisateur ---

    def start(self, spec: dict[str, Any], origine: str = "utilisateur") -> dict[str, Any]:
        """`origine` est fixée par le CŒUR selon le chemin d'appel (bouton du HUD : « utilisateur » ;
        outil `mission_start` : « claude »), jamais lue dans `spec`."""
        origine = "claude" if origine == "claude" else "utilisateur"
        objective = str(spec.get("objective") or "").strip()
        done_when = str(spec.get("done_when") or "").strip()
        if not objective:
            return {"ok": False, "error": "objectif vide"}
        if self.active:
            return {"ok": False, "error": "une mission tourne déjà : arrête-la d'abord"}
        if origine == "claude" and self.status == PAUSED:
            # Revue P43 : Astrel ne REMPLACE pas une mission en pause (critère, historique) ; l'utilisateur
            # la reprend ou l'arrête d'abord.
            return {"ok": False, "error": "une mission est en pause : l'utilisateur la reprend ou l'arrête d'abord"}
        base = str(spec.get("base") or config.WORKDIR)
        scope = _abs_paths(spec.get("scope"), base)[:20]
        if not scope:
            return {"ok": False, "error": "il faut un périmètre : le ou les dossiers où la mission peut écrire"}
        # 03/11 (CI Windows) : une RACINE, quel que soit le système (« / », « C:\\ » : « / » y devient
        # la racine du lecteur courant), jamais le seul texte « / ».
        if any(Path(s).parent == Path(s) for s in scope):
            return {"ok": False, "error": "périmètre trop large : « / » ouvrirait tout le disque"}
        brut = spec.get("verification")
        critere = None
        lignes = str(spec.get("verification_lignes") or "").strip()  # formulaire du HUD
        if brut in (None, "", {}) and lignes:
            try:
                # 02/11 (essai réel) : relatif = depuis le PREMIER dossier du périmètre, pas le canal.
                brut = preuve.lire_lignes(lignes, scope[0])
            except preuve.CritereInvalide as exc:
                return {"ok": False, "error": f"critère de fin refusé : {exc}"}
        if brut not in (None, "", {}):
            try:
                critere = preuve.valider_critere(brut, scope)
            except preuve.CritereInvalide as exc:
                return {"ok": False, "error": f"critère de fin refusé : {exc}"}
        elif origine == "claude":
            return {"ok": False, "error": "critère de fin TYPÉ obligatoire (verification : {controles: […]}) : "
                                          "sans lui, la fin d'une mission ne se vérifie pas"}
        if critere and origine == "claude" and any(c["type"] == "commande_observee" for c in critere["controles"]):
            # Lot C : Astrel ne choisit pas la commande qui prouve sa propre fin (décision 3 de P43).
            return {"ok": False, "error": "critère de fin refusé : une commande observée se choisit par l'utilisateur "
                                          "seulement (formulaire du HUD)"}
        if not done_when and critere:
            done_when = " ; ".join(preuve.decrire(critere))
        if not done_when:
            return {"ok": False, "error": "il faut un critère de fin vérifiable (« fini quand… ») : "
                                          "sans lui, une mission ne sait pas s'arrêter"}
        try:
            ancrage = preuve.ancrer(critere, scope)
        except preuve.CritereInvalide as exc:
            return {"ok": False, "error": f"critère de fin refusé : {exc}"}
        with self.lock:
            generation = int(self.data.get("generation") or 0) + 1  # jamais en arrière, même entre deux missions
        data = {
            "schema": SCHEMA, "id": "m-" + secrets.token_hex(6), "generation": generation,
            "objective": objective[:600],
            "done_when": done_when[:600],
            "scope": scope,
            "forbidden": _lines(spec.get("forbidden"))[:20],
            "forbidden_paths": _abs_paths(spec.get("forbidden_paths"), base)[:20],
            "channel": str(spec.get("channel") or ""),
            "max_turns": int(_num(spec.get("max_turns"), config.MISSION_MAX_TURNS, 1, 200)),
            "max_minutes": _num(spec.get("max_minutes"), config.MISSION_MAX_MINUTES, 1, 24 * 60),
            "max_tokens": int(_num(spec.get("max_tokens"), config.MISSION_MAX_TOKENS, 10_000, 100_000_000)),
            "max_attempts": int(_num(spec.get("max_attempts"), config.MISSION_MAX_ATTEMPTS, 1, 10)),
            "lancee_par": "astrel" if origine == "claude" else "utilisateur",
            "status": ACTIVE, "started_at": time.time(), "ended_at": 0.0,
            "turns": 0, "tokens": 0, "attempts": 0, "reason": "", "last": "",
            # 04/11 (T50) : `tokens` = travail (hors cache relu), sous-agents compris ; `cache_lu` à part
            "mesure": MESURE_TRAVAIL, "cache_lu": 0, "mesure_partielle": False,
            "phase": TRAVAIL, "critere": critere, "critere_sha": preuve.empreinte_critere(critere) if critere else "",
            "origine": origine, "approbation": None, "ancrage": ancrage, "verif_en_cours": None,
            "verifs_ratees": 0, "bloquees": 0, "declaration": None, "preuve": None, "historique_preuves": [],
            "observations": _observations_vides(),
        }
        with self.lock:
            self.data = data
            self.awaiting, self.next_prompt = False, self._prompt(first=True)
        self._save()
        log.info("mission lancée : %s (fini quand : %s ; critère %s)", objective, done_when,
                 "typé" if critere else "libre")
        return {"ok": True, "mission": self.describe()}

    def pause(self, reason: str = "mise en pause par l'utilisateur") -> dict[str, Any]:
        with self.lock:
            if self.data.get("status") != ACTIVE:
                return {"ok": False, "error": "aucune mission active"}
            self.data.update(status=PAUSED, reason=reason, verif_en_cours=None)  # phase gardée
            self._bump()
            self.next_prompt = ""
        self._save()
        return {"ok": True, "mission": self.describe()}

    def resume(self) -> dict[str, Any]:
        """Reprise : la phase est gardée. En vérification, le contrôle est REFAIT (l'ancien
        résultat est jeté) ; en attente d'une décision, aucun tour n'est relancé."""
        with self.lock:
            if self.data.get("status") != PAUSED:
                return {"ok": False, "error": "aucune mission en pause"}
            self.data.update(status=ACTIVE, reason="", verif_en_cours=None)
            self._bump()
            self.awaiting, self.next_prompt = False, ""
            if self.phase == TRAVAIL:
                self.data.update(attempts=0, bloquees=0)
                self.next_prompt = self._prompt()
        self._save()
        return {"ok": True, "mission": self.describe()}

    def stop(self, reason: str = "arrêtée par l'utilisateur", status: str = STOPPED) -> dict[str, Any]:
        with self.lock:
            if self.data.get("status") not in (ACTIVE, PAUSED):
                return {"ok": False, "error": "aucune mission en cours"}
            self.data.update(status=status, reason=reason, ended_at=time.time(), verif_en_cours=None)
            self._bump()
            self.awaiting, self.next_prompt = False, ""
        self._save()
        log.info("mission finie (%s) : %s", status, reason)
        return {"ok": True, "mission": self.describe()}

    def close(self, mission_id: str = "") -> dict[str, Any]:
        """04/11 : FERMER une mission terminée (× du panneau) : `closed_at` ajouté, rien d'effacé
        (objectif, preuve, historique restent dans le fichier). Refusé pour une mission active ou en
        pause : la barrière du hook (`status`, `in_turn`) ne dépend jamais de ce champ. `mission_id`
        vide (terminal) : la mission du fichier ; donné et différent : refusé (une autre l'a remplacée)."""
        with self.lock:
            d = self.data
            if not d:
                return {"ok": False, "error": "aucune mission à fermer"}
            if mission_id and mission_id != d.get("id"):
                return {"ok": False, "error": "ce n'est plus la mission affichée : une autre l'a remplacée"}
            if d.get("status") not in ENDED:
                return {"ok": False, "error": "mission en cours ou en pause : arrête-la d'abord"}
            neuve = not d.get("closed_at")
            if neuve:
                d["closed_at"] = time.time()
                self._bump()
        if neuve:
            self._save()
            log.info("mission fermée : %s", d.get("objective", ""))
        return {"ok": True, "mission": self.describe()}

    # --- décisions de l'utilisateur sur une vérification (P43 ; refusées à Claude par `totalgate`) ---

    def _attend_decision(self) -> bool:
        return self.data.get("status") in (ACTIVE, PAUSED) and self.data.get("phase") == VERIF_BLOCKED

    def accept(self) -> dict[str, Any]:
        """l'utilisateur accepte le résultat SANS preuve automatique : `accepted_unverified`, jamais `done`."""
        with self.lock:  # RLock : l'arrêt se fait sous le même verrou que le contrôle
            if not self._attend_decision():
                return {"ok": False, "error": "aucune mission n'attend ta décision"}
            return self.stop("acceptée par l'utilisateur, sans preuve automatique", status=ACCEPTED)

    def reverify(self) -> dict[str, Any]:
        with self.lock:
            if not self._attend_decision() or self.data.get("status") != ACTIVE:
                return {"ok": False, "error": "aucune mission n'attend une nouvelle vérification"}
            if not self.data.get("critere"):
                return {"ok": False, "error": "critère libre : rien à vérifier automatiquement (accepte ou arrête)"}
            self.data.update(phase=VERIFICATION, reason="", verif_en_cours=None)
            self._bump()
        self._save()
        return {"ok": True, "mission": self.describe()}

    def back_to_work(self) -> dict[str, Any]:
        with self.lock:
            if not self._attend_decision() or self.data.get("status") != ACTIVE:
                return {"ok": False, "error": "aucune mission n'attend ta décision"}
            budget = self.over_budget()
            if budget:
                return {"ok": False, "error": f"{budget} : accepte ou arrête la mission"}
            self.data.update(phase=TRAVAIL, reason="", attempts=0, bloquees=0, verif_en_cours=None)
            self._bump()
            self.awaiting = False
            self.next_prompt = self._prompt(retry="L'utilisateur a relu la vérification et te remet au travail")
        self._save()
        return {"ok": True, "mission": self.describe()}

    def approve(self) -> dict[str, Any]:
        """l'utilisateur approuve le critère proposé par Astrel, lié à son empreinte EXACTE."""
        with self.lock:
            d = self.data
            if d.get("status") not in (ACTIVE, PAUSED) or not d.get("critere"):
                return {"ok": False, "error": "aucun critère typé à approuver"}
            if d.get("origine") != "claude":
                return {"ok": False, "error": "ce critère vient déjà de toi"}
            # Pas de `_bump` : une vérification en cours reste valable (l'approbation ne change pas le critère).
            d["approbation"] = {"sha": d.get("critere_sha", ""), "at": time.time()}
        self._save()
        return {"ok": True, "mission": self.describe()}

    def compter(self, ajouts: Any, usage: Any = None) -> None:
        """04/11 (T50) : un `result` du CLI pendant une mission ACTIVE (tour de mission, de l'utilisateur ou d'un
        agent de fond) : son travail entre dans le budget, sous-agents compris. Sans ajouts du compteur
        (pas de `modelUsage`) : repli sur `usage`, session principale seule, mesure notée partielle."""
        with self.lock:
            d = self.data
            if not d or d.get("status") != ACTIVE or d.get("mesure") != MESURE_TRAVAIL:
                return
            vu = travail_de(ajouts)
            if vu is None:
                vu = travail_usage(usage)
                if vu == (0, 0):
                    return
                d["mesure_partielle"] = True
            d["tokens"] = int(d.get("tokens") or 0) + vu[0]
            d["cache_lu"] = int(d.get("cache_lu") or 0) + vu[1]
        self._save()

    # --- le fil de la mission ---

    def over_budget(self) -> str:
        """Pourquoi le budget est épuisé (vide sinon)."""
        with self.lock:
            d = self.data
            if not d:
                return ""
            if d["turns"] >= d["max_turns"]:
                return f"budget de {d['max_turns']} tours atteint"
            if time.time() - d["started_at"] >= d["max_minutes"] * 60:
                return f"durée maximale de {d['max_minutes']:g} minutes atteinte"
            if d["tokens"] >= d["max_tokens"]:
                return f"budget de {d['max_tokens'] // 1000} k tokens atteint"
        if self.quota >= config.MISSION_QUOTA_STOP:
            return f"fenêtre de 5 h de l'abonnement à {round(self.quota * 100)} %"
        return ""

    def take_prompt(self) -> str:
        """La relance à envoyer maintenant (vide s'il n'y en a pas). Le tour est compté ici.
        Jamais hors de la phase « travail » (P43)."""
        with self.lock:
            if self.data.get("status") != ACTIVE or self.awaiting or not self.next_prompt \
                    or self.phase != TRAVAIL:
                return ""
            prompt, self.next_prompt = self.next_prompt, ""
            self.awaiting = True
            self.data["turns"] += 1
        self._save()
        return prompt

    def after_turn(self, text: str, usage: Any, by_mission: bool = True) -> tuple[str, str]:
        """Fin d'un tour pendant la mission. Rend (statut, raison) : « continue »,
        « verification » (FINIE déclarée : le cœur lance le contrôle), ou un statut de fin.
        `by_mission` faux : l'utilisateur a pris le tour (il a parlé en plein travail) ; il ne
        compte pas comme un essai raté."""
        with self.lock:
            if not self.awaiting:
                return "", ""
            self.awaiting = False
            if self.data.get("status") != ACTIVE:
                # Mise en pause pendant ce tour : il est fini, la barrière du hook tombe.
                self._save()  # RLock : réentrant
                return "", ""
            d = self.data
            if d.get("mesure") != MESURE_TRAVAIL:
                d["tokens"] += tokens_of(usage)  # mission d'avant le 04/11 : son ancienne mesure
            kind, reason = verdict(text)
            last_line = text.strip().splitlines()[-1] if text.strip() else ""
            d["last"] = (reason or last_line)[:300]
            if kind == "finie":
                # P43 : une DÉCLARATION, pas une preuve. Vérifiée même si le budget vient d'être
                # atteint (aucun token), mais aucun tour de Claude de plus.
                d.update(phase=VERIFICATION, verif_en_cours=None, bloquees=0,
                         declaration={"texte": (reason or last_line)[:300], "tour": d["turns"], "at": time.time()})
                self._bump()
                self.next_prompt = ""
                self._save()
                return VERIFICATION, ""
            end: tuple[str, str] | None = None
            retry = ""
            # 02/11 (essai réel) : deux « BLOQUÉE » DÉCLARÉS de suite arrêtent la mission (une consigne
            # impossible ne se débloque pas en relançant) ; une ligne absente reste un simple essai.
            d["bloquees"] = d.get("bloquees", 0) + 1 if kind == "bloquee" and by_mission else 0
            if kind == "continue" or not by_mission:
                d["attempts"] = 0
            else:
                d["attempts"] += 1
                retry = reason or "dernière ligne « MISSION : … » absente"
                if d["bloquees"] >= 2:
                    end = (BLOCKED, f"BLOQUÉE deux fois de suite ({retry}) : à toi de voir")
                elif d["attempts"] >= d["max_attempts"]:
                    end = (BLOCKED, f"{d['attempts']} essais sans progrès ({retry}) : à toi de voir")
            if end is None:
                budget = self.over_budget()
                if budget:
                    end = (BUDGET, budget)
                else:
                    self.next_prompt = self._prompt(retry=retry)
        if end is not None:
            self.stop(end[1], status=end[0])
            return end
        self._save()
        return "continue", ""

    # --- vérification (P43) : début, puis application, sous le verrou ; le contrôle entre les deux ---

    def begin_verification(self) -> dict[str, Any] | None:
        """Instantané figé à vérifier, ou None (rien à faire, ou critère libre : la mission
        passe alors en attente de la décision de l'utilisateur). Un seul contrôle à la fois."""
        with self.lock:
            d = self.data
            if d.get("status") != ACTIVE or self.phase != VERIFICATION or self.awaiting or d.get("verif_en_cours"):
                return None
            if not d.get("critere"):
                d.update(phase=VERIF_BLOCKED,
                         reason="critère libre : pas de preuve automatique possible. Accepte sans preuve, "
                                "remets-la au travail ou arrête-la.")
                self._bump()
                self._save()
                return None
            self._bump()
            vid = f"{d['id']}:{d['generation']}"
            d["verif_en_cours"] = {"verification_id": vid, "depuis": time.time()}
            inst = json.loads(json.dumps({
                "mission_id": d["id"], "generation": d["generation"], "verification_id": vid,
                "critere": d["critere"], "critere_sha": d.get("critere_sha", ""), "origine": d.get("origine", ""),
                "approuve": _approuve(d), "scope": d.get("scope") or [], "ancrage": d.get("ancrage") or {},
                "observations": d.get("observations") or _observations_vides(),
            }))
        self._save()
        return inst

    # --- lot C : commandes observées dans le flux du CLI ---

    def observe_tool_start(self, tool: str, tool_id: str, tool_input: Any) -> None:
        """Un outil de Claude (ou d'un sous-agent) démarre. La commande EXACTE d'un critère
        `commande_observee`, au premier plan, est notée ; tout autre outil qui peut écrire avance
        le repère « dernière écriture ». Le cœur ne lance rien : il lit."""
        with self.lock:
            d = self.data
            if d.get("status") != ACTIVE:
                return
            obs = d.setdefault("observations", _observations_vides())
            obs["seq"] = int(obs.get("seq") or 0) + 1
            attendues = {preuve.commande_attendue(c) for c in (d.get("critere") or {}).get("controles", [])
                         if c.get("type") == "commande_observee"}
            entree = tool_input if isinstance(tool_input, dict) else {}
            cmd = str(entree.get("command") or "").strip() if tool == "Bash" else ""
            if cmd and cmd in attendues and not entree.get("run_in_background"):
                obs["commandes"] = ([o for o in obs.get("commandes") or [] if isinstance(o, dict)]
                                    + [{"cmd": cmd, "id": str(tool_id), "seq": obs["seq"], "etat": "en_cours",
                                        "code": "", "at": time.time()}])[-MAX_OBSERVATIONS:]
            elif tool not in _SANS_ECRITURE:
                obs["ecriture"], obs["ecriture_outil"] = obs["seq"], str(tool)[:60]

    def observe_tool_result(self, tool_id: str, is_error: bool, text: str = "", fond: bool = False) -> None:
        """Résultat d'un outil : seule une commande observée en attente est concernée. Code de
        sortie non nul (« Exit code N », délai dépassé compris) = échec ; parti en tâche de fond = échec."""
        with self.lock:
            obs = (self.data or {}).get("observations") or {}
            for o in reversed(obs.get("commandes") or []):
                if isinstance(o, dict) and o.get("id") == str(tool_id) and o.get("etat") == "en_cours":
                    if is_error or fond:
                        premiere = (str(text or "").strip().splitlines() or [""])[0]
                        o.update(etat=preuve.ECHEC, code="tâche de fond" if fond else premiere[:40])
                    else:
                        o["etat"] = preuve.OK
                    return

    def verification_stuck(self, after_s: float) -> bool:
        """Chien de garde : une vérification commencée il y a plus de `after_s` (fil coincé, système
        de fichiers qui ne répond pas) est abandonnée ; la mission attend la décision de l'utilisateur."""
        with self.lock:
            d = self.data
            v = d.get("verif_en_cours") or {}
            if d.get("status") != ACTIVE or self.phase != VERIFICATION or not v \
                    or time.time() - float(v.get("depuis") or 0) < after_s:
                return False
            d.update(phase=VERIF_BLOCKED, verif_en_cours=None,
                     reason="la vérification ne s'est pas terminée à temps : à toi de décider")
            _historique(d, str(v.get("verification_id") or ""), "abandonnee")
            self._bump()
        self._save()
        return True

    def still_current(self, inst: dict[str, Any]) -> bool:
        """L'instantané est-il encore celui de la vérification en cours ? (lu par `doit_arreter`)"""
        with self.lock:
            d = self.data
            return (d.get("id") == inst.get("mission_id") and d.get("generation") == inst.get("generation")
                    and d.get("status") == ACTIVE and d.get("phase") == VERIFICATION
                    and (d.get("verif_en_cours") or {}).get("verification_id") == inst.get("verification_id"))

    def apply_verification(self, inst: dict[str, Any], rapport: dict[str, Any]) -> tuple[str, str]:
        """Résultat du contrôle → transition, ATOMIQUEMENT. Rend (statut ou phase, raison) ; vide si
        le résultat est OBSOLÈTE (pause, arrêt, redémarrage, nouvelle tentative entre-temps) : jeté."""
        with self.lock:
            d = self.data
            if not self.still_current(inst) or rapport.get("verification_id") != inst.get("verification_id"):
                log.info("vérification %s jetée : la mission a changé entre-temps", inst.get("verification_id"))
                if d.get("id") == inst.get("mission_id"):
                    _historique(d, inst.get("verification_id", ""), "jetee")
                    self._save()
                return "", ""
            d["verif_en_cours"] = None
            d["preuve"] = rapport
            statut = rapport.get("statut")
            _historique(d, inst["verification_id"], statut)
            self._bump()
            if statut == preuve.VERIFIEE:
                self.stop("critère de fin vérifié par Astrel", status=DONE)
                return DONE, d["reason"]
            if statut != preuve.EN_ECHEC:  # bloquée (ou interrompue sans arrêt connu) : l'utilisateur décide
                d.update(phase=VERIF_BLOCKED,
                         reason=f"vérification bloquée ({preuve.resume_echec(rapport) or 'interrompue'}) : "
                                "à toi de décider")
                self._save()
                return VERIF_BLOCKED, d["reason"]
            d["verifs_ratees"] = int(d.get("verifs_ratees") or 0) + 1
            d["attempts"] += 1
            detail = preuve.resume_echec(rapport)
            if d["verifs_ratees"] >= config.MISSION_MAX_VERIFS or d["attempts"] >= d["max_attempts"]:
                self.stop(f"critère de fin non rempli après {d['verifs_ratees']} vérification(s) : {detail}",
                          status=VERIF_FAILED)
                return VERIF_FAILED, d["reason"]
            budget = self.over_budget()
            if budget:
                self.stop(f"critère de fin non rempli ({detail}), et {budget}", status=VERIF_FAILED)
                return VERIF_FAILED, d["reason"]
            d.update(phase=TRAVAIL, reason="")
            self.next_prompt = self._prompt(retry=f"tu as écrit FINIE, mais la vérification d'Astrel a échoué : {detail}")
        self._save()
        return TRAVAIL, ""

    # --- textes envoyés à Claude ---

    def _criterion_lines(self) -> list[str]:
        d = self.data
        lignes = preuve.decrire(d.get("critere"))
        if not lignes:
            return [f"Fini quand (critère libre) : {d['done_when']}. Astrel ne peut pas le vérifier seul : "
                    "l'utilisateur constatera lui-même."]
        out = [f"Fini quand (VÉRIFIÉ par Astrel après ton « FINIE », pas sur ta parole) : {'; '.join(lignes)}."]
        if any(c.get("type") == "commande_observee" for c in d["critere"].get("controles", [])):
            out.append("Commande observée : lance-la avec l'outil Bash EXACTEMENT telle qu'écrite (au premier plan, "
                       "sans tube, sans « || true », sans rien ajouter), APRÈS ta dernière modification, puis "
                       "déclare FINIE sans rien lancer d'autre ; Astrel lit son code de sortie dans ton flux.")
        if any(c.get("type") == "empreinte" for c in d["critere"].get("controles", [])):
            # 02/11 (essai réel) : jamais chercher un contenu par calcul pour tomber sur une empreinte.
            out.append("Une empreinte sha256 se compare ENTIÈRE ; ne cherche jamais par calcul un contenu qui la "
                       "donnerait : si le contenu attendu est inconnu, MISSION : BLOQUÉE.")
        return out

    def _prompt(self, first: bool = False, retry: str = "") -> str:
        d = self.data
        left_turns = d["max_turns"] - d["turns"]
        left_min = max(0, round(d["max_minutes"] - (time.time() - d["started_at"]) / 60))
        left_k = max(0, (d["max_tokens"] - d["tokens"]) // 1000)
        n = d["turns"] + 1
        head = f"[Mission autonome lancée par l'utilisateur, tour {n} sur {d['max_turns']}]"
        budget = f"Il reste {left_turns} tours, {left_min} minutes et {left_k} k tokens."
        forbidden = "; ".join(d["forbidden"]) or "rien de plus que d'habitude"
        if d["forbidden_paths"]:
            forbidden += " ; dossiers où tu n'écris RIEN : " + ", ".join(d["forbidden_paths"])
        scope = ", ".join(d.get("scope") or []) or "non précisé"
        fence = (f"Périmètre : tu n'écris, ne déplaces et ne supprimes QUE dans {scope} (et /tmp). "
                 "Ailleurs, le garde-fou t'arrête et attend l'utilisateur : ne tente pas de contourner.")
        rules = (
            "Termine CHAQUE tour par une dernière ligne seule : « MISSION : CONTINUE », "
            "« MISSION : FINIE — preuve » ou « MISSION : BLOQUÉE — raison »."
        )
        if first:
            return "\n".join([
                head,
                f"Objectif : {d['objective']}",
                *self._criterion_lines(),
                fence,
                f"Interdits : {forbidden}.",
                f"Budget : {d['max_turns']} tours, {d['max_minutes']:g} minutes, {d['max_tokens'] // 1000} k tokens.",
                "Règles : travaille seul, sans poser de question à l'utilisateur (il n'est peut-être pas là) ; les "
                "actions critiques restent confirmées par le garde-fou. Prouve le critère de fin (sortie de "
                "commande, extrait de fichier) au lieu de l'affirmer, et ne supprime ni n'affaiblis un test ou "
                "une vérification pour le remplir. Si un essai échoue, change d'approche ; après "
                f"{d['max_attempts']} échecs de suite, ou deux « BLOQUÉE » de suite, la mission s'arrête et "
                "l'utilisateur décide. N'élargis "
                "jamais la mission. Le texte de chaque tour s'affiche dans le tableau de bord sans être dit : "
                "écris-le comme un compte rendu court.",
                rules,
            ])
        lines = [head, f"Continue vers l'objectif : {d['objective']} (fini quand : {d['done_when']}). "
                       f"Fais la prochaine étape concrète. {budget}", fence]
        if retry:
            lines.append(f"Essai {d['attempts'] + 1} sur {d['max_attempts']} : le tour précédent n'a pas abouti "
                         f"({retry}). Change d'approche.")
        lines.append(rules)
        return "\n".join(lines)

    def report(self) -> str:
        """Compte rendu de fin, écrit pour être écouté (posé dans le HUD)."""
        d = self.describe()
        if not d.get("objective"):
            return ""
        status = {DONE: "Mission terminée et vérifiée", BLOCKED: "Mission arrêtée, bloquée",
                  BUDGET: "Mission arrêtée, budget épuisé", STOPPED: "Mission arrêtée",
                  ACCEPTED: "Mission acceptée par toi, sans preuve automatique",
                  VERIF_FAILED: "Mission non terminée : la vérification a échoué"}.get(d.get("status"), "Mission")
        if d.get("status") == DONE and d.get("origine") == "claude" and not d.get("approuve"):
            status = "Mission terminée, vérifiée selon le critère choisi par Astrel (non approuvé par toi)"
        tours = d.get("turns", 0)
        return (f"{status} : {d['objective']}. {d.get('reason', '').rstrip('.')}. "
                f"{tours} tour{'s' if tours > 1 else ''} en {d.get('elapsed_min', 0):g} minutes.")


def _completer(d: dict[str, Any]) -> None:
    """Mission d'avant P43 (schéma 1) : champs manquants, sans rien changer à ce qui existe."""
    d.setdefault("schema", 1)
    d.setdefault("id", "m-" + secrets.token_hex(6))
    d["generation"] = int(d.get("generation") or 0)
    d.setdefault("phase", TRAVAIL)
    for key, value in (("critere", None), ("critere_sha", ""), ("origine", "claude" if d.get("lancee_par") == "astrel"
                       else "utilisateur"), ("approbation", None), ("ancrage", {}), ("verif_en_cours", None),
                       ("verifs_ratees", 0), ("bloquees", 0), ("observations", _observations_vides()), ("declaration", None), ("preuve", None), ("historique_preuves", [])):
        d.setdefault(key, value)
    d.setdefault("mesure", MESURE_ANCIENNE)  # 04/11 (T50) : une mission d'avant garde sa mesure jusqu'à sa fin


def est_close(d: Any, now: float | None = None) -> bool:
    """04/11 : mission TERMINÉE et fermée par l'utilisateur (`closed_at`), ou finie depuis plus de
    `FERMETURE_AUTO_S`. Jamais vrai pour une mission active ou en pause."""
    if not isinstance(d, dict) or d.get("status") not in ENDED:
        return False
    if d.get("closed_at"):
        return True
    try:
        fin = float(d.get("ended_at") or d.get("started_at") or 0)
    except (TypeError, ValueError):
        return False
    return fin > 0 and (time.time() if now is None else now) - fin >= FERMETURE_AUTO_S


def _observations_vides() -> dict[str, Any]:
    return {"seq": 0, "ecriture": 0, "ecriture_outil": "", "commandes": []}


def _approuve(d: dict[str, Any]) -> bool:
    a = d.get("approbation")
    return bool(isinstance(a, dict) and d.get("critere_sha") and a.get("sha") == d.get("critere_sha"))


def _historique(d: dict[str, Any], vid: str, statut: Any) -> None:
    h = [x for x in d.get("historique_preuves") or [] if isinstance(x, dict)]
    h.append({"verification_id": vid, "at": time.time(), "statut": str(statut or "")})
    d["historique_preuves"] = h[-5:]


mission = Mission()
