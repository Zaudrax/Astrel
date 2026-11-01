"""Banc d'essai de régression d'Astrel (plan P3, lot C ; demande du 17/10).

Scénarios : `config/banc/scenarios.json` (18). Deux paliers :

- Palier 1, 0 token : fonctions pures sur un corpus (`guard.classify`,
  `memory_scan`, `silence`, `rappel`). Lancé par pytest (`tests/test_banc.py`)
  et par `tools/banc_essai.py`.
- Palier 2, À LA DEMANDE (choix de l'utilisateur) : un vrai `claude -p` en haiku,
  `--restricted --strict-mcp-config --tools ""`, sans session gardée, avec le
  SEUL serveur MCP factice `astrel/banc_mcp.py` (outils qui journalisent l'appel
  sans rien faire) et le prompt d'Astrel en ajout. On vérifie les outils
  appelés, les outils interdits, une expression sur la réponse.

Garde-fous du palier 2 :
- refus de démarrer un scénario si l'init du CLI annonce un outil hors des
  outils simulés, ou un autre serveur MCP (connecteurs claude.ai compris : il
  n'est PAS établi que `--strict-mcp-config` les écarte, ce contrôle est le filet) ;
- plafond de 1 M tokens par exécution (plus de nouveau scénario au-delà, et
  `--max-budget-usd` par scénario), délai maximal par scénario ;
- un verrou (une exécution à la fois) et une exécution par heure sans `--force` ;
- jamais pendant un tour lancé par un réflexe (le cœur est interrogé), jamais
  sous arrêt d'urgence (`runtime/ESTOP`). Aucun réflexe ne le lance.

Résultats : `runtime/banc/derniere.json` (comparaison d'une exécution à l'autre).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from . import config

try:  # plan P6 : fcntl n'existe pas sous Windows (verrou par msvcrt, `_lock`)
    import fcntl
except ImportError:  # pragma: no cover — Windows
    fcntl = None  # type: ignore[assignment]

SCENARIOS_FILE = config.CONFIG_DIR / "banc" / "scenarios.json"
BANC_DIR = config.RUNTIME / "banc"
TOKEN_CAP = 1_000_000
MIN_INTERVAL_S = 3600.0
MODEL = "haiku"
SCENARIO_BUDGET_USD = 0.30   # équivalent API, par scénario (`--max-budget-usd`)
SCENARIO_TIMEOUT_S = 180.0
SERVER = "banc"


class BancRefuse(RuntimeError):
    """Le palier 2 ne démarre pas (ou s'arrête) : raison en français."""


def load(path: Path | None = None) -> list[dict]:
    data = json.loads((path or SCENARIOS_FILE).read_text(encoding="utf-8"))
    scenarios = data["scenarios"]
    ids = [s["id"] for s in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("identifiants de scénario en double")
    return scenarios


def _fill_root(value):
    if isinstance(value, str):
        return value.replace("{root}", str(config.ROOT))
    if isinstance(value, dict):
        return {k: _fill_root(v) for k, v in value.items()}
    return value


# --------------------------------------------------------------------------- palier 1

def _check1(sc: dict) -> tuple[bool, str]:
    kind, exp = sc["kind"], sc["expect"]
    if kind == "guard":
        from . import guard

        v = guard.classify(sc["tool"], _fill_root(sc.get("input") or {}), cwd=str(config.ROOT))
        seen = {"needs_confirm": v.needs_confirm, "level": v.level, "guard": v.guard}
    elif kind == "memory_scan":
        from .tools import memory_scan

        try:
            text, _ = memory_scan.clean_for_write(sc["text"])
            seen = {"refused": False, "masked": memory_scan.MASK in text}
        except ValueError:
            seen = {"refused": True, "masked": False}
    elif kind == "silence":
        from . import silence

        seen = {"silent": silence.take_marker(sc["text"])[0]}
    elif kind == "rappel":
        from . import rappel

        seen = {"automatic": rappel.is_automatic(sc["text"]),
                "trivial": rappel.is_trivial(rappel.clean(sc["text"]))}
    else:
        return False, f"type inconnu : {kind}"
    diff = {k: (want, seen.get(k)) for k, want in exp.items() if seen.get(k) != want}
    return (not diff), ("" if not diff else "; ".join(f"{k} attendu {w!r}, vu {s!r}" for k, (w, s) in diff.items()))


def run_palier1(scenarios: list[dict]) -> list[dict]:
    out = []
    for sc in scenarios:
        if sc.get("palier") != 1:
            continue
        try:
            ok, detail = _check1(sc)
        except Exception as exc:  # un scénario cassé ne fait pas tomber les autres
            ok, detail = False, f"{type(exc).__name__} : {exc}"
        out.append({"id": sc["id"], "palier": 1, "ok": ok, "detail": detail, "tokens": 0})
    return out


# --------------------------------------------------------------------------- palier 2

def allowed_tools() -> set[str]:
    from .banc_mcp import MOCKS

    return {f"mcp__{SERVER}__{name}" for name in MOCKS}


def init_refusal(event: dict) -> str:
    """Raison de refuser ce `system/init` (outil ou serveur hors du banc), ou « »."""
    tools = [str(t) for t in event.get("tools") or []]
    extra = sorted(set(tools) - allowed_tools())
    servers = event.get("mcp_servers") or []
    names = {str(s.get("name")) if isinstance(s, dict) else str(s) for s in servers}
    others = sorted(names - {SERVER})
    if extra:
        return "outils hors du banc annoncés par le CLI : " + ", ".join(extra[:10])
    if others:
        return "serveurs MCP hors du banc : " + ", ".join(others[:10])
    status = next((s.get("status") for s in servers if isinstance(s, dict) and s.get("name") == SERVER), None)
    if status not in (None, "connected"):
        return f"serveur factice non connecté ({status})"
    if not tools:
        return "aucun outil simulé annoncé"
    return ""


def _usage_tokens(usage: dict) -> int:
    return sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                 "cache_read_input_tokens", "output_tokens"))


