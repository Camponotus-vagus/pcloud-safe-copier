## 2025-05-18 - [Optimization of Hot-Loop Object Creation]
**Learning:** In performance-critical loops such as directory scanning, using `dataclasses.asdict()` on a per-item basis introduces significant overhead due to its recursive nature and generalized data handling. Replacing it with manual dictionary literal creation can yield a substantial (30%+) speedup for operations involving thousands of items.
**Action:** Favor manual dictionary construction or specialized conversion methods over `asdict()` for high-volume item creation in performance-sensitive paths.

## 2026-04-13 - [Safe Optimization of Manifest Loading]
**Learning:** Replacing `asdict(FileRecord(**f))` with a manual dictionary update from a default record provides a ~8.5x speedup while maintaining schema safety for older manifests. Using `dataclasses.fields()` ensures the optimization is robust against dataclass changes.
**Action:** When optimizing dataclass/dictionary round-trips for performance, use a pre-defined default record and manually map keys to preserve functional parity with the original instantiation.

## 2024-05-24 - [Throttling Manifest I/O for Large Datasets]
**Learning:** Frequent JSON serialization and disk I/O for large manifests (e.g., 100k+ file records) can create significant CPU/IO bottlenecks, effectively an O(N^2) overhead as the transfer progresses. JSON serialization speed in Python is heavily impacted by the `indent` parameter; removing it can yield a ~7x speedup for large structures.
**Action:** Implement time-based throttling for state persistence in long-running loops and use compact JSON formatting (no indentation) for machine-read internal state files.

## 2024-06-12 - [Optimizing Directory Scanning in Python]
**Learning:** For large directory structures, `os.path.relpath` is significantly slower than string slicing due to path normalization overhead. Slicing with `path[root_len:].lstrip(os.sep)` is ~3.5x faster. Additionally, iterating over `os.scandir` directly instead of converting to a list avoids unnecessary memory allocation and list creation overhead.
**Action:** Use string slicing for relative path calculation and iterate over `os.scandir` directly in hot scanning loops.

## 2024-06-20 - [Optimizing Rate Calculations and Stats Throttling]
**Learning:** Using a list comprehension to prune a rolling window of samples for rate calculation creates an O(N^2) cumulative overhead when updates are high-frequency. Replacing the list with a 'collections.deque' allows for O(1) pruning with 'popleft()'. Furthermore, mixed byte/time throttling for stats updates can still flood the message queue on fast local transfers; transitioning to a pure time-based throttle (e.g., 0.1s) ensures stable CPU usage regardless of transfer speed.
**Action:** Use 'deque' for rolling windows and favor time-based throttling for UI/stats updates in high-throughput data loops.

## 2026-05-25 - [Optimizing Redundant Directory Creation]
**Learning:** In file-by-file copy operations, especially on high-latency filesystems like FUSE or network mounts, calling `mkdir(parents=True, exist_ok=True)` for every file in a directory adds significant cumulative overhead. Caching the last created directory path allows skipping these expensive syscalls for subsequent files in the same directory.
**Action:** Use a simple 'last_created_dir' cache when performing bulk file operations that require ensuring directory existence.

## 2026-06-29 - [Optimizing Hasher Instantiation]
**Learning:** Using `hashlib.new(name)` repeatedly in hot loops (e.g., file hashing) incurs overhead from string lookups and internal dispatching. Pre-resolving the hasher factory using `getattr(hashlib, name)` (with a fallback to `hashlib.new`) provides a measured ~3.3x speedup for instantiation.
**Action:** Pre-resolve cryptographic hash factories during initialization when the algorithm remains constant across operations.

## 2026-07-06 - [Optimizing File I/O with readinto]
**Learning:** Using `f.readinto()` with a pre-allocated `bytearray` and `memoryview` in high-volume I/O loops (like file copying) significantly reduces memory allocation and GC overhead. For a 200MB file, this provided a measurable ~27% speedup in the read loop compared to standard `f.read(buf_size)`.
**Action:** Favor `readinto()` with buffer reuse for hot I/O loops where large amounts of data are processed in chunks.

## 2026-07-06 - [Leveraging C-Optimized File Hashing]
**Learning:** `hashlib.file_digest` (introduced in Python 3.11) provides a C-optimized implementation for hashing files that is more efficient than manual Python loops. However, it requires a fallback for older Python versions to maintain environment compatibility.
**Action:** Use `hashlib.file_digest` for file hashing when available, ensuring a fallback is provided for pre-3.11 environments.
