## 2025-05-14 - [Unicode Normalization Strategy for Cross-Platform File Copying]
**Learning:** In cross-platform file operations, it is critical to use the original file name for source disk access and only apply normalization (e.g., NFC) when determining the destination path. Normalizing the source path too early can prevent the OS from finding files if they exist in a different normalization form on the disk (e.g., NFD on macOS vs. NFC expected by the application).
**Action:** Always store source file paths exactly as they appear on the filesystem and only normalize the destination path before creation.

## 2025-05-15 - [Tkinter Tooltip and Font Fallback Best Practices]
**Learning:** Tooltip positioning in Tkinter should use `winfo_rootx/y` and `winfo_height` for universal widget compatibility, as `bbox("insert")` is only for text widgets and causes crashes elsewhere. For cross-platform monospaced logs, explicitly check `font.families()` to select the best available face (e.g., Menlo, Consolas).
**Action:** Use root coordinates for tooltips and system-verified family names for fallback fonts in Tkinter GUIs.
## 2025-05-15 - [Monospace Font Consistency and Color Contrast in Tkinter]
**Learning:** For log displays in Tkinter, using "TkFixedFont" is more robust than naming specific fonts (like "Menlo"), as it automatically selects the system's default monospace font. Additionally, using specific hex codes instead of standard color names (e.g., "#af5200" instead of "orange") allows for precise control over color contrast to meet accessibility standards on light backgrounds.
**Action:** Use "TkFixedFont" for code/log views and prefer high-contrast hex codes for semantic text tags.
## 2025-05-15 - [Tkinter Accessibility and Cross-Platform Polish]
**Learning:** Improving accessibility and polish in Tkinter desktop apps can be achieved without new dependencies by implementing custom tooltip classes (using `winfo_rootx/y` for positioning) and using `font.families()` to dynamically select the best available monospaced fonts on each OS. Also, ensure command handlers bound to keys accept an optional `event` parameter.
**Action:** Use a dynamic font selection helper for logs and always include an `event=None` parameter in method signatures targeted by both buttons and key bindings.

## 2025-05-16 - [Intelligent Log Scrolling for Real-Time Feedback]
**Learning:** In applications with high-frequency logging (like file transfers), auto-scrolling can be disruptive if the user is trying to inspect previous entries. Implementing "smart" scrolling that only triggers if the user is already at the bottom of the view improves the experience by respecting the user's focus.
**Action:** Before calling `see(tk.END)` in a Tkinter Text/ScrolledText widget, check if `widget.yview()[1] >= 0.99`.

## 2025-05-17 - [Enhanced Feedback for Long-Running Background Operations]
**Learning:** For desktop applications performing long-running tasks like file copying, providing the absolute completion time (e.g., "Finish at 14:30") is more intuitive for users than a relative countdown (e.g., "30m 5s"). Additionally, reflecting the current state and progress in the window title allows users to monitor the process without switching focus to the application.
**Action:** Always include an absolute timestamp in ETA calculations when possible and update the window title dynamically during active operations.
## 2025-05-17 - [Operational Feedback with Absolute Timestamps]
**Learning:** Providing an ETA in terms of duration (e.g., "3m 32s") is helpful, but augmenting it with an absolute finish time (e.g., "Finish at 14:45") provides better situational awareness for the user. Using `timedelta` makes this easy to implement in Python.
**Action:** Always consider adding absolute finish times to progress displays where an ETA is available.
