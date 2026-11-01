"""Mot de réveil (openWakeWord), enregistrement d'une phrase (VAD Silero) et
reconnaissance vocale (faster-whisper sur la carte graphique). Tout est local :
rien ne quitte la machine avant que la phrase soit devenue du texte.
"""
from __future__ import annotations

import logging
import pathlib
import threading
import time
from typing import Callable

import numpy as np

from . import config
from .audio import MicStream

log = logging.getLogger("astrel.listener")


def _preload_cuda_libs() -> None:
    """Charge les bibliothèques CUDA livrées par pip.

    ⚠️ Sur cette machine, libcublas/libcudnn n'existent QUE dans les paquets pip
    `nvidia-*`, pas dans le chargeur système. ctranslate2 les cherche pourtant
    par le chargeur : sans ce préchargement, `RuntimeError: Library
    libcublas.so.12 is not found`. À faire avant d'importer faster_whisper.
    """
    import ctypes
    import sysconfig

    site = pathlib.Path(sysconfig.get_paths()["purelib"]) / "nvidia"
    for rel in ("cublas/lib/libcublasLt.so.12", "cublas/lib/libcublas.so.12", "cudnn/lib/libcudnn.so.9"):
        lib = site / rel
        if lib.exists():
            try:
                ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
            except OSError as exc:
                log.debug("préchargement de %s impossible: %s", lib.name, exc)


def _setting(key: str, default):
    """Réglage de la page Réglages, `default` s'il manque ou est illisible (jamais d'erreur ici)."""
    try:
        from .settings import reglages

        value = reglages.value(key)
    except Exception:
        return default
    return default if value is None else value



# 29/10 (fluidité, profil py-spy du cœur au repos) : openwakeword 0.4.0 recopie, toutes les 80 ms,
# ses 10 s de son (160 000 échantillons) en liste Python pour n'en garder que la fin (~1 760) :
# ~40 % du temps de calcul du cœur micro ouvert. Même calcul ici sur la seule fin du tampon
# (mêmes échantillons, même type int16 : spectrogramme identique au bit près).
def _accelerer_openwakeword() -> None:
    from itertools import islice

    from openwakeword.utils import AudioFeatures

    if getattr(AudioFeatures, "_astrel_rapide", False):
        return

    def _streaming_melspectrogram(self, n_samples):
        fin = np.fromiter(islice(reversed(self.raw_data_buffer), n_samples + 160 * 3), dtype=np.int16)[::-1]
        self.melspectrogram_buffer = np.vstack((self.melspectrogram_buffer, self._get_melspectrogram(fin)))
        if self.melspectrogram_buffer.shape[0] > self.melspectrogram_max_len:
            self.melspectrogram_buffer = self.melspectrogram_buffer[-self.melspectrogram_max_len:, :]

    AudioFeatures._astrel_origine = AudioFeatures._streaming_melspectrogram  # gardée pour le test d'égalité
    AudioFeatures._streaming_melspectrogram = _streaming_melspectrogram
    AudioFeatures._astrel_rapide = True


def oww_model(path):
    """Modèle openwakeword (mot d'éveil) avec le tampon accéléré."""
    from openwakeword.model import Model

    _accelerer_openwakeword()
    return Model(wakeword_model_paths=[str(path)])

