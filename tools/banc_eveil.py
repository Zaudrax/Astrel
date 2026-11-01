#!/usr/bin/env python3
"""Banc de mesure du mot d'éveil « Astrel » (25/10, plan P36, lot 2), SANS micro humain.

Voix de synthèse (Piper local, deux locuteurs ; edge-tts : fr-FR, fr-CA, fr-BE, fr-CH, en-US, en-GB),
vitesse ±10 %, bruit blanc et babil à 20, 10 et 5 dB, réverbération synthétique. Chaque extrait est
rejoué par blocs de 80 ms dans le VRAI `listener.WakeWord` (voie texte, Whisper dédié) avec le VRAI VAD
(`VoiceRecorder.speech_prob`), au temps SIMULÉ (`sync=True`) : le délai mesuré compte la durée réelle
de chaque transcription. Les négatifs (phrases FR/EN, « Estelle », « Astérix », « Mistral », « astres »)
forment un flux continu ; l'alias `hey_jarvis` y est aussi compté, pour comparaison.

    .venv/bin/python tools/banc_eveil.py                    # mesure complète (négatifs ≈ 2 h)
    .venv/bin/python tools/banc_eveil.py --rapide           # quelques voix, 10 min de négatifs
    .venv/bin/python tools/banc_eveil.py --sans-edge        # Piper seul (aucun réseau)
    .venv/bin/python tools/banc_eveil.py --pret             # « Astrel est prêt » par la voix Rémy, retranscrit
    .venv/bin/python tools/banc_eveil.py --voie modele --sortie /tmp/banc-eveil/modele   # lot 6 : modèle seul
    .venv/bin/python tools/banc_eveil.py --voie confirme --sortie /tmp/banc-eveil/confirme

Voies (27/10, plan P36 lot 6) : `texte` (défaut, la mesure d'avant), `modele` (modèle entraîné
`models/eveil/astrel.onnx` seul, sans Whisper), `ou`, `confirme` (modes de `ASTREL_WAKE_MODEL`). Dès que
la voie modèle tourne, ses scores sont gardés et une GRILLE de seuils du modèle seul est ajoutée au rapport.

Sorties : /tmp/banc-eveil/ (cache des voix, `rapport.json`, `rapport.txt`). Rien dans runtime/.
Cibles du plan : rappel ≥ 90 % propre, ≥ 75 % à 10 dB, ≤ 1 faux réveil par heure, délai < 1,2 s.
edge-tts envoie le texte des phrases à Microsoft (comme la voix d'Astrel) ; `--sans-edge` l'évite.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import statistics
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ASTREL_ASSISTANT_NAME", "Astrel")  # mesure du nom par défaut, quel que soit le réglage

from astrel import config  # noqa: E402

OUT = Path("/tmp/banc-eveil")
CACHE = OUT / "voix"
RATE = config.SAMPLE_RATE
FRAME = config.FRAME_SAMPLES
FRAME_S = FRAME / RATE
LEVEL_RMS = 2500.0  # voix normale au micro, après gain (cible de `--calibrate`)

POSITIFS = ["Astrel.", "Hey Astrel.", "Astrel, quelle heure est-il ?"]
PIPER = [("piper:jessica", 0), ("piper:pierre", 1)]
EDGE = ["fr-FR-RemyMultilingualNeural", "fr-FR-VivienneMultilingualNeural", "fr-FR-DeniseNeural",
        "fr-FR-HenriNeural", "fr-CA-AntoineNeural", "fr-CA-SylvieNeural", "fr-BE-GerardNeural",
        "fr-BE-CharlineNeural", "fr-CH-ArianeNeural", "fr-CH-FabriceNeural", "en-US-GuyNeural",
        "en-US-JennyNeural", "en-GB-RyanNeural", "en-GB-SoniaNeural"]
EDGE_NEG = ["fr-FR-RemyMultilingualNeural", "fr-FR-DeniseNeural", "fr-FR-HenriNeural", "fr-CA-SylvieNeural",
            "fr-BE-GerardNeural", "fr-CH-ArianeNeural", "en-US-GuyNeural", "en-GB-SoniaNeural"]
VITESSES = (0.9, 1.0, 1.1)
# 27/10 (plan P36 lot 6) : voie mesurée. « modele » = le modèle entraîné seul (WakeWord en mode « ou »
# sans voie texte), « ou » / « confirme » = modes de `config.WAKE_MODEL_MODE` avec la voie texte.
VOIES = ("texte", "modele", "ou", "confirme")
MODE_DE_VOIE = {"texte": "off", "modele": "ou", "ou": "ou", "confirme": "confirme"}
CONDITIONS = ["propre", "blanc20", "blanc10", "blanc5", "babil20", "babil10", "babil5", "reverb"]

# Confusables : à NE PAS prendre pour le nom (« astral » à part : variante voulue, voir eveil.py).
PIEGES = [
    "Estelle arrive demain matin.", "Estelle, tu peux venir ?", "Astérix et Obélix sont au cinéma.",
    "Le Mistral souffle fort aujourd'hui.", "Mistral gagnant est une belle chanson.",
    "Les astres brillent ce soir.", "Un astre se lève à l'horizon.", "Elle a vu un voyage astral au cinéma.",
    "L'hémisphère austral est plus froid.", "Estelle et Astrid sont parties.", "Castel, c'est un nom de famille.",
    "Hey, tu as vu Estelle ?", "OK, Mistral, c'est noté.",
]
ASTRAL = ["Astral.", "Astral, c'est un joli mot."]
PHRASES_FR = [
    "Bonjour, comment ça va aujourd'hui ?", "Je vais faire les courses cet après-midi.",
    "Tu peux me passer le sel, s'il te plaît ?", "Il fait beau, on pourrait aller se promener.",
    "La réunion est décalée à quinze heures.", "J'ai oublié mes clés dans la voiture.",
    "Qu'est-ce qu'on mange ce soir ?", "Le train a encore vingt minutes de retard.",
    "Envoie-moi le fichier quand tu peux.", "On se retrouve devant la gare à midi.",
    "Je n'ai pas bien dormi cette nuit.", "Le chat a renversé le verre d'eau.",
    "Il faudrait rappeler le plombier demain.", "Tu as regardé le match hier soir ?",
    "Je termine ce rapport et j'arrive.", "La boulangerie ferme à dix-neuf heures.",
    "Peux-tu vérifier la pression des pneus ?", "C'est l'anniversaire de ma sœur samedi.",
    "On devrait réserver le restaurant à l'avance.", "Le code ne compile plus depuis ce matin.",
    "J'ai mis à jour la base de données.", "Il reste du café dans la cuisine.",
    "Ferme la fenêtre, il commence à pleuvoir.", "Je crois que l'imprimante est en panne.",
    "Les enfants rentrent de l'école à seize heures trente.", "Tu veux venir au cinéma avec nous ?",
    "Le médecin m'a dit de me reposer.", "Il y a des travaux sur l'autoroute.",
    "J'ai trouvé un appartement près du centre.", "Pense à acheter du pain en rentrant.",
    "La batterie du téléphone est presque vide.", "Nous partons en vacances la semaine prochaine.",
    "Ce film était vraiment trop long.", "Je dois finir le budget avant vendredi.",
    "Le facteur est passé ce matin.", "On a perdu le match de foot dimanche.",
    "Tu sais où j'ai rangé le chargeur ?", "La température va baisser cette nuit.",
    "J'ai envoyé le devis au client.", "Les impôts sont à payer avant la fin du mois.",
    "Mon frère vient dîner ce soir.", "Le jardin a besoin d'être arrosé.",
    "Il faut que je change d'abonnement internet.", "Le magasin a fermé ses portes.",
    "Elle travaille dans une banque à Lille.", "J'adore cette chanson à la radio.",
    "Le projet avance mieux que prévu.", "On attend la livraison depuis trois jours.",
    "Tu as pensé à sortir les poubelles ?", "Je commence un nouveau livre ce soir.",
    "Il y a trop de monde au supermarché le samedi.", "La voisine a adopté un petit chien.",
    "Je vais appeler maman tout à l'heure.", "Le serveur de production est tombé.",
    "Notre équipe a gagné le tournoi.", "Il faut renouveler le passeport.",
    "Le bus passe toutes les dix minutes.", "J'ai rendez-vous chez le dentiste jeudi.",
    "Le gâteau au chocolat est délicieux.", "On pourrait repeindre le salon en bleu.",
    "Le wifi ne marche pas dans la chambre.", "C'est trop cher pour ce que c'est.",
    "J'ai couru dix kilomètres ce matin.", "La conférence commence dans une heure.",
    "Je n'arrive pas à ouvrir ce document.", "Les fraises sont enfin de saison.",
    "On a reçu une facture d'électricité énorme.", "Il a neigé toute la nuit en montagne.",
    "Je préfère le thé au café.", "Ma voiture doit passer au contrôle technique.",
    "Astrid m'a appelé ce matin.", "Le stade était plein à craquer.",
    "Tu peux baisser un peu la musique ?", "Le dossier est sur le bureau.",
    "Nous avons visité le musée du Louvre.", "La piscine est fermée pour travaux.",
    "J'ai acheté des billets pour le concert.", "Le chef veut un point demain matin.",
    "Il faut nettoyer le frigo ce week-end.", "Le prix de l'essence a encore augmenté.",
    "Alors, tu en penses quoi de ce projet ?", "Ah oui, j'avais complètement oublié.",
    "Bon, on y va ou pas ?", "Dis donc, tu es en avance aujourd'hui.",
    "Écoute, je te rappelle plus tard.", "Euh, je ne sais pas trop quoi dire.",
    "Hé, regarde par la fenêtre !", "OK, c'est noté pour demain.",
    "Allô, tu m'entends bien ?", "Attends, je cherche mes lunettes.",
]
PHRASES_EN = [
    "Good morning, how are you today?", "I need to finish this report by Friday.",
    "Can you pass me the remote, please?", "The meeting has been moved to three o'clock.",
    "Let's grab lunch near the station.", "My phone battery is almost dead.",
    "Did you watch the game last night?", "The traffic was terrible this morning.",
    "I'll send you the file in a minute.", "We should book the hotel early.",
    "The coffee machine is broken again.", "Turn left at the next traffic light.",
    "She works at a bank in London.", "I forgot my umbrella at the office.",
    "The kids are playing in the garden.", "This movie is way too long.",
    "Hey, did you see that?", "OK, that sounds good to me.",
    "Australia is a beautiful country.", "Mistral is a French company.",
    "The stars are bright tonight.", "Astronomy is my favourite subject.",
]


# --------------------------------------------------------------------------- synthèse

def _key(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:16]


def _write(path: Path, pcm: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.clip(pcm, -32768, 32767).astype(np.int16).tobytes())


def _read(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64)


_piper = None


def piper(text: str, speaker: int, speed: float) -> np.ndarray:
    global _piper
    path = CACHE / f"p-{_key(text, speaker, speed)}.wav"
    if path.exists():
        return _read(path)
    from math import gcd

    from piper import PiperVoice
    from piper.config import SynthesisConfig
    from scipy.signal import resample_poly

    if _piper is None:
        _piper = PiperVoice.load(str(config.MODELS / "piper" / "fr_FR-upmc-medium.onnx"))
    syn = SynthesisConfig(speaker_id=speaker, length_scale=1.0 / speed)
    chunks, rate = [], 22050
    for c in _piper.synthesize(text, syn_config=syn):
        rate = c.sample_rate
        chunks.append(np.frombuffer(c.audio_int16_bytes, dtype=np.int16))
    raw = np.concatenate(chunks).astype(np.float64)
    g = gcd(RATE, rate)
    pcm = resample_poly(raw, RATE // g, rate // g)
    _write(path, pcm)
    return pcm


async def _edge_one(text: str, voice: str, speed: float) -> np.ndarray:
    import av
    import edge_tts
    import edge_tts.communicate as edge_communicate
    from edge_tts.constants import TRUSTED_CLIENT_TOKEN

    # Même contournement du 403 que astrel/voice/engines.py (EdgeEngine).
    edge_communicate.WSS_URL = ("wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"
                                f"?Ocp-Apim-Subscription-Key={TRUSTED_CLIENT_TOKEN}")
    rate = f"{round((speed - 1.0) * 100):+d}%"
    mp3 = bytearray()
    async for msg in edge_tts.Communicate(text, voice, rate=rate).stream():
        if msg["type"] == "audio":
            mp3 += msg["data"]
    codec = av.CodecContext.create("mp3", "r")
    res = av.AudioResampler(format="s16", layout="mono", rate=RATE)
    out = []
    for packet in codec.parse(bytes(mp3)) + codec.parse(b""):
        for fr in codec.decode(packet):
            for o in res.resample(fr):
                out.append(np.frombuffer(bytes(o.planes[0])[: o.samples * 2], dtype=np.int16))
    for o in res.resample(None):
        out.append(np.frombuffer(bytes(o.planes[0])[: o.samples * 2], dtype=np.int16))
    if not out:
        raise RuntimeError("edge-tts : aucun audio")
    return np.concatenate(out).astype(np.float64)


def edge_batch(jobs: list[tuple[str, str, float]], parallel: int = 2) -> dict:
    """{(texte, voix, vitesse): pcm} ; cache disque ; une voix en échec est sautée (dit dans le rapport)."""
    result, todo = {}, []
    for job in jobs:
        path = CACHE / f"e-{_key(*job)}.wav"
        if path.exists():
            result[job] = _read(path)
        else:
            todo.append(job)

    async def run():
        sem = asyncio.Semaphore(parallel)

        async def one(job):
            async with sem:
                for attempt in range(3):
                    try:
                        pcm = await _edge_one(*job)
                        _write(CACHE / f"e-{_key(*job)}.wav", pcm)
                        result[job] = pcm
                        return
                    except Exception as exc:  # 429, réseau : on réessaie un peu, puis on saute
                        err = exc
                        await asyncio.sleep(1.0 + attempt * 2)
                print(f"   edge-tts {job[1]} « {job[0][:30]} » sauté : {str(err).split(',')[0][:60]}", file=sys.stderr)

        await asyncio.gather(*(one(j) for j in todo))

    if todo:
        print(f"   edge-tts : {len(todo)} extraits à synthétiser ({len(result)} en cache)…", flush=True)
        asyncio.run(run())
    return result


# --------------------------------------------------------------------------- conditions

def level(pcm: np.ndarray, rms: float = LEVEL_RMS) -> np.ndarray:
    voiced = pcm[np.abs(pcm) > 0.05 * (np.abs(pcm).max() or 1)]
    cur = float(np.sqrt((voiced ** 2).mean())) if voiced.size else 1.0
    return pcm * (rms / max(cur, 1.0))


def speech_end(pcm: np.ndarray) -> float:
    """Fin de la parole (s) : dernier échantillon au-dessus de 5 % du pic."""
    idx = np.nonzero(np.abs(pcm) > 0.05 * (np.abs(pcm).max() or 1))[0]
    return float(idx[-1] + 1) / RATE if idx.size else 0.0


def mix(sig: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    ps = float((sig[np.abs(sig) > 0.05 * np.abs(sig).max()] ** 2).mean())
    pn = float((noise ** 2).mean()) or 1.0
    return sig + noise * np.sqrt(ps / (pn * 10 ** (snr_db / 10)))


def reverb(sig: np.ndarray, rng: random.Random, rt60: float = 0.5) -> np.ndarray:
    from scipy.signal import fftconvolve

    n = int(rt60 * RATE)
    t = np.arange(n) / RATE
    ir = np.random.default_rng(rng.randrange(1 << 30)).standard_normal(n) * np.exp(-6.9 * t / rt60)
    ir[0] = 1.0
    wet = fftconvolve(sig, ir)[: len(sig) + n]
    return level(wet, float(np.sqrt((sig[np.abs(sig) > 0.05 * np.abs(sig).max()] ** 2).mean())))


def babble(pool: list[np.ndarray], n: int, rng: random.Random) -> np.ndarray:
    out = np.zeros(n)
    for _ in range(4):
        b = rng.choice(pool)
        while len(b) < n:
            b = np.concatenate([b, rng.choice(pool)])
        start = rng.randrange(0, len(b) - n + 1)
        out += level(b[start:start + n])
    return out


def condition(clip: np.ndarray, cond: str, pool: list[np.ndarray], rng: random.Random) -> np.ndarray:
    lead, tail = np.zeros(int(1.0 * RATE)), np.zeros(int(2.5 * RATE))
    sig = np.concatenate([lead, level(clip), tail])
    if cond == "propre":
        return sig + np.random.default_rng(rng.randrange(1 << 30)).standard_normal(len(sig)) * 30  # souffle du micro
    if cond == "reverb":
        return reverb(sig, rng)[: len(sig)]
    snr = float(cond[-2:] if cond[-2:].isdigit() else cond[-1])
    noise = (np.random.default_rng(rng.randrange(1 << 30)).standard_normal(len(sig)) if cond.startswith("blanc")
             else babble(pool, len(sig), rng))
    return mix(sig, noise, snr)


# --------------------------------------------------------------------------- rejeu

class Sim:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def replay(pcm: np.ndarray, wake, vad, sim: Sim, alias=None, scores: list | None = None
           ) -> tuple[list[float], list[float], list[float]]:
    """Rejoue `pcm` par blocs de 80 ms. Rend (instants des réveils, + durée de transcription),
    durées de transcription, instants des réveils de l'alias (temps du flux). `scores` : reçoit le
    score de la voie modèle à chaque bloc (grille de seuils calculée après coup)."""
    data = np.clip(pcm, -32768, 32767).astype(np.int16)
    t0 = sim.t
    fires, busy, alias_fires = [], [], []
    text = wake._text
    for k in range(len(data) // FRAME):
        frame = data[k * FRAME:(k + 1) * FRAME].tobytes()
        sim.t += FRAME_S
        prob = vad.speech_prob(frame)
        if text is not None:
            text.busy_s = 0.0
        fired = wake.feed(frame, None, speech=prob, vad=config.VAD_SPEECH_THRESHOLD)
        spent = text.busy_s if text is not None else 0.0  # > 0 : une capture close et transcrite sur ce bloc
        if scores is not None:
            scores.append(wake.model_score)
        if spent:
            busy.append(spent)
        if fired:
            fires.append(sim.t - t0 + spent)
        if alias is not None and alias.feed(frame, None):
            alias_fires.append(sim.t - t0)
    return fires, busy, alias_fires


def stream_fires(s, thr: float, patience: int, cooldown_s: float | None = None) -> list[int]:
    """Indices des réveils d'une suite de scores (patience blocs de suite au-dessus du seuil, puis
    repos de `WAKE_COOLDOWN_S`), comme `listener._ModelWake` + `WakeWord`."""
    cooldown_s = config.WAKE_COOLDOWN_S if cooldown_s is None else cooldown_s
    out, hits, last = [], 0, -1e9
    for i, v in enumerate(s):
        hits = hits + 1 if v >= thr else 0
        if hits >= patience:
            hits = 0
            if (i - last) * FRAME_S >= cooldown_s:
                out.append(i)
                last = i
    return out


def pret_mode() -> int:
    """« Astrel est prêt » dit par Rémy (voix d'Astrel), retranscrit par le Whisper des phrases."""
    from astrel.listener import Transcriber, _WakeWhisper

    text = "Astrel est prêt."
    pcm = edge_batch([(text, "fr-FR-RemyMultilingualNeural", 1.0)])
    if not pcm:
        print("edge-tts indisponible")
        return 1
    clip = level(next(iter(pcm.values()))).astype(np.int16).tobytes()
    tr = Transcriber()
    tr._prompt = ""  # sans lexique : ce qu'on entend vraiment
    print(f"Whisper {config.STT_MODEL} sans lexique : « {tr.transcribe(clip)} »")
    print(f"Whisper {config.WAKE_TEXT_MODEL} avec hotwords : « {_WakeWhisper()(clip, 'Astrel')} »")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rapide", action="store_true", help="3 voix edge, vitesse 1.0, 10 min de négatifs")
    ap.add_argument("--sans-edge", action="store_true", help="Piper seul (aucun texte envoyé à Microsoft)")
    ap.add_argument("--minutes", type=float, default=None, help="durée visée des négatifs (défaut 120)")
    ap.add_argument("--pret", action="store_true", help="« Astrel est prêt » par Rémy, retranscrit")
    ap.add_argument("--graine", type=int, default=36)
    ap.add_argument("--voie", choices=VOIES, default="texte",
                    help="texte (défaut, voie d'avant le lot 6) ; modele (modèle entraîné seul) ; ou (texte OU "
                         "modèle) ; confirme (texte confirmé par le modèle)")
    ap.add_argument("--seuil", type=float, default=None, help="seuil de la voie modèle (défaut : config)")
    ap.add_argument("--seuil-confirme", type=float, default=None, help="seuil de confirmation (défaut : config)")
    ap.add_argument("--sortie", default=str(OUT), help="dossier des rapports (cache des voix : toujours /tmp/banc-eveil)")
    args = ap.parse_args()
    if args.pret:
        return pret_mode()
    if args.seuil is not None:
        config.WAKE_MODEL_THRESHOLD = args.seuil
    if args.seuil_confirme is not None:
        config.WAKE_MODEL_CONFIRM_THRESHOLD = args.seuil_confirme
    out_dir = Path(args.sortie)

    from astrel.listener import VoiceRecorder, WakeWord

    rng = random.Random(args.graine)
    edge_pos = [] if args.sans_edge else (EDGE[:3] if args.rapide else EDGE)
    edge_neg = [] if args.sans_edge else (EDGE_NEG[:3] if args.rapide else EDGE_NEG)
    speeds = (1.0,) if args.rapide else VITESSES
    minutes = args.minutes if args.minutes is not None else (10.0 if args.rapide else 120.0)
    t_start = time.time()

    print("1/4 Voix de synthèse (positifs)…", flush=True)
    pos: list[tuple[str, str, float, np.ndarray]] = []
    for text in POSITIFS + ASTRAL:
        for name, spk in PIPER:
            for sp in speeds:
                pos.append((text, name, sp, piper(text, spk, sp)))
    got = edge_batch([(t, v, s) for t in POSITIFS + ASTRAL for v in edge_pos for s in speeds])
    pos += [(t, f"edge:{v}", s, p) for (t, v, s), p in got.items()]
    skipped = len(edge_pos) * len(speeds) * len(POSITIFS + ASTRAL) - len(got)

    print("2/4 Voix de synthèse (négatifs)…", flush=True)
    sentences = PHRASES_FR + PHRASES_EN + PIEGES
    neg: list[tuple[str, str, np.ndarray]] = []
    for text in sentences:
        for name, spk in PIPER:
            for sp in (VITESSES if not args.rapide else (1.0,)):
                neg.append((text, name, piper(text, spk, sp)))
    got_neg = edge_batch([(t, v, 1.0) for t in sentences for v in edge_neg])
    neg += [(t, f"edge:{v}", p) for (t, v, _s), p in got_neg.items()]
    pool = [level(p) for _t, _v, p in neg if len(p) > RATE]

    vad = VoiceRecorder()
    sim = Sim()
    text_on = args.voie != "modele"
    mode = MODE_DE_VOIE[args.voie]
    wake = WakeWord(legacy=False, text=text_on, clock=sim, sync=True, model_mode=mode)
    if wake.model_mode != mode:
        print(f"voie modèle impossible ({config.WAKE_MODEL_FILE}) : voir le journal", file=sys.stderr)
        return 1
    if text_on:
        wake._text._transcribe.load()
    track = mode != "off"  # scores de la voie modèle gardés (grille de seuils)

    print(f"3/4 Positifs : {len(pos)} extraits × {len(CONDITIONS)} conditions…", flush=True)
    rows = []
    for text, voice, sp, clip in pos:
        for cond in CONDITIONS:
            sig = condition(clip, cond, pool, rng)
            vad._vad.reset_states()
            wake.reset()
            sim.t += 10.0  # au-delà du délai entre deux réveils
            sc: list | None = [] if track else None
            fires, busy, _ = replay(sig, wake, vad, sim, scores=sc)
            end = 1.0 + speech_end(clip)
            rows.append({"texte": text, "voix": voice, "vitesse": sp, "condition": cond,
                         "reveil": bool(fires), "delai": (fires[0] - end) if fires else None,
                         "transcription_s": max(busy) if busy else None, "fin_parole_s": end,
                         "scores": [round(v, 4) for v in sc] if sc is not None else None})

    print(f"4/4 Négatifs : flux continu de {minutes:.0f} min…", flush=True)
    alias = WakeWord(legacy=True, text=False, clock=sim)
    rng.shuffle(neg)
    target = minutes * 60
    stream_s, false_text, false_alias, n_utt, pieges_hits, busy_all = 0.0, [], [], 0, [], []
    neg_scores: list[float] = []
    neg_spans: list[tuple[int, int, str]] = []  # (début, fin) de chaque phrase dans neg_scores
    vad._vad.reset_states()
    wake.reset()
    i = 0
    while stream_s < target and neg:
        text, voice, clip = neg[i % len(neg)]
        i += 1
        gap = np.zeros(int(rng.uniform(0.6, 1.6) * RATE))
        sig = np.concatenate([level(clip), gap])
        sig = sig + np.random.default_rng(rng.randrange(1 << 30)).standard_normal(len(sig)) * 30
        if i % 4 == 0:  # un quart du flux sous du babil à 10 dB
            sig = mix(sig, babble(pool, len(sig), rng), 10)
        start = len(neg_scores)
        fires, busy, afires = replay(sig, wake, vad, sim, alias=alias, scores=neg_scores if track else None)
        if track:
            neg_spans.append((start, len(neg_scores), text))
        busy_all += busy
        stream_s += len(sig) / RATE
        n_utt += 1
        if fires:
            false_text.append({"texte": text, "voix": voice, "nom": wake.last_name})
            if text in PIEGES:
                pieges_hits.append(text)
        false_alias += [{"texte": text, "voix": voice}] * len(afires)
        if n_utt % 200 == 0:
            print(f"   {stream_s / 60:.0f} min, {len(false_text)} faux réveils (texte), {len(false_alias)} (alias)",
                  flush=True)

    hours = stream_s / 3600
    def recall(conds, texts=POSITIFS):
        sel = [r for r in rows if r["condition"] in conds and r["texte"] in texts]
        return (sum(r["reveil"] for r in sel) / len(sel)) if sel else None, len(sel)

    delays = [r["delai"] for r in rows if r["delai"] is not None and r["texte"] in POSITIFS]
    by_text = {t: [r["delai"] for r in rows if r["delai"] is not None and r["texte"] == t] for t in POSITIFS}
    report = {
        "date": time.strftime("%Y-%m-%d %H:%M"), "duree_banc_s": round(time.time() - t_start),
        "modele": config.WAKE_TEXT_MODEL, "voix_positives": sorted({r["voix"] for r in rows}),
        "edge_sautes": skipped,
        "rappel": {c: recall([c]) for c in CONDITIONS},
        "rappel_propre": recall(["propre"]), "rappel_10db": recall(["blanc10", "babil10"]),
        "rappel_par_texte_propre": {t: recall(["propre"], [t]) for t in POSITIFS},
        "astral_reveille": recall(["propre"], ASTRAL),
        "delai_median_s": statistics.median(delays) if delays else None,
        "delai_p90_s": float(np.percentile(delays, 90)) if delays else None,
        "delai_median_par_texte": {t: (statistics.median(d) if d else None) for t, d in by_text.items()},
        "transcription_mediane_s": statistics.median(busy_all) if busy_all else None,
        "negatifs_heures": round(hours, 2), "negatifs_phrases": n_utt,
        "faux_reveils_texte": len(false_text), "faux_reveils_texte_par_h": len(false_text) / hours if hours else None,
        "faux_reveils_alias": len(false_alias), "faux_reveils_alias_par_h": len(false_alias) / hours if hours else None,
        "pieges_pris": pieges_hits, "faux_reveils_detail": false_text[:50],
        "rates_propres": [f"{r['voix']} {r['vitesse']} « {r['texte']} »" for r in rows
                          if r["condition"] == "propre" and not r["reveil"] and r["texte"] in POSITIFS],
        "voie": args.voie,
        "modele": {"fichier": str(config.WAKE_MODEL_FILE), "seuil": config.WAKE_MODEL_THRESHOLD,
                   "patience": config.WAKE_MODEL_PATIENCE, "seuil_confirme": config.WAKE_MODEL_CONFIRM_THRESHOLD}
        if track else None,
    }
    if track:
        report["grille_modele_seul"] = model_grid(rows, neg_scores, neg_spans, hours)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rapport.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "lignes.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    def pct(v):
        return "—" if v[0] is None else f"{v[0] * 100:.0f} % ({v[1]})"
    lines = [
        f"Banc du mot d'éveil « Astrel » ({report['date']}, Whisper {config.WAKE_TEXT_MODEL}, "
        f"{len(report['voix_positives'])} voix, {report['duree_banc_s']} s)",
        f"Rappel propre : {pct(report['rappel_propre'])}   (cible ≥ 90 %)",
        f"Rappel à 10 dB (blanc + babil) : {pct(report['rappel_10db'])}   (cible ≥ 75 %)",
        "Par condition : " + ", ".join(f"{c} {pct(v)}" for c, v in report["rappel"].items()),
        "Par phrase (propre) : " + ", ".join(f"« {t} » {pct(v)}" for t, v in report["rappel_par_texte_propre"].items()),
        f"« Astral » seul (variante voulue) : {pct(report['astral_reveille'])}",
        f"Délai après la fin de la parole : médiane {report['delai_median_s'] or 0:.2f} s, "
        f"p90 {report['delai_p90_s'] or 0:.2f} s   (cible < 1,2 s) ; transcription médiane "
        f"{report['transcription_mediane_s'] or 0:.2f} s",
        "Délai médian par phrase : " + ", ".join(f"« {t} » {d:.2f} s" for t, d in report["delai_median_par_texte"].items()
                                                if d is not None),
        f"Négatifs : {report['negatifs_heures']} h, {n_utt} phrases ; faux réveils texte {len(false_text)} "
        f"({report['faux_reveils_texte_par_h'] or 0:.2f}/h, cible ≤ 1/h) ; alias hey_jarvis {len(false_alias)} "
        f"({report['faux_reveils_alias_par_h'] or 0:.2f}/h)",
        f"Pièges pris pour le nom : {', '.join(sorted(set(pieges_hits))) or 'aucun'}",
        f"Voix edge sautées : {skipped}",
    ]
    lines.insert(1, f"Voie mesurée : {args.voie}" + (
        f" (modèle {config.WAKE_MODEL_FILE.name}, seuil {config.WAKE_MODEL_THRESHOLD:.2f}, patience "
        f"{config.WAKE_MODEL_PATIENCE}, confirmation {config.WAKE_MODEL_CONFIRM_THRESHOLD:.2f})" if track else ""))
    if track:
        lines.append("Modèle SEUL, grille (seuil/patience : rappel propre, 10 dB, faux réveils/h, délai médian/p90) :")
        lines += [f"   {g['seuil']:.2f}/{g['patience']} : {g['rappel_propre'] * 100:.0f} %, {g['rappel_10db'] * 100:.0f} %, "
                  f"{g['faux_par_h']:.2f}/h, {g['delai_median_s'] or 0:.2f}/{g['delai_p90_s'] or 0:.2f} s"
                  + (f", pris : {', '.join(g['phrases_prises'][:4])}" if g["phrases_prises"] else "")
                  for g in report["grille_modele_seul"]]
    (out_dir / "rapport.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines))
    print(f"\nDétail : {out_dir / 'rapport.json'}")
    return 0


def model_grid(rows: list[dict], neg_scores: list[float], neg_spans: list, hours: float) -> list[dict]:
    """Voie modèle SEULE, calculée après coup sur les scores gardés, pour une grille de seuils : rappel
    (propre, 10 dB), faux réveils par heure (flux négatif continu), délai après la fin de la parole."""
    out = []
    pos_rows = [r for r in rows if r["texte"] in POSITIFS and r["scores"] is not None]
    for thr in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95):
        for patience in (1, 2, 3):
            delays, hit = [], {}
            for r in pos_rows:
                f = stream_fires(r["scores"], thr, patience)
                hit[id(r)] = bool(f)
                if f:
                    delays.append((f[0] + 1) * FRAME_S - r["fin_parole_s"])

            def rec(conds):
                sel = [r for r in pos_rows if r["condition"] in conds]
                return sum(hit[id(r)] for r in sel) / len(sel) if sel else 0.0

            fires = stream_fires(neg_scores, thr, patience)
            taken = []
            for idx in fires:
                for a, b, text in neg_spans:
                    if a <= idx < b:
                        taken.append(text)
                        break
            out.append({"seuil": thr, "patience": patience, "rappel_propre": round(rec(["propre"]), 3),
                        "rappel_10db": round(rec(["blanc10", "babil10"]), 3),
                        "rappel_par_condition": {c: round(rec([c]), 3) for c in CONDITIONS},
                        "faux": len(fires), "faux_par_h": round(len(fires) / hours, 2) if hours else None,
                        "delai_median_s": round(statistics.median(delays), 3) if delays else None,
                        "delai_p90_s": round(float(np.percentile(delays, 90)), 3) if delays else None,
                        "phrases_prises": taken})
    return out


if __name__ == "__main__":
    sys.exit(main())
