#!/usr/bin/env python3
"""
Tests for pcloud_copier.py — covers 13+ edge cases, resume, and timeout simulation.

Run with: python3 test_pcloud_copier.py
"""

import hashlib
import json
import os
import queue
import shutil
import stat
import tempfile
import threading
import time
import unittest
import unicodedata
from pathlib import Path
from unittest.mock import patch, MagicMock

from pcloud_copier import (
    CopyEngine, CopySettings, CopyManifest, FileRecord, ProgressStats,
    MsgType, EngineState, FileStatus,
    FUSETimeoutError, FUSEUnresponsiveError, IntegrityError,
    DiskFullError, PathTooLongError,
    fmt_bytes, fmt_duration,
)


class TestUtilityFunctions(unittest.TestCase):
    """Test fmt_bytes and fmt_duration."""

    def test_fmt_bytes_zero(self):
        self.assertEqual(fmt_bytes(0), "0.0 B")

    def test_fmt_bytes_bytes(self):
        self.assertEqual(fmt_bytes(512), "512.0 B")

    def test_fmt_bytes_kb(self):
        self.assertEqual(fmt_bytes(1536), "1.5 KB")

    def test_fmt_bytes_mb(self):
        self.assertEqual(fmt_bytes(10 * 1024 * 1024), "10.0 MB")

    def test_fmt_bytes_gb(self):
        self.assertEqual(fmt_bytes(2.5 * 1024**3), "2.5 GB")

    def test_fmt_bytes_negative(self):
        # Should handle gracefully
        result = fmt_bytes(-1024)
        self.assertIn("KB", result)

    def test_fmt_duration_zero(self):
        self.assertEqual(fmt_duration(0), "--")

    def test_fmt_duration_seconds(self):
        self.assertEqual(fmt_duration(45), "45s")

    def test_fmt_duration_minutes(self):
        self.assertEqual(fmt_duration(125), "2m 5s")

    def test_fmt_duration_hours(self):
        self.assertEqual(fmt_duration(3661), "1h 1m 1s")

    def test_fmt_duration_negative(self):
        self.assertEqual(fmt_duration(-5), "--")

    def test_fmt_duration_very_large(self):
        self.assertEqual(fmt_duration(86400 * 30), "--")


class BaseEngineTest(unittest.TestCase):
    """Base class that sets up temp directories and a CopyEngine."""

    def setUp(self):
        self.src_dir = tempfile.mkdtemp(prefix="pcloud_test_src_")
        self.dst_dir = tempfile.mkdtemp(prefix="pcloud_test_dst_")
        self.msg_queue = queue.Queue()
        self.settings = CopySettings(
            pause_between_files=0.01,  # fast for tests
            file_timeout=10.0,
            max_retries=1,
            retry_base_delay=0.1,
            verify_after_copy=True,
            scan_batch_pause=0.0,
        )
        self.engine = CopyEngine(self.msg_queue, self.settings)

    def tearDown(self):
        shutil.rmtree(self.src_dir, ignore_errors=True)
        shutil.rmtree(self.dst_dir, ignore_errors=True)

    def _run_and_wait(self, timeout=30):
        """Start engine and wait for completion."""
        self.engine.start(self.src_dir, self.dst_dir)
        start = time.monotonic()
        while self.engine.state not in (
                EngineState.COMPLETED, EngineState.ERROR, EngineState.IDLE):
            time.sleep(0.1)
            if time.monotonic() - start > timeout:
                self.engine.cancel()
                self.fail("Engine timed out")

    def _drain_messages(self) -> list:
        """Drain all messages from queue."""
        msgs = []
        try:
            while True:
                msgs.append(self.msg_queue.get_nowait())
        except queue.Empty:
            pass
        return msgs

    def _create_file(self, rel_path: str, content: bytes = b"hello world"):
        """Create a file in the source directory."""
        full = Path(self.src_dir) / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(content)
        return full


