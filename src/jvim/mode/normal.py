"""Normal mode mixin for JsonEditor."""

from __future__ import annotations


class NormalMixin:
    """Normal mode key handler and pending multi-char handler for JsonEditor."""

    def _enter_insert(self) -> None:
        if self.read_only:
            self.status_msg = "[readonly]"
            return
        EditorMode = self._mode.__class__
        self._mode = EditorMode.INSERT
        self.status_msg = "-- INSERT --"

    def _handle_normal(self, event) -> None:
        EditorMode = self._mode.__class__
        key = event.key
        char = event.character or ""

        # Escape: visual mode 해제 (pending보다 우선)
        if key == "escape" and self._visual_mode:
            self._visual_mode = ""
            self.status_msg = ""
            return

        if self.pending:
            self._handle_pending(char, key)
            return

        # Visual mode 진입/전환/해제
        if char == "v":
            if self._visual_mode == "v":
                self._visual_mode = ""
                self.status_msg = ""
            else:
                self._visual_mode = "v"
                self._visual_anchor_row = self.cursor_row
                self._visual_anchor_col = self.cursor_col
                self.status_msg = "-- VISUAL --"
            return
        if char == "V":
            if self._visual_mode == "V":
                self._visual_mode = ""
                self.status_msg = ""
            else:
                self._visual_mode = "V"
                self._visual_anchor_row = self.cursor_row
                self._visual_anchor_col = self.cursor_col
                self.status_msg = "-- VISUAL LINE --"
            return

        # movement
        if char == "h" or key == "left":
            self.cursor_col -= 1
        elif char == "j" or key == "down":
            self.cursor_row = (
                self._next_visible_line(self.cursor_row, 1)
                if self._folds
                else self.cursor_row + 1
            )
        elif char == "k" or key == "up":
            self.cursor_row = (
                self._next_visible_line(self.cursor_row, -1)
                if self._folds
                else self.cursor_row - 1
            )
        elif char == "l" or key == "right":
            self.cursor_col += 1
        elif char == "w":
            self._move_word_forward()
        elif char == "b":
            self._move_word_backward()
        elif char == "0":
            self.cursor_col = 0
        elif char == "$" or key == "end":
            self.cursor_col = max(0, len(self.lines[self.cursor_row]) - 1)
        elif char == "^" or key == "home":
            line = self.lines[self.cursor_row]
            self.cursor_col = len(line) - len(line.lstrip())
        elif char == "G":
            self.cursor_row = len(self.lines) - 1
            self._scroll_cursor_to_top()
        elif char == "%":
            self._jump_matching_bracket()
        elif key == "pagedown" or key == "ctrl+f":
            if self._folds:
                self.cursor_row = self._skip_visible_lines(
                    self.cursor_row, self._visible_height(), 1
                )
            else:
                self.cursor_row += self._visible_height()
        elif key == "pageup" or key == "ctrl+b":
            if self._folds:
                self.cursor_row = self._skip_visible_lines(
                    self.cursor_row, self._visible_height(), -1
                )
            else:
                self.cursor_row -= self._visible_height()
        elif key == "ctrl+d":
            if self._folds:
                self.cursor_row = self._skip_visible_lines(
                    self.cursor_row, self._visible_height() // 2, 1
                )
            else:
                self.cursor_row += self._visible_height() // 2
        elif key == "ctrl+u":
            if self._folds:
                self.cursor_row = self._skip_visible_lines(
                    self.cursor_row, self._visible_height() // 2, -1
                )
            else:
                self.cursor_row -= self._visible_height() // 2
        elif key == "ctrl+e":
            nxt = (
                self._next_visible_line(self._scroll_top, 1)
                if self._folds
                else self._scroll_top + 1
            )
            self._scroll_top = min(nxt, len(self.lines) - 1)
        elif key == "ctrl+y":
            prev = (
                self._next_visible_line(self._scroll_top, -1)
                if self._folds
                else self._scroll_top - 1
            )
            self._scroll_top = max(prev, 0)
        elif key == "ctrl+g":
            total = len(self.lines)
            pct = (self.cursor_row + 1) * 100 // total if total else 0
            self.status_msg = (
                f'"{self._mode.name}" line {self.cursor_row + 1} of {total} --{pct}%--'
            )

        # enter insert mode
        elif char == "i":
            if not self.read_only:
                self._dot_start(event)
            self._enter_insert()
        elif char == "I":
            if not self.read_only:
                self._dot_start(event)
            line = self.lines[self.cursor_row]
            self.cursor_col = len(line) - len(line.lstrip())
            self._enter_insert()
        elif char == "a":
            if not self.read_only:
                self._dot_start(event)
            self.cursor_col += 1
            self._enter_insert()
        elif char == "A":
            if not self.read_only:
                self._dot_start(event)
            self.cursor_col = len(self.lines[self.cursor_row])
            self._enter_insert()
        elif char == "o":
            if self.read_only:
                self.status_msg = "[readonly]"
            else:
                self._dot_start(event)
                self._save_undo()
                indent = self._current_indent()
                before = self.lines[self.cursor_row].rstrip()
                extra = "    " if before.endswith(("{", "[")) else ""
                self.cursor_row += 1
                self.lines.insert(self.cursor_row, " " * indent + extra)
                self._adjust_line_indices(self.cursor_row, 1)
                self.cursor_col = indent + len(extra)
                self._enter_insert()
        elif char == "O":
            if self.read_only:
                self.status_msg = "[readonly]"
            else:
                self._dot_start(event)
                self._save_undo()
                indent = self._current_indent()
                self.lines.insert(self.cursor_row, " " * indent)
                self._adjust_line_indices(self.cursor_row, 1)
                self.cursor_col = indent
                self._enter_insert()

        # single-key edits
        elif char == "x":
            if self.read_only:
                self.status_msg = "[readonly]"
            else:
                self._dot_start(event)
                self._dot_stop()
                self._save_undo()
                line = self.lines[self.cursor_row]
                if line and self.cursor_col < len(line):
                    self.lines[self.cursor_row] = (
                        line[: self.cursor_col] + line[self.cursor_col + 1 :]
                    )
        elif char == "p":
            if self.read_only:
                self.status_msg = "[readonly]"
            else:
                self._dot_start(event)
                self._dot_stop()
                self._paste_after()
        elif char == "P":
            if self.read_only:
                self.status_msg = "[readonly]"
            else:
                self._dot_start(event)
                self._dot_stop()
                self._paste_before()
        elif char == "u":
            if self.read_only:
                self.status_msg = "[readonly]"
            else:
                self._undo()
        elif key == "ctrl+r":
            if self.read_only:
                self.status_msg = "[readonly]"
            else:
                self._redo()
        elif char == "J":
            if self.read_only:
                self.status_msg = "[readonly]"
            else:
                self._dot_start(event)
                self._dot_stop()
                self._join_lines()

        # dot repeat
        elif char == ".":
            if not self.read_only:
                self._dot_replay()

        # multi-key starters
        elif char in ("d", "c", "y", "r", "g", "e", "z"):
            # Visual mode 연산자 인터셉트
            if self._visual_mode and char in ("d", "y", "c"):
                self._execute_visual_operator(char)
                return
            if self.read_only and char not in ("y", "g", "e", "z"):
                self.status_msg = "[readonly]"
            else:
                if char not in ("y", "g", "e", "z"):
                    self._dot_start(event)
                self.pending = char

        # search mode
        elif char == "/":
            self._visual_mode = ""
            self._mode = EditorMode.SEARCH
            self._search_buffer = ""
            self._search_forward = True
            self.status_msg = ""
        elif char == "?":
            self._visual_mode = ""
            self._mode = EditorMode.SEARCH
            self._search_buffer = ""
            self._search_forward = False
            self.status_msg = ""
        elif char == "n":
            self._goto_next_match()
        elif char == "N":
            self._goto_prev_match()

        # command mode
        elif char == ":":
            self._visual_mode = ""
            self._mode = EditorMode.COMMAND
            self.command_buffer = ""
            self.status_msg = ""

    # -- Pending multi-char ------------------------------------------------

    def _handle_pending(self, char: str, key: str) -> None:
        if key == "escape" or not char:
            self.pending = ""
            self.status_msg = ""
            self._dot_stop()
            return

        combo = self.pending + char
        self.pending = ""

        if self.read_only and combo not in ("yy", "gg", "ej"):
            self.status_msg = "[readonly]"
            return

        if combo == "dd":
            self._save_undo()
            self._yank_type = "line"
            self.yank_buffer = [self.lines[self.cursor_row]]
            if len(self.lines) > 1:
                deleted_at = self.cursor_row
                self.lines.pop(self.cursor_row)
                self._adjust_line_indices(deleted_at, -1)
                if self.cursor_row >= len(self.lines):
                    self.cursor_row = len(self.lines) - 1
            else:
                self.lines[0] = ""
            self.cursor_col = 0
            self.status_msg = "line deleted"
            self._dot_stop()

        elif combo == "dw":
            self._save_undo()
            self._delete_word()
            self._dot_stop()

        elif combo == "d$":
            self._save_undo()
            line = self.lines[self.cursor_row]
            self.lines[self.cursor_row] = line[: self.cursor_col]
            self._dot_stop()

        elif combo == "d0":
            self._save_undo()
            line = self.lines[self.cursor_row]
            self.lines[self.cursor_row] = line[self.cursor_col :]
            self.cursor_col = 0
            self._dot_stop()

        elif combo == "cw":
            self._save_undo()
            self._delete_word()
            self._enter_insert()
            # recording continues into insert mode

        elif combo == "cc":
            self._save_undo()
            self._yank_type = "line"
            indent = self._current_indent()
            self.yank_buffer = [self.lines[self.cursor_row]]
            self.lines[self.cursor_row] = " " * indent
            self.cursor_col = indent
            self._enter_insert()
            # recording continues into insert mode

        elif combo == "yy":
            self._yank_type = "line"
            self.yank_buffer = [self.lines[self.cursor_row]]
            self.status_msg = "line yanked"

        elif combo == "gg":
            self.cursor_row = 0
            self.cursor_col = 0
            self._scroll_cursor_to_top()

        elif len(combo) == 2 and combo[0] == "r":
            self._save_undo()
            line = self.lines[self.cursor_row]
            if self.cursor_col < len(line):
                self.lines[self.cursor_row] = (
                    line[: self.cursor_col] + combo[1] + line[self.cursor_col + 1 :]
                )
            self._dot_stop()

        elif combo == "ej":
            self._edit_embedded_json()

        # fold 명령어
        elif combo == "za":
            self._toggle_fold(self.cursor_row)
        elif combo == "zo":
            self._open_fold(self.cursor_row)
        elif combo == "zc":
            self._close_fold(self.cursor_row)
        elif combo == "zM":
            self._fold_all()
        elif combo == "zR":
            self._unfold_all()

        else:
            self._dot_stop()
            self.status_msg = f"unknown: {combo}"
