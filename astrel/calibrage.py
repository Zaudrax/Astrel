"""Calibrage d'un micro : mesure du bruit de fond puis de la voix, profil proposé (02/11, demande de
l'utilisateur : un assistant pas à pas dans le HUD, en plus de `./run.sh --calibrate`).

La logique de mesure est celle de `--calibrate` (28/09), sortie de `cli.calibrate_mode` telle quelle :
- micro à plusieurs canaux : gardés ceux où le VAD reconnaît la voix (part ≥ 60 % du meilleur) ;
- gain : voix normale autour de `TARGET_P90` (RMS 16 bits, 90e centile), jamais de saturation sur le
  plus fort pic, borné à [0,5 ; 12] ;
- seuil VAD : au milieu entre le pire bruit et la parole typique, borné à [0,25 ; 0,5].
Le seuil de l'ANCIEN mot d'éveil (`wake`, modèle hey_jarvis) n'est JAMAIS proposé autrement : les faux
réveils se traitent par un « custom verifier », pas par ce seuil (CLAUDE.md, piège 5). L'assistant compte
seulement combien de « Astrel » la voie texte reconnaît, avec l'ancien réglage et avec le proposé.

Ici : la mesure (`analyser`), la capture brute de tous les canaux (`ouvrir_flux`, `lire`), le compte du
nom (`compter_nom`) et une séance de l'assistant (`Seance`, fil à part, évènements `calibrage`). Rien
n'est écrit : l'enregistrement est fait par le cœur, après le clic « Enregistrer » de l'utilisateur.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import audio, config

log = logging.getLogger("astrel.calibrage")

TARGET_P90 = 2500.0       # RMS visé pour une voix normale (×4 saturait la voix sur le micro USB)
GAIN_MIN, GAIN_MAX = 0.5, 12.0
VAD_MIN, VAD_MAX = 0.25, 0.5
PIC_MAX = 30000.0         # le plus fort pic de la voix, une fois le gain appliqué
PAROLE = 0.5              # probabilité du VAD comptée comme de la voix
PART_CANAL = 0.6          # canal gardé : au moins 60 % de la voix reconnue par le meilleur
# Assistant du HUD (02/11) : 5 s de silence, puis 10 s pour dire « Astrel » trois fois.
BRUIT_S = 5.0
PAROLE_S = 10.0
ESSAIS_NOM = 3
AUCUN_CANAL = "Aucun canal où je reconnais ta voix : micro coupé, mauvaise source, ou trop loin ?"
PRESQUE_RIEN = "Je n'ai presque rien entendu de ta voix : micro coupé, mauvaise source, ou trop loin ?"
TROP_FAIBLE = ("Voix à peine plus forte que le bruit de fond : monter le gain ne servira pas, rapproche le "
               "micro ou change de micro.")


class Annule(Exception):
    """Séance annulée (bouton Annuler, Stop tout, nouveau calibrage)."""


@dataclass
class Mesure:
    """Résultat de `analyser`. `erreur` non vide : rien à proposer."""
    n: int = 1
    canaux: list[int] = field(default_factory=list)  # canaux gardés (vide : micro à un canal)
    parts: list[float] = field(default_factory=list)  # voix reconnue, par canal (micro à plusieurs canaux)
    gain: float = 0.0
    vad: float = 0.0
    bruit: float = 0.0           # RMS brut du bruit de fond (90e centile)
    bruit_vad_max: float = 0.0
    voix: float = 0.0            # RMS brut de la voix (90e centile)
    pic: float = 0.0
    voix_vad_mediane: float = 0.0
    erreur: str = ""

    @property
    def rapport(self) -> float:
        return self.voix / max(self.bruit, 1.0)

    @property
    def faible(self) -> bool:
        """Voix à peine plus forte que le bruit : le gain n'y peut rien."""
        return self.rapport < 2


def rms_trames(x: np.ndarray) -> np.ndarray:
    """RMS de chaque trame (x : trames × échantillons)."""
    return np.sqrt((x ** 2).mean(axis=1))


def probas(vad, x: np.ndarray) -> np.ndarray:
    """Probabilité de parole de chaque trame (x : trames × échantillons, flottants), VAD remis à zéro.
    `vad` : objet à `reset_states()` et `predict(int16)` (openwakeword.vad.VAD, ou faux en test)."""
    vad.reset_states()
    clipped = np.clip(x, -32768, 32767).astype(np.int16)
    return np.array([float(vad.predict(f)) for f in clipped]) if len(clipped) else np.zeros(0)