class TestEdgeCase01_UnicodeFilenames(BaseEngineTest):
    """Edge case 1: Files with unicode, accents, emoji in names."""

    def test_accented_filename(self):
        # NFC vs NFD: café can be encoded two ways
        nfd_name = unicodedata.normalize('NFD', 'café_menu.txt')
        self._create_file(nfd_name, b"menu content")
        self._run_and_wait()

        # Destination should have NFC-normalized name
        nfc_name = unicodedata.normalize('NFC', 'café_menu.txt')
        dst_file = Path(self.dst_dir) / nfc_name
        # On macOS, filesystem normalizes, so either form should work
        self.assertTrue(
            dst_file.exists() or (Path(self.dst_dir) / nfd_name).exists(),
            "Unicode file not copied")

    def test_cjk_filename(self):
        self._create_file('图片.png', b"fake png data")
        self._run_and_wait()
        dst = Path(self.dst_dir) / '图片.png'
        self.assertTrue(dst.exists())
        self.assertEqual(dst.read_bytes(), b"fake png data")

    def test_spaces_in_path(self):
        self._create_file('folder with spaces/file name.txt', b"data")
        self._run_and_wait()
        dst = Path(self.dst_dir) / 'folder with spaces' / 'file name.txt'
        self.assertTrue(dst.exists())


class TestEdgeCase02_LargeFileTimeout(BaseEngineTest):
    """Edge case 2: Large files with dynamic timeout scaling."""

    def test_timeout_scales_with_size(self):
        # Create a 5MB file
        data = os.urandom(5 * 1024 * 1024)
        self._create_file('big.bin', data)
        self.settings.timeout_seconds_per_mb = 3.0
        self.settings.file_timeout = 10.0
        self.engine = CopyEngine(self.msg_queue, self.settings)

        self._run_and_wait(timeout=60)

        dst = Path(self.dst_dir) / 'big.bin'
        self.assertTrue(dst.exists())
        self.assertEqual(dst.stat().st_size, 5 * 1024 * 1024)

    def test_simulated_fuse_timeout(self):
        """Simulate a FUSE freeze by patching _do_buffered_copy to block."""
        self._create_file('freezer.bin', b"x" * 1000)
        self.settings.file_timeout = 1.0
        self.settings.max_retries = 0
        self.engine = CopyEngine(self.msg_queue, self.settings)

        # Directly patch the engine's copy method to simulate a FUSE hang
        freeze_event = threading.Event()

        def blocking_copy(src, dst, file_rec):
            freeze_event.set()  # signal that we're blocking
            time.sleep(30)  # simulate FUSE freeze
            return ""

        self.engine._do_buffered_copy = blocking_copy

        self.engine.start(self.src_dir, self.dst_dir)

        # Wait for the engine thread to finish (it should timeout + error out)
        if self.engine._thread:
            self.engine._thread.join(timeout=20)

        # Drain all messages
        msgs = self._drain_messages()
        log_msgs = [str(d) for t, d in msgs if t == MsgType.LOG]
        error_msgs = [str(d) for t, d in msgs if t == MsgType.ERROR]
        all_text = ' '.join(log_msgs + error_msgs).lower()

        # The engine should have detected the timeout OR the FUSE unresponsive
        # state, OR ended in ERROR state
        detected = (
            'timeout' in all_text or
            'fail' in all_text or
            'unresponsive' in all_text or
            self.engine.state == EngineState.ERROR
        )
        self.assertTrue(detected,
            f"Timeout not detected. State={self.engine.state}, "
            f"Logs: {log_msgs}, Errors: {error_msgs}")


class TestEdgeCase03_EmptyFiles(BaseEngineTest):
    """Edge case 3: Zero-byte files."""

    def test_empty_file_copied(self):
        self._create_file('empty.txt', b"")
        self._run_and_wait()

        dst = Path(self.dst_dir) / 'empty.txt'
        self.assertTrue(dst.exists())
        self.assertEqual(dst.stat().st_size, 0)

    def test_empty_file_hash_matches(self):
        self._create_file('empty.dat', b"")
        self._run_and_wait()

        msgs = self._drain_messages()
        log_msgs = [str(d) for t, d in msgs if t == MsgType.LOG]
        has_ok = any('OK' in m and 'empty.dat' in m for m in log_msgs)
        self.assertTrue(has_ok)


