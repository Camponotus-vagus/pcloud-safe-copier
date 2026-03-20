## 2025-05-14 - [Unicode Normalization Strategy for Cross-Platform File Copying]
**Learning:** In cross-platform file operations, it is critical to use the original file name for source disk access and only apply normalization (e.g., NFC) when determining the destination path. Normalizing the source path too early can prevent the OS from finding files if they exist in a different normalization form on the disk (e.g., NFD on macOS vs. NFC expected by the application).
**Action:** Always store source file paths exactly as they appear on the filesystem and only normalize the destination path before creation.

## 2025-05-15 - [Tkinter Accessibility and Cross-Platform Polish]
**Learning:** Improving accessibility and polish in Tkinter desktop apps can be achieved without new dependencies by implementing custom tooltip classes (using `winfo_rootx/y` for positioning) and using `font.families()` to dynamically select the best available monospaced fonts on each OS. Also, ensure command handlers bound to keys accept an optional `event` parameter.
**Action:** Use a dynamic font selection helper for logs and always include an `event=None` parameter in method signatures targeted by both buttons and key bindings.

## 2025-05-16 - [Intelligent Log Scrolling for Real-Time Feedback]
**Learning:** In applications with high-frequency logging (like file transfers), auto-scrolling can be disruptive if the user is trying to inspect previous entries. Implementing "smart" scrolling that only triggers if the user is already at the bottom of the view improves the experience by respecting the user's focus.
**Action:** Before calling `see(tk.END)` in a Tkinter Text/ScrolledText widget, check if `widget.yview()[1] >= 0.99`.

## 2025-05-17 - [Enhancing Path Input Usability and Status Visibility]
**Learning:** For desktop applications where users frequently copy or replace file paths, binding `<FocusIn>` to `selection_range(0, tk.END)` provides a significant usability boost. Additionally, mirroring progress and state in the window title enables "passive monitoring" from the taskbar/dock, reducing the need for active window focus.
**Action:** In Tkinter apps, always auto-select path entry text on focus and reflect high-level operation status in the root window title.
