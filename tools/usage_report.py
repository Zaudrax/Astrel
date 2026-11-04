#!/usr/bin/env python3
"""Consommation de tokens des N derniers jours, lue dans les transcriptions du CLI.

    .venv/bin/python tools/usage_report.py              # 7 derniers jours
    .venv/bin/python tools/usage_report.py --days 1 --top 15
    .venv/bin/python tools/usage_report.py --days 30 --json > /tmp/usage.json
    .venv/bin/python tools/usage_report.py --from 2026-10-21 --to 2026-10-23   # jours entiers, heure locale

Source : `~/.claude/projects/-home-user-Projets/` (`--dir` pour un autre) :
`<session>.jsonl` (fil principal) et `<session>/subagents/agent-*.jsonl` (+ `.meta.json` :
type et description de l'agent). Chaque réponse du modèle (un `message.id`,
répété une fois par bloc de contenu) compte pour UNE requête ; ses compteurs
sont pris au plus haut des répétitions.

Colonnes : requêtes, entrée (non cachée), cache lu, cache écrit, sortie, puis TRAVAIL (04/11, T50 :
entrée + cache écrit + sortie, ce qui a été écrit une fois) et la part du cache lu. Sur
l'abonnement, le cache lu domine : c'est le contexte relu à chaque tour, que
les relèves de session et les briefs d'agents serrés font baisser ; les alertes et les budgets
de mission d'Astrel comptent le travail, pas le cache relu.
`--dir` se répète (plusieurs dossiers de transcriptions additionnés).
« Origine » : `sdk-cli` = Astrel (claude -p), `claude-vscode` = l'utilisateur dans VS
Code, `cli` = terminal. Mesure, pas facture : aucun prix n'est calculé.

« Sorties Bash » (20/10, plan P12, lot 2 : mesure de rtk avant/après) : caractères des
`tool_result` de l'outil Bash, par programme (`git status`, `ls`, `grep`…), relus par le
modèle. Comparer deux périodes de même longueur, sans puis avec rtk (`--from/--to`).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DIR = Path.home() / ".claude" / "projects" / "-home-user-Projets"
FIELDS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
LABELS = ("entrée", "cache lu", "cache écrit", "sortie")
WORK = ("input_tokens", "cache_creation_input_tokens", "output_tokens")  # 04/11 (T50) : le travail


def travail(row: dict) -> int:
    return sum(int(row.get(f) or 0) for f in WORK)
ORIGINS = {"sdk-cli": "Astrel", "claude-vscode": "VS Code", "cli": "terminal"}


def _ts(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def read_file(path: Path, since: datetime, until: datetime | None = None) -> tuple[dict[str, dict], str]:
    """Requêtes du fichier entre `since` et `until` : id → {model, ts, compteurs} ; et l'origine."""
    msgs: dict[str, dict] = {}
    origin = ""
    try:
        fh = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return msgs, origin
    with fh:
        for line in fh:
            if '"assistant"' not in line and not (not origin and '"entrypoint"' in line):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not origin and d.get("entrypoint"):
                origin = str(d["entrypoint"])
            if d.get("type") != "assistant":
                continue
            msg = d.get("message") or {}
            usage = msg.get("usage") or {}
            model = str(msg.get("model") or "")
            if not usage or model in ("", "<synthetic>"):
                continue
            ts = _ts(d.get("timestamp", ""))
            if ts is None or ts < since or (until is not None and ts >= until):
                continue
            mid = str(msg.get("id") or d.get("uuid"))
            cur = msgs.setdefault(mid, {"model": model, "ts": ts, **{f: 0 for f in FIELDS}})
            for f in FIELDS:
                v = usage.get(f)
                if isinstance(v, (int, float)) and v > cur[f]:
                    cur[f] = int(v)
    return msgs, origin


# Programmes dont le sous-commande compte (« git status » et « git diff » ne se comparent pas)
_WITH_SUB = frozenset({"git", "rtk", "npm", "npx", "uv", "gh", "docker", "cargo", "pip", "systemctl"})
_ASSIGN = re.compile(r"^[A-Za-z_]\w*=")


