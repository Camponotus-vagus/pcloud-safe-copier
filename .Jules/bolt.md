## 2025-05-18 - [Optimization of Hot-Loop Object Creation]
**Learning:** In performance-critical loops such as directory scanning, using `dataclasses.asdict()` on a per-item basis introduces significant overhead due to its recursive nature and generalized data handling. Replacing it with manual dictionary literal creation can yield a substantial (30%+) speedup for operations involving thousands of items.
**Action:** Favor manual dictionary construction or specialized conversion methods over `asdict()` for high-volume item creation in performance-sensitive paths.
