"""Navigation mixin for JsonEditor."""

from __future__ import annotations


class NavigationMixin:
    """Word and bracket movement methods for JsonEditor."""

    _BRACKET_PAIRS = {"{": "}", "[": "]", "(": ")"}
    _BRACKET_PAIRS_REV = {"}": "{", "]": "[", ")": "("}

    def _move_word_forward(self) -> None:
        line = self.lines[self.cursor_row]
        col = self.cursor_col
        while col < len(line) and (line[col].isalnum() or line[col] == "_"):
            col += 1
        while col < len(line) and not (line[col].isalnum() or line[col] == "_"):
            col += 1
        if col >= len(line) and self.cursor_row < len(self.lines) - 1:
            self.cursor_row += 1
            nline = self.lines[self.cursor_row]
            self.cursor_col = len(nline) - len(nline.lstrip())
        else:
            self.cursor_col = min(col, max(0, len(line) - 1))

    def _move_word_backward(self) -> None:
        line = self.lines[self.cursor_row]
        col = self.cursor_col
        if col == 0:
            if self.cursor_row > 0:
                self.cursor_row -= 1
                self.cursor_col = max(0, len(self.lines[self.cursor_row]) - 1)
            return
        col -= 1
        while col > 0 and not (line[col].isalnum() or line[col] == "_"):
            col -= 1
        while col > 0 and (line[col - 1].isalnum() or line[col - 1] == "_"):
            col -= 1
        self.cursor_col = col

    def _jump_matching_bracket(self) -> None:
        line = self.lines[self.cursor_row]
        if self.cursor_col >= len(line):
            return
        ch = line[self.cursor_col]
        if ch in self._BRACKET_PAIRS:
            self._search_bracket_forward(ch, self._BRACKET_PAIRS[ch])
        elif ch in self._BRACKET_PAIRS_REV:
            self._search_bracket_backward(ch, self._BRACKET_PAIRS_REV[ch])

    def _search_bracket_forward(self, open_ch: str, close_ch: str) -> None:
        depth = 1
        row, col = self.cursor_row, self.cursor_col + 1
        while row < len(self.lines):
            line = self.lines[row]
            while col < len(line):
                if line[col] == open_ch:
                    depth += 1
                elif line[col] == close_ch:
                    depth -= 1
                    if depth == 0:
                        self.cursor_row, self.cursor_col = row, col
                        return
                col += 1
            row += 1
            col = 0

    def _search_bracket_backward(self, close_ch: str, open_ch: str) -> None:
        depth = 1
        row, col = self.cursor_row, self.cursor_col - 1
        while row >= 0:
            line = self.lines[row]
            while col >= 0:
                if line[col] == close_ch:
                    depth += 1
                elif line[col] == open_ch:
                    depth -= 1
                    if depth == 0:
                        self.cursor_row, self.cursor_col = row, col
                        return
                col -= 1
            row -= 1
            if row >= 0:
                col = len(self.lines[row]) - 1
