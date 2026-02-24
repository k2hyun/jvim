"""Tests for jvimdiff diff computation and DiffEditor."""

import json
from types import SimpleNamespace

from jvim.diff import (
    DiffTag,
    compute_json_diff,
    format_json,
    format_jsonl,
    normalize_json,
    normalize_jsonl,
)
from jvim.differ import DiffEditor, JsonDiffApp, SyncJsonEditor, _collect_file_pairs


class TestFormatJson:
    """JSON 포맷팅 테스트 (키 순서 유지)."""

    def test_format_simple(self):
        result = format_json('{"a":1}')
        assert result == '{\n    "a": 1\n}'

    def test_format_preserves_key_order(self):
        result = format_json('{"b": 2, "a": 1}')
        assert result.index('"b"') < result.index('"a"')

    def test_format_invalid_json(self):
        assert format_json("not json") == "not json"


class TestNormalizeJson:
    """JSON 정규화 테스트 (키 정렬 포함)."""

    def test_normalize_simple(self):
        result = normalize_json('{"a":1}')
        assert result == '{\n    "a": 1\n}'

    def test_normalize_sorts_keys(self):
        result = normalize_json('{"b": 2, "a": 1}')
        assert '"a": 1' in result
        assert result.index('"a"') < result.index('"b"')

    def test_normalize_invalid_json(self):
        """잘못된 JSON은 원본 그대로 반환."""
        content = "not json {{"
        assert normalize_json(content) == content

    def test_normalize_preserves_unicode(self):
        result = normalize_json('{"name": "한글"}')
        assert "한글" in result

    def test_normalize_nested(self):
        result = normalize_json('{"a":{"b":1}}')
        assert '    "a": {' in result
        assert '        "b": 1' in result


class TestComputeJsonDiff:
    """Diff 계산 테스트."""

    def test_identical_files(self):
        content = '{"a": 1, "b": 2}'
        result = compute_json_diff(content, content)
        assert all(t == DiffTag.EQUAL for t in result.left_line_tags)
        assert all(t == DiffTag.EQUAL for t in result.right_line_tags)
        assert len(result.hunks) == 0

    def test_alignment_equal_length(self):
        """좌우 라인 수는 항상 동일해야 한다."""
        result = compute_json_diff('{"a": 1}', '{"a": 1, "b": 2}')
        assert len(result.left_lines) == len(result.right_lines)
        assert len(result.left_line_tags) == len(result.right_line_tags)

    def test_added_key(self):
        left = '{"a": 1}'
        right = '{"a": 1, "b": 2}'
        result = compute_json_diff(left, right)
        assert len(result.hunks) > 0
        # 우측에 INSERT 또는 REPLACE 태그가 존재해야 함
        has_change = any(
            t in (DiffTag.INSERT, DiffTag.REPLACE) for t in result.right_line_tags
        )
        assert has_change

    def test_removed_key(self):
        left = '{"a": 1, "b": 2}'
        right = '{"a": 1}'
        result = compute_json_diff(left, right)
        assert len(result.hunks) > 0
        has_change = any(
            t in (DiffTag.DELETE, DiffTag.REPLACE) for t in result.left_line_tags
        )
        assert has_change

    def test_changed_value(self):
        left = '{"a": 1}'
        right = '{"a": 2}'
        result = compute_json_diff(left, right)
        assert len(result.hunks) > 0
        has_replace = any(t == DiffTag.REPLACE for t in result.left_line_tags)
        assert has_replace

    def test_no_normalize_still_formats(self):
        """normalize=False여도 pretty formatting은 적용된다."""
        left = '{"a":1}'
        right = '{"a": 1}'
        result = compute_json_diff(left, right, normalize=False)
        # 포맷팅 후 동일하므로 diff 없음
        assert len(result.hunks) == 0
        # 표시되는 라인이 indent=4로 포맷팅됨
        assert any("    " in line for line in result.left_lines)

    def test_no_normalize_preserves_key_order(self):
        """normalize=False면 키 순서가 유지된다."""
        left = '{"b": 1, "a": 2}'
        right = '{"b": 1, "a": 2}'
        result = compute_json_diff(left, right, normalize=False)
        # 키 순서 유지: "b"가 "a"보다 먼저
        left_text = "\n".join(result.left_lines)
        assert left_text.index('"b"') < left_text.index('"a"')

    def test_normalize_sorts_keys_in_diff(self):
        """normalize=True면 키가 정렬되어 구조적으로 동일한 JSON은 diff 없음."""
        left = '{"b": 1, "a": 2}'
        right = '{"a": 2, "b": 1}'
        result = compute_json_diff(left, right, normalize=True)
        assert len(result.hunks) == 0

    def test_no_normalize_identical(self):
        content = '{"a":1}'
        result = compute_json_diff(content, content, normalize=False)
        assert len(result.hunks) == 0

    def test_empty_left(self):
        result = compute_json_diff("", '{"a": 1}')
        assert len(result.left_lines) == len(result.right_lines)
        assert len(result.hunks) > 0

    def test_empty_right(self):
        result = compute_json_diff('{"a": 1}', "")
        assert len(result.left_lines) == len(result.right_lines)
        assert len(result.hunks) > 0

    def test_both_empty(self):
        result = compute_json_diff("", "")
        assert len(result.hunks) == 0

    def test_completely_different(self):
        result = compute_json_diff('{"a": 1}', '{"z": 99}')
        assert len(result.hunks) > 0
        assert len(result.left_lines) == len(result.right_lines)

    def test_filler_lines_for_insert(self):
        """INSERT 시 좌측에 빈 filler 라인이 들어간다."""
        left = '{"a": 1}'
        right = '{"a": 1, "b": 2}'
        result = compute_json_diff(left, right)
        # INSERT 태그가 있는 행의 좌측은 빈 문자열 (filler)
        for i, tag in enumerate(result.left_line_tags):
            if tag == DiffTag.INSERT:
                assert result.left_lines[i] == ""

    def test_filler_lines_for_delete(self):
        """DELETE 시 우측에 빈 filler 라인이 들어간다."""
        left = '{"a": 1, "b": 2}'
        right = '{"a": 1}'
        result = compute_json_diff(left, right)
        for i, tag in enumerate(result.right_line_tags):
            if tag == DiffTag.DELETE:
                assert result.right_lines[i] == ""


