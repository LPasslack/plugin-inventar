"""Tests for code paths that ran zero times in the whole suite.

A reviewer measured line coverage across 145 mutations and found that several
of the most consequential paths were never executed at all — among them the
masking of secrets, which is the one place where a silent failure prints a
credential instead of reporting anything.

A branch nobody executes is not "probably fine". It is a branch whose failure
mode is unknown, in a tool whose entire point is that nothing stays unseen.

German string literals stand in for foreign plugin content or are the
report's own wording; the code around them is English, as everywhere else.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: F401  removes this run's temp directories
from inventory.collect import (_looks_secret, _mask_secrets, _url_without_secret,
                               build_inventory, collect_directories, collect_hooks,
                               collect_mcp, collect_settings, resolve_paths)
from inventory.frontmatter import body_of, read_frontmatter
from inventory.reading import file_digest, read_safely
from inventory.report import _safe, render, visible
from inventory.state import load, save, state_path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "bin", "plugin-inventar")


class Temp(unittest.TestCase):
    """Base class that cleans up after itself.

    56 bare mkdtemp calls left roughly 620 directories in $TMPDIR per run.
    """

    def temp(self):
        path = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        return path

    def write(self, root, relpath, content):
        full = os.path.join(root, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(content if isinstance(content, str) else json.dumps(content))
        return full

    def plugin(self, name="p", **manifest):
        root = self.temp()
        self.write(root, ".claude-plugin/plugin.json",
                   dict({"name": name, "version": "1"}, **manifest))
        return root

    def paths(self, root):
        return resolve_paths(root, {})[0]


class SecretsAreMasked(Temp):
    """The dict, list and shape branches of _mask_secrets never ran.

    Four separate mutations survived the whole suite. Each of them means a
    token from a foreign plugin reaches the report AND the state file in the
    clear — beside a line that explicitly withholds header values.
    """

    def test_an_agent_object_in_settings_is_masked(self):
        root = self.temp()
        self.write(root, "settings.json", {"agent": {
            "model": "opus", "apiKey": "sk-geheim123",
            "auth": {"token": "t-geheim"},
            "endpoint": "https://h/x?k=geheim123"}})
        entries, _ = collect_settings(root, self.paths(root))
        raw = json.dumps(entries)
        for secret in ("sk-geheim123", "t-geheim", "k=geheim123"):
            self.assertNotIn(secret, raw)
        # The model is not a secret and stays readable, otherwise the entry
        # says nothing at all.
        self.assertEqual("opus", entries["settings:agent"]["fields"]["value"]["model"])

    def test_a_token_under_a_harmless_key_is_caught_by_its_shape(self):
        """The key name gives nothing away, the value does."""
        self.assertEqual({"wert": "[…]"}, _mask_secrets({"wert": "ghp_abcdefghijklmnop"}))
        self.assertEqual(["[…]"], _mask_secrets(["xoxb-1-2-3"]))
        self.assertEqual({"a": {"b": "[…]"}}, _mask_secrets({"a": {"b": "AKIAIOSFODNN7"}}))

    def test_a_secret_in_a_flag_value_is_caught(self):
        self.assertTrue(_looks_secret("--key=sk-abc"))
        self.assertFalse(_looks_secret("--verbose"))

    def test_masking_does_not_recurse_without_bound(self):
        """The depth limit guards against a crash, not against a leak.

        A plugin can nest its settings deeper than Python's recursion limit.
        Without the bound the tool dies with a RecursionError in the middle
        of reading a foreign file.
        """
        deep = current = {}
        for _ in range(3000):
            current["a"] = {}
            current = current["a"]
        current["password"] = "geheim"
        masked = _mask_secrets(deep)
        # Careful: this passes because the depth limit returns "[…]" long
        # before the key name is ever looked at. It is a depth test, not
        # evidence that the name list works -- that is tested separately.
        self.assertNotIn("geheim", json.dumps(masked))
        # And it stops early rather than reproducing the whole structure.
        self.assertEqual("[…]", masked["a"]["a"]["a"]["a"]["a"]["a"]["a"])

    def test_an_unknown_hook_type_does_not_leak_its_payload(self):
        """An unknown type has no known payload key, so the whole rest of the
        object is folded into the command line -- masked."""
        root = self.plugin()
        self.write(root, "hooks/hooks.json", {"hooks": {"Stop": [{"hooks": [
            {"type": "websocket", "exec": "boese.sh", "apiKey": "sk-geheim123"}]}]}})
        self.assertNotIn("sk-geheim123", json.dumps(build_inventory(root)))

    def test_a_url_that_is_not_a_string_does_not_bypass_masking(self):
        self.assertNotIn("geheim", str(_url_without_secret({"u": "sk-geheim"})))

    def test_credentials_without_a_scheme_are_masked(self):
        self.assertNotIn("pw123", _url_without_secret("nutzer:pw123@h/mcp"))

    def test_an_at_sign_inside_the_password_does_not_confuse_the_split(self):
        self.assertNotIn("pw@123", _url_without_secret("https://nutzer:pw@123@h/mcp"))


class HooksSurviveAndStayMasked(Temp):
    """Four ways a hook disappeared or leaked, none of them covered."""

    def test_a_hook_of_an_unknown_type_stays_in_the_inventory(self):
        """The comment says it outright: a tool that promises visibility must
        not go silent. The existing test only checked for the finding."""
        root = self.plugin()
        self.write(root, "hooks/hooks.json", {"hooks": {"Stop": [{"matcher": "*", "hooks": [
            {"type": "websocket", "url": "wss://h/y?token=geheim123"}]}]}})
        entries, findings = collect_hooks(root, self.paths(root))
        self.assertEqual(1, len(entries), "hook of an unknown type disappeared")
        entry = list(entries.values())[0]
        self.assertEqual("unknown:websocket", entry["fields"]["hook_type"])
        self.assertNotIn("geheim123", json.dumps(entry))
        self.assertIn("unknown-hook-type", [f["code"] for f in findings])

    def test_the_type_is_matched_regardless_of_case_and_spacing(self):
        root = self.plugin()
        self.write(root, "hooks/hooks.json", {"hooks": {"Stop": [{"matcher": "*", "hooks": [
            {"type": " Command ", "command": "x"}]}]}})
        entries, findings = collect_hooks(root, self.paths(root))
        self.assertEqual("command", list(entries.values())[0]["fields"]["hook_type"])
        self.assertEqual([], findings)

    def test_two_identical_commands_both_survive(self):
        """Same event, same matcher, same command: the ID is built from the
        command hash, so both would land on the same key."""
        root = self.plugin()
        self.write(root, "hooks/hooks.json", {"hooks": {"Stop": [{"matcher": "*", "hooks": [
            {"type": "command", "command": "gleich"},
            {"type": "command", "command": "gleich"}]}]}})
        entries, _ = collect_hooks(root, self.paths(root))
        self.assertEqual(2, len(entries), "identical twin hook was overwritten")

    def test_a_token_in_an_http_hook_url_is_masked(self):
        root = self.plugin()
        self.write(root, "hooks/hooks.json", {"hooks": {"Stop": [{"matcher": "*", "hooks": [
            {"type": "http", "url": "https://h/hook?token=geheim123"}]}]}})
        entries, _ = collect_hooks(root, self.paths(root))
        self.assertNotIn("geheim123", json.dumps(entries))

    def test_three_components_of_the_same_name_all_survive(self):
        """The collision counter only ever ran up to #2."""
        root = self.plugin(skills=["a", "b", "c"])
        for base in ("a", "b", "c"):
            self.write(root, f"{base}/gleich/SKILL.md", "---\nname: gleich\n---\nB\n")
        skills = [e for e in build_inventory(root)["entries"].values()
                  if e["kind"] == "skill"]
        self.assertEqual(3, len(skills))


