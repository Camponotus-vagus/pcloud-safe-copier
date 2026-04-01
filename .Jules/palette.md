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

## 2025-05-16 - [Intelligent Log Scrolling in Tkinter]
**Learning:** Automatically scrolling to the bottom of a log widget on every new entry (using `see(tk.END)`) can frustrate users who have scrolled up to read previous entries. Checking the scroll position via `yview()` allows for "smart" scrolling that only follows the log if the user is already at the bottom.
**Action:** Implement intelligent scrolling by checking if `widget.yview()[1] >= 0.99` before deciding to call `see(tk.END)`.
**Learning:** Automatically snapping to the bottom of a log widget on every update can be frustrating for users trying to read earlier entries. By checking `widget.yview()[1] >= 0.99` before insertion, we can determine if the user is already at the bottom and only then perform the auto-scroll.
**Action:** Implement conditional `see(tk.END)` in log-heavy Tkinter applications to respect user-initiated scrolling.
**Learning:** Automatic scrolling to the bottom of a log window (using `see(tk.END)`) can be disruptive if the user has scrolled up to inspect previous entries. A better UX pattern is "intelligent scrolling," where auto-scrolling only occurs if the user is already at the bottom of the log.
**Action:** Check the current scroll position using `widget.yview()[1] >= 0.99` before inserting new log entries; only call `see(tk.END)` if the condition is met.
## 2025-05-16 - [Intelligent Log Scrolling for Real-Time Feedback]
**Learning:** In applications with high-frequency logging (like file transfers), auto-scrolling can be disruptive if the user is trying to inspect previous entries. Implementing "smart" scrolling that only triggers if the user is already at the bottom of the view improves the experience by respecting the user's focus.
**Action:** Before calling `see(tk.END)` in a Tkinter Text/ScrolledText widget, check if `widget.yview()[1] >= 0.99`.

## 2026-03-27 - [Reliable Auto-Selection in Tkinter Entry Widgets]
**Learning:** When implementing auto-selection on `<FocusIn>` for `Entry` widgets, using `widget.after_idle(widget.selection_range, 0, tk.END)` prevents the selection from being immediately cleared by the mouse-up event if focus was triggered by a click.
**Action:** Always wrap `selection_range` in `after_idle` for focus-based text selection.
## 2025-05-17 - [Operational Visibility via Window Titles and ETA Refinements]
**Learning:** For long-running background tasks, the window title is a high-value real estate for operational feedback, allowing users to monitor progress without switching windows. Additionally, providing an absolute finish time alongside a relative duration makes the ETA more actionable and easier for users to process.
**Action:** Always include the current state and progress percentage in the root window title and supplement relative durations with calculated 'Finish at HH:MM' timestamps using `timedelta`.
## 2025-05-17 - [Tkinter Entry Auto-Selection on Focus]
**Learning:** When implementing auto-selection on `<FocusIn>` for Tkinter `Entry` widgets, using `widget.after_idle(widget.selection_range, 0, tk.END)` prevents the selection from being immediately cleared by the mouse-up event if focus was triggered by a click.
**Action:** Bind the `<FocusIn>` event on path `Entry` widgets to a handler that uses `after_idle` to select all text, allowing users to quickly replace or edit folder paths.
## 2026-03-24 - [Enhanced Progress Visibility and Path Entry UX]
**Learning:** For long-running operations like file copying, mirroring the progress percentage and current engine state in the application's window title provides a "passive" status check for users when the app is in the background. Additionally, using `after_idle` with `selection_range` on `Entry` widgets ensures that the text selection is not immediately cleared by a mouse-up event when the user clicks to focus the field.
**Action:** Always include taskbar/title-bar progress updates for background-capable tasks, and use `after_idle` for robust auto-selection on focus in Tkinter applications.
## 2025-05-17 - [Real-time Operational Feedback in Window Title]
**Learning:** For long-running operations like file copies, displaying the overall progress percentage and engine state in the window title enables users to monitor progress from their taskbar or window switcher without switching focus. Additionally, translating relative ETA durations into absolute "Finish at HH:MM" timestamps provides more intuitive time management for the user.
**Action:** In future GUI applications with progress tracking, prioritize updating the main window title with key status metrics and provide both relative and absolute completion estimates.
## 2025-05-17 - [Operational Feedback via Window Title and Absolute ETA]
**Learning:** For long-running batch operations like file copies, providing progress feedback in the window title is a significant UX win, allowing users to monitor status from the taskbar when minimized. Additionally, supplementing a countdown ETA with a calculated "Finish at HH:MM" timestamp provides a more intuitive sense of when the task will be done.
**Action:** Update the window title with progress percentage and state during operations. Use `datetime.now() + timedelta(seconds=eta)` to display an absolute completion time.
## 2025-05-17 - [Enhancing Path Input Usability and Status Visibility]
**Learning:** For desktop applications where users frequently copy or replace file paths, binding `<FocusIn>` to `selection_range(0, tk.END)` provides a significant usability boost. Additionally, mirroring progress and state in the window title enables "passive monitoring" from the taskbar/dock, reducing the need for active window focus.
**Action:** In Tkinter apps, always auto-select path entry text on focus and reflect high-level operation status in the root window title.
## 2025-05-17 - [Enhanced Operational Feedback and Path Entry UX]
**Learning:** For long-running operations like file copying, providing feedback in the window title allows users to monitor progress while the window is in the background. Additionally, adding a "Finish at" time to the ETA provides more concrete context than just a duration. For path inputs, auto-selecting text on focus significantly speeds up common user actions like replacing or editing paths.
**Action:** Implement window title progress updates and "Finish at" timestamps for ETA. Use <FocusIn> bindings for path entries to improve keyboard efficiency.
## 2025-05-17 - [Enhanced Feedback for Long-Running Background Operations]
**Learning:** For desktop applications performing long-running tasks like file copying, providing the absolute completion time (e.g., "Finish at 14:30") is more intuitive for users than a relative countdown (e.g., "30m 5s"). Additionally, reflecting the current state and progress in the window title allows users to monitor the process without switching focus to the application.
**Action:** Always include an absolute timestamp in ETA calculations when possible and update the window title dynamically during active operations.
## 2025-05-17 - [Operational Feedback with Absolute Timestamps]
**Learning:** Providing an ETA in terms of duration (e.g., "3m 32s") is helpful, but augmenting it with an absolute finish time (e.g., "Finish at 14:45") provides better situational awareness for the user. Using `timedelta` makes this easy to implement in Python.
**Action:** Always consider adding absolute finish times to progress displays where an ETA is available.

## 2026-03-31 - [Tkinter Clipboard Integration and Log Management]
**Learning:** Implementing a "Copy Log" feature in Tkinter requires using `root.clipboard_clear()` before `root.clipboard_append()` to ensure the system clipboard is correctly updated with the current widget content. Additionally, grouping log controls (like a Copy button) in a sub-frame within the Log area improves discoverability and keeps the interface organized.
**Action:** Use a dedicated header frame for log-related actions and ensure a clean clipboard state before appending new data.
