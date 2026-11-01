#!/usr/bin/env python3
"""Données d'entraînement du modèle openWakeWord « Astrel » (27/10, plan P36 lot 6), voix de SYNTHÈSE.

Lancé avec la venv d'Astrel (Piper, edge-tts, openWakeWord y sont déjà ; RIEN n'y est installé) :

    .venv/bin/python tools/eveil_entrainement/donnees.py              # synthèse + caractéristiques
    .venv/bin/python tools/eveil_entrainement/donnees.py --sans-edge  # Piper seul (aucun réseau)

Voix DISJOINTES du banc (`tools/banc_eveil.py`), pour que le banc mesure une vraie généralisation :
Piper (voir `POS_TRAIN`, `NEG_EXTRA_TRAIN` : modèles téléchargés dans `.eveil-donnees/piper/` depuis
huggingface.co/rhasspy/piper-voices) ; edge-tts : voix fr et multilingues ABSENTES du banc ; voix
« nouvelles » par décalage de hauteur et de formants (rééchantillonnage, `augment`). Phrases négatives et pièges réécrits
(aucune phrase du banc). Le nom « astral » et les mots proches (Estelle, Astérix, Mistral, Astrid…)
sont des NÉGATIFS.

Chaque extrait est augmenté (niveau, bruit blanc/rose/babil, réverbération, filtre de micro), puis
passé par blocs de 80 ms dans `openwakeword.utils.AudioFeatures` EN FLUX, comme `Model.predict` le
fait dans Astrel : fenêtres (16, 96) identiques à l'inférence. Processeur seul (la carte graphique
sert au Whisper d'Astrel). Sorties : `.eveil-donnees/` (dossier du projet, hors runtime/).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import random
import sys
import time
from collections import deque
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""  # caractéristiques au processeur, jamais la carte graphique
os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import banc_eveil as banc  # noqa: E402  (synthèse edge, niveau, mélange : mêmes fonctions que le banc)

DATA = ROOT / ".eveil-donnees"
CLIPS = DATA / "clips"
FEAT = DATA / "feat"
RATE = banc.RATE
FRAME = banc.FRAME
FRAME_S = banc.FRAME_S
WARM = 16  # fenêtres de 16 caractéristiques : les 15 premiers blocs contiennent le remplissage initial

# Fenêtre « positive » : fin de fenêtre entre la fin du nom et 0,56 s après. Entre le début du nom et
# là, ou tant que le nom reste dans la fenêtre (1,28 s), on n'apprend rien (ambigu).
POS_AVANT_S, POS_APRES_S, FENETRE_S = 0.08, 0.56, 1.28

POSITIFS = ["Astrel.", "Astrel !", "Astrel ?", "Hey Astrel.", "OK Astrel.", "Dis Astrel.", "Eh, Astrel.",
            "Salut Astrel.", "Astrelle.", "Astrèle."]
SUITES = ["quelle heure est-il ?", "mets un peu de musique.", "baisse le volume.", "rappelle-moi d'appeler Paul.",
          "quel temps fera-t-il demain ?", "ouvre mes mails.", "lance le minuteur.", "tu es là ?",
          "ajoute du lait à la liste.", "éteins la lumière.", "résume ma journée.", "c'est quoi le programme ?",
          "envoie le rapport à Claire.", "note cette idée.", "combien font douze fois huit ?",
          "arrête la musique.", "lis-moi le dernier message.", "on en était où ?"]
PIEGES = [
    "Astral.", "Un voyage astral.", "Le plan astral, tu y crois ?", "Mon thème astral est bizarre.",
    "Estelle.", "Estelle, viens voir.", "Hey Estelle.", "Salut Estelle, ça va ?", "Dis Estelle.",
    "Astérix.", "Astérix chez les Bretons.", "Mistral.", "Le mistral se lève.", "OK Mistral.",
    "Astrid.", "Dis Astrid, tu viens ?", "Astrid est en retard.", "Un astre.", "Les astres.",
    "Castel.", "Pastel.", "Des crayons pastel.", "Austral.", "L'océan austral.", "Australie.",
    "Esther.", "Esther, tu peux venir ?", "Je suis d'astreinte.", "Une astreinte ce week-end.",
    "Sur l'estrade.", "Il faut rester.", "Je vais rester là.", "Astro.", "Un astronaute.",
    "Astrée.", "Estrella.", "Un cartel.", "À l'hôtel.", "Austerlitz.", "Une astuce.", "Strasbourg.",
    "Le stress.", "Ah, c'est trop tard.", "Hé, attends-moi.", "Hey Siri.", "OK Google.", "Alexa.",
    "Hey Jarvis.", "Astrophysique.", "Lustrer.", "Illustre.", "Est-ce réel ?", "Ce rappel.",
]
PHRASES_FR = [
    "Je reviens dans cinq minutes.", "Tu as vu le temps qu'il fait dehors ?", "On mange à quelle heure ?",
    "Il faut que je range mon bureau.", "La voiture fait un bruit bizarre.", "J'ai reçu un colis ce matin.",
    "Le rendez-vous est à dix heures.", "Tu peux m'aider à porter ça ?", "Je n'ai plus de batterie.",
    "On part en week-end vendredi soir.", "Le café est encore chaud.", "Il y a un bouchon sur le périphérique.",
    "J'ai fini le dossier hier soir.", "Tu connais un bon garagiste ?", "Les prix ont encore augmenté.",
    "Mon ordinateur rame depuis la mise à jour.", "On se voit demain à la pause.", "J'ai faim, pas toi ?",
    "La réunion a duré deux heures.", "Il pleut depuis ce matin.", "Je cherche mes écouteurs partout.",
    "Elle habite à Marseille maintenant.", "Le match commence à vingt et une heures.",
    "Tu as pensé à prendre le pain ?", "Le chien veut sortir.", "Je dois rappeler la banque.",
    "On a vu un très bon film hier.", "Le colis n'est toujours pas arrivé.", "Je préfère partir tôt.",
    "Il faut changer l'ampoule de la cuisine.", "Ma sœur arrive par le train de midi.",
    "Le serveur répond très lentement.", "On peut décaler à jeudi ?", "J'ai oublié mon mot de passe.",
    "La facture est arrivée par courrier.", "Je vais courir au parc.", "Ce restaurant est toujours plein.",
    "Le bébé dort enfin.", "Il manque une page au contrat.", "On a gagné trois à un.",
    "Tu veux un thé ou un café ?", "La mise en production est prévue lundi.", "Il fait froid dans le bureau.",
    "J'ai appelé le service client.", "La salle est réservée jusqu'à midi.", "Mes parents viennent dimanche.",
    "Le parking est complet.", "J'ai perdu mon parapluie.", "Le devis est trop élevé.",
    "Elle a eu son permis du premier coup.", "On a repeint la chambre.", "Je regarde une série ce soir.",
    "Le vol a été annulé.", "Tu peux répéter s'il te plaît ?", "Ça fait longtemps qu'on ne s'est pas vus.",
    "Il faut nourrir le chat.", "La lessive est terminée.", "Je n'ai pas compris la question.",
    "Le lave-vaisselle fuit encore.", "On a rendez-vous chez le notaire.", "C'est une excellente idée.",
    "Je pense que tu as raison.", "Bon, on verra bien demain.", "Voilà, c'est fait.", "Mais oui, bien sûr.",
    "Attends deux secondes.", "Euh, laisse-moi réfléchir.", "Ah bon, tu es sûr ?", "Allez, on y va.",
    "Hé, tu as deux minutes ?", "OK, je m'en occupe.", "Dis donc, quelle journée.", "Écoute, ce n'est pas grave.",
    "Il est temps de partir.", "Les vacances sont finies.", "Je vais faire une sieste.", "On commande une pizza ?",
    "Le professeur a donné beaucoup de devoirs.", "Je suis coincé dans les embouteillages.",
    "Le concert était génial.", "Tu as des nouvelles de Julien ?", "Marc a changé de travail.",
    "Sophie organise une fête samedi.", "Thomas est malade aujourd'hui.", "Camille a eu un bébé.",
    "Nicolas passe nous voir ce soir.", "Pauline adore la randonnée.", "Antoine a raté son train.",
    "Le stagiaire commence lundi.", "On a un problème avec le client.", "Le tableau est mal accroché.",
    "La musique est trop forte.", "Le four met du temps à chauffer.", "Je vais à la pharmacie.",
    "Il reste deux places dans le train.", "Le marché est ouvert le mardi.", "J'ai mal au dos.",
]
PHRASES_EN = [
    "I'll be back in five minutes.", "What time is dinner tonight?", "Have you seen my keys anywhere?",
    "The printer is out of paper again.", "Let's meet after lunch.", "My flight was delayed.",
    "Could you turn the heating up?", "I really enjoyed that book.", "The internet is down again.",
    "We need more milk and eggs.", "That was a great presentation.", "I think it's going to rain.",
    "Hey, how was your weekend?", "OK, let me check.", "Alright, see you tomorrow.",
    "The astronaut waved at the crowd.", "Estelle is my cousin.", "The Mistral wind is strong today.",
    "Australia won the match.", "That's an astral projection.", "Asterix is a comic book.",
]
SPLIT_SEED = 36


# --------------------------------------------------------------------------- synthèse

def _key(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:16]


_voices: dict[str, object] = {}


def piper_say(model: str, text: str, speaker: int | None, speed: float, noise: float, noise_w: float) -> np.ndarray:
    path = CLIPS / f"p-{_key(model, text, speaker, speed, noise, noise_w)}.wav"
    if path.exists():
        return banc._read(path)
    from math import gcd

    from piper import PiperVoice
    from piper.config import SynthesisConfig
    from scipy.signal import resample_poly

    if model not in _voices:
        _voices[model] = PiperVoice.load(str(DATA / "piper" / f"{model}.onnx"))
    voice = _voices[model]
    syn = SynthesisConfig(speaker_id=speaker, length_scale=1.0 / speed, noise_scale=noise, noise_w_scale=noise_w)
    chunks, rate = [], 22050
    for c in voice.synthesize(text, syn_config=syn):
        rate = c.sample_rate
        chunks.append(np.frombuffer(c.audio_int16_bytes, dtype=np.int16))
    raw = np.concatenate(chunks).astype(np.float64)
    g = gcd(RATE, rate)
    pcm = resample_poly(raw, RATE // g, rate // g)
    banc._write(path, pcm)
    return pcm


# Voix edge ABSENTES du banc (banc : EDGE + EDGE_NEG). Les deux dernières : validation seulement.
EDGE_TRAIN = ["fr-CA-ThierryNeural", "fr-FR-EloiseNeural", "en-US-AndrewMultilingualNeural",
              "en-US-BrianMultilingualNeural", "en-US-EmmaMultilingualNeural", "en-AU-WilliamMultilingualNeural",
              "de-DE-SeraphinaMultilingualNeural", "de-DE-FlorianMultilingualNeural",
              "it-IT-GiuseppeMultilingualNeural", "pt-BR-ThalitaMultilingualNeural"]
EDGE_VAL = ["fr-CA-JeanNeural", "en-US-AvaMultilingualNeural"]
assert not set(EDGE_TRAIN + EDGE_VAL) & set(banc.EDGE + banc.EDGE_NEG)


def edge_many(jobs: list[tuple[str, str, float]], pause: float = 3.0, passes: int = 4, rest: float = 60.0) -> dict:
    """{(texte, voix, vitesse): pcm}, cache dans CLIPS (pas celui du banc), LENTEMENT : edge-tts répond
    429 si on enchaîne (vu le 27/10, 80 refus en une passe), et c'est aussi la voix d'Astrel. Pause
    entre paquets, puis jusqu'à `passes` reprises des manquants après un long repos."""
    old = banc.CACHE
    banc.CACHE = CLIPS
    try:
        out = {}
        for p in range(passes):
            todo = [j for j in jobs if j not in out]
            if not todo:
                break
            if p:
                print(f"   edge-tts : reprise {p}, {len(todo)} manquants, repos {rest:.0f} s…", flush=True)
                time.sleep(rest)
            for i in range(0, len(todo), 20):
                batch = todo[i:i + 20]
                cached = all((CLIPS / f"e-{banc._key(*j)}.wav").exists() for j in batch)
                out.update(banc.edge_batch(batch, parallel=1))
                if not cached:
                    time.sleep(pause)
        return out
    finally:
        banc.CACHE = old


