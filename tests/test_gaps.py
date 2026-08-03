"""Regression tests for repaired bugs that had no test until now.

Location: tests/test_gaps.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: F401  removes this run's temp directories
from inventory.collect import (_url_without_secret, build_inventory, collect_hooks,
                               collect_mcp, collect_directories, resolve_paths)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "bin", "plugin-inventar")


def run_tool(path, *extra, state=None):
    env = dict(os.environ)
    if state:
        env["XDG_STATE_HOME"] = state
    return subprocess.run([sys.executable, TOOL, path, *extra],
                          capture_output=True, text=True, env=env, cwd=ROOT, timeout=120)


def run_tool_raw(path, *extra, state=None):
    """Like run_tool(), but WITHOUT text=True.

    Important: subprocess with text=True silently translates \\r into \\n
    (universal newlines). `assertNotIn("\\r", e.stdout)` can NEVER fail there
    and is worthless as an assurance. Whoever wants to check control
    characters in the process output has to look at the bytes.
    """
    env = dict(os.environ)
    if state:
        env["XDG_STATE_HOME"] = state
    return subprocess.run([sys.executable, TOOL, path, *extra],
                          capture_output=True, env=env, cwd=ROOT, timeout=120)


def write_json(root, relpath, obj):
    full = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        json.dump(obj, f)


# ------------------------------------------------------------------- Gap 1 ---
class TestWrongTypesInsteadOfEmptyValues(unittest.TestCase):
    """`or []` only catches empty values, not wrong types.

    The repair (as_list/as_dict) was applied in three places, but never
    secured by a test: all four mutations back to `or []` stayed green.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def _paths(self):
        return resolve_paths(self.root, {})[0]

    def test_hook_args_as_a_number_does_not_crash(self):
        write_json(self.root, "hooks/hooks.json", {"hooks": {"Stop": [
            {"matcher": "*", "hooks": [
                {"type": "command", "command": "x", "args": 7}]}]}})
        entries, _ = collect_hooks(self.root, self._paths())
        self.assertEqual(list(entries.values())[0]["fields"]["args"], [])

    def test_hook_list_as_a_number_does_not_crash(self):
        write_json(self.root, "hooks/hooks.json",
                   {"hooks": {"Stop": [{"matcher": "*", "hooks": 7}]}})
        entries, _ = collect_hooks(self.root, self._paths())
        self.assertEqual(entries, {})

    def test_hook_headers_as_a_list_does_not_crash(self):
        write_json(self.root, "hooks/hooks.json", {"hooks": {"Stop": [
            {"matcher": "*", "hooks": [
                {"type": "http", "url": "https://x", "headers": ["A", "B"]}]}]}})
        entries, _ = collect_hooks(self.root, self._paths())
        self.assertEqual(list(entries.values())[0]["fields"]["header_names"], [])

    def test_mcp_args_as_a_number_does_not_crash(self):
        write_json(self.root, ".mcp.json",
                   {"mcpServers": {"s": {"command": "x", "args": 7}}})
        entries, _ = collect_mcp(self.root, self._paths())
        self.assertEqual(entries["mcp:s"]["fields"]["args"], [])

    def test_mcp_env_as_a_list_does_not_crash(self):
        write_json(self.root, ".mcp.json",
                   {"mcpServers": {"s": {"command": "x", "env": ["A"]}}})
        entries, _ = collect_mcp(self.root, self._paths())
        self.assertIsInstance(entries["mcp:s"]["fields"]["env_variables"], list)

    def test_boolean_allowed_tools_does_not_crash_the_report(self):
        """A real crash: `allowed-tools: true` in the frontmatter.

        _value() returns True there, `raw.get(...) or []` lets True through,
        and the report iterates over it -> TypeError instead of a report.
        """
        full = os.path.join(self.root, "skills", "x", "SKILL.md")
        os.makedirs(os.path.dirname(full))
        with open(full, "w", encoding="utf-8") as f:
            f.write("---\nname: x\nallowed-tools: true\ndisallowed-tools: yes\n---\n")
        entries, _ = collect_directories(self.root, self._paths())
        fields = entries["skill:x"]["fields"]
        self.assertIsInstance(fields["allowed_tools"], list)
        self.assertIsInstance(fields["disallowed_tools"], list)

    def test_malicious_plugin_gives_a_report_instead_of_a_traceback(self):
        os.makedirs(os.path.join(self.root, ".claude-plugin"))
        write_json(self.root, ".claude-plugin/plugin.json", {"name": "b", "version": "1"})
        full = os.path.join(self.root, "skills", "x", "SKILL.md")
        os.makedirs(os.path.dirname(full))
        with open(full, "w", encoding="utf-8") as f:
            f.write("---\nname: x\nallowed-tools: true\n---\n")
        result = run_tool(self.root, state=tempfile.mkdtemp())
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.returncode, 0)


