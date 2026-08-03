"""Regression tests for the tenth review round.

The heaviest finding sat in the sweep that was built the same day: two
installations of one plugin landed on one state key, so each run compared
one against the state the other had written seconds earlier. Both reported
a change forever, from the very first run, and a real change would have
drowned in that noise.

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
from inventory.collect import _mask_secrets
from inventory.installed import installed_plugins
from inventory.report import render_sweep
from inventory.state import diff, load, state_path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "bin", "plugin-inventar")

EMPTY_DIFF = {"added": [], "removed": [], "changed": {}, "identity": {},
              "matchers": {}, "commands": {},
              "findings": {"added": [], "removed": []}, "categories": {}}


class Fake(unittest.TestCase):
    """A throwaway Claude Code configuration, never the user's own."""

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
        self.write(path, ".claude-plugin/plugin.json",
                   {"name": name, "version": version})
        self.write(path, "commands/go.md", "---\nname: go\n---\nB\n")
        self.registry.setdefault(f"{name}@{market}", []).append(
            {"scope": scope, "installPath": path, "version": version})
        return path

    def write(self, root, relpath, content):
        full = os.path.join(root, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(content if isinstance(content, str) else json.dumps(content))

    def commit(self, enabled=None):
        with open(os.path.join(self.config, "plugins",
                               "installed_plugins.json"), "w") as handle:
            json.dump({"version": 2, "plugins": self.registry}, handle)
        with open(os.path.join(self.config, "settings.json"), "w") as handle:
            json.dump({"enabledPlugins": enabled or {}}, handle)

    def run_sweep(self, cwd=None):
        return subprocess.run(
            [sys.executable, TOOL], cwd=cwd, capture_output=True, text=True,
            env=dict(os.environ, CLAUDE_CONFIG_DIR=self.config,
                     XDG_STATE_HOME=self.state), timeout=120)


class EveryInstallationHasItsOwnMemory(Fake):
    """state_key_for drops the version segment so a comparison survives an
    update. Right for one installation, wrong for two."""

    def test_two_scopes_do_not_overwrite_each_other(self):
        self.plugin("doppelt", version="1.0.0", scope="user")
        self.plugin("doppelt", version="2.0.0", scope="project")
        self.plugin("ruhig")
        self.commit()
        first = self.run_sweep()
        self.assertIn("3 Plugins. That is your baseline", first.stdout)
        self.assertNotIn("Changed", first.stdout)
        for _ in range(2):
            later = self.run_sweep()
            self.assertIn("3 unchanged", later.stdout, later.stdout)
            self.assertNotIn("Changed", later.stdout)

    def test_a_real_change_still_shows_with_two_scopes(self):
        """The counter-case: separating them must not make them blind."""
        path = self.plugin("doppelt", version="1.0.0", scope="user")
        self.plugin("doppelt", version="2.0.0", scope="project")
        self.commit()
        self.run_sweep()
        self.write(path, "commands/go.md", "---\nname: go\n---\nGanz anders\n")
        result = self.run_sweep()
        self.assertIn("Changed", result.stdout)
        self.assertIn("1 unchanged", result.stdout)

    def test_two_registry_names_for_one_directory_are_reported(self):
        """Not two installations, one under two names. Counting it twice
        turned the second into a checked, unchanged plugin on the very first
        run -- without ever having something to compare against."""
        path = self.plugin("werkzeug", market="marktA")
        self.registry["werkzeug@marktB"] = [
            {"scope": "user", "installPath": path, "version": "1.0.0"}]
        self.commit()
        result = self.run_sweep()
        self.assertIn("1 Plugin. That is your baseline", result.stdout)
        self.assertIn("duplicate id", result.stdout)
        self.assertIn("marktB", result.stdout)


class ADamagedStateCostsOnePluginNotTheRun(Fake):

    def test_a_malformed_findings_list_is_refused(self):
        """findings became part of the comparison in the ninth round, but
        load() never checked its shape. One damaged file ended the whole
        sweep in a traceback -- the only place this tool crashes at all."""
        for name in ("a", "b", "c"):
            self.plugin(name)
        self.commit()
        self.run_sweep()
        directory = os.path.join(self.state, "plugin-inventar")
        # Any one of the three; the point is that ONE damaged file costs its
        # own plugin the comparison and leaves the other two alone.
        target = sorted(f for f in os.listdir(directory)
                        if f.endswith(".json") and not f.endswith(".1.json"))[0]
        with open(os.path.join(directory, target)) as handle:
            data = json.load(handle)
        data["inventory"]["findings"] = "kaputt"
        with open(os.path.join(directory, target), "w") as handle:
            json.dump(data, handle)
        result = self.run_sweep()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("unerwarteter Aufbau", result.stdout)

    def test_diff_itself_survives_a_malformed_findings_value(self):
        for broken in ("text", {"a": 1}, ["nur text"], 7):
            result = diff({"entries": {}, "findings": broken},
                          {"entries": {}, "findings": []})
            self.assertEqual([], result["findings"]["added"], broken)


class TheSweepDoesNotInventWhatItDidNotSee(Fake):

    def test_an_empty_directory_is_not_a_checked_plugin(self):
        """The single-directory run ends with "There is no plugin here" for
        this. Counting it as checked and unchanged forever is a quiet number
        that reassures without meaning anything."""
        empty = os.path.join(self.config, "leer")
        os.makedirs(empty)
        self.registry["leer@m"] = [{"scope": "user", "installPath": empty}]
        self.commit()
        result = self.run_sweep()
        self.assertNotIn("Newly recorded", result.stdout)
        self.assertIn("leer@m", result.stdout)

    def test_a_relative_install_path_is_refused(self):
        """Resolving it against the working directory would answer
        differently one directory over -- two different plugins under one
        name, depending on where you stood when you typed it."""
        self.registry["rel@m"] = [{"scope": "user", "installPath": "beispiel"}]
        self.commit()
        records, findings = installed_plugins(self.config)
        self.assertEqual([], records)
        self.assertIn("relative-path", [f["code"] for f in findings])

    def test_the_answer_does_not_depend_on_the_working_directory(self):
        self.plugin("fest")
        self.commit()
        first = self.run_sweep(cwd=tempfile.mkdtemp())
        second = self.run_sweep(cwd=tempfile.mkdtemp())
        self.assertIn("1 Plugin. That is your baseline", first.stdout)
        self.assertIn("1 unchanged", second.stdout)


class MaskingHidesSecretsAndNothingElse(unittest.TestCase):

    def test_words_that_were_missing(self):
        for key in ("passphrase", "Authentication", "otp", "dsn",
                    "connectionString", "patToken", "credentials"):
            self.assertEqual({key: "[…]"}, _mask_secrets({key: "GEHEIM"}), key)

    def test_words_that_masked_ordinary_values(self):
        """A report that blacks out the endpoint of the session agent can
        still say THAT it changed, but no longer to what. That is a masking
        which conceals a change instead of a secret."""
        for key in ("apiUrl", "apiVersion", "apiEndpoint", "sessionTimeout",
                    "publicKey", "keyBindings", "signatureAlgorithm",
                    "privateNote", "tokenUrl", "keyId", "secretName"):
            self.assertEqual({key: "wert"}, _mask_secrets({key: "wert"}), key)

    def test_the_ones_that_have_to_stay_masked(self):
        for key in ("password", "api_key", "apiKey", "authToken",
                    "Authorization", "x-secret", "clientSecret", "AUTH"):
            self.assertEqual({key: "[…]"}, _mask_secrets({key: "GEHEIM"}), key)

    def test_the_ones_that_have_to_stay_visible(self):
        for key in ("path", "author", "pathToFile", "authorName", "model",
                    "description", "name"):
            self.assertEqual({key: "wert"}, _mask_secrets({key: "wert"}), key)


class TheClosingLineCountsWhatItLists(unittest.TestCase):

    def _result(self, key, differences, enabled=True):
        return {"key": key, "differences": differences, "enabled": enabled,
                "scope": "user", "note": None}

    def test_the_number_matches_the_names_beside_it(self):
        """It used to say "3 currently disabled" next to two names, and
        "Davon" referred to "N unchanged" one line up, where only some of
        them were."""
        moved = {**EMPTY_DIFF, "changed": {"x": {"a": (1, 2)}}}
        text = render_sweep([
            self._result("aus1@m", moved, enabled=False),
            self._result("aus2@m", EMPTY_DIFF, enabled=False),
            self._result("aus3@m", EMPTY_DIFF, enabled=False),
            self._result("an1@m", EMPTY_DIFF)])
        self.assertIn("2 plugins are currently disabled: aus2@m, aus3@m", text)
        self.assertNotIn("3 currently disabled", text)

    def test_a_failed_save_is_not_called_a_baseline(self):
        """Saying "this is your baseline from now on" while nothing was
        written is the same false reassurance the tool exists against."""
        text = render_sweep([self._result("a@m", None)],
                            unsaved=["[Errno 21] Is a directory"])
        self.assertNotIn("ab jetzt dein baseline", text)
        self.assertIn("saved nothing", text)
        self.assertIn("There is no baseline", text)
        # Plain text, not Markdown -- asterisks would be visible.
        self.assertNotIn("**", text)


class NoOutputPathSkipsEscaping(Fake):

    def test_the_registry_key_on_stderr_is_escaped(self):
        """The last output path in the tool without escaping. The key comes
        from a foreign file, and stderr is what the slash command hands to
        the model."""
        self.registry["\x1b[31mROT\x1b[0m\nZeile2"] = [{"scope": "user"}]
        self.commit()
        result = self.run_sweep()
        self.assertNotIn("\x1b", result.stderr)
        self.assertNotIn("ROT\nZeile2", result.stderr)
        self.assertIn("\\x1b", result.stderr)


class TheFourFixesThatHadNoTest(Fake):
    """Written after a mutation run: four repairs from this round survived
    every existing test. A fix without a test is a fix until someone tidies
    up."""

    def test_a_directory_in_bin_is_not_an_executable(self):
        """`mode & 0o111` is true for a directory (0755), so every
        subdirectory was reported as a file on the PATH -- and a stray
        bin/__pycache__ then broke the self-run criterion."""
        root = self.plugin("b")
        os.makedirs(os.path.join(root, "bin", "unterordner"))
        self.write(root, "bin/echt", "#!/bin/sh\n")
        os.chmod(os.path.join(root, "bin", "echt"), 0o755)
        result = subprocess.run(
            [sys.executable, TOOL, root, "--no-save"],
            capture_output=True, text=True,
            env=dict(os.environ, XDG_STATE_HOME=self.state), timeout=120)
        self.assertIn("1 bin/", result.stdout)
        self.assertNotIn("unterordner", result.stdout.split("Finding")[0])

    def test_an_unusable_state_is_not_listed_as_newly_recorded(self):
        """render() refuses to call an unusable state a first run. The sweep
        used to do it anyway, and then "das ist ab jetzt dein
        baseline" was a lie about a plugin whose state had just been
        rejected."""
        blocked = {"key": "a@m", "differences": None, "enabled": True,
                   "scope": "user", "note": "not valid JSON"}
        text = render_sweep([blocked])
        self.assertIn("No comparison possible", text)
        self.assertNotIn("Newly recorded", text)
        self.assertNotIn("baseline", text)

    def test_the_headline_says_no_comparison_was_possible(self):
        blocked = {"key": "a@m", "differences": None, "enabled": True,
                   "scope": "user", "note": "different schema"}
        self.assertIn("but it could be compared",
                      render_sweep([blocked]))

    def test_the_schema_is_raised_when_compared_fields_are_added(self):
        """The guard exists to stop an avalanche of false alarms. Fields
        that are COMPARED were added after schema 2, so an old state passed
        the guard and produced a pseudo-change for every skill.

        This test is a reminder, not a proof: whoever adds a compared field,
        or changes how an existing one is computed, has to raise SCHEMA and
        the number here.
        """
        from inventory.state import SCHEMA
        self.assertEqual(4, SCHEMA,
                         "compared fields changed? raise SCHEMA and this number")


    def test_an_unreadable_settings_file_is_said_out_loud(self):
        """Swallowing the read error made every disabled plugin quietly
        count as enabled -- a wrong statement about the environment, made
        without a word. The tool may not know the state; it may not pretend
        to."""
        self.plugin("eins")
        self.commit(enabled={"eins@markt": False})
        with open(os.path.join(self.config, "settings.json"), "w") as handle:
            handle.write("{ kaputt")
        records, findings = installed_plugins(self.config)
        self.assertTrue(findings, "no finding about the unreadable settings")
        self.assertIn("enabled-state-unknown",
                      [f.get("detail") for f in findings])
        result = self.run_sweep()
        self.assertIn("active state could not be determined",
                      " ".join(result.stdout.split()))


    def test_a_state_directory_that_cannot_be_written_is_not_a_baseline(self):
        """The whole path, not just the rendering.

        The existing test called render_sweep(unsaved=[…]) directly, so the
        wiring from _save_quietly through to the report never ran once. It
        confirmed a promise whose trigger was never pulled.
        """
        self.plugin("eins")
        self.commit()
        blocked = os.path.join(self.state, "plugin-inventar")
        os.makedirs(blocked)
        os.chmod(blocked, 0o500)
        self.addCleanup(os.chmod, blocked, 0o755)
        result = self.run_sweep()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("saved nothing", result.stdout)
        self.assertNotIn("ab jetzt dein baseline", result.stdout)


if __name__ == "__main__":
    unittest.main()
