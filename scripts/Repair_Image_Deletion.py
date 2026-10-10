"""Repair InvokeAI #9504: move-journal references blocking image deletion.

Run with InvokeAI stopped. Existing move records are preserved. This is an
upgrade of the journal FK, not a deletion of image records or files.
"""
import argparse
from contextlib import closing
import datetime
import os
from pathlib import Path
import sqlite3

COLUMNS = ('job_id', 'image_name', 'old_subfolder', 'new_subfolder',
           'is_intermediate', 'old_path', 'new_path', 'old_thumbnail_path',
           'new_thumbnail_path', 'state', 'error_message')
CREATE = """
CREATE TABLE image_subfolder_move_items_repair_9504 (
    job_id INTEGER NOT NULL REFERENCES image_subfolder_move_jobs(id),
    image_name TEXT NOT NULL REFERENCES images(image_name) ON DELETE CASCADE,
    old_subfolder TEXT NOT NULL,
    new_subfolder TEXT NOT NULL,
    is_intermediate BOOLEAN NOT NULL DEFAULT FALSE,
    old_path TEXT,
    new_path TEXT,
    old_thumbnail_path TEXT,
    new_thumbnail_path TEXT,
    state TEXT NOT NULL CHECK (state IN ('planned', 'moved', 'committed', 'error')),
    error_message TEXT,
    PRIMARY KEY (job_id, image_name)
)
"""


def checkpoint():
    marker = os.environ.get('INVOKEAI_TOOLS_STOP_FILE')
    if marker and Path(marker).is_file():
        print('[STOPPED] Repair cancelled. Pending schema changes are rolled back.')
        raise SystemExit(130)


def validate(connection):
    if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise ValueError('Database integrity check failed; repair aborted.')
    if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise ValueError('Existing foreign-key violations found; repair aborted.')
    names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'image_subfolder_move_items' not in names:
        print('[INFO] Move journal is absent. This repair is not needed.')
        return False
    if 'image_subfolder_move_jobs' not in names or 'images' not in names:
        raise ValueError('Incomplete move-journal schema.')
    keys = connection.execute('PRAGMA foreign_key_list(image_subfolder_move_items)').fetchall()
    image_keys = [row for row in keys if row[2] == 'images' and row[3] == 'image_name' and row[4] == 'image_name']
    if len(image_keys) != 1:
        raise ValueError('Unrecognized image foreign key; repair aborted.')
    if image_keys[0][6] == 'CASCADE':
        print('[INFO] ON DELETE CASCADE is already present. No repair needed.')
        return False
    columns = tuple(row[1] for row in connection.execute('PRAGMA table_info(image_subfolder_move_items)'))
    if columns != COLUMNS:
        raise ValueError('Unrecognized move-journal columns; repair aborted.')
    if 'image_subfolder_move_items_repair_9504' in names:
        raise ValueError('Temporary repair table already exists; repair aborted.')
    if connection.execute("SELECT 1 FROM image_subfolder_move_jobs WHERE state != 'committed' LIMIT 1").fetchone():
        raise ValueError('Unfinished or failed move jobs exist. Resolve them in InvokeAI first.')
    if connection.execute("SELECT 1 FROM image_subfolder_move_items WHERE state != 'committed' LIMIT 1").fetchone():
        raise ValueError('Unfinished or failed move items exist. Resolve them in InvokeAI first.')
    return True


def repair(database: Path, dry_run: bool = False) -> int:
    database = database.expanduser().resolve()
    checkpoint()
    with closing(sqlite3.connect(database.as_uri() + ('?mode=ro' if dry_run else '?mode=rw'), uri=True, timeout=5)) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        if not validate(connection):
            return 0
        count = connection.execute('SELECT COUNT(*) FROM image_subfolder_move_items').fetchone()[0]
        if dry_run:
            print(f'[DRY] Would add ON DELETE CASCADE, preserving {count} move records.')
            print('[DRY] A database backup will be created before applying the repair.')
            return 0
        checkpoint()
        directory = database.parent / 'backup'
        directory.mkdir(exist_ok=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = directory / f'fix-9504-{stamp}-invokeai.db'
        with closing(sqlite3.connect(backup)) as target:
            connection.backup(target, pages=256, progress=lambda *args: checkpoint())
        print(f'[INFO] Backup created: {backup}')
        try:
            connection.execute('BEGIN IMMEDIATE')
            if not validate(connection):
                connection.rollback()
                return 0
            count = connection.execute('SELECT COUNT(*) FROM image_subfolder_move_items').fetchone()[0]
            objects = connection.execute("SELECT sql FROM sqlite_master WHERE tbl_name='image_subfolder_move_items' AND type IN ('index','trigger') AND sql IS NOT NULL").fetchall()
            checkpoint()
            connection.execute(CREATE)
            fields = ', '.join(COLUMNS)
            connection.execute(f'INSERT INTO image_subfolder_move_items_repair_9504 ({fields}) SELECT {fields} FROM image_subfolder_move_items')
            checkpoint()
            connection.execute('DROP TABLE image_subfolder_move_items')
            connection.execute('ALTER TABLE image_subfolder_move_items_repair_9504 RENAME TO image_subfolder_move_items')
            for (sql,) in objects:
                connection.execute(sql)
            checkpoint()
            if connection.execute('SELECT COUNT(*) FROM image_subfolder_move_items').fetchone()[0] != count:
                raise ValueError('Move-record count mismatch.')
            if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('Integrity check failed after repair.')
            if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError('Foreign-key check failed after repair.')
            checkpoint()
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        print(f'[OK] Repair complete. Preserved {count} move records. Images and files are unchanged.')
    return 0


def main():
    parser = argparse.ArgumentParser(description='Repair image deletion after folder migration (InvokeAI #9504). Stop InvokeAI first.')
    parser.add_argument('--db-path', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        return repair(args.db_path, args.dry_run)
    except (sqlite3.Error, OSError, ValueError) as error:
        print(f'[ERROR] {error}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
