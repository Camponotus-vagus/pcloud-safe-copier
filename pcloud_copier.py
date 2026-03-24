#!/usr/bin/env python3
"""
pCloud Safe Copier — Robust file-by-file copy tool for FUSE-mounted drives.

Designed to work around pCloud's FUSE driver freezing during bulk operations.
Works with any FUSE-based cloud drive (pCloud, Google Drive, etc.).

Usage:
    python3 pcloud_copier.py              # Launch GUI
    python3 pcloud_copier.py --help       # Show CLI help
    python3 pcloud_copier.py SRC DST      # CLI mode (no GUI)
"""

__version__ = "1.0.0"

import concurrent.futures
import hashlib
import json
import logging
import os
import queue
import subprocess
import shutil
import sys
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import Optional

logger = logging.getLogger("pcloud_copier")

# ── Enums ──────────────────────────────────────────────────────────────────

class FileStatus(Enum):
    PENDING = auto()
    IN_PROGRESS = auto()
    COPIED = auto()
    VERIFIED = auto()
    SKIPPED_EXISTS = auto()
    SKIPPED_SYMLINK = auto()
    SKIPPED_PERMISSION = auto()
    SKIPPED_BROKEN_LINK = auto()
    FAILED = auto()
    TIMEOUT = auto()


class EngineState(Enum):
    IDLE = auto()
    SCANNING = auto()
    COPYING = auto()
    PAUSED = auto()
    CANCELLING = auto()
    COMPLETED = auto()
    ERROR = auto()


class MsgType(Enum):
    LOG = auto()
    FILE_START = auto()
    FILE_PROGRESS = auto()
    FILE_DONE = auto()
    SCAN_PROGRESS = auto()
    STATE_CHANGE = auto()
    STATS_UPDATE = auto()
    FINISHED = auto()
    ERROR = auto()


# ── Exceptions ─────────────────────────────────────────────────────────────

class PCloudCopyError(Exception):
    pass

class FUSETimeoutError(PCloudCopyError):
    pass

class FUSEUnresponsiveError(PCloudCopyError):
    pass

class IntegrityError(PCloudCopyError):
    pass

class DiskFullError(PCloudCopyError):
    pass

class PathTooLongError(PCloudCopyError):
    pass


# ── Dataclasses ────────────────────────────────────────────────────────────

@dataclass
class CopySettings:
    pause_between_files: float = 1.0
    file_timeout: float = 120.0
    max_retries: int = 3
    retry_base_delay: float = 5.0
    hash_algorithm: str = "blake2b"
    verify_after_copy: bool = True
    copy_buffer_size: int = 131072  # 128KB
    skip_symlinks: bool = True
    preserve_metadata: bool = True
    max_leaked_threads: int = 3
    scan_batch_pause: float = 0.05
    timeout_seconds_per_mb: float = 3.0


@dataclass
class FileRecord:
    rel_path: str
    size_bytes: int = 0
    source_hash: str = ""
    dest_hash: str = ""
    status: str = "PENDING"
    error_message: str = ""
    retries: int = 0
    bytes_copied: int = 0
    is_empty_dir: bool = False


@dataclass
class CopyManifest:
    source_root: str
    dest_root: str
    settings: dict = field(default_factory=dict)
    files: list = field(default_factory=list)
    total_bytes: int = 0
    bytes_completed: int = 0
    files_completed: int = 0
    files_failed: int = 0
    files_skipped: int = 0
    started_at: str = ""
    last_updated: str = ""
    version: int = 1


@dataclass
class ProgressStats:
    engine_state: str = ""
    current_file: str = ""
    current_file_bytes: int = 0
    current_file_total: int = 0
    files_done: int = 0
    files_total: int = 0
    bytes_done: int = 0
    bytes_total: int = 0
    files_failed: int = 0
    files_skipped: int = 0
    elapsed_seconds: float = 0.0
    eta_seconds: float = 0.0
    transfer_rate_bps: float = 0.0
    leaked_threads: int = 0


# ── Copy Engine ────────────────────────────────────────────────────────────

