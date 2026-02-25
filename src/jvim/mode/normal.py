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

    def _consume_count(self) -> int:
        """_count_buf를 소비하고 정수 반환. 비어 있으면 1."""
        if self._count_buf:
            n = int(self._count_buf)
            self._count_buf = ""
            return max(1, n)
        return 1

    def _handle_normal(self, event) -> None:
        EditorMode = self._mode.__class__
        key = event.key
        char = event.character or ""

        # Escape: visual mode 해제 (pending보다 우선) + count 리셋
        if key == "escape":
            self._count_buf = ""
            if self._visual_mode:
                self._visual_mode = ""
                self.status_msg = ""
            return

        if self.pending:
            self._handle_pending(char, key)
            return

        # 숫자 접두사 수집: 1-9로 시작, 이후 0-9 누적
        if char and char.isdigit():
            if char != "0" or self._count_buf:
                self._count_buf += char
                return

        # Visual mode 진입/전환/해제
        if char == "v":
            self._count_buf = ""
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
            self._count_buf = ""
            if self._visual_mode == "V":
                self._visual_mode = ""
                self.status_msg = ""
            else:
                self._visual_mode = "V"
                self._visual_anchor_row = self.cursor_row
                self._visual_anchor_col = self.cursor_col
                self.status_msg = "-- VISUAL LINE --"
            return

        # movement (count 적용)
        if char == "h" or key == "left":
            count = self._consume_count()
            self.cursor_col -= count
        elif char == "j" or key == "down":
            count = self._consume_count()
            for _ in range(count):
                self.cursor_row = (
                    self._next_visible_line(self.cursor_row, 1)
                    if self._folds
                    else self.cursor_row + 1
                )
        elif char == "k" or key == "up":
            count = self._consume_count()
            for _ in range(count):
                self.cursor_row = (
                    self._next_visible_line(self.cursor_row, -1)
                    if self._folds
                    else self.cursor_row - 1
                )
        elif char == "l" or key == "right":
            count = self._consume_count()
            self.cursor_col += count
        elif char == "w":
            count = self._consume_count()
            for _ in range(count):
                self._move_word_forward()
        elif char == "b":
            count = self._consume_count()
            for _ in range(count):
                self._move_word_backward()
        elif char == "0":
            # bare 0 (count_buf가 비었을 때만 여기 도달)
            self.cursor_col = 0
        elif char == "$" or key == "end":
            self._count_buf = ""
            self.cursor_col = max(0, len(self.lines[self.cursor_row]) - 1)
        elif char == "^" or key == "home":
            self._count_buf = ""
            line = self.lines[self.cursor_row]
            self.cursor_col = len(line) - len(line.lstrip())
        elif char == "G":
            if self._count_buf:
                # count가 있으면 해당 줄로 이동 (1-indexed)
                count = self._consume_count()
                self.cursor_row = count - 1
            else:
                # count 없으면 마지막 줄
                self.cursor_row = len(self.lines) - 1
            self._scroll_cursor_to_top()
        elif char == "%":
            self._count_buf = ""
            self._jump_matching_bracket()
        elif key == "pagedown" or key == "ctrl+f":
            self._count_buf = ""
            if self._folds:
                self.cursor_row = self._skip_visible_lines(
                    self.cursor_row, self._visible_height(), 1
                )
            else:
                self.cursor_row += self._visible_height()
        elif key == "pageup" or key == "ctrl+b":
            self._count_buf = ""
            if self._folds:
                self.cursor_row = self._skip_visible_lines(
                    self.cursor_row, self._visible_height(), -1
                )
            else:
                self.cursor_row -= self._visible_height()
        elif key == "ctrl+d":
            self._count_buf = ""
            if self._folds:
                self.cursor_row = self._skip_visible_lines(
                    self.cursor_row, self._visible_height() // 2, 1
                )
            else:
                self.cursor_row += self._visible_height() // 2
        elif key == "ctrl+u":
            self._count_buf = ""
            if self._folds:
                self.cursor_row = self._skip_visible_lines(
                    self.cursor_row, self._visible_height() // 2, -1
                )
            else:
                self.cursor_row -= self._visible_height() // 2
        elif key == "ctrl+e":
            self._count_buf = ""
            nxt = (
                self._next_visible_line(self._scroll_top, 1)
                if self._folds
                else self._scroll_top + 1
            )
            self._scroll_top = min(nxt, len(self.lines) - 1)
        elif key == "ctrl+y":
            self._count_buf = ""
            prev = (
                self._next_visible_line(self._scroll_top, -1)
                if self._folds
                else self._scroll_top - 1
            )
            self._scroll_top = max(prev, 0)
        elif key == "ctrl+g":
            self._count_buf = ""
            total = len(self.lines)
            pct = (self.cursor_row + 1) * 100 // total if total else 0
            self.status_msg = (
                f'"{self._mode.name}" line {self.cursor_row + 1} of {total} --{pct}%--'
            )

        # enter insert mode
        elif char == "i":
            self._count_buf = ""
            if not self.read_only:
                self._dot_start(event)
            self._enter_insert()
        elif char == "I":
            self._count_buf = ""
            if not self.read_only:
                self._dot_start(event)
            line = self.lines[self.cursor_row]
            self.cursor_col = len(line) - len(line.lstrip())
            self._enter_insert()
        elif char == "a":
            self._count_buf = ""
            if not self.read_only:
                self._dot_start(event)
            self.cursor_col += 1
            self._enter_insert()
        elif char == "A":
            self._count_buf = ""
            if not self.read_only:
                self._dot_start(event)
            self.cursor_col = len(self.lines[self.cursor_row])
            self._enter_insert()
        elif char == "o":
            self._count_buf = ""
            if self.read_only:
                self.status_msg = "[readonly]"
            else:
                self._dot_start(event)
                self._save_undo()
                # fold-aware: fold 헤더이면 fold end 뒤에 삽입
                fold_end = self._folds.get(self.cursor_row)
                if fold_end is not None:
                    insert_row = fold_end + 1
                    # fold end 행의 들여쓰기 기준
                    end_line = self.lines[fold_end]
                    indent = (
                        len(end_line) - len(end_line.lstrip())
                        if end_line.strip()
                        else 0
                    )
                    extra = ""
                else:
                    insert_row = self.cursor_row + 1
                    indent = self._current_indent()
                    before = self.lines[self.cursor_row].rstrip()
                    extra = "    " if before.endswith(("{", "[")) else ""
                self.cursor_row = insert_row
                self.lines.insert(self.cursor_row, " " * indent + extra)
                self._adjust_line_indices(self.cursor_row, 1)
                self.cursor_col = indent + len(extra)
                self._enter_insert()
        elif char == "O":
            self._count_buf = ""
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

        # single-key edits (count 적용)
        elif char == "x":
            if self.read_only:
                self._count_buf = ""
                self.status_msg = "[readonly]"
            else:
                count = self._consume_count()
                self._dot_start(event)
                self._dot_stop()
                self._save_undo()
                line = self.lines[self.cursor_row]
                if line and self.cursor_col < len(line):
                    end = min(self.cursor_col + count, len(line))
                    self.lines[self.cursor_row] = line[: self.cursor_col] + line[end:]
        elif char == "p":
            if self.read_only:
                self._count_buf = ""
                self.status_msg = "[readonly]"
            else:
                count = self._consume_count()
                self._dot_start(event)
                self._dot_stop()
                for _ in range(count):
                    self._paste_after()
        elif char == "P":
            if self.read_only:
                self._count_buf = ""
                self.status_msg = "[readonly]"
            else:
                count = self._consume_count()
                self._dot_start(event)
                self._dot_stop()
                for _ in range(count):
                    self._paste_before()
        elif char == "u":
            self._count_buf = ""
            if self.read_only:
                self.status_msg = "[readonly]"
            else:
                self._undo()
        elif key == "ctrl+r":
            self._count_buf = ""
            if self.read_only:
                self.status_msg = "[readonly]"
            else:
                self._redo()
        elif char == "J":
            if self.read_only:
                self._count_buf = ""
                self.status_msg = "[readonly]"
            else:
                count = self._consume_count()
                self._dot_start(event)
                self._dot_stop()
                for _ in range(count):
                    self._join_lines()

        # dot repeat
        elif char == ".":
            self._count_buf = ""
            if not self.read_only:
                self._dot_replay()

        # multi-key starters (count는 pending에서 계속 누적 가능)
        elif char in ("d", "c", "y", "r", "g", "e", "z"):
            # Visual mode 연산자 인터셉트
            if self._visual_mode and char in ("d", "y", "c"):
                self._count_buf = ""
                self._execute_visual_operator(char)
                return
            if self.read_only and char not in ("y", "g", "e", "z"):
                self._count_buf = ""
                self.status_msg = "[readonly]"
            else:
                if char not in ("y", "g", "e", "z"):
                    self._dot_start(event)
                self.pending = char

        # search mode
        elif char == "/":
            self._count_buf = ""
            self._visual_mode = ""
            self._mode = EditorMode.SEARCH
            self._search_buffer = ""
            self._search_forward = True
            self.status_msg = ""
        elif char == "?":
            self._count_buf = ""
            self._visual_mode = ""
            self._mode = EditorMode.SEARCH
            self._search_buffer = ""
            self._search_forward = False
            self.status_msg = ""
        elif char == "n":
            self._count_buf = ""
            self._goto_next_match()
        elif char == "N":
            self._count_buf = ""
            self._goto_prev_match()

        # command mode
        elif char == ":":
            self._count_buf = ""
            self._visual_mode = ""
            self._mode = EditorMode.COMMAND
            self.command_buffer = ""
            self.status_msg = ""

    # -- Pending multi-char ------------------------------------------------

    def _handle_pending(self, char: str, key: str) -> None:
        if key == "escape" or not char:
            self.pending = ""
            self._count_buf = ""
            self.status_msg = ""
            self._dot_stop()
            return

        # pending 상태에서 숫자 입력 → count에 누적 (d3d 패턴)
        if char.isdigit() and self.pending in ("d", "y", "c"):
            self._count_buf += char
            return

        combo = self.pending + char
        self.pending = ""

        if self.read_only and combo not in ("yy", "gg", "ej"):
            self._count_buf = ""
            self.status_msg = "[readonly]"
            return

        if combo == "dd":
            count = self._consume_count()
            self._save_undo()
            self._yank_type = "line"
            yanked: list[str] = []
            for _ in range(count):
                if len(self.lines) <= 1 and not yanked:
                    yanked.append(self.lines[0])
                    self.lines[0] = ""
                    break
                if self.cursor_row >= len(self.lines):
                    break
                # fold-aware: fold 헤더이면 전체 블록 삭제
                fold_end = self._folds.get(self.cursor_row)
                if fold_end is not None:
                    block = self.lines[self.cursor_row : fold_end + 1]
                    yanked.extend(block)
                    del self._folds[self.cursor_row]
                    self._folded_lines_dirty = True
                    block_len = len(block)
                    del self.lines[self.cursor_row : self.cursor_row + block_len]
                    if not self.lines:
                        self.lines = [""]
                    self._adjust_line_indices(self.cursor_row, -block_len)
                else:
                    yanked.append(self.lines[self.cursor_row])
                    if len(self.lines) > 1:
                        deleted_at = self.cursor_row
                        self.lines.pop(self.cursor_row)
                        self._adjust_line_indices(deleted_at, -1)
                    else:
                        self.lines[0] = ""
                        break
                if self.cursor_row >= len(self.lines):
                    self.cursor_row = len(self.lines) - 1
            self.yank_buffer = yanked
            if self.cursor_row >= len(self.lines):
                self.cursor_row = len(self.lines) - 1
            self.cursor_col = 0
            n = len(yanked)
            self.status_msg = f"{n} line{'s' if n > 1 else ''} deleted"
            self._dot_stop()

        elif combo == "dw":
            self._count_buf = ""
            self._save_undo()
            self._delete_word()
            self._dot_stop()

        elif combo == "d$":
            self._count_buf = ""
            self._save_undo()
            line = self.lines[self.cursor_row]
            self.lines[self.cursor_row] = line[: self.cursor_col]
            self._dot_stop()

        elif combo == "d0":
            self._count_buf = ""
            self._save_undo()
            line = self.lines[self.cursor_row]
            self.lines[self.cursor_row] = line[self.cursor_col :]
            self.cursor_col = 0
            self._dot_stop()

        elif combo == "cw":
            self._count_buf = ""
            self._save_undo()
            self._delete_word()
            self._enter_insert()
            # recording continues into insert mode

        elif combo == "cc":
            self._count_buf = ""
            self._save_undo()
            self._yank_type = "line"
            indent = self._current_indent()
            self.yank_buffer = [self.lines[self.cursor_row]]
            self.lines[self.cursor_row] = " " * indent
            self.cursor_col = indent
            self._enter_insert()
            # recording continues into insert mode

        elif combo == "yy":
            count = self._consume_count()
            self._yank_type = "line"
            yanked = []
            row = self.cursor_row
            for _ in range(count):
                if row >= len(self.lines):
                    break
                # fold-aware: fold 헤더이면 전체 블록 yank
                fold_end = self._folds.get(row)
                if fold_end is not None:
                    yanked.extend(self.lines[row : fold_end + 1])
                    row = fold_end + 1
                else:
                    yanked.append(self.lines[row])
                    row += 1
            self.yank_buffer = yanked
            n = len(yanked)
            self.status_msg = f"{n} line{'s' if n > 1 else ''} yanked"

        elif combo == "gg":
            count = self._consume_count()
            if count > 1:
                self.cursor_row = count - 1
            else:
                self.cursor_row = 0
            self.cursor_col = 0
            self._scroll_cursor_to_top()

        elif len(combo) == 2 and combo[0] == "r":
            self._count_buf = ""
            self._save_undo()
            line = self.lines[self.cursor_row]
            if self.cursor_col < len(line):
                self.lines[self.cursor_row] = (
                    line[: self.cursor_col] + combo[1] + line[self.cursor_col + 1 :]
                )
            self._dot_stop()

        elif combo == "ej":
            self._count_buf = ""
            self._edit_embedded_json()

        # fold 명령어
        elif combo == "za":
            self._count_buf = ""
            self._toggle_fold(self.cursor_row)
        elif combo == "zo":
            self._count_buf = ""
            self._open_fold(self.cursor_row)
        elif combo == "zc":
            self._count_buf = ""
            self._close_fold(self.cursor_row)
        elif combo == "zM":
            self._count_buf = ""
            self._fold_all()
        elif combo == "zR":
            self._count_buf = ""
            self._unfold_all()

        else:
            self._count_buf = ""
            self._dot_stop()
            self.status_msg = f"unknown: {combo}"
