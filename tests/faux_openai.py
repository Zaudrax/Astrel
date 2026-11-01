"""Faux serveur compatible OpenAI (plan P11, lot 2) : `/v1/chat/completions` en flux SSE ou non,
`/v1/models`. Sert aux tests de la passerelle et au banc d'essai : il SCRIPTE ses réponses selon
le dernier message, pour vérifier la traduction Anthropic ↔ OpenAI de bout en bout sans réseau.

- un message `tool` (résultat d'outil) depuis la dernière réponse : texte « Résultat reçu : <début du résultat> » ;
- « BANC-OUTIL » dans le dernier message de l'utilisateur et un outil `Bash` proposé : appel de
  Bash (`echo banc-ok`) ;
- « BANC-REFUS » : appel de Bash `echo banc-interdit` (le hook du banc doit le refuser) ;
- « BANC-AGENT » et un outil `Agent` (ou `Task`) : appel d'un sous-agent ;
- sinon : `reply` (« Bonjour depuis le faux OpenAI. » par défaut).

Chaque requête est notée (`requests` : chemin, en-têtes, corps), pour vérifier la clé reçue."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _last_user_text(messages: list[dict]) -> str:
    """Texte des messages de l'utilisateur depuis la dernière réponse : le CLI ajoute ses rappels
    (environnement…) en messages séparés APRÈS la demande (vu le 20/10)."""
    out: list[str] = []
    for m in reversed(messages):
        if m.get("role") == "assistant":
            break
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, list):
            out.append(" ".join(str(c.get("text") or "") for c in content if isinstance(c, dict)))
        else:
            out.append(str(content or ""))
    return " ".join(reversed(out))


def _tool_names(body: dict) -> list[str]:
    out = []
    for t in body.get("tools") or []:
        fn = t.get("function") if isinstance(t, dict) else None
        if isinstance(fn, dict) and fn.get("name"):
            out.append(str(fn["name"]))
    return out


class FauxOpenAI:
    def __init__(self, reply: str = "Bonjour depuis le faux OpenAI.") -> None:
        self.reply = reply
        self.requests: list[dict] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):  # silencieux
                pass

            def _json(self, code: int, data: dict) -> None:
                raw = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):  # noqa: N802
                if self.path.rstrip("/").endswith("/models"):
                    return self._json(200, {"object": "list", "data": [{"id": "faux-modele", "object": "model"}]})
                self._json(404, {"error": {"message": "inconnu"}})

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    body = {}
                owner.requests.append({"path": self.path, "headers": dict(self.headers.items()), "body": body})
                if not self.path.rstrip("/").endswith("/chat/completions"):
                    return self._json(404, {"error": {"message": "inconnu"}})
                text, call = owner.decide(body)
                if body.get("stream"):
                    return self._stream(body, text, call)
                message: dict = {"role": "assistant", "content": text or None}
                if call:
                    message["tool_calls"] = [{"id": call[0], "type": "function",
                                              "function": {"name": call[1], "arguments": json.dumps(call[2])}}]
                self._json(200, {
                    "id": "chatcmpl-faux", "object": "chat.completion", "created": int(time.time()),
                    "model": body.get("model", "faux-modele"),
                    "choices": [{"index": 0, "message": message,
                                 "finish_reason": "tool_calls" if call else "stop"}],
                    "usage": {"prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49},
                })

            def _stream(self, body: dict, text: str, call: tuple | None) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                model = body.get("model", "faux-modele")

                def chunk(delta: dict, finish: str | None = None, usage: dict | None = None) -> None:
                    data: dict = {"id": "chatcmpl-faux", "object": "chat.completion.chunk",
                                  "created": int(time.time()), "model": model,
                                  "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                    if usage:
                        data["usage"] = usage
                    self.wfile.write(b"data: " + json.dumps(data).encode() + b"\n\n")
                    self.wfile.flush()

                chunk({"role": "assistant", "content": ""})
                for i in range(0, len(text), 12):  # plusieurs morceaux : le flux est vraiment découpé
                    chunk({"content": text[i:i + 12]})
                if call:
                    args = json.dumps(call[2])
                    chunk({"tool_calls": [{"index": 0, "id": call[0], "type": "function",
                                           "function": {"name": call[1], "arguments": ""}}]})
                    for i in range(0, len(args), 16):
                        chunk({"tool_calls": [{"index": 0, "function": {"arguments": args[i:i + 16]}}]})
                chunk({}, finish="tool_calls" if call else "stop",
                      usage={"prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49})
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                self.close_connection = True

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/v1"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def decide(self, body: dict) -> tuple[str, tuple | None]:
        """(texte, appel d'outil (id, nom, arguments) ou None) selon le dernier message."""
        messages = body.get("messages") or []
        # Résultat d'outil depuis la dernière réponse (le CLI peut ajouter un rappel après lui).
        last = {}
        for m in reversed(messages):
            if m.get("role") == "assistant":
                break
            if m.get("role") == "tool":
                last = m
                break
        if last:
            content = last.get("content")
            if isinstance(content, list):
                content = " ".join(str(c.get("text") or "") for c in content if isinstance(c, dict))
            return f"Résultat reçu : {' '.join(str(content or '').split())[:120]}", None
        said, tools = _last_user_text(messages), _tool_names(body)
        if "BANC-OUTIL" in said and "Bash" in tools:
            return "Je lance la commande.", ("call_bash_1", "Bash", {"command": "echo banc-ok",
                                                                      "description": "Essai du banc"})
        if "BANC-REFUS" in said and "Bash" in tools:
            return "", ("call_bash_2", "Bash", {"command": "echo banc-interdit",
                                                       "description": "Essai de refus"})
        agent = next((t for t in ("Agent", "Task") if t in tools), "")
        if "BANC-AGENT" in said and agent:
            return "", ("call_agent_1", agent, {"description": "Essai de sous-agent", "subagent_type": "general-purpose",
                                                "prompt": "Réponds seulement : agent-ok"})
        return self.reply, None

    def __enter__(self) -> "FauxOpenAI":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()
