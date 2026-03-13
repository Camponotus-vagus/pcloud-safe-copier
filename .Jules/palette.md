## 2025-05-14 - [Unicode Normalization Strategy for Cross-Platform File Copying]
**Learning:** In cross-platform file operations, it is critical to use the original file name for source disk access and only apply normalization (e.g., NFC) when determining the destination path. Normalizing the source path too early can prevent the OS from finding files if they exist in a different normalization form on the disk (e.g., NFD on macOS vs. NFC expected by the application).
**Action:** Always store source file paths exactly as they appear on the filesystem and only normalize the destination path before creation.

## 2025-05-15 - [Tkinter Tooltip and Font Fallback Best Practices]
**Learning:** Tooltip positioning in Tkinter should use `winfo_rootx/y` and `winfo_height` for universal widget compatibility, as `bbox("insert")` is only for text widgets and causes crashes elsewhere. For cross-platform monospaced logs, explicitly check `font.families()` to select the best available face (e.g., Menlo, Consolas).
**Action:** Use root coordinates for tooltips and system-verified family names for fallback fonts in Tkinter GUIs.