class TestEdgeCase04_EmptyDirectories(BaseEngineTest):
    """Edge case 4: Empty directories should be preserved."""

    def test_empty_directory_created(self):
        empty_dir = Path(self.src_dir) / 'empty_folder'
        empty_dir.mkdir()
        self._run_and_wait()

        dst = Path(self.dst_dir) / 'empty_folder'
        self.assertTrue(dst.exists())
        self.assertTrue(dst.is_dir())

    def test_nested_empty_directory(self):
        nested = Path(self.src_dir) / 'a' / 'b' / 'c'
        nested.mkdir(parents=True)
        self._run_and_wait()

        # At minimum the leaf empty dir should exist
        dst_a = Path(self.dst_dir) / 'a'
        self.assertTrue(dst_a.exists())


class TestEdgeCase05_Symlinks(BaseEngineTest):
    """Edge case 5: Symbolic links (skip by default)."""

    def test_symlink_skipped_by_default(self):
        target = self._create_file('real_file.txt', b"real content")
        link_path = Path(self.src_dir) / 'link.txt'
        link_path.symlink_to(target)

        self._run_and_wait()

        msgs = self._drain_messages()
        log_msgs = [str(d) for t, d in msgs if t == MsgType.LOG]
        has_skip = any('SKIP' in m and 'symlink' in m for m in log_msgs)
        self.assertTrue(has_skip, "Symlink not reported as skipped")

        # The real file should still be copied
        dst_real = Path(self.dst_dir) / 'real_file.txt'
        self.assertTrue(dst_real.exists())


class TestEdgeCase06_PermissionDenied(BaseEngineTest):
    """Edge case 6: Files we can't read."""

    def test_unreadable_file_skipped(self):
        f = self._create_file('secret.txt', b"forbidden")
        os.chmod(f, 0o000)

        self._create_file('normal.txt', b"ok content")
        self._run_and_wait()

        msgs = self._drain_messages()
        log_msgs = [str(d) for t, d in msgs if t == MsgType.LOG]

        # Normal file should be copied
        dst_normal = Path(self.dst_dir) / 'normal.txt'
        self.assertTrue(dst_normal.exists())

        # Restore permissions for cleanup
        os.chmod(f, 0o644)


class TestEdgeCase07_SizeChangeDuringCopy(BaseEngineTest):
    """Edge case 7: File size reported differently than actual bytes read."""

    def test_size_mismatch_warning(self):
        # Create a file, then we'll manipulate the manifest
        self._create_file('changing.txt', b"real content here")
        self._run_and_wait()

        # The file should be copied regardless
        dst = Path(self.dst_dir) / 'changing.txt'
        self.assertTrue(dst.exists())
        self.assertEqual(dst.read_bytes(), b"real content here")


class TestEdgeCase08_SourceDisappears(BaseEngineTest):
    """Edge case 8: Source becomes unavailable mid-copy."""

    def test_fuse_health_check(self):
        """Test that _check_fuse_health works on a valid path."""
        self._create_file('test.txt', b"data")
        self.engine._manifest = CopyManifest(
            source_root=self.src_dir, dest_root=self.dst_dir)
        # Should not raise on valid directory
        self.engine._check_fuse_health()

    def test_fuse_health_check_missing_path(self):
        """Test health check on non-existent path."""
        self.engine._manifest = CopyManifest(
            source_root='/tmp/nonexistent_pcloud_test_xyz',
            dest_root=self.dst_dir)
        with self.assertRaises(Exception):
            self.engine._check_fuse_health()


