#!/usr/bin/env python3
"""Banc d'essai de la passerelle (20/10, plan P11, lot 2) : le VRAI CLI `claude` parle à un
modèle non-Claude à travers `astrel/passerelle_serveur.py`, comme le cerveau le ferait.

Quatre épreuves par modèle :
- flux     : une réponse simple arrive en plusieurs morceaux (phrase par phrase possible) ;
- outil    : le modèle appelle Bash (`echo banc-ok`), lit le résultat et le répète ;
- refus    : un hook PreToolUse (celui du BANC, pas le garde-fou d'Astrel : aucune carte sur
             l'écran de l'utilisateur) refuse `echo banc-interdit` ; la commande ne tourne pas et le
             modèle reçoit le refus ;
- agent    : le modèle lance un sous-agent (outil Agent) et rend sa réponse.

Cibles :
    tools/banc_passerelle.py --faux                       # faux serveur OpenAI scripté (aucun réseau)
    tools/banc_passerelle.py --ollama qwen2.5-coder:7b    # modèle local d'Ollama, traduit par LiteLLM
    tools/banc_passerelle.py --fournisseur openai --modele gpt-5   # vraie clé de config/secrets.json (payant)

Aucun serveur MCP, aucune session gardée, réglages de l'utilisateur ignorés (`--setting-sources
project` dans un dossier vide) ; le jeton et les secrets de test vivent dans un dossier temporaire.
Le jeton de la passerelle d'Astrel (`runtime/passerelle-jeton`) n'est jamais touché.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from astrel import config, fournisseurs, passerelle  # noqa: E402

HOOK = r'''
import json, sys
data = json.load(sys.stdin)
cmd = str((data.get("tool_input") or {}).get("command") or "")
open(@LOG@, "a").write(json.dumps({"tool": data.get("tool_name"), "command": cmd}) + "\n")
if "banc-interdit" in cmd:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                      "permissionDecisionReason": "Refusé par le banc : commande interdite."}}))
'''

EPREUVES = {
    "flux": "Dis bonjour en deux phrases courtes, en français.",
    "outil": "BANC-OUTIL : utilise l'outil Bash pour lancer exactement la commande `echo banc-ok`, "
             "puis répète ce qu'elle a affiché.",
    "refus": "BANC-REFUS : utilise l'outil Bash pour lancer exactement la commande `echo banc-interdit`, "
             "puis dis-moi ce qui s'est passé.",
    "agent": "BANC-AGENT : lance un sous-agent avec l'outil Agent (subagent_type general-purpose) dont la "
             "consigne est « Réponds seulement : agent-ok », puis répète sa réponse.",
}


def run_cli(prompt: str, url: str, modele: str, work: Path, timeout: float) -> dict:
    hook_log = work / "hook.jsonl"
    hook = work / "hook.py"
    hook.write_text(HOOK.replace("@LOG@", repr(str(hook_log))), encoding="utf-8")
    helper = " ".join(shlex.quote(str(p)) for p in (config.VENV_PYTHON, "-I", config.CLE_API_SCRIPT,
                                                       "passerelle", config.PASSERELLE_JETON_FILE))
    settings = {"apiKeyHelper": helper,
                "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
                    {"type": "command", "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(hook))}",
                     "timeout": 20}]}]}}
    cmd = [config.CLAUDE_BIN, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
           "--dangerously-skip-permissions", "--settings", json.dumps(settings), "--setting-sources", "project",
           "--strict-mcp-config", "--no-session-persistence", "--model", "sonnet",
           "--tools", "Bash,Agent,Read"]
    env = fournisseurs.env_cerveau(dict(os.environ), "passerelle", url=url, modele=modele)
    for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT"):
        env.pop(k, None)
    t0 = time.time()
    out = {"deltas": 0, "tools": [], "results": [], "sub": 0, "final": "", "error": "", "s": 0.0}
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout,
                              cwd=str(work), env=env)
        lines = proc.stdout.splitlines()
        if proc.returncode and not lines:
            out["error"] = (proc.stderr or "").strip()[-300:]
    except subprocess.TimeoutExpired as exc:
        lines = (exc.stdout or b"").decode(errors="replace").splitlines() if isinstance(exc.stdout, bytes) \
            else (exc.stdout or "").splitlines()
        out["error"] = f"délai de {timeout:.0f} s dépassé"
    out["s"] = round(time.time() - t0, 1)
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("parent_tool_use_id"):
            out["sub"] += 1
        if ev.get("type") == "stream_event" and not ev.get("parent_tool_use_id"):
            delta = (ev.get("event") or {}).get("delta") or {}
            if delta.get("type") == "text_delta":
                out["deltas"] += 1
        elif ev.get("type") == "assistant" and not ev.get("parent_tool_use_id"):
            for b in (ev.get("message") or {}).get("content") or []:
                if b.get("type") == "tool_use":
                    out["tools"].append({"name": b.get("name"), "input": b.get("input")})
        elif ev.get("type") == "user" and not ev.get("parent_tool_use_id"):
            for b in (ev.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    if isinstance(c, list):
                        c = " ".join(str(x.get("text") or "") for x in c if isinstance(x, dict))
                    out["results"].append({"error": bool(b.get("is_error")), "text": " ".join(str(c or "").split())[:200]})
        elif ev.get("type") == "result":
            out["final"] = str(ev.get("result") or "")
            if ev.get("is_error"):
                out["error"] = out["final"][:300] or "résultat en erreur"
    out["hook"] = [json.loads(x) for x in hook_log.read_text().splitlines()] if hook_log.exists() else []
    return out


def juge(nom: str, r: dict) -> tuple[bool, str]:
    if r["error"]:
        return False, r["error"]
    if nom == "flux":
        return r["deltas"] >= 2 and bool(r["final"].strip()), f"{r['deltas']} morceaux"
    if nom == "outil":
        bash = [t for t in r["tools"] if t["name"] == "Bash" and "echo banc-ok" in str((t["input"] or {}).get("command"))]
        seen = any("banc-ok" in x["text"] and not x["error"] for x in r["results"])
        return bool(bash) and seen and "banc-ok" in r["final"], f"Bash {'appelé' if bash else 'NON appelé'}, " \
            f"résultat {'lu' if seen else 'absent'}, réponse {'cite' if 'banc-ok' in r['final'] else 'ne cite pas'} banc-ok"
    if nom == "refus":
        asked = any("banc-interdit" in h.get("command", "") for h in r["hook"])
        refused = any(x["error"] and "banc" in x["text"].lower() for x in r["results"])
        ran = any(not x["error"] and "banc-interdit" in x["text"] for x in r["results"])
        return asked and refused and not ran and bool(r["final"].strip()), \
            f"hook {'consulté' if asked else 'PAS consulté'}, refus {'transmis' if refused else 'NON transmis'}" \
            f"{', COMMANDE EXÉCUTÉE' if ran else ''}"
    agent = [t for t in r["tools"] if t["name"] in ("Agent", "Task")]
    ok = bool(agent) and any(not x["error"] for x in r["results"]) and r["sub"] > 0
    return ok, f"Agent {'appelé' if agent else 'NON appelé'}, {r['sub']} évènement(s) du sous-agent, " \
               f"réponse {'cite' if 'agent-ok' in r['final'] else 'ne cite pas'} agent-ok"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--faux", action="store_true", help="faux serveur OpenAI scripté")
    g.add_argument("--ollama", metavar="TAG", help="modèle local d'Ollama (API compatible OpenAI)")
    g.add_argument("--fournisseur", choices=[k for k, f in fournisseurs.CATALOGUE.items() if f.passerelle])
    ap.add_argument("--modele", default="")
    ap.add_argument("--timeout", type=float, default=240.0)
    ap.add_argument("--epreuves", default=",".join(EPREUVES))
    args = ap.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="banc-passerelle-"))
    config.PASSERELLE_JETON_FILE = work / "jeton"  # jamais le jeton de l'Astrel en marche
    faux = None
    if args.fournisseur:
        if not fournisseurs.a_une_cle(args.fournisseur):
            print(f"aucune clé {args.fournisseur} dans config/secrets.json")
            return 2
        fournisseur, modele, base, nom_cle = args.fournisseur, args.modele or fournisseurs.CATALOGUE[
            args.fournisseur].modeles[0][0], "", fournisseurs.CATALOGUE[args.fournisseur].cle
    else:
        config.SECRETS_FILE = work / "secrets.json"
        config.SECRETS_FILE.write_text(json.dumps({"CLE_API_OPENAI": "cle-factice-du-banc"}))
        nom_cle, fournisseur = "CLE_API_OPENAI", "openai"
        if args.faux:
            from faux_openai import FauxOpenAI

            faux = FauxOpenAI().__enter__()
            modele, base = "faux-modele", faux.url
        else:
            # `/api/create` échoue sur cette machine (« chtimes … », vu le 19/10) : la passerelle
            # passe le contexte elle-même (fournisseur « ollama » réservé au banc).
            fournisseur, modele, base = "ollama", args.ollama, config.OLLAMA_URL
    label = f"{fournisseur}/{args.ollama or modele}"
    try:
        url = passerelle.passerelle.start(fournisseur, modele, nom_cle, base=base)
        print(f"banc {label} : passerelle {url}")
        results = {}
        for nom in [e for e in args.epreuves.split(",") if e in EPREUVES]:
            r = run_cli(EPREUVES[nom], url, modele, work, args.timeout)
            ok, why = juge(nom, r)
            results[nom] = {"ok": ok, "detail": why, "s": r["s"], "final": r["final"][:200]}
            print(f"  {nom:<6} {'OK   ' if ok else 'ÉCHEC'} {r['s']:>6.1f} s  {why}")
            if not ok and r["final"]:
                print(f"         réponse : {' '.join(r['final'].split())[:160]}")
        out = config.RUNTIME / "banc" / "passerelle.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            prev = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prev = {}
        prev[label] = {"at": time.strftime("%Y-%m-%d %H:%M"), "epreuves": results}
        out.write_text(json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if all(v["ok"] for v in results.values()) else 1
    finally:
        passerelle.passerelle.stop()
        if faux:
            faux.__exit__(None, None, None)


if __name__ == "__main__":
    sys.exit(main())
