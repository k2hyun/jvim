"""Clipboard mixin for JsonEditor."""

from __future__ import annotations


class ClipboardMixin:
    """Clipboard (yank/paste/delete-word/join) methods for JsonEditor."""

    def _delete_word(self) -> None:
        line = self.lines[self.cursor_row]
        col = self.cursor_col
        start = col
        while col < len(line) and (line[col].isalnum() or line[col] == "_"):
            col += 1
        while col < len(line) and line[col] == " ":
            col += 1
        if col == start and col < len(line):
            col += 1
        self.lines[self.cursor_row] = line[:start] + line[col:]

    def _paste_after(self) -> None:
        if not self.yank_buffer:
            return
        self._save_undo()
        if self._yank_type == "char":
            text = self.yank_buffer[0]
            line = self.lines[self.cursor_row]
            insert_col = min(self.cursor_col + 1, len(line))
            if "\n" not in text:
                self.lines[self.cursor_row] = (
                    line[:insert_col] + text + line[insert_col:]
                )
                self.cursor_col = insert_col + len(text) - 1
            else:
                parts = text.split("\n")
                after = line[insert_col:]
                self.lines[self.cursor_row] = line[:insert_col] + parts[0]
                for i, p in enumerate(parts[1:], 1):
                    if i == len(parts) - 1:
                        self.lines.insert(self.cursor_row + i, p + after)
                    else:
                        self.lines.insert(self.cursor_row + i, p)
                inserted = len(parts) - 1
                self._adjust_line_indices(self.cursor_row + 1, inserted)
                self.cursor_row += inserted
                self.cursor_col = len(parts[-1]) - 1
            return
        # fold-aware: fold 헤더이면 fold end 뒤에 삽입
        insert_after = self.cursor_row
        fold_end = self._get_fold_end(self.cursor_row)
        if fold_end is not None:
            insert_after = fold_end
        inserted = len(self.yank_buffer)
        for i, line in enumerate(self.yank_buffer):
            self.lines.insert(insert_after + 1 + i, line)
        self._adjust_line_indices(insert_after + 1, inserted)
        self.cursor_row = insert_after + 1
        self.cursor_col = 0

    def _paste_before(self) -> None:
        if not self.yank_buffer:
            return
        self._save_undo()
        if self._yank_type == "char":
            text = self.yank_buffer[0]
            line = self.lines[self.cursor_row]
            insert_col = self.cursor_col
            if "\n" not in text:
                self.lines[self.cursor_row] = (
                    line[:insert_col] + text + line[insert_col:]
                )
                self.cursor_col = insert_col + len(text) - 1
            else:
                parts = text.split("\n")
                after = line[insert_col:]
                self.lines[self.cursor_row] = line[:insert_col] + parts[0]
                for i, p in enumerate(parts[1:], 1):
                    if i == len(parts) - 1:
                        self.lines.insert(self.cursor_row + i, p + after)
                    else:
                        self.lines.insert(self.cursor_row + i, p)
                inserted = len(parts) - 1
                self._adjust_line_indices(self.cursor_row + 1, inserted)
                self.cursor_row += inserted
                self.cursor_col = len(parts[-1]) - 1
            return
        inserted = len(self.yank_buffer)
        for i, line in enumerate(self.yank_buffer):
            self.lines.insert(self.cursor_row + i, line)
        self._adjust_line_indices(self.cursor_row, inserted)
        self.cursor_col = 0

    def _join_lines(self) -> None:
        if self.cursor_row >= len(self.lines) - 1:
            return
        # fold-aware: fold 헤더이면 fold 전체를 제거한 뒤 다음 보이는 줄과 join
        fold_end = self._get_fold_end(self.cursor_row)
        if fold_end is not None:
            next_row = fold_end + 1
            if next_row >= len(self.lines):
                return
            self._save_undo()
            cur = self.lines[self.cursor_row].rstrip()
            # fold 내부 행 제거 (cursor_row+1 ~ fold_end)
            del self.lines[self.cursor_row + 1 : next_row]
            removed = fold_end - self.cursor_row
            self._open_fold(self.cursor_row)
            self._adjust_line_indices(self.cursor_row + 1, -removed)
            # 이제 cursor_row+1 = 원래 next_row (fold 뒤 첫 줄)
            nxt = self.lines[self.cursor_row + 1].lstrip()
            self.cursor_col = len(cur)
            self.lines[self.cursor_row] = cur + " " + nxt
            deleted_at = self.cursor_row + 1
            self.lines.pop(deleted_at)
            self._adjust_line_indices(deleted_at, -1)
        else:
            self._save_undo()
            cur = self.lines[self.cursor_row].rstrip()
            nxt = self.lines[self.cursor_row + 1].lstrip()
            self.cursor_col = len(cur)
            self.lines[self.cursor_row] = cur + " " + nxt
            deleted_at = self.cursor_row + 1
            self.lines.pop(deleted_at)
            self._adjust_line_indices(deleted_at, -1)
