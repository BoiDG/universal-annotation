# Prompt Scratchpad Bridge

This local Cursor/VS Code extension sends only the active file path and selected one-based line range to Prompt Scratchpad over the current user's named pipe. It never sends selected code. Press **Ctrl+Alt+A** in the editor after selecting lines; the desktop app inserts a compact reference at the scratchpad caret.

The bridge activates after editor startup. It needs the desktop app running, a saved local file, and an active text editor. It does not capture selections in integrated terminals or unsaved editors. The command **Prompt Scratchpad: Refresh Editor Location** sends the current location again if needed.