def bash_program(command: str) -> str:
    """Programme d'une commande Bash, pour regrouper ses sorties : premier segment qui
    n'est pas un `cd`, sans variables posées ni `env`/`timeout` devant ; sous-commande
    gardée pour git, npm, rtk… (« cd x && git status | head » → « git status »)."""
    for seg in re.split(r"&&|\|\||[;|\n]", command):
        words = seg.split()
        while words:
            if _ASSIGN.match(words[0]) or words[0] in ("env", "nice", "command"):
                words.pop(0)
            elif words[0] == "timeout":
                words = [w for w in words[1:] if not re.fullmatch(r"-\S+|\d+[smhd]?", w)] if len(words) > 1 else []
            else:
                break
        if not words or words[0] == "cd":
            continue
        prog = os.path.basename(words[0])
        if prog in _WITH_SUB and len(words) > 1 and not words[1].startswith("-"):
            prog += " " + words[1]
        return prog
    return "?"


def _result_chars(content) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(str(c.get("text") or "")) for c in content if isinstance(c, dict) and c.get("type") == "text")
    return 0


def bash_outputs(path: Path, since: datetime, until: datetime | None = None) -> dict[str, dict]:
    """Sorties de l'outil Bash du fichier : programme → {calls, chars} (taille des tool_result)."""
    pending: dict[str, str] = {}   # id de l'appel → programme
    out: dict[str, dict] = {}
    try:
        fh = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return out
    with fh:
        for line in fh:
            if '"tool_use"' not in line and '"tool_result"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            ts = _ts(d.get("timestamp", ""))
            if ts is None or ts < since or (until is not None and ts >= until):
                continue
            content = (d.get("message") or {}).get("content")
            for c in content if isinstance(content, list) else ():
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use" and c.get("name") == "Bash":
                    pending[str(c.get("id"))] = bash_program(str((c.get("input") or {}).get("command") or ""))
                elif c.get("type") == "tool_result" and str(c.get("tool_use_id")) in pending:
                    row = out.setdefault(pending.pop(str(c.get("tool_use_id"))), {"calls": 0, "chars": 0})
                    row["calls"] += 1
                    row["chars"] += _result_chars(c.get("content"))
    return out


