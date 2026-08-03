"""Candidate replacements for the mutations that survived round 10's audit."""
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
from inventory.collect import (_mask_secrets, build_inventory, collect_mcp,
                               resolve_paths)
from inventory.installed import installed_plugins
from inventory.reading import tree_digest
from inventory.report import render, render_sweep
from inventory.state import diff

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "bin", "plugin-inventar")

EMPTY = {"added": [], "removed": [], "changed": {}, "identity": {},
         "matchers": {}, "commands": {},
         "findings": {"added": [], "removed": []}, "categories": {}}


def d(**over):
    out = json.loads(json.dumps(EMPTY))
    out.update(over)
    return out


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


# --------------------------------------------------------------- masking ----

class SecretsUnderCamelCaseKeys(T):
    def test_a_camel_case_secret_key_is_masked(self):
        self.assertEqual({"apiKey": "[…]"}, _mask_secrets({"apiKey": "geheim"}))
        self.assertEqual({"authToken": "[…]"}, _mask_secrets({"authToken": "geheim"}))
        self.assertEqual({"accessToken": "[…]"},
                         _mask_secrets({"accessToken": "geheim"}))

    def test_every_hint_word_masks_on_its_own(self):
        for name in ("key", "token", "secret", "password", "passwd", "pwd",
                     "passphrase", "credential", "credentials", "auth",
                     "authorization", "authentication", "cookie", "bearer",
                     "pat", "otp", "dsn", "connection", "signature"):
            self.assertEqual({name: "[…]"}, _mask_secrets({name: "geheim"}), name)

    def test_an_ordinary_key_is_not_masked(self):
        self.assertEqual({"path": "/x", "author": "A", "description": "d"},
                         _mask_secrets({"path": "/x", "author": "A",
                                        "description": "d"}))

    def test_a_camel_case_secret_does_not_reach_the_report(self):
        root = self.plugin()
        self.write(root, "settings.json", {"agent": {"apiKey": "SUPERGEHEIM"}})
        self.write(root, "commands/go.md", "---\nname: go\n---\nB\n")
        text = render(build_inventory(root), None)
        self.assertNotIn("SUPERGEHEIM", text)

    def test_an_mcp_command_that_looks_like_a_token_is_masked(self):
        root = self.plugin()
        self.write(root, ".mcp.json",
                   {"mcpServers": {"s": {"command": "sk-liveAbc123"}}})
        entries, _ = collect_mcp(root, resolve_paths(root, {})[0])
        self.assertEqual("[…]", entries["mcp:s"]["fields"]["command"])


# -------------------------------------------------------- finding identity --

class FindingsAreComparedByTheirIdentity(T):
    def _inv(self, findings):
        return {"entries": {}, "identity": {}, "checked_absent": [],
                "findings": findings}

    def test_another_code_at_the_same_place_is_two_changes(self):
        old = self._inv([{"code": "absolute-path", "path": "/x",
                          "category": "hooks", "detail": ""}])
        new = self._inv([{"code": "path-leaves-plugin", "path": "/x",
                          "category": "hooks", "detail": ""}])
        result = diff(old, new)
        self.assertEqual(["path-leaves-plugin"],
                         [f["code"] for f in result["findings"]["added"]])
        self.assertEqual(["absolute-path"],
                         [f["code"] for f in result["findings"]["removed"]])

    def test_the_same_code_at_another_place_is_two_changes(self):
        old = self._inv([{"code": "symlink-outside", "path": "skills/a",
                          "category": "skills", "detail": "points-outside"}])
        new = self._inv([{"code": "symlink-outside", "path": "skills/b",
                          "category": "skills", "detail": "points-outside"}])
        result = diff(old, new)
        self.assertEqual(["skills/b"],
                         [f["path"] for f in result["findings"]["added"]])
        self.assertEqual(["skills/a"],
                         [f["path"] for f in result["findings"]["removed"]])

    def test_the_same_code_in_another_category_is_two_changes(self):
        old = self._inv([{"code": "no-read-permission", "path": "x",
                          "category": "hooks", "detail": ""}])
        new = self._inv([{"code": "no-read-permission", "path": "x",
                          "category": "skills", "detail": ""}])
        result = diff(old, new)
        self.assertEqual(1, len(result["findings"]["added"]))
        self.assertEqual(1, len(result["findings"]["removed"]))

    def test_another_detail_is_two_changes(self):
        old = self._inv([{"code": "symlink-outside", "path": "x",
                          "category": "skills", "detail": "points-outside"}])
        new = self._inv([{"code": "symlink-outside", "path": "x",
                          "category": "skills",
                          "detail": "convention-path-points-outside"}])
        result = diff(old, new)
        self.assertEqual(1, len(result["findings"]["added"]))
        self.assertEqual(1, len(result["findings"]["removed"]))

    def test_another_detail_argument_is_two_changes(self):
        old = self._inv([{"code": "duplicate-id", "path": "skills/a/SKILL.md",
                          "category": None, "detail": "collides-with",
                          "detail_arg": "skills/b/SKILL.md"}])
        new = self._inv([{"code": "duplicate-id", "path": "skills/a/SKILL.md",
                          "category": None, "detail": "collides-with",
                          "detail_arg": "skills/c/SKILL.md"}])
        result = diff(old, new)
        self.assertEqual(1, len(result["findings"]["added"]))
        self.assertEqual(1, len(result["findings"]["removed"]))


