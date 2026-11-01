#!/usr/bin/env python3
"""Faux CLI scripté (plan P27) : imite `codex exec --json` et `grok -p … --output-format
streaming-json` d'après leurs formats SUPPOSÉS (documentation, non prouvés). Sert aux tests du
socle : flux, outil, refus du hook, annulation, reprise, MCP. Jamais un vrai fournisseur.

Le scénario vient de la consigne reçue (mots-clés) :
- « OUTIL »         : une commande `ls` lancée puis rendue ;
- « HOOK: <cmd> »   : demande au hook déclaré dans la config du CLI (comme le ferait le vrai CLI,
                       supposé) s'il peut lancer <cmd> ; refus = commande « declined », jamais lancée ;
- « LENT »          : lance un enfant (« serveur MCP » simulé), écrit les pid dans $FAUX_CLI_PIDS,
                       puis attend 60 s (annulation, `from_claude`) ;
- « PLANTE »        : stderr puis code 3.
Correction n°2 (22/10, fiches 2 et 3 du journal p27) :
- « PETITENFANT »   : un petit-enfant qui IGNORE SIGINT garde stdout ouvert ; pid dans $FAUX_CLI_PIDS ;
- « BIZARRE »       : un évènement que le traducteur ne sait pas lire (item/usage non-objet), puis 60 s ;
- « NONJSON »       : du texte seul (pas une ligne JSON), puis code 0 ;
- « OCTET »         : un octet 0xFF sur stdout et sur stderr, puis la suite normale ;
- « OCTETPLANTE »   : 0xFF sur stderr, une dernière ligne, puis code 3.
$FAUX_CLI_FIGE=1 : `--version` et `login status` ne rendent jamais la main (détection).
Chaque appel ajoute une ligne à $FAUX_CLI_LOG : argv, consigne reçue, quelques variables.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path


def out(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def journal(argv: list[str], prompt: str) -> None:
    path = os.environ.get("FAUX_CLI_LOG")
    if not path:
        return
    env = {k: os.environ.get(k) for k in ("OPENAI_API_KEY", "CODEX_API_KEY", "XAI_API_KEY", "ANTHROPIC_API_KEY",
                                           "GROK_CODE_XAI_API_KEY", "CODEX_HOME", "GROK_HOME", "ASTREL_BRAIN",
                                           "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_BASE", "XAI_BASE_URL",
                                           "ANTHROPIC_AUTH_TOKEN")}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": argv, "prompt": prompt, "env": env, "cwd": os.getcwd()}) + "\n")


def hook_decision(home: Path, cmd: str) -> tuple[bool, str]:
    """Lance le hook déclaré (hooks.json de Codex ou settings.json de Grok, supposés) avec une
    charge à la Codex : `{"tool_name": "shell", "tool_input": {"command": [...]}}`."""
    conf = home / "hooks.json"
    if not conf.exists():
        conf = home / "settings.json"
    hooks = json.loads(conf.read_text(encoding="utf-8"))["hooks"]["PreToolUse"][0]["hooks"][0]
    payload = {"hook_event_name": "PreToolUse", "tool_name": "shell",
               "tool_input": {"command": ["bash", "-lc", cmd]}, "cwd": os.getcwd()}
    p = subprocess.run(hooks["command"], shell=True, input=json.dumps(payload), capture_output=True, text=True,
                       timeout=hooks.get("timeout", 60))
    try:
        dec = json.loads(p.stdout.strip().splitlines()[-1])["hookSpecificOutput"]["permissionDecision"]
    except (ValueError, KeyError, IndexError):
        dec = ""
    allowed = p.returncode == 0 and dec == "allow"
    return allowed, (p.stderr.strip() or dec)


def main(argv: list[str]) -> int:
    if os.environ.get("FAUX_CLI_FIGE") == "1" and ("--version" in argv or argv[:2] == ["login", "status"]):
        time.sleep(60)
    if "--version" in argv:
        print("faux-cli 0.1.0")
        return 0
    if argv[:2] == ["login", "status"]:
        if os.environ.get("FAUX_CLI_CONNECTE", "1") == "0":
            print("Not logged in", file=sys.stderr)
            return 1
        print("Logged in using ChatGPT")
        return 0
    grok = "-p" in argv
    home = Path(os.environ.get("GROK_HOME" if grok else "CODEX_HOME") or "/nonexistent")
    if grok:
        prompt = argv[argv.index("-p") + 1]
        session = argv[argv.index("--resume") + 1] if "--resume" in argv else ""
    else:
        prompt = sys.stdin.read() if argv[-1] == "-" else argv[-1]
        session = argv[argv.index("resume") + 1] if "resume" in argv else ""
    journal(argv, prompt)
    store = home / "faux-sessions"
    store.mkdir(parents=True, exist_ok=True)
    if session and not (store / session).exists():
        print(f"session introuvable : {session}", file=sys.stderr)
        return 1
    sid = session or str(uuid.uuid4())
    n = int((store / sid).read_text() if (store / sid).exists() else "0") + 1
    (store / sid).write_text(str(n))

    if grok:
        out({"type": "session", "session_id": sid})
    else:
        out({"type": "thread.started", "thread_id": sid})
        out({"type": "turn.started"})

    def outil(tid: str, cmd: str, sortie: str, status: str = "completed", code: int = 0) -> None:
        if grok:
            out({"type": "tool_use", "id": tid, "name": "bash", "input": {"command": cmd}})
            out({"type": "tool_result", "tool_use_id": tid, "content": sortie, "is_error": status != "completed"})
        else:
            out({"type": "item.started", "item": {"id": tid, "type": "command_execution", "command": cmd,
                                                  "status": "in_progress"}})
            out({"type": "item.completed", "item": {"id": tid, "type": "command_execution", "command": cmd,
                                                    "aggregated_output": sortie, "exit_code": code,
                                                    "status": status}})

    def dire(texte: str) -> None:
        if grok:
            out({"type": "text", "text": texte})
        else:
            out({"type": "item.completed", "item": {"id": f"msg{n}", "type": "agent_message", "text": texte}})

    if "OCTETPLANTE" in prompt:
        sys.stderr.buffer.write(b"\xff octet non UTF-8\n")
        sys.stderr.buffer.flush()
        print("derniere ligne lisible", file=sys.stderr, flush=True)
        return 3
    if "PLANTE" in prompt:
        print("boom : le faux CLI plante", file=sys.stderr)
        return 3
    if "NONJSON" in prompt:
        print("Bonjour, ceci n'est pas du JSON.", flush=True)
        return 0
    if "OCTET" in prompt:
        sys.stderr.buffer.write(b"\xff avant\n")
        sys.stderr.buffer.flush()
        sys.stdout.buffer.write(b"\xff\n")
        sys.stdout.buffer.flush()
    if "PETITENFANT" in prompt:
        pret = home / f"pret-{os.getpid()}"
        gp = subprocess.Popen([sys.executable, "-c", "import signal, sys, time; "
                               "signal.signal(signal.SIGINT, signal.SIG_IGN); open(sys.argv[1], 'w').close(); "
                               "time.sleep(60)", str(pret)])
        t0 = time.time()
        while not pret.exists() and time.time() - t0 < 10:  # SIGINT ignoré AVANT d'annoncer les pid
            time.sleep(0.02)
        pids = os.environ.get("FAUX_CLI_PIDS")
        if pids:
            Path(pids).write_text(json.dumps({"cli": os.getpid(), "petit_enfant": gp.pid}))
        dire("Je commence.")
        time.sleep(60)  # SIGINT : le faux CLI meurt, le petit-enfant garde stdout
        return 0
    if "BIZARRE" in prompt:
        pids = os.environ.get("FAUX_CLI_PIDS")
        if pids:
            Path(pids).write_text(json.dumps({"cli": os.getpid()}))
        if grok:
            out({"type": "stream_event", "event": "x"})
        else:
            out({"type": "item.completed", "item": "x"})
        time.sleep(60)
        return 0
    if "LENT" in prompt:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        pids = os.environ.get("FAUX_CLI_PIDS")
        if pids:
            Path(pids).write_text(json.dumps({"cli": os.getpid(), "mcp": child.pid}))
        dire("Je commence un long travail.")
        time.sleep(60)
        return 0
    if "OUTIL" in prompt:
        outil("cmd1", "ls", "a.txt\nb.txt")
    if "HOOK:" in prompt:
        cmd = prompt.split("HOOK:", 1)[1].strip().splitlines()[0]
        allowed, why = hook_decision(home, cmd)
        if allowed:
            outil("cmd2", cmd, "exécuté")
        else:
            outil("cmd2", cmd, f"refusé par le hook : {why}", status="declined", code=1)
    dire(f"Bonjour, tour {n}.")
    usage = {"input_tokens": 100 * n, "cached_input_tokens": 50, "output_tokens": 10}
    if grok:
        out({"type": "done", "usage": {"input_tokens": usage["input_tokens"], "output_tokens": 10}})
    else:
        out({"type": "turn.completed", "usage": usage})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
