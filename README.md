# InvokeAI Tools GUI v1.1

A desktop application for InvokeAI database maintenance and PNG recompression.
It brings the scripts from [InvokeAI-Tools](https://github.com/DustyShoe/InvokeAI-Tools)
together in a convenient graphical interface.

## Getting started

Open the application and select your InvokeAI root directory, containing
`invokeai.yaml`. Choose an operation and review its settings. Hover over an
operation for a detailed explanation.

**Dry run** is enabled by default: it previews changes without applying them.
Restore and Recompress show the number of processed files and a progress bar.
The log shows results, skipped files, and errors. Right-click to copy selected
text or the entire log.

Stop InvokeAI before applying changes. Keep a backup of your database and images.
Restore and Fix Image Deletion create database backups automatically; the board
conversion operations do not.

## Operations

- **Restore Images Database** — register missing images and recover embedded
  metadata and create missing WebP thumbnails. Existing records retain their
  board membership.
- **Convert Board to Assets** — reclassify images on the selected board as user
  assets, preserving their files, metadata, and board membership.
- **Convert Assets to Board** — reclassify images on the selected board as regular
  gallery images, keeping them on the same board.
- **Recompress PNG** — reduce PNG file sizes using compression level 9 without
  changing pixels. Smaller results replace the originals. The summary reports space freed in megabytes;
  Dry run reports potential savings.
- **Fix Image Deletion (#9504)** — repair the database issue that blocks deleting
  images after folder migration. Already repaired databases are left unchanged.
  Unfinished migrations or database integrity problems block the repair.

Board conversion requires an explicit board selection.

## Import a batch

For Restore, **Source folder** and **Import board** are optional.

Leave Source folder empty to scan the configured `outputs/images` directory.
Select a folder to process a particular batch. Files already inside
`outputs/images` stay in place. External files are copied and verified;
originals remain untouched.

Enter an Import board name to group newly registered images. An existing board
with that name is reused. Leave it empty to use the automatic daily boards:
`Import-date` and `No metadata-date`.

Identical destination files are reused. Files with conflicting contents are
skipped and reported in the log. Dry run previews copying and registration.

## Folder layouts

Restore supports flat, date, type, hash (UUID prefix), and mixed directory
layouts. Existing files keep their actual location. External copies follow the
folder layout configured in InvokeAI.

Nested files require a compatible database. If the database is lost, start
InvokeAI first to create a new one, then use Restore to register the images.

## Stopping an operation

**Stop** requests cancellation at a safe point. The current PNG or copy finishes
before processing stops. Completed file changes remain on disk.

Pending database changes are rolled back. A final save already in progress is
allowed to finish. Copies left after cancellation can be registered on the next
run. Wait for the operation to finish or stop before closing the application.

## License

MIT. Maintenance scripts originate from DustyShoe/InvokeAI-Tools; the original
copyright notice is preserved in LICENSE.