# Voix Piper (27/10, essayées et retranscrites par Whisper small) : `fr_FR-mls-medium` (125 locuteurs)
# ne dit que du charabia avec piper-tts 1.8 (« Astrel » → « Je suis pas sûr ») : écartée. Les voix
# anglaises (vctk, libritts_r) disent « Astrel » comme « astral » : NÉGATIFS seulement, comme les
# voix allemande, italienne et mexicaine (prononciation fausse). Positifs : siwis, tom, gilles,
# sharvard (espagnol : « astrel » bien prononcé), plus edge-tts.
POS_TRAIN = [("piper", "fr_FR-siwis-medium", None), ("piper", "fr_FR-tom-medium", None),
             ("piper", "fr_FR-gilles-low", None), ("piper", "es_ES-sharvard-medium", 0)]
POS_VAL = [("piper", "es_ES-sharvard-medium", 1)]
NEG_EXTRA_TRAIN = ([("piper", "en_GB-vctk-medium", s) for s in range(90)]
                   + [("piper", "en_US-libritts_r-medium", s) for s in range(0, 800, 4)]
                   + [("piper", "de_DE-thorsten-medium", None), ("piper", "it_IT-riccardo-x_low", None),
                      ("piper", "es_MX-ald-medium", None)])
NEG_EXTRA_VAL = ([("piper", "en_GB-vctk-medium", s) for s in range(90, 109)]
                 + [("piper", "en_US-libritts_r-medium", s) for s in range(800, 904, 4)])