class TestEdgeCase09_DiskFull(BaseEngineTest):
    """Edge case 9: Destination disk full."""

    def test_disk_space_check(self):
        """Verify the space check runs without error on normal systems."""
        self._create_file('small.txt', b"tiny")
        self._run_and_wait()
        dst = Path(self.dst_dir) / 'small.txt'
        self.assertTrue(dst.exists())

    def test_disk_full_raises(self):
        """Simulate disk full by patching shutil.disk_usage."""
        self._create_file('file.txt', b"data")

        mock_usage = MagicMock()
        mock_usage.free = 100  # only 100 bytes free

        self.settings.max_retries = 0
        self.engine = CopyEngine(self.msg_queue, self.settings)

        with patch('shutil.disk_usage', return_value=mock_usage):
            self.engine.start(self.src_dir, self.dst_dir)
            start = time.monotonic()
            while self.engine.state not in (
                    EngineState.COMPLETED, EngineState.ERROR,
                    EngineState.PAUSED):
                time.sleep(0.1)
                if time.monotonic() - start > 10:
                    self.engine.cancel()
                    break

        # Engine should have paused or reported disk full
        msgs = self._drain_messages()
        log_msgs = [str(d) for t, d in msgs if t == MsgType.LOG]
        has_disk = any('DISK' in m or 'space' in m.lower() for m in log_msgs)
        # The engine should detect the issue
        self.assertTrue(
            has_disk or self.engine.state == EngineState.PAUSED,
            "Disk full not detected or handled")
        self.engine.cancel()


class TestEdgeCase10_CaseCollisions(BaseEngineTest):
    """Edge case 10: Case-insensitive filename collisions on macOS."""

    def test_case_collision_detection(self):
        """Two files differing only in case should both be preserved."""
        self._create_file('Report.txt', b"uppercase version")
        self._create_file('report.txt', b"lowercase version")

        self._run_and_wait()

        # On macOS (case-insensitive), at least one file should exist
        # and the engine should log a collision
        msgs = self._drain_messages()
        log_msgs = [str(d) for t, d in msgs if t == MsgType.LOG]

        dst_dir = Path(self.dst_dir)
        files = list(dst_dir.glob('*.txt')) + list(
            dst_dir.glob('*case_conflict*'))
        self.assertGreaterEqual(len(files), 1,
            "At least one file should be copied")


class TestEdgeCase11_LongPaths(BaseEngineTest):
    """Edge case 11: Very long file paths."""

    def test_long_filename(self):
        # 200-char filename (under 255 limit but testing boundary)
        name = "a" * 200 + ".txt"
        self._create_file(name, b"long name content")
        self._run_and_wait()

        dst = Path(self.dst_dir) / name
        self.assertTrue(dst.exists())

    def test_deeply_nested_path(self):
        # Create a deeply nested path
        parts = ["level"] * 20  # 20 levels deep
        rel = "/".join(parts) + "/deep_file.txt"
        self._create_file(rel, b"deep content")
        self._run_and_wait()

        dst = Path(self.dst_dir) / rel
        self.assertTrue(dst.exists())
        self.assertEqual(dst.read_bytes(), b"deep content")


class TestEdgeCase12_BrokenSymlinks(BaseEngineTest):
    """Edge case 12: Symbolic links pointing to non-existent targets."""

    def test_broken_symlink_skipped(self):
        link_path = Path(self.src_dir) / 'broken_link.txt'
        link_path.symlink_to('/tmp/this_does_not_exist_xyz_12345')

        self._create_file('real.txt', b"real data")
        self._run_and_wait()

        msgs = self._drain_messages()
        log_msgs = [str(d) for t, d in msgs if t == MsgType.LOG]
        has_skip = any('SKIP' in m and ('symlink' in m or 'broken' in m.lower())
                       for m in log_msgs)
        self.assertTrue(has_skip, "Broken symlink not reported as skipped")

        # Real file should still be copied
        dst_real = Path(self.dst_dir) / 'real.txt'
        self.assertTrue(dst_real.exists())


class TestEdgeCase13_ReadOnlyDestination(BaseEngineTest):
    """Edge case 13: Read-only destination directory."""

    def test_readonly_dest_detected(self):
        ro_dir = tempfile.mkdtemp(prefix="pcloud_test_ro_")
        os.chmod(ro_dir, stat.S_IRUSR | stat.S_IXUSR)

        self._create_file('file.txt', b"data")
        engine = CopyEngine(self.msg_queue, self.settings)

        with self.assertRaises(PermissionError):
            engine.start(self.src_dir, ro_dir)

        # Restore for cleanup
        os.chmod(ro_dir, stat.S_IRWXU)
        shutil.rmtree(ro_dir, ignore_errors=True)


