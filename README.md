# InvokeAI Tools GUI

A compact CustomTkinter desktop application for InvokeAI database maintenance
and PNG recompression. The application brings the maintenance scripts from
[InvokeAI-Tools](https://github.com/DustyShoe/InvokeAI-Tools) together in a convenient
graphical interface.

This repository is now the primary home for maintenance script development and
tests. Changes to the scripts are made and tested here first. InvokeAI-Tools
continues to provide standalone scripts; updates can be copied there when needed.

## Run

Requires Python 3.10 or newer.

```powershell
uv pip install -r requirements.txt
python app.py
```

Using an existing InvokeAI test environment:

```powershell
& "I:\Invoke-test\.venv\Scripts\python.exe" .\app.py
```

Choose the InvokeAI root directory containing invokeai.yaml. Board lists are read
from the configured SQLite database. Press Enter after typing a path manually.
Dry run is enabled by default.

Operations:
- Restore missing image records and metadata.
- Convert board images to assets.
- Convert board images to gallery images.
- Recompress PNG files recursively in the configured outputs/images directory.

Stop InvokeAI and back up your database and images before applying changes.
Restore creates a database backup; conversion scripts do not add automatic backups.

## Image directory layouts

Restore detects each image's actual directory relative to the configured
outputs/images folder and stores it in image_subfolder. Flat, date, type, hash
(UUID prefix), and mixed layouts are supported without moving files or relying
on the current image_subfolder_strategy setting. Existing records have their
paths repaired even when metadata is already present. Dry run previews repairs.

Nested images require a database schema with image_subfolder; older schemas
remain supported for flat layouts. Duplicate filenames in different directories
are rejected before writes, including when --limit is used. Initialize a missing
database with InvokeAI first; this tool restores image records, not the schema.

## Safe stop

Stop requests cooperative cancellation, with no forced process termination.
PNG processing completes the current file and its atomic replacement, then stops;
previously completed images remain processed. Database operations roll back the
pending transaction at a safe checkpoint. A final commit already in progress is
allowed to complete normally. Closing is blocked while an operation runs.

The progress bar shows activity rather than percentage until completion.

## Checks

```powershell
python -m unittest discover -v
```

Tests use temporary databases and generated images, never live InvokeAI data.

## Packaging status

This initial source version runs scripts from its own scripts directory using the
current Python environment. Standalone EXE packaging is planned, not implemented.

## License

MIT. Maintenance scripts originate from DustyShoe/InvokeAI-Tools; the original
copyright notice is preserved in LICENSE.