def plan_clips(rng: random.Random, sans_edge: bool) -> list[dict]:
    """Liste des extraits propres : {kind: pos|neg|piege, split: train|val, voice, text, suite, speed…}."""
    items = []

    def pos_voice(split):
        return rng.choice(POS_TRAIN if split == "train" else POS_VAL)

    def neg_voice(split):
        if rng.random() < 0.4:
            return pos_voice(split)
        return rng.choice(NEG_EXTRA_TRAIN if split == "train" else NEG_EXTRA_VAL)

    def var():
        return dict(speed=round(rng.uniform(0.8, 1.25), 2), noise=round(rng.uniform(0.4, 0.9), 2),
                    noise_w=round(rng.uniform(0.5, 1.0), 2))

    for split, n_pos, n_neg_per, n_piege_per in (("train", 2000, 5, 22), ("val", 250, 1, 4)):
        for _ in range(n_pos):
            text = rng.choice(POSITIFS)
            suite = rng.choice(SUITES) if rng.random() < 0.35 else None
            items.append({"kind": "pos", "split": split, "voice": pos_voice(split), "text": text, "suite": suite, **var()})
        for text in PHRASES_FR + PHRASES_EN:
            for _ in range(n_neg_per):
                items.append({"kind": "neg", "split": split, "voice": neg_voice(split), "text": text, **var()})
        for text in PIEGES:
            for _ in range(n_piege_per):
                items.append({"kind": "piege", "split": split, "voice": neg_voice(split), "text": text, **var()})
    if not sans_edge:
        for split, voices in (("train", EDGE_TRAIN), ("val", EDGE_VAL)):
            for v in voices:
                for text in POSITIFS[:8]:
                    for sp in (0.9, 1.0, 1.15):
                        suite = rng.choice(SUITES) if rng.random() < 0.35 else None
                        items.append({"kind": "pos", "split": split, "voice": ("edge", v, None), "text": text,
                                      "suite": suite, "speed": sp})
                for text in PIEGES:
                    items.append({"kind": "piege", "split": split, "voice": ("edge", v, None), "text": text, "speed": 1.0})
                for text in rng.sample(PHRASES_FR + PHRASES_EN, 30):
                    items.append({"kind": "neg", "split": split, "voice": ("edge", v, None), "text": text, "speed": 1.0})
    return items


