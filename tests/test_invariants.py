"""Guards over the tables that have to be edited together.

Five of this project's findings came from the same shape: a rule lives in
three places and the fourth is forgotten. These tests do not check
behaviour, they check that the tables still agree with each other -- the
kind of failure that otherwise shows up as a report quietly claiming
absence about something it just found.

The model is the SCHEMA reminder in test_round10: a test whose failure
message says what to do.
"""
import ast
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: F401  removes this run's temp directories
from inventory.collect import (COMPONENTS, COUNT_ONLY_CATEGORIES,
                               KIND_TO_CATEGORY, build_inventory)
from inventory.report import (CATEGORY_TEXT, DETAIL_TEXT, FINDING_TEXT,
                              KIND_TEXT, SECTIONS)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "lib", "inventory")


def _read(path):
    """Closing read. Leaving the handle to the garbage collector filled the
    run with ResourceWarnings, from the one file that guards the others."""
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class EveryComponentKindReachesTheReport(unittest.TestCase):

    def test_every_entry_kind_has_a_section(self):
        """A kind missing from SECTIONS does not exist in the report.

        It is still in the inventory and in --json, so nothing looks wrong,
        and the report says nothing about it at all.
        """
        sections = {kind for kind, _, _ in SECTIONS}
        kinds = set(KIND_TO_CATEGORY) | {"count", "unused_file"}
        self.assertEqual(set(), kinds - sections,
                         "new entry kind? add it to SECTIONS in report.py")

    def test_every_entry_kind_maps_to_a_category(self):
        """A kind missing from KIND_TO_CATEGORY does not count as covered.

        The category then lands in "checked and absent" -- the positive
        claim that nothing is there, made about something just found. The
        design document calls that the worst possible mistake here.
        """
        sections = {kind for kind, _, _ in SECTIONS} - {"count", "unused_file"}
        self.assertEqual(set(), sections - set(KIND_TO_CATEGORY),
                         "new entry kind? add it to KIND_TO_CATEGORY in collect.py")

    def test_every_category_has_a_german_name(self):
        """Otherwise the raw camelCase key shows up in a German report."""
        self.assertEqual(set(), set(COMPONENTS) - set(CATEGORY_TEXT),
                         "new category? add it to CATEGORY_TEXT in report.py")

    def test_every_id_prefix_has_a_german_name(self):
        """Entry kind and ID prefix are the same word everywhere except one.

        `unused_file` entries carry `file:` in their ID, so the two
        vocabularies diverge exactly there. Held here on purpose: the next
        person adding a kind should see that the mapping is not the identity
        function, rather than discover it from a raw word in the report.
        """
        divergent = {"unused_file": "file"}
        prefixes = {divergent.get(kind, kind) for kind, _, _ in SECTIONS}
        self.assertEqual(set(), prefixes - set(KIND_TEXT),
                         "new entry kind? add its ID prefix to KIND_TEXT")

    def test_counted_categories_are_components(self):
        self.assertEqual(set(), set(COUNT_ONLY_CATEGORIES) - set(COMPONENTS))


class TheReportNeverClaimsAbsenceAboutSomethingItFound(unittest.TestCase):

    def test_no_category_is_both_covered_and_absent(self):
        """The invariant behind the table guards above, checked on a real
        run over a plugin that carries one of everything."""
        fixtures = os.path.join(ROOT, "tests", "fixtures", "complete")
        inventory = build_inventory(fixtures)
        covered = set()
        for ident, entry in inventory["entries"].items():
            if entry["kind"] == "count":
                covered.add(ident.split(":", 1)[1])
            elif entry["kind"] in KIND_TO_CATEGORY:
                covered.add(KIND_TO_CATEGORY[entry["kind"]])
        self.assertEqual(set(), covered & set(inventory["checked_absent"]))
        self.assertEqual(set(), covered & set(inventory["unreadable"]))

    def test_every_entry_kind_of_that_run_is_renderable(self):
        fixtures = os.path.join(ROOT, "tests", "fixtures", "complete")
        kinds = {e["kind"] for e in build_inventory(fixtures)["entries"].values()}
        sections = {kind for kind, _, _ in SECTIONS}
        self.assertEqual(set(), kinds - sections)


class EveryCodeInTheSourceHasATranslation(unittest.TestCase):
    """The existing guard reads three of the five files that produce codes.

    Codes are also created in installed.py and in the entry point, and
    detail codes had no guard at all -- an unknown one is printed raw, so a
    new one appears in English in the middle of a German report.
    """

    def _sources(self):
        files = [os.path.join(LIB, name) for name in
                 ("collect.py", "reading.py", "frontmatter.py", "installed.py")]
        files.append(os.path.join(ROOT, "bin", "plugin-inventar"))
        return "".join(_read(path) for path in files)

    def test_every_finding_code(self):
        source = self._sources()
        codes = set(re.findall(r'"code":\s*"([a-z0-9-]+)"', source))
        codes |= set(re.findall(r'return None,\s*"([a-z0-9-]+)"', source))
        codes |= set(re.findall(r'return \{\},\s*"([a-z0-9-]+)"', source))
        codes |= set(re.findall(r'findings\.append\("([a-z0-9-]+)"\)', source))
        self.assertEqual(set(), codes - set(FINDING_TEXT),
                         "new finding code? add it to FINDING_TEXT in report.py")

    def test_every_detail_code(self):
        source = self._sources()
        codes = set(re.findall(r'"detail":\s*"([a-z0-9-]+)"', source))
        self.assertEqual(set(), codes - set(DETAIL_TEXT),
                         "new detail code? add it to DETAIL_TEXT in report.py")


class TheIdentityIsComparedAsAWhole(unittest.TestCase):

    def test_every_identity_field_is_compared(self):
        """Entry fields are compared over the union of both sides, the
        identity over a hard-wired list. A new identity field is therefore
        stored, translated and never compared, and nothing fails.
        """
        source = _read(os.path.join(LIB, "collect.py"))
        tree = ast.parse(source)
        produced = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = [k.value for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            if "manifest_present" in keys:
                produced.update(keys)
        state = _read(os.path.join(LIB, "state.py"))
        compared = set(re.search(
            r'for key in \(([^)]*)\):\s*\n\s*before = old\.get\("identity"',
            state).group(1).replace('"', "").replace(" ", "").split(","))
        # manifest_path is deliberately not compared: it is either the one
        # constant path or None, and manifest_present already carries that.
        self.assertEqual({"manifest_path"}, produced - compared - {""},
                         "new identity field? compare it in state.diff or say "
                         "here why it is not compared")


if __name__ == "__main__":
    unittest.main()
