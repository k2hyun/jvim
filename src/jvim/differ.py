"""Side-by-side JSON diff viewer."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Header, Static

from rich.text import Text

from .action.jsonpath import jsonpath_find
from .diff import DiffHunk, DiffTag, compute_json_diff
from .editor import _detect_jsonl
from .widget import JsonEditor

_KEY_RE = re.compile(r'^"([^"]+)"\s*:')


def _is_binary(path: Path) -> bool:
    """바이너리 파일 여부 확인 (null byte 또는 UTF-8 디코딩 실패)."""
    try:
        content = path.read_text(encoding="utf-8")
        return "\x00" in content
    except (UnicodeDecodeError, OSError):
        return True


def _collect_file_pairs(left_dir: str, right_dir: str) -> list[tuple[str, str]]:
    """두 디렉토리를 재귀 탐색하여 차이가 있는 파일 쌍 반환.

    - 바이너리 파일 제외
    - 동일한 파일 건너뛰기
    - 한쪽에만 있는 파일은 없는 쪽을 빈 문자열로 표현
    - 상대 경로 기준 알파벳 정렬
    """
    left_base, right_base = Path(left_dir), Path(right_dir)
    left_files = {f.relative_to(left_base) for f in left_base.rglob("*") if f.is_file()}
    right_files = {
        f.relative_to(right_base) for f in right_base.rglob("*") if f.is_file()
    }
    all_paths = sorted(left_files | right_files)

    pairs: list[tuple[str, str]] = []
    for rel in all_paths:
        lp = left_base / rel
        rp = right_base / rel
        l_exists = rel in left_files
        r_exists = rel in right_files

        # 바이너리 파일 제외
        if l_exists and _is_binary(lp):
            continue
        if r_exists and _is_binary(rp):
            continue

        if l_exists and r_exists:
            # 내용이 동일하면 건너뛰기
            try:
                if lp.read_text(encoding="utf-8") == rp.read_text(encoding="utf-8"):
                    continue
            except OSError:
                continue
            pairs.append((str(lp), str(rp)))
        elif l_exists:
            pairs.append((str(lp), ""))
        else:
            pairs.append(("", str(rp)))

    return pairs


class SyncJsonEditor(JsonEditor):
    """스크롤 동기화를 지원하는 JsonEditor."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._sync_target: SyncJsonEditor | None = None

    def _ensure_cursor_visible(self, avail: int) -> None:
        if not self.has_focus and self._sync_target is not None:
            return
        super()._ensure_cursor_visible(avail)

    def _sync_folds_to_target(self) -> None:
        """fold 상태를 sync target에 복사."""
        if self._sync_target is not None:
            self._sync_target._folds = dict(self._folds)
            self._sync_target._folded_lines_dirty = True
            self._sync_target._collapsed_strings = set(self._collapsed_strings)
            self._sync_target.refresh()

    def _toggle_fold(self, line_idx: int) -> None:
        super()._toggle_fold(line_idx)
        self._sync_folds_to_target()

    def _open_fold(self, line_idx: int) -> None:
        super()._open_fold(line_idx)
        self._sync_folds_to_target()

    def _close_fold(self, line_idx: int) -> None:
        super()._close_fold(line_idx)
        self._sync_folds_to_target()

    def _fold_all(self) -> None:
        super()._fold_all()
        self._sync_folds_to_target()

    def _unfold_all(self) -> None:
        super()._unfold_all()
        self._sync_folds_to_target()

    def render(self) -> Text:
        if not self.has_focus and self._sync_target is not None:
            self._scroll_top = self._sync_target._scroll_top
        result = super().render()
        if self.has_focus and self._sync_target is not None:
            if self._sync_target._scroll_top != self._scroll_top:
                self._sync_target._scroll_top = self._scroll_top
                self._sync_target.refresh()
        return result


