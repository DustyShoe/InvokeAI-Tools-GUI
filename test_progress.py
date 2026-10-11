"""Progress counters across scripts and the GUI worker transport."""
from contextlib import closing, redirect_stdout
import io
import os
from pathlib import Path
import queue
import sqlite3
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import app
import test_restore_layouts as layouts
from test_cancellation import load


class ProgressTests(unittest.TestCase):
    schema = layouts.LayoutTests.schema
    image = layouts.LayoutTests.image
    run_restore = layouts.LayoutTests.run_restore

    def setUp(self):
        layouts.LayoutTests.setUp(self)
        self.schema()
        environment = patch.dict(os.environ, INVOKEAI_TOOLS_PROGRESS='1')
        environment.start()
        self.addCleanup(environment.stop)

    def counts(self, log):
        return [parsed for line in log.splitlines()
                if (parsed := app.parse_progress_line(line)) is not None]

    def test_restore_counts_skips_and_obeys_limit(self):
        self.image('a.png')
        self.image('b.png')
        self.image('c.png')
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("INSERT INTO images(image_name, metadata) VALUES('a.png','{}')")
            connection.commit()
        with redirect_stdout(io.StringIO()) as log:
            self.assertEqual(self.run_restore('--limit', '2', '--dry-run'), 0)
        self.assertEqual(self.counts(log.getvalue()), [(0, 2), (1, 2), (2, 2)])

    def test_restore_counts_file_errors_and_continues(self):
        self.image('good.png')
        (self.images / 'bad.png').write_bytes(b'broken image')
        with redirect_stdout(io.StringIO()) as log:
            self.assertEqual(self.run_restore(), 2)
        self.assertEqual(self.counts(log.getvalue()), [(0, 2), (1, 2), (2, 2)])

    def test_restore_interrupted_copy_is_not_counted_as_processed(self):
        source = self.root / 'source'
        source.mkdir()
        from PIL import Image
        Image.new('RGB', (4, 4)).save(source / 'external.png')
        marker = self.root / 'stop'
        original = self.module.copy_verified
        def copy(*args):
            result = original(*args)
            marker.touch()
            return result
        with patch.dict(os.environ, INVOKEAI_TOOLS_STOP_FILE=str(marker)), patch.object(self.module, 'copy_verified', copy):
            with redirect_stdout(io.StringIO()) as log:
                with self.assertRaises(SystemExit) as result:
                    self.run_restore('--source-path', str(source))
        self.assertEqual(result.exception.code, 130)
        self.assertEqual(self.counts(log.getvalue()), [(0, 1)])

    def png(self, *flags):
        module = load('scripts/png_recompress_level9_v1.0.py', 'progress_png')
        with patch.object(sys, 'argv', ['png', str(self.images), *flags]):
            return module.main()

    def test_png_reports_actual_completed_files_including_errors(self):
        self.image('good.png')
        (self.images / 'bad.png').write_bytes(b'broken image')
        for flags in ([], ['--dry-run']):
            with self.subTest(flags=flags), redirect_stdout(io.StringIO()) as log:
                self.assertEqual(self.png(*flags), 2)
            self.assertEqual(self.counts(log.getvalue()), [(0, 2), (1, 2), (2, 2)])

    def test_empty_scan_reports_zero_total(self):
        with redirect_stdout(io.StringIO()) as log:
            self.assertEqual(self.run_restore('--dry-run'), 0)
        self.assertEqual(self.counts(log.getvalue()), [(0, 0)])
        with redirect_stdout(io.StringIO()) as log:
            self.assertEqual(self.png('--dry-run'), 1)
        self.assertEqual(self.counts(log.getvalue()), [(0, 0)])

    def test_cli_does_not_emit_machine_progress_by_default(self):
        self.image('sample.png')
        with patch.dict(os.environ, INVOKEAI_TOOLS_PROGRESS=''), redirect_stdout(io.StringIO()) as log:
            self.assertEqual(self.run_restore('--dry-run'), 0)
        self.assertEqual(self.counts(log.getvalue()), [])

    def test_worker_transports_progress_and_hides_protocol_from_log(self):
        self.image('first.png')
        self.image('second.png')
        for operation, flags in (
            ('restore', ['--db-path', str(self.db), '--outputs-path', str(self.images), '--dry-run']),
            ('png', [str(self.images), '--dry-run']),
        ):
            with self.subTest(operation=operation):
                worker = SimpleNamespace(stop_file=self.root / 'stop', events=queue.Queue())
                args = [sys.executable, '-u', str(app.REPO / 'app.py'), '--run-operation', operation, *flags]
                app.App.worker(worker, args, self.root)
                events = []
                while not worker.events.empty():
                    events.append(worker.events.get_nowait())
                progress = [value for kind, value in events if kind == 'progress']
                self.assertTrue(progress)
                self.assertEqual(progress[-1], (2, 2))
                self.assertEqual(events[-1], ('done', 0))
                self.assertNotIn('[PROGRESS]', ''.join(value for kind, value in events if kind == 'log'))

    def test_parser_rejects_malformed_progress(self):
        for line in ('[PROGRESS] 2 1', '[PROGRESS] -1 2', '[PROGRESS] x y', '[PROGRESS] 1', '[INFO] 1 2'):
            self.assertIsNone(app.parse_progress_line(line))
        self.assertEqual(app.parse_progress_line('[PROGRESS] 1 2'), (1, 2))

    def test_restore_progress_inside_an_existing_exception_handler(self):
        self.image('sample.png')
        with redirect_stdout(io.StringIO()) as log:
            try:
                raise ValueError('caller error')
            except ValueError:
                self.assertEqual(self.run_restore('--dry-run'), 0)
        self.assertEqual(self.counts(log.getvalue()), [(0, 1), (1, 1)])

    def test_worker_handles_split_utf8_and_progress_lines(self):
        worker = SimpleNamespace(stop_file=self.root / 'stop', events=queue.Queue())
        code = """
import sys, time
from pathlib import Path
path = Path(sys.argv[sys.argv.index('--worker-log') + 1])
payload = 'Привет, мир!'.encode('utf-8')
with path.open('wb', buffering=0) as output:
    for chunk in (payload[:1], payload[1:] + b'\\n[PROG', b'RESS] 1 2\\n', b'[PROGRESS] 2 2'):
        output.write(chunk)
        time.sleep(0.15)
"""
        args = [sys.executable, '-u', '-c', code, '--run-operation', 'png']
        app.App.worker(worker, args, self.root)
        events = []
        while not worker.events.empty():
            events.append(worker.events.get_nowait())
        messages = ''.join(value for kind, value in events if kind == 'log')
        self.assertEqual(messages, 'Привет, мир!')
        self.assertEqual([value for kind, value in events if kind == 'progress'][-1], (2, 2))
        self.assertEqual(events[-1], ('done', 0))


if __name__ == '__main__':
    unittest.main()
