## 2025-05-14 - [Unicode Normalization Strategy for Cross-Platform File Copying]
**Learning:** In cross-platform file operations, it is critical to use the original file name for source disk access and only apply normalization (e.g., NFC) when determining the destination path. Normalizing the source path too early can prevent the OS from finding files if they exist in a different normalization form on the disk (e.g., NFD on macOS vs. NFC expected by the application).
**Action:** Always store source file paths exactly as they appear on the filesystem and only normalize the destination path before creation.

## 2025-05-15 - [Monospace Font Consistency and Color Contrast in Tkinter]
**Learning:** For log displays in Tkinter, using "TkFixedFont" is more robust than naming specific fonts (like "Menlo"), as it automatically selects the system's default monospace font. Additionally, using specific hex codes instead of standard color names (e.g., "#af5200" instead of "orange") allows for precise control over color contrast to meet accessibility standards on light backgrounds.
**Action:** Use "TkFixedFont" for code/log views and prefer high-contrast hex codes for semantic text tags.
