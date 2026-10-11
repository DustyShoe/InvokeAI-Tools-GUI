"""Imported images have InvokeAI-compatible, safely published thumbnails."""
from contextlib import closing, redirect_stdout
import io
from pathlib import Path
import sqlite3
from unittest.mock import patch
import unittest
from PIL import Image
import test_batch_import as batch


class ThumbnailTests(unittest.TestCase):
    schema = batch.ImportTests.schema
    image = batch.ImportTests.image
    rows = batch.ImportTests.rows
    external = batch.ImportTests.external
    run_restore = batch.ImportTests.run_restore
    run_batch = batch.ImportTests.run_batch
    setUp = batch.ImportTests.setUp

    def test_external_import_creates_webp_and_preserves_original(self):
        source = self.source / 'example.png'
        Image.new('RGB', (1024, 512), 'blue').save(source)
        original = source.read_bytes()
        self.assertEqual(self.run_batch(), 0)
        thumbnail = self.images / 'thumbnails/example.webp'
        with Image.open(thumbnail) as image:
            self.assertEqual(image.format, 'WEBP')
            self.assertEqual(image.size, (256, 128))
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual((self.images / source.name).read_bytes(), original)
        self.assertEqual(list(self.images.rglob('*.tmp')), [])

    def test_nested_reused_file_gets_matching_thumbnail_subfolder(self):
        source = self.external()
        target = self.images / 'general/batch' / source.name
        target.parent.mkdir(parents=True)
        target.write_bytes(source.read_bytes())
        self.assertEqual(self.run_batch(), 0)
        self.assertTrue((self.images / 'thumbnails/general/batch' / source.with_suffix('.webp').name).is_file())
        self.assertEqual(self.rows(), [(source.name, 'general/batch')])

    def test_repeat_restore_repairs_thumbnail_for_existing_metadata_record(self):
        image = self.image('general/existing.png')
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("INSERT INTO images(image_name, metadata, image_subfolder) VALUES('existing.png','{}','general')")
            c.commit()
        before = self.db.read_bytes()
        self.assertEqual(self.run_restore(), 0)
        thumbnail = self.images / 'thumbnails/general/existing.webp'
        self.assertTrue(thumbnail.is_file())
        self.assertEqual(self.db.read_bytes(), before)
        thumbnail_before = thumbnail.read_bytes()
        with patch.object(self.module.PIL.Image, 'open', side_effect=AssertionError('existing preview must be reused')):
            self.assertEqual(self.run_restore(), 0)
        self.assertEqual(thumbnail.read_bytes(), thumbnail_before)

    def test_dry_run_plans_thumbnails_without_creating_files(self):
        self.external()
        with redirect_stdout(io.StringIO()) as log:
            self.assertEqual(self.run_batch('--dry-run'), 0)
        self.assertIn('Would create thumbnail:', log.getvalue())
        self.assertEqual(list(self.images.iterdir()), [])
        self.assertEqual(self.rows(), [])

    def test_transparency_and_small_images(self):
        source = self.source / 'transparent.png'
        Image.new('RGBA', (20, 10), (255, 0, 0, 0)).save(source)
        self.assertEqual(self.run_batch(), 0)
        with Image.open(self.images / 'thumbnails/transparent.webp') as thumbnail:
            self.assertEqual(thumbnail.size, (20, 10))
            self.assertEqual(thumbnail.getpixel((0, 0))[3], 0)

    def test_stop_finishes_thumbnail_then_rolls_back_database(self):
        self.external('first.png')
        self.external('second.png')
        marker = self.root / 'stop'
        before = self.db.read_bytes()
        original = self.module.ensure_thumbnail
        completed = []
        def make(*args, **kwargs):
            result = original(*args, **kwargs)
            completed.append(args[0])
            if len(completed) == 2:
                marker.touch()
            return result
        import os
        with patch.dict(os.environ, INVOKEAI_TOOLS_STOP_FILE=str(marker)), patch.object(self.module, 'ensure_thumbnail', make):
            with self.assertRaises(SystemExit) as result:
                self.run_batch()
        self.assertEqual(result.exception.code, 130)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(len(list((self.images / 'thumbnails').glob('*.webp'))), 2)
        for path in (self.images / 'thumbnails').glob('*.webp'):
            with Image.open(path) as image:
                image.verify()
        self.assertEqual(list(self.images.rglob('*.tmp')), [])
        self.assertEqual(self.run_batch(), 0)
        self.assertEqual(len(self.rows()), 2)

    def test_thumbnail_write_error_does_not_register_broken_import(self):
        source = self.external()
        original_save = self.module.PIL.Image.Image.save
        def save(image, stream, *args, **kwargs):
            if kwargs.get('format') == 'WEBP':
                stream.write(b'partial preview')
                raise OSError('disk full')
            return original_save(image, stream, *args, **kwargs)
        with patch.object(self.module.PIL.Image.Image, 'save', save):
            self.assertEqual(self.run_batch(), 2)
        self.assertEqual(self.rows(), [])
        self.assertFalse((self.images / 'thumbnails' / source.with_suffix('.webp').name).exists())
        self.assertEqual(list(self.images.rglob('*.tmp')), [])
        self.assertEqual((self.images / source.name).read_bytes(), source.read_bytes())


if __name__ == '__main__':
    unittest.main()
