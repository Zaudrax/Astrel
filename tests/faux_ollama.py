"""Faux serveur Ollama (plan P8) : /api/version, /api/tags, /api/pull (en flux), /api/delete,
/api/create, /v1/messages (format Anthropic, en flux SSE ou non), /api/chat (en flux, ndjson) et /api/ps
(04/11, moteur local), et un faux registre
(/v2/library/<nom>/manifests/<tag>). Aucun réseau, aucun disque : tout est en mémoire."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FauxOllama:
    def __init__(self, installed: dict[str, int] | None = None, manifests: dict[str, int] | None = None,
                 pull_steps: int = 4, pull_delay: float = 0.0, reply: str = "Bonjour, je suis en mode léger.",
                 create_error: str = "", chat: list[str] | None = None, chat_error_gpu: str = "",
                 loaded: list[str] | None = None, thinking: list[str] | None = None) -> None:
        self.installed = dict(installed or {})
        # /api/chat : morceaux de la réponse ; `chat_error_gpu` : erreur rendue tant que `num_gpu` est demandé
        # (repli du moteur local) ; `loaded` : modèles déjà chargés (/api/ps).
        self.chat = list(chat or ["Bonjour. ", "Je réponds en local."])
        self.chat_error_gpu = chat_error_gpu
        self.loaded = list(loaded or [])
        self.thinking = list(thinking or ["Je réfléchis ", "à la demande."])  # 05/11 : morceaux si `think`
        self.manifests = dict(manifests or {})
        self.pull_steps, self.pull_delay = pull_steps, pull_delay
        self.reply, self.create_error = reply, create_error
        self.requests: list[tuple[str, str, dict]] = []
        self.pull_closed = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):  # silencieux
                pass

            def _body(self) -> dict:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                try:
                    return json.loads(raw or b"{}")
                except ValueError:
                    return {}

            def _json(self, code: int, data: dict) -> None:
                raw = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _chunk(self, raw: bytes) -> None:
                self.wfile.write(f"{len(raw):x}\r\n".encode() + raw + b"\r\n")
                self.wfile.flush()

            def do_GET(self):  # noqa: N802
                owner.requests.append(("GET", self.path, {}))
                if self.path == "/api/ps":
                    return self._json(200, {"models": [{"name": n, "model": n} for n in owner.loaded]})
                if self.path == "/api/version":
                    return self._json(200, {"version": "0.30.6"})
                if self.path == "/api/tags":
                    return self._json(200, {"models": [{"name": n, "model": n, "size": s}
                                                       for n, s in owner.installed.items()]})
                if self.path.startswith("/v2/library/"):
                    _, _, rest = self.path.partition("/v2/library/")
                    name, _, tag = rest.partition("/manifests/")
                    size = owner.manifests.get(f"{name}:{tag}")
                    if size is None:
                        return self._json(404, {"errors": [{"code": "MANIFEST_UNKNOWN"}]})
                    return self._json(200, {"config": {"size": 500},
                                            "layers": [{"size": size - 1500}, {"size": 1000}]})
                return self._json(404, {"error": "inconnu"})

            def do_DELETE(self):  # noqa: N802
                body = self._body()
                owner.requests.append(("DELETE", self.path, body))
                if self.path == "/api/delete":
                    if owner.installed.pop(body.get("model"), None) is None:
                        return self._json(404, {"error": "model not found"})
                    return self._json(200, {})
                return self._json(404, {"error": "inconnu"})

            def do_POST(self):  # noqa: N802
                body = self._body()
                owner.requests.append(("POST", self.path, body))
                if self.path == "/api/create":
                    if owner.create_error:
                        return self._json(500, {"error": owner.create_error})
                    owner.installed[body["model"]] = 1
                    return self._json(200, {"status": "success"})
                if self.path == "/api/pull":
                    return self._pull(body.get("model", ""))
                if self.path == "/api/chat":
                    return self._chat(body)
                if self.path.partition("?")[0] == "/v1/messages":  # le CLI ajoute « ?beta=true »
                    return self._messages(body)
                return self._json(404, {"error": "inconnu"})

            def _pull(self, tag: str) -> None:
                size = owner.manifests.get(tag, 3_000_000_000)
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                try:
                    self._chunk(json.dumps({"status": "pulling manifest"}).encode() + b"\n")
                    for i in range(1, owner.pull_steps + 1):
                        time.sleep(owner.pull_delay)
                        self._chunk(json.dumps({"status": "pulling abc", "digest": "sha256:abc", "total": size,
                                                "completed": size * i // owner.pull_steps}).encode() + b"\n")
                    owner.installed[tag] = size
                    self._chunk(json.dumps({"status": "success"}).encode() + b"\n")
                    self._chunk(b"")
                except (BrokenPipeError, ConnectionResetError):
                    owner.pull_closed.set()

            def _chat(self, body: dict) -> None:
                if owner.chat_error_gpu and "num_gpu" in (body.get("options") or {}):
                    return self._json(500, {"error": owner.chat_error_gpu})
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                if body.get("think"):  # 05/11 : réflexion demandée → morceaux `thinking` avant la réponse (comme Ollama)
                    for part in owner.thinking:
                        self._chunk(json.dumps({"message": {"role": "assistant", "content": "", "thinking": part},
                                                "done": False}).encode() + b"\n")
                for part in owner.chat:
                    self._chunk(json.dumps({"message": {"role": "assistant", "content": part},
                                            "done": False}).encode() + b"\n")
                self._chunk(json.dumps({"message": {"role": "assistant", "content": ""}, "done": True,
                                        "eval_count": 20, "eval_duration": 500_000_000,
                                        "load_duration": 100_000_000}).encode() + b"\n")
                self._chunk(b"")

            def _messages(self, body: dict) -> None:
                model, text = body.get("model", ""), owner.reply
                usage = {"input_tokens": 12, "output_tokens": 8}
                if not body.get("stream"):
                    return self._json(200, {"id": "msg_1", "type": "message", "role": "assistant", "model": model,
                                            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                                            "usage": usage})
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                events = [
                    ("message_start", {"type": "message_start", "message": {
                        "id": "msg_1", "type": "message", "role": "assistant", "model": model, "content": [],
                        "stop_reason": None, "usage": {"input_tokens": 12, "output_tokens": 0}}}),
                    ("content_block_start", {"type": "content_block_start", "index": 0,
                                             "content_block": {"type": "text", "text": ""}}),
                    ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                             "delta": {"type": "text_delta", "text": text}}),
                    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                    ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                                       "usage": {"output_tokens": 8}}),
                    ("message_stop", {"type": "message_stop"}),
                ]
                for name, data in events:
                    self._chunk(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
                self._chunk(b"")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "FauxOllama":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
