import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: F401  removes this run's temp directories
from inventory.frontmatter import read_frontmatter


class TestFrontmatter(unittest.TestCase):
    def test_simple_pairs(self):
        fields, finding = read_frontmatter("---\ndescription: Hallo Welt\n---\nRumpf")
        self.assertIsNone(finding)
        self.assertEqual(fields["description"], "Hallo Welt")

    def test_boolean_values(self):
        fields, _ = read_frontmatter("---\ndisable-model-invocation: true\n---\n")
        self.assertIs(fields["disable-model-invocation"], True)

    def test_inline_list(self):
        fields, _ = read_frontmatter("---\nallowed-tools: [Bash, Read]\n---\n")
        self.assertEqual(fields["allowed-tools"], ["Bash", "Read"])

    def test_comma_string_becomes_list(self):
        fields, _ = read_frontmatter("---\nallowed-tools: Bash(node:*), Read\n---\n")
        self.assertEqual(fields["allowed-tools"], ["Bash(node:*)", "Read"])

    def test_list_is_sorted(self):
        # Otherwise "B, A" and "[A, B]" diff as a change even though they mean the same.
        fields, _ = read_frontmatter("---\nallowed-tools: [Read, Bash]\n---\n")
        self.assertEqual(fields["allowed-tools"], ["Bash", "Read"])

    def test_block_list(self):
        fields, _ = read_frontmatter("---\ntools:\n  - Bash\n  - Read\n---\n")
        self.assertEqual(fields["tools"], ["Bash", "Read"])

    def test_no_frontmatter(self):
        fields, finding = read_frontmatter("Nur Rumpf, kein Block")
        self.assertEqual(fields, {})
        self.assertIsNone(finding)

    def test_unterminated_block(self):
        fields, finding = read_frontmatter("---\na: b\nkein Ende")
        self.assertEqual(finding, "unparsable-frontmatter")

    def test_quotes_are_stripped(self):
        fields, _ = read_frontmatter('---\ndescription: "Mit Zitat"\n---\n')
        self.assertEqual(fields["description"], "Mit Zitat")

    def test_colon_in_value_is_kept(self):
        fields, _ = read_frontmatter("---\ndescription: Nutze das: sofort\n---\n")
        self.assertEqual(fields["description"], "Nutze das: sofort")

    def test_comment_is_ignored(self):
        fields, _ = read_frontmatter("---\n# nur ein Kommentar\na: b\n---\n")
        self.assertNotIn("#", fields)
        self.assertEqual(fields["a"], "b")

    def test_empty_block(self):
        fields, finding = read_frontmatter("---\n---\nRumpf")
        self.assertEqual(fields, {})
        self.assertIsNone(finding)


class TestBlockScalar(unittest.TestCase):
    """Real-world case from home-assistant-skills 0.1.0.

    The first attempt turned every indented line containing a colon into its
    own key and set description to ">".
    """

    TEXT = (
        "---\n"
        "name: beispiel\n"
        "description: >\n"
        "  Erste Zeile der Beschreibung.\n"
        "\n"
        "  TRIGGER THIS SKILL WHEN:\n"
        "  - Erster Fall\n"
        "  - Zweiter Fall\n"
        "user-invocable: false\n"
        "---\n"
        "Rumpf\n"
    )

    def test_no_phantom_key(self):
        fields, _ = read_frontmatter(self.TEXT)
        self.assertNotIn("TRIGGER THIS SKILL WHEN", fields)

    def test_description_contains_the_text(self):
        fields, _ = read_frontmatter(self.TEXT)
        self.assertIn("Erste Zeile der Beschreibung.", fields["description"])
        self.assertNotEqual(fields["description"], ">")

    def test_key_after_the_block_is_recognized(self):
        fields, _ = read_frontmatter(self.TEXT)
        self.assertIs(fields["user-invocable"], False)

    def test_pipe_variant(self):
        fields, _ = read_frontmatter("---\nd: |\n  Zeile eins\n  Zeile zwei\nx: y\n---\n")
        self.assertIn("Zeile eins", fields["d"])
        self.assertEqual(fields["x"], "y")

    def test_indented_continuation_without_marker(self):
        fields, _ = read_frontmatter("---\nd: Anfang\n  Fortsetzung\nx: y\n---\n")
        self.assertNotIn("Fortsetzung", fields)
        self.assertEqual(fields["x"], "y")


if __name__ == "__main__":
    unittest.main()