# ------------------------------------------------------------- the report ---

class TheDiffSpellsOutWhatMoved(T):
    def _empty_inventory(self):
        return {"identity": {}, "entries": {}, "checked_absent": [],
                "findings": []}

    def test_a_finding_only_change_is_not_called_no_change(self):
        differences = d(findings={"added": [{"code": "absolute-path",
                                             "path": "/etc/x",
                                             "category": "hooks",
                                             "detail": ""}],
                                  "removed": []})
        text = render(self._empty_inventory(), differences)
        self.assertNotIn("No changes", text)
        self.assertIn("+ Finding absolute path", text)

    def test_a_category_only_change_is_not_called_no_change(self):
        differences = d(categories={"hooks": ("absent", "unreadable")})
        text = render(self._empty_inventory(), differences)
        self.assertNotIn("No changes", text)
        self.assertIn("~ Kategorie Hooks", text)

    def test_a_finding_that_disappeared_is_named(self):
        old = {"entries": {}, "findings": [{"code": "absolute-path",
                                            "path": "/etc/x",
                                            "category": "hooks",
                                            "detail": ""}]}
        text = render(self._empty_inventory(), diff(old, {"entries": {}}))
        self.assertIn("- Finding absolute path", text)


class TheSweepReport(T):
    def _r(self, key, differences, enabled=True, scope="user", note=None):
        return {"key": key, "differences": differences, "enabled": enabled,
                "scope": scope, "note": note}

    def test_a_finding_only_diff_is_spelled_out_per_plugin(self):
        differences = d(findings={"added": [{"code": "absolute-path",
                                             "path": "/etc/x",
                                             "category": "hooks",
                                             "detail": ""}],
                                  "removed": []})
        text = render_sweep([self._r("a@b", differences)])
        self.assertIn("Changed", text)
        self.assertIn("Finding absolute path", text)

    def test_a_category_only_diff_counts_as_changed(self):
        text = render_sweep([self._r("a@b",
                                     d(categories={"hooks": ("absent",
                                                             "unreadable")}))])
        self.assertIn("Changed", text)
        self.assertIn("Kategorie Hooks", text)

    def test_an_identity_only_diff_counts_as_changed(self):
        text = render_sweep([self._r("a@b",
                                     d(identity={"version": ("1.0.0", "2.0.0")}))])
        self.assertIn("Changed", text)
        self.assertIn("2.0.0", text)

    def test_a_changed_plugin_keeps_its_markers(self):
        differences = d(added=["skill:neu"])
        text = render_sweep([self._r("a@b", differences, enabled=False,
                                     scope="project")])
        self.assertIn("a@b  [project, disabled]", text)

    def test_the_count_line_counts_every_plugin(self):
        text = render_sweep([self._r("a@b", None), self._r("c@d", EMPTY)])
        self.assertIn("Checked 2 Plugins", text)
        self.assertNotIn("baseline", text)

    def test_the_first_run_line_is_singular_for_one_plugin(self):
        self.assertIn("1 Plugin. That is your baseline",
                      render_sweep([self._r("a@b", None)]))

    def test_the_disabled_line_is_singular_for_one_plugin(self):
        one = render_sweep([self._r("a@b", EMPTY, enabled=False)])
        self.assertIn("It does not run", one)
        two = render_sweep([self._r("a@b", EMPTY, enabled=False),
                            self._r("c@d", EMPTY, enabled=False)])
        self.assertIn("They do not run", two)

    def test_the_reason_for_a_missing_comparison_is_named(self):
        text = render_sweep([self._r("a@b", None, note="not valid JSON")])
        self.assertIn("not valid JSON", text)


