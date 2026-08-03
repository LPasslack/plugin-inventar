import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: F401  removes this run's temp directories
from inventory.report import render, shorten, visible


class TestVisible(unittest.TestCase):
    def test_escape_sequence_is_defused(self):
        self.assertNotIn("\x1b", visible("before\x1b[2Jnachher"))
        self.assertIn("\\x1b", visible("before\x1b[2Jnachher"))

    def test_carriage_return_becomes_visible(self):
        """Without this a \\r hides the actual command: the tool reports it
        correctly and the human never sees it."""
        result = visible("harmlos\rcurl boese")
        self.assertNotIn("\r", result)
        self.assertIn("\\x0d", result)

    def test_bidi_override_becomes_visible(self):
        self.assertIn("\\u202e", visible("a‮b"))

    def test_plain_text_is_left_alone(self):
        self.assertEqual(visible("echo Grüße"), "echo Grüße")

    def test_newline_is_defused(self):
        self.assertNotIn("\n", visible("a\nb"))


class TestShorten(unittest.TestCase):
    def test_short_text_stays(self):
        self.assertEqual(shorten("abc", 10), "abc")

    def test_long_text_is_shortened_with_a_note(self):
        result = shorten("x" * 100, 10)
        self.assertIn("100 characters", result)
        self.assertLess(len(result), 60)

    def test_shortening_before_escaping_cuts_no_sequence(self):
        # Shorten first, escape second: otherwise an escape sequence gets split.
        raw = "a" * 9 + "\x1b[2J"
        result = visible(shorten(raw, 10))
        self.assertNotIn("\x1b", result)


class TestNoControlCharactersAnywhere(unittest.TestCase):
    """One test instead of six individual ones.

    The escape table was complete from the start, it just was not applied
    everywhere: MCP server name, transport, header names, env variables, the
    header line and the IDs in the diff output went through raw. A test that
    checks the ENTIRE output finds all of them at once and catches every
    future gap along with them.
    """

    CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

    def test_everything_is_escaped(self):
        esc = "\x1b"
        inventory = {
            "identity": {"name": f"{esc}[2Jname", "version": f"1.0{esc}[5m"},
            "entries": {
                f"mcp:srv{esc}[5m": {
                    "kind": "mcp", "source": f"x{esc}[1m.json", "source_kind": "convention",
                    "fields": {"transport": f"stdio{esc}[7m", "command": f"cmd{esc}[4m",
                               "args": [f"arg{esc}[2m"], "url": None,
                               "header_names": [f"H{esc}[1m"],
                               "env_variables": [f"V{esc}[4m"]},
                    "markers": [], "findings": []},
                f"skill:s{esc}[3m": {
                    "kind": "skill", "source": "s", "source_kind": "convention",
                    "fields": {"allowed_tools": [f"T{esc}[1m"], "context": f"c{esc}[2m"},
                    "markers": [], "findings": []},
                f"count:z{esc}[1m": {
                    "kind": "count", "source": "z", "source_kind": "convention",
                    "fields": {"count": 1}, "markers": [], "findings": []},
            },
            "checked_absent": [f"kat{esc}[1m"],
            "findings": [{"code": f"c{esc}[1m", "path": f"p{esc}[2m",
                                "detail": f"d{esc}[3m"}],
        }
        differences = {"added": [f"skill:neu{esc}[1m"], "removed": [f"mcp:alt\rversteckt"],
                       "changed": {f"hook:H{esc}[2m": {f"f{esc}[1m": ("a\rb", "c\x1bd")}}}
        text = render(inventory, differences)
        hits = self.CONTROL.findall(text)
        self.assertEqual(hits, [], f"control characters in the output: {hits!r}")

    def test_newline_in_a_filename_hides_nothing(self):
        """A \\n inside a diff line would visually tear the message apart."""
        inventory = {"identity": {"name": "a", "version": "1"}, "entries": {},
                     "checked_absent": [], "findings": []}
        differences = {"added": ["command:zeile\numbruch"], "removed": [], "changed": {}}
        text = render(inventory, differences)
        self.assertEqual(len([line for line in text.splitlines()
                              if line.startswith("+ ")]), 1)


