# Architecture

The browser is a local, read-only viewer for legacy iOS MBDB backups. It does
not emulate iOS or install applications from a backup.

## Data flow

- `backup.py` parses the MBDB manifest and resolves its indexed files. Reads
  reject symlinks and support bounded AES-CBC byte ranges.
- `crypto.py` unlocks backup keybags. Passwords are never persisted; unlocked
  class keys live only in the server process.
- `content.py`, `notes.py` and `recordings.py` read known app stores. SQLite
  databases are deserialized into memory with query-only mode and memory-only
  temporary storage. Unsupported schemas and WAL-dependent stores report errors.
- `session.py` owns opened snapshots. History combines older snapshots of the
  same device, preserves source dates and keeps deletion markers distinct from
  historical absence. Explicitly locking clears every opened snapshot.
- `server.py` exposes the adapters and ranged media reads on authenticated
  loopback HTTP routes. `__main__.py` binds the socket to `127.0.0.1`.
- `static/` contains the iPod-style interface, local icons and native media
  controls. Backup content is inserted as text, not executable markup.

## Local access

Startup creates a random session token, exchanged for an HttpOnly,
SameSite=Strict cookie. Host, Origin, fetch-site and mutation headers are
checked. Responses disable caching, and the content security policy blocks
external assets and framing. This is a local desktop tool, not a service to
expose to a network. Other software already running as the same OS user is
outside this access boundary.

Unlocking does not write extracted content to disk. Locking closes databases
and discards key references. Python does not guarantee physical memory erasure.
The local diagnostic endpoint returns schema and counts, never app content.

## Snapshot semantics

Each timestamp requires its own complete backup directory and manifest.
Combining directories does not create another timestamp. Unindexed files and
missing indexed files produce warnings. Added paths and unlocked keys are
retained for the current process session only.

The optional history view distinguishes retained deletion flags, items seen
only in an older opened snapshot, and orphan note fragments. It cannot infer
deletion from absence or reconstruct overwritten SQLite pages. Selecting a
snapshot displays that version; the combined view prefers the selected
snapshot when an item identifier occurs in multiple backups.

## Tests and contributions

The tests generate synthetic backups and SQLite databases with invented data.
No real device backups, media, credentials, screenshots, diagnostic dumps or
machine-specific task notes belong in this repository. Keep private test
artifacts outside version control; `.local/` is ignored for that purpose.

Run `uv run --frozen pytest -q`, `node --test tests/test_ui.mjs` and
`node --check ibackup/static/app.js` before submitting changes.