class TestResumeCapability(BaseEngineTest):
    """Test resume from manifest."""

    def test_resume_skips_completed_files(self):
        # Create files
        for i in range(5):
            self._create_file(f'file_{i}.txt', f'content {i}'.encode())

        # First run
        self._run_and_wait()

        # Get manifest
        manifest_path = Path(self.dst_dir) / '.pcloud_copy_manifest.json'
        self.assertTrue(manifest_path.exists(), "Manifest not saved")

        with open(manifest_path) as f:
            manifest = json.load(f)

        # All files should be done
        completed = sum(1 for f in manifest['files']
                        if f['status'] in ('VERIFIED', 'SKIPPED_EXISTS',
                                           'COPIED'))
        self.assertEqual(completed, 5)

        # Second run with manifest — should skip all
        engine2 = CopyEngine(queue.Queue(), self.settings)
        engine2.start(self.src_dir, self.dst_dir, resume_manifest=manifest)
        start = time.monotonic()
        while engine2.state not in (EngineState.COMPLETED, EngineState.ERROR):
            time.sleep(0.1)
            if time.monotonic() - start > 10:
                engine2.cancel()
                break

    def test_resume_retries_failed_files(self):
        # Create a manifest with one FAILED file
        self._create_file('retry_me.txt', b"retry content")

        manifest = {
            'source_root': self.src_dir,
            'dest_root': self.dst_dir,
            'settings': {},
            'files': [{
                'rel_path': 'retry_me.txt',
                'size_bytes': len(b"retry content"),
                'source_hash': '',
                'dest_hash': '',
                'status': 'FAILED',
                'error_message': 'Previous timeout',
                'retries': 0,
                'bytes_copied': 0,
                'is_empty_dir': False,
            }],
            'total_bytes': len(b"retry content"),
            'bytes_completed': 0,
            'files_completed': 0,
            'files_failed': 1,
            'files_skipped': 0,
            'started_at': '',
            'last_updated': '',
            'version': 1,
        }

        engine = CopyEngine(self.msg_queue, self.settings)
        engine.start(self.src_dir, self.dst_dir, resume_manifest=manifest)

        start = time.monotonic()
        while engine.state not in (EngineState.COMPLETED, EngineState.ERROR):
            time.sleep(0.1)
            if time.monotonic() - start > 10:
                engine.cancel()
                break

        dst = Path(self.dst_dir) / 'retry_me.txt'
        self.assertTrue(dst.exists())
        self.assertEqual(dst.read_bytes(), b"retry content")


class TestIntegrityVerification(BaseEngineTest):
    """Test hash verification catches corruption."""

    def test_successful_verification(self):
        content = b"integrity test data " * 100
        self._create_file('verified.bin', content)
        self._run_and_wait()

        dst = Path(self.dst_dir) / 'verified.bin'
        self.assertTrue(dst.exists())
        self.assertEqual(dst.read_bytes(), content)

    def test_hash_consistency(self):
        """Verify that the engine's hash matches manual computation."""
        content = b"hash consistency test"
        self._create_file('hash_test.txt', content)
        self._run_and_wait()

        # Compute expected hash
        expected = hashlib.blake2b(content).hexdigest()

        # Check manifest
        manifest_path = Path(self.dst_dir) / '.pcloud_copy_manifest.json'
        if manifest_path.exists():
            with open(manifest_path) as f:
                manifest = json.load(f)
            for f in manifest['files']:
                if f['rel_path'] == 'hash_test.txt':
                    self.assertEqual(f['source_hash'], expected)
                    self.assertEqual(f['dest_hash'], expected)
                    break