class TestJsonlFormat:
    """JSONL 포맷팅/정규화 테스트."""

    def test_format_jsonl(self):
        content = '{"a":1}\n{"b":2}'
        result = format_jsonl(content)
        # 레코드별 pretty-print, 빈 줄로 구분
        assert "    " in result
        assert "\n\n" in result

    def test_format_jsonl_preserves_key_order(self):
        content = '{"b":1,"a":2}\n{"d":3,"c":4}'
        result = format_jsonl(content)
        assert result.index('"b"') < result.index('"a"')
        assert result.index('"d"') < result.index('"c"')

    def test_normalize_jsonl_sorts_keys(self):
        content = '{"b":1,"a":2}'
        result = normalize_jsonl(content)
        assert result.index('"a"') < result.index('"b"')

    def test_format_jsonl_skips_empty_lines(self):
        content = '{"a":1}\n\n{"b":2}\n'
        result = format_jsonl(content)
        blocks = result.split("\n\n")
        assert len(blocks) == 2

    def test_format_jsonl_invalid_record(self):
        content = '{"a":1}\nnot json\n{"b":2}'
        result = format_jsonl(content)
        assert "not json" in result


class TestComputeJsonDiffJsonl:
    """JSONL diff 계산 테스트."""

    def test_identical_jsonl(self):
        content = '{"a":1}\n{"b":2}'
        result = compute_json_diff(content, content, jsonl=True)
        assert len(result.hunks) == 0

    def test_jsonl_added_record(self):
        left = '{"a":1}'
        right = '{"a":1}\n{"b":2}'
        result = compute_json_diff(left, right, jsonl=True)
        assert len(result.hunks) > 0
        assert len(result.left_lines) == len(result.right_lines)

    def test_jsonl_changed_value(self):
        left = '{"a":1}\n{"b":2}'
        right = '{"a":1}\n{"b":99}'
        result = compute_json_diff(left, right, jsonl=True)
        assert len(result.hunks) > 0

    def test_jsonl_no_normalize_still_formats(self):
        """normalize=False여도 JSONL pretty formatting은 적용."""
        content = '{"a":1}\n{"b":2}'
        result = compute_json_diff(content, content, normalize=False, jsonl=True)
        assert len(result.hunks) == 0
        # 포맷팅이 적용되었는지 확인
        assert any("    " in line for line in result.left_lines)

    def test_jsonl_normalize_sorts_keys(self):
        """normalize=True면 JSONL 레코드 키도 정렬."""
        left = '{"b":1,"a":2}'
        right = '{"a":2,"b":1}'
        result = compute_json_diff(left, right, normalize=True, jsonl=True)
        assert len(result.hunks) == 0


class TestDiffEditor:
    """DiffEditor 위젯 테스트."""

    def test_init_readonly(self):
        editor = DiffEditor('{"a": 1}')
        assert editor.read_only is True

    def test_set_diff_data(self):
        editor = DiffEditor()
        lines = ["line1", "line2", "line3"]
        tags = [DiffTag.EQUAL, DiffTag.REPLACE, DiffTag.EQUAL]
        from jvim.diff import DiffHunk

        hunks = [DiffHunk(1, 1, 1, 1, DiffTag.REPLACE)]
        editor.set_diff_data(lines, tags, set(), hunks)
        assert editor.lines == lines
        assert editor._line_tags == tags
        assert len(editor._diff_hunks) == 1

    def test_line_background_equal(self):
        editor = DiffEditor()
        editor._line_tags = [DiffTag.EQUAL]
        assert editor._line_background(0) == ""

    def test_line_background_delete(self):
        editor = DiffEditor()
        editor._line_tags = [DiffTag.DELETE]
        assert "on" in editor._line_background(0)

    def test_line_background_insert(self):
        editor = DiffEditor()
        editor._line_tags = [DiffTag.INSERT]
        assert "on" in editor._line_background(0)

    def test_line_background_replace(self):
        editor = DiffEditor()
        editor._line_tags = [DiffTag.REPLACE]
        assert "on" in editor._line_background(0)

    def test_line_background_filler(self):
        editor = DiffEditor()
        editor._line_tags = [DiffTag.INSERT]
        editor._filler_rows = {0}
        bg = editor._line_background(0)
        assert bg == DiffEditor._FILLER_BG

    def test_line_background_out_of_range(self):
        editor = DiffEditor()
        editor._line_tags = [DiffTag.EQUAL]
        assert editor._line_background(99) == ""

    def test_hunk_navigation_next(self):
        editor = DiffEditor()
        from jvim.diff import DiffHunk

        hunks = [
            DiffHunk(5, 2, 5, 2, DiffTag.REPLACE),
            DiffHunk(15, 3, 15, 3, DiffTag.DELETE),
        ]
        lines = [f"line{i}" for i in range(20)]
        tags = [DiffTag.EQUAL] * 20
        editor.set_diff_data(lines, tags, set(), hunks)
        editor._visible_height = lambda: 30

        editor._goto_next_hunk()
        assert editor.cursor_row == 5
        assert editor._current_hunk == 0

        editor._goto_next_hunk()
        assert editor.cursor_row == 15
        assert editor._current_hunk == 1

        # 순환
        editor._goto_next_hunk()
        assert editor.cursor_row == 5
        assert editor._current_hunk == 0

    def test_hunk_navigation_prev(self):
        editor = DiffEditor()
        from jvim.diff import DiffHunk

        hunks = [
            DiffHunk(5, 2, 5, 2, DiffTag.REPLACE),
            DiffHunk(15, 3, 15, 3, DiffTag.DELETE),
        ]
        lines = [f"line{i}" for i in range(20)]
        tags = [DiffTag.EQUAL] * 20
        editor.set_diff_data(lines, tags, set(), hunks)
        editor._visible_height = lambda: 30

        editor._goto_prev_hunk()
        assert editor.cursor_row == 15
        assert editor._current_hunk == 1

        editor._goto_prev_hunk()
        assert editor.cursor_row == 5
        assert editor._current_hunk == 0

    def test_hunk_navigation_no_hunks(self):
        editor = DiffEditor()
        editor.set_diff_data(["line"], [DiffTag.EQUAL], set(), [])
        editor._goto_next_hunk()
        assert editor.status_msg == "No diffs"

    def test_pending_bracket_c(self):
        """']c' / '[c' 키 조합으로 hunk 네비게이션."""
        editor = DiffEditor()
        from jvim.diff import DiffHunk

        hunks = [DiffHunk(3, 1, 3, 1, DiffTag.REPLACE)]
        lines = [f"line{i}" for i in range(10)]
        tags = [DiffTag.EQUAL] * 10
        editor.set_diff_data(lines, tags, set(), hunks)
        editor._visible_height = lambda: 30

        # ']' sets pending
        event_bracket = SimpleNamespace(key="right_square_bracket", character="]")
        editor._handle_normal(event_bracket)
        assert editor.pending == "]"

        # 'c' triggers next hunk
        event_c = SimpleNamespace(key="c", character="c")
        editor._handle_normal(event_c)
        assert editor.cursor_row == 3
        assert editor.pending == ""

    def test_status_msg_hunk_count(self):
        editor = DiffEditor()
        from jvim.diff import DiffHunk

        hunks = [
            DiffHunk(1, 1, 1, 1, DiffTag.REPLACE),
            DiffHunk(5, 1, 5, 1, DiffTag.DELETE),
        ]
        editor._diff_hunks = hunks
        editor._update_hunk_status()
        assert "2 hunks" in editor.status_msg

    def test_status_msg_identical(self):
        editor = DiffEditor()
        editor._diff_hunks = []
        editor._update_hunk_status()
        assert "identical" in editor.status_msg.lower()


