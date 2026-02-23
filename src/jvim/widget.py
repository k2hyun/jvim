"""Modal JSON editor widget."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum, auto

from rich.text import Text
from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget

from jvim.action import (
    ClipboardMixin,
    FoldMixin,
    NavigationMixin,
    SubstituteMixin,
    VisualMixin,
)
from jvim.mode import CommandMixin, InsertMixin, NormalMixin, SearchMixin


class EditorMode(Enum):
    NORMAL = auto()
    INSERT = auto()
    COMMAND = auto()
    SEARCH = auto()


class JsonEditor(
    NormalMixin,
    InsertMixin,
    CommandMixin,
    ClipboardMixin,
    NavigationMixin,
    VisualMixin,
    FoldMixin,
    SubstituteMixin,
    SearchMixin,
    Widget,
    can_focus=True,
):
    """A modal JSON editor Textual widget.

    Supported commands:
      NORMAL: h j k l  w b  0 $ ^  gg G  %  i I a A o O
              x  dd dw d$  cw cc  r{c}  J  yy p P  u
      INSERT: typing / Backspace / Enter / Tab / Escape
      COMMAND: :w :q :wq :fmt
    """

    DEFAULT_CSS = """
    JsonEditor {
        height: 1fr;
        background: $surface;
        padding: 0 1;
    }
    """

    mode: reactive[EditorMode] = reactive(EditorMode.NORMAL)

    # -- Messages ----------------------------------------------------------

    @dataclass
    class JsonValidated(Message):
        content: str
        valid: bool
        error: str = ""

    @dataclass
    class FileSaveRequested(Message):
        content: str
        file_path: str  # empty string means save to current file
        quit_after: bool = False

    @dataclass
    class FileOpenRequested(Message):
        file_path: str

    @dataclass
    class Quit(Message):
        pass

    @dataclass
    class ForceQuit(Message):
        pass

    @dataclass
    class HelpToggleRequested(Message):
        pass

    @dataclass
    class EmbeddedEditRequested(Message):
        content: str  # Parsed JSON content to edit
        source_row: int  # Row of the string value
        source_col_start: int  # Column where string starts (including quote)
        source_col_end: int  # Column where string ends (including quote)

    @dataclass
    class EmbeddedEditSave(Message):
        content: str  # Updated JSON content

    # -- Init --------------------------------------------------------------

    def __init__(
        self,
        initial_content: str = "",
        *,
        read_only: bool = False,
        jsonl: bool = False,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.read_only: bool = read_only
        self.jsonl: bool = jsonl
        if self.jsonl and initial_content:
            initial_content = self._jsonl_to_pretty(initial_content)
        self.lines: list[str] = initial_content.split("\n") if initial_content else [""]
        self.cursor_row: int = 0
        self.cursor_col: int = 0
        self._mode: EditorMode = EditorMode.NORMAL
        self.command_buffer: str = ""
        self.pending: str = ""
        self.status_msg: str = ""
        self.undo_stack: list[tuple[list[str], int, int]] = []
        self.redo_stack: list[tuple[list[str], int, int]] = []
        self.yank_buffer: list[str] = []
        self._scroll_top: int = 0
        self._dot_buffer: list[tuple[str, str | None]] = []
        self._dot_recording: bool = False
        self._dot_replaying: bool = False
        # Search state
        self._search_buffer: str = ""
        self._search_pattern: str = ""
        self._search_forward: bool = True  # True for /, False for ?
        self._search_matches: list[
            tuple[int, int, int]
        ] = []  # (row, col_start, col_end)
        self._search_match_by_row: dict[
            int, list[tuple[int, int, int]]
        ] = {}  # Fast lookup
        self._current_match: int = -1  # Index in _search_matches
        self._search_history: list[str] = []  # Previous search patterns
        self._search_history_idx: int = (
            -1
        )  # Current position in history (-1 = new search)
        self._search_history_max: int = 50  # Max history size
        # Command history
        self._command_history: list[str] = []  # Previous commands
        self._command_history_idx: int = -1  # Current position in history
        self._command_history_max: int = 50  # Max history size
        # Render caches
        self._style_cache: dict[int, list[str]] = {}
        self._cache_dirty: bool = False
        self._jsonl_records_cache: list[int] | None = None
        self._char_width_cache: dict[str, int] = {}
        # Fold state
        self._folds: dict[int, int] = {}  # {fold_header_line: fold_end_line}
        self._collapsed_strings: set[int] = set()  # 접힌 긴 string 라인
        self._string_collapse_threshold: int = (
            60  # 이 길이 이상의 string value를 접기 대상으로
        )
        # Visual mode 상태
        self._visual_mode: str = ""  # "" | "v" | "V"
        self._visual_anchor_row: int = 0  # 선택 시작 row
        self._visual_anchor_col: int = 0  # 선택 시작 col (v 모드용)
        self._yank_type: str = "line"  # "line" | "char" — paste 동작 결정
        # Tab 자동완성 상태
        self._tab_completions: list[str] = []
        self._tab_index: int = -1  # -1: 공통 접두사, 0+: 후보 순회 중
        # 초기 로드 시 긴 문자열 자동 접기
        for i in range(len(self.lines)):
            if self._find_long_string_at(i):
                self._collapsed_strings.add(i)

    # -- Helpers -----------------------------------------------------------

    def _invalidate_caches(self) -> None:
        """Invalidate render caches when content changes."""
        self._cache_dirty = True

    def _check_readonly(self) -> bool:
        """Check if read-only and set status. Returns True if read-only."""
        if self.read_only:
            self.status_msg = "[readonly]"
        return self.read_only

    def _dot_start(self, event) -> None:
        """Begin recording a new edit sequence for dot-repeat."""
        if self._dot_replaying:
            return
        self._dot_buffer = [(event.key, event.character)]
        self._dot_recording = True

    def _dot_stop(self) -> None:
        self._dot_recording = False

    def _dot_replay(self) -> None:
        """Replay the last recorded edit sequence."""
        if not self._dot_buffer:
            return
        from types import SimpleNamespace

        self._dot_replaying = True
        for rkey, rchar in self._dot_buffer:
            mock = SimpleNamespace(key=rkey, character=rchar)
            if self._mode == EditorMode.NORMAL:
                self._handle_normal(mock)
            elif self._mode == EditorMode.INSERT:
                self._handle_insert(mock)
            self._clamp_cursor()
        self._dot_replaying = False

    def _save_undo(self) -> None:
        self.undo_stack.append((self.lines[:], self.cursor_row, self.cursor_col))
        if len(self.undo_stack) > 200:
            self.undo_stack.pop(0)
        # Clear redo stack on new edit
        if self.redo_stack:
            self.redo_stack.clear()
        self._invalidate_caches()

    def _clamp_cursor(self) -> None:
        self.cursor_row = max(0, min(self.cursor_row, len(self.lines) - 1))
        # fold 안이면 fold 헤더로 snap
        if self._folds and self._is_line_folded(self.cursor_row):
            for start, end in self._folds.items():
                if start < self.cursor_row <= end:
                    self.cursor_row = start
                    break
        # collapsed string에서 커서가 숨겨진 영역에 진입하면 자동 펼기
        row = self.cursor_row
        if row in self._collapsed_strings:
            info = self._find_long_string_at(row)
            if info:
                qs, _qe, _slen = info
                # 미리보기 끝 위치: 여는 따옴표 + preview 문자수
                visible_end = qs + 1 + min(20, _qe - qs - 2)
                if self.cursor_col > visible_end:
                    self._collapsed_strings.discard(row)
        # fold 헤더에서 커서가 라인 끝을 넘으려 하면 자동 펼기
        if row in self._folds:
            line_len_here = len(self.lines[row])
            max_here = max(0, line_len_here - 1) if line_len_here else 0
            if self.cursor_col > max_here:
                del self._folds[row]
        line_len = len(self.lines[self.cursor_row])
        if self._mode == EditorMode.NORMAL:
            max_col = max(0, line_len - 1) if line_len else 0
        else:
            max_col = line_len
        self.cursor_col = max(0, min(self.cursor_col, max_col))

    def _char_width(self, ch: str) -> int:
        """Return display width of a character (2 for fullwidth/wide)."""
        if ch < "\u0100":
            return 1
        w = self._char_width_cache.get(ch)
        if w is None:
            w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
            self._char_width_cache[ch] = w
        return w

    def _make_segments(self, line: str, avail: int) -> list[tuple[int, int]]:
        """Break *line* into segments fitting within *avail* display columns."""
        if not line:
            return [(0, 0)]
        if line.isascii():
            return [(s, min(s + avail, len(line))) for s in range(0, len(line), avail)]
        segs: list[tuple[int, int]] = []
        seg_start = 0
        w = 0
        for i, ch in enumerate(line):
            cw = self._char_width(ch)
            if w + cw > avail and i > seg_start:
                segs.append((seg_start, i))
                seg_start = i
                w = cw
            else:
                w += cw
        segs.append((seg_start, len(line)))
        return segs

    def _wrap_rows(self, line: str, avail: int) -> int:
        """Return the number of display rows a line occupies when wrapped."""
        if not line:
            return 1
        if line.isascii():
            return -(-len(line) // avail)
        rows = 1
        w = 0
        for ch in line:
            cw = self._char_width(ch)
            if w + cw > avail:
                rows += 1
                w = cw
            else:
                w += cw
        return rows

    def _cursor_wrap_dy(self, line: str, cursor_col: int, avail: int) -> int:
        """Return the wrapped row index (0-based) of *cursor_col* within *line*."""
        segs = self._make_segments(line, avail)
        for si, (s_start, s_end) in enumerate(segs):
            if cursor_col < s_end:
                return si
        # cursor at end of line — check if cursor block fits on last row
        if line:
            ls, le = segs[-1]
            last_w = sum(self._char_width(line[c]) for c in range(ls, le))
            if last_w + 1 > avail:
                return len(segs)
        return max(0, len(segs) - 1)

    def _gutter_widths(self) -> tuple[int, int, int]:
        """Return ``(ln_width, rec_width, prefix_width)``.

        *rec_width* is 0 when not in JSONL mode.
        """
        ln_width = max(3, len(str(len(self.lines))))
        if not self.jsonl:
            return ln_width, 0, ln_width + 1
        rec_count = 0
        in_block = False
        for line in self.lines:
            if line.strip():
                if not in_block:
                    rec_count += 1
                    in_block = True
            else:
                in_block = False
        rec_width = max(2, len(str(max(1, rec_count))))
        return ln_width, rec_width, rec_width + 1 + ln_width + 1

    def _jsonl_line_records(self) -> list[int]:
        """Map each editor line to its JSONL record number.

        The first line of each block gets the 1-based record number;
        all other lines (continuation / blank separator) get 0.
        """
        result = [0] * len(self.lines)
        record = 0
        in_block = False
        for i, line in enumerate(self.lines):
            if line.strip():
                if not in_block:
                    record += 1
                    result[i] = record
                    in_block = True
            else:
                in_block = False
        return result

    def _visible_height(self) -> int:
        return max(1, self.content_region.height - 2)

    def _ensure_cursor_visible(self, avail: int) -> None:
        base_vh = self._visible_height()
        jsonl_records = self._jsonl_records_cache if self.jsonl else None

        def _effective_vh(scroll_top: int) -> int:
            if jsonl_records and scroll_top > 0 and jsonl_records[scroll_top] == 0:
                return base_vh - 1
            return base_vh

        vh = _effective_vh(self._scroll_top)

        if self.cursor_row < self._scroll_top:
            self._scroll_top = self.cursor_row

        wrap_rows = self._wrap_rows
        lines = self.lines
        is_folded = self._is_line_folded if self._folds else None
        rows_before = sum(
            wrap_rows(lines[i], avail)
            for i in range(self._scroll_top, self.cursor_row)
            if not (is_folded and is_folded(i))
        )
        cursor_dy = self._cursor_wrap_dy(lines[self.cursor_row], self.cursor_col, avail)
        while rows_before + cursor_dy >= vh and self._scroll_top <= self.cursor_row:
            if not (is_folded and is_folded(self._scroll_top)):
                rows_before -= wrap_rows(lines[self._scroll_top], avail)
            self._scroll_top += 1
            vh = _effective_vh(self._scroll_top)

    def _scroll_cursor_to_top(self) -> None:
        """Position viewport so cursor is at the top of the screen."""
        self._scroll_top = self.cursor_row

    def _scroll_cursor_to_center(self, ratio: float = 0.33) -> None:
        """Position viewport so cursor is at given ratio from top (default 1/3)."""
        vh = self._visible_height()
        offset = int(vh * ratio)
        self._scroll_top = max(0, self.cursor_row - offset)

    # -- Public API --------------------------------------------------------

    def get_content(self) -> str:
        return "\n".join(self.lines)

    def set_content(self, content: str) -> None:
        if self.jsonl and content:
            content = self._jsonl_to_pretty(content)
        self.lines = content.split("\n") if content else [""]
        self.cursor_row = 0
        self.cursor_col = 0
        self._folds.clear()
        self._collapsed_strings.clear()
        # 초기 로드 시 긴 문자열 자동 접기
        for i in range(len(self.lines)):
            if self._find_long_string_at(i):
                self._collapsed_strings.add(i)
        self._invalidate_caches()
        self.refresh()

    def get_history(self) -> dict:
        """Get search and command history for persistence."""
        return {
            "search": self._search_history[:],
            "command": self._command_history[:],
        }

    def set_history(self, history: dict) -> None:
        """Restore search and command history."""
        if "search" in history:
            self._search_history = history["search"][: self._search_history_max]
        if "command" in history:
            self._command_history = history["command"][: self._command_history_max]

    # =====================================================================
    # Rendering
    # =====================================================================

    def _line_background(self, line_idx: int) -> str:
        """서브클래스에서 라인별 배경 스타일을 지정하기 위한 훅."""
        return ""

    def render(self) -> Text:
        width = self.content_region.width
        height = self.content_region.height
        if height < 3 or width < 10:
            return Text("(too small)")

        # Flush caches when content changed
        if self._cache_dirty:
            self._style_cache.clear()
            self._jsonl_records_cache = None
            self._cache_dirty = False

        content_height = height - 2
        ln_width, rec_width, prefix_w = self._gutter_widths()
        avail = max(1, width - prefix_w)
        # Use cached JSONL records
        if self.jsonl:
            if self._jsonl_records_cache is None:
                self._jsonl_records_cache = self._jsonl_line_records()
            jsonl_records = self._jsonl_records_cache
        else:
            jsonl_records = None

        self._ensure_cursor_visible(avail)

        # Local references for hot path
        lines = self.lines
        cursor_row = self.cursor_row
        cursor_col = self.cursor_col
        make_segments = self._make_segments
        char_width = self._char_width
        style_cache = self._style_cache
        compute_styles = self._compute_line_styles
        search_by_row = self._search_match_by_row
        result_append = Text.append

        result = Text()
        rows_used = 0
        line_idx = self._scroll_top
        num_lines = len(lines)
        gutter_pad = " " * prefix_w  # 래핑된 줄의 거터 공백 (미리 생성)

        # Floating header for JSONL: show record start line when scrolled into middle of record
        if self.jsonl and jsonl_records and self._scroll_top > 0:
            first_visible_rec = jsonl_records[self._scroll_top]
            if first_visible_rec == 0:
                # We're in the middle of a record, find its start line
                rec_start_line = self._scroll_top - 1
                while rec_start_line >= 0 and jsonl_records[rec_start_line] == 0:
                    rec_start_line -= 1
                if rec_start_line >= 0:
                    rec_num = jsonl_records[rec_start_line]
                    # Show floating header
                    header = (
                        f"{rec_start_line + 1:>{ln_width}} {rec_num:>{rec_width}} ↓"
                    )
                    result_append(result, header, style="bold cyan on grey23")
                    result_append(result, " " * (width - len(header)) + "\n")
                    rows_used += 1

        folds = self._folds
        collapsed_strs = self._collapsed_strings
        while rows_used < content_height and line_idx < num_lines:
            # 접힌 라인 스킵
            if folds and self._is_line_folded(line_idx):
                line_idx += 1
                continue

            line = lines[line_idx]
            is_cursor_line = line_idx == cursor_row
            is_fold_header = line_idx in folds

            # Collapsed string: 라인을 잘라서 표시
            str_collapse_info = None
            if line_idx in collapsed_strs:
                info = self._find_long_string_at(line_idx)
                if info:
                    qs, qe, slen = info
                    # 미리보기: 처음 20자 + ...
                    preview_len = min(20, qe - qs - 2)
                    # raw string에서 미리보기 추출 (따옴표 안쪽)
                    preview = line[qs + 1 : qs + 1 + preview_len]
                    suffix = f'..." ({slen} chars)'
                    # 접힌 라인: key 부분 + "preview..." (N chars) + trailing
                    collapsed_line = line[:qs] + '"' + preview + suffix + line[qe:]
                    collapsed_styles = compute_styles(collapsed_line)
                    # suffix 부분을 dim italic으로 변경
                    suffix_start = qs + 1 + preview_len
                    for ci in range(suffix_start, suffix_start + len(suffix)):
                        if ci < len(collapsed_styles):
                            collapsed_styles[ci] = "dim italic"
                    str_collapse_info = (collapsed_line, collapsed_styles)

            if str_collapse_info:
                line, line_styles = str_collapse_info
            else:
                # Use cached styles or compute
                if line_idx in style_cache:
                    line_styles = style_cache[line_idx]
                else:
                    line_styles = compute_styles(line)
                    style_cache[line_idx] = line_styles

            line_len = len(line)

            # Break line into width-aware wrapped segments
            segs = make_segments(line, avail)
            # Cursor at end of line may need an extra wrap row
            if is_cursor_line and cursor_col >= line_len and line:
                ls, le = segs[-1]
                last_w = sum(char_width(line[c]) for c in range(ls, le))
                if last_w + 1 > avail:
                    segs.append((line_len, line_len))

            # 라인 배경 (diff 하이라이팅 등 서브클래스용 훅)
            line_bg = self._line_background(line_idx)
            has_search = search_by_row and line_idx in search_by_row
            has_visual = bool(self._visual_mode) and line_len > 0
            # 변이가 필요한 경우에만 복사
            if line_bg or has_visual or has_search:
                line_styles = line_styles[:]
                if line_bg:
                    for c in range(len(line_styles)):
                        line_styles[c] = f"{line_bg} {line_styles[c]}"
                # Visual 하이라이트 (search보다 아래 — search가 위에 보이도록)
                if has_visual:
                    vsr, vsc, ver, vec = self._visual_selection_range()
                    if self._visual_mode == "V":
                        if vsr <= line_idx <= ver:
                            for c in range(line_len):
                                line_styles[c] = "on dark_blue"
                    else:
                        if vsr == ver == line_idx:
                            for c in range(vsc, min(vec + 1, line_len)):
                                line_styles[c] = "on dark_blue"
                        elif line_idx == vsr:
                            for c in range(vsc, line_len):
                                line_styles[c] = "on dark_blue"
                        elif line_idx == ver:
                            for c in range(0, min(vec + 1, line_len)):
                                line_styles[c] = "on dark_blue"
                        elif vsr < line_idx < ver:
                            for c in range(line_len):
                                line_styles[c] = "on dark_blue"
                if has_search:
                    for m_start, m_end, mi in search_by_row[line_idx]:
                        is_current = mi == self._current_match
                        style = (
                            "black on yellow"
                            if is_current
                            else "black on dark_goldenrod"
                        )
                        for c in range(m_start, min(m_end, line_len)):
                            line_styles[c] = style

            # Collapsed string은 1줄만 렌더 (wrap 방지)
            if str_collapse_info:
                segs = segs[:1]

            for si, (s_start, s_end) in enumerate(segs):
                if rows_used >= content_height:
                    break
                # Line number on first segment, or first visible row (floating line number)
                if si == 0 or rows_used == 0:
                    result_append(
                        result, f"{line_idx + 1:>{ln_width}} ", style="dim cyan"
                    )
                    if rec_width:
                        rec_num = jsonl_records[line_idx]
                        if rec_num:
                            result_append(
                                result, f"{rec_num:>{rec_width}} ", style="dim yellow"
                            )
                        else:
                            result_append(result, " " * (rec_width + 1))
                else:
                    result_append(result, gutter_pad)
                # Render segment — batch consecutive chars with same style
                col = s_start
                while col < s_end:
                    if is_cursor_line and col == cursor_col:
                        result_append(
                            result, line[col], style=f"reverse {line_styles[col]}"
                        )
                        col += 1
                        continue
                    sty = line_styles[col]
                    end = col + 1
                    while (
                        end < s_end
                        and line_styles[end] == sty
                        and not (is_cursor_line and end == cursor_col)
                    ):
                        end += 1
                    result_append(result, line[col:end], style=sty)
                    col = end
                # Cursor block at end of line (insert mode)
                if is_cursor_line and cursor_col >= line_len and si == len(segs) - 1:
                    result_append(result, " ", style="reverse")
                # Fold summary 표시
                fold_summary_w = 0
                if is_fold_header and si == 0:
                    hidden = folds[line_idx] - line_idx
                    summary = f" ... ({hidden} lines)"
                    fold_summary_w = len(summary)
                    result_append(result, summary, style="dim italic")
                # 라인 배경이 있으면 나머지 너비를 배경색으로 채움
                if line_bg:
                    seg_w = sum(char_width(line[c]) for c in range(s_start, s_end))
                    if (
                        is_cursor_line
                        and cursor_col >= line_len
                        and si == len(segs) - 1
                    ):
                        seg_w += 1
                    pad = avail - seg_w - fold_summary_w
                    if pad > 0:
                        result_append(result, " " * pad, style=line_bg)
                result_append(result, "\n")
                rows_used += 1

            line_idx += 1

        # Fill remaining rows with ~
        if rows_used < content_height:
            tilde_line = f"{'~':>{prefix_w - 1}} \n"
            while rows_used < content_height:
                result_append(result, tilde_line, style="dim blue")
                rows_used += 1

        # status bar (wildmenu: 후보 목록이 있으면 status bar를 대체)
        mode = self._mode
        if self._tab_completions and mode == EditorMode.COMMAND:
            self._render_wildmenu(result, result_append, width)
        else:
            if self._visual_mode:
                mode_label = " VISUAL LINE " if self._visual_mode == "V" else " VISUAL "
                mode_style = "bold white on dark_orange"
            else:
                mode_label = f" {mode.name} "
                mode_style = self._MODE_STYLE[mode]
            result_append(result, mode_label, style=mode_style)

            read_only = self.read_only
            if read_only:
                result_append(result, " RO ", style="bold white on grey37")

            pending = self.pending
            if pending:
                result_append(result, f"  {pending}", style="bold yellow")

            status_msg = self.status_msg
            pos = f" Ln {cursor_row + 1}/{num_lines}, Col {cursor_col + 1} "
            ro_len = 4 if read_only else 0
            spacer_len = max(
                0, width - len(mode_label) - ro_len - len(pos) - len(status_msg) - 4
            )
            result_append(result, f"  {status_msg}")
            if spacer_len:
                result_append(result, " " * spacer_len)
            result_append(result, pos, style="bold")

        if mode == EditorMode.COMMAND:
            result_append(result, f"\n:{self.command_buffer}", style="bold yellow")
            result_append(result, " ", style="reverse")
        elif mode == EditorMode.SEARCH:
            prefix = "/" if self._search_forward else "?"
            result_append(
                result, f"\n{prefix}{self._search_buffer}", style="bold magenta"
            )
            result_append(result, " ", style="reverse")
        else:
            result_append(result, "\n")

        return result

    # -- Selection (마우스 선택 시 거터 제외) --------------------------------

    def get_selection(self, selection) -> tuple[str, str] | None:
        """마우스 선택 시 거터(줄 번호)를 제외한 콘텐츠만 반환."""
        from textual.geometry import Offset
        from textual.selection import Selection as Sel

        visual = self._render()
        text = str(visual)
        lines = text.splitlines()
        if not lines:
            return "", "\n"

        _, _, prefix_w = self._gutter_widths()
        # 마지막 2줄(상태바 + 명령줄)은 거터가 없음
        content_end = max(0, len(lines) - 2)
        stripped = []
        for i, line in enumerate(lines):
            if i < content_end:
                stripped.append(line[prefix_w:] if len(line) > prefix_w else "")
            else:
                stripped.append(line)

        # Selection 오프셋에서 거터 너비를 빼서 조정
        start = selection.start
        end = selection.end
        if start is not None and start.y < content_end:
            start = Offset(max(0, start.x - prefix_w), start.y)
        if end is not None and end.y < content_end:
            end = Offset(max(0, end.x - prefix_w), end.y)

        return Sel(start, end).extract("\n".join(stripped)), "\n"

    # -- Wildmenu rendering ------------------------------------------------

    def _render_wildmenu(self, result: Text, result_append, width: int) -> None:
        """Tab 자동완성 후보를 status bar 영역에 wildmenu 스타일로 렌더링."""
        comps = self._tab_completions
        idx = self._tab_index
        n = len(comps)

        # 선택 항목이 보이도록 표시 윈도우 결정
        start = 0
        end = n
        if idx >= 0:
            # 선택 항목을 포함하면서 왼쪽으로 확장
            start = idx
            w = len(comps[idx])
            # 오른쪽에 더 있을 수 있으므로 " >" 2칸 예약
            if idx < n - 1:
                w += 2
            while start > 0:
                prev_w = 2 + len(comps[start - 1])
                # 왼쪽에 더 남으면 "< " + "  " = 4칸 예약
                reserve = 4 if (start - 1) > 0 else 0
                if w + prev_w + reserve > width:
                    break
                w += prev_w
                start -= 1

        # start부터 오른쪽으로 width에 맞게 end 결정
        has_left = start > 0
        budget = width
        if has_left:
            budget -= 4  # "< " + "  " 최소 공간
        acc = 0
        end = start
        for i in range(start, n):
            item_w = len(comps[i]) + (2 if i > start else 0)
            # 이 아이템 뒤에 더 있으면 " >" 2칸 예약
            right_reserve = 2 if (i + 1) < n else 0
            if acc + item_w + right_reserve > budget:
                break
            acc += item_w
            end = i + 1
        has_right = end < n

        # 렌더링
        used = 0
        # 왼쪽 overflow
        if has_left:
            result_append(result, "< ", style="bold yellow on grey23")
            used += 2
            prev_name = comps[start - 1]
            avail = budget - acc - (2 if has_right else 0)  # " >" 공간 확보
            if avail >= len(prev_name):
                result_append(result, prev_name, style="dim on grey23")
                used += len(prev_name)
            elif avail >= 2:
                result_append(
                    result, prev_name[: avail - 1] + "\u2026", style="dim on grey23"
                )
                used += avail
            result_append(result, "  ", style="on grey23")
            used += 2

        # 메인 항목
        for i in range(start, end):
            if i > start:
                result_append(result, "  ", style="on grey23")
                used += 2
            style = "bold black on white" if i == idx else "bold on grey23"
            result_append(result, comps[i], style=style)
            used += len(comps[i])

        # 오른쪽 overflow
        remaining = width - used
        if has_right and remaining >= 2:
            if remaining > 4:
                result_append(result, "  ", style="on grey23")
                remaining -= 2
                next_name = comps[end]
                avail = remaining - 2  # " >" 예약
                if avail >= len(next_name):
                    result_append(result, next_name, style="dim on grey23")
                    remaining -= len(next_name)
                elif avail >= 2:
                    result_append(
                        result, next_name[: avail - 1] + "\u2026", style="dim on grey23"
                    )
                    remaining -= avail
            result_append(result, " " * max(0, remaining - 1), style="on grey23")
            result_append(result, ">", style="bold yellow on grey23")
            remaining = 0

        # 남은 공간 패딩
        if remaining > 0:
            result_append(result, " " * remaining, style="on grey23")

    # -- Syntax colouring helpers ------------------------------------------

    _BRACKET = frozenset("{}[]")
    _PUNCT = frozenset(":,")
    _DIGIT = frozenset("0123456789.-+eE")
    _KEYWORDS = ("true", "false", "null")
    _KEYWORD_RE = re.compile(r"true|false|null")
    _MODE_STYLE = {
        EditorMode.NORMAL: "bold white on dark_green",
        EditorMode.INSERT: "bold white on dark_blue",
        EditorMode.COMMAND: "bold white on dark_red",
        EditorMode.SEARCH: "bold white on dark_magenta",
    }

    def _compute_line_styles(self, line: str) -> list[str]:
        """Compute syntax highlight styles for every character in *line*."""
        n = len(line)
        if n == 0:
            return []

        # Local references for hot path
        BRACKET = self._BRACKET
        DIGIT = self._DIGIT

        styles = ["white"] * n
        is_in_str = [False] * n

        # Single pass: track string regions and first unquoted colon
        in_str = False
        first_colon = -1
        prev_ch = ""

        for i, ch in enumerate(line):
            if ch == '"' and prev_ch != "\\":
                in_str = not in_str
                is_in_str[i] = True
            elif in_str:
                is_in_str[i] = True
            elif ch == ":" and first_colon == -1:
                first_colon = i
            prev_ch = ch

        # Assign styles in single pass
        for i, ch in enumerate(line):
            if ch in BRACKET:
                styles[i] = "bold white"
            elif is_in_str[i]:
                styles[i] = "cyan" if first_colon == -1 or i < first_colon else "green"
            elif ch in DIGIT:
                styles[i] = "yellow"
            # PUNCT stays "white" (default)

        # Keywords outside strings (single regex pass)
        for m in self._KEYWORD_RE.finditer(line):
            ms, me = m.start(), m.end()
            if not is_in_str[ms]:
                for j in range(ms, me):
                    styles[j] = "magenta"

        return styles

    # =====================================================================
    # Key handling
    # =====================================================================

    def on_key(self, event: events.Key) -> None:
        event.prevent_default()
        event.stop()

        if not self._dot_replaying and self._dot_recording:
            self._dot_buffer.append((event.key, event.character))

        if self._mode == EditorMode.NORMAL:
            self._handle_normal(event)
        elif self._mode == EditorMode.INSERT:
            self._handle_insert(event)
        elif self._mode == EditorMode.COMMAND:
            self._handle_command(event)
        elif self._mode == EditorMode.SEARCH:
            self._handle_search(event)

        self._clamp_cursor()
        self.refresh()

    # -- JSON operations ---------------------------------------------------

    def _check_content(self, content: str) -> tuple[bool, str]:
        """Validate content as JSON or JSONL. Returns (valid, error_msg)."""
        if self.jsonl:
            blocks = self._split_jsonl_blocks(content)
            for i, block in enumerate(blocks, 1):
                try:
                    json.loads(block)
                except json.JSONDecodeError as e:
                    return False, f"JSONL error: record {i}: {e.msg}"
            return True, ""
        try:
            json.loads(content)
            return True, ""
        except json.JSONDecodeError as e:
            return False, f"JSON error: {e.msg} (line {e.lineno})"

    def _validate_json(self) -> bool:
        content = self.get_content()
        valid, err = self._check_content(content)
        if valid:
            label = "JSONL" if self.jsonl else "JSON"
            self.status_msg = f"{label} valid"
            self.post_message(self.JsonValidated(content=content, valid=True))
            return True
        self.status_msg = err
        self.post_message(self.JsonValidated(content=content, valid=False, error=err))
        return False

    def _format_json(self) -> None:
        if self.jsonl:
            self._format_jsonl()
            return
        content = self.get_content()
        try:
            parsed = json.loads(content)
            formatted = json.dumps(parsed, indent=4, ensure_ascii=False)
            self._save_undo()
            self.lines = formatted.split("\n")
            self.cursor_row = 0
            self.cursor_col = 0
            self._folds.clear()
            self._collapsed_strings.clear()
            self.status_msg = "formatted"
        except json.JSONDecodeError as e:
            self.status_msg = f"cannot format: {e.msg} (line {e.lineno})"

    def _format_jsonl(self) -> None:
        content = self.get_content()
        blocks = self._split_jsonl_blocks(content)
        formatted: list[str] = []
        for i, block in enumerate(blocks):
            try:
                parsed = json.loads(block)
                formatted.append(json.dumps(parsed, indent=4, ensure_ascii=False))
            except json.JSONDecodeError as e:
                self.status_msg = f"cannot format: record {i + 1}: {e.msg}"
                return
        self._save_undo()
        self.lines = "\n\n".join(formatted).split("\n")
        self._folds.clear()
        self._collapsed_strings.clear()
        self.cursor_row = 0
        self.cursor_col = 0
        self.status_msg = "formatted"

    def _find_string_at_cursor(self) -> tuple[int, int, str] | None:
        """Find a string value on the current line.

        Returns (col_start, col_end, string_content) or None if no string value found.
        col_start and col_end include the quotes.
        """
        line = self.lines[self.cursor_row]

        # Parse all strings on this line with their positions
        strings: list[tuple[int, int, str]] = []  # (start, end, content)
        i = 0
        while i < len(line):
            if line[i] == '"':
                start = i
                i += 1
                while i < len(line):
                    if line[i] == '"' and line[i - 1] != "\\":
                        raw = line[start + 1 : i]
                        try:
                            content = json.loads(f'"{raw}"')
                            strings.append((start, i + 1, content))
                        except json.JSONDecodeError:
                            pass
                        break
                    i += 1
            i += 1

        if not strings:
            return None

        # Find string values (strings that follow a ':')
        for start, end, content in strings:
            before = line[:start].rstrip()
            if before.endswith(":"):
                return (start, end, content)

        return None

    def _edit_embedded_json(self) -> None:
        """Handle :ej command to edit embedded JSON string."""
        result = self._find_string_at_cursor()
        if result is None:
            self.status_msg = "cursor not on a string value"
            return

        col_start, col_end, content = result

        # Try to parse as JSON
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            self.status_msg = "string is not valid JSON"
            return

        # Check if it's a list or dict
        if not isinstance(parsed, (list, dict)):
            self.status_msg = "string is not a list or dict"
            return

        # Format and send for editing
        formatted = json.dumps(parsed, indent=4, ensure_ascii=False)
        self.post_message(
            self.EmbeddedEditRequested(
                content=formatted,
                source_row=self.cursor_row,
                source_col_start=col_start,
                source_col_end=col_end,
            )
        )

    def update_embedded_string(
        self, row: int, col_start: int, col_end: int, new_content: str
    ) -> None:
        """Update a string value with new JSON content."""
        self._save_undo()
        # Escape the new content as a JSON string
        escaped = json.dumps(new_content, ensure_ascii=False)
        line = self.lines[row]
        self.lines[row] = line[:col_start] + escaped + line[col_end:]
        self.refresh()

    # -- JSONL helpers -----------------------------------------------------

    @staticmethod
    def _jsonl_to_pretty(content: str) -> str:
        """Convert JSONL (one-json-per-line) to pretty-printed blocks."""
        blocks: list[str] = []
        for line in content.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
                blocks.append(json.dumps(parsed, indent=4, ensure_ascii=False))
            except json.JSONDecodeError:
                blocks.append(stripped)
        return "\n\n".join(blocks)

    @staticmethod
    def _split_jsonl_blocks(content: str) -> list[str]:
        """Split pretty-printed content into blocks separated by blank lines."""
        blocks: list[str] = []
        current: list[str] = []
        for line in content.split("\n"):
            if line.strip():
                current.append(line)
            else:
                if current:
                    blocks.append("\n".join(current))
                    current = []
        if current:
            blocks.append("\n".join(current))
        return blocks

    @staticmethod
    def _pretty_to_jsonl(content: str) -> str:
        """Convert pretty-printed blocks back to JSONL (one-json-per-line)."""
        blocks = JsonEditor._split_jsonl_blocks(content)
        lines: list[str] = []
        for block in blocks:
            try:
                parsed = json.loads(block)
                lines.append(json.dumps(parsed, ensure_ascii=False))
            except json.JSONDecodeError:
                lines.append(" ".join(block.split()))
        return "\n".join(lines)

    # -- Movement helpers --------------------------------------------------

    def _current_indent(self) -> int:
        line = self.lines[self.cursor_row]
        return len(line) - len(line.lstrip()) if line.strip() else 0

    def _undo(self) -> None:
        if not self.undo_stack:
            self.status_msg = "nothing to undo"
            return
        # Save current state for redo
        self.redo_stack.append((self.lines[:], self.cursor_row, self.cursor_col))
        lines, row, col = self.undo_stack.pop()
        self.lines = lines
        self.cursor_row = row
        self.cursor_col = col
        self._visual_mode = ""
        self._folds.clear()
        self._collapsed_strings.clear()
        self._invalidate_caches()
        self.status_msg = "undone"

    def _redo(self) -> None:
        if not self.redo_stack:
            self.status_msg = "nothing to redo"
            return
        # Save current state for undo
        self.undo_stack.append((self.lines[:], self.cursor_row, self.cursor_col))
        lines, row, col = self.redo_stack.pop()
        self.lines = lines
        self.cursor_row = row
        self.cursor_col = col
        self._visual_mode = ""
        self._folds.clear()
        self._collapsed_strings.clear()
        self._invalidate_caches()
        self.status_msg = "redone"
