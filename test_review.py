"""Regression checks for issues found during the release review."""
from contextlib import closing, redirect_stdout
import io
import sqlite3
from unittest.mock import patch

import test_batch_import as batch
from test_cancellation import load
import unittest


class ReviewTests(unittest.TestCase):
    schema = batch.ImportTests.schema
    image = batch.ImportTests.image
    rows = batch.ImportTests.rows
    run_restore = batch.ImportTests.run_restore
    external = batch.ImportTests.external
    run_batch = batch.ImportTests.run_batch
    setUp = batch.ImportTests.setUp

    def test_backup_includes_committed_wal_and_has_unique_name(self):
        with closing(sqlite3.connect(self.db)) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute("INSERT INTO boards(board_id,board_name) VALUES('wal','WAL board')")
            writer.commit()
            for _ in range(2):
                self.module.backup_db(self.db, self.root / 'backup')
            backups = list((self.root / 'backup').glob('*.db'))
            self.assertEqual(len(backups), 2)
            for backup in backups:
                with closing(sqlite3.connect(backup)) as c:
                    self.assertEqual(c.execute('SELECT board_name FROM boards').fetchall(), [('WAL board',)])
                    self.assertEqual(c.execute('PRAGMA integrity_check').fetchone(), ('ok',))

    def test_legacy_database_rejects_reused_nested_destination(self):
        self.db.unlink()
        self.schema(modern=False)
        source = self.external()
        target = self.images / 'general' / source.name
        target.parent.mkdir()
        target.write_bytes(source.read_bytes())
        with redirect_stdout(io.StringIO()) as log:
            self.assertEqual(self.run_batch(), 2)
        self.assertIn('no image_subfolder column', log.getvalue())
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM images').fetchone(), (0,))

    def test_failed_image_does_not_inflate_db_statistics(self):
        self.external('bad.png')
        self.external('good.png')
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("CREATE TRIGGER reject_bad BEFORE INSERT ON board_images WHEN NEW.image_name='bad.png' BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with redirect_stdout(io.StringIO()) as log:
            self.assertEqual(self.run_batch(), 2)
        self.assertEqual(self.rows(), [('good.png', '')])
        self.assertIn('Inserted             : 1', log.getvalue())
        self.assertIn('Files copied/planned : 2', log.getvalue())
        self.assertEqual(log.getvalue().count('Created board         : Batch A'), 1)

    def test_board_resolution_prefers_exact_and_rejects_ambiguity(self):
        for script in ('Convert_Board_to_Assets_v1.1.py', 'Convert_Assets_to_Board_v1.1.py'):
            module = load('scripts/' + script, 'review_conversion')
            with self.subTest(script=script), closing(sqlite3.connect(':memory:')) as c:
                c.executescript("CREATE TABLE boards(board_id TEXT,board_name TEXT); INSERT INTO boards VALUES('one','Characters'),('two','characters');")
                self.assertEqual(module.get_board_id_by_name(c, 'characters'), 'two')
                with self.assertRaisesRegex(SystemExit, 'ambiguous'):
                    module.get_board_id_by_name(c, 'CHARACTERS')
                c.execute("INSERT INTO boards VALUES('three','characters')")
                with self.assertRaisesRegex(SystemExit, 'ambiguous'):
                    module.get_board_id_by_name(c, 'characters')

    def test_invalid_yaml_does_not_fall_back_to_default_database(self):
        config = self.root / 'invokeai.yaml'
        config.write_text('InvokeAI: invalid\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.module.ConfigMapper()._load_paths_from_yaml_file(config)

    def test_png_disappears_without_aborting_remaining_files(self):
        module = load('scripts/png_recompress_level9_v1.0.py', 'review_png')
        first = self.image('first.png')
        second = self.image('second.png')
        original = module.process_file
        completed = []
        def process(path, **kwargs):
            if path == first:
                path.unlink()
                raise FileNotFoundError('file removed during scan')
            completed.append(path)
            return original(path, **kwargs)
        with patch.object(module, 'parse_args') as args, patch.object(module, 'process_file', process):
            from types import SimpleNamespace
            args.return_value = SimpleNamespace(path=self.images, no_recursive=False, dry_run=False, rewrite_if_different=False, engine='pil', zip_text_metadata=False, verbose=False)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(module.main(), 2)
        self.assertEqual(completed, [second])

    def test_png_16_bit_rgb_keeps_original_sample_precision(self):
        import struct
        import zlib
        module = load('scripts/png_recompress_level9_v1.0.py', 'review_png_16')
        # Two RGB pixels with values that cannot survive an 8-bit conversion.
        scanlines = b'\x00' + struct.pack('>6H', 1, 257, 65534, 1000, 30000, 50000)
        payload = module.PNG_SIGNATURE
        payload += module.pack_chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 1, 16, 2, 0, 0, 0))
        payload += module.pack_chunk(b'tEXt', b'invokeai_metadata\x00{}')
        payload += module.pack_chunk(b'IDAT', zlib.compress(scanlines, level=0))
        payload += module.pack_chunk(b'IEND', b'')
        path = self.images / '16-bit.png'
        path.write_bytes(payload)
        result = module.process_file(path, False, False, 'pil', False)
        self.assertEqual(result.status, 'rewritten')
        chunks = module.read_chunks(path.read_bytes())
        self.assertEqual(zlib.decompress(b''.join(c.data for c in chunks if c.chunk_type == b'IDAT')), scanlines)
        self.assertEqual([c for c in chunks if c.chunk_type != b'IDAT'],
                         [c for c in module.read_chunks(payload) if c.chunk_type != b'IDAT'])

    def test_png_write_failure_cleans_temp_and_preserves_original(self):
        module = load('scripts/png_recompress_level9_v1.0.py', 'review_png_write')
        path = self.image('original.png')
        original = path.read_bytes()
        with patch.object(module.os, 'fsync', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                module.rewrite_file(path, b'replacement', path.stat())
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.images.iterdir()), [path])


if __name__ == '__main__':
    unittest.main()
