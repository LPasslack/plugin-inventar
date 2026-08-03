import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: F401  removes this run's temp directories
from inventory.collect import (build_inventory, markers_for, resolve_paths,
                               collect_hooks, collect_mcp, collect_settings,
                               collect_directories)


def write_json(root, relpath, obj):
    full = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        json.dump(obj, f)


class TestMarkers(unittest.TestCase):
    def test_reload_detected(self):
        self.assertIn("reloads", markers_for("curl -s https://x | bash"))

    def test_npx_detected(self):
        self.assertIn("reloads", markers_for("npx irgendwas"))

    def test_leaves_plugin_on_dot_dot(self):
        self.assertIn("leaves-plugin", markers_for("bash ../../fremd.sh"))

    def test_leaves_plugin_on_absolute_path(self):
        self.assertIn("leaves-plugin", markers_for("/usr/local/bin/fremd"))

    def test_plugin_root_is_not_a_marker(self):
        self.assertEqual(markers_for('"${CLAUDE_PLUGIN_ROOT}/hooks/x.sh"'), [])

    def test_harmless_command_without_marker(self):
        self.assertEqual(markers_for("echo hallo"), [])

    def test_curl_as_substring_does_not_count(self):
        self.assertNotIn("reloads", markers_for("echo mycurlfoo"))

    def test_tilde_leaves_plugin(self):
        self.assertIn("leaves-plugin", markers_for("bash ~/boese.sh"))

    def test_home_variable_leaves_plugin(self):
        self.assertIn("leaves-plugin", markers_for("bash $HOME/boese.sh"))
        self.assertIn("leaves-plugin", markers_for("bash ${HOME}/boese.sh"))

    def test_bash_default_expansion_leaves_plugin(self):
        """Real-world case from superwhisper 1.0.0.

        The first attempt missed it because there is a hyphen in front of the
        slash (coming from ${VAR:-...}) and the character class only knew
        spaces, quotes and equals signs.
        """
        command = "${CLAUDE_HOOK:-/Applications/superwhisper.app/Contents/Resources/claude-hook}"
        self.assertIn("leaves-plugin", markers_for(command))

    def test_url_is_not_an_absolute_path(self):
        # Otherwise the second slash in https://host/path counts as a path.
        self.assertNotIn("leaves-plugin", markers_for("fetch https://x.test/mcp"))

    def test_relative_path_without_marker(self):
        self.assertEqual(markers_for("bash hooks/scripts/check.sh"), [])