class StateIsWrittenSafely(Temp):
    """save() had six surviving mutations. Its docstring names three classic
    pitfalls; none of them was checked."""

    def setUp(self):
        directory = self.temp()
        patcher = unittest.mock.patch.dict(os.environ,
                                           {"XDG_STATE_HOME": directory})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _state(self, entries):
        return {"meta": {"schema": 2}, "inventory": {"identity": {}, "entries": entries,
                                                     "checked_absent": [], "findings": []}}

    def test_a_failure_during_the_write_leaves_the_old_state_intact(self):
        save("a@b", self._state({"alt:1": {"fields": {}}}))
        with open(state_path("a@b"), encoding="utf-8") as handle:
            before = handle.read()
        with unittest.mock.patch("os.replace", side_effect=OSError("voll")):
            with self.assertRaises(OSError):
                save("a@b", self._state({"neu:1": {"fields": {}}}))
        with open(state_path("a@b"), encoding="utf-8") as handle:
            self.assertEqual(before, handle.read())
        directory = os.path.dirname(state_path("a@b"))
        self.assertEqual([], [f for f in os.listdir(directory)
                              if f.startswith(".tmp-")])

    def test_the_temporary_file_lives_next_to_its_target(self):
        """Across a filesystem boundary os.replace fails, and it fails at the
        end of the run, after the report was already printed."""
        seen = []
        real = tempfile.mkstemp

        def spy(*args, **kwargs):
            seen.append(kwargs.get("dir"))
            return real(*args, **kwargs)

        with unittest.mock.patch("tempfile.mkstemp", spy):
            save("a@b", self._state({}))
        self.assertEqual([os.path.dirname(state_path("a@b"))], seen)

    def test_the_same_content_produces_the_same_bytes(self):
        """Insertion order must not reach the file.

        A hash seed no longer shuffles dicts in Python 3.7+, so testing via
        PYTHONHASHSEED proves nothing. What sort_keys actually promises is
        this: the same set of entries, inserted in a different order, has to
        come out byte for byte identical -- otherwise the next run diffs a
        file against itself.
        """
        first = {"a:1": {"fields": {"x": 1, "y": 2}},
                 "b:2": {"fields": {"m": 3, "n": 4}}}
        second = {"b:2": {"fields": {"n": 4, "m": 3}},
                  "a:1": {"fields": {"y": 2, "x": 1}}}
        save("a@b", self._state(first))
        with open(state_path("a@b"), "rb") as handle:
            before = handle.read()
        save("a@b", self._state(second))
        with open(state_path("a@b"), "rb") as handle:
            self.assertEqual(before, handle.read())


