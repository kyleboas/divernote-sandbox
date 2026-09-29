import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
from notes_bridge import divernote_command  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