# ---------------------------------------------------------- the registry ----

class Fake(unittest.TestCase):
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
        with open(os.path.join(path, ".claude-plugin", "plugin.json"), "w") as h:
            json.dump(dict({"name": name, "version": version}, **manifest), h)
        full = os.path.join(path, "commands", "go.md")
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as h:
            h.write("---\nname: go\n---\nB\n")
        key = f"{name}@{market}"
        self.registry.setdefault(key, []).append(
            {"scope": scope, "installPath": path, "version": version})
        self.enabled[key] = enabled
        return path

    def commit(self):
        with open(os.path.join(self.config, "plugins",
                               "installed_plugins.json"), "w") as h:
            json.dump({"version": 2, "plugins": self.registry}, h)
        with open(os.path.join(self.config, "settings.json"), "w") as h:
            json.dump({"enabledPlugins": self.enabled}, h)

    def run_sweep(self, *extra, **env_extra):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=self.config,
                   XDG_STATE_HOME=self.state, **env_extra)
        return subprocess.run([sys.executable, TOOL, *extra], env=env,
                              capture_output=True, text=True, timeout=120)


class TheRegistryIsReadDefensively(Fake):
    def test_a_single_object_instead_of_a_list_is_accepted(self):
        self.plugin("einzeln")
        self.registry["einzeln@markt"] = self.registry["einzeln@markt"][0]
        self.commit()
        records, findings = installed_plugins(self.config)
        self.assertEqual(["einzeln@markt"], [r["key"] for r in records])
        self.assertEqual([], findings)

    def test_a_registry_value_of_the_wrong_type_is_reported(self):
        self.plugin("gut")
        self.registry["kaputt@markt"] = 42
        self.commit()
        records, findings = installed_plugins(self.config)
        self.assertEqual(["gut@markt"], [r["key"] for r in records])
        self.assertIn("unexpected-type", [f["code"] for f in findings])

    def test_an_entry_that_is_not_an_object_is_reported(self):
        self.plugin("gut")
        self.registry["kaputt@markt"] = ["nur ein String"]
        self.commit()
        records, findings = installed_plugins(self.config)
        self.assertEqual(["gut@markt"], [r["key"] for r in records])
        self.assertIn("unexpected-type", [f["code"] for f in findings])

    def test_a_registry_without_a_plugins_object_is_reported(self):
        with open(os.path.join(self.config, "plugins",
                               "installed_plugins.json"), "w") as h:
            json.dump({"version": 2, "plugins": []}, h)
        records, findings = installed_plugins(self.config)
        self.assertEqual([], records)
        self.assertIn("unexpected-type", [f["code"] for f in findings])

    def test_an_empty_install_path_is_reported(self):
        self.registry["leer@markt"] = [{"scope": "user", "installPath": "",
                                        "version": "1"}]
        self.commit()
        records, findings = installed_plugins(self.config)
        self.assertEqual([], records)
        self.assertIn("declared-path-missing", [f["code"] for f in findings])

    def test_only_install_path_counts_as_a_path(self):
        self.registry["alt@markt"] = [{"scope": "user", "path": "/irgendwo",
                                       "version": "1"}]
        self.commit()
        records, findings = installed_plugins(self.config)
        self.assertEqual([], records)
        self.assertIn("declared-path-missing", [f["code"] for f in findings])

    def test_a_directory_at_the_registry_path_is_not_silence(self):
        os.makedirs(os.path.join(self.config, "plugins",
                                 "installed_plugins.json"))
        records, findings = installed_plugins(self.config)
        self.assertEqual([], records)
        self.assertEqual([], findings)

    def test_the_version_is_carried_into_the_record(self):
        self.plugin("v", version="3.2.1")
        self.commit()
        records, _ = installed_plugins(self.config)
        self.assertEqual(["3.2.1"], [r["version"] for r in records])