class UnusableStateIsRefused(Temp):
    """Three of the five shape checks in load() never ran. Their purpose is
    to keep diff() from ending in a traceback, and that was unproven."""

    def setUp(self):
        directory = self.temp()
        patcher = unittest.mock.patch.dict(os.environ,
                                           {"XDG_STATE_HOME": directory})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_shapes_that_would_crash_the_comparison_are_refused(self):
        os.makedirs(os.path.dirname(state_path("a@b")), exist_ok=True)
        for content in (
                '{"meta":{"schema":2},"inventory":{"entries":{"a":1}}}',
                '{"meta":{"schema":2},"inventory":{"entries":{"a":{"fields":[]}}}}',
                '{"meta":{"schema":2},"inventory":{"entries":{},"identity":[]}}'):
            with open(state_path("a@b"), "w", encoding="utf-8") as handle:
                handle.write(content)
            data, reason = load("a@b")
            self.assertIsNone(data, content)
            self.assertEqual("unerwarteter Aufbau", reason)


class InvisibleCharactersAreMadeVisible(Temp):
    """The second pass in visible() -- the one using unicodedata.category --
    ran zero times. Its docstring exists precisely because a positive list
    always misses something."""

    def test_a_zero_width_space_inside_a_word(self):
        # "cu<zwsp>rl" reads like curl and is not.
        self.assertEqual("cu\\u200brl", visible("cu​rl"))

    def test_tag_characters(self):
        self.assertIn("\\U000e0041", visible("a\U000e0041b"))

    def test_a_variation_selector_and_a_hard_space(self):
        self.assertNotIn("️", visible("x️"))
        self.assertNotIn(" ", visible("a b"))

    def test_ordinary_diacritics_survive(self):
        self.assertEqual("Grüße naïve", visible("Grüße naïve"))

    def test_shortening_happens_before_escaping(self):
        """The existing test called the right order itself and then checked
        only for a raw escape byte, which both orders pass. This checks the
        one thing that differs: a cut through an escape sequence."""
        raw = "a" * 498 + "\x1b[2J" + "b" * 50
        result = _safe(raw)
        self.assertNotIn("\x1b", result)
        self.assertIn("\\x1b", result)
        self.assertNotRegex(result, r"\\(x[0-9a-f]?|u[0-9a-f]{0,3})…")


