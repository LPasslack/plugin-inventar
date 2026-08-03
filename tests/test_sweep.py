"""Tests for the collective run over every installed plugin.

The tool is a memory, and a memory is worth nothing until it has something
to remember. Before this mode, taking that first baseline meant knowing the
cache layout and typing one path per plugin -- eighteen of them on the
machine this was built on. Whoever forgot one created exactly the blind spot
the tool exists against.

German literals are the report's own wording.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: F401  removes this run's temp directories
from inventory.installed import installed_plugins
from inventory.report import render_sweep

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "bin", "plugin-inventar")


class Fake(unittest.TestCase):
    """Builds a throwaway Claude Code configuration directory."""

    def setUp(self):
        self.config = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.config, ignore_errors=True)
        self.state = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.state, ignore_errors=True)
        os.makedirs(os.path.join(self.config, "plugins"))
        self.registry = {}
        self.enabled = {}

    def plugin(self, name, market="markt", version="1.0.0", scope="user",
               enabled=True, **manifest):
        path = os.path.join(self.config, "plugins", "cache", market, name, version)
        os.makedirs(os.path.join(path, ".claude-plugin"))
        with open(os.path.join(path, ".claude-plugin", "plugin.json"), "w") as handle:
            json.dump(dict({"name": name, "version": version}, **manifest), handle)
        self.write(path, "commands/go.md", "---\nname: go\n---\nB\n")
        key = f"{name}@{market}"
        self.registry.setdefault(key, []).append(
            {"scope": scope, "installPath": path, "version": version})
        self.enabled[key] = enabled
        return path

    def write(self, root, relpath, content):
        full = os.path.join(root, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(content if isinstance(content, str) else json.dumps(content))

    def commit(self):
        with open(os.path.join(self.config, "plugins",
                               "installed_plugins.json"), "w") as handle:
            json.dump({"version": 2, "plugins": self.registry}, handle)
        with open(os.path.join(self.config, "settings.json"), "w") as handle:
            json.dump({"enabledPlugins": self.enabled}, handle)

    def run_sweep(self, *extra):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=self.config,
                   XDG_STATE_HOME=self.state)
        return subprocess.run([sys.executable, TOOL, *extra], env=env,
                              capture_output=True, text=True, timeout=120)


class DiscoveryUsesTheRegistry(Fake):

    def test_every_installation_becomes_a_record(self):
        self.plugin("eins")
        self.plugin("zwei")
        self.commit()
        records, findings = installed_plugins(self.config)
        self.assertEqual(["eins@markt", "zwei@markt"], [r["key"] for r in records])
        self.assertEqual([], findings)

    def test_the_same_plugin_at_two_scopes_is_two_records(self):
        """A registry key maps to a LIST, because the same plugin can be
        installed at several scopes at once. Each is its own installation
        with its own directory, so each needs its own comparison."""
        self.plugin("doppelt", version="1.0.0", scope="user")
        self.plugin("doppelt", version="2.0.0", scope="project")
        self.commit()
        records, _ = installed_plugins(self.config)
        self.assertEqual(2, len(records))
        self.assertEqual({"user", "project"}, {r["scope"] for r in records})

    def test_a_disabled_plugin_is_still_found(self):
        """It does not run, but it keeps updating, and whoever switches it
        back on switches on the newer version."""
        self.plugin("aus", enabled=False)
        self.commit()
        records, _ = installed_plugins(self.config)
        self.assertEqual([False], [r["enabled"] for r in records])

    def test_a_plugin_without_an_entry_counts_as_enabled(self):
        """enabledPlugins only carries a key once someone has flipped it."""
        self.plugin("neu")
        self.commit()
        with open(os.path.join(self.config, "settings.json"), "w") as handle:
            json.dump({"enabledPlugins": {}}, handle)
        records, _ = installed_plugins(self.config)
        self.assertEqual([True], [r["enabled"] for r in records])

    def test_a_broken_registry_produces_a_finding_not_silence(self):
        with open(os.path.join(self.config, "plugins",
                               "installed_plugins.json"), "w") as handle:
            handle.write("{ kaputt")
        records, findings = installed_plugins(self.config)
        self.assertEqual([], records)
        self.assertTrue(findings)

    def test_an_entry_without_a_path_is_reported(self):
        self.registry["ohne@markt"] = [{"scope": "user", "version": "1"}]
        self.commit()
        _, findings = installed_plugins(self.config)
        self.assertIn("declared-path-missing", [f["code"] for f in findings])

    def test_no_registry_at_all_is_not_an_error(self):
        records, findings = installed_plugins(self.config)
        self.assertEqual(([], []), (records, findings))


class TheFirstRunSaysItOnce(Fake):

    def test_not_once_per_plugin(self):
        """Printing "this is the first run" ten times buries the one
        sentence that matters."""
        for name in ("eins", "zwei", "drei"):
            self.plugin(name)
        self.commit()
        result = self.run_sweep()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("3 Plugins. That is your baseline", result.stdout)
        self.assertNotIn("first run", result.stdout)
        self.assertEqual(1, result.stdout.count("baseline"))

    def test_the_second_run_is_silent_about_what_did_not_move(self):
        for name in ("eins", "zwei", "drei"):
            self.plugin(name)
        self.commit()
        self.run_sweep()
        result = self.run_sweep()
        self.assertIn("3 unchanged", result.stdout)
        self.assertNotIn("Changed", result.stdout)

    def test_a_change_in_one_plugin_names_that_plugin(self):
        path = self.plugin("bewegt")
        self.plugin("ruhig")
        self.commit()
        self.run_sweep()
        self.write(path, "commands/go.md", "---\nname: go\n---\nGanz anderer Rumpf\n")
        result = self.run_sweep()
        self.assertIn("Changed", result.stdout)
        self.assertIn("bewegt@markt", result.stdout)
        self.assertIn("1 unchanged", result.stdout)
        # The one that did not move must not appear under the changed ones.
        changed_block = result.stdout.split("Changed")[1].split("unchanged")[0]
        self.assertNotIn("ruhig@markt", changed_block)

    def test_disabled_plugins_are_named_with_their_state(self):
        self.plugin("an")
        self.plugin("aus", enabled=False)
        self.commit()
        result = self.run_sweep()
        # Listed above with its marker, so the closing line only carries the
        # count and the reason -- repeating the name would be noise.
        self.assertIn("aus@markt  [disabled]", result.stdout)
        self.assertIn("One of them is disabled", result.stdout)
        self.assertIn("still updates",
                      " ".join(result.stdout.split()))

    def test_a_disabled_plugin_that_is_not_listed_above_is_named(self):
        """When everything is unchanged, nothing is listed, and then the
        closing line is the only place the name can appear."""
        self.plugin("an")
        self.plugin("aus", enabled=False)
        self.commit()
        self.run_sweep()
        result = self.run_sweep()
        self.assertIn("aus@markt", result.stdout)
        self.assertIn("currently disabled", result.stdout)
        # The count beside the list has to match the list, not some
        # other set. It used to say "3" next to two names.
        self.assertIn("One plugin is currently disabled", result.stdout)

    def test_a_registry_entry_whose_directory_is_gone_is_reported(self):
        path = self.plugin("verschwunden")
        self.commit()
        shutil.rmtree(path)
        result = self.run_sweep()
        self.assertIn("verschwunden@markt", result.stdout)

    def test_nothing_installed_is_said_plainly(self):
        self.commit()
        result = self.run_sweep()
        self.assertEqual(2, result.returncode)
        self.assertIn("Keine installierten Plugins", result.stderr)


class TheSweepAndTheSinglePathShareTheirState(Fake):

    def test_a_path_run_sees_what_the_sweep_recorded(self):
        """Both modes derive the key the same way, so a sweep and a
        single-directory run are the same memory, not two."""
        path = self.plugin("geteilt")
        self.commit()
        self.run_sweep()
        result = subprocess.run(
            [sys.executable, TOOL, path], capture_output=True, text=True,
            env=dict(os.environ, CLAUDE_CONFIG_DIR=self.config,
                     XDG_STATE_HOME=self.state), timeout=120)
        self.assertIn("No changes", result.stdout)


class TheDefaultIsNoLongerTheWorkingDirectory(Fake):

    def test_a_viewer_in_their_own_project_does_not_get_a_refusal(self):
        """The old default was the current directory. Whoever installed the
        plugin and typed the command stood in their own project and got
        "There is no plugin here" -- a refusal as a first impression."""
        self.plugin("irgendwas")
        self.commit()
        elsewhere = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)
        result = subprocess.run(
            [sys.executable, TOOL], cwd=elsewhere, capture_output=True, text=True,
            env=dict(os.environ, CLAUDE_CONFIG_DIR=self.config,
                     XDG_STATE_HOME=self.state), timeout=120)
        self.assertEqual(0, result.returncode)
        self.assertNotIn("There is no plugin here", result.stdout + result.stderr)
        self.assertIn("Newly recorded", result.stdout)

    def test_flags_that_need_a_directory_say_so(self):
        self.plugin("x")
        self.commit()
        for flag in ("--json", "--as"):
            extra = [flag] if flag == "--json" else [flag, "schluessel"]
            result = self.run_sweep(*extra)
            self.assertEqual(1, result.returncode, flag)
            self.assertIn("a single directory", result.stderr, flag)


class TheReportShape(unittest.TestCase):

    def _result(self, key, differences, enabled=True, scope="user", note=None):
        return {"key": key, "differences": differences, "enabled": enabled,
                "scope": scope, "note": note}

    def test_an_empty_diff_counts_as_unchanged(self):
        empty = {"added": [], "removed": [], "changed": {}, "identity": {},
                 "matchers": {}, "commands": {},
                 "findings": {"added": [], "removed": []}, "categories": {}}
        text = render_sweep([self._result("a@b", empty)])
        self.assertIn("1 unchanged", text)
        self.assertNotIn("Changed", text)

    def test_a_new_finding_alone_counts_as_changed(self):
        """Findings were compared for the first time in the ninth round.
        The sweep has to honour that, or a plugin that gained a finding
        would be counted as unchanged."""
        differences = {"added": [], "removed": [], "changed": {}, "identity": {},
                       "matchers": {}, "commands": {}, "categories": {},
                       "findings": {"added": [{"code": "absolute-path",
                                               "path": "/etc/x",
                                               "category": "hooks"}],
                                    "removed": []}}
        text = render_sweep([self._result("a@b", differences)])
        self.assertIn("Changed", text)
        self.assertIn("a@b", text)

    def test_a_project_scope_installation_is_marked(self):
        text = render_sweep([self._result("a@b", None, scope="project")])
        # Scopes are a closed vocabulary and get translated, like source_kind.
        # Since 03.08.2026 the report is English, so the translation and the
        # raw key look alike -- what must not appear is the old German word.
        self.assertIn("[project]", text)
        self.assertNotIn("Projekt", text)

    def test_the_first_run_names_what_it_recorded(self):
        """Not printing "first run" per plugin was the point. Withholding
        WHICH ones were recorded is a different thing, and it is the moment
        where someone spots a plugin they had forgotten about."""
        text = render_sweep([self._result("a@b", None),
                             self._result("c@d", None)])
        self.assertIn("a@b", text)
        self.assertIn("c@d", text)


if __name__ == "__main__":
    unittest.main()