class _WakeWhisper:
    """Whisper DÉDIÉ au mot d'éveil (`config.WAKE_TEXT_MODEL`, « base ») : jamais celui des phrases,
    qui peut transcrire en même temps. Carte graphique, sinon processeur."""

    def __init__(self) -> None:
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        _preload_cuda_libs()
        from faster_whisper import WhisperModel

        try:
            self._model = WhisperModel(config.WAKE_TEXT_MODEL, device=config.STT_DEVICE,
                                       compute_type=config.STT_COMPUTE)
        except Exception as exc:
            log.warning("mot d'éveil : Whisper CUDA indisponible (%s), processeur int8", exc)
            self._model = WhisperModel(config.WAKE_TEXT_MODEL, device="cpu", compute_type="int8")

    def __call__(self, pcm: bytes, hotwords: str) -> str:
        self.load()
        assert self._model is not None
        audio_f32 = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _info = self._model.transcribe(
            audio_f32, language=stt_language(), beam_size=1, temperature=0.0, vad_filter=False,
            condition_on_previous_text=False, without_timestamps=True, hotwords=hotwords or None,
        )
        # Un segment que Whisper juge sans parole est une hallucination sur du bruit.
        return " ".join(s.text.strip() for s in segments if s.no_speech_prob < config.WAKE_TEXT_NO_SPEECH).strip()


class _TextWake:
    """Voie TEXTE du mot d'éveil (25/10, plan P36) : capture d'une phrase courte ouverte par le VAD
    (déjà calculé par l'appelant), transcription dans un fil à part (jamais dans la boucle du micro),
    nom cherché par `eveil.trouve`. `feed` rend le nom reconnu, sinon « ».

    `transcribe(pcm, hotwords) -> texte` : remplaçable (tests, banc). `sync` : transcription sur place,
    dans `feed` (banc de mesure au temps simulé : le délai compte alors la durée de la transcription)."""

    def __init__(self, transcribe: Callable[[bytes, str], str] | None = None, sync: bool = False) -> None:
        self._frame_s = config.FRAME_SAMPLES / config.SAMPLE_RATE
        self._transcribe = transcribe or _WakeWhisper()
        self._sync = sync
        self._pre: list[bytes] = []
        self._buf: list[bytes] | None = None
        self._speech_n = 0   # blocs depuis le début de la parole
        self._voiced_n = 0   # blocs au-dessus du seuil
        self._blank_n = 0
        self._skip = False   # capture coupée à sa durée maximale : on attend un blanc
        self._last_t: float | None = None
        self._gen = 0        # `reset` rend caduque une transcription en cours
        self._cv = threading.Condition()
        self._job: tuple[int, float, bytes] | None = None
        self._result: tuple[int, float, str] | None = None
        self._dead = False
        self.busy_s = 0.0    # durée de la dernière transcription (banc, `--diag`)
        if not sync:
            threading.Thread(target=self._worker, name="eveil-texte", daemon=True).start()

    # --- capture (fil du micro)

    def _clear(self) -> None:
        self._pre, self._buf = [], None
        self._speech_n = self._voiced_n = self._blank_n = 0
        self._skip = False

    def reset(self) -> None:
        with self._cv:
            self._gen += 1
            self._job = self._result = None
        self._clear()
        self._last_t = None

    def feed(self, frame: bytes, speech: float | None, vad: float, now: float) -> str:
        if self._last_t is not None and now - self._last_t > config.WAKE_TEXT_GAP_S:
            self._clear()  # micro sourd entre-temps (Astrel parlait) : ne pas recoller deux bouts
        self._last_t = now
        if speech is not None and not self._dead:
            self._capture(frame, float(speech), vad, now)
        return self._take(now)

    def _capture(self, frame: bytes, speech: float, vad: float, now: float) -> None:
        keep_on = end_threshold(vad)
        end_n = max(1, round(config.WAKE_TEXT_END_S / self._frame_s))
        if self._skip:
            self._blank_n = self._blank_n + 1 if speech < keep_on else 0
            if self._blank_n >= end_n:
                self._clear()
            return
        if self._buf is None:
            if speech >= vad:
                self._buf = [*self._pre, frame]
                self._pre = []
                self._speech_n = self._voiced_n = 1
                self._blank_n = 0
            else:
                self._pre.append(frame)
                pre_n = max(0, round(config.WAKE_TEXT_PREROLL_S / self._frame_s))
                del self._pre[:-pre_n or len(self._pre)]
            return
        self._buf.append(frame)
        self._speech_n += 1
        self._voiced_n += speech >= vad
        self._blank_n = 0 if speech >= keep_on else self._blank_n + 1
        if self._blank_n >= end_n:
            self._close(now, skip=False)
        elif self._speech_n * self._frame_s >= config.WAKE_TEXT_MAX_S - 1e-6:
            self._close(now, skip=True)

    def _close(self, now: float, skip: bool) -> None:
        pcm, voiced_s = b"".join(self._buf or ()), self._voiced_n * self._frame_s
        self._clear()
        self._skip = skip
        if voiced_s < config.WAKE_TEXT_MIN_S - 1e-6:
            return
        with self._cv:
            job = (self._gen, now, pcm)
            if self._sync:
                self._job = None
            else:
                self._job = job  # la plus récente seulement : une ancienne capture n'a plus d'intérêt
                self._cv.notify()
        if self._sync:
            self._run(job)

    def _take(self, now: float) -> str:
        with self._cv:
            result, self._result = self._result, None
            gen = self._gen
        if result is None or result[0] != gen:
            return ""
        _gen, t_end, name = result
        age = now - t_end
        if age > config.WAKE_TEXT_MAX_AGE_S:
            log.info("mot d'éveil « %s » reconnu trop tard (%.1f s après la phrase) : ignoré", name, age)
            return ""
        return name

    # --- transcription (fil à part)

    def _run(self, job: tuple[int, float, bytes]) -> None:
        from . import eveil

        gen, t_end, pcm = job
        t0 = time.monotonic()
        noms = eveil.noms_actuels()
        try:
            text = self._transcribe(pcm, eveil.mots_indices(noms))
        except Exception:
            log.exception("mot d'éveil : transcription impossible")
            return
        finally:
            self.busy_s = time.monotonic() - t0
        name = eveil.trouve(text, noms)
        if self._sync:
            t_end += self.busy_s  # temps simulé : le résultat arrive après la transcription
        if name:
            with self._cv:
                if gen == self._gen:
                    self._result = (gen, t_end, name)
        # Jamais le texte entendu dans le journal : l'écoute est continue (accord de l'utilisateur, P36).
        log.debug("capture de mot d'éveil transcrite en %.2f s : %s", self.busy_s, "nom reconnu" if name else "rien")

    def _worker(self) -> None:
        try:
            load = getattr(self._transcribe, "load", None)
            if load is not None:
                load()  # préchargé : la première phrase n'attend pas le modèle
        except Exception:
            log.exception("mot d'éveil par le texte indisponible (Whisper ne se charge pas)")
            self._dead = True
            return
        while True:
            with self._cv:
                while self._job is None:
                    self._cv.wait()
                job, self._job = self._job, None
            self._run(job)


