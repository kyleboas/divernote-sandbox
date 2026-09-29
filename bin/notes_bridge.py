"""A small HTTP service on the Mac that lets a sandboxed agent use Divernote notes.

sbx-run starts one per launch on 127.0.0.1 and gives the sandbox its port and
a random token. The only thing it does is run

    divernote agent-items retrieve|add|edit --type note [--id ..] [--text ..] [--date ..] --json

on the Mac. Tasks, deletes and every other Divernote command stay out of reach.
Divernote's own CLI validates IDs, text and dates.
"""
import hmac
import json
import secrets
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OPERATIONS = {"retrieve": ("id",), "add": ("text", "date"), "edit": ("id", "text", "date")}
MAX_REQUEST = 64 * 1024
MAX_OUTPUT = 1024 * 1024


def divernote_command(request):
    """The exact argv for one request, or a message saying why it is refused."""
    if not isinstance(request, dict):
        return None, "The request must be a JSON object."
    operation = request.get("operation")
    if operation not in OPERATIONS:
        return None, "Only notes retrieve, add and edit are available in the sandbox."
    options = request.get("options") or {}
    if not isinstance(options, dict):
        return None, "options must be an object."
    argv = ["agent-items", operation, "--type", "note"]
    for name, value in options.items():
        if name not in OPERATIONS[operation]:
            return None, f"--{name} is not available for notes {operation} in the sandbox."
        if not isinstance(value, str):
            return None, f"--{name} must be text."
        argv += [f"--{name}", value]
    return argv + ["--json"], None


class Bridge:
    def __init__(self, divernote, env):
        self.token = secrets.token_urlsafe(32)
        self.divernote = divernote
        self.env = env
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                if self.path != "/agent-items":
                    return self.reply(404, {"error": "Not found."})
                if not hmac.compare_digest(self.headers.get("X-Divernote-Token", ""), bridge.token):
                    return self.reply(403, {"error": "Not allowed."})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = -1
                if not 0 < length <= MAX_REQUEST:
                    return self.reply(413, {"error": "The request is empty or too large."})
                try:
                    request = json.loads(self.rfile.read(length))
                except ValueError:
                    return self.reply(400, {"error": "The request is not JSON."})
                argv, refusal = divernote_command(request)
                if refusal:
                    return self.reply(400, {"error": refusal})
                self.reply(200, bridge.run(argv))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def run(self, argv):
        try:
            result = subprocess.run([self.divernote, *argv], env=self.env, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as error:
            return {"code": 1, "stdout": "", "stderr": f"divernote could not run: {error}"}
        return {"code": result.returncode, "stdout": result.stdout[:MAX_OUTPUT], "stderr": result.stderr[-4096:]}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def start(search_path, env):
    """A running bridge, or None when this Mac has no divernote command."""
    divernote = shutil.which("divernote", path=search_path)
    return Bridge(divernote, dict(env)) if divernote else None
