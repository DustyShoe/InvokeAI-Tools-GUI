"""Directory layout recovery checks using temporary files only."""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from test_cancellation import load


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.images = self.root / 'images'
        self.images.mkdir()
        self.db = self.root / 'test.db'
        self.module = load('scripts/Restore_Images_DB_v3.1.py', 'restore_layout')

    def schema(self, modern=True):
        extra = ", image_subfolder TEXT NOT NULL DEFAULT ''" if modern else ''
        with closing(sqlite3.connect(self.db)) as c:
            c.executescript('CREATE TABLE images(image_name TEXT PRIMARY KEY, image_origin TEXT, image_category TEXT, width INT, height INT, metadata TEXT, is_intermediate INT, created_at TEXT, updated_at TEXT' + extra + '); CREATE TABLE boards(board_id TEXT PRIMARY KEY, board_name TEXT, created_at TEXT); CREATE TABLE board_images(board_id TEXT, image_name TEXT);')

    def image(self, relative):
        path = self.images / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (4, 4), 'red').save(path)
        return path

    def run_restore(self, *flags):
        args = ['restore', '--db-path', str(self.db), '--outputs-path', str(self.images), '--no-backup', *flags]
        with patch.object(sys, 'argv', args):
            return self.module.main()

    def rows(self):
        with closing(sqlite3.connect(self.db)) as c:
            return c.execute('SELECT image_name, image_subfolder FROM images ORDER BY image_name').fetchall()

    def test_mixed_layout_import(self):
        self.schema()
        paths = ['flat.png', '2026/10/10/date.png', 'general/type.png', 'ab/hash.png']
        originals = {self.image(p): (self.images / p).read_bytes() for p in paths}
        self.image('thumbnails/ignored.png')
        self.assertEqual(self.run_restore(), 0)
        self.assertEqual(self.rows(), [('date.png', '2026/10/10'), ('flat.png', ''), ('hash.png', 'ab'), ('type.png', 'general')])
        for path, content in originals.items():
            self.assertEqual(path.read_bytes(), content)

    def test_existing_paths_repaired_even_with_metadata(self):
        self.schema()
        self.image('general/moved.png')
        self.image('flat.png')
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("INSERT INTO images(image_name, metadata, image_subfolder) VALUES('moved.png','{}','old')")
            c.execute("INSERT INTO images(image_name, metadata, image_subfolder) VALUES('flat.png','{}','general')")
            c.commit()
        before = self.db.read_bytes()
        self.assertEqual(self.run_restore('--dry-run'), 0)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(self.run_restore(), 0)
        self.assertEqual(self.rows(), [('flat.png', ''), ('moved.png', 'general')])

    def test_legacy_schema_flat_only(self):
        self.schema(modern=False)
        self.image('general/nested.png')
        before = self.db.read_bytes()
        self.assertEqual(self.run_restore(), 1)
        self.assertEqual(self.db.read_bytes(), before)
        (self.images / 'general/nested.png').unlink()
        self.image('flat.png')
        self.assertEqual(self.run_restore(), 0)

    def test_duplicate_names_blocked_before_limit(self):
        self.schema()
        self.image('a/same.png')
        self.image('b/same.png')
        before = self.db.read_bytes()
        self.assertEqual(self.run_restore('--limit', '1'), 1)
        self.assertEqual(self.db.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