def analyser(noise: np.ndarray, speech: np.ndarray, probs_of: Callable[[np.ndarray], np.ndarray]) -> Mesure:
    """Mesure de `--calibrate`. `noise`, `speech` : trames × échantillons × canaux (flottants, gain 1) ;
    `probs_of(x)` : probabilités du VAD des trames x (trames × échantillons)."""
    n = int(speech.shape[2]) if speech.ndim == 3 else 1
    m = Mesure(n=n)
    if n > 1:
        for c in range(n):
            x = speech[:, :, c]
            m.parts.append(float((probs_of(x) >= PAROLE).mean()) if np.abs(x).max() > 0 else 0.0)
        best = max(m.parts)
        if best > 0:
            m.canaux = [c for c in range(n) if m.parts[c] >= PART_CANAL * best]
        if not m.canaux:
            m.erreur = AUCUN_CANAL
            return m
    pick = m.canaux or [0]
    speech_mix = speech[:, :, pick].mean(axis=2)
    noise_mix = noise[:, :, pick].mean(axis=2)
    n_rms, s_rms = rms_trames(noise_mix), rms_trames(speech_mix)
    n_prob, s_prob = probs_of(noise_mix), probs_of(speech_mix)
    s_peak = np.abs(speech_mix).max(axis=1)
    # La voix, c'est ce que le VAD reconnaît ; à défaut (micro très bruyant), ce qui dépasse le bruit de fond.
    voiced = s_prob >= PAROLE
    if voiced.sum() < 5:
        voiced = s_rms > np.percentile(n_rms, 90) * 1.3
    if voiced.sum() < 5:
        m.erreur = PRESQUE_RIEN
        return m
    m.voix, m.pic = float(np.percentile(s_rms[voiced], 90)), float(s_peak[voiced].max())
    m.bruit = float(np.percentile(n_rms, 90))
    m.bruit_vad_max = float(n_prob.max()) if len(n_prob) else 0.0
    m.voix_vad_mediane = float(np.median(s_prob[voiced]))
    gain = TARGET_P90 / m.voix
    gain = min(gain, PIC_MAX / m.pic)  # jamais de saturation sur le plus fort pic
    m.gain = round(max(GAIN_MIN, min(gain, GAIN_MAX)), 1)
    # VAD : au milieu entre le pire bruit et la parole typique, borné comme les mesures du micro USB.
    vad = (float(np.percentile(n_prob, 95)) + float(np.percentile(s_prob[voiced], 25))) / 2
    m.vad = round(float(min(VAD_MAX, max(VAD_MIN, vad))), 2)
    return m


# --------------------------------------------------------------------------- capture brute

def ouvrir_flux(source: str, n: int):
    """Capture de TOUS les canaux du micro, gain 1, à côté de l'écoute d'Astrel (PipeWire permet
    plusieurs lecteurs). `pulse` : parecord ; autres moteurs : `platform.son` (fichier en test)."""
    eng = audio.engine()
    if eng != "pulse":
        from .platform import son

        return son.open_capture(eng, config.SAMPLE_RATE, n, source)
    cmd = ["parecord", f"--rate={config.SAMPLE_RATE}", f"--channels={n}", "--format=s16le", "--raw",
           "--latency-msec=40", "--client-name=Astrel", "--stream-name=calibrage"] + (
        [f"--device={source}"] if source else [])
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)


def fermer_flux(proc) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def lire(proc, n: int, seconds: float, on_frame: Callable[[int, int, np.ndarray], None] | None = None,
         stop: threading.Event | None = None) -> np.ndarray:
    """`seconds` de son : trames × échantillons × canaux, en flottants. `on_frame(i, total, trame)` après
    chaque trame (vumètre) ; `stop` posé : `Annule`."""
    size = config.FRAME_BYTES * n
    total = int(seconds * config.SAMPLE_RATE / config.FRAME_SAMPLES)
    frames = []
    for i in range(total):
        if stop is not None and stop.is_set():
            raise Annule()
        buf = proc.stdout.read(size)
        if len(buf) < size:
            raise IOError("flux microphone interrompu")
        frame = np.frombuffer(buf, dtype=np.int16).reshape(-1, n).astype(np.float64)
        frames.append(frame)
        if on_frame is not None:
            on_frame(i, total, frame)
    return np.array(frames) if frames else np.zeros((0, config.FRAME_SAMPLES, n))


def melange(x: np.ndarray, canaux: list[int] | tuple[int, ...], gain: float) -> np.ndarray:
    """Ce qu'entend Astrel avec ce réglage : canaux choisis moyennés (tous si aucun), gain, saturation
    à 16 bits (trames × échantillons, flottants)."""
    pick = [c for c in canaux if c < x.shape[2]] or list(range(x.shape[2]))
    return np.clip(x[:, :, pick].mean(axis=2) * gain, -32768, 32767)