WAKE_MODEL_MODES = ("off", "ou", "confirme")


class _ModelWake:
    """Voie MODÈLE (27/10, plan P36 lot 6) : modèle openWakeWord entraîné pour « Astrel »
    (`config.WAKE_MODEL_FILE`), son propre `Model` (l'alias `hey_jarvis` n'est pas touché), processeur.
    `feed` rend True quand `patience` blocs de suite dépassent le seuil ; `recent_max(s)` : meilleur
    score des `s` dernières secondes (confirmation de la voie texte).

    `model` : objet à `predict(pcm) -> {nom: score}` et `reset()` (tests) ; sinon le fichier."""

    def __init__(self, path: pathlib.Path, threshold: float, patience: int, model=None) -> None:
        from collections import deque

        if model is None:
            model = oww_model(path)
        self._model = model
        self._threshold, self._patience = threshold, max(1, int(patience))
        self._hits = 0
        self.score = 0.0
        frame_s = config.FRAME_SAMPLES / config.SAMPLE_RATE
        self._recent: deque[float] = deque(maxlen=max(1, round(config.WAKE_MODEL_CONFIRM_S / frame_s)))

    def feed(self, frame: bytes) -> bool:
        scores = self._model.predict(np.frombuffer(frame, dtype=np.int16))
        self.score = float(max(scores.values())) if scores else 0.0
        self._recent.append(self.score)
        self._hits = self._hits + 1 if self.score >= self._threshold else 0
        if self._hits < self._patience:
            return False
        self._hits = 0
        return True

    def recent_max(self, seconds: float) -> float:
        frame_s = config.FRAME_SAMPLES / config.SAMPLE_RATE
        n = max(1, round(seconds / frame_s))
        return max(list(self._recent)[-n:], default=0.0)

    def reset(self) -> None:
        self._model.reset()
        self._hits = 0
        self._recent.clear()