# ------------------------------------------------------------------- Gap 2 ---
class TestMcpUrlMasking(unittest.TestCase):
    """Masking header values while printing the token right next to it in the
    query string creates a false sense of security. The repair had no test:
    ripping the masking out entirely stayed green."""

    def test_query_string_is_masked(self):
        self.assertEqual(_url_without_secret("https://h/mcp?token=geheim123"),
                         "https://h/mcp?[…]")

    def test_user_and_password_are_masked(self):
        result = _url_without_secret("https://nutzer:geheim123@h/mcp")
        self.assertNotIn("geheim123", result)
        self.assertNotIn("nutzer", result)
        self.assertIn("h/mcp", result)

    def test_harmless_url_stays_intact(self):
        self.assertEqual(_url_without_secret("https://h/mcp"), "https://h/mcp")

    def test_secret_does_not_end_up_in_the_state(self):
        root = tempfile.mkdtemp()
        write_json(root, ".mcp.json", {"mcpServers": {"s": {
            "url": "https://nutzer:pw123@h/mcp?token=geheim123"}}})
        entries, _ = collect_mcp(root, resolve_paths(root, {})[0])
        raw = json.dumps(entries)
        self.assertNotIn("geheim123", raw)
        self.assertNotIn("pw123", raw)


# ------------------------------------------------------------------- Gap 3 ---
class TestRootSkillNameStableAcrossVersionChange(unittest.TestCase):
    """In the cache the version sits inside the path (…/<plugin>/<version>/).

    If the name of a root SKILL.md without a frontmatter name falls back to the
    DIRECTORY name, the entry ID changes with every update and produces a pair
    of added and removed -- in exactly the situation this tool was built for.
    The repair had no test.
    """

    def _plugin(self, base, version):
        target = os.path.join(base, version)
        os.makedirs(os.path.join(target, ".claude-plugin"))
        write_json(target, ".claude-plugin/plugin.json",
                   {"name": "tool", "version": version})
        with open(os.path.join(target, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\ndescription: Ein Skill ohne name-Feld\n---\nRumpf\n")
        return target

    def test_id_hangs_on_the_manifest_name_not_on_the_directory(self):
        base = tempfile.mkdtemp()
        a = build_inventory(self._plugin(base, "0.1.3"))
        b = build_inventory(self._plugin(base, "0.2.0"))
        self.assertIn("skill:tool", a["entries"])
        self.assertEqual(set(a["entries"]), set(b["entries"]))

    def test_version_change_produces_no_pair(self):
        state = tempfile.mkdtemp()
        base = tempfile.mkdtemp()
        run_tool(self._plugin(base, "0.1.3"), "--as", "w@markt", state=state)
        result = run_tool(self._plugin(base, "0.2.0"), "--as", "w@markt", state=state)
        self.assertNotIn("+ Skill ", result.stdout)
        self.assertNotIn("- Skill ", result.stdout)

    def test_move_out_of_the_root_is_a_change_not_a_pair(self):
        """in_plugin_root must ALWAYS be set, otherwise the move diffs against
        None instead of against False."""
        base = tempfile.mkdtemp()
        top = self._plugin(base, "0.1.3")
        below = os.path.join(base, "0.2.0")
        os.makedirs(os.path.join(below, ".claude-plugin"))
        write_json(below, ".claude-plugin/plugin.json",
                   {"name": "tool", "version": "0.2.0"})
        os.makedirs(os.path.join(below, "skills", "tool"))
        with open(os.path.join(below, "skills", "tool", "SKILL.md"),
                  "w", encoding="utf-8") as f:
            f.write("---\ndescription: Ein Skill ohne name-Feld\n---\nRumpf\n")

        a = build_inventory(top)["entries"]["skill:tool"]
        b = build_inventory(below)["entries"]["skill:tool"]
        self.assertIs(a["fields"]["in_plugin_root"], True)
        self.assertIs(b["fields"]["in_plugin_root"], False)
        self.assertNotEqual(a["source"], b["source"])


# ------------------------------------------------------------------- Gap 4 ---
class TestFindingsWhenThereIsNoPlugin(unittest.TestCase):
    """Without the findings the tool swallows the REASON why it found nothing
    -- for instance that a declared path led out of the plugin. And the
    findings have to go through the same defusing as the report: control
    characters from foreign manifests used to reach stderr raw."""

    def setUp(self):
        self.empty = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.empty, ".claude-plugin"))

    def test_the_reason_is_named(self):
        write_json(self.empty, ".claude-plugin/plugin.json",
                   {"name": "leer", "hooks": "../../ausserhalb.json"})
        result = run_tool(self.empty, state=tempfile.mkdtemp())
        self.assertEqual(result.returncode, 2)
        self.assertIn("path leaves the plugin", result.stderr)
        self.assertIn("There is no plugin here", result.stderr)

    def test_control_characters_in_the_finding_are_defused(self):
        write_json(self.empty, ".claude-plugin/plugin.json",
                   {"name": "leer", "commands": "weg\x1b[2J\rharmlos"})
        result = run_tool_raw(self.empty, state=tempfile.mkdtemp())
        self.assertNotIn(b"\x1b", result.stderr)
        self.assertNotIn(b"\r", result.stderr)