class BinIsReportedFully(Temp):
    """bin/ lands on the PATH of the Bash tool. Three mutations survived."""

    def test_a_symlink_out_of_the_plugin_is_a_finding(self):
        outside = self.temp()
        with open(os.path.join(outside, "fremd"), "w") as handle:
            handle.write("#!/bin/sh\n")
        root = self.plugin()
        os.makedirs(os.path.join(root, "bin"))
        os.symlink(os.path.join(outside, "fremd"), os.path.join(root, "bin", "x"))
        entries, findings = collect_directories(root, self.paths(root))
        self.assertIn("symlink-outside",
                      [f["code"] for f in findings if f.get("category") == "bin"])
        self.assertIsNone(entries["bin:x"]["fields"]["content_hash"])

    def test_a_non_executable_entry_is_marked_as_such(self):
        root = self.plugin()
        self.write(root, "bin/liesmich", "nur Text\n")
        os.chmod(os.path.join(root, "bin", "liesmich"), 0o644)
        entries, _ = collect_directories(root, self.paths(root))
        self.assertIs(False, entries["bin:liesmich"]["fields"]["executable"])
        self.assertIn("(not executable)", render(
            {"identity": {}, "entries": entries, "checked_absent": [],
             "findings": []}, None))

    def test_the_mode_comes_from_the_link_not_from_its_target(self):
        root = self.plugin()
        target = self.write(root, "ziel.sh", "#!/bin/sh\n")
        os.chmod(target, 0o755)
        os.makedirs(os.path.join(root, "bin"))
        os.symlink("../ziel.sh", os.path.join(root, "bin", "verweis"))
        entries, _ = collect_directories(root, self.paths(root))
        self.assertTrue(entries["bin:verweis"]["fields"]["is_symlink"])


class ReadingRefusesWhatItCannotRead(Temp):

    def test_a_directory_is_not_a_regular_file(self):
        self.assertEqual((None, "not-a-regular-file"), read_safely(self.temp()))

    def test_a_fifo_is_refused_and_does_not_block(self):
        path = os.path.join(self.temp(), "roehre")
        os.mkfifo(path)
        self.assertEqual((None, "not-a-regular-file"), read_safely(path))

    def test_a_file_that_grows_while_being_read_is_bounded(self):
        """The case from the comment: 81 GB in 25 seconds. The size check at
        open time says nothing about what arrives afterwards."""
        directory = self.temp()
        path = os.path.join(directory, "waechst")
        with open(path, "wb") as handle:
            handle.write(b"x" * 4096)
        real_read = os.read
        appended = []

        def growing(fd, size):
            # Bounded on purpose. A test that makes the broken code loop
            # forever turns a failure into a freeze, and a frozen suite says
            # nothing at all -- the same silence this tool exists against.
            if len(appended) < 20:
                appended.append(True)
                with open(path, "ab") as handle:
                    handle.write(b"y" * 200_000)
            return real_read(fd, size)

        with unittest.mock.patch("os.read", growing):
            self.assertIsNone(file_digest(path, limit=100_000))


