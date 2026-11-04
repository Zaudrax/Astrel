#!/usr/bin/env python3
"""Rendement par métier d'agent (04/11/2026, carte T50 « mesures honnêtes »). LECTURE SEULE.

    .venv/bin/python tools/rendement_metiers.py              # 14 derniers jours
    .venv/bin/python tools/rendement_metiers.py --days 30 --json

Par métier (codeur, testeur, relecteur, chercheur…) : lancements, tokens de TRAVAIL (entrée hors
cache + création de cache + sortie ; le cache relu est montré à part), fiches de passation rendues,
part de fiches sans preuve, renvois vers le codeur, travail par fiche.

Sources :
- `logs/agents-couts.jsonl` (écrit par le cœur à la fin de chaque sous-agent depuis le 04/11) :
  coût exact, jointure exacte avec les fiches par ses dépôts ;
- avant lui, les transcriptions du CLI (`tools/usage_report.py`) jointes aux fiches par métier et par
  heure (fiche écrite entre le début de l'agent et 2 min après sa fin) ;
- `logs/route.jsonl` (lancements) et `runtime/equipe/*/fiches.jsonl` (passations).
L'ancien nom d'Astrel dans les fiches (`a`) est ramené à « astrel ».
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COUTS = ROOT / "logs" / "agents-couts.jsonl"
ROUTE = ROOT / "logs" / "route.jsonl"
EQUIPE = ROOT / "runtime" / "equipe"
NON_VERIFIE = "non vérifié"  # astrel.equipe.NON_VERIFIE (pas importé : outil lisible sans le paquet)
FENETRE_S = 120.0
_ASTREL = re.compile(r"^(?:astrel|jarvis)$")  # renommage du 29/10 (forme permise par tests/test_renommage_astrel.py)


def _usage_report():
    spec = importlib.util.spec_from_file_location("usage_report", ROOT / "tools" / "usage_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _jsonl(path: Path) -> list[dict]:
    out = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def _epoch(value) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (dt if dt.tzinfo else dt.astimezone()).timestamp()


def _metier(nom: str) -> str:
    nom = str(nom or "?").strip().lower()
    return "astrel" if _ASTREL.match(nom) else nom


def fiches(equipe: Path, since: float) -> list[dict]:
    out = []
    for path in sorted(equipe.glob("*/fiches.jsonl")):
        for f in _jsonl(path):
            at = _epoch(f.get("at"))
            if at >= since:
                out.append({**f, "de": _metier(f.get("de")), "a": _metier(f.get("a")), "at": at,
                            "mission": path.parent.name})
    return out


def agents_mesures(couts: Path, transcripts: list[Path], since: float) -> list[dict]:
    """Un agent = {type, debut, fin, travail, cache_lu, exact, depots}. Le journal du cœur prime ; les
    transcriptions couvrent ce qui le précède (premier horodatage du journal)."""
    exacts = []
    for c in _jsonl(couts):
        fin = _epoch(c.get("fin"))
        if fin >= since:
            exacts.append({"type": _metier(c.get("type")), "debut": fin - float(c.get("duree_s") or 0.0), "fin": fin,
                           "travail": int(c.get("travail") or 0), "cache_lu": int(c.get("cache_lu") or 0),
                           "exact": True, "depots": c.get("depots") or []})
    debut_journal = min((a["debut"] for a in exacts), default=float("inf"))
    anciens = []
    if transcripts:
        ur = _usage_report()
        rep = ur.collect_all(transcripts, since=datetime.fromtimestamp(since, timezone.utc))
        for a in rep["agents"]:
            debut, fin = _epoch(a.get("start_ts")), _epoch(a.get("end_ts"))
            if debut and debut < debut_journal:
                anciens.append({"type": _metier(a.get("type")), "debut": debut, "fin": fin or debut,
                                "travail": int(a.get("travail") or 0), "cache_lu": int(a.get("cache_read_input_tokens") or 0),
                                "exact": False, "depots": []})
    return exacts + anciens


def rendement(agents: list[dict], fiches_vues: list[dict], route: list[dict], since: float) -> dict[str, dict]:
    par: dict[str, dict] = defaultdict(lambda: {"lancements": 0, "agents": 0, "travail": [], "cache_lu": 0,
                                                 "fiches": 0, "sans_preuve": 0, "vers_codeur": 0, "sans_fiche": 0})
    for r in route:
        if _epoch(r.get("at")) >= since:
            par[_metier(r.get("type"))]["lancements"] += 1
    restantes = list(fiches_vues)
    for a in sorted(agents, key=lambda a: a["debut"]):
        m = par[a["type"]]
        m["agents"] += 1
        m["travail"].append(a["travail"])
        m["cache_lu"] += a["cache_lu"]
        if a["exact"] and a["depots"]:
            cles = {(d.get("mission"), _metier(d.get("de"))) for d in a["depots"] if d.get("outil") == "passation"}
            siennes = [f for f in restantes if (f["mission"], f["de"]) in cles and a["debut"] <= f["at"] <= a["fin"] + FENETRE_S]
        else:
            siennes = [f for f in restantes if f["de"] == a["type"] and a["debut"] <= f["at"] <= a["fin"] + FENETRE_S]
        for f in siennes:
            restantes.remove(f)
            m["fiches"] += 1
            m["sans_preuve"] += str(f.get("preuves") or "").strip() == NON_VERIFIE
            m["vers_codeur"] += f["a"] == "codeur" and a["type"] != "codeur"
        m["sans_fiche"] += not siennes
    out = {}
    for nom, m in sorted(par.items()):
        t = sorted(m["travail"])
        total = sum(t)
        out[nom] = {
            "lancements": m["lancements"], "agents_mesures": m["agents"], "travail_total": total,
            "travail_mediane": int(statistics.median(t)) if t else 0,
            "travail_p90": t[min(len(t) - 1, int(0.9 * len(t)))] if t else 0,
            "cache_lu": m["cache_lu"], "fiches": m["fiches"],
            "fiches_par_agent": round(m["fiches"] / m["agents"], 2) if m["agents"] else 0.0,
            "part_sans_preuve": round(m["sans_preuve"] / m["fiches"], 2) if m["fiches"] else 0.0,
            "renvois_vers_codeur": m["vers_codeur"], "agents_sans_fiche": m["sans_fiche"],
            "travail_par_fiche": int(total / m["fiches"]) if m["fiches"] else 0,
        }
    return out


def _fmt(n: int) -> str:
    return f"{n / 1e6:.1f} M" if n >= 1_000_000 else f"{n / 1e3:.0f} k" if n >= 1_000 else str(n)


def render(table: dict[str, dict], days: float) -> str:
    head = (f"{'métier':20}{'lanc.':>6}{'mesurés':>8}{'travail':>9}{'médiane':>9}{'p90':>8}{'fiches':>7}"
            f"{'/agent':>7}{'sans preuve':>12}{'→ codeur':>9}{'trav./fiche':>12}")
    lignes = [f"Rendement par métier, {days:g} derniers jours (travail = hors cache relu)", "", head, "-" * len(head)]
    for nom, r in sorted(table.items(), key=lambda kv: -kv[1]["travail_total"]):
        lignes.append(f"{nom[:19]:20}{r['lancements']:>6}{r['agents_mesures']:>8}{_fmt(r['travail_total']):>9}"
                      f"{_fmt(r['travail_mediane']):>9}{_fmt(r['travail_p90']):>8}{r['fiches']:>7}"
                      f"{r['fiches_par_agent']:>7}{round(100 * r['part_sans_preuve']):>11}%{r['renvois_vers_codeur']:>9}"
                      f"{_fmt(r['travail_par_fiche']):>12}")
    return "\n".join(lignes)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rendement par métier d'agent (lecture seule).")
    ap.add_argument("--days", type=float, default=14.0)
    ap.add_argument("--dir", type=Path, action="append", help="transcriptions du CLI (répétable)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp()
    transcripts = [d for d in (args.dir or [_usage_report().DEFAULT_DIR]) if d.is_dir()]
    table = rendement(agents_mesures(COUTS, transcripts, since), fiches(EQUIPE, since), _jsonl(ROUTE), since)
    print(json.dumps(table, ensure_ascii=False, indent=2) if args.json else render(table, args.days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