# ------------------------------------------------------------------- Gap 5 ---
class TestHookOutputIsDefused(unittest.TestCase):
    """The test that checks 'the ENTIRE output' does not contain a single hook
    entry. That leaves matcher, event and the 'nur wenn' line unchecked -- of
    all things the block the tool prints first, because it runs without any
    action from the user."""

    def test_control_characters_in_matcher_event_and_condition(self):
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, ".claude-plugin"))
        write_json(root, ".claude-plugin/plugin.json", {"name": "h", "version": "1"})
        write_json(root, "hooks/hooks.json", {"hooks": {"Pre\x1b[2JToolUse": [
            {"matcher": "Bash\rversteckt", "hooks": [
                {"type": "command", "command": "echo x",
                 "if": "Bash(git\x1b[5m *)"}]}]}})
        result = run_tool_raw(root, state=tempfile.mkdtemp())
        self.assertEqual(result.returncode, 0)
        self.assertNotIn(b"\x1b", result.stdout)
        self.assertNotIn(b"\r", result.stdout)
        self.assertIn(b"\\x1b", result.stdout)
        self.assertIn(b"\\x0d", result.stdout)

    def test_overly_long_matcher_is_shortened(self):
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, ".claude-plugin"))
        write_json(root, ".claude-plugin/plugin.json", {"name": "h", "version": "1"})
        write_json(root, "hooks/hooks.json", {"hooks": {"Stop": [
            {"matcher": "M" * 5000, "hooks": [
                {"type": "command", "command": "echo x"}]}]}})
        result = run_tool(root, state=tempfile.mkdtemp())
        self.assertLess(max(len(line) for line in result.stdout.splitlines()), 1000,
                        "matcher is not shortened: visible() without shorten()")


# ------------------------------------------------- Bonus: blocking on FIFOs ---
class TestNoBlockingBySpecialFiles(unittest.TestCase):
    """A tool meant to look at a FOREIGN plugin before installing it must not
    let that plugin stop it. os.open() on a FIFO blocks until a writer shows
    up; the S_ISREG check comes too late."""

    def test_fifo_does_not_stall_the_run(self):
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, ".claude-plugin"))
        write_json(root, ".claude-plugin/plugin.json", {"name": "f", "version": "1"})
        os.makedirs(os.path.join(root, "skills", "x"))
        with open(os.path.join(root, "skills", "x", "SKILL.md"), "w") as f:
            f.write("---\nname: x\n---\n")
        os.makedirs(os.path.join(root, "hooks"))
        os.mkfifo(os.path.join(root, "hooks", "hooks.json"))
        env = dict(os.environ, XDG_STATE_HOME=tempfile.mkdtemp())
        try:
            result = subprocess.run([sys.executable, TOOL, root],
                                    capture_output=True, text=True, timeout=15,
                                    env=env, cwd=ROOT)
        except subprocess.TimeoutExpired:
            self.fail("run blocked on a FIFO inside the plugin directory")
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
