import os
import sys
import tempfile
import unicodedata
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # noqa: F401  removes this run's temp directories
from inventory.reading import read_json, clean_name, read_safely, too_deep

# Never write these as literals, always construct them: two identical-looking
# literals in the source are both NFC, so comparing them proves nothing.
# The first attempt at this test was green and worthless for exactly that reason.
WORD_NFC = unicodedata.normalize("NFC", "prüfen")
WORD_NFD = unicodedata.normalize("NFD", "prüfen")


class TestReadSafely(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _write(self, name, content):
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return p

    def test_normal_file(self):
        p = self._write("a.json", '{"x": 1}')
        text, finding = read_safely(p)
        self.assertIsNone(finding)
        self.assertEqual(text, '{"x": 1}')

    def test_too_large(self):
        p = self._write("gross.json", "x" * (1024 * 1024 + 10))
        text, finding = read_safely(p)
        self.assertIsNone(text)
        self.assertEqual(finding, "file-too-large")

    def test_symlink_is_not_opened(self):
        target = self._write("ziel.json", "{}")
        link = os.path.join(self.dir, "link.json")
        os.symlink(target, link)
        text, finding = read_safely(link)
        self.assertIsNone(text)
        self.assertEqual(finding, "symlink")

    def test_missing_file(self):
        text, finding = read_safely(os.path.join(self.dir, "weg.json"))
        self.assertIsNone(text)
        self.assertEqual(finding, "no-read-permission")

    def test_broken_encoding_does_not_crash(self):
        p = os.path.join(self.dir, "latin.md")
        with open(p, "wb") as f:
            f.write(b"Gr\xfc\xdfe")
        text, finding = read_safely(p)
        self.assertIsNone(finding)
        self.assertIn("Gr", text)


class TestTooDeep(unittest.TestCase):
    def test_shallow_is_ok(self):
        self.assertFalse(too_deep('{"a": [1, 2, {"b": 3}]}'))

    def test_deep_is_detected(self):
        self.assertTrue(too_deep("[" * 200 + "]" * 200))

    def test_brace_inside_string_does_not_count(self):
        self.assertFalse(too_deep('{"a": "' + "{" * 200 + '"}'))

    def test_escaped_quote(self):
        self.assertFalse(too_deep('{"a": "x\\"' + "{" * 5 + '"}'))


class TestCleanName(unittest.TestCase):
    def test_precondition_nfd_differs_from_nfc(self):
        # Without this guarantee the test below could be trivially green.
        self.assertNotEqual(WORD_NFD, WORD_NFC)
        self.assertEqual(len(WORD_NFD), 7)
        self.assertEqual(len(WORD_NFC), 6)

    def test_nfd_becomes_nfc(self):
        self.assertEqual(clean_name(WORD_NFD), WORD_NFC)

    def test_already_nfc_stays(self):
        self.assertEqual(clean_name(WORD_NFC), WORD_NFC)

    def test_surrogate_is_replaced(self):
        result = clean_name("broken\udcff")
        self.assertIsInstance(result, str)
        self.assertNotIn("\udcff", result)
        result.encode("utf-8")  # must not raise


class TestReadJson(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_valid_json(self):
        p = os.path.join(self.dir, "g.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write('{"a": 1}')
        data, finding = read_json(p)
        self.assertIsNone(finding)
        self.assertEqual(data, {"a": 1})

    def test_invalid_json(self):
        p = os.path.join(self.dir, "u.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{broken")
        data, finding = read_json(p)
        self.assertIsNone(data)
        self.assertEqual(finding, "invalid-json")

    def test_json_nested_too_deeply(self):
        p = os.path.join(self.dir, "t.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("[" * 200 + "]" * 200)
        data, finding = read_json(p)
        self.assertIsNone(data)
        self.assertEqual(finding, "nesting-too-deep")


if __name__ == "__main__":
    unittest.main()