def collect(folder: Path, days: float = 7.0, since: datetime | None = None,
            until: datetime | None = None) -> dict:
    since = since or datetime.now(timezone.utc) - timedelta(days=days)
    if until is not None:
        days = (until - since).total_seconds() / 86400
    cutoff = since.timestamp() - 3600  # un fichier non touché depuis : rien de récent dedans
    bash: dict[str, dict] = defaultdict(lambda: {"calls": 0, "chars": 0})

    def add_bash(path: Path) -> None:
        for prog, row in bash_outputs(path, since, until).items():
            bash[prog]["calls"] += row["calls"]
            bash[prog]["chars"] += row["chars"]
    table: dict[tuple[str, str, str], dict] = defaultdict(lambda: {"requests": 0, **{f: 0 for f in FIELDS}})
    agents: list[dict] = []
    by_day: dict[str, int] = defaultdict(int)
    by_day_work: dict[str, int] = defaultdict(int)

    def add(kind: str, origin: str, msgs: dict[str, dict]) -> dict:
        tot = {"requests": 0, **{f: 0 for f in FIELDS}}
        for m in msgs.values():
            row = table[(kind, ORIGINS.get(origin, origin or "?"), m["model"])]
            row["requests"] += 1
            tot["requests"] += 1
            for f in FIELDS:
                row[f] += m[f]
                tot[f] += m[f]
            day = m["ts"].astimezone().strftime("%Y-%m-%d")
            by_day[day] += sum(m[f] for f in FIELDS)
            by_day_work[day] += travail(m)
        return tot

    for main in sorted(folder.glob("*.jsonl")):
        sub_dir = main.with_suffix("") / "subagents"
        subs = sorted(sub_dir.glob("agent-*.jsonl")) if sub_dir.is_dir() else []
        if main.stat().st_mtime < cutoff and all(s.stat().st_mtime < cutoff for s in subs):
            continue
        msgs, origin = read_file(main, since, until)
        add("principal", origin, msgs)
        add_bash(main)
        for sub in subs:
            if sub.stat().st_mtime < cutoff:
                continue
            add_bash(sub)
            smsgs, _ = read_file(sub, since, until)
            if not smsgs:
                continue
            tot = add("sous-agent", origin, smsgs)
            try:
                meta = json.loads(sub.with_name(sub.stem + ".meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
            models = sorted({m["model"] for m in smsgs.values()})
            first = min(m["ts"] for m in smsgs.values())
            last = max(m["ts"] for m in smsgs.values())
            agents.append({
                "session": main.stem[:8], "agent": sub.stem.removeprefix("agent-")[:10],
                "type": meta.get("agentType", "?"), "description": meta.get("description", ""),
                "model": ",".join(m.replace("claude-", "") for m in models),
                "start": first.astimezone().strftime("%d/%m %H:%M"),
                "start_ts": first.isoformat(), "end_ts": last.isoformat(),  # 04/11 : jointure avec les fiches
                "total": sum(tot[f] for f in FIELDS), "travail": travail(tot), **tot,
            })
    agents.sort(key=lambda a: -a["total"])
    rows = [{"kind": k, "origin": o, "model": m, **v} for (k, o, m), v in sorted(table.items())]
    return {"days": days, "since": since.isoformat(), "until": until.isoformat() if until else "",
            "rows": rows, "agents": agents, "by_day": dict(sorted(by_day.items())),
            "by_day_work": dict(sorted(by_day_work.items())),
            "bash": dict(sorted(bash.items(), key=lambda kv: -kv[1]["chars"]))}


def collect_all(folders: list[Path], days: float = 7.0, since: datetime | None = None,
                until: datetime | None = None) -> dict:
    """`collect` sur plusieurs dossiers (04/11, `--dir` répété), additionnés."""
    reports = [collect(f, days, since=since, until=until) for f in folders]
    out = dict(reports[0])
    for other in reports[1:]:
        rows = {(r["kind"], r["origin"], r["model"]): dict(r) for r in out["rows"]}
        for r in other["rows"]:
            cur = rows.setdefault((r["kind"], r["origin"], r["model"]), {**r, "requests": 0, **{f: 0 for f in FIELDS}})
            for k in ("requests", *FIELDS):
                cur[k] += r[k]
        out["rows"] = [rows[k] for k in sorted(rows)]
        out["agents"] = sorted(out["agents"] + other["agents"], key=lambda a: -a["total"])
        for key in ("by_day", "by_day_work"):
            merged = defaultdict(int, out[key])
            for d, v in other[key].items():
                merged[d] += v
            out[key] = dict(sorted(merged.items()))
        bash = defaultdict(lambda: {"calls": 0, "chars": 0}, {k: dict(v) for k, v in out["bash"].items()})
        for k, v in other["bash"].items():
            bash[k]["calls"] += v["calls"]
            bash[k]["chars"] += v["chars"]
        out["bash"] = dict(sorted(bash.items(), key=lambda kv: -kv[1]["chars"]))
    return out


def _fmt(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} M"
    if n >= 1_000:
        return f"{n / 1_000:.0f} k"
    return str(n)


def _work_cols(r: dict) -> str:
    total = sum(r[f] for f in FIELDS) or 1
    return f"{_fmt(travail(r)):>12}{100 * r['cache_read_input_tokens'] / total:>8.0f}%"


def render(report: dict, top: int) -> str:
    if report.get("until"):
        title = (f"Consommation du {report['since'][:16].replace('T', ' ')} au "
                 f"{report['until'][:16].replace('T', ' ')} (heure locale, {report['days']:g} j)")
    else:
        title = f"Consommation des {report['days']:g} derniers jours (depuis {report['since'][:16]} UTC)"
    out = [title, ""]
    head = (f"{'':11}{'origine':9}{'modèle':20}{'requêtes':>9}" + "".join(f"{l:>12}" for l in LABELS)
            + f"{'travail':>12}{'cache %':>9}")
    out.append(head)
    out.append("-" * len(head))
    totals: dict[str, dict] = defaultdict(lambda: {"requests": 0, **{f: 0 for f in FIELDS}})
    for r in report["rows"]:
        out.append(f"{r['kind']:11}{r['origin']:9}{r['model'].replace('claude-', ''):20}{r['requests']:>9}"
                   + "".join(f"{_fmt(r[f]):>12}" for f in FIELDS) + _work_cols(r))
        for key in (r["kind"], "TOTAL"):
            totals[key]["requests"] += r["requests"]
            for f in FIELDS:
                totals[key][f] += r[f]
    out.append("-" * len(head))
    for key in ("principal", "sous-agent", "TOTAL"):
        t = totals.get(key)
        if t:
            out.append(f"{key:11}{'':9}{'':20}{t['requests']:>9}" + "".join(f"{_fmt(t[f]):>12}" for f in FIELDS)
                       + _work_cols(t))
    grand = totals.get("TOTAL")
    if grand:
        alltok = sum(grand[f] for f in FIELDS) or 1
        out.append("")
        out.append(f"Part du cache lu : {100 * grand['cache_read_input_tokens'] / alltok:.0f} % des tokens ; "
                   f"sous-agents : {100 * sum(totals['sous-agent'][f] for f in FIELDS) / alltok:.0f} %.")
    if report["by_day"]:
        out.append("")
        out.append("Par jour (tous tokens) : " + ", ".join(f"{d[5:]} {_fmt(v)}" for d, v in report["by_day"].items()))
        work = report.get("by_day_work") or {}
        out.append("Par jour (travail)     : " + ", ".join(f"{d[5:]} {_fmt(v)}" for d, v in work.items()))
    if report["agents"] and top:
        out.append("")
        out.append(f"Les {min(top, len(report['agents']))} plus gros sous-agents :")
        for a in report["agents"][:top]:
            out.append(f"  {_fmt(a['total']):>7}  trav. {_fmt(a.get('travail', 0)):>6}  {a['requests']:>4} req.  "
                       f"{a['model']:<22} {a['type']:<16} "
                       f"{a['start']}  {a['description'][:60]}")
    bash = report.get("bash") or {}
    if bash and top:
        total = sum(r["chars"] for r in bash.values())
        out.append("")
        out.append(f"Sorties Bash relues par le modèle : {_fmt(total)} caractères, "
                   f"{sum(r['calls'] for r in bash.values())} appels ; les {min(top, len(bash))} plus lourds :")
        for prog, r in list(bash.items())[:top]:
            out.append(f"  {_fmt(r['chars']):>7} car.  {r['calls']:>5} appels  "
                       f"{_fmt(r['chars'] // max(1, r['calls'])):>6} par appel  {prog[:40]}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Consommation de tokens (transcriptions du CLI claude).")
    ap.add_argument("--days", type=float, default=7.0, help="période, en jours (7 par défaut)")
    ap.add_argument("--from", dest="date_from", help="premier jour AAAA-MM-JJ (heure locale), au lieu de --days")
    ap.add_argument("--to", dest="date_to", help="dernier jour AAAA-MM-JJ, compris (aujourd'hui par défaut)")
    ap.add_argument("--top", type=int, default=10, help="nombre de gros sous-agents listés")
    ap.add_argument("--dir", type=Path, action="append",
                    help="dossier des transcriptions (répétable ; défaut : celui du cerveau d'Astrel)")
    ap.add_argument("--json", action="store_true", help="sortie JSON")
    args = ap.parse_args(argv)
    folders = args.dir or [DEFAULT_DIR]
    for folder in folders:
        if not folder.is_dir():
            print(f"dossier introuvable : {folder}", file=sys.stderr)
            return 1
    since = until = None
    try:
        if args.date_from:
            since = datetime.strptime(args.date_from, "%Y-%m-%d").astimezone()
            until = (datetime.strptime(args.date_to, "%Y-%m-%d").astimezone() + timedelta(days=1)
                     if args.date_to else datetime.now().astimezone())
        elif args.date_to:
            print("--to demande --from", file=sys.stderr)
            return 2
    except ValueError:
        print("date attendue : AAAA-MM-JJ", file=sys.stderr)
        return 2
    if since is not None and until <= since:
        print("--to est avant --from", file=sys.stderr)
        return 2
    report = collect_all(folders, args.days, since=since, until=until)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(render(report, args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
