"""The success criteria from docs/design.md as executable tests.

Every class here corresponds to one numbered criterion. If one of them fails,
the tool is breaking a promise that it makes in public.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: F401  removes this run's temp directories
from make_fixtures import EXPECTED_FINDINGS, build_hostile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "bin", "plugin-inventar")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")


def run_tool(path, *extra, state=None, env_extra=None, cwd=None):
    env = dict(os.environ)
    if state:
        env["XDG_STATE_HOME"] = state
    env.update(env_extra or {})
    return subprocess.run([sys.executable, TOOL, path, *extra],
                          capture_output=True, text=True, env=env,
                          cwd=cwd or ROOT, timeout=120)


class TestCriterion1DiffAcrossVersionChange(unittest.TestCase):
    """A second run after an update shows the differences, even when the
    installation path has changed."""

    def test_command_change_is_not_a_pair(self):
        state = tempfile.mkdtemp()
        base = tempfile.mkdtemp()
        first = os.path.join(base, "1.0.0")
        shutil.copytree(os.path.join(FIXTURES, "complete"), first)
        run_tool(first, "--as", "test@markt", state=state)

        second = os.path.join(base, "1.1.0")
        shutil.copytree(first, second)
        with open(os.path.join(second, "hooks", "hooks.json"), "w") as f:
            json.dump({"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [
                {"type": "command", "command": "etwas ganz anderes"}]}]}}, f)

        result = run_tool(second, "--as", "test@markt", state=state)
        self.assertIn("before", result.stdout)
        self.assertIn("etwas ganz anderes", result.stdout)
        # The actual test: NO pair of added and removed.
        self.assertNotIn("+ Hook ", result.stdout)
        self.assertNotIn("- Hook ", result.stdout)


class TestCriterion2Completeness(unittest.TestCase):
    """A run reports hooks with their command, MCP targets, bin/ and settings."""

    def setUp(self):
        self.state = tempfile.mkdtemp()
        self.output = json.loads(
            run_tool(os.path.join(FIXTURES, "complete"), "--json",
                     state=self.state).stdout)["inventory"]["entries"]

    def test_all_kinds_are_present(self):
        kinds = {v["kind"] for v in self.output.values()}
        for expected in ("hook", "mcp", "bin", "settings", "command", "skill", "agent"):
            self.assertIn(expected, kinds, f"kind {expected} missing")

    def test_hook_command_is_character_exact(self):
        hooks = [v for v in self.output.values() if v["kind"] == "hook"]
        self.assertEqual(hooks[0]["fields"]["command"],
                         '"${CLAUDE_PLUGIN_ROOT}/bin/werkzeug" start')

    def test_mcp_target_and_variables(self):
        mcp = self.output["mcp:beispiel"]["fields"]
        self.assertEqual(mcp["command"], "uvx")
        self.assertIn("MEIN_TOKEN", mcp["env_variables"])

    def test_settings_agent_with_value(self):
        self.assertEqual(self.output["settings:agent"]["fields"]["value"],
                         "eigener-haupt-agent")


class TestCriterion3ManifestPaths(unittest.TestCase):
    """Hooks that are only reachable through a manifest-declared path get
    reported. This is the worst failure this tool could possibly have."""

    def test_declared_hook_path_is_found(self):
        state = tempfile.mkdtemp()
        target = tempfile.mkdtemp()
        os.makedirs(os.path.join(target, ".claude-plugin"))
        os.makedirs(os.path.join(target, "konfig"))
        with open(os.path.join(target, ".claude-plugin", "plugin.json"), "w") as f:
            json.dump({"name": "versteckt", "hooks": "./konfig/eigene-hooks.json"}, f)
        with open(os.path.join(target, "konfig", "eigene-hooks.json"), "w") as f:
            json.dump({"hooks": {"Stop": [{"matcher": "*", "hooks": [
                {"type": "command", "command": "versteckter befehl"}]}]}}, f)

        data = json.loads(run_tool(target, "--json", state=state).stdout)["inventory"]
        hooks = [v for v in data["entries"].values() if v["kind"] == "hook"]
        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0]["fields"]["command"], "versteckter befehl")
        self.assertEqual(hooks[0]["source"], "konfig/eigene-hooks.json")
        self.assertEqual(hooks[0]["source_kind"], "manifest")
        self.assertNotIn("hooks", data["checked_absent"])


class TestCriterion4Robustness(unittest.TestCase):
    """Broken and hostile input produces findings, not crashes."""

    def setUp(self):
        self.state = tempfile.mkdtemp()
        self.target = tempfile.mkdtemp()
        build_hostile(self.target)

    def test_no_crash(self):
        result = run_tool(self.target, state=self.state)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)

    def test_findings_match_exactly(self):
        data = json.loads(
            run_tool(self.target, "--json", state=self.state).stdout)["inventory"]
        codes = {finding["code"] for finding in data["findings"]}
        for entry in data["entries"].values():
            codes |= set(entry["findings"])
        self.assertEqual(codes, set(EXPECTED_FINDINGS))

    def test_control_characters_are_defused(self):
        result = run_tool(self.target, state=self.state)
        # text=True translates \r to \n before the assertion can see it, so
        # checking stdout for \r proves nothing. The escaped form below is the
        # assertion that actually holds.
        self.assertNotIn("\x1b", result.stdout)
        self.assertIn("\\x0d", result.stdout)

    def test_symlink_pointing_outside_is_not_read_as_a_skill(self):
        data = json.loads(
            run_tool(self.target, "--json", state=self.state).stdout)["inventory"]
        self.assertNotIn("skill:nach-aussen", data["entries"])
        self.assertIn("skill:echt", data["entries"])

    def test_broken_fixture_runs_through(self):
        result = run_tool(os.path.join(FIXTURES, "broken"), state=self.state)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)


class TestCriterion5Determinism(unittest.TestCase):
    """Two runs over unchanged material produce byte-identical inventory
    parts, even under a different environment."""

    def test_environment_changes_nothing(self):
        state = tempfile.mkdtemp()
        target = os.path.join(FIXTURES, "complete")
        a = run_tool(target, "--json", state=state, cwd=ROOT,
                     env_extra={"LC_ALL": "C", "TZ": "UTC", "PYTHONHASHSEED": "0"})
        b = run_tool(target, "--json", state=state, cwd=tempfile.mkdtemp(),
                     env_extra={"LC_ALL": "de_DE.UTF-8", "TZ": "Asia/Tokyo",
                                "PYTHONHASHSEED": "1"})
        ai = json.dumps(json.loads(a.stdout)["inventory"], sort_keys=True)
        bi = json.dumps(json.loads(b.stdout)["inventory"], sort_keys=True)
        self.assertEqual(ai, bi)

    def test_no_absolute_paths_in_the_compared_part(self):
        state = tempfile.mkdtemp()
        target = os.path.join(FIXTURES, "complete")
        data = json.loads(run_tool(target, "--json", state=state).stdout)
        self.assertNotIn(ROOT, json.dumps(data["inventory"]))
        self.assertIn(ROOT, json.dumps(data["meta"]))


class TestCriterion6SelfRun(unittest.TestCase):
    """The tool reports its own bin/ entry."""

    def setUp(self):
        self.state = tempfile.mkdtemp()
        self.entries = json.loads(
            run_tool(ROOT, "--json", state=self.state).stdout)["inventory"]["entries"]

    def test_own_bin_entry(self):
        self.assertIn("bin:plugin-inventar", self.entries)

    def test_own_command(self):
        self.assertIn("command:stand", self.entries)

    def test_fixtures_are_not_counted(self):
        """The self-run must not report the repository's own fixtures.

        Not because of an exemption -- tests/ is simply not a collection path.
        """
        self.assertNotIn("skill:beispiel", self.entries)
        self.assertNotIn("command:los", self.entries)

    def test_exactly_two_kinds_of_component(self):
        kinds = {v["kind"] for v in self.entries.values()}
        self.assertEqual(kinds, {"bin", "command"})

    def test_the_block_printed_in_the_readme_is_the_real_output(self):
        """Criterion 6 promises the README block equals a fresh run.

        Nothing held that promise, and it showed: the diff example two
        sections above the block was still in the format from before the
        rename of every field. An output block nobody checks goes stale, and
        it is the one thing a reader copies.
        """
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as handle:
            readme = handle.read()
        blocks = readme.split("```")
        printed = [b.strip("\n") for b in blocks[1::2]
                   if b.lstrip().startswith("plugin-inventar 0.1.0")]
        self.assertEqual(1, len(printed), "self-run block not found in README")
        fresh = run_tool(ROOT, "--no-save",
                         state=tempfile.mkdtemp()).stdout.strip("\n")
        self.assertEqual(printed[0], fresh)


if __name__ == "__main__":
    unittest.main()