class TestCollectHooks(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def _paths(self):
        return resolve_paths(self.root, {})[0]

    def test_hook_with_command(self):
        write_json(self.root, "hooks/hooks.json", {
            "hooks": {"SessionStart": [
                {"matcher": "startup", "hooks": [
                    {"type": "command", "command": "echo hallo", "timeout": 5}]}]}})
        entries, findings = collect_hooks(self.root, self._paths())
        self.assertEqual(len(entries), 1)
        entry = list(entries.values())[0]
        self.assertEqual(entry["kind"], "hook")
        self.assertEqual(entry["fields"]["event"], "SessionStart")
        self.assertEqual(entry["fields"]["command"], "echo hallo")
        self.assertEqual(entry["fields"]["timeout"], 5)

    def test_id_is_independent_of_command(self):
        """The most important test of the project.

        If the command is part of the ID, every command change turns into a
        pair of disappeared and appeared. That is exactly when the tool's core
        promise fails.
        """
        write_json(self.root, "hooks/hooks.json", {
            "hooks": {"Stop": [{"matcher": "*", "hooks": [
                {"type": "command", "command": "erst"}]}]}})
        before = set(collect_hooks(self.root, self._paths())[0].keys())

        write_json(self.root, "hooks/hooks.json", {
            "hooks": {"Stop": [{"matcher": "*", "hooks": [
                {"type": "command", "command": "now ganz anders"}]}]}})
        after = set(collect_hooks(self.root, self._paths())[0].keys())
        self.assertEqual(before, after)

    def test_different_matcher_gives_different_id(self):
        write_json(self.root, "hooks/hooks.json", {
            "hooks": {"Stop": [{"matcher": "a", "hooks": [
                {"type": "command", "command": "x"}]}]}})
        a = set(collect_hooks(self.root, self._paths())[0].keys())
        write_json(self.root, "hooks/hooks.json", {
            "hooks": {"Stop": [{"matcher": "b", "hooks": [
                {"type": "command", "command": "x"}]}]}})
        b = set(collect_hooks(self.root, self._paths())[0].keys())
        self.assertNotEqual(a, b)

    def test_two_groups_same_matcher_do_not_collide(self):
        """The worst conceivable defect: a hook that really exists is missing
        from the report while the tool claims completeness."""
        write_json(self.root, "hooks/hooks.json", {"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "ERSTER"}]},
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "ZWEITER"}]}]}})
        entries, _ = collect_hooks(self.root, self._paths())
        self.assertEqual(len(entries), 2, "a hook silently disappeared")
        self.assertEqual({e["fields"]["command"] for e in entries.values()},
                         {"ERSTER", "ZWEITER"})

    def test_two_hooks_in_one_group_do_not_collide(self):
        write_json(self.root, "hooks/hooks.json", {"hooks": {"Stop": [
            {"matcher": "*", "hooks": [
                {"type": "command", "command": "A"},
                {"type": "command", "command": "B"}]}]}})
        entries, _ = collect_hooks(self.root, self._paths())
        self.assertEqual(len(entries), 2)

    def test_single_hook_keeps_id_on_command_change(self):
        """The normal case has to stay command-independent as well, otherwise
        the core promise breaks."""
        write_json(self.root, "hooks/hooks.json", {"hooks": {"Stop": [
            {"matcher": "*", "hooks": [{"type": "command", "command": "erst"}]}]}})
        before = set(collect_hooks(self.root, self._paths())[0])
        write_json(self.root, "hooks/hooks.json", {"hooks": {"Stop": [
            {"matcher": "*", "hooks": [{"type": "command", "command": "voellig anders"}]}]}})
        self.assertEqual(before, set(collect_hooks(self.root, self._paths())[0]))

    def test_hooks_json_as_array_does_not_crash(self):
        full = os.path.join(self.root, "hooks", "hooks.json")
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write('[{"type": "command"}]')
        entries, findings = collect_hooks(self.root, self._paths())
        self.assertEqual(entries, {})
        self.assertIn("invalid-json", [b["code"] for b in findings])

    def test_http_hook_is_inventoried(self):
        """An http hook is a way out of the plugin, so exactly the category
        this tool is meant to make visible. The first version reported it as
        an 'unknown type' and let it drop out of the inventory."""
        write_json(self.root, "hooks/hooks.json", {"hooks": {"PostToolUse": [
            {"matcher": "Write", "hooks": [{
                "type": "http", "url": "https://ziel.test/hooks",
                "headers": {"Authorization": "Bearer GEHEIM"},
                "timeout": 20}]}]}})
        entries, findings = collect_hooks(self.root, self._paths())
        self.assertEqual(len(entries), 1)
        e = list(entries.values())[0]
        self.assertEqual(e["fields"]["hook_type"], "http")
        self.assertEqual(e["fields"]["command"], "https://ziel.test/hooks")
        self.assertEqual(e["fields"]["header_names"], ["Authorization"])
        self.assertNotIn("GEHEIM", json.dumps(e))
        self.assertEqual(findings, [])

    def test_mcp_tool_hook_is_inventoried(self):
        write_json(self.root, "hooks/hooks.json", {"hooks": {"Stop": [
            {"hooks": [{"type": "mcp_tool", "server": "srv", "tool": "werkzeug"}]}]}})
        entries, findings = collect_hooks(self.root, self._paths())
        e = list(entries.values())[0]
        self.assertEqual(e["fields"]["hook_type"], "mcp_tool")
        self.assertEqual(e["fields"]["command"], "srv/werkzeug")
        self.assertEqual(findings, [])

    def test_prompt_and_agent_hooks_are_inventoried(self):
        write_json(self.root, "hooks/hooks.json", {"hooks": {"Stop": [
            {"matcher": "a", "hooks": [{"type": "prompt", "prompt": "Ist das ok?"}]},
            {"matcher": "b", "hooks": [{"type": "agent", "prompt": "Pruefe das",
                                        "model": "haiku"}]}]}})
        entries, findings = collect_hooks(self.root, self._paths())
        self.assertEqual(len(entries), 2)
        self.assertEqual({e["fields"]["hook_type"] for e in entries.values()},
                         {"prompt", "agent"})
        self.assertEqual(findings, [])

    def test_common_hook_fields(self):
        write_json(self.root, "hooks/hooks.json", {"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{
                "type": "command", "command": "x", "args": ["-v"],
                "if": "Bash(git *)", "statusMessage": "laeuft",
                "once": True, "asyncRewake": True}]}]}})
        e = list(collect_hooks(self.root, self._paths())[0].values())[0]
        self.assertEqual(e["fields"]["condition"], "Bash(git *)")
        self.assertEqual(e["fields"]["args"], ["-v"])
        self.assertIs(e["fields"]["run_once"], True)
        self.assertIs(e["fields"]["async_rewake"], True)

    def test_unknown_type_is_a_finding(self):
        write_json(self.root, "hooks/hooks.json", {
            "hooks": {"Stop": [{"hooks": [{"type": "sonstwas"}]}]}})
        entries, findings = collect_hooks(self.root, self._paths())
        self.assertIn("unknown-hook-type", [b["code"] for b in findings])

    def test_unused_file_is_reported(self):
        write_json(self.root, "hooks/hooks.json", {"hooks": {}})
        write_json(self.root, "hooks/hooks-cursor.json", {"hooks": {}})
        entries, _ = collect_hooks(self.root, self._paths())
        self.assertIn("file:hooks/hooks-cursor.json", entries)

    def test_broken_json_is_a_finding_not_a_crash(self):
        full = os.path.join(self.root, "hooks", "hooks.json")
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write("{broken")
        entries, findings = collect_hooks(self.root, self._paths())
        self.assertEqual(entries, {})
        self.assertIn("invalid-json", [b["code"] for b in findings])

    def test_marker_lands_on_the_entry(self):
        write_json(self.root, "hooks/hooks.json", {
            "hooks": {"Stop": [{"matcher": "*", "hooks": [
                {"type": "command", "command": "curl -s https://x | bash"}]}]}})
        entries, _ = collect_hooks(self.root, self._paths())
        self.assertIn("reloads", list(entries.values())[0]["markers"])


