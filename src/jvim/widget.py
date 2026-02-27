"""Modal JSON editor widget."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum, auto

from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget

from jvim.action import (
    ClipboardMixin,
    ContentMixin,
    FoldMixin,
    NavigationMixin,
    RenderMixin,
    SubstituteMixin,
    UndoMixin,
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
    UndoMixin,
    ContentMixin,
    RenderMixin,
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
    class FileNavigateRequested(Message):
        action: str  # "next", "prev", "alternate"

    @dataclass
    class EmbeddedEditRequested(Message):
        content: str  # Parsed JSON content to edit
        source_row: int  # Row of the string value
        source_col_start: int  # Column where string starts (including quote)
        source_col_end: int  # Column where string ends (including quote)

    @dataclass
    class EmbeddedEditSave(Message):
        content: str  # Updated JSON content

    @dataclass
    class IgnorePathRequested(Message):
        path: str

    @dataclass
    class UnignorePathRequested(Message):
        path: str = ""
        clear_all: bool = False

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
        self._count_buf: str = ""
        self.status_msg: str = ""
        self.undo_stack: deque[tuple[list[str], int, int]] = deque(maxlen=200)
        self.redo_stack: deque[tuple[list[str], int, int]] = deque(maxlen=200)
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
        self._cached_line_count: int = len(self.lines)
        self._jsonl_records_cache: list[int] | None = None
        self._char_width_cache: dict[str, int] = {}
        # Fold state
        self._folds: dict[int, int] = {}  # {fold_header_line: fold_end_line}
        self._folded_lines: set[int] = set()  # _is_line_folded() O(1) 캐시
        self._folded_lines_dirty: bool = False
        self._folded_lines_folds_len: int = 0
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
        # _ensure_cursor_visible 조기 종료 캐시
        self._last_ecv_state: tuple | None = None
        # 거터 설정
        self._show_line_number: bool = True  # False면 LN 컬럼 숨김
        # 초기 로드 시 긴 문자열 자동 접기
        for i in range(len(self.lines)):
            if self._find_long_string_at(i):
                self._collapsed_strings.add(i)

    # -- Helpers -----------------------------------------------------------

    def _invalidate_caches(self) -> None:
        """Invalidate render caches when content changes."""
        self._cache_dirty = True
        self._last_ecv_state = None

    def _check_readonly(self) -> bool:
        """Check if read-only and set status. Returns True if read-only."""
        if self.read_only:
            self.status_msg = "[readonly]"
        return self.read_only

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
        if self._has_fold_header(row):
            line_len_here = len(self.lines[row])
            max_here = max(0, line_len_here - 1) if line_len_here else 0
            if self.cursor_col > max_here:
                self._open_fold(row)
        line_len = len(self.lines[self.cursor_row])
        if self._mode == EditorMode.NORMAL:
            max_col = max(0, line_len - 1) if line_len else 0
        else:
            max_col = line_len
        self.cursor_col = max(0, min(self.cursor_col, max_col))

    def _ensure_cursor_visible(self, avail: int) -> None:
        state = (
            self.cursor_row,
            self.cursor_col,
            self._scroll_top,
            avail,
            len(self.lines),
        )
        if state == self._last_ecv_state:
            return
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
        self._last_ecv_state = (
            self.cursor_row,
            self.cursor_col,
            self._scroll_top,
            avail,
            len(self.lines),
        )

    def _scroll_cursor_to_top(self) -> None:
        """Position viewport so cursor is at the top of the screen."""
        self._scroll_top = self.cursor_row

    def _scroll_cursor_to_center(self, ratio: float = 0.33) -> None:
        """Position viewport so cursor is at given ratio from top (default 1/3)."""
        vh = self._visible_height()
        offset = int(vh * ratio)
        self._scroll_top = max(0, self.cursor_row - offset)

    # =====================================================================
    # Key handling
    # =====================================================================

    def on_paste(self, event: events.Paste) -> None:
        event.prevent_default()
        event.stop()
        text = event.text.replace("\r\n", "\n").replace("\r", "\n")
        if self._mode == EditorMode.SEARCH:
            # 개행 제거 후 검색 버퍼에 추가
            self._search_buffer += text.replace("\n", "")
            self._search_history_idx = -1
        elif self._mode == EditorMode.COMMAND:
            self.command_buffer += text.replace("\n", "")
            self._command_history_idx = -1
            self._refresh_completions()
        elif self._mode == EditorMode.INSERT and not self.read_only:
            self._save_undo()
            paste_lines = text.split("\n")
            line = self.lines[self.cursor_row]
            if len(paste_lines) == 1:
                self.lines[self.cursor_row] = (
                    line[: self.cursor_col] + paste_lines[0] + line[self.cursor_col :]
                )
                self.cursor_col += len(paste_lines[0])
            else:
                after = line[self.cursor_col :]
                self.lines[self.cursor_row] = line[: self.cursor_col] + paste_lines[0]
                for j, pl in enumerate(paste_lines[1:], 1):
                    self.lines.insert(self.cursor_row + j, pl)
                self._adjust_line_indices(self.cursor_row + 1, len(paste_lines) - 1)
                self.cursor_row += len(paste_lines) - 1
                self.cursor_col = len(paste_lines[-1])
                self.lines[self.cursor_row] += after
        self._clamp_cursor()
        self.refresh()

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

    # -- Movement helpers --------------------------------------------------

    def _current_indent(self) -> int:
        line = self.lines[self.cursor_row]
        return len(line) - len(line.lstrip()) if line.strip() else 0
