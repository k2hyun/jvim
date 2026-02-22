"""Insert mode mixin for JsonEditor."""

from __future__ import annotations


class InsertMixin:
    """Insert mode key handler for JsonEditor."""

    def _handle_insert(self, event) -> None:
        key = event.key
        char = event.character

        if key == "escape":
            EditorMode = self._mode.__class__
            self._dot_stop()
            self._mode = EditorMode.NORMAL
            self.cursor_col = max(0, self.cursor_col - 1)
            self.status_msg = ""
            return

        if key == "backspace":
            self._save_undo()
            if self.cursor_col > 0:
                line = self.lines[self.cursor_row]
                self.lines[self.cursor_row] = (
                    line[: self.cursor_col - 1] + line[self.cursor_col :]
                )
                self.cursor_col -= 1
            elif self.cursor_row > 0:
                prev = self.lines[self.cursor_row - 1]
                self.cursor_col = len(prev)
                self.lines[self.cursor_row - 1] = prev + self.lines[self.cursor_row]
                deleted_at = self.cursor_row
                self.lines.pop(self.cursor_row)
                self._adjust_line_indices(deleted_at, -1)
                self.cursor_row -= 1
            return

        if key == "enter":
            self._save_undo()
            line = self.lines[self.cursor_row]
            indent = len(line) - len(line.lstrip()) if line.strip() else 0
            before = line[: self.cursor_col].rstrip()
            after = line[self.cursor_col :].lstrip()

            if before.endswith(("{", "[")) and after and after[0] in ("}", "]"):
                closing_indent = " " * indent
                new_indent = " " * indent + "    "
                self.lines[self.cursor_row] = line[: self.cursor_col]
                self.lines.insert(self.cursor_row + 1, new_indent)
                self.lines.insert(
                    self.cursor_row + 2,
                    closing_indent + line[self.cursor_col :].lstrip(),
                )
                self._adjust_line_indices(self.cursor_row + 1, 2)
                self.cursor_row += 1
                self.cursor_col = len(new_indent)
                return

            extra = "    " if before.endswith(("{", "[")) else ""
            self.lines[self.cursor_row] = line[: self.cursor_col]
            new_line = " " * indent + extra + line[self.cursor_col :]
            self.cursor_row += 1
            self.lines.insert(self.cursor_row, new_line)
            self._adjust_line_indices(self.cursor_row, 1)
            self.cursor_col = indent + len(extra)
            return

        if key == "tab":
            self._save_undo()
            line = self.lines[self.cursor_row]
            self.lines[self.cursor_row] = (
                line[: self.cursor_col] + "    " + line[self.cursor_col :]
            )
            self.cursor_col += 4
            return

        if key == "end":
            self.cursor_col = len(self.lines[self.cursor_row])
            return
        if key == "home":
            line = self.lines[self.cursor_row]
            self.cursor_col = len(line) - len(line.lstrip())
            return

        if key in ("left", "right", "up", "down"):
            delta = {"left": (0, -1), "right": (0, 1), "up": (-1, 0), "down": (1, 0)}
            dr, dc = delta[key]
            self.cursor_row += dr
            self.cursor_col += dc
            return

        # auto-dedent for closing brackets
        if char in ("}", "]"):
            self._save_undo()
            line = self.lines[self.cursor_row]
            before = line[: self.cursor_col]
            if before.strip() == "":
                new_indent = max(0, len(before) - 4)
                self.lines[self.cursor_row] = (
                    " " * new_indent + char + line[self.cursor_col :]
                )
                self.cursor_col = new_indent + 1
                return

        if char and char.isprintable():
            self._save_undo()
            line = self.lines[self.cursor_row]
            self.lines[self.cursor_row] = (
                line[: self.cursor_col] + char + line[self.cursor_col :]
            )
            self.cursor_col += 1
