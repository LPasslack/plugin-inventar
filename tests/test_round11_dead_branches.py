"""Tests for branches that no test in the suite executes at all."""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: F401
from inventory.collect import build_inventory, collect_hooks, collect_mcp, resolve_paths
from inventory.installed import installed_plugins
from inventory.state import load, state_path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "bin", "plugin-inventar")


class T(unittest.TestCase):
    def temp(self):
        p = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, p, ignore_errors=True)
        return p

    def write(self, root, rel, content):
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as h:
            h.write(content if isinstance(content, str) else json.dumps(content))
        return full

    def plugin(self, **manifest):
        root = self.temp()
        self.write(root, ".claude-plugin/plugin.json",
                   dict({"name": "p", "version": "1"}, **manifest))
        return root


class Fake(unittest.TestCase):
    def setUp(self):
        self.config = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.config, ignore_errors=True)
        self.state = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.state, ignore_errors=True)
        os.makedirs(os.path.join(self.config, "plugins"))
        self.registry = {}

    def plugin(self, name, market="markt", version="1.0.0", scope="user"):
        path = os.path.join(self.config, "plugins", "cache", market, name, version)
        os.makedirs(os.path.join(path, ".claude-plugin"))
        with open(os.path.join(path, ".claude-plugin", "plugin.json"), "w") as h:
            json.dump({"name": name, "version": version}, h)
        full = os.path.join(path, "commands", "go.md")
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as h:
            h.write("---\nname: go\n---\nB\n")
        self.registry.setdefault(f"{name}@{market}", []).append(
            {"scope": scope, "installPath": path, "version": version})
        return path

    def commit(self, settings=None):
        with open(os.path.join(self.config, "plugins",
                               "installed_plugins.json"), "w") as h:
            json.dump({"version": 2, "plugins": self.registry}, h)
        if settings is not None:
            with open(os.path.join(self.config, "settings.json"), "w") as h:
                h.write(settings if isinstance(settings, str) else json.dumps(settings))

    def run_sweep(self, *extra):
        return subprocess.run(
            [sys.executable, TOOL, *extra], capture_output=True, text=True,
            timeout=120,
            env=dict(os.environ, CLAUDE_CONFIG_DIR=self.config,
                     XDG_STATE_HOME=self.state))


class TheEnabledStateIsNeverGuessed(Fake):
    def test_an_unreadable_settings_file_is_a_finding(self):
        self.plugin("x")
        self.commit(settings="{ kaputt")
        records, findings = installed_plugins(self.config)
        self.assertEqual(1, len(records))
        self.assertIn("enabled-state-unknown", [f.get("detail") for f in findings])

    def test_a_settings_file_that_is_a_list_is_a_finding(self):
        self.plugin("x")
        self.commit(settings=[1, 2, 3])
        _, findings = installed_plugins(self.config)
        self.assertIn("enabled-state-unknown", [f.get("detail") for f in findings])

    def test_enabled_plugins_of_the_wrong_type_is_a_finding(self):
        self.plugin("x")
        self.commit(settings={"enabledPlugins": ["x@markt"]})
        _, findings = installed_plugins(self.config)
        self.assertIn("enabled-state-unknown", [f.get("detail") for f in findings])

    def test_no_settings_file_at_all_is_not_a_finding(self):
        self.plugin("x")
        self.commit()
        records, findings = installed_plugins(self.config)
        self.assertEqual(1, len(records))
        self.assertEqual([True], [r["enabled"] for r in records])
        self.assertEqual([], findings)


class TheSweepSaysWhenItCouldNotWrite(Fake):
    def test_a_state_directory_that_cannot_be_written_is_not_a_baseline(self):
        self.plugin("x")
        self.commit()
        directory = os.path.join(self.state, "plugin-inventar")
        os.makedirs(directory)
        os.chmod(directory, 0o500)
        self.addCleanup(os.chmod, directory, 0o700)
        result = self.run_sweep()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("saved nothing", result.stdout)
        self.assertNotIn("ab jetzt dein baseline", result.stdout)

    def test_a_stored_state_with_another_schema_is_not_compared(self):
        self.plugin("s")
        self.commit()
        self.run_sweep()
        for name in glob.glob(os.path.join(self.state, "plugin-inventar", "*.json")):
            if name.endswith(".1.json"):
                continue
            with open(name, encoding="utf-8") as h:
                data = json.load(h)
            data["meta"]["schema"] = 999
            with open(name, "w", encoding="utf-8") as h:
                json.dump(data, h)
        result = self.run_sweep()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("different schema", result.stdout)


class AStoredStateWithABrokenShapeIsRefused(T):
    def test_a_checked_absent_that_is_not_a_list(self):
        directory = self.temp()
        previous = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = directory
        self.addCleanup(lambda: os.environ.__setitem__("XDG_STATE_HOME", previous)
                        if previous else os.environ.pop("XDG_STATE_HOME", None))
        path = state_path("a@b")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as h:
            json.dump({"meta": {"schema": 3},
                       "inventory": {"entries": {}, "identity": {},
                                     "findings": [], "checked_absent": "kaputt"}}, h)
        data, reason = load("a@b")
        self.assertIsNone(data)
        self.assertEqual("unerwarteter Aufbau", reason)


class UnreadableSourcesAreNamed(T):
    def test_an_mcp_file_that_cannot_be_read_is_a_finding(self):
        root = self.plugin()
        os.mkfifo(os.path.join(root, ".mcp.json"))
        _, findings = collect_mcp(root, resolve_paths(root, {})[0])
        self.assertIn("mcpServers", [f.get("category") for f in findings])
        self.assertIn("not-a-regular-file", [f["code"] for f in findings])

    def test_an_mcp_servers_value_that_is_not_an_object_is_a_finding(self):
        root = self.plugin()
        self.write(root, ".mcp.json", {"mcpServers": 7})
        _, findings = collect_mcp(root, resolve_paths(root, {})[0])
        self.assertIn("mcp-not-an-object", [f.get("detail") for f in findings])

    def test_a_hook_that_is_not_an_object_is_a_finding(self):
        root = self.plugin()
        self.write(root, "hooks/hooks.json",
                   {"hooks": {"Stop": [{"matcher": "", "hooks": ["kein Objekt"]}]}})
        _, findings = collect_hooks(root, resolve_paths(root, {})[0])
        self.assertIn("hook-not-an-object", [f.get("detail") for f in findings])

    def test_a_command_file_that_cannot_be_read_keeps_its_entry(self):
        root = self.plugin()
        os.makedirs(os.path.join(root, "commands"))
        os.mkfifo(os.path.join(root, "commands", "go.md"))
        entry = build_inventory(root)["entries"]["command:go"]
        self.assertEqual(["not-a-regular-file"], entry["findings"])

    def test_a_conventional_path_pointing_out_of_the_plugin_is_a_finding(self):
        root = self.plugin()
        outside = self.temp()
        os.symlink(outside, os.path.join(root, "skills"))
        findings = build_inventory(root)["findings"]
        self.assertIn("convention-path-points-outside",
                      [f.get("detail") for f in findings])


if __name__ == "__main__":
    unittest.main()
