"""Repair #9504 against disposable SQLite databases only."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from test_cancellation import load


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'invokeai.db'
        self.module = load('scripts/Repair_Image_Deletion.py', 'repair_9504')
        with closing(sqlite3.connect(self.db)) as c:
            c.executescript("CREATE TABLE images(image_name TEXT PRIMARY KEY); CREATE TABLE image_subfolder_move_jobs(id INTEGER PRIMARY KEY,state TEXT); INSERT INTO images VALUES('example.png'); INSERT INTO image_subfolder_move_jobs VALUES(1,'committed');")
            c.execute(self.module.CREATE.replace('_repair_9504','').replace(' ON DELETE CASCADE',''))
            c.execute("INSERT INTO image_subfolder_move_items(job_id,image_name,old_subfolder,new_subfolder,state) VALUES(1,'example.png','','general','committed')")
            c.execute('CREATE INDEX test_move_index ON image_subfolder_move_items(image_name)')
            c.commit()

    def test_repair_preserves_records_and_allows_delete(self):
        self.assertEqual(self.module.repair(self.db),0)
        backups=list((self.db.parent/'backup').glob('*.db'))
        self.assertEqual(len(backups),1)
        with closing(sqlite3.connect(self.db)) as c:
            c.execute('PRAGMA foreign_keys=ON')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM images').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM image_subfolder_move_items').fetchone()[0],1)
            self.assertIsNotNone(c.execute("SELECT 1 FROM sqlite_master WHERE name='test_move_index'").fetchone())
            c.execute("DELETE FROM images WHERE image_name='example.png'")
            self.assertEqual(c.execute('SELECT COUNT(*) FROM image_subfolder_move_items').fetchone()[0],0)
            c.rollback()
        before=self.db.read_bytes()
        self.assertEqual(self.module.repair(self.db),0)
        self.assertEqual(self.db.read_bytes(),before)
        self.assertEqual(len(list((self.db.parent/'backup').glob('*.db'))),1)

    def test_dry_run_does_not_write(self):
        before=self.db.read_bytes()
        self.assertEqual(self.module.repair(self.db,True),0)
        self.assertEqual(self.db.read_bytes(),before)
        self.assertFalse((self.db.parent/'backup').exists())

    def test_unfinished_jobs_block_repair(self):
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("UPDATE image_subfolder_move_jobs SET state='moving'")
            c.commit()
        before=self.db.read_bytes()
        with self.assertRaisesRegex(ValueError,'Unfinished'):
            self.module.repair(self.db)
        self.assertEqual(self.db.read_bytes(),before)

    def test_stop_after_drop_restores_original_schema(self):
        original_connect=sqlite3.connect
        dropped=False
        def trace(sql):
            nonlocal dropped
            if sql.startswith('DROP TABLE image_subfolder_move_items'):
                dropped=True
        def connect(*args,**kwargs):
            c=original_connect(*args,**kwargs)
            c.set_trace_callback(trace)
            return c
        def checkpoint():
            if dropped:
                raise SystemExit(130)
        before=self.db.read_bytes()
        with patch.object(self.module.sqlite3,'connect',connect), patch.object(self.module,'checkpoint',checkpoint):
            with self.assertRaises(SystemExit) as result:
                self.module.repair(self.db)
        self.assertEqual(result.exception.code,130)
        self.assertEqual(self.db.read_bytes(),before)
        with closing(original_connect(self.db)) as c:
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')


if __name__=='__main__':
    unittest.main()