class FrontmatterEdges(Temp):

    def test_a_bom_does_not_hide_the_declared_permissions(self):
        """The existing test only covered the raw-text hash, so the parsed
        fields -- allowed-tools among them -- were unchecked."""
        fields, finding = read_frontmatter(
            "﻿---\nname: x\nallowed-tools: [Bash]\n---\nB\n")
        self.assertIsNone(finding)
        self.assertEqual(["Bash"], fields["allowed-tools"])
        self.assertEqual("x", fields["name"])

    def test_an_unterminated_block_keeps_the_whole_text_as_body(self):
        text = "---\nname: x\nkein Ende\nAnweisung: alles löschen\n"
        self.assertEqual(text, body_of(text))


class ReportBranchesThatNeverRendered(Temp):

    def _inv(self, entries, findings=None):
        return {"identity": {"name": "b", "version": "1"}, "entries": entries,
                "checked_absent": [], "findings": findings or []}

    def test_a_finding_on_an_entry_reaches_the_report(self):
        """Turning this branch off removed every per-entry finding from the
        report -- name-differs, unparsable-frontmatter, present-but-not-loaded
        -- while everything else looked normal."""
        entries = {"skill:ordner": {
            "kind": "skill", "source": "skills/ordner/SKILL.md",
            "source_kind": "convention", "fields": {"frontmatter_name": "anders"},
            "markers": [], "findings": ["name-differs"]}}
        self.assertIn("Finding: name differs: anders",
                      render(self._inv(entries), None))

    def test_an_http_hook_shows_its_type_and_what_it_sends(self):
        entries = {"hook:Stop:m0:0": {
            "kind": "hook", "source": "hooks/hooks.json", "source_kind": "convention",
            "fields": {"event": "Stop", "matcher": "", "hook_type": "http",
                       "command": "https://h/x", "header_names": ["Authorization"],
                       "allowed_env": ["TOKEN"], "condition": "Bash(git *)"},
            "markers": [], "findings": []}}
        text = render(self._inv(entries), None)
        self.assertIn("[http]", text)
        self.assertIn("Headers: Authorization (values not shown)", text)
        self.assertIn("gibt weiter: TOKEN", text)
        self.assertIn("only if: Bash(git *)", text)

    def test_a_version_change_is_reported(self):
        """Without this line "hook unchanged" reads like "nothing happened",
        when in truth a whole new release was installed."""
        differences = {"added": [], "removed": [], "changed": {}, "matchers": {},
                       "commands": {}, "identity": {"version": ("1.0.0", "2.0.0")}}
        self.assertIn("~ Version  before: 1.0.0  now: 2.0.0",
                      render(self._inv({}), differences).splitlines())

    def test_the_mcp_extras_are_shown(self):
        entries = {"mcp:s": {
            "kind": "mcp", "source": ".mcp.json", "source_kind": "convention",
            "fields": {"transport": "http", "url": "https://h/x", "args": [],
                       "always_load": True, "uses_oauth": True,
                       "uses_headers_helper": True, "env_variables": ["TOKEN"]},
            "markers": [], "findings": []}}
        text = render(self._inv(entries), None)
        for expected in ("always loaded", "OAuth", "headers from external command",
                         "expects: TOKEN"):
            self.assertIn(expected, text)

    def test_a_file_that_looks_like_a_component_but_is_not(self):
        entries = {"file:hooks/hooks-cursor.json": {
            "kind": "unused_file", "source": "hooks/hooks-cursor.json",
            "source_kind": "convention", "fields": {},
            "markers": [], "findings": ["present-but-not-loaded"]}}
        text = render(self._inv(entries), None)
        self.assertIn("Files not loaded", text)
        self.assertIn("hooks-cursor.json", text)