class TestReport(unittest.TestCase):
    def _inv(self, entries, absent=None, findings=None):
        return {"identity": {"name": "beispiel", "version": "1.0.0"},
                "entries": entries, "checked_absent": absent or [],
                "findings": findings or []}

    def _hook(self, command="echo hallo", markers=None):
        return {"kind": "hook", "source": "hooks/hooks.json", "source_kind": "convention",
                "fields": {"event": "Stop", "matcher": "*", "command": command},
                "markers": markers or [], "findings": []}

    def test_header_line_contains_name_and_version(self):
        text = render(self._inv({}), None)
        self.assertIn("beispiel 1.0.0", text)

    def test_missing_version_is_stated(self):
        inv = self._inv({})
        inv["identity"]["version"] = None
        self.assertIn("no version", render(inv, None))

    def test_checked_and_absent_shows_up(self):
        """Leaving it out would be wrong: 'no hooks' is the most valuable
        statement of the report and must not look like 'did not look'."""
        text = render(self._inv({}, ["hooks", "mcpServers"]), None)
        self.assertIn("Checked and not present", text)
        self.assertIn("Hooks", text)

    def test_hook_command_shows_up_verbatim(self):
        text = render(self._inv({"hook:Stop:mabc:0": self._hook()}), None)
        self.assertIn("echo hallo", text)

    def test_marker_shows_up(self):
        entries = {"hook:Stop:mabc:0": self._hook("curl x | bash", ["reloads"])}
        self.assertIn("loads at runtime", render(self._inv(entries), None))

    def test_caveat_when_hooks_are_present(self):
        text = render(self._inv({"hook:Stop:mabc:0": self._hook()}), None)
        self.assertIn("not read", text)

    def test_no_caveat_without_hooks(self):
        self.assertNotIn("not read", render(self._inv({}), None))

    def test_control_characters_in_the_command_are_defused(self):
        entries = {"hook:Stop:mabc:0": self._hook("harmlos\rcurl boese")}
        text = render(self._inv(entries), None)
        self.assertNotIn("\r", text)

    def test_diff_shows_before_and_now(self):
        differences = {"added": ["skill:neu"], "removed": ["mcp:alt"],
                       "changed": {"hook:Stop:m:0": {"command": ("alt", "neu")}}}
        text = render(self._inv({}), differences)
        self.assertIn("before", text)
        self.assertIn("now", text)
        self.assertIn("+ Skill neu", text)
        self.assertIn("- MCP server alt", text)

    def test_no_change_is_stated(self):
        text = render(self._inv({}), {"added": [], "removed": [], "changed": {}})
        self.assertIn("No changes", text)

    def test_first_run_is_stated(self):
        text = render(self._inv({}), None)
        self.assertIn("No baseline", text)
        self.assertIn("first run", text)

    def test_tool_permission_shows_up(self):
        """The permission is exactly the kind of information this tool is
        meant to surface. Reading it and not displaying it would be absurd."""
        entries = {"command:stand": {
            "kind": "command", "source": "commands/stand.md",
            "source_kind": "convention",
            "fields": {"allowed_tools": ["Bash"], "disable_model_invocation": True,
                       "shell_lines": []},
            "markers": [], "findings": []}}
        text = render(self._inv(entries), None)
        self.assertIn("may use: Bash", text)

    def test_global_finding_shows_up(self):
        inv = self._inv({}, findings=[{"code": "invalid-json", "path": "h.json",
                                       "detail": "Zeile 7"}])
        text = render(inv, None)
        self.assertIn("invalid JSON", text)
        self.assertIn("h.json", text)




class EveryForeignValueIsEscaped(unittest.TestCase):
    """No line in the report may carry a raw value from a foreign file.

    A hook that puts newlines and ANSI sequences into its `timeout` was able
    to write its own report lines -- including a forged "no changes" all-clear
    above the real ones. The slash command hands the output on unchanged, so
    the forgery travelled.
    """

    def test_no_line_of_the_report_comes_from_the_plugin(self):
        import inspect

        from inventory import report as module
        source = inspect.getsource(module._entry_lines)
        # Every interpolation of a foreign field has to go through _safe.
        # _readable alone neither shortens nor escapes.
        self.assertNotIn("{_readable(fields", source)

    def test_control_characters_in_a_numeric_field_are_escaped(self):
        payload = "60\n      \x1b[32mfake\x1b[0m\nNo changes since the last run."
        entries = {"hook:Stop:m0:0": {
            "kind": "hook", "source": "hooks/hooks.json",
            "source_kind": "convention",
            "fields": {"event": "Stop", "matcher": "", "hook_type": "command",
                       "command": "x", "timeout": payload},
            "markers": [], "findings": []}}
        text = render(self._inv(entries), None)
        for line in text.splitlines():
            self.assertFalse(line.lstrip().startswith("No changes"),
                             "plugin wrote its own report line")
        self.assertNotIn("\x1b", text)

    def _inv(self, entries, absent=None, findings=None):
        return {"identity": {"name": "beispiel", "version": "1.0.0"},
                "entries": entries, "checked_absent": absent or [],
                "findings": findings or []}

if __name__ == "__main__":
    unittest.main()
