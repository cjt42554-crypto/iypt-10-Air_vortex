"""Small independent cases for the entry-document checker."""
import tempfile
import unittest
from pathlib import Path
from check_repository_docs import check_document

class EntryDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "目标.md").write_text("# Target\n", encoding="utf-8")
    def check(self, text):
        (self.root / "entry.md").write_text(text, encoding="utf-8")
        return check_document(self.root, "entry.md")
    def test_valid_unicode_and_encoded_link(self):
        self.assertEqual(self.check("[a](目标.md) [b](%E7%9B%AE%E6%A0%87.md#x)\n"), [])
    def test_code_label_is_checked(self):
        self.assertTrue(self.check("[`a`](missing.md)\n"))
    def test_inline_and_fenced_examples_are_ignored(self):
        self.assertEqual(self.check("`[a](missing.md)`\n\n```text\n[a](missing.md)\n```\n"), [])
    def test_external_and_anchor_links(self):
        self.assertEqual(self.check("[a](https://example.invalid/x) [b](#heading)\n"), [])
    def test_missing_target(self):
        self.assertTrue(self.check("[a](missing.md)\n"))
    def test_escape(self):
        self.assertTrue(self.check("[a](../outside.md)\n"))
    def test_unclosed_or_wrong_fence(self):
        self.assertTrue(self.check("```text\n~~~\n"))
    def test_missing_entry(self):
        self.assertTrue(check_document(self.root, "absent.md"))
    def test_final_newline(self):
        self.assertTrue(self.check("# Missing newline"))

if __name__ == "__main__":
    unittest.main()