class CopyEngine:
    """Orchestrates file-by-file copying with FUSE-safety mechanisms."""

    FUSE_ERROR_CODES = {5, 60, 64, 70}  # EIO, ETIMEDOUT, EHOSTDOWN, ESTALE

    def __init__(self, msg_queue: queue.Queue, settings: CopySettings):
        self._queue = msg_queue
        self._settings = settings
        self.state = EngineState.IDLE
        self._pause_event = threading.Event()
        self._cancel_event = threading.Event()
        self._pause_event.set()
        self._manifest: Optional[CopyManifest] = None
        self._thread: Optional[threading.Thread] = None
        self._leaked_thread_count = 0
        self._start_time = 0.0
        self._seen_paths_lower: dict[str, str] = {}
        self._lock = threading.Lock()
        # EMA-smoothed transfer rate for stable ETA
        self._ema_rate = 0.0          # bytes/sec, smoothed
        self._ema_alpha = 0.15        # EMA weight (lower = smoother, 0.1-0.3 typical)
        self._last_rate_time = 0.0
        self._last_rate_bytes = 0
        self._rate_window: list[tuple[float, float]] = []  # (timestamp, bytes_done)
        self._last_stats_time = 0.0   # throttle stats updates

    # ── Public API ──────────────────────────────────────────────────────

    def start(self, source: str, dest: str, resume_manifest: Optional[dict] = None):
        if not os.path.isdir(source):
            raise FileNotFoundError(f"Source not found: {source}")
        os.makedirs(dest, exist_ok=True)
        self._test_dest_writable(dest)

        if resume_manifest:
            self._manifest = CopyManifest(**{
                k: v for k, v in resume_manifest.items()
                if k in CopyManifest.__dataclass_fields__
            })
            self._manifest.files = [
                asdict(FileRecord(**{
                    k: v for k, v in f.items()
                    if k in FileRecord.__dataclass_fields__
                })) if isinstance(f, dict) else asdict(f)
                for f in self._manifest.files
            ]
        else:
            self._manifest = CopyManifest(
                source_root=source,
                dest_root=dest,
                settings=asdict(self._settings),
                started_at=datetime.now().isoformat(),
            )

        self._cancel_event.clear()
        self._pause_event.set()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def pause(self):
        self._pause_event.clear()
        self._set_state(EngineState.PAUSED)

    def resume(self):
        self._pause_event.set()
        self._set_state(EngineState.COPYING)

    def cancel(self):
        self._cancel_event.set()
        self._pause_event.set()

    def get_manifest_dict(self) -> dict:
        if self._manifest:
            d = asdict(self._manifest) if hasattr(self._manifest, '__dataclass_fields__') else self._manifest.__dict__
            d['last_updated'] = datetime.now().isoformat()
            return d
        return {}

    # ── Background thread ───────────────────────────────────────────────

    def _run(self):
        try:
            self._start_time = time.monotonic()
            self._ema_rate = 0.0
            self._rate_window.clear()
            self._last_stats_time = 0.0
            if not self._manifest.files:
                self._scan_source()
            if self._cancel_event.is_set():
                self._set_state(EngineState.CANCELLING)
                self._save_manifest_checkpoint()
                self._send(MsgType.FINISHED, self._build_summary())
                return
            self._copy_all_files()
            if self._cancel_event.is_set():
                self._set_state(EngineState.CANCELLING)
            else:
                self._set_state(EngineState.COMPLETED)
            self._save_manifest_checkpoint()
            self._send(MsgType.FINISHED, self._build_summary())
        except FUSEUnresponsiveError as e:
            self._set_state(EngineState.ERROR)
            self._save_manifest_checkpoint()
            self._send(MsgType.ERROR, f"FUSE mount unresponsive: {e}")
            self._send(MsgType.FINISHED, self._build_summary())
        except Exception as e:
            self._set_state(EngineState.ERROR)
            logger.exception("Unexpected error in copy engine")
            self._save_manifest_checkpoint()
            self._send(MsgType.ERROR, str(e))
            self._send(MsgType.FINISHED, self._build_summary())

    # ── Source scanning ─────────────────────────────────────────────────

    def _scan_source(self):
        self._set_state(EngineState.SCANNING)
        self._send(MsgType.LOG, "Scanning source folder...")
        root = self._manifest.source_root
        files = []
        dirs_scanned = 0
        stack = [root]

        while stack:
            if self._cancel_event.is_set():
                return
            current = stack.pop()
            try:
                entries = list(os.scandir(current))
            except PermissionError:
                self._send(MsgType.LOG, f"SKIP (permission denied): {current}")
                continue
            except OSError as e:
                if e.errno in self.FUSE_ERROR_CODES:
                    self._send(MsgType.LOG, f"FUSE error scanning {current}: {e}")
                    continue
                raise

            child_dirs = []
            child_files = []

            for entry in entries:
                try:
                    rel = os.path.relpath(entry.path, root)

                    if entry.is_symlink():
                        if self._settings.skip_symlinks:
                            files.append(asdict(FileRecord(
                                rel_path=rel, status="SKIPPED_SYMLINK")))
                            self._send(MsgType.LOG, f"SKIP (symlink): {rel}")
                            continue
                        if not os.path.exists(entry.path):
                            files.append(asdict(FileRecord(
                                rel_path=rel, status="SKIPPED_BROKEN_LINK")))
                            self._send(MsgType.LOG, f"SKIP (broken link): {rel}")
                            continue
                        real = os.path.realpath(entry.path)
                        if not real.startswith(root):
                            files.append(asdict(FileRecord(
                                rel_path=rel, status="SKIPPED_SYMLINK")))
                            self._send(MsgType.LOG,
                                f"SKIP (symlink outside source): {rel}")
                            continue

                    if entry.is_dir(follow_symlinks=False):
                        child_dirs.append(entry.path)
                    elif entry.is_file(follow_symlinks=True):
                        try:
                            st = entry.stat()
                            size = st.st_size
                        except OSError:
                            size = 0
                        child_files.append(asdict(FileRecord(
                            rel_path=rel, size_bytes=size)))
                except OSError as e:
                    self._send(MsgType.LOG, f"Error scanning {entry.path}: {e}")

            if not child_files and not child_dirs:
                rel = os.path.relpath(current, root)
                if current != root:
                    files.append(asdict(FileRecord(
                        rel_path=rel, is_empty_dir=True)))

            files.extend(child_files)
            stack.extend(child_dirs)
            dirs_scanned += 1
            self._send(MsgType.SCAN_PROGRESS, dirs_scanned)
            time.sleep(self._settings.scan_batch_pause)

        files.sort(key=lambda f: f.get('size_bytes', 0))
        total = sum(f.get('size_bytes', 0) for f in files)
        self._manifest.files = files
        self._manifest.total_bytes = total
        self._send(MsgType.LOG,
            f"Scan complete: {len(files)} items, {fmt_bytes(total)} total")

    # ── File copy loop ──────────────────────────────────────────────────

    def _copy_all_files(self):
        self._set_state(EngineState.COPYING)
        for i, file_rec in enumerate(self._manifest.files):
            if self._cancel_event.is_set():
                break
            self._pause_event.wait()
            if self._cancel_event.is_set():
                break

            status = file_rec.get('status', 'PENDING')
            if status in ('COPIED', 'VERIFIED', 'SKIPPED_EXISTS',
                          'SKIPPED_SYMLINK', 'SKIPPED_BROKEN_LINK',
                          'SKIPPED_PERMISSION'):
                continue

            if file_rec.get('is_empty_dir'):
                self._ensure_directory(file_rec)
                continue

            self._copy_single_file_with_retry(file_rec)

            # Send stats after every file for real-time updates
            self._send_stats()

            if not self._cancel_event.is_set():
                time.sleep(self._settings.pause_between_files)

            if i % 10 == 0:
                self._save_manifest_checkpoint()

        self._save_manifest_checkpoint()
        self._send_stats()

    def _copy_single_file_with_retry(self, file_rec: dict):
        max_attempts = self._settings.max_retries + 1
        for attempt in range(max_attempts):
            if self._cancel_event.is_set():
                return
            try:
                self._copy_single_file(file_rec)
                return
            except FUSETimeoutError:
                delay = self._settings.retry_base_delay * (2 ** attempt)
                file_rec['retries'] = attempt + 1
                self._send(MsgType.LOG,
                    f"TIMEOUT: {file_rec['rel_path']} — "
                    f"retry {attempt + 1}/{self._settings.max_retries} "
                    f"in {delay:.0f}s")
                self._check_fuse_health()
                time.sleep(delay)
            except IntegrityError as e:
                delay = self._settings.retry_base_delay * (2 ** attempt)
                file_rec['retries'] = attempt + 1
                self._send(MsgType.LOG,
                    f"INTEGRITY ERROR: {file_rec['rel_path']} — {e} — "
                    f"retry {attempt + 1}/{self._settings.max_retries} "
                    f"in {delay:.0f}s")
                time.sleep(delay)
            except DiskFullError as e:
                self._send(MsgType.LOG, f"DISK FULL: {e}")
                self._send(MsgType.LOG, "Pausing — free up space and resume.")
                self.pause()
                self._pause_event.wait()
                if self._cancel_event.is_set():
                    return
            except PermissionError:
                file_rec['status'] = 'SKIPPED_PERMISSION'
                file_rec['error_message'] = 'Permission denied'
                self._manifest.files_skipped += 1
                self._send(MsgType.LOG,
                    f"SKIP (permission): {file_rec['rel_path']}")
                return
            except OSError as e:
                if e.errno in self.FUSE_ERROR_CODES:
                    delay = self._settings.retry_base_delay * (2 ** attempt)
                    self._send(MsgType.LOG,
                        f"FUSE error ({e.errno}): {file_rec['rel_path']} — "
                        f"retry in {delay:.0f}s")
                    self._check_fuse_health()
                    time.sleep(delay)
                else:
                    file_rec['status'] = 'FAILED'
                    file_rec['error_message'] = str(e)
                    self._manifest.files_failed += 1
                    self._send(MsgType.LOG,
                        f"FAIL: {file_rec['rel_path']} — {e}")
                    return

        file_rec['status'] = 'FAILED'
        file_rec['error_message'] = f'Failed after {max_attempts} attempts'
        self._manifest.files_failed += 1
        self._send(MsgType.LOG,
            f"FAIL (all retries exhausted): {file_rec['rel_path']}")

    def _copy_single_file(self, file_rec: dict):
        src = Path(self._manifest.source_root) / file_rec['rel_path']
        dst_rel = self._resolve_dest_path(file_rec)
        dst = Path(self._manifest.dest_root) / dst_rel

        self._validate_destination_path(dst)
        self._check_destination_space(dst, file_rec.get('size_bytes', 0))
        dst.parent.mkdir(parents=True, exist_ok=True)

        if self._should_skip_existing(file_rec, dst):
            file_rec['status'] = 'SKIPPED_EXISTS'
            self._manifest.files_skipped += 1
            self._manifest.bytes_completed += file_rec.get('size_bytes', 0)
            self._send(MsgType.LOG, f"SKIP (exists): {file_rec['rel_path']}")
            self._send(MsgType.FILE_DONE, file_rec)
            return

        self._send(MsgType.FILE_START, file_rec['rel_path'])
        file_rec['status'] = 'IN_PROGRESS'
        file_rec['bytes_copied'] = 0

        size = file_rec.get('size_bytes', 0)
        if size == 0:
            # Empty file fast path
            dst.open('wb').close()
            hasher = hashlib.new(self._settings.hash_algorithm)
            file_rec['source_hash'] = hasher.hexdigest()
            file_rec['status'] = 'VERIFIED'
            self._manifest.files_completed += 1
            self._send(MsgType.LOG, f"OK (empty): {file_rec['rel_path']}")
            self._send(MsgType.FILE_DONE, file_rec)
            return

        timeout = max(
            self._settings.file_timeout,
            size / (1024 * 1024) * self._settings.timeout_seconds_per_mb
        )

        # Do NOT use 'with' block: executor.shutdown(wait=True) would block
        # if the worker thread is stuck in a FUSE read. Instead, on timeout
        # we abandon the executor (worker is daemon-like, will leak).
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(
                self._do_buffered_copy, src, dst, file_rec
            )
            try:
                source_hash = future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                self._leaked_thread_count += 1
                self._safe_delete(dst)
                file_rec['status'] = 'TIMEOUT'
                # Don't wait for the stuck worker — just abandon it
                executor.shutdown(wait=False, cancel_futures=True)
                if self._leaked_thread_count >= self._settings.max_leaked_threads:
                    raise FUSEUnresponsiveError(
                        f"{self._leaked_thread_count} threads stuck in FUSE")
                raise FUSETimeoutError(
                    f"Timeout after {timeout:.0f}s on {file_rec['rel_path']}")
            else:
                executor.shutdown(wait=False)
        except (FUSETimeoutError, FUSEUnresponsiveError):
            raise
        except Exception:
            executor.shutdown(wait=False, cancel_futures=True)
            raise

        file_rec['source_hash'] = source_hash
        file_rec['status'] = 'COPIED'

        if self._settings.verify_after_copy:
            dest_hash = self._hash_local_file(dst)
            file_rec['dest_hash'] = dest_hash
            if source_hash != dest_hash:
                self._safe_delete(dst)
                file_rec['status'] = 'FAILED'
                raise IntegrityError(
                    f"Hash mismatch on {file_rec['rel_path']}")
            file_rec['status'] = 'VERIFIED'

        if self._settings.preserve_metadata:
            self._try_copy_metadata(src, dst)

        self._manifest.files_completed += 1
        self._manifest.bytes_completed += file_rec.get('size_bytes', 0)
        self._send(MsgType.LOG, f"OK: {file_rec['rel_path']}")
        self._send(MsgType.FILE_DONE, file_rec)

    # ── Buffered copy with inline hashing ───────────────────────────────

    def _do_buffered_copy(self, src: Path, dst: Path, file_rec: dict) -> str:
        hasher = hashlib.new(self._settings.hash_algorithm)
        bytes_copied = 0
        last_stats_bytes = 0
        buf_size = self._settings.copy_buffer_size
        file_total = file_rec.get('size_bytes', 0)
        # Byte threshold: send at least every ~5% of file (min 64KB)
        byte_threshold = max(65536, file_total // 20) if file_total > 0 else 65536

        with open(src, 'rb') as fsrc, open(dst, 'wb') as fdst:
            while True:
                chunk = fsrc.read(buf_size)
                if not chunk:
                    break
                fdst.write(chunk)
                hasher.update(chunk)
                bytes_copied += len(chunk)
                file_rec['bytes_copied'] = bytes_copied
                # Send stats if enough time OR enough bytes have passed:
                # - time: ~10 updates/sec on slow FUSE drives
                # - bytes: ~20 updates/file on fast local drives
                now = time.monotonic()
                time_ok = (now - self._last_stats_time) >= 0.10
                bytes_ok = (bytes_copied - last_stats_bytes) >= byte_threshold
                if time_ok or bytes_ok:
                    self._last_stats_time = now
                    last_stats_bytes = bytes_copied
                    self._send_stats(bytes_copied, file_total)
            fdst.flush()
            os.fsync(fdst.fileno())

        # Always send a final stats update so bar reaches ~100% before FILE_DONE
        self._send_stats(bytes_copied, file_total)

        if bytes_copied != file_rec.get('size_bytes', 0):
            self._send(MsgType.LOG,
                f"Warning: {file_rec['rel_path']} size changed during copy "
                f"(expected {file_rec['size_bytes']}, got {bytes_copied})")
            file_rec['size_bytes'] = bytes_copied

        return hasher.hexdigest()

    # ── Helpers ─────────────────────────────────────────────────────────

    def _ensure_directory(self, file_rec: dict):
        dst = Path(self._manifest.dest_root) / file_rec['rel_path']
        dst.mkdir(parents=True, exist_ok=True)
        file_rec['status'] = 'VERIFIED'

    def _resolve_dest_path(self, file_rec: dict) -> str:
        rel = unicodedata.normalize('NFC', file_rec['rel_path'])
        lower = rel.lower()
        if lower in self._seen_paths_lower:
            existing = self._seen_paths_lower[lower]
            if existing != rel:
                p = Path(rel)
                new_name = f"{p.stem}_case_conflict{p.suffix}"
                new_rel = str(Path(p.parent) / new_name)
                self._send(MsgType.LOG,
                    f"Case collision: '{rel}' -> '{new_rel}' "
                    f"(conflicts with '{existing}')")
                self._seen_paths_lower[new_rel.lower()] = new_rel
                return new_rel
        self._seen_paths_lower[lower] = rel
        return rel

    def _validate_destination_path(self, dst: Path):
        dst_str = str(dst)
        if len(dst_str) > 1024:
            name = dst.name
            ext = dst.suffix
            stem = dst.stem
            max_stem = 255 - len(ext) - 9  # _XXXX + ext
            if max_stem < 10:
                max_stem = 10
            short = stem[:max_stem] + "_" + hashlib.md5(
                name.encode()).hexdigest()[:8] + ext
            new_dst = dst.parent / short
            self._send(MsgType.LOG,
                f"Path too long ({len(dst_str)} chars), "
                f"truncated: {dst.name} -> {short}")
            return
        if len(dst.name) > 255:
            ext = dst.suffix
            stem = dst.stem
            max_stem = 255 - len(ext) - 9
            if max_stem < 10:
                max_stem = 10
            short = stem[:max_stem] + "_" + hashlib.md5(
                dst.name.encode()).hexdigest()[:8] + ext
            self._send(MsgType.LOG,
                f"Filename too long ({len(dst.name)} chars), "
                f"truncated: -> {short}")

    def _check_destination_space(self, dst: Path, needed_bytes: int):
        try:
            parent = dst.parent if dst.parent.exists() else dst.parent.parent
            usage = shutil.disk_usage(parent)
            margin = 10 * 1024 * 1024  # 10MB
            if usage.free < needed_bytes + margin:
                raise DiskFullError(
                    f"Need {fmt_bytes(needed_bytes)}, "
                    f"only {fmt_bytes(usage.free)} free")
        except (OSError, FileNotFoundError):
            pass

    def _should_skip_existing(self, file_rec: dict, dst: Path) -> bool:
        if not dst.exists():
            return False
        try:
            dst_size = dst.stat().st_size
            src_size = file_rec.get('size_bytes', -1)
            if dst_size != src_size:
                return False
            if file_rec.get('source_hash'):
                return self._hash_local_file(dst) == file_rec['source_hash']
            return False
        except OSError:
            return False

    def _hash_local_file(self, path: Path) -> str:
        hasher = hashlib.new(self._settings.hash_algorithm)
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(self._settings.copy_buffer_size), b''):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _check_fuse_health(self):
        def _probe():
            os.stat(self._manifest.source_root)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            try:
                ex.submit(_probe).result(timeout=10)
            except concurrent.futures.TimeoutError:
                raise FUSEUnresponsiveError("Cannot stat source root — mount dead")

    def _try_copy_metadata(self, src: Path, dst: Path):
        try:
            st = os.stat(src)
            os.utime(dst, (st.st_atime, st.st_mtime))
        except OSError:
            pass

    def _safe_delete(self, path: Path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _test_dest_writable(self, dest: str):
        test_file = Path(dest) / ".pcloud_copier_write_test"
        try:
            test_file.write_bytes(b"test")
            test_file.unlink()
        except OSError as e:
            raise PermissionError(
                f"Destination not writable: {dest} — {e}")

    def _send(self, msg_type: MsgType, data=None):
        try:
            self._queue.put_nowait((msg_type, data))
        except queue.Full:
            pass

    def _set_state(self, state: EngineState):
        self.state = state
        self._send(MsgType.STATE_CHANGE, state.name)

    def _send_stats(self, current_file_bytes: int = 0, current_file_total: int = 0):
        if not self._manifest:
            return
        now = time.monotonic()
        elapsed = now - self._start_time

        # Total bytes done = completed files + current file progress
        done = self._manifest.bytes_completed + current_file_bytes
        total = self._manifest.total_bytes

        # Update rolling rate window (keep last 10 seconds of samples)
        self._rate_window.append((now, done))
        cutoff = now - 10.0
        self._rate_window = [(t, b) for t, b in self._rate_window if t >= cutoff]

        # Compute instantaneous rate from the rolling window
        if len(self._rate_window) >= 2:
            oldest_t, oldest_b = self._rate_window[0]
            dt = now - oldest_t
            db = done - oldest_b
            instant_rate = db / dt if dt > 0 else 0
        elif elapsed > 0:
            instant_rate = done / elapsed
        else:
            instant_rate = 0

        # Apply EMA smoothing
        if self._ema_rate <= 0:
            self._ema_rate = instant_rate  # seed with first measurement
        else:
            self._ema_rate = (self._ema_alpha * instant_rate +
                              (1 - self._ema_alpha) * self._ema_rate)

        # ETA from smoothed rate
        remaining = total - done
        if self._ema_rate > 0 and remaining >= 0:
            eta = remaining / self._ema_rate
        else:
            eta = 0

        self._send(MsgType.STATS_UPDATE, ProgressStats(
            engine_state=self.state.name,
            current_file_bytes=current_file_bytes,
            current_file_total=current_file_total,
            files_done=self._manifest.files_completed,
            files_total=len(self._manifest.files),
            bytes_done=done,
            bytes_total=total,
            files_failed=self._manifest.files_failed,
            files_skipped=self._manifest.files_skipped,
            elapsed_seconds=elapsed,
            eta_seconds=eta,
            transfer_rate_bps=self._ema_rate,
            leaked_threads=self._leaked_thread_count,
        ))

    def _save_manifest_checkpoint(self):
        if not self._manifest:
            return
        try:
            path = Path(self._manifest.dest_root) / '.pcloud_copy_manifest.json'
            self._manifest.last_updated = datetime.now().isoformat()
            data = {
                'source_root': self._manifest.source_root,
                'dest_root': self._manifest.dest_root,
                'settings': self._manifest.settings,
                'files': self._manifest.files,
                'total_bytes': self._manifest.total_bytes,
                'bytes_completed': self._manifest.bytes_completed,
                'files_completed': self._manifest.files_completed,
                'files_failed': self._manifest.files_failed,
                'files_skipped': self._manifest.files_skipped,
                'started_at': self._manifest.started_at,
                'last_updated': self._manifest.last_updated,
                'version': self._manifest.version,
            }
            tmp = path.with_suffix('.tmp')
            tmp.write_text(json.dumps(data, indent=2, default=str))
            tmp.replace(path)
        except OSError as e:
            logger.warning(f"Could not save manifest: {e}")

    def _build_summary(self) -> dict:
        m = self._manifest
        elapsed = time.monotonic() - self._start_time
        return {
            'files_total': len(m.files) if m else 0,
            'files_completed': m.files_completed if m else 0,
            'files_failed': m.files_failed if m else 0,
            'files_skipped': m.files_skipped if m else 0,
            'bytes_total': m.total_bytes if m else 0,
            'bytes_completed': m.bytes_completed if m else 0,
            'elapsed_seconds': elapsed,
            'state': self.state.name,
        }


# ── Utility functions ──────────────────────────────────────────────────────

def fmt_bytes(n: float) -> str:
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def fmt_duration(seconds: float) -> str:
    if seconds <= 0 or seconds > 86400 * 7:
        return "--"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def detect_pcloud_path() -> str:
    candidates = [
        Path.home() / "pCloud Drive",
        Path("/Volumes/pCloudDrive"),
        Path.home() / "pCloudDrive",
    ]
    for p in candidates:
        if p.exists() and any(p.iterdir()):
            return str(p)
    for p in candidates:
        if p.exists():
            return str(p)
    return ""


# ── GUI ────────────────────────────────────────────────────────────────────

def build_gui():
    """Build and run the tkinter GUI. Imported lazily to allow headless use."""
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext, font

    class ToolTip:
        """Lightweight tooltip for tkinter widgets."""
        def __init__(self, widget, text):
            self.widget = widget
            self.text = text
            self.tip_window = None
            widget.bind("<Enter>", self.show_tip)
            widget.bind("<Leave>", self.hide_tip)

        def show_tip(self, event=None):
            if self.tip_window or not self.text:
                return
            x = self.widget.winfo_rootx() + 20
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
            self.tip_window = tw = tk.Toplevel(self.widget)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f"+{x}+{y}")
            label = tk.Label(tw, text=self.text, justify=tk.LEFT,
                             background="#ffffe0", relief=tk.SOLID, borderwidth=1,
                             font=("tahoma", "9", "normal"))
            label.pack(ipadx=1)

        def hide_tip(self, event=None):
            tw = self.tip_window
            self.tip_window = None
            if tw:
                tw.destroy()

    class CopierGUI:
        POLL_MS = 100

        def __init__(self):
            self._root = tk.Tk()
            self._root.title(f"pCloud Safe Copier v{__version__}")
            self._root.geometry("850x700")
            self._root.minsize(750, 600)

            self._queue: queue.Queue = queue.Queue()
            self._engine: Optional[CopyEngine] = None
            self._settings = CopySettings()
            self._resume_manifest: Optional[dict] = None
            self._polling = False

            self._build_ui()
            self._root.protocol("WM_DELETE_WINDOW", self._on_close)
            self._root.bind("<Return>", self._on_enter_pressed)
            self._root.bind("<Escape>", self._on_cancel)

            default = detect_pcloud_path()
            if default:
                self._source_var.set(default)

        # ── UI construction ─────────────────────────────────────────

        def _build_ui(self):
            pad = {'padx': 8, 'pady': 4}

            # Path frame
            path_frame = ttk.LabelFrame(self._root, text="Folders", padding=8)
            path_frame.pack(fill=tk.X, **pad)

            self._source_var = tk.StringVar()
            self._dest_var = tk.StringVar()

            ttk.Label(path_frame, text="Source:").grid(
                row=0, column=0, sticky=tk.W)
            src_ent = ttk.Entry(
                path_frame, textvariable=self._source_var, width=60)
            src_ent.grid(row=0, column=1, sticky=tk.EW, padx=4)
            ttk.Button(path_frame, text="Browse...",
                command=lambda: self._browse(self._source_var)).grid(
                row=0, column=2)

            ttk.Label(path_frame, text="Destination:").grid(
                row=1, column=0, sticky=tk.W)
            dst_ent = ttk.Entry(
                path_frame, textvariable=self._dest_var, width=60)
            dst_ent.grid(row=1, column=1, sticky=tk.EW, padx=4)
            ttk.Button(path_frame, text="Browse...",
                command=lambda: self._browse(self._dest_var)).grid(
                row=1, column=2)

            # Auto-select text on focus for easier path editing
            select_all = lambda e: e.widget.after_idle(
                e.widget.selection_range, 0, tk.END)
            src_ent.bind("<FocusIn>", select_all)
            dst_ent.bind("<FocusIn>", select_all)

            path_frame.columnconfigure(1, weight=1)

            # Settings frame
            settings_frame = ttk.LabelFrame(
                self._root, text="Settings", padding=8)
            settings_frame.pack(fill=tk.X, **pad)

            self._pause_var = tk.DoubleVar(value=self._settings.pause_between_files)
            self._timeout_var = tk.DoubleVar(value=self._settings.file_timeout)
            self._retries_var = tk.IntVar(value=self._settings.max_retries)
            self._verify_var = tk.BooleanVar(value=self._settings.verify_after_copy)
            self._skip_sym_var = tk.BooleanVar(value=self._settings.skip_symlinks)
            self._preserve_var = tk.BooleanVar(value=self._settings.preserve_metadata)

            row = 0
            ttk.Label(settings_frame, text="Pause between files (s):").grid(
                row=row, column=0, sticky=tk.W)
            ttk.Spinbox(settings_frame, from_=0.1, to=30.0, increment=0.5,
                textvariable=self._pause_var, width=8).grid(
                row=row, column=1, sticky=tk.W, padx=4)

            ttk.Label(settings_frame, text="File timeout (s):").grid(
                row=row, column=2, sticky=tk.W, padx=(16, 0))
            ttk.Spinbox(settings_frame, from_=30, to=600, increment=30,
                textvariable=self._timeout_var, width=8).grid(
                row=row, column=3, sticky=tk.W, padx=4)

            ttk.Label(settings_frame, text="Max retries:").grid(
                row=row, column=4, sticky=tk.W, padx=(16, 0))
            ttk.Spinbox(settings_frame, from_=0, to=10, increment=1,
                textvariable=self._retries_var, width=5).grid(
                row=row, column=5, sticky=tk.W, padx=4)

            row = 1
            ttk.Checkbutton(settings_frame, text="Verify integrity (hash)",
                variable=self._verify_var).grid(
                row=row, column=0, columnspan=2, sticky=tk.W)
            ttk.Checkbutton(settings_frame, text="Skip symlinks",
                variable=self._skip_sym_var).grid(
                row=row, column=2, columnspan=2, sticky=tk.W)
            ttk.Checkbutton(settings_frame, text="Preserve timestamps",
                variable=self._preserve_var).grid(
                row=row, column=4, columnspan=2, sticky=tk.W)

            # Control buttons
            ctrl_frame = ttk.Frame(self._root)
            ctrl_frame.pack(fill=tk.X, **pad)

            self._start_btn = ttk.Button(ctrl_frame, text="Start Copy",
                command=self._on_start)
            self._start_btn.pack(side=tk.LEFT, padx=4)

            self._pause_btn = ttk.Button(ctrl_frame, text="Pause",
                command=self._on_pause, state=tk.DISABLED)
            self._pause_btn.pack(side=tk.LEFT, padx=4)

            self._resume_btn = ttk.Button(ctrl_frame, text="Resume",
                command=self._on_resume, state=tk.DISABLED)
            self._resume_btn.pack(side=tk.LEFT, padx=4)

            self._cancel_btn = ttk.Button(ctrl_frame, text="Cancel",
                command=self._on_cancel, state=tk.DISABLED)
            self._cancel_btn.pack(side=tk.LEFT, padx=4)

            self._open_dest_btn = ttk.Button(ctrl_frame, text="Open Destination",
                command=self._on_open_dest)
            self._open_dest_btn.pack(side=tk.LEFT, padx=4)

            ttk.Separator(ctrl_frame, orient=tk.VERTICAL).pack(
                side=tk.LEFT, fill=tk.Y, padx=8)

            self._manifest_btn = ttk.Button(ctrl_frame,
                text="Load Manifest (Resume)",
                command=self._on_load_manifest)
            self._manifest_btn.pack(side=tk.LEFT, padx=4)

            # Progress frame
            prog_frame = ttk.LabelFrame(
                self._root, text="Progress", padding=8)
            prog_frame.pack(fill=tk.X, **pad)

            self._current_file_var = tk.StringVar(value="Ready")
            ttk.Label(prog_frame, textvariable=self._current_file_var,
                anchor=tk.W).pack(fill=tk.X)

            ttk.Label(prog_frame, text="Current file:").pack(
                anchor=tk.W, pady=(4, 0))
            self._file_progress = ttk.Progressbar(
                prog_frame, mode='determinate', maximum=100)
            self._file_progress.pack(fill=tk.X, pady=2)

            ttk.Label(prog_frame, text="Overall:").pack(
                anchor=tk.W, pady=(4, 0))
            self._overall_progress = ttk.Progressbar(
                prog_frame, mode='determinate', maximum=100)
            self._overall_progress.pack(fill=tk.X, pady=2)

            stats_frame = ttk.Frame(prog_frame)
            stats_frame.pack(fill=tk.X, pady=(4, 0))

            self._files_var = tk.StringVar(value="Files: 0/0")
            self._bytes_var = tk.StringVar(value="0 B / 0 B")
            self._rate_var = tk.StringVar(value="Rate: --")
            self._eta_var = tk.StringVar(value="ETA: --")
            self._errors_var = tk.StringVar(value="Failed: 0 | Skipped: 0")
            self._leaked_var = tk.StringVar(value="")

            ttk.Label(stats_frame, textvariable=self._files_var).pack(
                side=tk.LEFT, padx=(0, 16))
            ttk.Label(stats_frame, textvariable=self._bytes_var).pack(
                side=tk.LEFT, padx=(0, 16))
            ttk.Label(stats_frame, textvariable=self._rate_var).pack(
                side=tk.LEFT, padx=(0, 16))
            ttk.Label(stats_frame, textvariable=self._eta_var).pack(
                side=tk.LEFT, padx=(0, 16))

            stats_frame2 = ttk.Frame(prog_frame)
            stats_frame2.pack(fill=tk.X, pady=(2, 0))
            ttk.Label(stats_frame2, textvariable=self._errors_var).pack(
                side=tk.LEFT, padx=(0, 16))
            self._leaked_label = ttk.Label(
                stats_frame2, textvariable=self._leaked_var, foreground="red")
            self._leaked_label.pack(side=tk.LEFT)

            # Log frame
            log_frame = ttk.LabelFrame(self._root, text="Log", padding=4)
            log_frame.pack(fill=tk.BOTH, expand=True, **pad)

            self._log_text = scrolledtext.ScrolledText(
                log_frame, height=12, state=tk.DISABLED,
                font=self._get_mono_font(), wrap=tk.WORD)
            self._log_text.pack(fill=tk.BOTH, expand=True)
            self._log_text.tag_configure("ok", foreground="#2e7d32")
            self._log_text.tag_configure("fail", foreground="#c62828")
            self._log_text.tag_configure("warn", foreground="#f57f17")
            self._log_text.tag_configure("info", foreground="#1565c0")

            # Add tooltips
            ToolTip(self._start_btn, "Start the copy process (Enter)")
            ToolTip(self._pause_btn, "Temporarily pause copying")
            ToolTip(self._resume_btn, "Resume the paused copy")
            ToolTip(self._cancel_btn, "Cancel the copy and save manifest (Esc)")
            ToolTip(self._manifest_btn, "Resume from a previously saved .json manifest")
            ToolTip(self._leaked_label, "Threads currently frozen in FUSE reads. "
                                        "Engine will abort if too many threads hang.")
            ToolTip(settings_frame, "Configure FUSE-safe copy parameters")

        def _get_mono_font(self):
            """Select the best available monospaced font for the platform."""
            families = font.families()
            for f in ("Menlo", "Consolas", "Cascadia Code", "Monaco", "Courier New"):
                if f in families:
                    return (f, 11)
            return ("monospace", 11)

        # ── Button handlers ─────────────────────────────────────────

        def _browse(self, var: tk.StringVar):
            path = filedialog.askdirectory(initialdir=var.get() or str(Path.home()))
            if path:
                var.set(path)

        def _on_start(self):
            source = self._source_var.get().strip()
            dest = self._dest_var.get().strip()

            if not source:
                messagebox.showerror("Error", "Please select a source folder.")
                return
            if not os.path.isdir(source):
                messagebox.showerror("Error", f"Source not found:\n{source}")
                return
            if not dest:
                messagebox.showerror("Error", "Please select a destination folder.")
                return

            self._read_settings()
            self._engine = CopyEngine(self._queue, self._settings)

            try:
                self._engine.start(source, dest, self._resume_manifest)
            except PermissionError as e:
                messagebox.showerror("Error", str(e))
                return
            except Exception as e:
                messagebox.showerror("Error", f"Failed to start: {e}")
                return

            self._resume_manifest = None
            self._start_btn.config(state=tk.DISABLED)
            self._open_dest_btn.config(state=tk.DISABLED)
            self._manifest_btn.config(state=tk.DISABLED)
            self._pause_btn.config(state=tk.NORMAL)
            self._cancel_btn.config(state=tk.NORMAL)
            self._start_polling()

        def _on_pause(self):
            if self._engine:
                self._engine.pause()
                self._pause_btn.config(state=tk.DISABLED)
                self._resume_btn.config(state=tk.NORMAL)

        def _on_resume(self):
            if self._engine:
                self._engine.resume()
                self._resume_btn.config(state=tk.DISABLED)
                self._pause_btn.config(state=tk.NORMAL)

        def _on_cancel(self, event=None):
            if self._engine and self._engine.state in (
                    EngineState.COPYING, EngineState.PAUSED, EngineState.SCANNING):
                if messagebox.askyesno("Confirm",
                        "Cancel the copy?\nProgress is saved for resume."):
                    self._engine.cancel()

        def _on_open_dest(self):
            dest = self._dest_var.get().strip()
            if not dest or not os.path.isdir(dest):
                return
            try:
                if sys.platform == 'darwin':
                    subprocess.run(['open', dest])
                elif sys.platform == 'win32':
                    os.startfile(dest)
                else:
                    subprocess.run(['xdg-open', dest])
            except Exception as e:
                self._log(f"Could not open destination: {e}", "warn")

        def _on_enter_pressed(self, event):
            if str(self._start_btn.cget('state')) == str(tk.NORMAL):
                self._on_start()

        def _on_load_manifest(self):
            path = filedialog.askopenfilename(
                title="Select resume manifest",
                filetypes=[("JSON manifest", "*.json"), ("All", "*")],
                initialdir=self._dest_var.get() or str(Path.home())
            )
            if not path:
                return
            try:
                with open(path) as f:
                    manifest = json.load(f)
                self._source_var.set(manifest.get('source_root', ''))
                self._dest_var.set(manifest.get('dest_root', ''))
                self._resume_manifest = manifest

                total = len(manifest.get('files', []))
                done = sum(1 for f in manifest.get('files', [])
                           if f.get('status') in (
                               'COPIED', 'VERIFIED', 'SKIPPED_EXISTS'))
                self._log(f"Loaded manifest: {done}/{total} files already done",
                          "info")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load manifest:\n{e}")

        def _on_close(self):
            if (self._engine and
                    self._engine.state in (EngineState.COPYING, EngineState.PAUSED)):
                if messagebox.askyesno("Confirm",
                        "Copy in progress. Save progress and exit?"):
                    self._engine.cancel()
                    time.sleep(0.5)
                else:
                    return
            self._root.destroy()

        # ── Settings ────────────────────────────────────────────────

        def _read_settings(self):
            self._settings.pause_between_files = self._pause_var.get()
            self._settings.file_timeout = self._timeout_var.get()
            self._settings.max_retries = self._retries_var.get()
            self._settings.verify_after_copy = self._verify_var.get()
            self._settings.skip_symlinks = self._skip_sym_var.get()
            self._settings.preserve_metadata = self._preserve_var.get()

        # ── Queue polling ───────────────────────────────────────────

        def _start_polling(self):
            if not self._polling:
                self._polling = True
                self._poll_queue()

        def _poll_queue(self):
            try:
                for _ in range(50):  # drain up to 50 messages per tick
                    msg_type, data = self._queue.get_nowait()
                    self._handle_message(msg_type, data)
            except queue.Empty:
                pass

            if (self._engine and self._engine.state not in (
                    EngineState.COMPLETED, EngineState.ERROR, EngineState.IDLE)):
                self._root.after(self.POLL_MS, self._poll_queue)
            else:
                self._polling = False
                # One final drain
                try:
                    while True:
                        msg_type, data = self._queue.get_nowait()
                        self._handle_message(msg_type, data)
                except queue.Empty:
                    pass

        def _handle_message(self, msg_type: MsgType, data):
            if msg_type == MsgType.LOG:
                tag = "info"
                msg = str(data)
                if msg.startswith("OK"):
                    tag = "ok"
                elif any(msg.startswith(w) for w in
                         ("FAIL", "TIMEOUT", "INTEGRITY", "FUSE", "DISK")):
                    tag = "fail"
                elif msg.startswith(("SKIP", "Warning")):
                    tag = "warn"
                self._log(msg, tag)

            elif msg_type == MsgType.FILE_START:
                self._current_file_var.set(f"Copying: {data}")
                self._file_progress['value'] = 0

            elif msg_type == MsgType.FILE_PROGRESS:
                if isinstance(data, dict):
                    total = data.get('total_bytes', 1) or 1
                    done = data.get('bytes_copied', 0)
                    self._file_progress['value'] = (done / total) * 100

            elif msg_type == MsgType.FILE_DONE:
                self._file_progress['value'] = 100

            elif msg_type == MsgType.SCAN_PROGRESS:
                self._current_file_var.set(
                    f"Scanning... ({data} directories)")

            elif msg_type == MsgType.STATE_CHANGE:
                self._current_file_var.set(f"State: {data}")

            elif msg_type == MsgType.STATS_UPDATE:
                if isinstance(data, ProgressStats):
                    self._update_stats(data)

            elif msg_type == MsgType.FINISHED:
                self._on_finished(data)

            elif msg_type == MsgType.ERROR:
                self._log(f"ERROR: {data}", "fail")

        def _update_stats(self, stats: ProgressStats):
            # Overall progress bar (bytes-based, includes current file)
            pct = 0
            if stats.bytes_total > 0:
                pct = (stats.bytes_done / stats.bytes_total) * 100
                self._overall_progress['value'] = pct

            # Update window title with progress and state
            title_pct = f"{int(pct)}%" if stats.bytes_total > 0 else "0%"
            self._root.title(
                f"[{title_pct}] {stats.engine_state.title()} — pCloud Safe Copier"
            )

            # Per-file progress bar from real-time intra-file bytes
            if stats.current_file_total > 0:
                fpct = (stats.current_file_bytes / stats.current_file_total) * 100
                self._file_progress['value'] = fpct
            elif stats.current_file_bytes == 0 and stats.current_file_total == 0:
                # Between files — leave at 100 or 0
                pass
            self._files_var.set(
                f"Files: {stats.files_done}/{stats.files_total}")
            self._bytes_var.set(
                f"{fmt_bytes(stats.bytes_done)} / {fmt_bytes(stats.bytes_total)}")
            self._rate_var.set(
                f"Rate: {fmt_bytes(stats.transfer_rate_bps)}/s")

            # ETA with "Finish at" time
            eta_str = fmt_duration(stats.eta_seconds)
            if stats.eta_seconds > 0 and stats.engine_state == "COPYING":
                finish_time = datetime.now() + timedelta(seconds=stats.eta_seconds)
                eta_str += f" (Finish at {finish_time.strftime('%H:%M')})"
            self._eta_var.set(f"ETA: {eta_str}")

            self._errors_var.set(
                f"Failed: {stats.files_failed} | "
                f"Skipped: {stats.files_skipped}")
            if stats.leaked_threads > 0:
                self._leaked_var.set(
                    f"Stuck threads: {stats.leaked_threads}")

        def _on_finished(self, summary: dict):
            self._start_btn.config(state=tk.NORMAL)
            self._open_dest_btn.config(state=tk.NORMAL)
            self._manifest_btn.config(state=tk.NORMAL)
            self._pause_btn.config(state=tk.DISABLED)
            self._resume_btn.config(state=tk.DISABLED)
            self._cancel_btn.config(state=tk.DISABLED)

            if not summary:
                return

            state = summary.get('state', '')
            total = summary.get('files_total', 0)
            completed = summary.get('files_completed', 0)
            failed = summary.get('files_failed', 0)
            skipped = summary.get('files_skipped', 0)
            elapsed = summary.get('elapsed_seconds', 0)
            bytes_done = summary.get('bytes_completed', 0)

            msg = (
                f"{'Completed' if state == 'COMPLETED' else 'Stopped'}\n\n"
                f"Files copied: {completed}/{total}\n"
                f"Files skipped: {skipped}\n"
                f"Files failed: {failed}\n"
                f"Data transferred: {fmt_bytes(bytes_done)}\n"
                f"Time elapsed: {fmt_duration(elapsed)}\n"
            )
            if failed > 0 or state != 'COMPLETED':
                msg += (
                    "\nA resume manifest has been saved.\n"
                    "Use 'Load Manifest' to continue later."
                )

            self._current_file_var.set(
                f"Done — {completed}/{total} files copied")
            self._log(msg.replace('\n', ' | '), "info")

            if state == 'COMPLETED' and failed == 0:
                messagebox.showinfo("Copy Complete", msg)
            else:
                messagebox.showwarning("Copy Finished", msg)

        # ── Log helper ──────────────────────────────────────────────

        def _log(self, message: str, tag: str = "info"):
            # Only auto-scroll if the user is already at the bottom
            at_bottom = self._log_text.yview()[1] >= 0.99

            self._log_text.config(state=tk.NORMAL)
            ts = datetime.now().strftime("%H:%M:%S")
            self._log_text.insert(tk.END, f"[{ts}] {message}\n", tag)

            # Prune to 1000 lines
            line_count = int(self._log_text.index('end-1c').split('.')[0])
            if line_count > 1000:
                self._log_text.delete('1.0', f'{line_count - 1000}.0')

            if at_bottom:
                self._log_text.see(tk.END)
            self._log_text.config(state=tk.DISABLED)

        def run(self):
            self._root.mainloop()

    app = CopierGUI()
    app.run()


