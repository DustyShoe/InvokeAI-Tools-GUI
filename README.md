# InvokeAI Tools GUI

A compact CustomTkinter desktop application for InvokeAI database maintenance
and PNG recompression. The application brings the maintenance scripts from
[InvokeAI-Tools](https://github.com/DustyShoe/InvokeAI-Tools) together in a convenient
graphical interface.

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

## Safe stop

Stop requests cooperative cancellation, with no forced process termination.
PNG processing completes the current file and its atomic replacement, then stops;
previously completed images remain processed. Database operations roll back the
pending transaction at a safe checkpoint. A final commit already in progress is
allowed to complete normally. Closing is blocked while an operation runs.

The progress bar shows activity rather than percentage until completion.

## Checks

```powershell
python test_cancellation.py
```

Tests use temporary databases and generated images, never live InvokeAI data.

## Packaging status

This initial source version runs scripts from its own scripts directory using the
current Python environment. Standalone EXE packaging is planned, not implemented.

## License

MIT. Maintenance scripts originate from DustyShoe/InvokeAI-Tools; the original
copyright notice is preserved in LICENSE.