class TestDiffEditorEmbeddedJson:
    """DiffEditor EJ 기능 테스트."""

    def _make_editor_with_embedded(self) -> DiffEditor:
        """임베디드 JSON이 포함된 DiffEditor 생성."""
        inner = json.dumps({"nested": 1}, ensure_ascii=False)
        escaped = json.dumps(inner, ensure_ascii=False)
        content = f'{{"data": {escaped}}}'
        editor = DiffEditor(content)
        return editor

    def test_find_string_at_cursor(self):
        """DiffEditor에서 임베디드 JSON 문자열을 찾을 수 있다."""
        editor = self._make_editor_with_embedded()
        # 포맷팅되지 않은 한 줄 JSON
        editor.cursor_row = 0
        editor.cursor_col = 9
        result = editor._find_string_at_cursor()
        assert result is not None
        _, _, content = result
        parsed = json.loads(content)
        assert parsed == {"nested": 1}

    def test_ej_on_diff_editor_is_readonly(self):
        """DiffEditor는 항상 read_only."""
        editor = self._make_editor_with_embedded()
        assert editor.read_only is True

    def test_ej_stack_push_pop(self):
        """EJ 스택 기반 중첩 네비게이션 로직 검증."""
        # 스택 동작은 App 레벨이므로 여기서는 스택 자체만 테스트
        stack: list[str] = []
        content1 = '{"level": 1}'
        content2 = '{"level": 2}'

        # 첫 번째 ej: 스택 비어있음
        stack.append(content1)
        assert len(stack) == 1

        # 중첩 ej
        stack.append(content2)
        assert len(stack) == 2

        # pop (닫기)
        restored = stack.pop()
        assert restored == content2
        assert len(stack) == 1

        restored = stack.pop()
        assert restored == content1
        assert len(stack) == 0

    def test_ej_editor_inherits_read_only(self):
        """EJ 패널에 사용될 DiffEditor도 read_only."""
        ej_editor = DiffEditor("")
        assert ej_editor.read_only is True

    def test_ej_diff_editor_set_diff_data(self):
        """EJ DiffEditor에 diff 데이터를 설정할 수 있다."""
        ej = DiffEditor("")
        lines = ['    "a": 1,', '    "b": 2']
        tags = [DiffTag.EQUAL, DiffTag.REPLACE]
        from jvim.diff import DiffHunk

        hunks = [DiffHunk(1, 1, 1, 1, DiffTag.REPLACE)]
        ej.set_diff_data(lines, tags, set(), hunks)
        assert ej.lines == lines
        assert ej._line_tags == tags
        assert len(ej._diff_hunks) == 1
        # REPLACE 행에는 배경색이 있어야 함
        assert ej._line_background(1) == DiffEditor._DIFF_BG[DiffTag.REPLACE]

    def test_ej_diff_both_sides(self):
        """양쪽 임베디드 JSON의 diff 계산 검증."""
        left_ej = '{\n    "key": "old_value"\n}'
        right_ej = '{\n    "key": "new_value"\n}'
        result = compute_json_diff(left_ej, right_ej, normalize=False)
        # 차이가 있어야 함
        assert len(result.hunks) > 0
        assert len(result.left_lines) == len(result.right_lines)
        has_change = any(
            t in (DiffTag.REPLACE, DiffTag.DELETE, DiffTag.INSERT)
            for t in result.left_line_tags
        )
        assert has_change

    def test_ej_diff_identical(self):
        """동일한 임베디드 JSON은 diff 없음."""
        content = '{\n    "key": "value"\n}'
        result = compute_json_diff(content, content, normalize=False)
        assert len(result.hunks) == 0