def synth_item(it: dict, edge_cache: dict) -> tuple[np.ndarray, np.ndarray | None]:
    eng, model, spk = it["voice"]

    def say(text):
        if eng == "piper":
            return piper_say(model, text, spk, it["speed"], it["noise"], it["noise_w"])
        return edge_cache.get((text, model, it["speed"]))

    main = say(it["text"])
    suite = say(it["suite"][0].upper() + it["suite"][1:]) if it.get("suite") else None
    return main, suite


# --------------------------------------------------------------------------- augmentation

def pink(n: int, g: np.random.Generator) -> np.ndarray:
    f = np.fft.rfftfreq(n)
    spec = g.standard_normal(len(f)) + 1j * g.standard_normal(len(f))
    spec[1:] /= np.sqrt(f[1:])
    spec[0] = 0
    x = np.fft.irfft(spec, n)
    return x / (x.std() or 1.0)


def shift(x: np.ndarray, f: float) -> np.ndarray:
    """Hauteur ET formants multipliés par `f` (durée divisée par `f`) : un autre « locuteur »."""
    if abs(f - 1.0) < 0.01:
        return x
    from scipy.signal import resample_poly

    up = 100
    return resample_poly(x, up, max(1, round(up * f)))


def mic_filter(x: np.ndarray, rng: random.Random) -> np.ndarray:
    from scipy.signal import butter, sosfilt

    lo, hi = rng.uniform(60, 300), rng.uniform(3400, 7800)
    return sosfilt(butter(2, [lo, hi], btype="band", fs=RATE, output="sos"), x)


