"""Resident local embedding server for dev-guard recall.

It runs under a separate interpreter that has sentence-transformers, so hooks stay
dependency-free and never pay the model load. Loopback only, bearer token, bounded
requests, and request text is never logged. Imports nothing else from dev-guard.
"""

import argparse
import contextlib
import hmac
import json
import os
import secrets
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_TEXTS = 32
MAX_CHARS = 4000
MAX_BODY = 1_000_000


def make_server(encode, model: str, dims: int, token: str) -> ThreadingHTTPServer:
    """Serve `encode(list[str]) -> list[list[float]]` (L2-normalized) on 127.0.0.1."""
    expected = ("Bearer " + token).encode("latin-1")
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # request lines can carry prompt text; nothing is logged

        def reply(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self) -> bool:
            given = self.headers.get("Authorization", "").encode("latin-1", "replace")
            if hmac.compare_digest(given, expected):
                return True
            self.reply(401, {"error": "UNAUTHORIZED"})
            return False

        def do_GET(self):
            if not self.authorized():
                return
            if self.path == "/health":
                self.reply(200, {"model": model, "dims": dims, "pid": os.getpid()})
            else:
                self.reply(404, {"error": "NOT_FOUND"})

        def do_POST(self):
            if not self.authorized():
                return
            if self.path != "/embed":
                self.reply(404, {"error": "NOT_FOUND"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    raise ValueError("body size")
                texts = json.loads(self.rfile.read(length))["texts"]
                if (
                    not isinstance(texts, list)
                    or not 0 < len(texts) <= MAX_TEXTS
                    or not all(isinstance(t, str) and 0 < len(t) <= MAX_CHARS for t in texts)
                ):
                    raise ValueError("texts")
            except (ValueError, KeyError, TypeError):
                self.reply(400, {"error": "INVALID_REQUEST"})
                return
            with lock:
                vectors = encode(texts)
            self.reply(200, {"model": model, "dims": dims, "vectors": vectors})

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def write_state(directory: Path, state: dict) -> None:
    """Publish the endpoint atomically; its presence means the model is loaded."""
    fd, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(state, stream)
    os.replace(temporary, directory / "server.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m devguard.embedder")
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--cache")
    args = parser.parse_args(argv)
    from sentence_transformers import SentenceTransformer

    loaded = SentenceTransformer(args.model, device="cpu", cache_folder=args.cache)
    dimension = getattr(loaded, "get_embedding_dimension", None)
    dims = int((dimension or loaded.get_sentence_embedding_dimension)())

    def encode(texts: list[str]) -> list[list[float]]:
        return loaded.encode(texts, normalize_embeddings=True, convert_to_numpy=True).tolist()

    token = secrets.token_urlsafe(32)
    server = make_server(encode, args.model, dims, token)
    state = {
        "pid": os.getpid(),
        "port": server.server_address[1],
        "token": token,
        "model": args.model,
        "dims": dims,
    }
    write_state(args.state, state)
    (args.state / "starting").unlink(missing_ok=True)
    try:
        server.serve_forever()
    finally:
        path = args.state / "server.json"
        with contextlib.suppress(OSError, ValueError):
            if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
                path.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