class TheCommandLineKeepsItsPromises(Fake):
    def test_an_unknown_option_does_not_look_like_an_empty_directory(self):
        result = self.run_sweep("--gibtesnicht")
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("Fehler", result.stderr)

    def test_the_sweep_honours_do_not_save(self):
        self.plugin("x")
        self.commit()
        result = self.run_sweep("--no-save")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], glob.glob(os.path.join(self.state, "plugin-inventar",
                                                    "*.json")))

    def test_a_tilde_in_the_install_path_is_expanded(self):
        path = self.plugin("tilde")
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        shutil.move(path, os.path.join(home, "plug"))
        self.registry["tilde@markt"] = [{"scope": "user",
                                         "installPath": "~/plug",
                                         "version": "1.0.0"}]
        self.commit()
        result = self.run_sweep(HOME=home)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("declared path missing", result.stdout)
        self.assertIn("tilde@markt", result.stdout)

    def test_an_unusable_stored_state_says_why_in_the_sweep(self):
        self.plugin("kaputt")
        self.commit()
        self.run_sweep()
        for name in glob.glob(os.path.join(self.state, "plugin-inventar", "*.json")):
            if name.endswith(".1.json"):
                continue
            with open(name, "w", encoding="utf-8") as h:
                h.write("{ kaputt")
        result = self.run_sweep()
        self.assertIn("not valid JSON", result.stdout)


# ---------------------------------------------------------------- collect ---

class ChangesThatMustNotBeSilent(T):
    def test_a_changed_settings_value_is_a_change(self):
        root = self.plugin()
        self.write(root, "settings.json",
                   {"permissions": {"allow": ["Bash(ls)"]}})
        before = build_inventory(root)
        self.write(root, "settings.json",
                   {"permissions": {"allow": ["Bash(rm -rf /)"]}})
        self.assertIn("settings:permissions",
                      diff(before, build_inventory(root))["changed"])

    def test_two_bases_sharing_a_prefix_are_both_kept(self):
        root = self.plugin(commands=["befehle", "befehle-extra"])
        self.write(root, "befehle/a.md", "---\nname: a\n---\nB\n")
        self.write(root, "befehle-extra/b.md", "---\nname: b\n---\nB\n")
        entries = build_inventory(root)["entries"]
        self.assertIn("command:a", entries)
        self.assertIn("command:b", entries)

    def test_an_agent_named_after_its_file_is_not_flagged(self):
        root = self.plugin()
        self.write(root, "agents/helfer.md", "---\nname: helfer\n---\nB\n")
        self.assertEqual(
            [], build_inventory(root)["entries"]["agent:helfer"]["findings"])


class TreeDigestAndDirectorySymlinks(T):
    def test_renaming_a_linked_directory_changes_the_hash(self):
        base, target = self.temp(), self.temp()
        self.write(target, "regeln.md", "A")
        os.symlink(target, os.path.join(base, "references"))
        first = tree_digest(base)
        self.assertIsNotNone(first)
        os.rename(os.path.join(base, "references"),
                  os.path.join(base, "scripts"))
        self.assertNotEqual(first, tree_digest(base))

    def test_the_skip_list_also_covers_a_directory_symlink(self):
        base, a, b = self.temp(), self.temp(), self.temp()
        self.write(base, "echt.md", "bleibt")
        self.write(a, "x.md", "A")
        self.write(b, "x.md", "B")
        os.symlink(a, os.path.join(base, "extra"))
        first = tree_digest(base, skip=("extra",))
        self.assertIsNotNone(first)
        os.unlink(os.path.join(base, "extra"))
        os.symlink(b, os.path.join(base, "extra"))
        self.assertEqual(first, tree_digest(base, skip=("extra",)))