class DiffEditor(SyncJsonEditor):
    """SyncJsonEditor 서브클래스: diff 배경 하이라이팅과 hunk 네비게이션."""

    # diff 태그별 배경색
    _DIFF_BG = {
        DiffTag.DELETE: "on #72261a",
        DiffTag.INSERT: "on #1e5c34",
        DiffTag.REPLACE: "#1e1e1e on #6a6a6a",
    }
    _FILLER_BG = "on #2a2a2a"

    def __init__(
        self,
        initial_content: str = "",
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(
            initial_content, read_only=True, name=name, id=id, classes=classes
        )
        self._line_tags: list[DiffTag] = []
        self._filler_rows: set[int] = set()
        self._diff_hunks: list[DiffHunk] = []
        self._current_hunk: int = -1
        self._ignore_paths: list[str] = []
        self._suppressed_lines: set[int] = set()
        self._physical_line_map: list[int] = []
        self._show_logical_line: bool = True
        self._physical_width: int = 3
        self._logical_width: int = 3

    def _get_parseable_content(self) -> str:
        """filler 행을 제외한 JSON 파싱용 콘텐츠."""
        return "\n".join(
            line for i, line in enumerate(self.lines) if i not in self._filler_rows
        )

    def _compute_block_start_lines(self) -> dict[int, int]:
        """filler 행을 건너뛰는 JSONL 블록 시작 라인 계산."""
        result: dict[int, int] = {}
        block_idx = 0
        in_block = False
        for i, line in enumerate(self.lines):
            if i in self._filler_rows:
                continue
            if line.strip():
                if not in_block:
                    result[block_idx] = i
                    block_idx += 1
                    in_block = True
            else:
                in_block = False
        return result

    def set_diff_data(
        self,
        lines: list[str],
        tags: list[DiffTag],
        filler_rows: set[int],
        hunks: list[DiffHunk],
    ) -> None:
        """Diff 결과를 설정."""
        self.lines = lines if lines else [""]
        self._line_tags = tags
        self._filler_rows = filler_rows
        self._diff_hunks = hunks
        self._current_hunk = -1
        self.cursor_row = 0
        self.cursor_col = 0
        self._scroll_top = 0
        self._folds.clear()
        self._folded_lines.clear()
        self._folded_lines_dirty = False
        self._folded_lines_folds_len = 0
        # physical line map 구축 (filler=0, 나머지는 1-based)
        physical = 0
        self._physical_line_map = []
        for i in range(len(self.lines)):
            if i in self._filler_rows:
                self._physical_line_map.append(0)
            else:
                physical += 1
                self._physical_line_map.append(physical)
        self._invalidate_caches()
        self.refresh()

    def _line_background(self, line_idx: int) -> str:
        if line_idx in self._suppressed_lines:
            return ""
        if line_idx < len(self._line_tags):
            tag = self._line_tags[line_idx]
            if line_idx in self._filler_rows:
                return self._FILLER_BG
            return self._DIFF_BG.get(tag, "")
        return ""

    def _gutter_widths(self) -> tuple[int, int, int]:
        max_physical = max(1, len(self.lines) - len(self._filler_rows))
        self._physical_width = max(3, len(str(max_physical)))
        if self._show_logical_line:
            self._logical_width = max(3, len(str(len(self.lines))))
            prefix_w = self._logical_width + 1 + self._physical_width + 1
        else:
            self._logical_width = 0
            prefix_w = self._physical_width + 1
        return self._physical_width, 0, prefix_w

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
        if si == 0 or rows_used == 0:
            is_filler = line_idx in self._filler_rows
            if not self._show_logical_line and is_filler:
                # 오른쪽 에디터 filler: 거터 전체 공백
                result_append(result, " " * prefix_w)
                return
            if self._show_logical_line:
                # 왼쪽: logical line number (항상 표시)
                result_append(
                    result, f"{line_idx + 1:>{self._logical_width}} ", style="dim cyan"
                )
            # physical line number (filler면 공백)
            if is_filler:
                result_append(result, " " * (self._physical_width + 1))
            else:
                phy = (
                    self._physical_line_map[line_idx]
                    if line_idx < len(self._physical_line_map)
                    else 0
                )
                if phy:
                    result_append(
                        result, f"{phy:>{self._physical_width}} ", style="dim cyan"
                    )
                else:
                    result_append(result, " " * (self._physical_width + 1))
        else:
            result_append(result, gutter_pad)

    @staticmethod
    def _build_line_paths(lines: list[str]) -> list[str]:
        """indent 기반 스택으로 각 라인의 JSONPath 추적."""
        result: list[str] = []
        # 스택: (key_or_index, is_array) 쌍
        stack: list[tuple[str, bool]] = []
        # 각 depth의 배열 인덱스 카운터
        array_indices: dict[int, int] = {}
        key_re = _KEY_RE

        for line in lines:
            stripped = line.lstrip()
            if not stripped:
                result.append("")
                continue

            indent = len(line) - len(stripped)
            depth = indent // 4

            # 스택을 현재 depth까지 축소
            while len(stack) > depth:
                stack.pop()

            # 키 추출: "key": ... 패턴
            key_match = key_re.match(stripped)

            if key_match:
                key = key_match.group(1)
                # 현재 depth에 스택 항목이 있으면 교체, 없으면 추가
                if len(stack) > depth:
                    stack[depth] = (key, False)
                else:
                    while len(stack) < depth:
                        stack.append(("", False))
                    stack.append((key, False))

                # 경로 생성
                path = "$"
                for seg, is_arr in stack:
                    if isinstance(seg, str) and seg.startswith("["):
                        path += seg
                    elif seg:
                        path += f".{seg}"
                result.append(path)

                # 값이 { 또는 [로 시작하는 컨테이너인지 확인
                after_colon = stripped[key_match.end() :].strip()
                if after_colon.startswith("{"):
                    # 다음 depth에서 오브젝트
                    pass
                elif after_colon.startswith("["):
                    # 배열 시작 — 인덱스 카운터 초기화
                    array_indices[depth + 1] = 0

            elif stripped.startswith("{"):
                # 배열 내부의 오브젝트 시작
                parent_depth = depth - 1
                if parent_depth >= 0 and parent_depth + 1 in array_indices:
                    idx = array_indices[parent_depth + 1]
                    idx_key = f"[{idx}]"
                    if len(stack) > depth:
                        stack[depth] = (idx_key, False)
                    else:
                        while len(stack) < depth:
                            stack.append(("", False))
                        stack.append((idx_key, False))

                path = "$"
                for seg, is_arr in stack:
                    if isinstance(seg, str) and seg.startswith("["):
                        path += seg
                    elif seg:
                        path += f".{seg}"
                result.append(path)

            elif stripped.startswith("}"):
                # 오브젝트 종료
                path = "$"
                for seg, is_arr in stack:
                    if isinstance(seg, str) and seg.startswith("["):
                        path += seg
                    elif seg:
                        path += f".{seg}"
                result.append(path)

                # 배열 내부 오브젝트 종료 시 인덱스 증가
                if depth in array_indices:
                    pass
                parent_depth = depth - 1
                if parent_depth >= 0 and depth in array_indices:
                    array_indices[depth] += 1

            elif stripped.startswith("]"):
                # 배열 종료
                path = "$"
                for seg, is_arr in stack:
                    if isinstance(seg, str) and seg.startswith("["):
                        path += seg
                    elif seg:
                        path += f".{seg}"
                result.append(path)
                # 인덱스 카운터 정리
                array_indices.pop(depth + 1, None)

            elif stripped.startswith("["):
                # 최상위 배열 시작
                array_indices[depth + 1] = 0
                result.append("$")

            else:
                # 배열 내 프리미티브 값 등
                path = "$"
                for seg, is_arr in stack:
                    if isinstance(seg, str) and seg.startswith("["):
                        path += seg
                    elif seg:
                        path += f".{seg}"
                result.append(path)

        return result

    def _update_suppressed_lines(self) -> None:
        """ignore 패턴과 매칭하여 _suppressed_lines 갱신."""
        self._suppressed_lines.clear()
        if not self._ignore_paths:
            return

        line_paths = self._build_line_paths(self.lines)

        # 동적 패턴(.. 또는 [*]) 분리 — jsonpath_find로 해석 필요
        dynamic_patterns: list[str] = []
        prefix_patterns: list[str] = []
        for pat in self._ignore_paths:
            if ".." in pat or "[*]" in pat:
                dynamic_patterns.append(pat)
            else:
                prefix_patterns.append(pat)

        # 동적 패턴: JSON 파싱 후 jsonpath_find로 매칭 경로 수집
        resolved_prefixes: set[str] = set()
        if dynamic_patterns:
            content = self._get_parseable_content()
            data_list: list[object] = []
            try:
                data_list.append(json.loads(content))
            except json.JSONDecodeError as e:
                if "Extra data" in e.msg:
                    # JSONL: 각 블록을 개별 파싱
                    for block in self._split_jsonl_blocks(content):
                        try:
                            data_list.append(json.loads(block))
                        except json.JSONDecodeError:
                            pass
            for data in data_list:
                for pat in dynamic_patterns:
                    try:
                        matched = jsonpath_find(data, pat)
                    except ValueError:
                        continue
                    for path_parts in matched:
                        p = "$"
                        for seg in path_parts:
                            if isinstance(seg, int):
                                p += f"[{seg}]"
                            else:
                                p += f".{seg}"
                        resolved_prefixes.add(p)

        for i, lp in enumerate(line_paths):
            if not lp:
                continue
            # filler 라인은 억제하지 않음
            if i in self._filler_rows:
                continue
            # prefix 패턴: 정확 일치 또는 접두사 매칭
            for pat in prefix_patterns:
                if lp == pat or lp.startswith(pat + ".") or lp.startswith(pat + "["):
                    self._suppressed_lines.add(i)
                    break
            else:
                # 동적 패턴 매칭
                for rp in resolved_prefixes:
                    if lp == rp or lp.startswith(rp + ".") or lp.startswith(rp + "["):
                        self._suppressed_lines.add(i)
                        break

        self.refresh()

    def _update_hunk_status(self) -> None:
        total = len(self._diff_hunks)
        if total == 0:
            self.status_msg = "Files are identical"
        elif self._current_hunk >= 0:
            self.status_msg = f"Hunk {self._current_hunk + 1}/{total}"
        else:
            self.status_msg = f"{total} hunks"

    def _goto_next_hunk(self) -> None:
        if not self._diff_hunks:
            self.status_msg = "No diffs"
            return
        self._current_hunk += 1
        if self._current_hunk >= len(self._diff_hunks):
            self._current_hunk = 0
        hunk = self._diff_hunks[self._current_hunk]
        self.cursor_row = hunk.left_start
        self.cursor_col = 0
        self._scroll_cursor_to_center()
        self._update_hunk_status()

    def _goto_prev_hunk(self) -> None:
        if not self._diff_hunks:
            self.status_msg = "No diffs"
            return
        self._current_hunk -= 1
        if self._current_hunk < 0:
            self._current_hunk = len(self._diff_hunks) - 1
        hunk = self._diff_hunks[self._current_hunk]
        self.cursor_row = hunk.left_start
        self.cursor_col = 0
        self._scroll_cursor_to_center()
        self._update_hunk_status()

    def _handle_normal(self, event) -> None:
        char = event.character or ""
        if self.pending in ("]", "["):
            self._handle_pending(char, event.key)
            return
        if char in ("]", "["):
            self.pending = char
            return
        super()._handle_normal(event)

    def _handle_pending(self, char: str, key: str) -> None:
        combo = self.pending + char
        if combo == "]c":
            self.pending = ""
            self._goto_next_hunk()
            return
        if combo == "[c":
            self.pending = ""
            self._goto_prev_hunk()
            return
        super()._handle_pending(char, key)


class JsonDiffApp(App):
    """Side-by-side JSON diff viewer."""

    CSS_PATH = "styles/differ.tcss"
    TITLE = "JSON Diff"
    BINDINGS = []
    ENABLE_COMMAND_PALETTE = False

    def __init__(
        self,
        left_path: str,
        right_path: str,
        normalize: bool = True,
        jsonl: bool | None = False,
        file_pairs: list[tuple[str, str]] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.left_path = left_path
        self.right_path = right_path
        self.normalize = normalize
        self.jsonl = jsonl
        self.file_pairs: list[tuple[str, str]] = file_pairs or []
        self.pair_index: int = 0
        self._left_ej_stack: list[str] = []
        self._right_ej_stack: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="diff-container"):
            with Vertical(id="left-panel"):
                yield Static("", id="left-title")
                yield DiffEditor("", id="left-editor")
                with Vertical(id="left-ej-panel"):
                    with Horizontal(id="left-ej-header"):
                        yield Static("[b]Embedded JSON[/b]", id="left-ej-title")
                        yield Button("\u2715", id="left-ej-close", variant="error")
                    yield DiffEditor("", id="left-ej-editor")
            with Vertical(id="right-panel"):
                yield Static("", id="right-title")
                yield DiffEditor("", id="right-editor")
                with Vertical(id="right-ej-panel"):
                    with Horizontal(id="right-ej-header"):
                        yield Static("[b]Embedded JSON[/b]", id="right-ej-title")
                        yield Button("\u2715", id="right-ej-close", variant="error")
                    yield DiffEditor("", id="right-ej-editor")

    @staticmethod
    def _truncate_path(path: str, width: int) -> str:
        """패널 폭에 맞게 경로를 왼쪽부터 잘라서 파일명 우선 표시."""
        # padding 좌우 1칸씩 제외
        available = width - 2
        if available <= 0 or len(path) <= available:
            return path
        return "\u2026" + path[-(available - 1) :]

    def _update_titles(self) -> None:
        """패널 폭에 맞게 타이틀 경로 업데이트."""
        indicator = ""
        if len(self.file_pairs) >= 2:
            indicator = f" [{self.pair_index + 1}/{len(self.file_pairs)}]"
        for side, path in (("left", self.left_path), ("right", self.right_path)):
            display = path if path else "(empty)"
            title = self.query_one(f"#{side}-title", Static)
            w = title.size.width
            # indicator 길이를 고려하여 경로 잘라내기
            avail = w - len(indicator) if w > 0 else 0
            truncated = self._truncate_path(display, avail) if avail > 0 else display
            title.update(f"[b]{truncated}{indicator}[/b]")

    @staticmethod
    def _unfold_diff_regions(editor: DiffEditor) -> None:
        """diff가 있는 라인을 포함하는 fold/collapsed string을 unfold."""
        to_remove = []
        for start, end in editor._folds.items():
            for i in range(start, end + 1):
                if i < len(editor._line_tags) and editor._line_tags[i] != DiffTag.EQUAL:
                    to_remove.append(start)
                    break
        for s in to_remove:
            del editor._folds[s]
        if to_remove:
            editor._folded_lines_dirty = True
        # diff가 있는 collapsed string도 펼기
        to_expand = [
            i
            for i in editor._collapsed_strings
            if i < len(editor._line_tags) and editor._line_tags[i] != DiffTag.EQUAL
        ]
        for i in to_expand:
            editor._collapsed_strings.discard(i)

    def on_resize(self) -> None:
        self._update_titles()

    @staticmethod
    def _detect_jsonl_for_pair(
        left_path: str, right_path: str, left_content: str, right_content: str
    ) -> bool:
        """파일 쌍의 JSONL 여부를 확장자 + 내용 기반으로 판별."""
        for p in (left_path, right_path):
            if p and p.lower().endswith(".jsonl"):
                return True
        for content in (left_content, right_content):
            if content and _detect_jsonl(content):
                return True
        return False

    def _load_pair(self, index: int) -> None:
        """파일 쌍을 로드하여 diff 계산 + 에디터 갱신."""
        if self.file_pairs:
            left_path, right_path = self.file_pairs[index]
            self.pair_index = index
            self.left_path = left_path
            self.right_path = right_path

        left_content = (
            Path(self.left_path).read_text(encoding="utf-8") if self.left_path else ""
        )
        right_content = (
            Path(self.right_path).read_text(encoding="utf-8") if self.right_path else ""
        )

        # 파일 쌍별 JSONL 자동 감지 (명시적 --jsonl 플래그가 없는 경우)
        jsonl = self.jsonl
        if jsonl is None:
            jsonl = self._detect_jsonl_for_pair(
                self.left_path, self.right_path, left_content, right_content
            )

        diff_result = compute_json_diff(
            left_content,
            right_content,
            normalize=self.normalize,
            jsonl=jsonl,
        )

        left_editor = self.query_one("#left-editor", DiffEditor)
        right_editor = self.query_one("#right-editor", DiffEditor)
        left_editor._show_logical_line = True
        right_editor._show_logical_line = False

        # filler 행 계산: 한쪽만 빈 정렬 패딩 (양쪽 모두 빈 JSONL separator 제외)
        left_fillers = {
            i
            for i, (line, tag) in enumerate(
                zip(diff_result.left_lines, diff_result.left_line_tags)
            )
            if not line and tag != DiffTag.EQUAL and diff_result.right_lines[i]
        }
        right_fillers = {
            i
            for i, (line, tag) in enumerate(
                zip(diff_result.right_lines, diff_result.right_line_tags)
            )
            if not line and tag != DiffTag.EQUAL and diff_result.left_lines[i]
        }

        left_editor.set_diff_data(
            diff_result.left_lines,
            diff_result.left_line_tags,
            left_fillers,
            diff_result.hunks,
        )
        right_editor.set_diff_data(
            diff_result.right_lines,
            diff_result.right_line_tags,
            right_fillers,
            diff_result.hunks,
        )

        # EJ 스택 초기화
        self._left_ej_stack.clear()
        self._right_ej_stack.clear()
        self.query_one("#left-ej-panel").remove_class("visible")
        self.query_one("#right-ej-panel").remove_class("visible")

        # 모든 depth fold 후 diff 있는 부분만 unfold
        left_editor._fold_all_nested()
        self._unfold_diff_regions(left_editor)
        right_editor._folds = dict(left_editor._folds)
        right_editor._folded_lines_dirty = True
        right_editor._collapsed_strings = set(left_editor._collapsed_strings)

        left_editor._update_hunk_status()
        right_editor._update_hunk_status()
        self._update_titles()

    def on_mount(self) -> None:
        self._load_pair(0)

        # 렌더 타임 스크롤 동기화 설정
        left_editor = self.query_one("#left-editor", DiffEditor)
        right_editor = self.query_one("#right-editor", DiffEditor)
        left_editor._sync_target = right_editor
        right_editor._sync_target = left_editor

        # EJ 패널 스크롤 동기화
        left_ej = self.query_one("#left-ej-editor", DiffEditor)
        right_ej = self.query_one("#right-ej-editor", DiffEditor)
        left_ej._sync_target = right_ej
        right_ej._sync_target = left_ej
        left_ej._show_logical_line = True
        right_ej._show_logical_line = False

        left_editor.focus()

    def on_json_editor_quit(self, event: JsonEditor.Quit) -> None:
        focused = self.focused
        fid = focused.id if focused else ""
        if fid in ("left-ej-editor", "right-ej-editor"):
            side = "left" if fid == "left-ej-editor" else "right"
            self._close_ej_panel(side)
        else:
            self.exit()

    def on_json_editor_force_quit(self, event: JsonEditor.ForceQuit) -> None:
        focused = self.focused
        fid = focused.id if focused else ""
        if fid in ("left-ej-editor", "right-ej-editor"):
            side = "left" if fid == "left-ej-editor" else "right"
            self._close_ej_panel(side)
        else:
            self.exit()

    def on_json_editor_ignore_path_requested(
        self, event: JsonEditor.IgnorePathRequested
    ) -> None:
        """양쪽 에디터에 ignore 패턴 추가 + 갱신."""
        for eid in ("left-editor", "right-editor"):
            editor = self.query_one(f"#{eid}", DiffEditor)
            if event.path not in editor._ignore_paths:
                editor._ignore_paths.append(event.path)
            editor._update_suppressed_lines()

    def on_json_editor_unignore_path_requested(
        self, event: JsonEditor.UnignorePathRequested
    ) -> None:
        """패턴 제거 + 갱신."""
        for eid in ("left-editor", "right-editor"):
            editor = self.query_one(f"#{eid}", DiffEditor)
            if event.clear_all:
                editor._ignore_paths.clear()
            elif event.path in editor._ignore_paths:
                editor._ignore_paths.remove(event.path)
            editor._update_suppressed_lines()

    def on_json_editor_file_navigate_requested(
        self, event: JsonEditor.FileNavigateRequested
    ) -> None:
        """디렉토리 비교 시 :n / :N 으로 파일 쌍 전환."""
        if not self.file_pairs or len(self.file_pairs) < 2:
            left_editor = self.query_one("#left-editor", DiffEditor)
            left_editor.status_msg = "No other files"
            return
        if event.action == "next":
            new_idx = self.pair_index + 1
            if new_idx >= len(self.file_pairs):
                new_idx = 0
        elif event.action == "prev":
            new_idx = self.pair_index - 1
            if new_idx < 0:
                new_idx = len(self.file_pairs) - 1
        else:
            return
        self._load_pair(new_idx)
        self.query_one("#left-editor", DiffEditor).focus()

    def on_json_editor_embedded_edit_requested(
        self, event: JsonEditor.EmbeddedEditRequested
    ) -> None:
        focused = self.focused
        if focused is None:
            return
        fid = focused.id
        if fid in ("left-editor", "left-ej-editor"):
            side = "left"
            other_side = "right"
        elif fid in ("right-editor", "right-ej-editor"):
            side = "right"
            other_side = "left"
        else:
            return

        # EJ 에디터에서 중첩 호출
        if fid == f"{side}-ej-editor":
            ej_stack = self._left_ej_stack if side == "left" else self._right_ej_stack
            other_ej_stack = (
                self._right_ej_stack if side == "left" else self._left_ej_stack
            )
            ej_editor = self.query_one(f"#{side}-ej-editor", DiffEditor)
            other_ej_editor = self.query_one(f"#{other_side}-ej-editor", DiffEditor)

            this_content = event.content
            other_content = self._find_ej_content_in(
                other_ej_editor,
                event.source_row,
            )

            ej_stack.append(ej_editor.get_content())
            if other_content is not None:
                other_ej_stack.append(other_ej_editor.get_content())
                left_content = this_content if side == "left" else other_content
                right_content = other_content if side == "left" else this_content
                self._open_ej_with_diff(left_content, right_content)
                self._update_ej_title(other_side)
            else:
                lines = this_content.split("\n") if this_content else [""]
                tags = [DiffTag.EQUAL] * len(lines)
                ej_editor.set_diff_data(lines, tags, set(), [])

            self._update_ej_title(side)
            ej_editor.focus()
            return

        # diff 에디터에서 ej 호출 → 양쪽 diff 표시
        this_content = event.content
        other_editor = self.query_one(f"#{other_side}-editor", DiffEditor)
        other_content = self._find_ej_content_in(other_editor, event.source_row)

        if other_content is not None:
            left_content = this_content if side == "left" else other_content
            right_content = other_content if side == "left" else this_content
            self._open_ej_with_diff(left_content, right_content)
        else:
            ej_editor = self.query_one(f"#{side}-ej-editor", DiffEditor)
            ej_panel = self.query_one(f"#{side}-ej-panel")
            lines = this_content.split("\n") if this_content else [""]
            tags = [DiffTag.EQUAL] * len(lines)
            ej_editor.set_diff_data(lines, tags, set(), [])
            self._update_ej_title(side)
            ej_panel.add_class("visible")

        self.query_one(f"#{side}-ej-editor", DiffEditor).focus()

    def _find_ej_content_in(
        self,
        editor: DiffEditor,
        source_row: int,
    ) -> str | None:
        """에디터의 지정 행에서 임베디드 JSON을 찾아 포맷팅된 문자열 반환."""
        if source_row >= len(editor.lines):
            return None

        saved_row = editor.cursor_row
        editor.cursor_row = source_row
        result = editor._find_string_at_cursor()
        editor.cursor_row = saved_row

        if result is None:
            return None

        _, _, content = result
        try:
            parsed = json.loads(content)
            if not isinstance(parsed, (list, dict)):
                return None
            return json.dumps(parsed, indent=4, ensure_ascii=False)
        except (json.JSONDecodeError, ValueError):
            return None

    def _open_ej_with_diff(self, left_content: str, right_content: str) -> None:
        """양쪽 EJ 패널에 diff 결과를 표시."""
        diff_result = compute_json_diff(
            left_content,
            right_content,
            normalize=False,
        )

        left_ej = self.query_one("#left-ej-editor", DiffEditor)
        right_ej = self.query_one("#right-ej-editor", DiffEditor)

        left_fillers = {
            i
            for i, (line, tag) in enumerate(
                zip(diff_result.left_lines, diff_result.left_line_tags)
            )
            if not line and tag != DiffTag.EQUAL
        }
        right_fillers = {
            i
            for i, (line, tag) in enumerate(
                zip(diff_result.right_lines, diff_result.right_line_tags)
            )
            if not line and tag != DiffTag.EQUAL
        }

        left_ej.set_diff_data(
            diff_result.left_lines,
            diff_result.left_line_tags,
            left_fillers,
            diff_result.hunks,
        )
        right_ej.set_diff_data(
            diff_result.right_lines,
            diff_result.right_line_tags,
            right_fillers,
            diff_result.hunks,
        )

        left_ej._update_hunk_status()
        right_ej._update_hunk_status()

        self._update_ej_title("left")
        self._update_ej_title("right")
        self.query_one("#left-ej-panel").add_class("visible")
        self.query_one("#right-ej-panel").add_class("visible")

    def _close_ej_panel(self, side: str) -> None:
        """EJ 패널 닫기 또는 중첩 레벨 팝."""
        other_side = "right" if side == "left" else "left"
        ej_stack = self._left_ej_stack if side == "left" else self._right_ej_stack
        other_stack = self._right_ej_stack if side == "left" else self._left_ej_stack

        if ej_stack:
            this_prev = ej_stack.pop()
            # 반대편 스택도 함께 pop하여 diff 재계산
            if other_stack:
                other_prev = other_stack.pop()
                left_content = this_prev if side == "left" else other_prev
                right_content = other_prev if side == "left" else this_prev
                self._open_ej_with_diff(left_content, right_content)
                self._update_ej_title(other_side)
            else:
                ej_editor = self.query_one(f"#{side}-ej-editor", DiffEditor)
                lines = this_prev.split("\n") if this_prev else [""]
                tags = [DiffTag.EQUAL] * len(lines)
                ej_editor.set_diff_data(lines, tags, set(), [])
            self._update_ej_title(side)
        else:
            # 패널 닫기 — 반대편도 함께
            self.query_one(f"#{side}-ej-panel").remove_class("visible")
            self.query_one(f"#{other_side}-ej-panel").remove_class("visible")
            other_stack.clear()
            self.query_one(f"#{side}-editor", DiffEditor).focus()

    def _update_ej_title(self, side: str) -> None:
        ej_stack = self._left_ej_stack if side == "left" else self._right_ej_stack
        level = len(ej_stack) + 1
        title = self.query_one(f"#{side}-ej-title", Static)
        title.update(f"[b]Embedded JSON[/b] [dim](level {level})[/dim]")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "left-ej-close":
            self._close_ej_panel("left")
        elif event.button.id == "right-ej-close":
            self._close_ej_panel("right")

    def key_tab(self) -> None:
        """Tab으로 좌/우 패널 전환."""
        focused = self.focused
        fid = focused.id if focused else ""
        if fid and fid.startswith("right"):
            self.query_one("#left-editor", DiffEditor).focus()
        else:
            self.query_one("#right-editor", DiffEditor).focus()


def _install_difftool(global_flag: str = "--global", cwd: str | None = None) -> None:
    """git difftool로 jvimdiff 등록."""
    try:
        subprocess.run(
            [
                "git",
                "config",
                global_flag,
                "difftool.jvimdiff.cmd",
                'jvimdiff "$LOCAL" "$REMOTE"',
            ],
            check=True,
            cwd=cwd,
        )
        subprocess.run(
            ["git", "config", global_flag, "difftool.jvimdiff.trustExitCode", "true"],
            check=True,
            cwd=cwd,
        )
    except FileNotFoundError:
        print("jvimdiff: git not found", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        print(f"jvimdiff: git config failed: {exc}", file=sys.stderr)
        sys.exit(1)
    print("Installed jvimdiff as git difftool.")
    print("  Usage: git difftool -t jvimdiff")
    print("  Set as default: git config --global diff.tool jvimdiff")


def _uninstall_difftool(global_flag: str = "--global", cwd: str | None = None) -> None:
    """git difftool에서 jvimdiff 제거."""
    try:
        subprocess.run(
            ["git", "config", global_flag, "--remove-section", "difftool.jvimdiff"],
            check=True,
            cwd=cwd,
        )
    except FileNotFoundError:
        print("jvimdiff: git not found", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError:
        print("jvimdiff: difftool.jvimdiff not configured", file=sys.stderr)
        sys.exit(1)
    print("Uninstalled jvimdiff from git difftool.")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="jvimdiff",
        description="JSON diff viewer with vim-style keybindings",
    )
    parser.add_argument(
        "file1", nargs="?", default="", help="First JSON file or directory"
    )
    parser.add_argument(
        "file2", nargs="?", default="", help="Second JSON file or directory"
    )
    parser.add_argument(
        "--no-normalize",
        action="store_true",
        help="Don't normalize JSON formatting before diffing",
    )
    parser.add_argument(
        "--jsonl",
        action="store_true",
        default=None,
        help="Treat files as JSONL (auto-detected by .jsonl extension)",
    )
    parser.add_argument(
        "--install-difftool",
        action="store_true",
        default=False,
        help="register jvimdiff as git difftool (git config --global)",
    )
    parser.add_argument(
        "--uninstall-difftool",
        action="store_true",
        default=False,
        help="remove jvimdiff from git difftool (git config --global --remove-section)",
    )
    args = parser.parse_args()

    if args.install_difftool:
        _install_difftool()
        sys.exit(0)
    if args.uninstall_difftool:
        _uninstall_difftool()
        sys.exit(0)

    if not args.file1 or not args.file2:
        parser.error("the following arguments are required: file1, file2")

    for f in (args.file1, args.file2):
        if not Path(f).exists():
            print(f"jvimdiff: {f}: No such file or directory", file=sys.stderr)
            sys.exit(1)

    # 디렉토리 비교 모드
    if Path(args.file1).is_dir() and Path(args.file2).is_dir():
        file_pairs = _collect_file_pairs(args.file1, args.file2)
        if not file_pairs:
            print("No differences", file=sys.stderr)
            sys.exit(0)
        left_path, right_path = file_pairs[0]
        # --jsonl 명시 시 True, 미지정 시 None → 파일별 자동 감지
        jsonl = True if args.jsonl else None
        app = JsonDiffApp(
            left_path=left_path,
            right_path=right_path,
            normalize=not args.no_normalize,
            jsonl=jsonl,
            file_pairs=file_pairs,
        )
        app.run()
        return

    if Path(args.file1).is_dir() or Path(args.file2).is_dir():
        print("jvimdiff: cannot compare file and directory", file=sys.stderr)
        sys.exit(1)

    # 바이너리 파일 방어
    for f in (args.file1, args.file2):
        try:
            content = Path(f).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            print(f"jvimdiff: binary file: {f}", file=sys.stderr)
            sys.exit(1)
        if "\x00" in content:
            print(f"jvimdiff: binary file: {f}", file=sys.stderr)
            sys.exit(1)

    # JSONL 자동 감지: 확장자 또는 내용 기반
    jsonl = args.jsonl
    if jsonl is None:
        jsonl = any(f.lower().endswith(".jsonl") for f in (args.file1, args.file2))
    if not jsonl:
        for f in (args.file1, args.file2):
            content = Path(f).read_text(encoding="utf-8")
            if _detect_jsonl(content):
                jsonl = True
                break

    app = JsonDiffApp(
        left_path=args.file1,
        right_path=args.file2,
        normalize=not args.no_normalize,
        jsonl=jsonl,
    )
    app.run()


if __name__ == "__main__":
    main()
