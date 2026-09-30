"""A small HTTP service on the Mac that a sandboxed agent can ask for two things.

sbx-run starts one per launch on 127.0.0.1 and gives the sandbox its port and
a random token.

/agent-items runs Divernote notes commands, and nothing else:

    divernote agent-items retrieve|add|edit --type note [--id ..] [--text ..] [--date ..] --json

Tasks, deletes and every other Divernote command stay out of reach.
Divernote's own CLI validates IDs, text and dates.

/serve gives a port inside the sandbox a tailnet URL. It publishes that port
on the Mac's loopback (`sbx ports`) and serves it with `tailscale serve`,
which is only reachable from the tailnet (never Funnel). A URL lasts two
hours, even after the launch ends; sbx-run removes it then.
"""
import fcntl
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OPERATIONS = {"retrieve": ("id",), "add": ("text", "date"), "edit": ("id", "text", "date")}
MAX_REQUEST = 64 * 1024
MAX_OUTPUT = 1024 * 1024
MAX_SHARES = 8
LIFETIME = 2 * 60 * 60


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


def serve_request(request):
    """(action, sandbox port, label) for one /serve request, or a refusal."""
    if not isinstance(request, dict):
        return None, "The request must be a JSON object."
    action = request.get("action")
    if action == "status":
        return ("status", None, None), None
    if action not in ("on", "off"):
        return None, "action must be on, off or status."
    port = request.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        return None, "port must be a number from 1 to 65535."
    label = request.get("label", "")
    if not isinstance(label, str) or len(label) > 200:
        return None, "label must be short text."
    return (action, port, label), None


class Shares:
    """The tailnet URLs of one sandbox, kept in `folder/<sandbox>.json`.

    A URL outlives the launch that made it: it lasts LIFETIME seconds (asking
    again renews it) and sbx-run keeps the sandbox running until the last
    one expires (see expire). Every launch in the sandbox shares the file.
    """

    def __init__(self, sandbox, sbx, tailscale, folder):
        self.sandbox, self.sbx, self.tailscale = sandbox, sbx, tailscale
        self.folder = Path(folder)
        self.record = self.folder / f"{sandbox}.json"

    @contextmanager
    def locked(self):
        """The shares, saved again when the block ends."""
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(self.folder / f"{self.sandbox}.lock", "a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                data = json.loads(self.record.read_text())
                shares = {int(port): share for port, share in data["shares"].items()}
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                shares = {}
            yield shares
            if shares:
                temporary = self.record.with_suffix(".tmp")
                temporary.write_text(json.dumps({"sandbox": self.sandbox, "shares": shares}, indent=2) + "\n")
                os.replace(temporary, self.record)
            else:
                self.record.unlink(missing_ok=True)

    def run(self, *argv):
        try:
            return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as error:
            return subprocess.CompletedProcess(argv, 1, "", str(error))

    def dns_name(self):
        result = self.run(self.tailscale, "status", "--json")
        try:
            name = json.loads(result.stdout)["Self"]["DNSName"].rstrip(".")
        except (ValueError, KeyError, TypeError, AttributeError):
            name = ""
        return name or None

    def served_ports(self):
        result = self.run(self.tailscale, "serve", "status", "--json")
        try:
            return {int(port) for port in (json.loads(result.stdout or "{}").get("TCP") or {})}
        except (ValueError, AttributeError):
            return set()

    def publish(self, port):
        result = self.run(self.sbx, "ports", self.sandbox, "--publish", str(port))
        match = re.search(r"127\.0\.0\.1:(\d+) -> " + str(port) + r"\b", result.stdout + result.stderr)
        return (int(match.group(1)), None) if match else (None, (result.stderr or result.stdout).strip())

    def unpublish(self, port):
        # By sandbox port alone: sbx remembers published ports and publishes them
        # again after a restart, and only this form forgets them.
        self.run(self.sbx, "ports", self.sandbox, "--unpublish", str(port))

    def remove(self, port, share):
        if self.tailscale:
            self.run(self.tailscale, "serve", f"--https={share['https']}", "off")
        self.unpublish(port)

    def handle(self, action, port, label, now=None):
        now = time.time() if now is None else now
        with self.locked() as shares:
            if action == "status":
                return 200, {"shares": [{"port": port, **share} for port, share in sorted(shares.items())]}
            if action == "off":
                share = shares.pop(port, None)
                if not share:
                    return 404, {"error": f"Sandbox port {port} has no tailnet URL."}
                self.remove(port, share)
                return 200, {"port": port, **share}
            return self.on(shares, port, label, now)

    def on(self, shares, port, label, now):
        if port in shares:
            shares[port]["expires"] = now + LIFETIME
            return 200, {"port": port, **shares[port]}
        if len(shares) >= MAX_SHARES:
            return 400, {"error": f"At most {MAX_SHARES} tailnet URLs at a time; turn one off first."}
        if not self.tailscale:
            return 503, {"error": "Tailscale is not installed on the Mac."}
        dns = self.dns_name()
        if not dns:
            return 503, {"error": "Tailscale on the Mac is not running or not signed in."}
        taken = self.served_ports()
        for _attempt in range(3):
            host_port, problem = self.publish(port)
            if host_port is None:
                return 502, {"error": f"Could not publish sandbox port {port}: {problem or 'sbx failed'}"}
            if host_port not in taken:
                break
            # The Mac already serves something on that HTTPS port; never replace it.
            self.unpublish(port)
        else:
            return 502, {"error": "Could not find a free HTTPS port on the Mac."}
        served = self.run(self.tailscale, "serve", "--bg", "--yes", f"--https={host_port}", f"http://127.0.0.1:{host_port}")
        if served.returncode:
            self.unpublish(port)
            return 502, {"error": f"tailscale serve failed: {(served.stderr or served.stdout).strip()}"}
        shares[port] = {"https": host_port, "url": f"https://{dns}:{host_port}/", "label": label, "expires": now + LIFETIME}
        return 200, {"port": port, **shares[port]}

    def expire(self, everything=False, now=None):
        """Remove expired URLs (or all of them); how many are left."""
        now = time.time() if now is None else now
        with self.locked() as shares:
            for port, share in list(shares.items()):
                if everything or share.get("expires", 0) <= now:
                    self.remove(port, shares.pop(port))
            return len(shares)


def sandboxes_with_shares(folder):
    return sorted(path.stem for path in Path(folder).glob("*.json"))


class Bridge:
    def __init__(self, divernote, env, shares):
        self.token = secrets.token_urlsafe(32)
        self.divernote = divernote
        self.env = env
        self.shares = shares
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
                if self.path not in ("/agent-items", "/serve"):
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
                if self.path == "/serve":
                    parsed, refusal = serve_request(request)
                    if refusal:
                        return self.reply(400, {"error": refusal})
                    return self.reply(*bridge.shares.handle(*parsed))
                if not bridge.divernote:
                    return self.reply(503, {"error": "Divernote is not installed on the Mac."})
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


def start(search_path, env, shares):
    """A running bridge for one launch; `shares` is its sandbox's Shares."""
    return Bridge(shutil.which("divernote", path=search_path), dict(env), shares)