def augment(sig: np.ndarray, pool: list[np.ndarray], rng: random.Random) -> np.ndarray:
    """Un tirage parmi : propre, blanc, rose, babil (0-25 dB), réverbération (+ bruit parfois)."""
    g = np.random.default_rng(rng.randrange(1 << 30))
    x = banc.level(sig, rng.uniform(900, 6000))
    if rng.random() < 0.15:  # gain qui sature, comme un micro trop poussé
        x = np.clip(x * rng.uniform(2, 5), -32768, 32767)
    if rng.random() < 0.35:
        x = banc.reverb(x, rng, rt60=rng.uniform(0.2, 0.9))[: len(x)]
    if rng.random() < 0.5:
        x = mic_filter(x, rng)
    r = rng.random()
    if r < 0.2:
        noise = None
    elif r < 0.45:
        noise = g.standard_normal(len(x))
    elif r < 0.7:
        noise = pink(len(x), g)
    else:
        noise = banc.babble(pool, len(x), rng) if pool else g.standard_normal(len(x))
    if noise is not None:
        x = banc.mix(x, noise, rng.uniform(0, 25))
    return x + g.standard_normal(len(x)) * rng.uniform(5, 40)  # souffle du micro


# --------------------------------------------------------------------------- caractéristiques

_af = None
_init = None


def _worker_init():
    global _af, _init
    from openwakeword.utils import AudioFeatures

    _af = AudioFeatures(ncpu=1)
    _init = _af.feature_buffer.copy()


