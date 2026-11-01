"""L'orateur : une file de phrases, une voix, un haut-parleur.

- Les phrases arrivent pendant que Claude écrit encore ; la première part dès
  qu'elle est complète, les suivantes s'enchaînent sans rouvrir le lecteur.
- Si plusieurs phrases attendent, elles sont synthétisées d'un bloc : moins
  d'appels réseau pour Gemini, une prosodie plus naturelle pour tous.
- Si le moteur principal échoue (réseau, quota, clé), la phrase est rejouée
  avec la voix de secours : la voix ne se tait JAMAIS pour une panne.
  Une erreur PASSAGÈRE (429, 5xx, coupure réseau, DNS) a d'abord droit à un ou
  deux nouveaux essais rapides, tant que rien n'a été joué et dans un budget
  de temps court (`RETRY_BUDGET_S`). Après un échec définitif, un disjoncteur
  met le principal au repos (`COOLDOWN_BASE_S`, doublé à chaque rechute
  jusqu'à `COOLDOWN_MAX_S`, ou plus si le service envoie Retry-After) : pas de
  latence ajoutée à chaque phrase, pas d'acharnement sur un 429. Le repos
  fini, la phrase suivante le retente UNE fois (sans nouvel essai) ; s'il
  répond, son retour est journalisé et la série d'échecs remise à zéro.
- `interrupt()` coupe le son immédiatement et jette la file.
- `pause()` coupe le son NET, comme un lecteur vidéo, mais ne jette rien : le
  son pas encore entendu (depuis le début du mot en cours) est gardé, la phrase
  en cours de synthèse aussi, et les phrases suivantes attendent dans la file.
  `resume()` rejoue tout dans l'ordre. Pendant la pause l'orateur compte comme
  inactif (`busy` faux, `wait_idle` rend la main) : le micro n'est plus sourd et
  la conversation continue, seul le haut-parleur se tait.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from .. import audio, config
from ..audio import Player
from ..events import bus
from .engines import Engine, build_engine
from .text import clean_for_speech

log = logging.getLogger("astrel.speaker")

BATCH_CHARS = 400          # au-delà, on n'agrège plus les phrases en attente
GRACE_S = 0.35             # attente d'une phrase suivante avant de fermer le lecteur
RETRY_DELAYS_S = (0.3, 0.8)  # nouveaux essais sur erreur passagère, avant la voix de secours
RETRY_BUDGET_S = 2.5       # au-delà (essais compris), on bascule sans réessayer
COOLDOWN_BASE_S = 90.0     # repos du moteur principal après un premier échec définitif
COOLDOWN_MAX_S = 900.0     # plafond du repos, doublé à chaque rechute
_NET_ERRORS = {"ClientConnectionError", "ClientPayloadError"}  # aiohttp, sans l'importer ici


def _status(exc: BaseException) -> int | None:
    status = getattr(exc, "status", None)
    return status if isinstance(status, int) else None


def is_transient(exc: BaseException) -> bool:
    """Vaut la peine d'un nouvel essai immédiat : trop de requêtes, serveur en
    difficulté, réseau ou DNS qui flanche. Un 403 n'en est pas (edge-tts le
    réessaie déjà une fois en recalant l'horloge) ; une attente de 30 s non plus."""
    status = _status(exc)
    if status is not None:
        return status in (408, 429) or 500 <= status < 600
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    return any(k.__name__ in _NET_ERRORS for k in type(exc).__mro__)


def _short(exc: BaseException) -> str:
    """L'erreur en clair pour le journal : « HTTP 429 » plutôt que l'URL entière."""
    status = _status(exc)
    if status is not None:
        return f"HTTP {status}"
    text = str(exc) or type(exc).__name__
    return text if len(text) <= 200 else text[:200] + "…"


def retry_after(exc: BaseException) -> float | None:
    """Délai demandé par le service (en-tête Retry-After, en secondes)."""
    headers = getattr(exc, "headers", None)
    try:
        value = headers.get("Retry-After") if headers else None
        return max(0.0, float(value)) if value is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


@dataclass
class _Item:
    text: str
    generation: int
    # 15/10 : étiquette d'une phrase qu'on peut retirer SEULE (`Speaker.cancel`) : la
    # question d'une confirmation, à faire taire dès que l'utilisateur a répondu à l'écran.
    tag: str = ""