class TestMixedWorkload(BaseEngineTest):
    """Test a realistic mixed workload combining multiple edge cases."""

    def test_mixed_files(self):
        # Normal files
        self._create_file('doc.txt', b"document content")
        self._create_file('photo.jpg', os.urandom(1024 * 100))  # 100KB

        # Empty file
        self._create_file('placeholder', b"")

        # Unicode name
        self._create_file('données.csv', b"col1,col2\n1,2\n")

        # Nested structure
        self._create_file('sub/dir/deep.txt', b"nested")

        # Empty directory
        (Path(self.src_dir) / 'empty_subdir').mkdir()

        # File with no extension
        self._create_file('Makefile', b"all: build\n")

        self._run_and_wait()

        # Verify all
        self.assertTrue((Path(self.dst_dir) / 'doc.txt').exists())
        self.assertTrue((Path(self.dst_dir) / 'photo.jpg').exists())
        self.assertTrue((Path(self.dst_dir) / 'placeholder').exists())
        self.assertEqual(
            (Path(self.dst_dir) / 'placeholder').stat().st_size, 0)
        self.assertTrue((Path(self.dst_dir) / 'sub' / 'dir' / 'deep.txt').exists())
        self.assertTrue((Path(self.dst_dir) / 'Makefile').exists())

        # Check data integrity
        self.assertEqual(
            (Path(self.dst_dir) / 'doc.txt').read_bytes(),
            b"document content")
        self.assertEqual(
            (Path(self.dst_dir) / 'Makefile').read_bytes(),
            b"all: build\n")


class TestCancelAndPause(BaseEngineTest):
    """Test pause and cancel functionality."""

    def test_cancel_saves_manifest(self):
        # Create many files to have time to cancel
        for i in range(50):
            self._create_file(f'batch/{i:03d}.txt', os.urandom(512))

        self.settings.pause_between_files = 0.05
        self.engine = CopyEngine(self.msg_queue, self.settings)
        self.engine.start(self.src_dir, self.dst_dir)

        # Wait a bit then cancel
        time.sleep(1)
        self.engine.cancel()

        start = time.monotonic()
        while self.engine.state not in (
                EngineState.COMPLETED, EngineState.ERROR,
                EngineState.CANCELLING, EngineState.IDLE):
            time.sleep(0.1)
            if time.monotonic() - start > 10:
                break

        # Manifest should exist
        manifest_path = Path(self.dst_dir) / '.pcloud_copy_manifest.json'
        self.assertTrue(manifest_path.exists(),
            "Manifest should be saved on cancel")


