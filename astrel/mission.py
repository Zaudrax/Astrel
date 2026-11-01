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
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger("astrel.mission")

ACTIVE, PAUSED, DONE, BLOCKED, STOPPED, BUDGET = "active", "paused", "done", "blocked", "stopped", "budget"
ENDED = (DONE, BLOCKED, STOPPED, BUDGET)

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


def tokens_of(usage: Any) -> int:
    """Tokens traités par la session principale pendant un tour (entrée, cache
    lu et écrit, sortie). Les sous-agents n'y sont pas : leur coût se voit dans
    la fenêtre de 5 h, d'où l'arrêt sur le quota en plus."""
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

    # --- état ---

    def load(self) -> None:
        """Au démarrage : une mission active repart EN PAUSE (l'utilisateur la relance)."""
        try:
            data = json.loads(config.MISSION_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict) or not data.get("objective"):
            data = {}
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
    def active(self) -> bool:
        return self.status == ACTIVE

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
            d = dict(self.data)
        if not d:
            return {"status": ""}
        elapsed = (d.get("ended_at") or time.time()) - float(d.get("started_at") or time.time())
        d["elapsed_min"] = round(max(0.0, elapsed) / 60.0, 1)
        return d

    # --- commandes de l'utilisateur ---

    def start(self, spec: dict[str, Any]) -> dict[str, Any]:
        objective = str(spec.get("objective") or "").strip()
        done_when = str(spec.get("done_when") or "").strip()
        if not objective:
            return {"ok": False, "error": "objectif vide"}
        if not done_when:
            return {"ok": False, "error": "il faut un critère de fin vérifiable (« fini quand… ») : "
                                          "sans lui, une mission ne sait pas s'arrêter"}
        if self.active:
            return {"ok": False, "error": "une mission tourne déjà : arrête-la d'abord"}
        base = str(spec.get("base") or config.WORKDIR)
        scope = _abs_paths(spec.get("scope"), base)[:20]
        if not scope:
            return {"ok": False, "error": "il faut un périmètre : le ou les dossiers où la mission peut écrire"}
        if "/" in scope:
            return {"ok": False, "error": "périmètre trop large : « / » ouvrirait tout le disque"}
        data = {
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
            "lancee_par": "astrel" if spec.get("lancee_par") == "astrel" else "utilisateur",
            "status": ACTIVE, "started_at": time.time(), "ended_at": 0.0,
            "turns": 0, "tokens": 0, "attempts": 0, "reason": "", "last": "",
        }
        with self.lock:
            self.data = data
            self.awaiting, self.next_prompt = False, self._prompt(first=True)
        self._save()
        log.info("mission lancée : %s (fini quand : %s)", objective, done_when)
        return {"ok": True, "mission": self.describe()}

    def pause(self, reason: str = "mise en pause par l'utilisateur") -> dict[str, Any]:
        with self.lock:
            if self.data.get("status") != ACTIVE:
                return {"ok": False, "error": "aucune mission active"}
            self.data.update(status=PAUSED, reason=reason)
            self.next_prompt = ""
        self._save()
        return {"ok": True, "mission": self.describe()}

    def resume(self) -> dict[str, Any]:
        with self.lock:
            if self.data.get("status") != PAUSED:
                return {"ok": False, "error": "aucune mission en pause"}
            self.data.update(status=ACTIVE, reason="", attempts=0)
            self.awaiting, self.next_prompt = False, self._prompt()
        self._save()
        return {"ok": True, "mission": self.describe()}

    def stop(self, reason: str = "arrêtée par l'utilisateur", status: str = STOPPED) -> dict[str, Any]:
        with self.lock:
            if self.data.get("status") not in (ACTIVE, PAUSED):
                return {"ok": False, "error": "aucune mission en cours"}
            self.data.update(status=status, reason=reason, ended_at=time.time())
            self.awaiting, self.next_prompt = False, ""
        self._save()
        log.info("mission finie (%s) : %s", status, reason)
        return {"ok": True, "mission": self.describe()}

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
        """La relance à envoyer maintenant (vide s'il n'y en a pas). Le tour est compté ici."""
        with self.lock:
            if self.data.get("status") != ACTIVE or self.awaiting or not self.next_prompt:
                return ""
            prompt, self.next_prompt = self.next_prompt, ""
            self.awaiting = True
            self.data["turns"] += 1
        self._save()
        return prompt

    def after_turn(self, text: str, usage: Any, by_mission: bool = True) -> tuple[str, str]:
        """Fin d'un tour pendant la mission. Rend (statut, raison) : « continue »,
        ou un statut de fin. `by_mission` faux : l'utilisateur a pris le tour (il a
        parlé en plein travail) ; il ne compte pas comme un essai raté."""
        with self.lock:
            if not self.awaiting:
                return "", ""
            self.awaiting = False
            if self.data.get("status") != ACTIVE:
                # Mise en pause pendant ce tour : il est fini, la barrière du hook tombe.
                self._save()  # RLock : réentrant
                return "", ""
            d = self.data
            d["tokens"] += tokens_of(usage)
            kind, reason = verdict(text)
            last_line = text.strip().splitlines()[-1] if text.strip() else ""
            d["last"] = (reason or last_line)[:300]
            end: tuple[str, str] | None = None
            retry = ""
            if kind == "finie":
                end = (DONE, reason or "critère de fin atteint")
            elif kind == "continue" or not by_mission:
                d["attempts"] = 0
            else:
                d["attempts"] += 1
                retry = reason or "dernière ligne « MISSION : … » absente"
                if d["attempts"] >= d["max_attempts"]:
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

    # --- textes envoyés à Claude ---

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
                f"Fini quand (critère vérifiable) : {d['done_when']}",
                fence,
                f"Interdits : {forbidden}.",
                f"Budget : {d['max_turns']} tours, {d['max_minutes']:g} minutes, {d['max_tokens'] // 1000} k tokens.",
                "Règles : travaille seul, sans poser de question à l'utilisateur (il n'est peut-être pas là) ; les "
                "actions critiques restent confirmées par le garde-fou. Prouve le critère de fin (sortie de "
                "commande, extrait de fichier) au lieu de l'affirmer, et ne supprime ni n'affaiblis un test ou "
                "une vérification pour le remplir. Si un essai échoue, change d'approche ; après "
                f"{d['max_attempts']} échecs de suite, la mission s'arrête et l'utilisateur décide. N'élargis "
                "jamais la mission. Le texte de chaque tour s'affiche dans le tableau de bord sans être dit : "
                "écris-le comme un compte rendu court.",
                rules,
            ])
        lines = [head, f"Continue vers l'objectif : {d['objective']} (fini quand : {d['done_when']}). "
                       f"Fais la prochaine étape concrète. {budget}", fence]
        if retry:
            lines.append(f"Essai {d['attempts'] + 1} sur {d['max_attempts']} : le tour précédent n'a pas avancé "
                         f"({retry}). Change d'approche.")
        lines.append(rules)
        return "\n".join(lines)

    def report(self) -> str:
        """Compte rendu de fin, écrit pour être écouté (posé dans le HUD)."""
        d = self.describe()
        if not d.get("objective"):
            return ""
        status = {DONE: "Mission terminée", BLOCKED: "Mission arrêtée, bloquée", BUDGET: "Mission arrêtée, budget épuisé",
                  STOPPED: "Mission arrêtée"}.get(d.get("status"), "Mission")
        tours = d.get("turns", 0)
        return (f"{status} : {d['objective']}. {d.get('reason', '').rstrip('.')}. "
                f"{tours} tour{'s' if tours > 1 else ''} en {d.get('elapsed_min', 0):g} minutes.")


mission = Mission()