def compter_nom(x: np.ndarray, vad_seuil: float, probs_of: Callable[[np.ndarray], np.ndarray],
                transcribe: Callable[[bytes, str], str]) -> int:
    """Combien de fois la voie texte du mot d'éveil (`listener._TextWake`, mêmes captures et mêmes noms
    qu'en veille) reconnaît le nom dans `x` (trames × échantillons, réglage déjà appliqué)."""
    from .listener import _TextWake

    wake = _TextWake(transcribe, sync=True)
    frame_s = config.FRAME_SAMPLES / config.SAMPLE_RATE
    probs = probs_of(x)
    frames = np.clip(x, -32768, 32767).astype(np.int16)
    count = 0
    for i, (f, p) in enumerate(zip(frames, probs)):
        if wake.feed(f.tobytes(), float(p), vad_seuil, i * frame_s):
            count += 1
    # Fin du son : un blanc pour clore la dernière capture (le nom dit tout à la fin).
    blank = np.zeros(config.FRAME_SAMPLES, dtype=np.int16).tobytes()
    for k in range(int(config.WAKE_TEXT_END_S / frame_s) + 2):
        if wake.feed(blank, 0.0, vad_seuil, (len(frames) + k) * frame_s):
            count += 1
    return count


# --------------------------------------------------------------------------- séance de l'assistant

def _profil(p) -> dict:
    return {"gain": round(float(p.gain), 2), "vad": round(float(p.vad), 2), "wake": round(float(p.wake), 2),
            "canaux": list(p.channels), "cle": p.key}