class WakeWord:
    """Mot d'éveil, deux voies (25/10, plan P36), la première qui parle gagne :
    - TEXTE (`_TextWake`) : le nom affiché (« Astrel ») ou une variante, dans une phrase courte ;
    - ALIAS : le modèle audio `hey_jarvis` d'openWakeWord, INCHANGÉ (seuil, patience), si le réglage
      `wake_legacy` est vrai (relu toutes les 2 s : appliqué à chaud).
    + voie MODÈLE (27/10, plan P36 lot 6, `_ModelWake`), ÉTEINTE par défaut (`config.WAKE_MODEL_MODE`) :
      « ou » = une voie de plus ; « confirme » = la voie texte ne réveille que si le modèle a aussi
      entendu le nom.
    Interface : `feed(frame, threshold=None, speech=None, vad=None)`, `reset()`, `last_score`
    (alias), `model_score` (voie modèle), `last_source` (« texte » / « alias » / « modele »),
    `last_name` (nom reconnu).

    `legacy`, `text`, `model_mode` : forcent une voie (banc, tests) ; `model` : faux modèle (tests) ;
    `transcribe`, `clock`, `sync` : voir `_TextWake` (horloge simulée du banc, sinon `time.monotonic`)."""

    def __init__(self, legacy: bool | None = None, text: bool | None = None,
                 transcribe: Callable[[bytes, str], str] | None = None,
                 clock: Callable[[], float] | None = None, sync: bool = False,
                 model_mode: str | None = None, model=None) -> None:
        self._clock = clock or time.monotonic
        self._legacy_forced = legacy
        self._legacy_cache: tuple[float, bool] = (float("-inf"), False)
        self._model = None
        self._hits = 0
        self._last_fire = float("-inf")
        self.last_score = 0.0
        self.last_source = ""
        self.last_name = ""
        self._text = _TextWake(transcribe, sync) if (config.WAKE_TEXT if text is None else text) else None
        self._mw: _ModelWake | None = None
        self.model_mode = self._start_model(config.WAKE_MODEL_MODE if model_mode is None else model_mode, model)
        self._legacy_prev = self._legacy_on(self._clock())
        if self._legacy_prev:
            self._alias()
        if self._text is None and not self._legacy_prev and self.model_mode != "ou":
            log.warning("mot d'éveil : aucune voie active (voie texte et alias coupés) ; réveil exprès seulement")

    def _start_model(self, mode: str, model) -> str:
        """Voie modèle : « off » si coupée, inconnue, ou si le modèle ne se charge pas (dit au journal)."""
        mode = (mode or "off").strip().lower()
        if mode not in WAKE_MODEL_MODES:
            log.warning("mot d'éveil : voie modèle « %s » inconnue (off, ou, confirme) : coupée", mode)
            return "off"
        if mode == "off":
            return mode
        if mode == "confirme" and self._text is None:
            log.warning("mot d'éveil : voie modèle « confirme » sans voie texte : coupée")
            return "off"
        if model is None and not config.WAKE_MODEL_FILE.exists():
            log.warning("mot d'éveil : modèle entraîné absent (%s) : voie modèle coupée", config.WAKE_MODEL_FILE)
            return "off"
        try:
            self._mw = _ModelWake(config.WAKE_MODEL_FILE, config.WAKE_MODEL_THRESHOLD,
                                  config.WAKE_MODEL_PATIENCE, model=model)
        except Exception:
            log.exception("mot d'éveil : modèle entraîné illisible : voie modèle coupée")
            return "off"
        log.info("mot d'éveil : voie modèle « %s » (seuil %.2f)", mode, config.WAKE_MODEL_THRESHOLD)
        return mode

    @property
    def model_score(self) -> float:
        """Dernier score de la voie modèle (0 si elle est coupée)."""
        return self._mw.score if self._mw is not None else 0.0

    @property
    def legacy(self) -> bool:
        """L'alias `hey_jarvis` (ancien mot d'éveil) est-il écouté (dernier état lu du réglage `wake_legacy`) ?"""
        return self._legacy_prev

    @property
    def deaf(self) -> bool:
        """Aucune voie n'écoute : voie texte absente ou morte (Whisper ne se charge pas) ET alias coupé
        (dernier état lu). Le cœur le signale par une notice (relecture P36, fiche 5) : sinon le réveil
        par la voix s'arrête sans prévenir. La voie modèle « ou » écoute seule ; « confirme » non."""
        return ((self._text is None or self._text._dead) and not self._legacy_prev
                and not (self.model_mode == "ou" and self._mw is not None))

    def _legacy_on(self, now: float) -> bool:
        if self._legacy_forced is not None:
            return self._legacy_forced
        when, on = self._legacy_cache
        if now - when >= 2.0:
            on = bool(_setting("wake_legacy", config.WAKE_LEGACY))
            self._legacy_cache = (now, on)
        return on

    def _alias(self):
        if self._model is None:
            self._model = oww_model(config.oww_model(config.WAKE_MODEL_NAME))
        return self._model

    def reset(self) -> None:
        if self._model is not None:
            self._model.reset()
        self._hits = 0
        if self._text is not None:
            self._text.reset()
        if self._mw is not None:
            self._mw.reset()

    def _feed_alias(self, frame: bytes, threshold: float | None) -> bool:
        """Le modèle `hey_jarvis` tel qu'avant P36 (mêmes seuils mesurés)."""
        pcm = np.frombuffer(frame, dtype=np.int16)
        scores = self._alias().predict(pcm)
        score = max(scores.values()) if scores else 0.0
        self.last_score = float(score)
        self._hits = self._hits + 1 if score >= (config.WAKE_THRESHOLD if threshold is None else threshold) else 0
        if self._hits < config.WAKE_PATIENCE:
            return False
        self._hits = 0
        return True

    def feed(self, frame: bytes, threshold: float | None = None, speech: float | None = None,
             vad: float | None = None) -> bool:
        """Consomme un bloc de 80 ms. Retourne True au déclenchement.
        `threshold` : seuil de l'alias pour le micro en cours (profil), sinon le seuil global.
        `speech` : probabilité de parole du bloc (VAD de l'appelant, calculée UNE fois) ; sans elle,
        la voie texte ne capte rien. `vad` : seuil du VAD du micro (défaut `VAD_SPEECH_THRESHOLD`)."""
        now = self._clock()
        source = ""
        legacy = self._legacy_on(now)
        if legacy and not self._legacy_prev and self._model is not None:
            self._model.reset()  # rallumé à chaud : pas de vieux son dans le tampon du modèle
            self._hits = 0
        self._legacy_prev = legacy
        if legacy:
            if self._feed_alias(frame, threshold):
                source = "alias"
        else:
            self.last_score = 0.0
        model_fired = self._mw.feed(frame) if self._mw is not None else False
        if self._text is not None:
            name = self._text.feed(frame, speech, config.VAD_SPEECH_THRESHOLD if vad is None else vad, now)
            if name and not source:
                if self.model_mode == "confirme" and self._mw is not None and \
                        self._mw.recent_max(config.WAKE_MODEL_CONFIRM_S) < config.WAKE_MODEL_CONFIRM_THRESHOLD:
                    log.info("nom reconnu par le texte, non confirmé par le modèle (meilleur score %.2f) : ignoré",
                             self._mw.recent_max(config.WAKE_MODEL_CONFIRM_S))
                else:
                    source, self.last_name = "texte", name
        if model_fired and not source and self.model_mode == "ou":
            from .i18n import assistant_name

            source, self.last_name = "modele", assistant_name()
        if not source:
            return False
        if now - self._last_fire < config.WAKE_COOLDOWN_S:
            return False
        self._last_fire = now
        self.last_source = source
        self.reset()  # la même phrase ne réveille pas deux fois (l'autre voie l'écoutait aussi)
        if source == "alias":
            log.info("mot de réveil détecté (alias hey_jarvis, score=%.2f)", self.last_score)
        elif source == "modele":
            log.info("mot d'éveil reconnu par le modèle entraîné (« %s »)", self.last_name)
        else:
            log.info("mot d'éveil reconnu (« %s »)", self.last_name)
        return True