class TheStateKeyUsesTheInnermostCache(T):
    def cli(self):
        import importlib.util
        from importlib.machinery import SourceFileLoader
        previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
        loader = SourceFileLoader("cli_probe", TOOL)
        module = importlib.util.module_from_spec(
            importlib.util.spec_from_loader(loader.name, loader))
        try:
            loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = previous
        return module

    def test_a_cache_inside_a_cache_uses_the_inner_one(self):
        path = os.path.join(self.temp(), "cache", "aussen", "cache",
                            "markt", "plug", "1.0.0")
        os.makedirs(path)
        self.assertTrue(self.cli().state_key_for(path).startswith("plug@markt-"),
                        self.cli().state_key_for(path))



class MoreGaps(T):
    def _empty_inventory(self):
        return {"identity": {}, "entries": {}, "checked_absent": [],
                "findings": []}

    def test_a_category_that_becomes_absent_was_present_before(self):
        old = {"entries": {}, "checked_absent": [], "unreadable": []}
        new = {"entries": {}, "checked_absent": ["hooks"], "unreadable": []}
        self.assertEqual(("present", "absent"),
                         diff(old, new)["categories"]["hooks"])

    def test_the_category_line_names_before_and_after_in_order(self):
        text = render(self._empty_inventory(),
                      d(categories={"hooks": ("absent", "unreadable")}))
        self.assertIn("before: not present", text)
        self.assertIn("now: present, but could not be evaluated", text)

    def test_the_sweep_does_not_repeat_the_diff_header(self):
        text = render_sweep([{"key": "a@b", "differences": d(added=["skill:neu"]),
                              "enabled": True, "scope": "user", "note": None}])
        self.assertNotIn("Changes since the last run", text)
        self.assertIn("+ Skill neu", text)


class TheSweepStateIsUsableLater(Fake):
    def test_it_records_which_directory_it_read_and_when(self):
        path = self.plugin("merk")
        self.commit()
        self.run_sweep()
        files = [f for f in glob.glob(os.path.join(self.state, "plugin-inventar",
                                                   "*.json"))
                 if not f.endswith(".1.json")]
        self.assertEqual(1, len(files))
        with open(files[0], encoding="utf-8") as handle:
            meta = json.load(handle)["meta"]
        self.assertEqual(os.path.realpath(path), os.path.realpath(meta["path"]))
        self.assertRegex(meta["read_at"], r"^20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ$")




class TheSkipListReachesDirectories(unittest.TestCase):
    """tree_digest compared `skip` against the full relative path and the
    bare basename, never against a parent directory. The root-skill case
    passes the component directories and `.claude-plugin`, and none of them
    had any effect: the whole plugin folded into the skill's extras hash, so
    a version bump in the manifest moved it and everything with its own
    entry was reported twice.
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

    def _root_skill(self):
        root = self.temp()
        self.write(root, ".claude-plugin/plugin.json", {"name": "w", "version": "1"})
        self.write(root, "SKILL.md", "---\nname: w\n---\nB\n")
        self.write(root, "references/regeln.md", "A")
        return root

    def _extras(self, root):
        return build_inventory(root)["entries"]["skill:w"]["fields"]["extras_hash"]

    def test_the_manifest_is_not_folded_into_the_skill(self):
        root = self._root_skill()
        before = self._extras(root)
        self.write(root, ".claude-plugin/plugin.json", {"name": "w", "version": "2"})
        self.assertEqual(before, self._extras(root))

    def test_a_component_directory_is_not_folded_in(self):
        """Everything under commands/ has its own entry already."""
        root = self._root_skill()
        before = self._extras(root)
        self.write(root, "commands/go.md", "---\nname: go\n---\nB\n")
        self.assertEqual(before, self._extras(root))

    def test_the_real_extras_are_still_seen(self):
        """The counter-case: narrowing the hash must not make it blind."""
        root = self._root_skill()
        before = self._extras(root)
        self.write(root, "references/regeln.md", "GEÄNDERT")
        self.assertNotEqual(before, self._extras(root))

    def test_a_skipped_directory_is_not_walked_into(self):
        root = self.temp()
        self.write(root, "behalten/a.md", "A")
        self.write(root, "weg/tief/b.md", "B")
        before = tree_digest(root, skip=("weg",))
        self.write(root, "weg/tief/b.md", "ganz anders")
        self.assertEqual(before, tree_digest(root, skip=("weg",)))

if __name__ == "__main__":
    unittest.main()
