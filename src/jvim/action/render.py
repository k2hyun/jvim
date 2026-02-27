"""Rendering mixin for JsonEditor."""

from __future__ import annotations

import re
import unicodedata

from rich.text import Text


class RenderMixin:
    """Rendering, syntax highlighting, and display utility methods for JsonEditor."""

    # -- Syntax colouring constants ----------------------------------------

    _BRACKET = frozenset("{}[]")
    _PUNCT = frozenset(":,")
    _DIGIT = frozenset("0123456789.-+eE")
    _KEYWORDS = ("true", "false", "null")
    _KEYWORD_RE = re.compile(r"true|false|null")
    # 지연 초기화 (EditorMode 순환 import 방지)
    _MODE_STYLE: dict | None = None

    # -- Character width / wrapping ----------------------------------------

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

    # -- Gutter / line records ---------------------------------------------

    def _gutter_widths(self) -> tuple[int, int, int]:
        """Return ``(ln_width, rec_width, prefix_width)``.

        *rec_width* is 0 when not in JSONL mode.
        *ln_width* is always computed but excluded from *prefix_width* when
        ``_show_line_number`` is False.
        """
        ln_width = max(3, len(str(len(self.lines))))
        if not self.jsonl:
            if self._show_line_number:
                return ln_width, 0, ln_width + 1
            return ln_width, 0, 0
        # 캐시가 있으면 max() 활용, 없으면 라인 순회 fallback
        cache = self._jsonl_records_cache
        if cache:
            rec_count = max(cache)
        else:
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
        if self._show_line_number:
            return ln_width, rec_width, rec_width + 1 + ln_width + 1
        return ln_width, rec_width, rec_width + 1

    def _render_gutter(
        self,
        line_idx,
        si,
        rows_used,
        jsonl_records,
        prefix_w,
        ln_width,
        rec_width,
        result,
        result_append,
        gutter_pad,
    ):
        """거터 렌더링 (라인 번호 + JSONL 레코드 번호)."""
        if si == 0 or rows_used == 0:
            if self._show_line_number:
                result_append(result, f"{line_idx + 1:>{ln_width}} ", style="dim cyan")
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

    # -- Line background hook ----------------------------------------------

    def _line_background(self, line_idx: int) -> str:
        """서브클래스에서 라인별 배경 스타일을 지정하기 위한 훅."""
        return ""

    # -- Syntax highlighting -----------------------------------------------

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

    # -- Main render -------------------------------------------------------

    def render(self) -> Text:
        width = self.content_region.width
        height = self.content_region.height
        if height < 3 or width < 10:
            return Text("(too small)")

        # _MODE_STYLE 지연 초기화 (EditorMode 순환 import 방지)
        if RenderMixin._MODE_STYLE is None:
            EditorMode = self._mode.__class__
            RenderMixin._MODE_STYLE = {
                EditorMode.NORMAL: "bold white on dark_green",
                EditorMode.INSERT: "bold white on dark_blue",
                EditorMode.COMMAND: "bold white on dark_red",
                EditorMode.SEARCH: "bold white on dark_magenta",
            }

        # Flush caches when content changed
        if self._cache_dirty:
            new_count = len(self.lines)
            if new_count != self._cached_line_count:
                # 행 수 변경 → 캐시 키(행 번호) 틀어짐, 전체 무효화
                self._style_cache.clear()
            else:
                # 행 수 동일 → 변경된 행만 무효화
                sc = self._style_cache
                lines = self.lines
                to_del = [
                    k
                    for k, v in sc.items()
                    if k < new_count and len(v) != len(lines[k])
                ]
                for k in to_del:
                    del sc[k]
            self._cached_line_count = new_count
            self._jsonl_records_cache = None
            self._cache_dirty = False

        content_height = height - 2
        # JSONL 캐시를 _gutter_widths() 전에 구축 (rec_count 계산에 활용)
        if self.jsonl:
            if self._jsonl_records_cache is None:
                self._jsonl_records_cache = self._jsonl_line_records()
            jsonl_records = self._jsonl_records_cache
        else:
            jsonl_records = None
        ln_width, rec_width, prefix_w = self._gutter_widths()
        avail = max(1, width - prefix_w)

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
                    if self._show_line_number:
                        header = (
                            f"{rec_start_line + 1:>{ln_width}} {rec_num:>{rec_width}} ↓"
                        )
                    else:
                        header = f"{rec_num:>{rec_width}} ↓"
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
                self._render_gutter(
                    line_idx,
                    si,
                    rows_used,
                    jsonl_records,
                    prefix_w,
                    ln_width,
                    rec_width,
                    result,
                    result_append,
                    gutter_pad,
                )
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
        EditorMode = mode.__class__
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

            count_buf = self._count_buf
            pending = self.pending
            prefix_str = count_buf + pending
            if prefix_str:
                result_append(result, f"  {prefix_str}", style="bold yellow")

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
