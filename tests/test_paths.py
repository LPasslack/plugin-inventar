import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: F401  removes this run's temp directories
from inventory.collect import resolve_paths


class TestResolvePaths(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def _create(self, relpath, is_dir=False):
        full = os.path.join(self.root, relpath)
        if is_dir:
            os.makedirs(full, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            open(full, "w").close()
        return full

    def _codes(self, findings):
        return [f["code"] for f in findings]

    def test_convention_is_found(self):
        self._create("hooks/hooks.json")
        paths, findings = resolve_paths(self.root, {})
        self.assertEqual(paths["hooks"], ["hooks/hooks.json"])
        self.assertEqual(findings, [])

    def test_nothing_present_yields_empty_lists(self):
        paths, findings = resolve_paths(self.root, {})
        self.assertEqual(paths["hooks"], [])
        self.assertEqual(findings, [])

    def test_skills_extends_the_default(self):
        self._create("skills", is_dir=True)
        self._create("extra-skills", is_dir=True)
        paths, _ = resolve_paths(self.root, {"skills": "./extra-skills"})
        self.assertEqual(paths["skills"], ["extra-skills", "skills"])

    def test_commands_replaces_the_default(self):
        self._create("commands", is_dir=True)
        self._create("eigene", is_dir=True)
        paths, _ = resolve_paths(self.root, {"commands": "./eigene"})
        self.assertEqual(paths["commands"], ["eigene"])

    def test_array_is_accepted(self):
        self._create("a", is_dir=True)
        self._create("b", is_dir=True)
        paths, _ = resolve_paths(self.root, {"agents": ["./a", "./b"]})
        self.assertEqual(paths["agents"], ["a", "b"])

    def test_declared_path_missing_is_a_finding(self):
        paths, findings = resolve_paths(self.root, {"hooks": "./weg.json"})
        self.assertIn("declared-path-missing", self._codes(findings))

    def test_path_leaving_plugin_is_not_followed(self):
        paths, findings = resolve_paths(self.root, {"hooks": "../../etc/passwd"})
        self.assertIn("path-leaves-plugin", self._codes(findings))
        self.assertEqual(paths.get("hooks", []), [])

    def test_absolute_path_is_not_followed(self):
        paths, findings = resolve_paths(self.root, {"hooks": "/etc/passwd"})
        self.assertIn("absolute-path", self._codes(findings))
        self.assertEqual(paths.get("hooks", []), [])

    def test_inline_object_is_a_finding(self):
        paths, findings = resolve_paths(self.root, {"mcpServers": {"a": {"command": "x"}}})
        self.assertIn("declared-inline", self._codes(findings))

    def test_experimental_themes(self):
        self._create("eigene-themes", is_dir=True)
        paths, _ = resolve_paths(self.root, {"experimental": {"themes": "./eigene-themes"}})
        self.assertEqual(paths["themes"], ["eigene-themes"])

    def test_replacement_only_applies_for_a_valid_path(self):
        # An invalid declared path must not delete the conventional path,
        # otherwise a genuinely present hook disappears from the report.
        self._create("commands", is_dir=True)
        paths, findings = resolve_paths(self.root, {"commands": "./gibt-es-nicht"})
        self.assertEqual(paths["commands"], ["commands"])
        self.assertIn("declared-path-missing", self._codes(findings))

    def test_duplicate_entry_is_deduplicated(self):
        self._create("skills", is_dir=True)
        paths, _ = resolve_paths(self.root, {"skills": ["./skills", "skills"]})
        self.assertEqual(paths["skills"], ["skills"])


if __name__ == "__main__":
    unittest.main()