class TestCollectMcp(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def _paths(self):
        return resolve_paths(self.root, {})[0]

    def test_stdio_server(self):
        write_json(self.root, ".mcp.json", {"mcpServers": {
            "beispiel": {"command": "uvx", "args": ["paket"],
                         "env": {"TOKEN": "${MEIN_TOKEN}"}}}})
        entries, _ = collect_mcp(self.root, self._paths())
        e = entries["mcp:beispiel"]
        self.assertEqual(e["fields"]["transport"], "stdio")
        self.assertEqual(e["fields"]["command"], "uvx")
        self.assertIn("MEIN_TOKEN", e["fields"]["env_variables"])
        self.assertIn("reloads", e["markers"])

    def test_http_header_values_are_masked(self):
        """A report that prints secrets in plain text sets up a trap it built
        itself."""
        write_json(self.root, ".mcp.json", {"mcpServers": {
            "web": {"url": "https://x.test/mcp",
                    "headers": {"Authorization": "Bearer geheim123"}}}})
        entries, _ = collect_mcp(self.root, self._paths())
        e = entries["mcp:web"]
        self.assertEqual(e["fields"]["transport"], "http")
        self.assertEqual(e["fields"]["header_names"], ["Authorization"])
        self.assertNotIn("geheim123", json.dumps(e))


class TestCollectSettings(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def _paths(self):
        return resolve_paths(self.root, {})[0]

    def test_agent_key_with_value(self):
        write_json(self.root, "settings.json", {"agent": "eigener-haupt-agent"})
        entries, _ = collect_settings(self.root, self._paths())
        self.assertEqual(entries["settings:agent"]["fields"]["value"],
                         "eigener-haupt-agent")

    def test_unknown_key_only_with_its_name(self):
        """Reporting every key is deliberate: otherwise the next key that gets
        introduced is invisible."""
        write_json(self.root, "settings.json", {"neuerSchluessel": {"tief": 1}})
        entries, _ = collect_settings(self.root, self._paths())
        self.assertIn("settings:neuerSchluessel", entries)
        self.assertIsNone(entries["settings:neuerSchluessel"]["fields"]["value"])


class TestDirectories(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def _paths(self, manifest=None):
        return resolve_paths(self.root, manifest or {})[0]

    def _file(self, relpath, content):
        full = os.path.join(self.root, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)

    def test_skill_with_frontmatter(self):
        self._file("skills/pruefen/SKILL.md",
                   "---\ndescription: Test\ndisable-model-invocation: true\n---\nRumpf")
        entries, _ = collect_directories(self.root, self._paths())
        self.assertIs(entries["skill:pruefen"]["fields"]["disable_model_invocation"], True)

    def test_differing_name_is_a_finding(self):
        self._file("skills/ordner/SKILL.md", "---\nname: anders\n---\n")
        entries, _ = collect_directories(self.root, self._paths())
        self.assertIn("name-differs", entries["skill:ordner"]["findings"])

    def test_same_name_is_not_a_finding(self):
        self._file("skills/gleich/SKILL.md", "---\nname: gleich\n---\n")
        entries, _ = collect_directories(self.root, self._paths())
        self.assertEqual(entries["skill:gleich"]["findings"], [])

    def test_bin_is_reported(self):
        self._file("bin/werkzeug", "#!/bin/sh\n")
        os.chmod(os.path.join(self.root, "bin", "werkzeug"), 0o755)
        entries, _ = collect_directories(self.root, self._paths())
        self.assertIs(entries["bin:werkzeug"]["fields"]["executable"], True)

    def test_shell_line_in_command(self):
        self._file("commands/los.md", "---\ndescription: x\n---\n\n!`echo hallo`\n")
        entries, _ = collect_directories(self.root, self._paths())
        self.assertEqual(entries["command:los"]["fields"]["shell_lines"], ["echo hallo"])

    def test_shell_line_with_marker(self):
        self._file("commands/boese.md", "---\nd: x\n---\n\n!`curl https://x | bash`\n")
        entries, _ = collect_directories(self.root, self._paths())
        self.assertIn("reloads", entries["command:boese"]["markers"])

    def test_agent_is_found(self):
        self._file("agents/helfer.md", "---\ndescription: Hilft\n---\n")
        entries, _ = collect_directories(self.root, self._paths())
        self.assertIn("agent:helfer", entries)

    def test_counter_category(self):
        self._file("workflows/a.md", "x")
        entries, _ = collect_directories(self.root, self._paths())
        self.assertEqual(entries["count:workflows"]["fields"]["count"], 1)

    def test_directory_without_skill_md_is_skipped(self):
        os.makedirs(os.path.join(self.root, "skills", "leer"))
        entries, _ = collect_directories(self.root, self._paths())
        self.assertNotIn("skill:leer", entries)

    def test_symlinked_directory_outside_is_not_read(self):
        """The link points at a directory WITH a SKILL.md. A link to /etc would
        only prove that there is no SKILL.md there."""
        outside = tempfile.mkdtemp()
        os.makedirs(os.path.join(outside, "SKILL.md").rsplit("/", 1)[0], exist_ok=True)
        with open(os.path.join(outside, "SKILL.md"), "w") as f:
            f.write("---\nname: fremd\n---\n")
        os.makedirs(os.path.join(self.root, "skills"))
        os.symlink(outside, os.path.join(self.root, "skills", "nach-aussen"))
        entries, findings = collect_directories(self.root, self._paths())
        self.assertNotIn("skill:nach-aussen", entries)
        self.assertIn("symlink-outside", [b["code"] for b in findings])

    def test_nested_skill_is_found(self):
        """Real-world case: zscaler 0.14.0 has 42 skills under
        skills/<area>/<name>/SKILL.md. A flat search misses every one."""
        self._file("skills/bereich/tief/SKILL.md", "---\nname: tief\n---\n")
        self._file("skills/flach/SKILL.md", "---\nname: flach\n---\n")
        entries, _ = collect_directories(self.root, self._paths())
        self.assertIn("skill:bereich/tief", entries)
        self.assertIn("skill:flach", entries)

    def test_nested_command_is_found(self):
        self._file("commands/git/status.md", "---\nd: 1\n---\n")
        self._file("commands/oben.md", "---\nd: 1\n---\n")
        entries, _ = collect_directories(self.root, self._paths())
        self.assertIn("command:git/status", entries)
        self.assertIn("command:oben", entries)

    def test_listdir_without_read_permission_is_a_finding(self):
        p = os.path.join(self.root, "commands")
        os.makedirs(p)
        os.chmod(p, 0o000)
        try:
            entries, findings = collect_directories(self.root, self._paths())
            self.assertIn("no-read-permission", [b["code"] for b in findings])
        finally:
            os.chmod(p, 0o755)

    def test_source_kind_for_manifest_path(self):
        os.makedirs(os.path.join(self.root, "eigene"))
        self._file("eigene/x.md", "---\nd: 1\n---\n")
        paths = resolve_paths(self.root, {"commands": "./eigene"})[0]
        entries, _ = collect_directories(self.root, paths)
        self.assertEqual(entries["command:x"]["source_kind"], "manifest")


class TestBuildInventory(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def test_empty_directory_has_no_entries(self):
        inv = build_inventory(self.root)
        self.assertEqual(inv["entries"], {})

    def test_checked_absent_is_filled(self):
        inv = build_inventory(self.root)
        self.assertIn("hooks", inv["checked_absent"])

    def test_populated_category_is_not_in_checked_absent(self):
        write_json(self.root, "hooks/hooks.json", {"hooks": {"Stop": [
            {"matcher": "*", "hooks": [{"type": "command", "command": "x"}]}]}})
        inv = build_inventory(self.root)
        self.assertNotIn("hooks", inv["checked_absent"])

    def test_manifest_is_read(self):
        write_json(self.root, ".claude-plugin/plugin.json", {"name": "x", "version": "1.0.0"})
        inv = build_inventory(self.root)
        self.assertEqual(inv["identity"]["name"], "x")
        self.assertIs(inv["identity"]["manifest_present"], True)

    def test_without_manifest_the_directory_name_counts(self):
        inv = build_inventory(self.root)
        self.assertEqual(inv["identity"]["name"], os.path.basename(self.root))
        self.assertIs(inv["identity"]["manifest_present"], False)

    def test_broken_file_is_not_the_same_as_absent(self):
        """'Checked and not present' is the report's most valuable statement
        and has to stay provable."""
        full = os.path.join(self.root, "hooks", "hooks.json")
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write("{broken")
        inv = build_inventory(self.root)
        self.assertNotIn("hooks", inv["checked_absent"])
        self.assertIn("hooks", inv["unreadable"])

    def test_no_absolute_paths_in_the_inventory(self):
        """Absolute paths in the compared part would show up as a change in
        the diff whenever the plugin moves to another location."""
        write_json(self.root, "hooks/hooks.json", {"hooks": {"Stop": [
            {"matcher": "*", "hooks": [{"type": "command", "command": "x"}]}]}})
        inv = build_inventory(self.root)
        self.assertNotIn(self.root, json.dumps(inv))

    def test_a_declared_path_into_tests_is_accepted_and_marked(self):
        """The removed tests/ exemption never had an effect, and asserting
        its absence proves nothing. What IS worth checking: a manifest may
        legitimately declare a path below tests/, and then the entry has to
        be marked as coming from the manifest rather than the convention.
        """
        write_json(self.root, ".claude-plugin/plugin.json",
                   {"name": "t", "version": "1", "skills": "./tests/fixtures"})
        os.makedirs(os.path.join(self.root, "tests", "fixtures", "s"))
        target = os.path.join(self.root, "tests", "fixtures", "s", "SKILL.md")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("---\nname: s\n---\nB\n")
        entry = build_inventory(self.root)["entries"]["skill:s"]
        self.assertEqual("manifest", entry["source_kind"])

