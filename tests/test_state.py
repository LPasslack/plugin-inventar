import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: F401  removes this run's temp directories
from inventory.state import diff, load, save, state_path


def inv(entries):
    return {"identity": {}, "entries": entries,
            "checked_absent": [], "findings": []}


def hook(command, event="Stop"):
    return {"kind": "hook", "source": "hooks/hooks.json", "source_kind": "convention",
            "fields": {"event": event, "matcher": "*", "command": command},
            "markers": [], "findings": []}


class TestDiff(unittest.TestCase):
    def test_unchanged_reports_nothing(self):
        a = inv({"hook:A::0": hook("x")})
        d = diff(a, a)
        self.assertEqual(d["added"], [])
        self.assertEqual(d["removed"], [])
        self.assertEqual(d["changed"], {})

    def test_changed_command_is_not_a_pair(self):
        """The most important test of the project.

        If a command change shows up as a pair of disappeared and appeared,
        the tool's core promise is broken.
        """
        old = inv({"hook:A::0": hook("alt")})
        new = inv({"hook:A::0": hook("neu")})
        d = diff(old, new)
        self.assertEqual(d["added"], [])
        self.assertEqual(d["removed"], [])
        self.assertEqual(d["changed"]["hook:A::0"]["command"], ("alt", "neu"))

    def test_added(self):
        d = diff(inv({}), inv({"skill:x": {"fields": {}}}))
        self.assertEqual(d["added"], ["skill:x"])

    def test_removed(self):
        d = diff(inv({"skill:x": {"fields": {}}}), inv({}))
        self.assertEqual(d["removed"], ["skill:x"])

    def test_several_fields_changed(self):
        old = inv({"mcp:a": {"fields": {"url": "http://alt", "transport": "http"}}})
        new = inv({"mcp:a": {"fields": {"url": "http://neu", "transport": "sse"}}})
        d = diff(old, new)
        self.assertEqual(set(d["changed"]["mcp:a"]), {"url", "transport"})

    def test_new_field_is_detected(self):
        old = inv({"skill:a": {"fields": {"x": 1}}})
        new = inv({"skill:a": {"fields": {"x": 1, "y": 2}}})
        d = diff(old, new)
        self.assertEqual(d["changed"]["skill:a"]["y"], (None, 2))

    def test_source_change_is_reported(self):
        """Real-world case watch 0.1.3 -> 0.2.0: the skill moves from the
        plugin root into a skills/ directory. Same thing, different place."""
        old = inv({"skill:watch": {"source": "SKILL.md",
                                   "source_kind": "convention", "fields": {}}})
        new = inv({"skill:watch": {"source": "skills/watch/SKILL.md",
                                   "source_kind": "convention", "fields": {}}})
        d = diff(old, new)
        self.assertEqual(d["added"], [])
        self.assertEqual(d["removed"], [])
        self.assertEqual(d["changed"]["skill:watch"]["source"],
                         ("SKILL.md", "skills/watch/SKILL.md"))

    def test_source_kind_change_is_reported(self):
        old = inv({"skill:a": {"source": "skills/a", "source_kind": "convention",
                               "fields": {}}})
        new = inv({"skill:a": {"source": "skills/a", "source_kind": "manifest",
                               "fields": {}}})
        self.assertIn("source_kind", diff(old, new)["changed"]["skill:a"])

    def test_list_order_is_not_a_change(self):
        # The collectors already sort; guarded here so it stays that way.
        old = inv({"skill:a": {"fields": {"allowed_tools": ["Bash", "Read"]}}})
        new = inv({"skill:a": {"fields": {"allowed_tools": ["Bash", "Read"]}}})
        self.assertEqual(diff(old, new)["changed"], {})


class TestState(unittest.TestCase):
    def setUp(self):
        # Set and never reset, the variable stayed put for the rest of the
        # process. Harmless only as long as this file runs last: any future
        # test that calls save() in-process before it would write into the
        # user's own ~/.local/state.
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        patcher = unittest.mock.patch.dict(os.environ,
                                           {"XDG_STATE_HOME": self.dir})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_save_and_load(self):
        state = {"meta": {"schema": 1}, "inventory": inv({"skill:a": {"fields": {}}})}
        save("test@markt", state)
        loaded, reason = load("test@markt")
        self.assertIsNone(reason)
        self.assertIn("skill:a", loaded["inventory"]["entries"])

    def test_load_without_stored_state(self):
        self.assertEqual(load("gibt-es-nicht@markt"), (None, None))

    def test_no_temp_file_is_left_behind(self):
        save("a@b", {"meta": {"schema": 1}, "inventory": inv({})})
        directory = os.path.dirname(state_path("a@b"))
        leftover = [f for f in os.listdir(directory) if f.startswith(".tmp-")]
        self.assertEqual(leftover, [])

    def test_predecessor_is_rotated(self):
        save("a@b", {"meta": {"schema": 1}, "inventory": inv({"x:1": {"fields": {}}})})
        save("a@b", {"meta": {"schema": 1}, "inventory": inv({"x:2": {"fields": {}}})})
        predecessor = state_path("a@b").replace(".json", ".1.json")
        self.assertTrue(os.path.exists(predecessor))

    def test_slug_is_filesystem_safe(self):
        save("Gross/Klein@markt", {"meta": {"schema": 1}, "inventory": inv({})})
        name = os.path.basename(state_path("Gross/Klein@markt"))
        self.assertNotIn("/", name)

    def test_upper_and_lower_case_do_not_collide(self):
        """On a filesystem that does not distinguish upper and lower case, Foo
        and foo would otherwise use the same file."""
        self.assertNotEqual(state_path("Foo@markt"), state_path("foo@markt"))

    def test_very_long_key_does_not_blow_up_the_path(self):
        long_key = "x" * 300 + "@markt"
        save(long_key, {"meta": {"schema": 1}, "inventory": inv({})})
        self.assertLess(len(os.path.basename(state_path(long_key))), 100)
        self.assertIsNotNone(load(long_key)[0])

    def test_corrupt_state_file_returns_none(self):
        """A tool that does not survive its own cache is dead after an aborted
        write."""
        save("a@b", {"meta": {"schema": 1}, "inventory": inv({})})
        for content in ('[]', '"text"', '{"meta":{"schema":1}}',
                        '{"meta":{"schema":1},"inventory":[]}',
                        '{"inventory":{"entries":[1,2]}}', '42'):
            with open(state_path("a@b"), "w") as f:
                f.write(content)
            data, reason = load("a@b")
            self.assertIsNone(data, f"not caught: {content}")
            self.assertIsNotNone(reason, f"no reason given: {content}")

    def test_file_is_deterministic(self):
        state = {"meta": {"schema": 1}, "inventory": inv({"b:2": {"fields": {}},
                                                          "a:1": {"fields": {}}})}
        save("det@markt", state)
        with open(state_path("det@markt"), encoding="utf-8") as f:
            first = f.read()
        save("det@markt", state)
        with open(state_path("det@markt"), encoding="utf-8") as f:
            second = f.read()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
