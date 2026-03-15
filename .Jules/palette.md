## 2025-05-14 - [Unicode Normalization Strategy for Cross-Platform File Copying]
**Learning:** In cross-platform file operations, it is critical to use the original file name for source disk access and only apply normalization (e.g., NFC) when determining the destination path. Normalizing the source path too early can prevent the OS from finding files if they exist in a different normalization form on the disk (e.g., NFD on macOS vs. NFC expected by the application).
**Action:** Always store source file paths exactly as they appear on the filesystem and only normalize the destination path before creation.

## 2025-05-15 - [Tkinter Accessibility and Cross-Platform Polish]
**Learning:** Improving accessibility and polish in Tkinter desktop apps can be achieved without new dependencies by implementing custom tooltip classes (using `winfo_rootx/y` for positioning) and using `font.families()` to dynamically select the best available monospaced fonts on each OS. Also, ensure command handlers bound to keys accept an optional `event` parameter.
**Action:** Use a dynamic font selection helper for logs and always include an `event=None` parameter in method signatures targeted by both buttons and key bindings.

## 2025-05-16 - [Intelligent Log Scrolling in Tkinter]
**Learning:** Automatic scrolling to the bottom of a log window (using `see(tk.END)`) can be disruptive if the user has scrolled up to inspect previous entries. A better UX pattern is "intelligent scrolling," where auto-scrolling only occurs if the user is already at the bottom of the log.
**Action:** Check the current scroll position using `widget.yview()[1] >= 0.99` before inserting new log entries; only call `see(tk.END)` if the condition is met.