class Seance:
    """Un calibrage de l'assistant du HUD, dans un fil : silence (`bruit_s`), puis le nom trois fois
    (`parole_s`), analyse, compte du nom, proposition. Chaque étape publie `calibrage` par `publier`
    ({etat, …}) ; `debut()` / `fin()` : appelés autour des MESURES (le cœur y suspend puis rétablit
    l'écoute ; `fin` est toujours appelé, même sur erreur ou annulation).

    `ancien` : MicProfile en vigueur ; `vad` : fabrique d'un VAD neuf ; `transcribe` : Whisper du mot
    d'éveil (None : nom non compté) ; `flux` : (source, n) → capture (tests)."""

    def __init__(self, source: str, label: str, n: int, ancien, publier: Callable[..., None],
                 vad: Callable[[], object], transcribe: Callable[[bytes, str], str] | None = None,
                 debut: Callable[[], None] | None = None, fin: Callable[[], None] | None = None,
                 bruit_s: float = BRUIT_S, parole_s: float = PAROLE_S, flux=None) -> None:
        self.source, self.label, self.n, self.ancien = source, label, max(1, int(n)), ancien
        self._publier, self._vad_factory, self._transcribe = publier, vad, transcribe
        self._debut, self._fin = debut or (lambda: None), fin or (lambda: None)
        self.bruit_s, self.parole_s = float(bruit_s), float(parole_s)
        self._flux = flux or ouvrir_flux
        self._stop = threading.Event()
        self._proc = None  # capture en cours : fermée par `annuler` (une lecture bloquée rend alors la main)
        self.etat = "bruit"
        self.mesure: Mesure | None = None
        self.proposition: dict | None = None
        self.raison = ""
        self._thread: threading.Thread | None = None

    @property
    def mesure_en_cours(self) -> bool:
        return self.etat in ("bruit", "parole", "analyse")

    @property
    def vivante(self) -> bool:
        """Le fil de la séance tourne encore (il n'a peut-être pas encore appelé `fin`)."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="calibrage", daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def annuler(self, raison: str = "") -> bool:
        """Arrête la séance ; vrai si elle mesurait encore (sinon la proposition est seulement jetée)."""
        self.raison = raison
        mesurait = self.mesure_en_cours
        self._stop.set()
        proc = self._proc
        if proc is not None:
            # Relecture du 02/11 : `lire` peut être bloqué sur le micro (parecord qui cale) ; fermer la capture
            # lui rend la main (fin de flux), et le fil passe par `fin` (écoute rétablie).
            fermer_flux(proc)
        if not mesurait and self.etat != "annule":
            self.etat = "annule"
            self.proposition = None
            self._pub("annule", raison=raison)
        return mesurait

    def _pub(self, etat: str, **data) -> None:
        self.etat = etat
        try:
            self._publier(etat=etat, micro=self.label or self.source or "micro du système", **data)
        except Exception:
            log.exception("calibrage : évènement non publié")

    def _run(self) -> None:
        proc = None
        vad = None
        try:
            try:
                self._debut()
                vad = self._vad_factory()
                proc = self._proc = self._flux(self.source, self.n)
                if self._stop.is_set():
                    raise Annule()
                self._pub("bruit", etape=1, duree_s=self.bruit_s, reste_s=self.bruit_s, niveau=0.0)
                noise = lire(proc, self.n, self.bruit_s, self._vumetre("bruit", 1, self.bruit_s), self._stop)
                self._pub("parole", etape=2, duree_s=self.parole_s, reste_s=self.parole_s, niveau=0.0,
                          essais=ESSAIS_NOM)
                speech = lire(proc, self.n, self.parole_s, self._vumetre("parole", 2, self.parole_s, vad),
                              self._stop)
            finally:
                self._proc = None
                if proc is not None:
                    fermer_flux(proc)
                self._fin()  # l'écoute revient dès la fin des mesures, pas après le choix de l'utilisateur
            self._pub("analyse")
            probs_of = (lambda x: probas(vad, x))
            m = analyser(noise, speech, probs_of)
            self.mesure = m
            if self._stop.is_set():
                raise Annule()
            if m.erreur:
                self._pub("erreur", erreur=m.erreur, parts=[round(p, 2) for p in m.parts], n_canaux=m.n)
                return
            canaux = m.canaux if m.n > 1 else []
            prop = {"gain": m.gain, "vad": m.vad, "wake": round(float(self.ancien.wake), 2), "canaux": canaux}
            nom = None
            if self._transcribe is not None:
                try:
                    vieux = list(self.ancien.channels) if m.n > 1 else []
                    nom = {"sur": ESSAIS_NOM,
                           "propose": compter_nom(melange(speech, canaux, m.gain), m.vad, probs_of, self._transcribe),
                           "ancien": compter_nom(melange(speech, vieux, float(self.ancien.gain)),
                                                 float(self.ancien.vad), probs_of, self._transcribe)}
                except Exception:
                    log.exception("calibrage : nom non compté")
                    nom = None
            if self._stop.is_set():
                raise Annule()
            self.proposition = prop
            self._pub("resultat", ancien=_profil(self.ancien), propose=prop, n_canaux=m.n,
                      parts=[round(p, 2) for p in m.parts], bruit=round(m.bruit), voix=round(m.voix),
                      pic=round(m.pic), rapport=round(m.rapport, 1), faible=m.faible,
                      avertissement=TROP_FAIBLE if m.faible else "", nom=nom)
            log.info("calibrage de %s : proposé %s (ancien %s), voix/bruit %.1f, nom %s",
                     self.label or self.source, prop, _profil(self.ancien), m.rapport, nom)
        except Annule:
            self.proposition = None
            if self.etat != "annule":
                self._pub("annule", raison=self.raison)
            log.info("calibrage annulé%s", f" ({self.raison})" if self.raison else "")
        except Exception as exc:
            self.proposition = None
            if self._stop.is_set():  # capture fermée par `annuler` : une annulation, pas une panne
                if self.etat != "annule":
                    self._pub("annule", raison=self.raison)
                log.info("calibrage annulé%s", f" ({self.raison})" if self.raison else "")
                return
            log.exception("calibrage impossible")
            self._pub("erreur", erreur=f"Mesure impossible : {exc}")

    def _vumetre(self, etat: str, etape: int, duree: float, vad=None):
        """Évènement de chaque trame : niveau perçu (0..1), temps restant, voix entendue (étape 2)."""
        frame_s = config.FRAME_SAMPLES / config.SAMPLE_RATE
        if vad is not None:
            vad.reset_states()

        def on_frame(i: int, total: int, frame: np.ndarray) -> None:
            mono = np.clip(frame.mean(axis=1), -32768, 32767)
            level = audio.level_from_rms(float(np.sqrt((mono ** 2).mean())))
            data = {"etape": etape, "duree_s": duree, "reste_s": round(max(0.0, (total - i - 1) * frame_s), 1),
                    "niveau": round(level, 3)}
            if vad is not None:
                data["parole"] = float(vad.predict(mono.astype(np.int16))) >= PAROLE
            self._pub(etat, **data)

        return on_frame


def attendre(seance: Seance, timeout: float = 60.0) -> None:
    """Attend la fin des mesures d'une séance (tests)."""
    deadline = time.monotonic() + timeout
    while seance.mesure_en_cours and time.monotonic() < deadline:
        time.sleep(0.02)
    seance.join(max(0.0, deadline - time.monotonic()))
