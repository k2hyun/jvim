"""Command mode mixin for JsonEditor."""

from __future__ import annotations

import re


class CommandMixin:
    """Command mode key handler and command execution for JsonEditor."""

    def _handle_command(self, event) -> None:
        EditorMode = self._mode.__class__
        key = event.key
        char = event.character

        if key == "escape":
            self._mode = EditorMode.NORMAL
            self.command_buffer = ""
            self._command_history_idx = -1
            self.status_msg = ""
            return

        if key == "enter":
            cmd = self.command_buffer.strip()
            if cmd:
                self._add_to_command_history(cmd)
            self._exec_command(cmd)
            if self._mode == EditorMode.COMMAND:
                self._mode = EditorMode.NORMAL
            self.command_buffer = ""
            self._command_history_idx = -1
            return

        if key == "backspace":
            if self.command_buffer:
                self.command_buffer = self.command_buffer[:-1]
                self._command_history_idx = -1
            else:
                self._mode = EditorMode.NORMAL
                self._command_history_idx = -1
            return

        # History navigation
        if key == "up":
            self._command_history_prev()
            return
        if key == "down":
            self._command_history_next()
            return

        if char and char.isprintable():
            self.command_buffer += char
            self._command_history_idx = -1

    def _add_to_command_history(self, cmd: str) -> None:
        """Add command to history, avoiding duplicates."""
        if not cmd:
            return
        if cmd in self._command_history:
            self._command_history.remove(cmd)
        self._command_history.insert(0, cmd)
        if len(self._command_history) > self._command_history_max:
            self._command_history.pop()

    def _command_history_prev(self) -> None:
        """Navigate to previous command in history."""
        if not self._command_history:
            return
        if self._command_history_idx < len(self._command_history) - 1:
            self._command_history_idx += 1
            self.command_buffer = self._command_history[self._command_history_idx]

    def _command_history_next(self) -> None:
        """Navigate to next command in history."""
        if self._command_history_idx > 0:
            self._command_history_idx -= 1
            self.command_buffer = self._command_history[self._command_history_idx]
        elif self._command_history_idx == 0:
            self._command_history_idx = -1
            self.command_buffer = ""

    def _exec_command(self, cmd: str) -> None:
        stripped = cmd.strip()

        # :$ → jump to last line
        if stripped == "$":
            self.cursor_row = len(self.lines) - 1
            self.cursor_col = 0
            self._scroll_cursor_to_top()
            return

        # Line jump: :l<num> → editor line; :<num> or :p<num> → file line (JSONL record)
        if len(stripped) > 1 and stripped[0] == "l" and stripped[1:].isdigit():
            num = int(stripped[1:])
            self.cursor_row = max(0, min(num - 1, len(self.lines) - 1))
            self.cursor_col = 0
            self._scroll_cursor_to_top()
            return
        if stripped.isdigit() or (
            len(stripped) > 1 and stripped[0] == "p" and stripped[1:].isdigit()
        ):
            num = int(stripped if stripped.isdigit() else stripped[1:])
            if self.jsonl:
                records = self._jsonl_line_records()
                for i, rec in enumerate(records):
                    if rec == num:
                        self.cursor_row = i
                        self.cursor_col = 0
                        self._scroll_cursor_to_top()
                        return
                self.status_msg = f"record {num} not found"
                return
            self.cursor_row = max(0, min(num - 1, len(self.lines) - 1))
            self.cursor_col = 0
            self._scroll_cursor_to_top()
            return

        # 치환 명령: :s/old/new/g, :%s/old/new/g, :N,Ms/old/new/g
        sub_match = re.match(r"^(%|(\d+),(\d+))?s(.)(.*)$", stripped)
        if sub_match:
            self._execute_substitute(stripped)
            return

        parts = cmd.split(None, 1)
        verb = parts[0] if parts else ""
        arg = parts[1] if len(parts) > 1 else ""

        force = verb.endswith("!")
        if force:
            verb = verb[:-1]

        if verb == "w":
            if self.read_only:
                self.status_msg = "[readonly]"
                return
            content = self.get_content()
            if not force:
                valid, err = self._check_content(content)
                if not valid:
                    self.status_msg = err
                    return
            save = self._pretty_to_jsonl(content) if self.jsonl else content
            self.post_message(self.FileSaveRequested(content=save, file_path=arg))
        elif verb == "q":
            if force:
                self.post_message(self.ForceQuit())
            else:
                self.post_message(self.Quit())
        elif verb in ("wq", "x"):
            if self.read_only:
                # read-only: just quit without saving
                self.post_message(self.Quit())
                return
            content = self.get_content()
            if not force:
                valid, err = self._check_content(content)
                if not valid:
                    self.status_msg = err
                    return
            save = self._pretty_to_jsonl(content) if self.jsonl else content
            self.post_message(
                self.FileSaveRequested(content=save, file_path=arg, quit_after=True)
            )
        elif verb == "e":
            if not arg:
                self.status_msg = "Usage: :e <file>"
            else:
                self.post_message(self.FileOpenRequested(file_path=arg))
        elif verb in ("fmt", "format"):
            if self.read_only:
                self.status_msg = "[readonly]"
            else:
                self._format_json()
        elif verb == "help":
            self.post_message(self.HelpToggleRequested())
        else:
            self.status_msg = f"unknown command: :{cmd}"
