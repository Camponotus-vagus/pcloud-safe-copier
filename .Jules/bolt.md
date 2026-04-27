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