def command(mcp_file: Path, budget_usd: float = SCENARIO_BUDGET_USD) -> list[str]:
    """Le prompt passe par l'entrée standard : les options à valeurs multiples
    (`--tools`, `--mcp-config`…) avaleraient un argument placé après elles."""
    from .brain import VOICE_SYSTEM_PROMPT

    return [config.CLAUDE_BIN, "-p", "--output-format", "stream-json", "--verbose",
            "--restricted", "--strict-mcp-config", "--mcp-config", str(mcp_file),
            "--no-session-persistence", "--model", MODEL, "--max-budget-usd", f"{budget_usd:.2f}",
            "--tools", "", "--allowedTools", f"mcp__{SERVER}", "--permission-prompts", "none",
            "--append-system-prompt", VOICE_SYSTEM_PROMPT]


def _mcp_config(log_path: Path) -> dict:
    return {"mcpServers": {SERVER: {
        "type": "stdio", "command": config.VENV_PYTHON.as_posix(), "args": ["-m", "astrel.banc_mcp"],
        "env": {"PYTHONPATH": str(config.ROOT), "ASTREL_BANC_LOG": str(log_path)}}}}


def _env() -> dict:
    env = dict(os.environ)
    for key in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT"):
        env.pop(key, None)
    return env


def observe(lines: Iterator[str], on_init: Callable[[dict], None] | None = None) -> dict:
    """Lit le flux stream-json d'un `claude -p` : init, outils appelés, réponse, tokens."""
    seen = {"tools": [], "reply": "", "tokens": 0, "cost_usd": 0.0, "error": "", "init": False}
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        kind = ev.get("type")
        if kind == "system" and ev.get("subtype") == "init":
            seen["init"] = True
            if on_init:
                on_init(ev)
        elif kind == "assistant":
            for block in (ev.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = str(block.get("name") or "")
                    seen["tools"].append(name.removeprefix(f"mcp__{SERVER}__"))
        elif kind == "result":
            seen["reply"] = str(ev.get("result") or "")
            seen["tokens"] = _usage_tokens(ev.get("usage") or {})
            seen["cost_usd"] = float(ev.get("total_cost_usd") or 0.0)
            if ev.get("is_error") or ev.get("subtype") not in (None, "success"):
                seen["error"] = str(ev.get("subtype") or "erreur") + (f" : {seen['reply'][:200]}" if seen["reply"] else "")
    return seen


def judge2(sc: dict, seen: dict) -> tuple[bool, str]:
    exp, called = sc["expect"], seen["tools"]
    problems = []
    if seen.get("error"):
        problems.append(f"erreur du CLI ({seen['error']})")
    missing = [t for t in exp.get("tools", []) if t not in called]
    if missing:
        problems.append("outils non appelés : " + ", ".join(missing))
    if exp.get("any_of") and not set(exp["any_of"]) & set(called):
        problems.append("aucun de : " + ", ".join(exp["any_of"]))
    forbidden = exp.get("forbidden", [])
    bad = list(called) if "*" in forbidden else [t for t in called if t in forbidden]
    if bad:
        problems.append("outils interdits appelés : " + ", ".join(dict.fromkeys(bad)))
    if exp.get("reply_regex") and not re.search(exp["reply_regex"], seen.get("reply", "")):
        problems.append(f"réponse sans /{exp['reply_regex']}/")
    return (not problems), "; ".join(problems)


def run_scenario2(sc: dict, workdir: Path, popen: Callable = subprocess.Popen,
                  timeout_s: float = SCENARIO_TIMEOUT_S) -> dict:
    """Un scénario du palier 2. Lève `BancRefuse` si l'init annonce un outil hors du banc."""
    log_path = workdir / f"{sc['id']}.jsonl"
    mcp_file = workdir / "mcp.json"
    mcp_file.write_text(json.dumps(_mcp_config(log_path)), encoding="utf-8")
    proc = popen(command(mcp_file), cwd=str(workdir), env=_env(), stdin=subprocess.PIPE,
                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    timer = threading.Timer(timeout_s, proc.kill)
    timer.start()

    def on_init(ev: dict) -> None:
        reason = init_refusal(ev)
        if reason:
            proc.kill()  # avant tout appel d'outil
            raise BancRefuse(reason)

    try:
        try:
            proc.stdin.write(sc["prompt"])
            proc.stdin.close()
        except OSError:  # CLI mort aussitôt (tube cassé) : on lit ce qu'il a dit, l'absence d'init le signale
            pass
        seen = observe(proc.stdout, on_init)
    finally:
        timer.cancel()
        try:
            proc.kill()
        except OSError:
            pass
        proc.wait()
    if not seen["init"] and not seen["error"]:
        seen["error"] = "pas d'init (délai dépassé ou CLI en panne)"
    ok, detail = judge2(sc, seen)
    return {"id": sc["id"], "palier": 2, "ok": ok, "detail": detail, "tokens": seen["tokens"],
            "cost_usd": round(seen["cost_usd"], 4), "tools": seen["tools"], "reply": seen["reply"][:300]}


# --------------------------------------------------------------------------- verrou, fréquence, réflexe

@contextmanager
def _lock(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    fd = os.open(folder / "verrou", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:  # Windows : verrou d'un octet, non bloquant (libéré à la fermeture)
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            raise BancRefuse("le banc d'essai tourne déjà") from None
        yield
    finally:
        os.close(fd)


def last_run(folder: Path | None = None) -> dict:
    try:
        data = json.loads(((folder or BANC_DIR) / "derniere.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_last(folder: Path, data: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / "derniere.json.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, folder / "derniere.json")


def reflex_turn() -> bool:
    """Vrai si ce processus vient de Claude (Astrel) PENDANT un tour lancé par un réflexe."""
    if not os.environ.get("CLAUDECODE"):
        return False
    from . import state

    reply = state.send_command(json.dumps({"cmd": "reflex_allowed"}), timeout=3.0)
    if reply is None:
        return False
    try:
        return not json.loads(reply).get("allowed", True)
    except ValueError:
        return False


def compare(current: list[dict], previous: list[dict]) -> list[str]:
    """Changements d'état d'un scénario d'une exécution à l'autre (« régression » ou « réparé »)."""
    before = {r["id"]: r["ok"] for r in previous}
    out = []
    for r in current:
        if r["id"] in before and before[r["id"]] != r["ok"]:
            out.append(f"{r['id']} : {'réparé' if r['ok'] else 'RÉGRESSION'}")
    return out


def run(palier: int = 1, force: bool = False, only: list[str] | None = None, token_cap: int = TOKEN_CAP,
        folder: Path | None = None, popen: Callable = subprocess.Popen, now: float | None = None) -> dict:
    """Exécute le palier 1 (0 token) et, si `palier` vaut 2, le palier 2 sous ses garde-fous."""
    folder = folder or BANC_DIR
    scenarios = [s for s in load() if not only or s["id"] in only]
    token_cap = min(int(token_cap), TOKEN_CAP)  # jamais au-dessus du plafond du plan
    results = run_palier1(scenarios)
    out = {"palier": palier, "results": results, "tokens": 0, "skipped": [], "refused": ""}
    if palier < 2:
        return _finish(out, folder, palier)
    now = time.time() if now is None else now
    if config.ESTOP_FILE.exists():
        raise BancRefuse("arrêt d'urgence posé : pas de palier 2")
    if reflex_turn():
        raise BancRefuse("refusé pendant un tour lancé par un réflexe : le palier 2 se lance à la demande")
    with _lock(folder):
        last = last_run(folder)
        started = last.get("palier2_started")
        since = now - float(started) if isinstance(started, (int, float)) else MIN_INTERVAL_S
        if since < MIN_INTERVAL_S and not force:
            raise BancRefuse(f"palier 2 déjà lancé il y a {int(since // 60)} min : une fois par heure "
                             "(--force pour passer outre)")
        last["palier2_started"] = now  # compté même si l'exécution plante
        _save_last(folder, last)
        used = biggest = 0
        with tempfile.TemporaryDirectory(prefix="astrel-banc-") as tmp:
            for sc in (s for s in scenarios if s.get("palier") == 2):
                # Pas de nouveau scénario si le précédent, refait, ferait passer le plafond.
                if used >= token_cap or used + biggest > token_cap:
                    out["skipped"].append(sc["id"])
                    continue
                try:
                    r = run_scenario2(sc, Path(tmp), popen=popen)
                except BancRefuse as exc:
                    out["refused"] = str(exc)
                    break
                used += r["tokens"]
                biggest = max(biggest, r["tokens"])
                results.append(r)
        out["tokens"] = used
    return _finish(out, folder, palier)


def _finish(out: dict, folder: Path, palier: int) -> dict:
    last = last_run(folder)
    key = "palier2" if palier >= 2 else "palier1"
    out["changes"] = compare(out["results"], (last.get(key) or {}).get("results", []))
    if not out["refused"]:
        last[key] = {"at": time.time(), "results": [{"id": r["id"], "ok": r["ok"], "palier": r["palier"]}
                                                    for r in out["results"]], "tokens": out["tokens"]}
        try:
            _save_last(folder, last)
        except OSError:
            pass
    return out


def summary(out: dict) -> str:
    lines = []
    for p in (1, 2):
        rs = [r for r in out["results"] if r["palier"] == p]
        if rs:
            ok = sum(r["ok"] for r in rs)
            extra = ", " + f"{out['tokens']:,}".replace(",", " ") + " tokens" if p == 2 else ", 0 token"
            lines.append(f"Palier {p} : {ok}/{len(rs)} réussis ({round(100 * ok / len(rs))} %){extra}")
            lines += [f"  ÉCHEC {r['id']} : {r['detail']}" for r in rs if not r["ok"]]
    if out.get("skipped"):
        lines.append("Non lancés (plafond de tokens) : " + ", ".join(out["skipped"]))
    if out.get("refused"):
        lines.append("ARRÊTÉ : " + out["refused"])
    lines.append("Par rapport à la dernière exécution : " + ("; ".join(out["changes"]) or "rien de changé"))
    return "\n".join(lines)
