#!/usr/bin/env python3
"""Entraîne le modèle openWakeWord « Astrel » (27/10, plan P36 lot 6) et l'exporte en ONNX.

Lancé avec la venv À PART `.venv-eveil` (torch PROCESSEUR seul, jamais dans la venv d'Astrel) :

    python3 -m venv .venv-eveil
    .venv-eveil/bin/pip install --index-url https://download.pytorch.org/whl/cpu torch
    .venv-eveil/bin/pip install numpy onnx onnxruntime onnxscript
    .venv-eveil/bin/python tools/eveil_entrainement/entrainer.py

Entrées (`.eveil-donnees/`) : `feat/*.npy` de `donnees.py` (voix de synthèse) ; négatifs généraux
pré-calculés d'openWakeWord (huggingface.co/datasets/davidscripka/openwakeword_features, licence
CC BY-NC-SA 4.0 : modèle pour usage NON commercial) : tranches d'ACAV100M (`acav/part*.bin`, ≈ 210 h,
parole multilingue, bruit, musique) et `validation_set_features.npy` (≈ 10,7 h, jamais appris : sert
à mesurer les faux réveils sur du VRAI son). `--sans-acav` : synthèse seule (aucune donnée NC).

Architecture d'openWakeWord (Flatten → Linear → LayerNorm → ReLU ×2 → Linear → Sigmoid), entrée
(1, 16, 96) = 1,28 s de caractéristiques, sortie (1, 1). Sortie : `models/eveil/astrel.onnx` + fiche
`astrel.json` (données, mesures de validation, seuil conseillé).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / ".eveil-donnees"
FEAT = DATA / "feat"
OUT = ROOT / "models" / "eveil"
FRAME_S = 0.08


def load(name: str) -> np.ndarray:
    p = FEAT / f"{name}.npy"
    return np.load(p) if p.exists() else np.zeros((0, 16, 96), np.float16)


class _Parts:
    """Tranches memmap vues comme un seul tableau (lecture à la demande, rien chargé d'avance)."""

    def __init__(self, arrs: list[np.ndarray]) -> None:
        self.arrs = arrs
        self.ends = np.cumsum([len(a) for a in arrs])

    def __len__(self) -> int:
        return int(self.ends[-1]) if len(self.arrs) else 0

    def __getitem__(self, idx: np.ndarray) -> np.ndarray:
        part = np.searchsorted(self.ends, idx, side="right")
        starts = np.concatenate([[0], self.ends[:-1]])
        return np.stack([self.arrs[p][i - starts[p]] for p, i in zip(part, idx)])


def acav():
    parts = sorted((DATA / "acav").glob("part*.bin"))
    return _Parts([np.memmap(p, dtype="<f2", mode="r").reshape(-1, 16, 96) for p in parts])


class Net(nn.Module):
    def __init__(self, dim: int = 64, dropout: float = 0.0) -> None:
        super().__init__()
        self.body = nn.Sequential(nn.Flatten(), nn.Dropout(dropout), nn.Linear(16 * 96, dim), nn.LayerNorm(dim),
                                  nn.ReLU(), nn.Dropout(dropout), nn.Linear(dim, dim), nn.LayerNorm(dim), nn.ReLU(),
                                  nn.Linear(dim, 1))

    def forward(self, x):  # logits
        return self.body(x)


class Exported(nn.Module):
    def __init__(self, net: Net) -> None:
        super().__init__()
        self.net = net

    def forward(self, x):
        return torch.sigmoid(self.net(x))


@torch.no_grad()
def scores(net: Net, x: np.ndarray, batch: int = 8192) -> np.ndarray:
    net.eval()
    out = [torch.sigmoid(net(torch.from_numpy(np.asarray(x[i:i + batch], dtype=np.float32)))).numpy()[:, 0]
           for i in range(0, len(x), batch)]
    net.train()
    return np.concatenate(out) if out else np.zeros(0)


def stream_windows(frames: np.ndarray) -> np.ndarray:
    """Flux continu (n, 96) → fenêtres glissantes (n-15, 16, 96), une par bloc de 80 ms."""
    from numpy.lib.stride_tricks import sliding_window_view

    return sliding_window_view(frames, (16, 96))[:, 0]


def fires(s: np.ndarray, thr: float, patience: int, cooldown_s: float = 2.0) -> int:
    """Réveils d'un flux de scores (patience blocs de suite au-dessus du seuil, puis repos)."""
    n, hits, last = 0, 0, -1e9
    for i, v in enumerate(s):
        hits = hits + 1 if v >= thr else 0
        if hits >= patience and (i - last) * FRAME_S >= cooldown_s:
            n, hits, last = n + 1, 0, i
    return n


def per_clip_max(s: np.ndarray, ids: np.ndarray) -> np.ndarray:
    if not len(ids):
        return np.zeros(0)
    out = np.full(ids.max() + 1, -1.0)
    np.maximum.at(out, ids, s)
    return out[out >= 0]


def per_clip_patience(s: np.ndarray, ids: np.ndarray, thr: float, patience: int) -> float:
    """Part des extraits où `patience` fenêtres consécutives dépassent le seuil."""
    ok, total = 0, 0
    for c in np.unique(ids):
        total += 1
        ok += fires(s[ids == c], thr, patience) > 0
    return ok / total if total else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pas", type=int, default=12000)
    ap.add_argument("--lot", type=int, default=1024)
    ap.add_argument("--dim", type=int, default=64)
    ap.add_argument("--poids-neg", type=float, default=8.0, help="poids final des négatifs (rampe depuis 1)")
    ap.add_argument("--sans-acav", action="store_true")
    ap.add_argument("--dropout", type=float, default=0.0)
    ap.add_argument("--part-acav", type=float, default=0.25, help="part des négatifs généraux dans chaque lot")
    ap.add_argument("--fils", type=int, default=4, help="fils torch (processeur)")
    ap.add_argument("--nom", default="astrel")
    ap.add_argument("--graine", type=int, default=36)
    args = ap.parse_args()
    torch.set_num_threads(args.fils)
    torch.manual_seed(args.graine)
    rng = np.random.default_rng(args.graine)

    pos = load("train_pos")
    neg = np.concatenate([load("train_neg"), load("train_pos_neg")])
    piege = load("train_piege")
    gen = acav() if not args.sans_acav else _Parts([])
    print(f"entraînement : pos {len(pos)}, neg {len(neg)}, pièges {len(piege)}, ACAV {len(gen)}", flush=True)

    sources = [(pos, 1.0, 0.30), (neg, 0.0, 0.25), (piege, 0.0, 0.20), (gen, 0.0, args.part_acav)]
    sources = [s for s in sources if len(s[0])]
    total_share = sum(s[2] for s in sources)
    net = Net(args.dim, args.dropout)
    opt = torch.optim.AdamW(net.parameters(), lr=1e-3, weight_decay=1e-3)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=2e-3, total_steps=args.pas, pct_start=0.1)
    bce = nn.BCEWithLogitsLoss(reduction="none")
    t0 = time.time()
    for step in range(args.pas):
        xs, ys = [], []
        for arr, label, share in sources:
            k = max(1, int(args.lot * share / total_share))
            idx = np.sort(rng.integers(0, len(arr), k))  # trié : lecture memmap plus rapide
            xs.append(np.asarray(arr[idx], dtype=np.float32))
            ys.append(np.full(k, label, np.float32))
        x = torch.from_numpy(np.concatenate(xs))
        y = torch.from_numpy(np.concatenate(ys))
        # Petite augmentation dans l'espace des caractéristiques (gain, bruit).
        x = x * (1 + 0.05 * torch.randn(len(x), 1, 1)) + 0.05 * torch.randn_like(x)
        w_neg = 1.0 + (args.poids_neg - 1.0) * min(1.0, step / (0.6 * args.pas))
        w = torch.where(y > 0.5, torch.ones_like(y), torch.full_like(y, w_neg))
        loss = (bce(net(x)[:, 0], y) * w).mean()
        opt.zero_grad()
        loss.backward()
        opt.step()
        sched.step()
        if step % 1000 == 0 or step == args.pas - 1:
            print(f"   pas {step}/{args.pas} perte {loss.item():.4f} poids neg {w_neg:.1f} ({time.time() - t0:.0f} s)",
                  flush=True)

    # --- validation (voix et locuteurs jamais appris ; flux réel de 10,7 h)
    val = {}
    s_pos = scores(net, load("val_pos"))
    ids_pos = np.load(FEAT / "val_pos_clip.npy") if (FEAT / "val_pos_clip.npy").exists() else np.zeros(0, int)
    s_neg = scores(net, load("val_neg"))
    s_piege = scores(net, load("val_piege"))
    ids_neg = np.load(FEAT / "val_neg_clip.npy") if (FEAT / "val_neg_clip.npy").exists() else np.zeros(0, int)
    ids_piege = np.load(FEAT / "val_piege_clip.npy") if (FEAT / "val_piege_clip.npy").exists() else np.zeros(0, int)
    real = np.load(DATA / "validation_set_features.npy", mmap_mode="r")
    s_real = scores(net, stream_windows(np.asarray(real, dtype=np.float32)))
    hours_real = len(real) * FRAME_S / 3600
    table = []
    for thr in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        for patience in (1, 2, 3):
            row = {
                "seuil": thr, "patience": patience,
                "rappel_val": round(per_clip_patience(s_pos, ids_pos, thr, patience), 3),
                "pieges_pris": round(per_clip_patience(s_piege, ids_piege, thr, patience), 3),
                "phrases_prises": round(per_clip_patience(s_neg, ids_neg, thr, patience), 3),
                "faux_par_h_reel": round(fires(s_real, thr, patience) / hours_real, 2),
            }
            table.append(row)
            print("   " + json.dumps(row, ensure_ascii=False), flush=True)
    val["table"] = table
    val["heures_reelles"] = round(hours_real, 2)
    val["val_extraits_pos"] = int(len(np.unique(ids_pos)))

    OUT.mkdir(parents=True, exist_ok=True)
    onnx_path = OUT / f"{args.nom}.onnx"
    exp = Exported(net).eval()
    torch.onnx.export(exp, torch.zeros(1, 16, 96), str(onnx_path), input_names=["x"], output_names=["score"],
                      opset_version=13, dynamo=False)
    # Contrôle : onnxruntime rend les mêmes scores que torch.
    import onnxruntime as ort

    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    probe = np.asarray(load("val_pos")[:50], dtype=np.float32)
    o = np.array([sess.run(None, {"x": p[None]})[0][0][0] for p in probe])
    ecart = float(np.abs(o - scores(net, probe)).max()) if len(probe) else 0.0
    meta = {
        "date": time.strftime("%Y-%m-%d %H:%M"), "nom": args.nom, "pas": args.pas, "dim": args.dim,
        "poids_neg": args.poids_neg, "dropout": args.dropout, "part_acav": args.part_acav, "acav": not args.sans_acav, "graine": args.graine,
        "donnees": json.loads((FEAT / "stats.json").read_text()) if (FEAT / "stats.json").exists() else {},
        "licence": ("CC BY-NC-SA 4.0 (négatifs ACAV100M d'openWakeWord) : usage NON commercial"
                    if not args.sans_acav else "voix de synthèse seules (Piper, edge-tts)"),
        "ecart_onnx_torch": ecart, "validation": val,
    }
    (OUT / f"{args.nom}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"modèle : {onnx_path} (écart onnx/torch {ecart:.2e}) ; fiche {OUT / (args.nom + '.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
