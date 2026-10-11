"""Smoke-test a standalone executable against temporary data."""
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from PIL import Image


def check(executable):
    with tempfile.TemporaryDirectory(prefix='invokeai-exe-check-') as directory:
        root = Path(directory)
        exe = root / 'InvokeAI-Tools-GUI.exe'
        shutil.copy2(executable, exe)
        environment = os.environ.copy()
        for name in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'TCL_LIBRARY', 'TK_LIBRARY', 'INVOKEAI_TOOLS_STOP_FILE'):
            environment.pop(name, None)
        environment['PATH'] = os.environ.get('SystemRoot', 'C:\\Windows') + '\\System32'
        def operation(key, args, expected=0, stop=False):
            log = root / 'operation.log'
            if stop:
                marker = root / 'stop'
                marker.touch()
                environment['INVOKEAI_TOOLS_STOP_FILE'] = str(marker)
            else:
                environment.pop('INVOKEAI_TOOLS_STOP_FILE', None)
            result = subprocess.run([str(exe), '--run-operation', key, '--worker-log', str(log), *args], cwd=root, env=environment, timeout=90)
            text = log.read_text(encoding='utf-8') if log.exists() else '(missing worker log)'
            assert result.returncode == expected, (key, result.returncode, text)
            return text
        result = subprocess.run([str(exe), '--check-gui'], cwd=root, env=environment, timeout=60)
        assert result.returncode == 0, 'GUI startup failed'
        for key in ('restore', 'assets', 'board', 'png', 'repair'):
            assert 'usage:' in operation(key, ['--help'])
        images = root / 'outputs/images/general'
        images.mkdir(parents=True)
        image = images / 'sample.png'
        Image.new('RGB', (64, 64), 'red').save(image, compress_level=0)
        (root / 'databases').mkdir()
        db = root / 'databases/invokeai.db'
        with closing(sqlite3.connect(db)) as c:
            c.executescript("CREATE TABLE images(image_name TEXT PRIMARY KEY, image_origin TEXT, image_category TEXT, width INT, height INT, metadata TEXT, is_intermediate INT, created_at TEXT, updated_at TEXT, image_subfolder TEXT DEFAULT ''); CREATE TABLE boards(board_id TEXT PRIMARY KEY, board_name TEXT, created_at TEXT); CREATE TABLE board_images(board_id TEXT, image_name TEXT);")
        operation('repair', ['--db-path', str(db), '--dry-run'])
        args = ['--db-path', str(db), '--outputs-path', str(images.parent), '--no-backup']
        original = db.read_bytes()
        operation('restore', args + ['--dry-run'])
        assert db.read_bytes() == original
        operation('restore', args)
        with Image.open(images.parent / 'thumbnails/general/sample.webp') as preview:
            assert preview.format == 'WEBP'
            assert max(preview.size) <= 256
        with closing(sqlite3.connect(db)) as c:
            assert c.execute('SELECT image_subfolder FROM images').fetchone()[0] == 'general'
            board = c.execute('SELECT board_name FROM boards').fetchone()[0]
        operation('assets', ['--board-name', board])
        with closing(sqlite3.connect(db)) as c:
            assert c.execute('SELECT image_category FROM images').fetchone()[0] == 'user'
        operation('board', ['--board-name', board])
        with closing(sqlite3.connect(db)) as c:
            assert c.execute('SELECT image_category FROM images').fetchone()[0] == 'general'
            assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        before = image.read_bytes()
        operation('png', [str(images), '--dry-run'])
        assert image.read_bytes() == before
        operation('png', [str(images)])
        assert image.stat().st_size < len(before)
        with Image.open(image) as loaded:
            loaded.load()
            assert loaded.getpixel((0, 0)) == (255, 0, 0)
        # Verify that the packaged PNG operation preserves 16-bit RGB samples.
        import struct
        import zlib
        from test_cancellation import load
        png_module = load('scripts/png_recompress_level9_v1.0.py', 'png_exe_fixture')
        scanlines = b'\x00' + struct.pack('>3H', 1, 257, 65534)
        payload = png_module.PNG_SIGNATURE
        payload += png_module.pack_chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 16, 2, 0, 0, 0))
        payload += png_module.pack_chunk(b'IDAT', zlib.compress(scanlines, level=0))
        payload += png_module.pack_chunk(b'IEND', b'')
        precise = images / '16-bit.png'
        precise.write_bytes(payload)
        operation('png', [str(precise)])
        chunks = png_module.read_chunks(precise.read_bytes())
        assert zlib.decompress(b''.join(c.data for c in chunks if c.chunk_type == b'IDAT')) == scanlines
        before = db.read_bytes()
        operation('restore', args, expected=130, stop=True)
        assert db.read_bytes() == before
        operation('assets', ['--board-name', board], expected=130, stop=True)
        operation('board', ['--board-name', board], expected=130, stop=True)
        before = image.read_bytes()
        operation('png', [str(images)], expected=130, stop=True)
        assert image.read_bytes() == before
        batch = root / 'external-batch'
        batch.mkdir()
        source_image = batch / 'batch-image.png'
        Image.new('RGB', (8, 8), 'blue').save(source_image)
        batch_args = args + ['--source-path', str(batch), '--board-name', 'Imported batch']
        environment.pop('INVOKEAI_TOOLS_STOP_FILE', None)
        operation('restore', batch_args + ['--dry-run'])
        assert not (images.parent / source_image.name).exists()
        operation('restore', batch_args)
        assert (images.parent / source_image.name).read_bytes() == source_image.read_bytes()
        with Image.open(images.parent / 'thumbnails/batch-image.webp') as preview:
            assert preview.format == 'WEBP'
            preview.verify()
        with closing(sqlite3.connect(db)) as c:
            assert c.execute("SELECT b.board_name FROM boards b JOIN board_images bi USING(board_id) WHERE bi.image_name='batch-image.png'").fetchone()[0] == 'Imported batch'
        from test_cancellation import load
        repair_module = load('scripts/Repair_Image_Deletion.py', 'repair_exe_fixture')
        with closing(sqlite3.connect(db)) as c:
            c.execute('CREATE TABLE image_subfolder_move_jobs(id INTEGER PRIMARY KEY,state TEXT)')
            c.execute("INSERT INTO image_subfolder_move_jobs VALUES(1,'committed')")
            c.execute(repair_module.CREATE.replace('_repair_9504','').replace(' ON DELETE CASCADE',''))
            c.execute("INSERT INTO image_subfolder_move_items(job_id,image_name,old_subfolder,new_subfolder,state) VALUES(1,'batch-image.png','','general','committed')")
            c.commit()
        original_db = db.read_bytes()
        operation('repair', ['--db-path', str(db), '--dry-run'])
        assert db.read_bytes() == original_db
        operation('repair', ['--db-path', str(db)], expected=130, stop=True)
        assert db.read_bytes() == original_db
        operation('repair', ['--db-path', str(db)])
        with closing(sqlite3.connect(db)) as c:
            c.execute('PRAGMA foreign_keys=ON')
            assert any(row[2]=='images' and row[6]=='CASCADE' for row in c.execute('PRAGMA foreign_key_list(image_subfolder_move_items)'))
            c.execute("DELETE FROM images WHERE image_name='batch-image.png'")
            assert c.execute('SELECT COUNT(*) FROM image_subfolder_move_items').fetchone()[0]==0
            c.rollback()
        print('PASS: isolated EXE, GUI startup, five embedded operations, dry run, writes, subfolders, safe-stop requests')


if __name__ == '__main__':
    check(Path(sys.argv[1]).resolve())
