## 2025-05-18 - [Optimization of Hot-Loop Object Creation]
**Learning:** In performance-critical loops such as directory scanning, using `dataclasses.asdict()` on a per-item basis introduces significant overhead due to its recursive nature and generalized data handling. Replacing it with manual dictionary literal creation can yield a substantial (30%+) speedup for operations involving thousands of items.
**Action:** Favor manual dictionary construction or specialized conversion methods over `asdict()` for high-volume item creation in performance-sensitive paths.

## 2026-04-13 - [Safe Optimization of Manifest Loading]
**Learning:** Replacing `asdict(FileRecord(**f))` with a manual dictionary update from a default record provides a ~8.5x speedup while maintaining schema safety for older manifests. Using `dataclasses.fields()` ensures the optimization is robust against dataclass changes.
**Action:** When optimizing dataclass/dictionary round-trips for performance, use a pre-defined default record and manually map keys to preserve functional parity with the original instantiation.
