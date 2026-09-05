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


class LayoutAndMigrationTests(unittest.TestCase):
    def test_required_layout_and_empty_history(self):
        from check_repository_docs import check_layout, REQUIRED_DIRS
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in REQUIRED_DIRS:
                (root / name).mkdir()
            (root / "00_项目管理/交接说明_history.md").write_bytes(b"")
            self.assertEqual(check_layout(root), [])
            (root / "00_项目管理/交接说明_history.md").write_text("restored")
            self.assertTrue(check_layout(root))
    def test_missing_layout_is_reported(self):
        from check_repository_docs import check_layout
        with tempfile.TemporaryDirectory() as temp:
            self.assertTrue(check_layout(Path(temp)))
    def test_old_active_workspace_is_not_recreated(self):
        from check_repository_docs import check_layout, REQUIRED_DIRS
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in REQUIRED_DIRS:
                (root / name).mkdir()
            (root / "00_项目管理/交接说明_history.md").write_bytes(b"")
            (root / "90_候选工作区").mkdir()
            self.assertTrue(check_layout(root))
    def test_nested_program_readmes_are_checked_not_frozen_records(self):
        from check_repository_docs import entry_documents
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("02_模拟程序/pkg/README.md", "03_研究记录/归档/README.md"):
                p = root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("# Entry\n")
            docs = entry_documents(root)
            self.assertIn("02_模拟程序/pkg/README.md", docs)
            self.assertNotIn("03_研究记录/归档/README.md", docs)
    def test_exact_and_longest_prefix_mapping(self):
        from resolve_legacy_path import resolve_legacy
        maps = [{"old":"old/current", "new":"records/topics"},
                {"old":"old/current/date", "new":"records/maintenance/date"}]
        self.assertEqual(resolve_legacy("old/current/a.txt",maps), "records/topics/a.txt")
        self.assertEqual(resolve_legacy("./old/current/date/a.txt",maps), "records/maintenance/date/a.txt")
    def test_mapping_boundary_is_not_substring_replacement(self):
        from resolve_legacy_path import resolve_legacy
        with self.assertRaises(ValueError):
            resolve_legacy("old/currently/a", [{"old":"old/current","new":"new"}])
    def test_mapping_rejects_escape(self):
        from resolve_legacy_path import resolve_legacy
        for bad in ("../old/current/a", "/old/current/a"):
            with self.assertRaises(ValueError):
                resolve_legacy(bad, [{"old":"old/current", "new":"new"}])

if __name__ == "__main__":
    unittest.main()
