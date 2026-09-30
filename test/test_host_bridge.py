import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
from host_bridge import Shares, clean_stale, divernote_command, serve_request  # noqa: E402


class NotesOnly(unittest.TestCase):
    def test_note_operations_map_to_fixed_argv(self):
        argv, refusal = divernote_command({"operation": "add", "options": {"text": "--x; $(y)"}})
        self.assertIsNone(refusal)
        self.assertEqual(argv, ["agent-items", "add", "--type", "note", "--text", "--x; $(y)", "--json"])
        argv, _ = divernote_command({"operation": "retrieve"})
        self.assertEqual(argv, ["agent-items", "retrieve", "--type", "note", "--json"])

    def test_everything_else_is_refused(self):
        for request in ({"operation": "delete", "options": {"id": "a"}},
                        {"operation": "add", "options": {"type": "task", "text": "x"}},
                        {"operation": "edit", "options": {"id": "a", "state": "open"}},
                        {"operation": "add", "options": {"text": ["x"]}},
                        {"operation": "pi-work"}, [], "x"):
            argv, refusal = divernote_command(request)
            self.assertIsNone(argv, request)
            self.assertTrue(refusal)


# Fake sbx and tailscale that log their argv and answer like the real ones.
FAKE = r'''#!/usr/bin/python3
import json, os, sys
log = os.environ["FAKE_LOG"]
with open(log, "a") as f:
    f.write(json.dumps([os.path.basename(sys.argv[0])] + sys.argv[1:]) + "\n")
args = sys.argv[1:]
if os.path.basename(sys.argv[0]) == "sbx":
    if args[0] == "ports" and args[2] == "--publish":
        count = sum(1 for line in open(log) if '"--publish"' in line)
        print(f"Published 127.0.0.1:{49150 + count} -> {args[3]}/tcp4")
elif args[:2] == ["status", "--json"]:
    print(json.dumps({"Self": {"DNSName": "mac.tail.ts.net."}}))
elif args[:3] == ["serve", "status", "--json"]:
    print(json.dumps({"TCP": {"49151": {"HTTPS": True}}}))
'''


class TailnetUrls(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.log = self.dir / "log"
        os.environ["FAKE_LOG"] = str(self.log)
        for name in ("sbx", "tailscale"):
            (self.dir / name).write_text(FAKE)
            (self.dir / name).chmod(stat.S_IRWXU)
        self.record = self.dir / "shares" / "dn-x.1.json"
        self.shares = Shares("dn-x", str(self.dir / "sbx"), str(self.dir / "tailscale"), self.record)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_requests_are_validated(self):
        self.assertEqual(serve_request({"action": "on", "port": 3000, "label": "x"})[0], ("on", 3000, "x"))
        self.assertEqual(serve_request({"action": "status"})[0], ("status", None, None))
        for request in ({"action": "on", "port": "3000"}, {"action": "on", "port": 0}, {"action": "on", "port": True},
                        {"action": "funnel", "port": 3000}, {"action": "on", "port": 3000, "label": 1}, []):
            self.assertIsNone(serve_request(request)[0], request)

    def test_a_port_gets_a_tailnet_url_and_close_removes_it(self):
        status, share = self.shares.handle("on", 40001, "http://localhost:3000")
        self.assertEqual(status, 200)
        # 49151 is already served on the Mac, so it is given back and the next port used.
        self.assertEqual(share["https"], 49152)
        self.assertEqual(share["url"], "https://mac.tail.ts.net:49152/")
        self.assertIn(["sbx", "ports", "dn-x", "--unpublish", "40001"], self.calls())
        self.assertIn(["tailscale", "serve", "--bg", "--yes", "--https=49152", "http://127.0.0.1:49152"], self.calls())
        self.assertEqual(json.loads(self.record.read_text())["shares"], [{"port": 40001, "https": 49152}])
        self.assertEqual(self.shares.handle("on", 40001, "")[1]["https"], 49152)  # asking again reuses it
        self.shares.close()
        self.assertIn(["tailscale", "serve", "--https=49152", "off"], self.calls())
        self.assertIn(["sbx", "ports", "dn-x", "--unpublish", "40001"], self.calls())
        self.assertFalse(self.record.exists())
        self.assertEqual(self.shares.handle("off", 40001, "")[0], 404)

    def test_a_dead_launchs_urls_are_removed(self):
        self.record.parent.mkdir(parents=True)
        self.record.write_text(json.dumps({"pid": 2 ** 22 + 12345, "sandbox": "dn-x", "shares": [{"port": 40001, "https": 49160}]}))
        live = self.record.parent / "dn-y.2.json"
        live.write_text(json.dumps({"pid": os.getpid(), "sandbox": "dn-y", "shares": [{"port": 1, "https": 2}]}))
        clean_stale(self.record.parent, str(self.dir / "sbx"), str(self.dir / "tailscale"))
        self.assertEqual(self.calls(), [["tailscale", "serve", "--https=49160", "off"],
                                        ["sbx", "ports", "dn-x", "--unpublish", "40001"]])
        self.assertFalse(self.record.exists())
        self.assertTrue(live.exists())

    def test_without_tailscale_it_says_so(self):
        shares = Shares("dn-x", str(self.dir / "sbx"), None, self.record)
        status, body = shares.handle("on", 40001, "")
        self.assertEqual(status, 503)
        self.assertIn("not installed", body["error"])


if __name__ == "__main__":
    unittest.main()