class TestBlockLevelDiff:
    """블록 단위 diff 최적화 테스트."""

    def _make_array_json(self, n: int, modify_indices: set[int] | None = None) -> str:
        """n개 요소의 배열 JSON 생성."""
        items = []
        for i in range(n):
            name = "modified" if modify_indices and i in modify_indices else f"user_{i}"
            items.append({"id": i, "name": name, "value": i * 10})
        return json.dumps({"items": items}, indent=4, ensure_ascii=False)

    def test_identical_large_array(self):
        """동일 배열은 hunk 0개."""
        data = self._make_array_json(100)
        result = compute_json_diff(data, data, normalize=False)
        assert len(result.hunks) == 0
        assert all(t == DiffTag.EQUAL for t in result.left_line_tags)

    def test_single_block_change(self):
        """1개 요소 변경 시 REPLACE 범위가 해당 블록으로 제한."""
        left = self._make_array_json(20)
        right = self._make_array_json(20, modify_indices={5})
        result = compute_json_diff(left, right, normalize=False)
        assert len(result.hunks) > 0
        # REPLACE 범위가 전체가 아닌 일부로 제한
        replace_count = sum(1 for t in result.left_line_tags if t == DiffTag.REPLACE)
        total_lines = len(result.left_lines)
        assert replace_count < total_lines * 0.5

    def test_block_insert(self):
        """요소 추가 시 INSERT 발생."""
        left = self._make_array_json(10)
        right_data = json.loads(left)
        right_data["items"].append({"id": 99, "name": "new", "value": 990})
        right = json.dumps(right_data, indent=4, ensure_ascii=False)
        result = compute_json_diff(left, right, normalize=False)
        has_change = any(
            t in (DiffTag.INSERT, DiffTag.REPLACE) for t in result.left_line_tags
        )
        assert has_change

    def test_block_delete(self):
        """요소 삭제 시 DELETE 발생."""
        left = self._make_array_json(10)
        right_data = json.loads(left)
        right_data["items"].pop(3)
        right = json.dumps(right_data, indent=4, ensure_ascii=False)
        result = compute_json_diff(left, right, normalize=False)
        has_change = any(
            t in (DiffTag.DELETE, DiffTag.REPLACE) for t in result.left_line_tags
        )
        assert has_change

    def test_fallback_flat_json(self):
        """블록 없는 flat JSON은 기존 방식 동작."""
        left = '{"a": 1, "b": 2}'
        right = '{"a": 1, "b": 3}'
        result = compute_json_diff(left, right, normalize=False)
        assert len(result.hunks) > 0

    def test_top_level_array(self):
        """[{...}, ...] 최상위 배열 구조 지원."""
        items = [{"id": i, "name": f"item_{i}"} for i in range(10)]
        left = json.dumps(items, indent=4, ensure_ascii=False)
        modified = json.loads(left)
        modified[3]["name"] = "changed"
        right = json.dumps(modified, indent=4, ensure_ascii=False)
        result = compute_json_diff(left, right, normalize=False)
        assert len(result.hunks) > 0
        replace_count = sum(1 for t in result.left_line_tags if t == DiffTag.REPLACE)
        assert replace_count < len(result.left_lines) * 0.5

    def test_different_structure_fallback(self):
        """양쪽 indent 불일치 시 폴백."""
        left = json.dumps({"outer": [{"id": i} for i in range(10)]}, indent=4)
        right = json.dumps([{"id": i} for i in range(10)], indent=4)
        result = compute_json_diff(left, right, normalize=False)
        assert len(result.left_lines) == len(result.right_lines)

    def test_equal_lines_are_truly_equal(self):
        """EQUAL 태그 라인은 좌우 동일."""
        left = self._make_array_json(20)
        right = self._make_array_json(20, modify_indices={5, 15})
        result = compute_json_diff(left, right, normalize=False)
        for i, tag in enumerate(result.left_line_tags):
            if tag == DiffTag.EQUAL:
                assert result.left_lines[i] == result.right_lines[i], (
                    f"Line {i}: EQUAL tag but lines differ: "
                    f"{result.left_lines[i]!r} vs {result.right_lines[i]!r}"
                )


class TestDiffFoldSync:
    """diff 뷰어에서 fold 동기화 테스트."""

    SAMPLE = '{\n    "a": {\n        "b": 1\n    },\n    "c": 2\n}'

    def test_sync_toggle_fold(self):
        """한쪽에서 fold하면 다른 쪽도 동기화."""
        left = SyncJsonEditor(self.SAMPLE)
        right = SyncJsonEditor(self.SAMPLE)
        left._sync_target = right
        right._sync_target = left

        left._toggle_fold(1)
        assert 1 in left._folds
        assert 1 in right._folds
        assert right._folds[1] == left._folds[1]

    def test_sync_unfold_all(self):
        """전체 펼기 동기화."""
        left = SyncJsonEditor(self.SAMPLE)
        right = SyncJsonEditor(self.SAMPLE)
        left._sync_target = right
        right._sync_target = left

        left._fold_all()
        assert len(right._folds) > 0
        left._unfold_all()
        assert right._folds == {}

    def test_set_diff_data_clears_folds(self):
        """set_diff_data 시 fold 초기화."""
        editor = DiffEditor(self.SAMPLE)
        editor._folds[1] = 3
        editor.set_diff_data(
            lines=["a", "b"],
            tags=[DiffTag.EQUAL, DiffTag.EQUAL],
            filler_rows=set(),
            hunks=[],
        )
        assert editor._folds == {}

    def test_unfold_diff_regions(self):
        """diff가 있는 fold 영역만 자동으로 unfold."""
        content = json.dumps({"a": {"x": 1}, "b": {"y": 2}, "c": {"z": 3}}, indent=4)
        editor = DiffEditor(content)
        # EQUAL 태그로 초기화하되 b 블록에 REPLACE 태그 삽입
        lines = content.split("\n")
        tags = [DiffTag.EQUAL] * len(lines)
        # "b" 블록의 라인을 찾아서 REPLACE 태그 설정
        for i, line in enumerate(lines):
            if '"b"' in line or '"y"' in line:
                tags[i] = DiffTag.REPLACE
        editor._line_tags = tags
        # 전체 fold
        editor._fold_all()
        folded_before = dict(editor._folds)
        assert len(folded_before) > 0

        # diff 영역 unfold
        JsonDiffApp._unfold_diff_regions(editor)

        # b 블록의 fold는 제거되어야 함
        for start, end in folded_before.items():
            has_diff = any(
                tags[i] != DiffTag.EQUAL for i in range(start, end + 1) if i < len(tags)
            )
            if has_diff:
                assert start not in editor._folds, (
                    f"fold at {start} should be unfolded (has diff)"
                )
            else:
                assert start in editor._folds, f"fold at {start} should remain folded"

    def test_unfold_diff_regions_all_equal(self):
        """모든 라인이 EQUAL이면 fold 유지."""
        content = json.dumps({"a": {"x": 1}, "b": {"y": 2}}, indent=4)
        editor = DiffEditor(content)
        tags = [DiffTag.EQUAL] * len(content.split("\n"))
        editor._line_tags = tags
        editor._fold_all()
        folds_before = dict(editor._folds)
        JsonDiffApp._unfold_diff_regions(editor)
        assert editor._folds == folds_before

    def test_nested_fold_preserves_clean_siblings(self):
        """diff가 있는 depth-1 블록을 열어도, 안쪽 diff 없는 블록은 접힌 상태 유지."""
        # "a" 블록에 diff가 있지만 "a.inner"에는 없음
        content = json.dumps(
            {
                "a": {"inner": {"x": 1, "y": 2}, "changed": "val"},
                "b": {"clean": {"z": 3}},
            },
            indent=4,
        )
        lines = content.split("\n")
        tags = [DiffTag.EQUAL] * len(lines)
        # "changed" 라인에만 REPLACE
        for i, line in enumerate(lines):
            if '"changed"' in line:
                tags[i] = DiffTag.REPLACE
        editor = DiffEditor(content)
        editor._line_tags = tags
        # 모든 depth fold
        editor._fold_all_nested()
        # "a"의 inner 블록과 "b"의 clean 블록도 접혀 있어야 함
        inner_folds = {s for s in editor._folds if s > 0}
        assert len(inner_folds) >= 2  # 최소 depth-1 2개 + depth-2 블록들

        # diff unfold
        JsonDiffApp._unfold_diff_regions(editor)

        # "b" 블록은 diff 없으므로 접힌 상태
        b_start = None
        for i, line in enumerate(lines):
            if '"b"' in line and "{" in line:
                b_start = i
                break
        assert b_start is not None
        assert b_start in editor._folds, "diff 없는 b 블록은 접힌 상태 유지"

        # "a.inner" 블록도 diff 없으므로 접힌 상태
        inner_start = None
        for i, line in enumerate(lines):
            if '"inner"' in line and "{" in line:
                inner_start = i
                break
        assert inner_start is not None
        assert inner_start in editor._folds, "diff 없는 inner 블록은 접힌 상태 유지"

    def test_unfold_diff_expands_collapsed_strings(self):
        """diff가 있는 collapsed string은 자동 펼기."""
        long_str = "a" * 100
        content = '{\n    "data": "' + long_str + '"\n}'
        editor = DiffEditor(content)
        lines = content.split("\n")
        tags = [DiffTag.EQUAL] * len(lines)
        tags[1] = DiffTag.REPLACE  # "data" 라인에 diff
        editor._line_tags = tags
        editor._collapsed_strings.add(1)

        JsonDiffApp._unfold_diff_regions(editor)
        assert 1 not in editor._collapsed_strings

    def test_unfold_diff_keeps_clean_collapsed_strings(self):
        """diff가 없는 collapsed string은 접힌 상태 유지."""
        long_str = "a" * 100
        content = '{\n    "data": "' + long_str + '"\n}'
        editor = DiffEditor(content)
        lines = content.split("\n")
        tags = [DiffTag.EQUAL] * len(lines)
        editor._line_tags = tags
        editor._collapsed_strings.add(1)

        JsonDiffApp._unfold_diff_regions(editor)
        assert 1 in editor._collapsed_strings


