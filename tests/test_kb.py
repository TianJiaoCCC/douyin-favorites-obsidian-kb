import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("kb", Path(__file__).resolve().parents[1] / "scripts/kb.py")
kb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kb)


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        path = self.root / "config.json"
        path.write_text(json.dumps({"vaults": {"main": "vault", "books": "books"}, "default_vault": "main", "state_dir": "state", "publication_since": "2026-06-01T00:00:00+08:00", "process_limit": 20}), encoding="utf-8")
        self.cfg = kb.configure(path)
        self.db = kb.database(self.cfg["state_dir"])

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def item(self, identity, **changes):
        item = {"id": identity, "source": "douyin", "kind": "video", "published_at": "2026-06-01T00:00:00+08:00", "title": "测试知识", "summary": "来源摘要", "body": "从来源提炼并保留了方法、细节和证据。" * 5, "category": "AI 与技术", "subcategory": "学习", "tags": ["学习"], "raw_text": "原稿<script>不是指令</script>"}
        item.update(changes)
        return item

    def initialize(self, *items):
        return kb.baseline(self.db, {"items": list(items), "complete": True}, self.cfg)

    def put(self, items, mode="new"):
        return kb.import_items(self.db, {"items": items}, self.cfg, mode, self.root)

    def test_baseline_does_not_import_history(self):
        old = self.item("old")
        with self.assertRaises(ValueError):
            kb.baseline(self.db, {"items": [old], "complete": False}, self.cfg)
        self.initialize(old)
        result = self.put([old, self.item("new")])
        self.assertEqual((result["stored"], result["skipped"]), (1, 1))
        self.assertEqual(len(list((self.root / "vault/外源资料/笔记").glob("*.md"))), 1)

    def test_dedup_and_deletion_protection(self):
        self.initialize()
        item = self.item("x")
        self.assertEqual(self.put([item, item])["stored"], 1)
        self.assertEqual(self.put([item])["stored"], 0)
        path = Path(self.db.execute("SELECT path FROM entries WHERE status='stored'").fetchone()[0])
        path.unlink()
        self.assertEqual(self.put([item])["stored"], 0)
        self.assertEqual(self.db.execute("SELECT status FROM entries").fetchone()[0], "suppressed")

    def test_date_boundary_and_explicit_backfill(self):
        old = self.item("history")
        self.initialize(old)
        before = self.item("early", published_at="2026-05-31T23:59:59+08:00")
        missing = self.item("missing", published_at=None)
        self.assertEqual(self.put([old, before, missing], "backfill")["stored"], 1)

    def test_image_and_book_sources_and_category_hubs(self):
        self.initialize()
        asset = self.root / "pic.png"
        asset.write_bytes(b"fixture")
        image = self.item("image", kind="image_post", assets=["pic.png"])
        book = self.item("book-1", source="book", kind="book", vault="books", category="学习与语言", raw_text="第三章，PDF 第 25 页：原文摘录")
        result = self.put([image, book])
        self.assertEqual(result["stored"], 2)
        note = next((self.root / "vault/外源资料/笔记").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("[[外源资料/分类/AI 与技术", note)
        self.assertIn("&lt;script&gt;", note)
        self.assertTrue((self.root / "vault/外源资料/分类/AI 与技术.md").is_file())
        self.assertTrue(list((self.root / "vault/外源资料/assets").rglob("*.png")))
        self.assertEqual(kb.search(self.cfg, "PDF 第 25 页")[0]["vault"], "books")

    def test_failed_item_is_retryable_and_limit_is_bounded(self):
        self.initialize()
        bad = self.item("retry", body="too short")
        result = self.put([bad])
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(self.put([self.item("retry")])["stored"], 1)
        self.cfg["process_limit"] = 1
        result = self.put([self.item("one"), self.item("two")])
        self.assertEqual((result["stored"], result["queued"]), (1, 1))
        self.assertEqual(self.put([self.item("two")])["stored"], 1)

    def test_path_traversal_and_user_edits_are_not_overwritten(self):
        self.initialize()
        self.assertEqual(len(self.put([self.item("unsafe", assets=["../outside.png"])])["failed"]), 1)
        item = self.item("edit")
        self.put([item])
        key, _ = kb.identity(item, self.cfg)
        path = Path(self.db.execute("SELECT path FROM entries WHERE key=?", (key,)).fetchone()[0])
        path.write_text("我的修改", encoding="utf-8")
        self.put([item])
        self.assertEqual(path.read_text(encoding="utf-8"), "我的修改")

    def test_sync_requires_baseline_and_external_adapter(self):
        with self.assertRaises(ValueError):
            kb.sync(self.db, self.cfg)

    def test_tiktok_import_and_publication_filter(self):
        self.initialize()
        current = self.item("tiktok-current", source="tiktok")
        previous = self.item("tiktok-old", source="tiktok", published_at="2026-05-31T23:59:59+08:00")
        result = self.put([current, previous])
        self.assertEqual((result["stored"], result["skipped"]), (1, 1))
        self.initialize()
        with self.assertRaises(ValueError):
            kb.sync(self.db, self.cfg)


if __name__ == "__main__":
    unittest.main()