def features(pcm16: np.ndarray) -> np.ndarray:
    """Fenêtres (n, 16, 96) float16, une par bloc de 80 ms à partir du 16e, EN FLUX comme Model.predict."""
    af = _af
    af.raw_data_buffer = deque(maxlen=RATE * 10)
    af.melspectrogram_buffer = np.ones((76, 32))
    af.accumulated_samples = 0
    af.feature_buffer = _init.copy()
    out = []
    for k in range(len(pcm16) // FRAME):
        af(pcm16[k * FRAME:(k + 1) * FRAME])
        if k >= WARM - 1:
            out.append(af.get_features(16)[0])
    return np.asarray(out, dtype=np.float16).reshape(-1, 16, 96)


def _job(args):
    """(pcm int16, kind, wake_start, wake_end) → (fenêtres positives, fenêtres négatives).
    Négatives : une fenêtre sur deux (deux voisines, décalées de 80 ms, se ressemblent trop)."""
    data, kind, w0, w1 = args
    f = features(data)
    if kind != "pos":
        return None, f[::2]
    t_end = (np.arange(len(f)) + WARM) * FRAME_S  # fin de chaque fenêtre (s)
    pos = (t_end >= w1 - POS_AVANT_S) & (t_end <= w1 + POS_APRES_S)
    neg = (t_end < w0) | (t_end > w1 + FENETRE_S + 0.08)
    return f[pos], f[neg][::2]


def _jobs_of(items, edge_cache, rng, n_aug, pool):
    """Extraits augmentés, un par un (en int16 : 4 fois moins de mémoire que le float64)."""
    for it in items:
        main, suite = synth_item(it, edge_cache)
        if main is None:
            continue
        for _ in range(n_aug):
            lead = np.zeros(int(rng.uniform(1.4, 2.2) * RATE))
            tail = np.zeros(int(rng.uniform(1.6, 2.2) * RATE))
            f = rng.uniform(0.86, 1.16) if rng.random() < 0.6 else 1.0
            clip = banc.level(shift(main, f))
            idx = np.nonzero(np.abs(clip) > 0.05 * (np.abs(clip).max() or 1))[0]
            w0 = len(lead) / RATE + (idx[0] / RATE if idx.size else 0)  # début du nom (s)
            w1 = len(lead) / RATE + banc.speech_end(clip)                # fin du nom (s)
            parts = [lead, clip]
            if suite is not None:
                parts += [np.zeros(int(rng.uniform(0.08, 0.35) * RATE)), banc.level(shift(suite, f))]
            parts.append(tail)
            sig = augment(np.concatenate(parts), pool, rng)
            yield (np.clip(sig, -32768, 32767).astype(np.int16), it["kind"], w0, w1)


def build(items: list[dict], edge_cache: dict, rng: random.Random, n_aug: int, procs: int) -> None:
    FEAT.mkdir(parents=True, exist_ok=True)
    # Babil : phrases négatives d'ENTRAÎNEMENT seulement.
    pool = []
    for it in items:
        if it["kind"] == "neg" and it["split"] == "train" and len(pool) < 300:
            main, _ = synth_item(it, edge_cache)
            if main is not None and len(main) > RATE:
                pool.append(banc.level(main))
    stats = {}
    for split in ("train", "val"):
        sel = [i for i in items if i["split"] == split]
        aug = n_aug if split == "train" else 1
        print(f"   {split} : {len(sel)} extraits × {aug} → caractéristiques ({procs} processus)…", flush=True)
        t0 = time.time()
        res = {"pos": [], "neg": [], "piege": [], "pos_neg": []}
        clip_ids = []  # extrait d'origine de chaque fenêtre positive (rappel PAR EXTRAIT)
        neg_ids: dict[str, list] = {}  # idem pour neg et piege (faux réveils PAR EXTRAIT)
        gen = _jobs_of(sel, edge_cache, rng, aug, pool)
        n = 0
        with mp.Pool(procs, initializer=_worker_init) as pool_p:
            while True:
                chunk = [j for _, j in zip(range(procs * 16), gen)]  # par paquets : mémoire bornée
                if not chunk:
                    break
                for k, ((fpos, fneg), job) in enumerate(zip(pool_p.map(_job, chunk, chunksize=4), chunk)):
                    if job[1] == "pos":
                        res["pos"].append(fpos)
                        clip_ids.append(np.full(len(fpos), n + k, dtype=np.int32))
                        res["pos_neg"].append(fneg)
                    else:
                        res[job[1]].append(fneg)
                        neg_ids.setdefault(job[1], []).append(np.full(len(fneg), n + k, dtype=np.int32))
                n += len(chunk)
                if n % (procs * 16 * 10) == 0:
                    print(f"      {n} extraits ({time.time() - t0:.0f} s)", flush=True)
        for k, arrs in res.items():
            arr = np.concatenate(arrs) if arrs else np.zeros((0, 16, 96), np.float16)
            np.save(FEAT / f"{split}_{k}.npy", arr)
            stats[f"{split}_{k}"] = int(len(arr))
        np.save(FEAT / f"{split}_pos_clip.npy", np.concatenate(clip_ids) if clip_ids else np.zeros(0, np.int32))
        for kind, ids in neg_ids.items():
            np.save(FEAT / f"{split}_{kind}_clip.npy", np.concatenate(ids))
        print(f"   {split} fait en {time.time() - t0:.0f} s : " +
              ", ".join(f"{k} {v}" for k, v in stats.items() if k.startswith(split)), flush=True)
    (FEAT / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sans-edge", action="store_true")
    ap.add_argument("--aug", type=int, default=3, help="tirages d'augmentation par extrait d'entraînement")
    ap.add_argument("--proc", type=int, default=6, help="processus pour les caractéristiques (processeur)")
    args = ap.parse_args()
    rng = random.Random(SPLIT_SEED)
    CLIPS.mkdir(parents=True, exist_ok=True)
    items = plan_clips(rng, args.sans_edge)
    print(f"1/3 {len(items)} extraits prévus (pos {sum(i['kind'] == 'pos' for i in items)}, "
          f"neg {sum(i['kind'] == 'neg' for i in items)}, pièges {sum(i['kind'] == 'piege' for i in items)})", flush=True)
    edge_cache = {}
    if not args.sans_edge:
        jobs = sorted({(t, i["voice"][1], i["speed"]) for i in items if i["voice"][0] == "edge"
                       for t in [i["text"]] + ([i["suite"][0].upper() + i["suite"][1:]] if i.get("suite") else [])})
        print(f"2/3 edge-tts : {len(jobs)} extraits…", flush=True)
        edge_cache = edge_many(jobs)
        print(f"   edge-tts : {len(edge_cache)} obtenus, {len(jobs) - len(edge_cache)} sautés", flush=True)
    print("2/3 Piper…", flush=True)
    t0 = time.time()
    for n, it in enumerate(items):
        if it["voice"][0] == "piper":
            synth_item(it, edge_cache)
        if n % 2000 == 0:
            print(f"   {n}/{len(items)} ({time.time() - t0:.0f} s)", flush=True)
    print("3/3 Augmentation et caractéristiques…", flush=True)
    build(items, edge_cache, rng, args.aug, args.proc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