def end_threshold(threshold: float) -> float:
    """Sous ce niveau, un bloc compte comme du blanc une fois la phrase
    commencée (hystérésis recommandée par Silero : seuil − 0,15)."""
    return max(0.05, threshold - config.VAD_END_MARGIN)


class VoiceRecorder:
    """Enregistre une phrase : attend la parole, s'arrête au silence."""

    def __init__(self) -> None:
        from openwakeword.vad import VAD

        self._vad = VAD(str(config.oww_model(config.VAD_MODEL_NAME)))

    def speech_prob(self, frame: bytes) -> float:
        return float(self._vad.predict(np.frombuffer(frame, dtype=np.int16)))

    def record(
        self,
        mic: MicStream,
        initial_timeout: float | None = None,
        abort: threading.Event | None = None,
        on_voice: Callable[[], None] | None = None,
    ) -> bytes:
        """`abort` : un événement qui interrompt l'écoute (réponse cliquée sur le HUD).
        `on_voice` : appelé à chaque bloc où l'utilisateur parle (`floor.heard` : rien
        de non demandé n'est dit tant qu'il parle, ni juste après).

        Fin de phrase (réglages et mesures : `config`, « Détection de fin de
        phrase ») : la parole COMMENCE au seuil du micro, mais ne s'arrête
        qu'après un vrai blanc, sous le seuil moins une marge, qui dure plus
        longtemps quand l'utilisateur explique que quand il lance un ordre court."""
        self._vad.reset_states()
        limit = config.VAD_INITIAL_TIMEOUT_S if initial_timeout is None else initial_timeout
        frame_s = config.FRAME_SAMPLES / config.SAMPLE_RATE
        chunks: list[bytes] = []
        speech_started = False
        silence_s = 0.0
        waited = 0.0     # attente du début de la parole
        speech_s = 0.0   # durée de la phrase depuis son début (garde-fou)
        voiced_s = 0.0   # parole reconnue : décide du blanc toléré
        peak_prob = 0.0
        threshold = mic.profile.vad
        keep_on = end_threshold(threshold)

        while True:
            if abort is not None and abort.is_set():
                return b""
            frame = mic.read_frame()
            if mic.muted:
                # Astrel parle : ce qu'on capte est sa propre voix, pas l'utilisateur.
                if not speech_started:
                    # Le délai repart de zéro après sa phrase : l'utilisateur doit
                    # avoir le temps de répondre.
                    chunks.clear()
                    waited = 0.0
                # l'utilisateur avait commencé : ce qu'il a dit est GARDÉ, et l'écoute
                # reprend après la phrase d'Astrel. Avant, tout était jeté et
                # l'utilisateur perdait le début de ce qu'il expliquait.
                silence_s = 0.0
                continue
            prob = self.speech_prob(frame)
            peak_prob = max(peak_prob, prob)
            if on_voice is not None and prob >= (keep_on if speech_started else threshold):
                on_voice()

            if not speech_started:
                waited += frame_s
                if prob >= threshold:
                    speech_started = True
                    chunks.append(frame)
                    speech_s = voiced_s = frame_s
                elif waited >= limit:
                    log.info("aucune parole en %.0fs (VAD max=%.2f, seuil=%.2f)",
                             waited, peak_prob, threshold)
                    return b""
                else:
                    # Garde ~400 ms avant le début de parole : la première syllabe.
                    chunks.append(frame)
                    if len(chunks) > 5:
                        chunks.pop(0)
                continue

            chunks.append(frame)
            speech_s += frame_s
            if prob >= threshold:
                voiced_s += frame_s
            silence_s = 0.0 if prob >= keep_on else silence_s + frame_s
            tolerated = config.VAD_SILENCE_S if voiced_s < config.VAD_LONG_AFTER_S else config.VAD_SILENCE_LONG_S
            if silence_s >= tolerated - 1e-6:  # somme de blocs de 80 ms : pas d'erreur d'arrondi
                log.info("phrase de %.1fs (parole %.1fs), close après %.1fs de blanc", speech_s, voiced_s, silence_s)
                return b"".join(chunks)
            if speech_s >= config.VAD_MAX_UTTERANCE_S - 1e-6:
                log.warning("phrase coupée à la durée maximale (%.0fs) : bruit continu ?", speech_s)
                return b"".join(chunks)