class MalformedInputIsReported(Temp):
    """Read-error branches in the MCP and settings collectors never ran."""

    def test_an_mcp_file_that_is_not_an_object(self):
        root = self.plugin()
        self.write(root, ".mcp.json", "[1, 2, 3]")
        _, findings = collect_mcp(root, self.paths(root))
        self.assertTrue(findings)

    def test_a_settings_file_that_is_a_list(self):
        root = self.plugin()
        self.write(root, "settings.json", "[1, 2, 3]")
        _, findings = collect_settings(root, self.paths(root))
        self.assertTrue(findings)

    def test_a_hook_group_that_is_a_number(self):
        root = self.plugin()
        self.write(root, "hooks/hooks.json", {"hooks": {"Stop": [7]}})
        _, findings = collect_hooks(root, self.paths(root))
        self.assertIn("unexpected-type", [f["code"] for f in findings])
        self.assertIn("hooks", [f.get("category") for f in findings])

    def test_a_broken_manifest_produces_a_finding(self):
        root = self.temp()
        self.write(root, ".claude-plugin/plugin.json", "{ kaputt")
        self.write(root, "commands/go.md", "---\nname: go\n---\nB\n")
        self.assertIn("invalid-json",
                      [f["code"] for f in build_inventory(root)["findings"]])

    def test_a_count_category_that_is_a_file(self):
        """`.lsp.json` is a file by convention; no test ever created one."""
        root = self.plugin()
        self.write(root, ".lsp.json", {"lsp": {"py": {"command": "pylsp"}}})
        self.assertIn("count:lsp", build_inventory(root)["entries"])


class TheCacheRuleIsActuallyUsed(Temp):
    """The existing key tests passed with the cache rule switched off.

    Without it both paths fall through to the local branch, which strips a
    version-like last directory -- so "survives a version bump" still held
    and "two caches stay apart" still held. The one thing that broke was
    invisible: the SHAPE of the key. This checks that.
    """

    def cli(self):
        import importlib.util
        from importlib.machinery import SourceFileLoader
        previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
        loader = SourceFileLoader("plugin_inventar_cli_gaps", TOOL)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        try:
            loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = previous
        return module

    def test_the_cache_layout_produces_a_readable_key(self):
        base = self.temp()
        keys = []
        for version in ("1.2.3", "2.0.0"):
            path = os.path.join(base, "cache", "markt", "plug", version)
            os.makedirs(path)
            keys.append(self.cli().state_key_for(path))
        self.assertEqual(keys[0], keys[1])
        self.assertTrue(keys[0].startswith("plug@markt-"), keys[0])

    def test_a_local_directory_gets_a_local_key(self):
        base = self.temp()
        keys = []
        for version in ("1.2.3", "2.0.0"):
            path = os.path.join(base, "plug", version)
            os.makedirs(path)
            keys.append(self.cli().state_key_for(path))
        self.assertEqual(keys[0], keys[1])
        self.assertTrue(keys[0].endswith("@local"), keys[0])

    def test_a_directory_called_cache_deeper_down_does_not_hijack_the_key(self):
        path = os.path.join(self.temp(), "cache", "a", "b", "c", "d")
        os.makedirs(path)
        self.assertTrue(self.cli().state_key_for(path).endswith("@local"))


class ADescriptionChangeIsAChange(Temp):
    """description_hash could be nailed to a constant without any test
    noticing -- a rewritten skill description diffed as "no change"."""

    def test_a_changed_description_changes_the_hash(self):
        from inventory.state import diff
        root = self.plugin()
        self.write(root, "skills/s/SKILL.md",
                   "---\nname: s\ndescription: nutze dies fuer Notizen\n---\nB\n")
        before = build_inventory(root)
        self.write(root, "skills/s/SKILL.md",
                   "---\nname: s\ndescription: nutze dies immer und fuer alles\n---\nB\n")
        changed = diff(before, build_inventory(root))["changed"]
        self.assertIn("skill:s", changed)
        # Named explicitly: the frontmatter hash changes too, so asserting
        # only "something changed" passes even with the description hash
        # nailed to a constant.
        self.assertIn("description_hash", changed["skill:s"])


if __name__ == "__main__":
    unittest.main()