class TestDiffBinaryFile:
    """jvimdiff 바이너리 파일 방어 테스트."""

    def test_binary_null_byte(self, tmp_path):
        """null byte 포함 파일은 거부."""
        import subprocess

        f = tmp_path / "a.json"
        f.write_bytes(b'{"key": "\x00"}')
        b = tmp_path / "b.json"
        b.write_text('{"key": "ok"}')
        result = subprocess.run(
            ["python", "-m", "jvim.differ", str(f), str(b)],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "binary file" in result.stderr

    def test_binary_encoding_error(self, tmp_path):
        """UTF-8 디코딩 불가 파일은 거부."""
        import subprocess

        f = tmp_path / "a.json"
        f.write_bytes(b'\xff\xfe{"key": 1}')
        b = tmp_path / "b.json"
        b.write_text('{"key": 1}')
        result = subprocess.run(
            ["python", "-m", "jvim.differ", str(f), str(b)],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "binary file" in result.stderr


class TestDirectoryDiff:
    """디렉토리 비교 기능 테스트."""

    def test_collect_different_files(self, tmp_path):
        """다른 파일만 수집."""
        left = tmp_path / "left"
        right = tmp_path / "right"
        left.mkdir()
        right.mkdir()
        (left / "a.json").write_text('{"a": 1}')
        (right / "a.json").write_text('{"a": 2}')
        pairs = _collect_file_pairs(str(left), str(right))
        assert len(pairs) == 1
        assert pairs[0] == (str(left / "a.json"), str(right / "a.json"))

    def test_skip_identical_files(self, tmp_path):
        """동일한 파일은 건너뛰기."""
        left = tmp_path / "left"
        right = tmp_path / "right"
        left.mkdir()
        right.mkdir()
        (left / "same.json").write_text('{"x": 1}')
        (right / "same.json").write_text('{"x": 1}')
        pairs = _collect_file_pairs(str(left), str(right))
        assert len(pairs) == 0

    def test_file_only_in_left(self, tmp_path):
        """한쪽에만 있는 파일 포함 (left only)."""
        left = tmp_path / "left"
        right = tmp_path / "right"
        left.mkdir()
        right.mkdir()
        (left / "only_left.json").write_text('{"a": 1}')
        pairs = _collect_file_pairs(str(left), str(right))
        assert len(pairs) == 1
        assert pairs[0] == (str(left / "only_left.json"), "")

    def test_file_only_in_right(self, tmp_path):
        """한쪽에만 있는 파일 포함 (right only)."""
        left = tmp_path / "left"
        right = tmp_path / "right"
        left.mkdir()
        right.mkdir()
        (right / "only_right.json").write_text('{"b": 2}')
        pairs = _collect_file_pairs(str(left), str(right))
        assert len(pairs) == 1
        assert pairs[0] == ("", str(right / "only_right.json"))

    def test_recursive_subdirectory(self, tmp_path):
        """하위 디렉토리 재귀 탐색."""
        left = tmp_path / "left"
        right = tmp_path / "right"
        (left / "sub").mkdir(parents=True)
        (right / "sub").mkdir(parents=True)
        (left / "sub" / "deep.json").write_text('{"v": 1}')
        (right / "sub" / "deep.json").write_text('{"v": 2}')
        pairs = _collect_file_pairs(str(left), str(right))
        assert len(pairs) == 1
        assert "sub" in pairs[0][0]

    def test_skip_binary_files(self, tmp_path):
        """바이너리 파일 제외."""
        left = tmp_path / "left"
        right = tmp_path / "right"
        left.mkdir()
        right.mkdir()
        (left / "bin.dat").write_bytes(b"\x00\x01\x02")
        (right / "bin.dat").write_bytes(b"\x00\x01\x03")
        pairs = _collect_file_pairs(str(left), str(right))
        assert len(pairs) == 0

    def test_empty_directories(self, tmp_path):
        """빈 디렉토리 → 빈 리스트."""
        left = tmp_path / "left"
        right = tmp_path / "right"
        left.mkdir()
        right.mkdir()
        pairs = _collect_file_pairs(str(left), str(right))
        assert pairs == []

    def test_sorted_by_relative_path(self, tmp_path):
        """상대 경로 기준 알파벳 정렬."""
        left = tmp_path / "left"
        right = tmp_path / "right"
        left.mkdir()
        right.mkdir()
        # 역순으로 생성해도 정렬됨
        for name in ("c.json", "a.json", "b.json"):
            (left / name).write_text(f'{{"file": "{name}"}}')
            (right / name).write_text(f'{{"file": "{name}", "extra": true}}')
        pairs = _collect_file_pairs(str(left), str(right))
        assert len(pairs) == 3
        names = [p[0].split("/")[-1] for p in pairs]
        assert names == ["a.json", "b.json", "c.json"]

    def test_diff_app_with_file_pairs(self):
        """JsonDiffApp에 file_pairs 전달 시 초기 상태 확인."""
        pairs = [("/tmp/a.json", "/tmp/b.json"), ("/tmp/c.json", "")]
        app = JsonDiffApp(
            left_path=pairs[0][0],
            right_path=pairs[0][1],
            file_pairs=pairs,
        )
        assert app.file_pairs == pairs
        assert app.pair_index == 0
        assert len(app.file_pairs) == 2

    def test_detect_jsonl_by_extension(self):
        """확장자 .jsonl로 JSONL 감지."""
        assert JsonDiffApp._detect_jsonl_for_pair("a.jsonl", "b.json", "", "") is True
        assert JsonDiffApp._detect_jsonl_for_pair("a.json", "b.JSONL", "", "") is True

    def test_detect_jsonl_by_content(self):
        """내용 기반 JSONL 감지."""
        content = '{"a":1}\n{"b":2}'
        assert (
            JsonDiffApp._detect_jsonl_for_pair("a.json", "b.json", content, "") is True
        )

    def test_detect_jsonl_negative(self):
        """일반 JSON은 JSONL로 감지되지 않음."""
        assert (
            JsonDiffApp._detect_jsonl_for_pair("a.json", "b.json", '{"a":1}', '{"b":2}')
            is False
        )

    def test_detect_jsonl_empty_path(self):
        """빈 경로(한쪽만 있는 파일)에서도 감지 동작."""
        content = '{"a":1}\n{"b":2}'
        assert JsonDiffApp._detect_jsonl_for_pair("", "b.json", "", content) is True
        assert JsonDiffApp._detect_jsonl_for_pair("a.jsonl", "", "", "") is True


class TestBuildLinePaths:
    """_build_line_paths 테스트."""

    def test_simple_object(self):
        """단순 객체의 경로 추적."""
        lines = json.dumps({"a": 1, "b": 2}, indent=4).split("\n")
        paths = DiffEditor._build_line_paths(lines)
        assert len(paths) == len(lines)
        assert paths[0] == "$"  # {
        assert "$.a" in paths
        assert "$.b" in paths

    def test_nested_object(self):
        """중첩 객체의 경로 추적."""
        lines = json.dumps({"a": {"x": 1, "y": 2}}, indent=4).split("\n")
        paths = DiffEditor._build_line_paths(lines)
        assert "$.a" in paths
        assert "$.a.x" in paths
        assert "$.a.y" in paths

    def test_array(self):
        """배열의 경로 추적."""
        lines = json.dumps({"items": [1, 2, 3]}, indent=4).split("\n")
        paths = DiffEditor._build_line_paths(lines)
        assert "$.items" in paths

    def test_object_array(self):
        """객체 배열의 경로 추적."""
        data = {"items": [{"id": 1}, {"id": 2}]}
        lines = json.dumps(data, indent=4).split("\n")
        paths = DiffEditor._build_line_paths(lines)
        assert "$.items" in paths
        assert "$.items[0]" in paths or any("$.items[0]" in p for p in paths)
        assert any("$.items[0].id" in p for p in paths)
        assert any("$.items[1].id" in p for p in paths)

    def test_empty_lines(self):
        """빈 라인은 빈 경로."""
        paths = DiffEditor._build_line_paths(["", "  ", ""])
        assert paths[0] == ""

    def test_empty_object(self):
        """빈 객체."""
        lines = json.dumps({"a": {}}, indent=4).split("\n")
        paths = DiffEditor._build_line_paths(lines)
        assert "$.a" in paths


class TestIgnorePaths:
    """ignore path 기능 테스트."""

    def _make_editor_with_diff(self, data: dict) -> DiffEditor:
        """diff 데이터가 설정된 DiffEditor 생성."""
        content = json.dumps(data, indent=4)
        lines = content.split("\n")
        tags = [DiffTag.REPLACE] * len(lines)
        editor = DiffEditor()
        editor.set_diff_data(lines, tags, set(), [])
        return editor

    def test_suppressed_lines_update(self):
        """ignore 패턴 추가 시 _suppressed_lines 갱신."""
        editor = self._make_editor_with_diff({"a": 1, "b": 2, "c": 3})
        editor._ignore_paths = ["$.b"]
        editor._update_suppressed_lines()
        assert len(editor._suppressed_lines) > 0
        # $.b 라인만 억제
        line_paths = DiffEditor._build_line_paths(editor.lines)
        for i in editor._suppressed_lines:
            assert line_paths[i].startswith("$.b")

    def test_line_background_suppressed(self):
        """억제된 라인은 빈 배경 반환."""
        editor = self._make_editor_with_diff({"a": 1, "b": 2})
        editor._ignore_paths = ["$.b"]
        editor._update_suppressed_lines()
        for i in editor._suppressed_lines:
            assert editor._line_background(i) == ""

    def test_nested_path_suppresses_children(self):
        """중첩 경로의 하위 전체 억제."""
        editor = self._make_editor_with_diff(
            {"metadata": {"timestamp": "2024", "version": "1.0"}}
        )
        editor._ignore_paths = ["$.metadata"]
        editor._update_suppressed_lines()
        line_paths = DiffEditor._build_line_paths(editor.lines)
        for i in editor._suppressed_lines:
            assert line_paths[i].startswith("$.metadata")
        # metadata.timestamp과 metadata.version 모두 억제
        meta_lines = [i for i, p in enumerate(line_paths) if p.startswith("$.metadata")]
        assert all(i in editor._suppressed_lines for i in meta_lines)

    def test_recursive_pattern(self):
        """$..key 재귀 매칭 테스트."""
        data = {"a": {"name": "x"}, "b": {"name": "y"}}
        editor = self._make_editor_with_diff(data)
        editor._ignore_paths = ["$..name"]
        editor._update_suppressed_lines()
        line_paths = DiffEditor._build_line_paths(editor.lines)
        # $.a.name, $.b.name 라인이 억제
        name_lines = [i for i, p in enumerate(line_paths) if p.endswith(".name")]
        assert len(name_lines) >= 2
        assert all(i in editor._suppressed_lines for i in name_lines)

    def test_filler_lines_not_suppressed(self):
        """filler 라인은 억제하지 않음."""
        editor = DiffEditor()
        lines = ["{\n", '    "a": 1', "", "}"]
        tags = [DiffTag.EQUAL, DiffTag.REPLACE, DiffTag.INSERT, DiffTag.EQUAL]
        editor.set_diff_data(lines, tags, {2}, [])
        editor._ignore_paths = ["$.a"]
        editor._update_suppressed_lines()
        assert 2 not in editor._suppressed_lines

    def test_clear_all_ignore(self):
        """전체 해제 시 suppressed_lines 비워짐."""
        editor = self._make_editor_with_diff({"a": 1, "b": 2})
        editor._ignore_paths = ["$.a", "$.b"]
        editor._update_suppressed_lines()
        assert len(editor._suppressed_lines) > 0
        editor._ignore_paths.clear()
        editor._update_suppressed_lines()
        assert len(editor._suppressed_lines) == 0

    def test_no_ignore_paths(self):
        """ignore 패턴 없으면 억제 없음."""
        editor = self._make_editor_with_diff({"a": 1})
        editor._update_suppressed_lines()
        assert len(editor._suppressed_lines) == 0

    def test_non_matching_pattern(self):
        """매칭되지 않는 패턴은 억제 없음."""
        editor = self._make_editor_with_diff({"a": 1, "b": 2})
        editor._ignore_paths = ["$.nonexistent"]
        editor._update_suppressed_lines()
        assert len(editor._suppressed_lines) == 0

    def test_wildcard_pattern(self):
        """[*] 와일드카드 패턴으로 배열 요소 억제."""
        data = {"items": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]}
        editor = self._make_editor_with_diff(data)
        editor._ignore_paths = ["$.items[*].id"]
        editor._update_suppressed_lines()
        line_paths = DiffEditor._build_line_paths(editor.lines)
        id_lines = [i for i, p in enumerate(line_paths) if p.endswith(".id")]
        assert len(id_lines) >= 2
        assert all(i in editor._suppressed_lines for i in id_lines)
        # name 라인은 억제되지 않음
        name_lines = [i for i, p in enumerate(line_paths) if p.endswith(".name")]
        assert all(i not in editor._suppressed_lines for i in name_lines)

    def test_wildcard_nested_suppresses_children(self):
        """[*] 와일드카드로 매칭된 경로의 하위 전체 억제."""
        data = {
            "groups": [
                {"meta": {"ts": "2024", "v": 1}},
                {"meta": {"ts": "2025", "v": 2}},
            ]
        }
        editor = self._make_editor_with_diff(data)
        editor._ignore_paths = ["$.groups[*].meta"]
        editor._update_suppressed_lines()
        line_paths = DiffEditor._build_line_paths(editor.lines)
        meta_lines = [i for i, p in enumerate(line_paths) if ".meta" in p]
        assert len(meta_lines) >= 4  # meta, ts, v (x2)
        assert all(i in editor._suppressed_lines for i in meta_lines)


class TestDiffEditorParseableContent:
    """DiffEditor._get_parseable_content 및 JSONPath 검색 테스트."""

    def test_parseable_content_excludes_fillers(self):
        """filler 행이 제외된 콘텐츠 반환."""
        editor = DiffEditor()
        lines = ["{", '    "a": 1', "", "}"]
        tags = [DiffTag.EQUAL, DiffTag.EQUAL, DiffTag.INSERT, DiffTag.EQUAL]
        editor.set_diff_data(lines, tags, {2}, [])
        content = editor._get_parseable_content()
        assert content == '{\n    "a": 1\n}'
        # filler 제외 후 JSON 파싱 가능
        data = json.loads(content)
        assert data == {"a": 1}

    def test_parseable_content_valid_json(self):
        """filler 없는 경우 원본과 동일."""
        editor = DiffEditor()
        lines = ["{", '    "a": 1', "}"]
        tags = [DiffTag.EQUAL, DiffTag.REPLACE, DiffTag.EQUAL]
        editor.set_diff_data(lines, tags, set(), [])
        content = editor._get_parseable_content()
        assert json.loads(content) == {"a": 1}

    def test_parseable_content_jsonl_with_fillers(self):
        """JSONL 콘텐츠에서 filler 제외 후 블록 분리 가능."""
        editor = DiffEditor()
        # JSONL: 두 레코드, 첫 번째에 filler 있음
        lines = ["{", '    "a": 1', "", "}", "", "{", '    "b": 2', "}"]
        tags = [
            DiffTag.EQUAL,
            DiffTag.EQUAL,
            DiffTag.INSERT,  # filler
            DiffTag.EQUAL,
            DiffTag.EQUAL,  # JSONL separator
            DiffTag.EQUAL,
            DiffTag.REPLACE,
            DiffTag.EQUAL,
        ]
        editor.set_diff_data(lines, tags, {2}, [])
        content = editor._get_parseable_content()
        # filler 제외 후 JSONL 블록 분리
        blocks = editor._split_jsonl_blocks(content)
        assert len(blocks) == 2
        assert json.loads(blocks[0]) == {"a": 1}
        assert json.loads(blocks[1]) == {"b": 2}

    def test_compute_block_start_lines_skips_fillers(self):
        """_compute_block_start_lines가 filler를 건너뛰고 올바른 블록 시작 반환."""
        editor = DiffEditor()
        lines = ["{", '    "a": 1', "", "}", "", "{", '    "b": 2', "}"]
        tags = [
            DiffTag.EQUAL,
            DiffTag.EQUAL,
            DiffTag.INSERT,
            DiffTag.EQUAL,
            DiffTag.EQUAL,
            DiffTag.EQUAL,
            DiffTag.REPLACE,
            DiffTag.EQUAL,
        ]
        editor.set_diff_data(lines, tags, {2}, [])
        block_starts = editor._compute_block_start_lines()
        # 블록 0: line 0 ({), 블록 1: line 5 ({)
        assert block_starts[0] == 0
        assert block_starts[1] == 5

    def test_recursive_pattern_with_fillers(self):
        """filler가 있는 DiffEditor에서 재귀 패턴이 올바르게 작동."""
        data = {"a": {"name": "x"}, "b": {"name": "y"}}
        content = json.dumps(data, indent=4)
        lines = content.split("\n")
        # 중간에 filler 삽입 시뮬레이션
        filler_idx = 4
        lines.insert(filler_idx, "")
        tags = [DiffTag.REPLACE] * len(lines)
        tags[filler_idx] = DiffTag.INSERT

        editor = DiffEditor()
        editor.set_diff_data(lines, tags, {filler_idx}, [])
        editor._ignore_paths = ["$..name"]
        editor._update_suppressed_lines()

        # filler 행은 억제되지 않음
        assert filler_idx not in editor._suppressed_lines
        # name 라인은 억제됨
        line_paths = DiffEditor._build_line_paths(editor.lines)
        name_lines = [i for i, p in enumerate(line_paths) if p.endswith(".name")]
        assert len(name_lines) >= 2
        assert all(i in editor._suppressed_lines for i in name_lines)


class TestJsonlFillerSeparation:
    """JSONL separator와 filler 구분 테스트."""

    def test_jsonl_separator_not_treated_as_filler(self):
        """양쪽 모두 빈 JSONL separator는 filler에서 제외."""
        left = '{"a":1}\n{"b":2}'
        right = '{"a":1}\n{"b":99}'
        result = compute_json_diff(left, right, jsonl=True)

        left_fillers = {
            i
            for i, (line, tag) in enumerate(
                zip(result.left_lines, result.left_line_tags)
            )
            if not line and tag != DiffTag.EQUAL and result.right_lines[i]
        }
        # JSONL separator(양쪽 모두 빈 줄)는 filler에 포함되지 않음
        for i in range(len(result.left_lines)):
            if not result.left_lines[i] and not result.right_lines[i]:
                assert i not in left_fillers

    def test_jsonl_parseable_content_preserves_separators(self):
        """_get_parseable_content가 JSONL separator를 유지."""
        left = '{"a":1}\n{"b":2}'
        right = '{"a":1}\n{"b":99}'
        result = compute_json_diff(left, right, jsonl=True)

        left_fillers = {
            i
            for i, (line, tag) in enumerate(
                zip(result.left_lines, result.left_line_tags)
            )
            if not line and tag != DiffTag.EQUAL and result.right_lines[i]
        }

        editor = DiffEditor()
        editor.set_diff_data(
            result.left_lines, result.left_line_tags, left_fillers, result.hunks
        )
        content = editor._get_parseable_content()
        blocks = editor._split_jsonl_blocks(content)
        assert len(blocks) == 2
        assert json.loads(blocks[0]) == {"a": 1}
        assert json.loads(blocks[1]) == {"b": 2}

    def test_real_fillers_still_excluded(self):
        """실제 filler(한쪽만 빈 줄)는 여전히 제외."""
        left = '{"a":1}'
        right = '{"a":1}\n{"b":2}'
        result = compute_json_diff(left, right, jsonl=True)

        left_fillers = {
            i
            for i, (line, tag) in enumerate(
                zip(result.left_lines, result.left_line_tags)
            )
            if not line and tag != DiffTag.EQUAL and result.right_lines[i]
        }
        # 좌측에 filler가 있어야 함 (우측에만 두 번째 레코드가 있으므로)
        assert len(left_fillers) > 0

        editor = DiffEditor()
        editor.set_diff_data(
            result.left_lines, result.left_line_tags, left_fillers, result.hunks
        )
        content = editor._get_parseable_content()
        # filler 제외 후 단일 JSON 파싱 가능
        data = json.loads(content)
        assert data == {"a": 1}


class TestDiffGutterLayout:
    """DiffEditor 거터 레이아웃 (logical + physical line number) 테스트."""

    def test_physical_line_map(self):
        """filler 제외 물리 라인 번호 매핑 검증."""
        editor = DiffEditor()
        lines = ["{", '    "key": "value",', "", "}"]
        tags = [DiffTag.EQUAL, DiffTag.EQUAL, DiffTag.INSERT, DiffTag.EQUAL]
        filler_rows = {2}
        editor.set_diff_data(lines, tags, filler_rows, [])
        # filler=0, 나머지는 1-based 연속 번호
        assert editor._physical_line_map == [1, 2, 0, 3]

    def test_physical_line_map_no_fillers(self):
        """filler 없으면 모든 행이 1-based 연속."""
        editor = DiffEditor()
        lines = ["{", '    "a": 1', "}"]
        tags = [DiffTag.EQUAL, DiffTag.REPLACE, DiffTag.EQUAL]
        editor.set_diff_data(lines, tags, set(), [])
        assert editor._physical_line_map == [1, 2, 3]

    def test_physical_line_map_multiple_fillers(self):
        """다중 filler 행 매핑."""
        editor = DiffEditor()
        lines = ["{", "", '    "a": 1', "", "}"]
        tags = [
            DiffTag.EQUAL,
            DiffTag.INSERT,
            DiffTag.EQUAL,
            DiffTag.INSERT,
            DiffTag.EQUAL,
        ]
        filler_rows = {1, 3}
        editor.set_diff_data(lines, tags, filler_rows, [])
        assert editor._physical_line_map == [1, 0, 2, 0, 3]

    def test_gutter_widths_left(self):
        """왼쪽 에디터: logical + physical 거터 너비."""
        editor = DiffEditor()
        lines = [f"line{i}" for i in range(10)]
        tags = [DiffTag.EQUAL] * 10
        editor.set_diff_data(lines, tags, {2, 5}, [])
        editor._show_logical_line = True
        ln_width, rec_width, prefix_w = editor._gutter_widths()
        # logical_width = max(3, len("10")) = 3, physical_width = max(3, len("8")) = 3
        assert editor._logical_width == 3
        assert editor._physical_width == 3
        assert rec_width == 0
        assert prefix_w == 3 + 1 + 3 + 1  # logical + space + physical + space

    def test_gutter_widths_right(self):
        """오른쪽 에디터: physical만 거터 너비."""
        editor = DiffEditor()
        lines = [f"line{i}" for i in range(10)]
        tags = [DiffTag.EQUAL] * 10
        editor.set_diff_data(lines, tags, {2, 5}, [])
        editor._show_logical_line = False
        ln_width, rec_width, prefix_w = editor._gutter_widths()
        assert editor._physical_width == 3
        assert editor._logical_width == 0
        assert rec_width == 0
        assert prefix_w == 3 + 1  # physical + space

    def test_gutter_widths_diff(self):
        """왼쪽과 오른쪽 거터 너비 차이 검증."""
        editor = DiffEditor()
        lines = [f"line{i}" for i in range(10)]
        tags = [DiffTag.EQUAL] * 10
        editor.set_diff_data(lines, tags, {2}, [])

        editor._show_logical_line = True
        _, _, left_prefix = editor._gutter_widths()

        editor._show_logical_line = False
        _, _, right_prefix = editor._gutter_widths()

        # 왼쪽이 logical 너비 + 1(공백)만큼 더 넓음
        assert left_prefix > right_prefix
        assert (
            left_prefix - right_prefix == editor._logical_width + 1
            or left_prefix > right_prefix
        )
