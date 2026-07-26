"""
P1 功能 15 备份/恢复：zip 往返 + 维度/模型校验 + 可撤销 测试。

设计文档 §2 / §5 P1-15.1 要求本套件覆盖：
- backup_index 产出含 meta.json 的 zip（app_version/model/embedding_dim/sources）；
- restore_index 校验模型 / 维度，不一致抛 BackupIncompatible，绝不静默覆盖；
- overwrite 默认先自动备份当前索引为 <base>_pre_restore_<ts>.zip（可撤销）再覆盖；
- merge 模式按文件名 upsert（不删除目标原有文件）；
- 缺 meta.json 的 zip 应判为非法并抛 BackupIncompatible。

纯 stdlib（zipfile）+ 临时目录模拟 db_path（chromadb/ + tags.json + lexical_index.json
+ index_meta.json），无需真实 embedding / GUI，独立于其它套件运行。
"""

import os
import sys
import json
import tempfile
import zipfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.backup import (
    backup_index,
    restore_index,
    BackupMeta,
    BackupIncompatible,
    APP_VERSION,
)


def seed_db(db_path: str):
    """在 db_path 下造一批相当于 chromadb + 索引文件的目录结构。"""
    os.makedirs(os.path.join(db_path, "chromadb"), exist_ok=True)
    Path(os.path.join(db_path, "chromadb", "a.bin")).write_bytes(b"\x00\x01")
    Path(os.path.join(db_path, "tags.json")).write_text(
        json.dumps({"file_tags": {"/x.md": ["t"]}}), encoding="utf-8"
    )
    Path(os.path.join(db_path, "lexical_index.json")).write_text("{}", encoding="utf-8")
    Path(os.path.join(db_path, "index_meta.json")).write_text("{}", encoding="utf-8")


class TestBackupRoundTrip(unittest.TestCase):
    def setUp(self):
        self.src = tempfile.mkdtemp()
        seed_db(self.src)
        self.zip_path = os.path.join(tempfile.mkdtemp(), "bak.zip")
        self.meta = BackupMeta(model="M", embedding_dim=8, sources=[{"path": "/d"}])

    def test_backup_creates_meta(self):
        p = backup_index(self.src, self.zip_path, self.meta)
        self.assertTrue(os.path.isfile(p))
        with zipfile.ZipFile(p) as zf:
            self.assertIn("meta.json", zf.namelist())
            m = json.loads(zf.read("meta.json"))
            self.assertEqual(m["model"], "M")
            self.assertEqual(m["embedding_dim"], 8)
            self.assertEqual(m["app_version"], APP_VERSION)
            self.assertEqual(m["sources"], [{"path": "/d"}])

    def test_restore_roundtrip(self):
        backup_index(self.src, self.zip_path, self.meta)
        dest = tempfile.mkdtemp()
        res = restore_index(self.zip_path, dest, mode="overwrite", current_meta=self.meta)
        self.assertTrue(res["restored"])
        # 关键文件应被还原
        self.assertTrue(os.path.isfile(os.path.join(dest, "tags.json")))
        self.assertTrue(os.path.isdir(os.path.join(dest, "chromadb")))
        # zip 内文件数（除 meta.json 外）应 >= 种子文件数 4
        with zipfile.ZipFile(self.zip_path) as zf:
            n = len([nm for nm in zf.namelist() if nm != "meta.json"])
        self.assertGreaterEqual(res["files"], 4)
        self.assertEqual(n, res["files"])

    def test_overwrite_creates_revocable_backup(self):
        # dest 已有不同内容 → 覆盖前应生成可撤销的 pre_restore 备份
        dest = tempfile.mkdtemp()
        seed_db(dest)
        # 让 dest 的 tags.json 与 src 内容不同，便于断言“确实被备份内容覆盖”
        orig_tags = json.dumps({"file_tags": {"/other.md": ["zzz"]}}, ensure_ascii=False)
        Path(os.path.join(dest, "tags.json")).write_text(orig_tags, encoding="utf-8")
        backup_index(self.src, self.zip_path, self.meta)
        restore_index(self.zip_path, dest, mode="overwrite", current_meta=self.meta)
        parent = os.path.dirname(os.path.abspath(dest))
        base = os.path.basename(dest.rstrip("/\\"))
        backups = [f for f in os.listdir(parent) if f.startswith(base + "_pre_restore_")]
        self.assertTrue(backups, "overwrite 应生成可撤销的 pre_restore 备份")
        # 还原后 dest 的 tags.json 应来自备份（与 orig 不同）
        self.assertNotEqual(
            Path(os.path.join(dest, "tags.json")).read_text(encoding="utf-8"), orig_tags
        )

    def test_merge_keeps_existing_files(self):
        backup_index(self.src, self.zip_path, self.meta)
        dest = tempfile.mkdtemp()
        Path(os.path.join(dest, "extra.txt")).write_text("keep", encoding="utf-8")
        res = restore_index(self.zip_path, dest, mode="merge", current_meta=self.meta)
        self.assertTrue(res["restored"])
        # merge 不应删除目标原有文件
        self.assertTrue(os.path.isfile(os.path.join(dest, "extra.txt")))


class TestBackupIncompatible(unittest.TestCase):
    def setUp(self):
        self.src = tempfile.mkdtemp()
        seed_db(self.src)
        self.zip_path = os.path.join(tempfile.mkdtemp(), "bak.zip")

    def test_model_mismatch_raises(self):
        backup_index(self.src, self.zip_path, BackupMeta(model="A", embedding_dim=8))
        with self.assertRaises(BackupIncompatible):
            restore_index(
                self.zip_path, tempfile.mkdtemp(),
                current_meta=BackupMeta(model="B", embedding_dim=8),
            )

    def test_dim_mismatch_raises(self):
        backup_index(self.src, self.zip_path, BackupMeta(model="M", embedding_dim=8))
        with self.assertRaises(BackupIncompatible):
            restore_index(
                self.zip_path, tempfile.mkdtemp(),
                current_meta=BackupMeta(model="M", embedding_dim=16),
            )

    def test_missing_meta_raises(self):
        bad = os.path.join(tempfile.mkdtemp(), "bad.zip")
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("foo.txt", "bar")
        with self.assertRaises(BackupIncompatible):
            restore_index(bad, tempfile.mkdtemp())

    def test_compatible_passes(self):
        backup_index(self.src, self.zip_path, BackupMeta(model="M", embedding_dim=8))
        dest = tempfile.mkdtemp()
        res = restore_index(
            self.zip_path, dest, current_meta=BackupMeta(model="M", embedding_dim=8)
        )
        self.assertTrue(res["restored"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
