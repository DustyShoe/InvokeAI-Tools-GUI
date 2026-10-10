"""Batch import, verified copy, and custom-board regression tests."""
from contextlib import closing
import io
from contextlib import redirect_stdout
from pathlib import Path
import sqlite3
from unittest.mock import patch
import unittest
from PIL import Image
import test_restore_layouts as layouts


class ImportTests(unittest.TestCase):
    schema = layouts.LayoutTests.schema
    image = layouts.LayoutTests.image
    rows = layouts.LayoutTests.rows
    run_restore = layouts.LayoutTests.run_restore
    def setUp(self):
        layouts.LayoutTests.setUp(self)
        self.schema()
        self.source = self.root / 'external'
        self.source.mkdir()

    def external(self, name='ab-image.png', color='blue'):
        p = self.source / name
        Image.new('RGB', (5, 5), color).save(p)
        return p

    def run_batch(self, *flags):
        with patch.object(self.module, 'import_strategy', return_value='flat'):
            return self.run_restore('--source-path', str(self.source), '--board-name', 'Batch A', *flags)

    def test_external_copy_and_existing_board(self):
        first = self.external()
        original = first.read_bytes()
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("INSERT INTO boards(board_id,board_name) VALUES('batch','Batch A')")
            c.commit()
        self.assertEqual(self.run_batch(),0)
        self.assertEqual((self.images/first.name).read_bytes(),original)
        self.assertEqual(first.read_bytes(),original)
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT board_id FROM board_images').fetchall(),[('batch',)])
        self.assertEqual(self.run_batch(),0)
        self.assertEqual(len(self.rows()),1)

    def test_external_dry_run_no_copy_or_db_writes(self):
        self.external()
        before=self.db.read_bytes()
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.run_batch('--dry-run'),0)
        self.assertIn('Would copy',output.getvalue())
        self.assertEqual(self.db.read_bytes(),before)
        self.assertEqual(list(self.images.rglob('*')),[])

    def test_conflict_skips_and_continues(self):
        self.external('conflict.png')
        existing=self.image('general/conflict.png')
        before=existing.read_bytes()
        self.external('good.png')
        self.assertEqual(self.run_batch(),0)
        self.assertEqual(existing.read_bytes(),before)
        self.assertEqual(self.rows(),[('good.png','')])

    def test_identical_nested_destination_reused(self):
        external=self.external()
        existing=self.images/'general'/external.name
        existing.parent.mkdir()
        existing.write_bytes(external.read_bytes())
        self.assertEqual(self.run_batch(),0)
        self.assertEqual(self.rows(),[(external.name,'general')])
        self.assertFalse((self.images/external.name).exists())

    def test_internal_batch_keeps_path(self):
        self.image('general/batch/sample.png')
        self.image('unselected.png')
        self.assertEqual(self.run_restore('--source-path',str(self.images/'general/batch'),'--board-name','Batch A'),0)
        self.assertEqual(self.rows(),[('sample.png','general/batch')])

    def test_stop_keeps_copy_rolls_back_all_db_changes(self):
        self.external('first.png')
        self.external('second.png')
        before=self.db.read_bytes()
        copied=[]
        original=self.module.copy_verified
        def copy(source,target):
            result=original(source,target)
            copied.append(target)
            return result
        # Trigger stop after the second copy; the first INSERT and board must roll back.
        def should_stop():
            return len(copied)>=2
        with patch.object(self.module,'copy_verified',copy), patch.object(self.module,'stop_requested',should_stop):
            with self.assertRaises(SystemExit) as error:
                self.run_batch()
        self.assertEqual(error.exception.code,130)
        self.assertEqual(self.db.read_bytes(),before)
        self.assertEqual(len(copied),2)
        for p in copied:
            with Image.open(p) as image:
                image.verify()
        self.assertEqual(self.run_batch(),0)
        self.assertEqual(len(self.rows()),2)

    def test_configured_external_layouts(self):
        for strategy in ('flat','date','type','hash'):
            name = f'ab-{strategy}.png'
            self.external(name)
            with patch.object(self.module,'import_strategy',return_value=strategy):
                self.assertEqual(self.run_restore('--source-path', str(self.source), '--board-name', strategy),0)
            expected = self.module.import_subfolder(strategy,name)
            self.assertIn((name,expected),self.rows())

    def test_config_strategy_read_from_yaml(self):
        (self.root/'invokeai.yaml').write_text('image_subfolder_strategy: type\n',encoding='utf-8')
        self.assertEqual(self.module.import_strategy(self.root),'type')

    def test_copy_failure_leaves_no_final_or_temporary_file(self):
        source=self.external()
        with patch.object(self.module,'file_digest',side_effect=[b'one',b'two']):
            with self.assertRaises(OSError):
                self.module.copy_verified(source,self.images/source.name)
        self.assertEqual(list(self.images.iterdir()),[])


if __name__=='__main__':
    unittest.main()