class Speaker:
    def __init__(
        self,
        spec: str | None = None,
        fallback: str | None = None,
        on_activity: Callable[[bool], None] | None = None,
    ) -> None:
        self._primary = build_engine(spec or config.TTS_ENGINE)
        fb = fallback or config.TTS_FALLBACK
        self._fallback = build_engine(fb) if fb and fb != self._primary.name else None
        self._degraded_until = 0.0
        self._failures = 0          # échecs définitifs d'affilée du principal (disjoncteur)
        self._degraded_since = 0.0  # début de la panne en cours, pour le log du retour
        self._emitted = False       # la tentative en cours a déjà joué du son
        self._on_activity = on_activity
        self._queue: queue.Queue[_Item] = queue.Queue()
        self._generation = 0
        self._idle = threading.Event()
        self._idle.set()
        self._player: Player | None = None
        self._closing: Player | None = None   # lecteur qui finit de jouer sa queue de son
        self._player_lock = threading.Lock()
        self._paused = threading.Event()
        self._resumed = threading.Event()
        self._resumed.set()
        self._held: list[tuple[int, bytes, str]] = []  # son gardé pendant la pause : (débit, PCM, étiquette)
        self._carry: _Item | None = None          # phrase tirée de la file juste quand la pause est tombée
        self._speaking_tag = ""                   # étiquette de la phrase en cours de synthèse/lecture
        self._cancelled: deque[str] = deque(maxlen=64)  # étiquettes retirées : plus jamais dites
        self._running = True
        self._worker = threading.Thread(target=self._loop, name="speaker", daemon=True)

    # --- cycle de vie ---

    def load(self) -> None:
        """Charge la voix ; bascule sur la voix de secours si la principale est inutilisable."""
        try:
            self._primary.load()
        except Exception as exc:
            log.warning("voix %s indisponible (%s)", self._primary.name, exc)
            if self._fallback is None:
                raise
            self._degraded_until = float("inf")
            bus.publish("notice", level="warning", text=f"Voix {self._primary.name} indisponible : {exc}")
        if self._fallback is not None:
            try:
                self._fallback.load()
            except Exception as exc:
                log.warning("voix de secours %s indisponible (%s)", self._fallback.name, exc)
                self._fallback = None
        self._publish_voice()
        if not self._worker.is_alive():
            self._worker.start()

    def close(self) -> None:
        self._running = False
        self.interrupt()
        self._resumed.set()
        self._queue.put(_Item("", -1))

    def set_voice(self, spec: str) -> str:
        """Change de voix à chaud (sélecteur du HUD)."""
        engine = build_engine(spec)
        engine.load()  # lève si inutilisable : l'ancienne voix reste en place
        self._primary = engine
        self._degraded_until = 0.0
        self._failures = 0
        self._publish_voice()
        return engine.name

    @property
    def voice(self) -> str:
        return self._active_engine().name

    # --- parole ---

    def say(self, text: str, raw: bool = False, tag: str = "") -> None:
        payload = text.strip() if raw else clean_for_speech(text)
        if not payload or (tag and tag in self._cancelled):
            return
        self._idle.clear()
        self._queue.put(_Item(payload, self._generation, tag))

    def cancel(self, tag: str) -> bool:
        """Retire la phrase marquée `tag`, et elle SEULE (15/10) : en file, gardée par
        la pause, ou en train d'être dite (coupée net). Ce qui suit dans la file est
        dit ensuite, normalement. Une phrase de cette étiquette dite plus tard est
        jetée. Vrai si quelque chose a été retiré."""
        if not tag:
            return False
        hit = False
        with self._player_lock:
            self._cancelled.append(tag)
            with self._queue.mutex:
                q = self._queue.queue
                keep = [i for i in q if i.tag != tag]
                hit = len(keep) != len(q)
                q.clear()
                q.extend(keep)
            if self._carry is not None and self._carry.tag == tag:
                self._carry, hit = None, True
            held = [h for h in self._held if h[2] != tag]
            if len(held) != len(self._held):
                self._held[:], hit = held, True
            if self._speaking_tag == tag:
                # La phrase est en cours : nouvelle génération (le rendu s'arrête), mais
                # ce qui attend derrière passe à la nouvelle génération et sera dit.
                hit = True
                old, self._generation = self._generation, self._generation + 1
                with self._queue.mutex:
                    for i in self._queue.queue:
                        if i.generation == old:
                            i.generation = self._generation
                if self._carry is not None and self._carry.generation == old:
                    self._carry.generation = self._generation
            # Synthèse finie mais son qui sort encore (lecteur courant ou qui se vide) :
            # chaque lecteur porte l'étiquette de ce qu'il joue, seul le sien est tué.
            for p in (self._player, self._closing):
                if p is not None and not p.stopped and getattr(p, "tag", "") == tag:
                    p.kill()
                    hit = True
        if hit:
            self._queue.put(_Item("", -1))  # réveille la boucle : elle rend « idle » s'il ne reste rien
        return hit

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Attend la fin de la parole. En pause, rend la main tout de suite :
        ce qui reste à dire attend la reprise, personne ne doit l'attendre."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while not (self._idle.is_set() or self._paused.is_set()):
            remaining = 0.1 if deadline is None else min(0.1, deadline - time.monotonic())
            if remaining <= 0:
                return False
            self._idle.wait(remaining)
        return True

    @property
    def busy(self) -> bool:
        return not self._idle.is_set() and not self._paused.is_set()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    @property
    def pending(self) -> bool:
        """Il reste quelque chose à dire (son gardé par la pause, phrases en file)."""
        with self._player_lock:
            return bool(self._held or self._carry) or self._queued_text()

    def _queued_text(self) -> bool:
        """Une vraie phrase attend dans la file (pas un simple réveil de la boucle)."""
        with self._queue.mutex:
            return any(i.text and i.generation == self._generation for i in self._queue.queue)

    def interrupt(self) -> None:
        """Coupe la parole en cours et jette ce qui attendait, pause comprise."""
        self._generation += 1
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        with self._player_lock:
            self._held.clear()
            self._carry = None
            playing = [p for p in (self._closing, self._player) if p is not None and not p.stopped]
            for p in playing:
                p.kill()
            if not playing:
                # Rien ne jouait : les phrases jetées ne seront jamais dites.
                self._idle.set()

    def pause(self) -> bool:
        """Coupe le son NET sans rien jeter. False si la voix était déjà en pause."""
        with self._player_lock:
            if self._paused.is_set():
                return False
            self._paused.set()
            self._resumed.clear()
            audio.hold_output(True)
            cut: list[tuple[int, bytes, str]] = []
            for p in (self._closing, self._player):  # le lecteur qui se vide a joué AVANT le courant
                if p is not None and not p.stopped:
                    rest = p.cut()
                    if rest:
                        cut.append((p.rate, rest, getattr(p, "tag", "")))
            self._held[:0] = cut  # avant ce qui a pu être gardé depuis : l'ordre du discours
        self._queue.put(_Item("", -1))  # réveille la boucle, qui libère le micro
        return True

    def resume(self) -> bool:
        """Reprend là où la pause a coupé. False si la voix n'était pas en pause."""
        with self._player_lock:
            if not self._paused.is_set():
                return False
            self._paused.clear()
            audio.hold_output(False)
            if self._held or self._carry or self._queued_text():
                self._idle.clear()
        self._resumed.set()
        return True

    # --- boucle ---

    def _active_engine(self) -> Engine:
        if self._fallback is not None and time.monotonic() < self._degraded_until:
            return self._fallback
        return self._primary

    def _publish_voice(self) -> None:
        degraded = self._fallback is not None and time.monotonic() < self._degraded_until
        bus.publish(
            "voice",
            engine=self._primary.name,
            fallback=self._fallback.name if self._fallback else "",
            active=self._active_engine().name,
            degraded=degraded,
        )

    def _loop(self) -> None:
        speaking = False
        while self._running:
            if self._paused.is_set():
                if speaking:
                    # Le lecteur est déjà coupé : on rend le micro tout de suite.
                    self._finish(speaking, wait=False)
                    speaking = False
                self._resumed.wait()
                continue
            if self._held:
                # Reprise : d'abord ce que la pause avait coupé.
                if not speaking:
                    speaking = True
                    if self._on_activity:
                        self._on_activity(True)
                self._flush_held()
                continue
            with self._player_lock:
                item, self._carry = self._carry, None
            if item is None:
                try:
                    item = self._queue.get(timeout=GRACE_S if speaking else None)
                except queue.Empty:
                    # Plus rien à dire depuis GRACE_S : on laisse finir le son.
                    self._finish(speaking)
                    speaking = False
                    continue
            if not self._running:
                break
            if self._paused.is_set():
                if item.text and item.generation == self._generation:
                    with self._player_lock:
                        self._carry = item  # dite à la reprise, à sa place
                continue
            if item.generation != self._generation or not item.text:
                if self._queue.empty() and not speaking and not self._held and self._carry is None:
                    self._idle.set()
                continue
            text = self._gather(item)
            with self._player_lock:
                if item.tag and item.tag in self._cancelled:
                    text = ""  # retirée entre la file et ici (`cancel`)
                else:
                    self._speaking_tag = item.tag
            if not text:
                if self._queue.empty() and not speaking and not self._held and self._carry is None:
                    self._idle.set()
                continue
            if not speaking:
                speaking = True
                if self._on_activity:
                    self._on_activity(True)
            bus.publish("speech", text=text)
            try:
                self._speak(text, item.generation)
            finally:
                with self._player_lock:
                    self._speaking_tag = ""
            if item.generation != self._generation:
                # Interrompu : on ferme tout de suite, sans attendre la fin du son.
                self._finish(speaking, wait=False)
                speaking = False
        self._finish(speaking, wait=False)

    def _gather(self, first: _Item) -> str:
        parts = [first.text]
        size = len(first.text)
        while size < BATCH_CHARS:
            try:
                nxt = self._queue.get_nowait()
            except queue.Empty:
                break
            if nxt.generation != first.generation:
                continue
            if nxt.tag != first.tag:
                # Une phrase étiquetée n'est jamais fondue avec une autre : `cancel`
                # doit pouvoir la retirer seule. Elle sera dite juste après, à sa place.
                with self._player_lock:
                    self._carry = nxt
                break
            parts.append(nxt.text)
            size += len(nxt.text)
        return " ".join(parts)

    def _speak(self, text: str, generation: int) -> None:
        engine = self._active_engine()
        if engine is self._fallback:
            try:
                self._render(engine, text, generation)
            except Exception:
                log.exception("la voix de secours a échoué")
            return
        try:
            self._render_primary(engine, text, generation)
        except Exception as exc:
            if self._fallback is None:
                log.exception("synthèse impossible: %s", exc)
                return
            if generation != self._generation:
                return  # interrompu pendant les essais : plus rien à dire
            self._trip(engine, exc)
            try:
                self._render(self._fallback, text, generation)
            except Exception:
                log.exception("la voix de secours a échoué aussi")
            return
        if self._failures and self._emitted:
            self._recovered(engine)

    def _render_primary(self, engine: Engine, text: str, generation: int) -> None:
        """Le moteur principal, avec un ou deux nouveaux essais rapides sur
        erreur passagère. Jamais si du son est déjà sorti (il serait redit),
        jamais au retour d'un repos (une seule sonde, sans latence ajoutée)."""
        delays = RETRY_DELAYS_S if not self._failures else ()
        started = time.monotonic()
        attempt = 0
        while True:
            try:
                self._render(engine, text, generation)
                if attempt:
                    log.info("voix %s : réussie au %de essai", engine.name, attempt + 1)
                return
            except Exception as exc:
                if (attempt >= len(delays) or self._emitted or not is_transient(exc)
                        or generation != self._generation):
                    raise
                delay = delays[attempt]
                wait = retry_after(exc)
                if wait is not None and wait > delay:
                    raise  # le service demande d'attendre plus : le disjoncteur s'en charge
                if time.monotonic() - started + delay > RETRY_BUDGET_S:
                    raise
                attempt += 1
                log.info("voix %s : %s — nouvel essai dans %.1f s (%d/%d)",
                         engine.name, _short(exc), delay, attempt, len(delays))
                time.sleep(delay)
                if generation != self._generation:
                    raise

    def _trip(self, engine: Engine, exc: Exception) -> None:
        """Échec définitif : repos du principal, plus long à chaque rechute."""
        now = time.monotonic()
        if not self._failures:
            self._degraded_since = now
        self._failures += 1
        cooldown = min(COOLDOWN_MAX_S, COOLDOWN_BASE_S * 2 ** (self._failures - 1))
        wait = retry_after(exc)
        if wait is not None:
            cooldown = min(COOLDOWN_MAX_S, max(cooldown, wait))
        self._degraded_until = now + cooldown
        log.warning("voix %s en échec (%s) — bascule sur %s, nouvel essai dans %.0f s (échec n°%d)",
                    engine.name, _short(exc), self._fallback.name, cooldown, self._failures)
        self._publish_voice()
        if self._failures == 1:
            bus.publish("notice", level="warning", text=f"Voix {engine.name} en panne, voix locale le temps que ça revienne.")

    def _recovered(self, engine: Engine) -> None:
        down = time.monotonic() - self._degraded_since
        log.info("voix %s de retour après %.0f s sur %s (%d échec(s) d'affilée)",
                 engine.name, down, self._fallback.name if self._fallback else "rien", self._failures)
        self._failures = 0
        self._degraded_until = 0.0
        self._publish_voice()
        bus.publish("notice", level="info", text=f"Voix {engine.name} revenue.")

    def _render(self, engine: Engine, text: str, generation: int) -> None:
        started = False
        self._emitted = False
        for pcm in engine.synthesize(text):
            if generation != self._generation:
                return
            if not pcm:
                continue
            started = self._emitted = True
            if not self._emit(engine.sample_rate, pcm, generation):
                return
        if not started:
            raise RuntimeError(f"{engine.name} n'a produit aucun son")

    def _emit(self, rate: int, pcm: bytes, generation: int) -> bool:
        """Joue un bloc, ou le garde pour la reprise si la voix est en pause
        (la synthèse de la phrase continue : rien n'est perdu). False si la
        parole a été interrompue."""
        tag = self._speaking_tag
        self._drain_if_rate_changes(rate, tag)
        with self._player_lock:
            if generation != self._generation:
                return False
            if self._paused.is_set() or self._held:
                self._held.append((rate, pcm, tag))
                player = None
            else:
                # Accepté sous le verrou : une pause qui tombe ensuite le compte
                # dans ce qu'elle coupe, jamais deux fois ni zéro fois.
                player = self._ensure_player(rate, tag)
                player.accept(pcm)
        if player is None:
            self._flush_held()
        else:
            player.push(pcm)
        return generation == self._generation

    def _flush_held(self) -> None:
        """Joue dans l'ordre le son gardé par la pause ; s'arrête sans rien
        perdre si la pause retombe."""
        while True:
            with self._player_lock:
                if self._paused.is_set() or not self._held:
                    return
                rate, tag = self._held[0][0], self._held[0][2]
            self._drain_if_rate_changes(rate, tag)
            with self._player_lock:
                if self._paused.is_set() or not self._held:
                    return
                rate, pcm, tag = self._held.pop(0)
                player = self._ensure_player(rate, tag)
                player.accept(pcm)
            player.push(pcm)

    def _ensure_player(self, rate: int, tag: str = "") -> Player:
        """Sous `_player_lock`. Un lecteur coupé (pause, interruption) est remplacé.
        Le lecteur porte l'étiquette de ce qu'il joue (`cancel` le retrouve tant que
        le son sort, synthèse finie ou non)."""
        if self._player is not None and (self._player.stopped or self._player.rate != rate
                                         or getattr(self._player, "tag", "") != tag):
            self._player.close(wait=False)
            self._player = None
        if self._player is None:
            self._player = Player(rate, on_level=lambda v: bus.publish("level", source="voice", value=round(v, 3)))
            self._player.tag = tag
        return self._player

    def _drain_if_rate_changes(self, rate: int, tag: str = "") -> None:
        """Changement de moteur (et de débit) en pleine parole, ou passage d'une
        phrase étiquetée à une autre (15/10 : un lecteur par étiquette, pour que
        `cancel` ne coupe qu'elle) : on laisse finir l'ancien lecteur avant
        d'ouvrir le nouveau, hors verrou pour qu'une pause puisse encore le couper."""
        with self._player_lock:
            old = self._player
            if old is None or old.stopped or (old.rate == rate and getattr(old, "tag", "") == tag):
                return
            self._player, self._closing = None, old
        old.close(wait=True)
        with self._player_lock:
            self._closing = None

    def _finish(self, speaking: bool, wait: bool = True) -> None:
        with self._player_lock:
            player, self._player = self._player, None
            self._closing = player
        if player is not None:
            player.close(wait=wait)  # une pause pendant l'attente coupe ce lecteur
        with self._player_lock:
            self._closing = None
            nothing_left = self._queue.empty() and not self._held and self._carry is None
        if speaking and self._on_activity:
            self._on_activity(False)
        if nothing_left:
            self._idle.set()