def stt_language() -> str:
    """Langue parlée, choisie dans la page « Réglages » du HUD (17/10, plan P4) ; lue à
    chaque phrase. Défaut : `config.STT_LANGUAGE`."""
    try:
        from .settings import reglages

        return str(reglages.value("stt_language") or config.STT_LANGUAGE)
    except Exception:
        return config.STT_LANGUAGE


def stt_prompt() -> str:
    """Lexique pour Whisper : nom affiché (« Astrel », plan P36 : sinon « Astral ») + réglage + noms
    des projets (ce qu'on nomme à l'oral)."""
    from .i18n import assistant_name

    words = [assistant_name()] + [w.strip() for w in config.STT_HINTS.split(",") if w.strip()]
    try:
        words += [p.name for p in config.WORKDIR.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))]
    except OSError:
        pass
    seen: dict[str, None] = {}
    for w in words:
        seen.setdefault(w, None)
    return "Vocabulaire : " + ", ".join(seen) + "."


class Transcriber:
    def __init__(self) -> None:
        self._model = None
        self.device = ""
        # Lexique recalculé à chaque phrase (`stt_prompt`, ≈ 0,1 ms) : `assistant_name` se change à chaud
        # (relecture P36, fiche 5). Une chaîne posée ici le remplace (tools/banc_eveil.py : "" = sans lexique).
        self._prompt: str | None = None

    def load(self) -> None:
        if self._model is not None:
            return
        _preload_cuda_libs()
        from faster_whisper import WhisperModel

        try:
            self._model = WhisperModel(config.STT_MODEL, device=config.STT_DEVICE, compute_type=config.STT_COMPUTE)
            self.device = config.STT_DEVICE
        except Exception as exc:  # pas de CUDA disponible → CPU
            log.warning("reconnaissance vocale CUDA indisponible (%s), bascule sur CPU int8", exc)
            self._model = WhisperModel(config.STT_MODEL, device="cpu", compute_type="int8")
            self.device = "cpu"

    def transcribe(self, pcm: bytes) -> str:
        if not pcm:
            return ""
        self.load()
        assert self._model is not None
        audio_f32 = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _info = self._model.transcribe(
            audio_f32,
            language=stt_language(),
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
            initial_prompt=stt_prompt() if self._prompt is None else self._prompt,
        )
        return " ".join(s.text.strip() for s in segments).strip()
