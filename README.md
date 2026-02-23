# pCloud Safe Copier

A robust file-by-file copy tool designed to work around FUSE filesystem freezes on macOS.

pCloud's FUSE driver (and similar cloud-mounted drives) can freeze when standard tools like `cp -r`, `rsync`, or Finder attempt bulk file operations. This tool copies **one file at a time** with configurable pauses, timeout detection, automatic retries, and integrity verification — so your transfers complete reliably even on unstable FUSE mounts.

## The Problem

When copying large folders from pCloud Drive on macOS:
- **Finder / `cp -r` / `rsync`**: The FUSE mount freezes after a few hundred MB, requiring a force-quit or reboot
- **Web download (ZIP)**: Consistently fails at ~248 MB on ~700 MB folders
- **No built-in workaround**: pCloud offers no throttling or queuing mechanism

## The Solution

This tool copies files **sequentially** with FUSE-safe mechanisms:

- One file at a time (no parallel I/O that overwhelms the FUSE driver)
- Configurable pause between files (default 1 second)
- Per-file timeout detection using a background thread (catches FUSE freezes)
- Automatic retry with exponential backoff
- Blake2b hash verification (computed inline during copy, verified against local destination)
- Resume capability via JSON manifest checkpoint
- FUSE health probing after failures
- Leaked thread tracking (aborts if too many threads are stuck in FUSE reads)

## Features

- **GUI mode** (tkinter) with real-time progress bars, smooth ETA, and detailed log
- **CLI mode** for terminal/scripted use
- **Zero dependencies** — uses only Python standard library
- **13+ edge cases handled**: Unicode filenames, empty files/dirs, symlinks, permission errors, disk full detection, case-insensitive collisions, long paths, broken symlinks, read-only destinations, and more
- **39 automated tests** covering all edge cases

## Screenshots

### GUI Mode
```
[Source folder: /Users/you/pCloud Drive/...  [Browse...]]
[Dest folder:   /Users/you/Desktop/backup    [Browse...]]
[Settings] pause: 1.0s | timeout: 120s | retries: 3
[Start] [Pause] [Resume] [Cancel] [Load Manifest]

Current file: photos/IMG_4521.jpg
[##########............] 45% file progress
[######................] 30% overall progress
Files: 107/359 | 210 MB / 721 MB | Rate: 2.4 MB/s | ETA: 3m 32s
Failed: 0 | Skipped: 2
```

## Installation

No installation needed. Just download the script:

```bash
# Clone the repo
git clone https://github.com/Camponotus-vagus/pcloud-safe-copier.git
cd pcloud-safe-copier

# Or just download the single file
curl -O https://raw.githubusercontent.com/Camponotus-vagus/pcloud-safe-copier/main/pcloud_copier.py
```

**Requirements:** Python 3.9+ with tkinter (included in standard macOS Python and most Linux distributions).

## Usage

### GUI Mode (recommended)

```bash
python3 pcloud_copier.py
```

1. Select source folder (auto-detects pCloud Drive location)
2. Select destination folder
3. Adjust settings if needed (pause, timeout, retries, verify, etc.)
4. Click **Start Copy**
5. Use **Pause/Resume/Cancel** as needed — progress is always saved

### CLI Mode

```bash
python3 pcloud_copier.py /path/to/source /path/to/destination [OPTIONS]

Options:
  --pause SECONDS     Pause between files (default: 1.0)
  --timeout SECONDS   Per-file timeout (default: 120)
  --retries N         Max retries per file (default: 3)
  --no-verify         Skip hash verification
```

### Resuming an Interrupted Copy

If a copy is interrupted (cancel, crash, FUSE unmount), a manifest file (`.pcloud_copy_manifest.json`) is saved in the destination folder. To resume:

1. Launch the GUI
2. Click **Load Manifest (Resume)**
3. Select the `.pcloud_copy_manifest.json` file
4. Click **Start Copy** — already-verified files are skipped automatically

## How It Works

### FUSE Safety Mechanisms

| Mechanism | Purpose |
|---|---|
| Serial copy (one file at a time) | Prevents concurrent I/O that overwhelms FUSE |
| Configurable inter-file pause | Lets the FUSE driver recover between files |
| Small read buffer (128 KB) | Avoids large FUSE read requests |
| Per-file timeout (scaled by file size) | Detects FUSE freezes without hanging forever |
| FUSE health probe after failures | Checks if the mount is still alive before retrying |
| Leaked thread tracking | Aborts if too many copy threads are stuck in FUSE |
| Exponential backoff on retries | Gives the FUSE driver time to recover |

### Data Integrity

1. Blake2b hash computed **inline** during the copy (single pass over FUSE)
2. `os.fsync()` called before verification
3. Hash verified against a second pass over the **local** destination
4. Partial files deleted on failure or hash mismatch

### ETA Calculation

Transfer rate is smoothed using an **Exponential Moving Average (EMA)** with a 10-second rolling window, preventing the wild ETA jumps (e.g., "47h" to "22h") common with simple byte/time calculations on variable-speed FUSE mounts.

## Running Tests

```bash
python3 test_pcloud_copier.py
```

All 39 tests should pass in ~5 seconds. Tests cover:
1. Unicode/accented filenames (NFC normalization)
2. Large files with dynamic timeout scaling
3. Simulated FUSE timeout (blocked thread detection)
4. Empty files (zero bytes)
5. Empty directories (structure preservation)
6. Symbolic links (skip by default)
7. Permission-denied files (skip and continue)
8. File size changes during copy
9. Source disappearance (FUSE unmount detection)
10. Disk full detection
11. Case-insensitive filename collisions
12. Long file paths
13. Broken symbolic links
14. Read-only destination detection
15. Resume from manifest (skip completed, retry failed)
16. Hash integrity verification
17. Mixed workload (realistic scenario)
18. Cancel with manifest preservation

## Works With

While designed for pCloud, this tool works with any FUSE-mounted filesystem:
- pCloud Drive
- Google Drive (via google-drive-ocamlfuse or similar)
- Dropbox (FUSE mounts)
- rclone mount
- SSHFS
- Any other FUSE-based cloud storage

## Background

This tool was born out of frustration: pCloud's FUSE driver on macOS consistently freezes during bulk file operations, and even their web interface fails to download folders larger than ~250 MB. After proving that copying **one file at a time** with pauses works reliably, I generalized the approach into this reusable tool.

The original shell script that inspired this project successfully copied 359 files (721 MB) from a frozen pCloud mount with zero errors.

## License

MIT License — see [LICENSE](LICENSE) for details.