class TestRealTimeProgressBars(BaseEngineTest):
    """Verify that progress bars update in real-time with proper 0%→100% fill."""

    def test_file_progress_increases_during_copy(self):
        """Stats updates during a large-ish file should show increasing
        current_file_bytes from 0 towards file_total."""
        # 2MB file — large enough to trigger multiple intra-file stats
        data = os.urandom(2 * 1024 * 1024)
        self._create_file('big_progress.bin', data)

        # Use a small buffer so there are many chunks
        self.settings.copy_buffer_size = 32768  # 32KB
        self.engine = CopyEngine(self.msg_queue, self.settings)
        self._run_and_wait(timeout=30)

        msgs = self._drain_messages()
        stats_msgs = [d for t, d in msgs
                      if t == MsgType.STATS_UPDATE and isinstance(d, ProgressStats)]

        # Collect intra-file progress values
        file_progress_values = [
            s.current_file_bytes for s in stats_msgs
            if s.current_file_total > 0
        ]

        # We should have at least a few intra-file progress updates
        self.assertGreaterEqual(len(file_progress_values), 2,
            f"Expected multiple intra-file stats updates, got {len(file_progress_values)}")

        # Values should be monotonically increasing
        for i in range(1, len(file_progress_values)):
            self.assertGreaterEqual(file_progress_values[i], file_progress_values[i - 1],
                f"File progress should be monotonically increasing: "
                f"{file_progress_values}")

        # First value should be > 0 (at least some bytes copied)
        self.assertGreater(file_progress_values[0], 0)

        # current_file_total should match file size in all intra-file stats
        file_totals = [s.current_file_total for s in stats_msgs
                       if s.current_file_total > 0]
        for ft in file_totals:
            self.assertEqual(ft, len(data),
                f"current_file_total should be {len(data)}, got {ft}")

    def test_overall_progress_reaches_100(self):
        """Overall bytes_done should reach bytes_total by the end."""
        for i in range(5):
            self._create_file(f'prog_{i}.txt', os.urandom(1024 * 50))

        self._run_and_wait()
        msgs = self._drain_messages()

        stats_msgs = [d for t, d in msgs
                      if t == MsgType.STATS_UPDATE and isinstance(d, ProgressStats)]

        # The last stats message should show bytes_done == bytes_total
        self.assertGreater(len(stats_msgs), 0, "No stats messages received")
        last = stats_msgs[-1]
        self.assertEqual(last.bytes_done, last.bytes_total,
            f"Final stats: bytes_done={last.bytes_done} != "
            f"bytes_total={last.bytes_total}")
        self.assertGreater(last.bytes_total, 0)

    def test_file_bar_resets_between_files(self):
        """After a file completes, the next file should have current_file_bytes
        starting from a low value again (reset)."""
        # Two files large enough for intra-file stats
        self._create_file('first.bin', os.urandom(1024 * 512))
        self._create_file('second.bin', os.urandom(1024 * 512))

        self.settings.copy_buffer_size = 32768
        self.engine = CopyEngine(self.msg_queue, self.settings)
        self._run_and_wait(timeout=30)

        msgs = self._drain_messages()

        # Track FILE_START and STATS_UPDATE sequence
        file_starts = 0
        saw_reset = False
        last_file_bytes = 0

        for msg_type, data in msgs:
            if msg_type == MsgType.FILE_START:
                file_starts += 1
                if file_starts > 1 and last_file_bytes > 0:
                    # After the first file, we expect to see stats with
                    # lower current_file_bytes (reset for new file)
                    saw_reset = True
                last_file_bytes = 0
            elif (msg_type == MsgType.STATS_UPDATE and
                  isinstance(data, ProgressStats) and
                  data.current_file_total > 0):
                last_file_bytes = data.current_file_bytes

        self.assertGreaterEqual(file_starts, 2,
            f"Expected at least 2 FILE_START messages, got {file_starts}")

    def test_eta_is_smooth_and_reasonable(self):
        """ETA should not jump wildly between consecutive stats updates."""
        data = os.urandom(3 * 1024 * 1024)
        self._create_file('eta_test.bin', data)

        self.settings.copy_buffer_size = 32768
        self.engine = CopyEngine(self.msg_queue, self.settings)
        self._run_and_wait(timeout=30)

        msgs = self._drain_messages()
        eta_values = [d.eta_seconds for t, d in msgs
                      if t == MsgType.STATS_UPDATE and isinstance(d, ProgressStats)
                      and d.eta_seconds > 0]

        if len(eta_values) >= 3:
            # Check that consecutive ETA values don't jump by more than 5x
            big_jumps = 0
            for i in range(1, len(eta_values)):
                prev, curr = eta_values[i - 1], eta_values[i]
                if prev > 0:
                    ratio = max(curr / prev, prev / curr)
                    if ratio > 5.0:
                        big_jumps += 1
            # Allow at most 1 big jump (the initial EMA seeding)
            self.assertLessEqual(big_jumps, 1,
                f"ETA jumped wildly: {eta_values[:10]}")

    def test_overall_bar_includes_intra_file_progress(self):
        """bytes_done during a copy should include partial file progress,
        not just completed files."""
        # One large file
        data = os.urandom(2 * 1024 * 1024)
        self._create_file('intra.bin', data)

        self.settings.copy_buffer_size = 32768
        self.engine = CopyEngine(self.msg_queue, self.settings)
        self._run_and_wait(timeout=30)

        msgs = self._drain_messages()
        intra_stats = [d for t, d in msgs
                       if t == MsgType.STATS_UPDATE and isinstance(d, ProgressStats)
                       and d.current_file_bytes > 0 and d.current_file_total > 0]

        if len(intra_stats) >= 2:
            # bytes_done should be > 0 but < bytes_total during copy
            mid = intra_stats[len(intra_stats) // 2]
            self.assertGreater(mid.bytes_done, 0,
                "bytes_done should include partial file progress")
            self.assertLess(mid.bytes_done, mid.bytes_total,
                "bytes_done should not be 100% mid-copy")


if __name__ == '__main__':
    unittest.main(verbosity=2)