# ── CLI mode ───────────────────────────────────────────────────────────────

def cli_mode(source: str, dest: str, **kwargs):
    """Run copy in CLI mode (no GUI)."""
    settings = CopySettings(**{
        k: v for k, v in kwargs.items()
        if k in CopySettings.__dataclass_fields__ and v is not None
    })
    msg_queue: queue.Queue = queue.Queue()
    engine = CopyEngine(msg_queue, settings)

    print(f"pCloud Safe Copier v{__version__}")
    print(f"Source:      {source}")
    print(f"Destination: {dest}")
    print(f"Settings:    pause={settings.pause_between_files}s, "
          f"timeout={settings.file_timeout}s, "
          f"retries={settings.max_retries}")
    print("-" * 60)

    engine.start(source, dest)

    while engine.state not in (EngineState.COMPLETED, EngineState.ERROR,
                                EngineState.IDLE):
        try:
            msg_type, data = msg_queue.get(timeout=0.5)
            if msg_type == MsgType.LOG:
                print(f"  {data}")
            elif msg_type == MsgType.STATS_UPDATE and isinstance(data, ProgressStats):
                sys.stdout.write(
                    f"\r  [{data.files_done}/{data.files_total}] "
                    f"{fmt_bytes(data.bytes_done)}/{fmt_bytes(data.bytes_total)} "
                    f"({fmt_bytes(data.transfer_rate_bps)}/s) "
                    f"ETA: {fmt_duration(data.eta_seconds)}    ")
                sys.stdout.flush()
            elif msg_type == MsgType.FINISHED:
                print()
                summary = data
                print("=" * 60)
                print(f"State:    {summary.get('state', '?')}")
                print(f"Files:    {summary.get('files_completed', 0)}"
                      f"/{summary.get('files_total', 0)}")
                print(f"Failed:   {summary.get('files_failed', 0)}")
                print(f"Skipped:  {summary.get('files_skipped', 0)}")
                print(f"Data:     {fmt_bytes(summary.get('bytes_completed', 0))}")
                print(f"Time:     {fmt_duration(summary.get('elapsed_seconds', 0))}")
                print("=" * 60)
            elif msg_type == MsgType.ERROR:
                print(f"\n  ERROR: {data}")
        except queue.Empty:
            pass


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(
                Path.home() / '.pcloud_copier.log', encoding='utf-8'),
            logging.StreamHandler(),
        ]
    )

    if len(sys.argv) == 1:
        build_gui()
    elif sys.argv[1] in ('--help', '-h'):
        print(__doc__)
        print("CLI usage: python3 pcloud_copier.py SOURCE DEST [OPTIONS]")
        print("  --pause SECONDS     Pause between files (default: 1.0)")
        print("  --timeout SECONDS   Per-file timeout (default: 120)")
        print("  --retries N         Max retries per file (default: 3)")
        print("  --no-verify         Skip hash verification")
        print()
        print("GUI usage: python3 pcloud_copier.py")
    elif len(sys.argv) >= 3:
        source = sys.argv[1]
        dest = sys.argv[2]
        kwargs = {}
        args = sys.argv[3:]
        i = 0
        while i < len(args):
            if args[i] == '--pause' and i + 1 < len(args):
                kwargs['pause_between_files'] = float(args[i + 1])
                i += 2
            elif args[i] == '--timeout' and i + 1 < len(args):
                kwargs['file_timeout'] = float(args[i + 1])
                i += 2
            elif args[i] == '--retries' and i + 1 < len(args):
                kwargs['max_retries'] = int(args[i + 1])
                i += 2
            elif args[i] == '--no-verify':
                kwargs['verify_after_copy'] = False
                i += 1
            else:
                print(f"Unknown option: {args[i]}")
                sys.exit(1)
        cli_mode(source, dest, **kwargs)
    else:
        print("Usage: python3 pcloud_copier.py [SOURCE DEST] [OPTIONS]")
        print("Run with --help for details.")
        sys.exit(1)


if __name__ == '__main__':
    main()
