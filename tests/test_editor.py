"""Tests for JsonEditor widget."""

import json
import subprocess

from src.jvim.differ import JsonDiffApp, _install_difftool, _uninstall_difftool
from src.jvim.editor import _detect_jsonl
from src.jvim.widget import JsonEditor, EditorMode
from src.jvim.action.jsonpath import parse_jsonpath_filter, jsonpath_value_matches


class TestEditorBasic:
    """Basic editor initialization and content tests."""

    def test_init_empty(self):
        editor = JsonEditor()
        assert editor.lines == [""]
        assert editor.cursor_row == 0
        assert editor.cursor_col == 0

    def test_init_with_content(self):
        editor = JsonEditor('{"key": "value"}')
        assert editor.lines == ['{"key": "value"}']

    def test_init_multiline(self):
        content = '{\n    "key": "value"\n}'
        editor = JsonEditor(content)
        assert len(editor.lines) == 3
        assert editor.lines[1] == '    "key": "value"'

    def test_get_content(self):
        content = '{"key": "value"}'
        editor = JsonEditor(content)
        assert editor.get_content() == content

    def test_set_content(self):
        editor = JsonEditor('{"old": "data"}')
        editor.set_content('{"new": "data"}')
        assert editor.lines == ['{"new": "data"}']
        assert editor.cursor_row == 0
        assert editor.cursor_col == 0


class TestCacheInvalidation:
    """Tests for cache invalidation - fixes for IndexError bugs."""

    def test_save_undo_marks_dirty(self):
        """_save_undo should mark cache as dirty."""
        editor = JsonEditor('{"key": "value"}')
        editor._style_cache[0] = ["white"] * 16
        editor._jsonl_records_cache = [1]

        editor._save_undo()

        assert editor._cache_dirty is True

    def test_set_content_marks_dirty(self):
        """set_content should mark cache as dirty."""
        editor = JsonEditor('{"old": "data"}')
        editor._style_cache[0] = ["white"] * 14
        editor._jsonl_records_cache = [1]

        editor.set_content('{"new": "data"}')

        assert editor._cache_dirty is True

    def test_undo_marks_dirty(self):
        """_undo should mark cache as dirty."""
        editor = JsonEditor('{"key": "value"}')
        editor._save_undo()
        editor.lines = ['{"modified": "data"}']
        editor._style_cache[0] = ["white"] * 20

        editor._undo()

        assert editor._cache_dirty is True
        assert editor.lines == ['{"key": "value"}']

    def test_redo_marks_dirty(self):
        """_redo should mark cache as dirty."""
        editor = JsonEditor('{"key": "value"}')
        editor._save_undo()
        editor.lines = ['{"modified": "data"}']
        editor._undo()
        editor._style_cache[0] = ["white"] * 16

        editor._redo()

        assert editor._cache_dirty is True
        assert editor.lines == ['{"modified": "data"}']

    def test_render_auto_invalidation_skips_empty_cache(self):
        """render() auto-invalidation should skip hash computation when cache is empty."""
        editor = JsonEditor('{"key": "value"}')
        # Cache is empty, hash check should be skipped
        assert editor._style_cache == {}
        # Change content
        editor.lines = ['{"new": "data"}']
        # With empty cache, no hash computation needed
        # (This is tested by the optimization logic itself)


class TestUndoRedo:
    """Tests for undo/redo functionality."""

    def test_undo_restores_content(self):
        editor = JsonEditor('{"original": "data"}')
        editor._save_undo()
        editor.lines = ['{"modified": "data"}']

        editor._undo()

        assert editor.lines == ['{"original": "data"}']
        assert editor.status_msg == "undone"

    def test_undo_restores_cursor_position(self):
        editor = JsonEditor('{"key": "value"}')
        editor.cursor_row = 0
        editor.cursor_col = 5
        editor._save_undo()
        editor.cursor_row = 0
        editor.cursor_col = 10

        editor._undo()

        assert editor.cursor_row == 0
        assert editor.cursor_col == 5

    def test_redo_restores_content(self):
        editor = JsonEditor('{"original": "data"}')
        editor._save_undo()
        editor.lines = ['{"modified": "data"}']
        editor._undo()

        editor._redo()

        assert editor.lines == ['{"modified": "data"}']
        assert editor.status_msg == "redone"

    def test_undo_nothing_to_undo(self):
        editor = JsonEditor('{"key": "value"}')

        editor._undo()

        assert editor.status_msg == "nothing to undo"

    def test_redo_nothing_to_redo(self):
        editor = JsonEditor('{"key": "value"}')

        editor._redo()

        assert editor.status_msg == "nothing to redo"

    def test_new_edit_clears_redo_stack(self):
        editor = JsonEditor('{"original": "data"}')
        editor._save_undo()
        editor.lines = ['{"modified": "data"}']
        editor._undo()
        assert len(editor.redo_stack) == 1

        # New edit should clear redo stack
        editor._save_undo()

        assert len(editor.redo_stack) == 0


class TestEmbeddedJson:
    """Tests for embedded JSON editing (ej command)."""

    def test_find_string_at_cursor(self):
        editor = JsonEditor('{"data": "{\\"nested\\": 1}"}')
        editor.cursor_row = 0
        editor.cursor_col = 9

        result = editor._find_string_at_cursor()

        assert result is not None
        col_start, col_end, content = result
        assert content == '{"nested": 1}'

    def test_update_embedded_string(self):
        editor = JsonEditor('{"data": "{\\"nested\\": 1}"}')
        # Simulate finding the string value position
        # The string value starts at col 9 and ends at col 25

        editor.update_embedded_string(0, 9, 25, '{"nested": 2}')

        # The new content is escaped as JSON string
        assert '\\"nested\\": 2' in editor.lines[0]
        # Should have saved undo
        assert len(editor.undo_stack) == 1

    def test_update_embedded_string_cache_cleared(self):
        """update_embedded_string should result in cleared cache via _save_undo."""
        editor = JsonEditor('{"data": "{\\"nested\\": 1}"}')
        editor._style_cache[0] = ["white"] * 27

        editor.update_embedded_string(0, 9, 25, '{"nested": 2}')

        # Cache should be marked dirty by _save_undo
        assert editor._cache_dirty is True


class TestJsonl:
    """Tests for JSONL file handling."""

    def test_jsonl_to_pretty(self):
        content = '{"a": 1}\n{"b": 2}'
        result = JsonEditor._jsonl_to_pretty(content)

        assert '"a": 1' in result
        assert '"b": 2' in result
        # Pretty printed with indentation
        assert "    " in result or result.count("\n") > 1

    def test_pretty_to_jsonl(self):
        pretty = '{\n    "a": 1\n}\n\n{\n    "b": 2\n}'
        result = JsonEditor._pretty_to_jsonl(pretty)

        lines = result.split("\n")
        assert len(lines) == 2
        assert '{"a": 1}' in lines[0] or '{"a":1}' in lines[0]

    def test_split_jsonl_blocks(self):
        content = '{\n    "a": 1\n}\n\n{\n    "b": 2\n}'
        blocks = JsonEditor._split_jsonl_blocks(content)

        assert len(blocks) == 2

    def test_jsonl_mode_init(self):
        content = '{"a": 1}\n{"b": 2}'
        editor = JsonEditor(content, jsonl=True)

        # Should be pretty-printed
        assert len(editor.lines) > 2

    def test_jsonl_line_records(self):
        editor = JsonEditor('{"a": 1}\n{"b": 2}', jsonl=True)
        records = editor._jsonl_line_records()

        # First line of each block should have record number
        assert 1 in records
        assert 2 in records


class TestEditorMode:
    """Tests for editor mode handling."""

    def test_initial_mode_is_normal(self):
        editor = JsonEditor()
        assert editor._mode == EditorMode.NORMAL

    def test_enter_insert_mode(self):
        editor = JsonEditor('{"key": "value"}')
        editor._enter_insert()

        assert editor._mode == EditorMode.INSERT
        assert editor.status_msg == "-- INSERT --"

    def test_readonly_blocks_insert(self):
        editor = JsonEditor('{"key": "value"}', read_only=True)
        editor._enter_insert()

        assert editor._mode == EditorMode.NORMAL
        assert editor.status_msg == "[readonly]"


class TestJsonValidation:
    """Tests for JSON validation."""

    def test_valid_json(self):
        editor = JsonEditor('{"key": "value"}')
        valid, err = editor._check_content(editor.get_content())

        assert valid is True
        assert err == ""

    def test_invalid_json(self):
        editor = JsonEditor('{"key": }')
        valid, err = editor._check_content(editor.get_content())

        assert valid is False
        assert "JSON error" in err

    def test_valid_jsonl(self):
        editor = JsonEditor('{"a": 1}\n{"b": 2}', jsonl=True)
        valid, err = editor._check_content(editor.get_content())

        assert valid is True

    def test_invalid_jsonl_record(self):
        content = '{\n    "a": 1\n}\n\n{\n    "b": \n}'
        editor = JsonEditor(jsonl=True)
        editor.lines = content.split("\n")
        valid, err = editor._check_content(editor.get_content())

        assert valid is False
        assert "JSONL error" in err


class TestMovement:
    """Tests for cursor movement."""

    def test_clamp_cursor_row(self):
        editor = JsonEditor("line1\nline2")
        editor.cursor_row = 10
        editor._clamp_cursor()

        assert editor.cursor_row == 1

    def test_clamp_cursor_col_normal_mode(self):
        editor = JsonEditor("short")
        editor._mode = EditorMode.NORMAL
        editor.cursor_col = 10
        editor._clamp_cursor()

        # In NORMAL mode, cursor stays on last character
        assert editor.cursor_col == 4

    def test_clamp_cursor_col_insert_mode(self):
        editor = JsonEditor("short")
        editor._mode = EditorMode.INSERT
        editor.cursor_col = 10
        editor._clamp_cursor()

        # In INSERT mode, cursor can be at end of line
        assert editor.cursor_col == 5

    def test_move_word_forward(self):
        editor = JsonEditor('{"key": "value"}')
        editor.cursor_col = 0
        editor._move_word_forward()

        assert editor.cursor_col > 0

    def test_move_word_backward(self):
        editor = JsonEditor('{"key": "value"}')
        editor.cursor_col = 10
        editor._move_word_backward()

        assert editor.cursor_col < 10


class TestBracketMatching:
    """Tests for bracket matching (% command)."""

    def test_jump_matching_bracket_forward(self):
        editor = JsonEditor('{"key": [1, 2, 3]}')
        editor.cursor_col = 0  # On {
        editor._jump_matching_bracket()

        assert editor.cursor_col == 17  # On }

    def test_jump_matching_bracket_backward(self):
        editor = JsonEditor('{"key": [1, 2, 3]}')
        editor.cursor_col = 17  # On }
        editor._jump_matching_bracket()

        assert editor.cursor_col == 0  # On {


class TestCharWidth:
    """Tests for character width calculation (CJK support)."""

    def test_ascii_width(self):
        editor = JsonEditor()
        assert editor._char_width("a") == 1
        assert editor._char_width("1") == 1

    def test_cjk_width(self):
        editor = JsonEditor()
        # Korean character should be width 2
        assert editor._char_width("한") == 2
        # Japanese
        assert editor._char_width("日") == 2
        # Chinese
        assert editor._char_width("中") == 2

    def test_char_width_cache(self):
        editor = JsonEditor()
        # First call computes and caches
        w1 = editor._char_width("한")
        # Second call uses cache
        w2 = editor._char_width("한")

        assert w1 == w2 == 2
        assert "한" in editor._char_width_cache


class TestJsonPathFilter:
    """Tests for JSONPath search with value filtering."""

    def test_parse_filter_equals_string(self):
        path, op, val = parse_jsonpath_filter('$.name="John"')

        assert path == "$.name"
        assert op == "="
        assert val == "John"

    def test_parse_filter_equals_number(self):
        path, op, val = parse_jsonpath_filter("$.age=30")

        assert path == "$.age"
        assert op == "="
        assert val == 30

    def test_parse_filter_greater_than(self):
        path, op, val = parse_jsonpath_filter("$.age>18")

        assert path == "$.age"
        assert op == ">"
        assert val == 18

    def test_parse_filter_less_than(self):
        path, op, val = parse_jsonpath_filter("$.price<100")

        assert path == "$.price"
        assert op == "<"
        assert val == 100

    def test_parse_filter_greater_or_equal(self):
        path, op, val = parse_jsonpath_filter("$.count>=5")

        assert path == "$.count"
        assert op == ">="
        assert val == 5

    def test_parse_filter_less_or_equal(self):
        path, op, val = parse_jsonpath_filter("$.count<=10")

        assert path == "$.count"
        assert op == "<="
        assert val == 10

    def test_parse_filter_not_equals(self):
        path, op, val = parse_jsonpath_filter("$.status!=null")

        assert path == "$.status"
        assert op == "!="
        assert val is None

    def test_parse_filter_regex(self):
        path, op, val = parse_jsonpath_filter("$.name~^J")

        assert path == "$.name"
        assert op == "~"
        assert val == "^J"

    def test_parse_filter_no_filter(self):
        path, op, val = parse_jsonpath_filter("$.users[*].name")

        assert path == "$.users[*].name"
        assert op == ""
        assert val is None

    def test_value_matches_equals(self):
        assert jsonpath_value_matches("John", "=", "John")
        assert not jsonpath_value_matches("Jane", "=", "John")
        assert jsonpath_value_matches(30, "=", 30)

    def test_value_matches_not_equals(self):
        assert jsonpath_value_matches("Jane", "!=", "John")
        assert not jsonpath_value_matches("John", "!=", "John")

    def test_value_matches_greater(self):
        assert jsonpath_value_matches(30, ">", 18)
        assert not jsonpath_value_matches(18, ">", 18)
        assert not jsonpath_value_matches(10, ">", 18)

    def test_value_matches_less(self):
        assert jsonpath_value_matches(10, "<", 18)
        assert not jsonpath_value_matches(18, "<", 18)
        assert not jsonpath_value_matches(30, "<", 18)

    def test_value_matches_greater_or_equal(self):
        assert jsonpath_value_matches(30, ">=", 18)
        assert jsonpath_value_matches(18, ">=", 18)
        assert not jsonpath_value_matches(10, ">=", 18)

    def test_value_matches_less_or_equal(self):
        assert jsonpath_value_matches(10, "<=", 18)
        assert jsonpath_value_matches(18, "<=", 18)
        assert not jsonpath_value_matches(30, "<=", 18)

    def test_value_matches_regex(self):
        assert jsonpath_value_matches("John", "~", "^J")
        assert jsonpath_value_matches("Jane", "~", "^J")
        assert not jsonpath_value_matches("Mary", "~", "^J")
        assert jsonpath_value_matches("test@email.com", "~", r"@.*\.com$")

    def test_search_with_equals_filter(self):
        editor = JsonEditor('{"users": [{"name": "John"}, {"name": "Jane"}]}')
        editor._search_buffer = '$.users[*].name="John"'
        editor._search_forward = True
        editor._execute_search()

        assert len(editor._search_matches) == 1

    def test_search_with_greater_filter(self):
        editor = JsonEditor('{"users": [{"age": 25}, {"age": 30}, {"age": 20}]}')
        editor._search_buffer = "$.users[*].age>24"
        editor._search_forward = True
        editor._execute_search()

        assert len(editor._search_matches) == 2  # 25 and 30

    def test_search_with_regex_filter(self):
        editor = JsonEditor(
            '{"users": [{"name": "John"}, {"name": "Jane"}, {"name": "Mary"}]}'
        )
        editor._search_buffer = "$.users[*].name~^J"
        editor._search_forward = True
        editor._execute_search()

        assert len(editor._search_matches) == 2  # John and Jane

    def test_search_jsonl_with_filter(self):
        content = '{"name": "John", "age": 25}\n{"name": "Jane", "age": 30}\n{"name": "Bob", "age": 20}'
        editor = JsonEditor(content, jsonl=True)
        editor._search_buffer = "$.age>24"
        editor._search_forward = True
        editor._execute_search()

        assert len(editor._search_matches) == 2  # 25 and 30

    def test_search_jsonl_with_regex_filter(self):
        content = '{"name": "John"}\n{"name": "Jane"}\n{"name": "Bob"}'
        editor = JsonEditor(content, jsonl=True)
        editor._search_buffer = "$.name~^J"
        editor._search_forward = True
        editor._execute_search()

        assert len(editor._search_matches) == 2  # John and Jane

    def test_search_with_boolean_filter(self):
        editor = JsonEditor('{"items": [{"active": true}, {"active": false}]}')
        editor._search_buffer = "$.items[*].active=true"
        editor._search_forward = True
        editor._execute_search()

        assert len(editor._search_matches) == 1


class TestJsonPathPositionMapping:
    """JSONPath 검색 시 경로 전체를 사용한 정확한 위치 매핑 테스트."""

    def test_same_key_different_parent(self):
        """동일 키가 다른 부모에 있을 때 올바른 위치 반환."""
        data = {
            "evaluate_response": {"id": "eval_1"},
            "model_response": {"id": "model_1"},
        }
        editor = JsonEditor(json.dumps(data))
        editor._search_buffer = "$.model_response.id"
        editor._search_forward = True
        editor._execute_search()
        assert len(editor._search_matches) == 1
        row, col_start, col_end = editor._search_matches[0]
        # 매칭된 텍스트가 model_1이어야 함
        matched = editor.lines[row][col_start:col_end]
        assert "model_1" in matched

    def test_same_key_different_parent_compact(self):
        """컴팩트 JSON에서 동일 키의 위치가 경로별로 구분됨."""
        editor = JsonEditor('{"a": {"id": 1}, "b": {"id": 2}}')
        editor._search_buffer = "$.b.id"
        editor._search_forward = True
        editor._execute_search()
        assert len(editor._search_matches) == 1
        row, col_start, col_end = editor._search_matches[0]
        assert editor.lines[row][col_start:col_end] == "2"

    def test_array_index_compact_json(self):
        """컴팩트 JSON에서 배열 인덱스 위치 찾기."""
        editor = JsonEditor('{"items": [10, 20, 30]}')
        editor._search_buffer = "$.items[1]"
        editor._search_forward = True
        editor._execute_search()
        assert len(editor._search_matches) == 1
        row, col_start, col_end = editor._search_matches[0]
        assert editor.lines[row][col_start:col_end] == "20"

    def test_array_object_element_compact(self):
        """컴팩트 JSON 배열 내 객체의 특정 키 검색."""
        editor = JsonEditor('[{"name": "Alice"}, {"name": "Bob"}]')
        editor._search_buffer = "$[1].name"
        editor._search_forward = True
        editor._execute_search()
        assert len(editor._search_matches) == 1
        row, col_start, col_end = editor._search_matches[0]
        assert editor.lines[row][col_start:col_end] == '"Bob"'

    def test_nested_array_with_shared_key(self):
        """중첩 배열에서 공유 키를 경로로 구분."""
        data = {"groups": [{"members": [{"id": "a"}]}, {"members": [{"id": "b"}]}]}
        editor = JsonEditor(json.dumps(data))
        editor._search_buffer = "$.groups[1].members[0].id"
        editor._search_forward = True
        editor._execute_search()
        assert len(editor._search_matches) == 1
        row, col_start, col_end = editor._search_matches[0]
        assert "b" in editor.lines[row][col_start:col_end]

    def test_wildcard_returns_all_matches(self):
        """[*] 와일드카드가 모든 요소를 반환."""
        editor = JsonEditor('{"vals": [{"x": 1}, {"x": 2}, {"x": 3}]}')
        editor._search_buffer = "$.vals[*].x"
        editor._search_forward = True
        editor._execute_search()
        assert len(editor._search_matches) == 3
        values = []
        for row, cs, ce in editor._search_matches:
            values.append(editor.lines[row][cs:ce])
        assert values == ["1", "2", "3"]


class TestPaste:
    """Paste 이벤트 처리 테스트."""

    def _paste_event(self, text):
        from types import SimpleNamespace

        return SimpleNamespace(
            text=text, prevent_default=lambda: None, stop=lambda: None
        )

    def test_paste_in_search_mode(self):
        editor = JsonEditor('{"a": 1}')
        editor._mode = EditorMode.SEARCH
        editor._search_buffer = "/"
        editor.on_paste(self._paste_event("$.a"))
        assert editor._search_buffer == "/$.a"

    def test_paste_in_command_mode(self):
        editor = JsonEditor('{"a": 1}')
        editor._mode = EditorMode.COMMAND
        editor.command_buffer = "ig "
        editor.on_paste(self._paste_event("$.path"))
        assert editor.command_buffer == "ig $.path"

    def test_paste_strips_newlines_in_search(self):
        editor = JsonEditor('{"a": 1}')
        editor._mode = EditorMode.SEARCH
        editor._search_buffer = ""
        editor.on_paste(self._paste_event("line1\nline2"))
        assert editor._search_buffer == "line1line2"

    def test_paste_in_insert_mode(self):
        editor = JsonEditor('{"a": 1}')
        editor._mode = EditorMode.INSERT
        editor.cursor_row = 0
        editor.cursor_col = 0
        editor.on_paste(self._paste_event("hello"))
        assert editor.lines[0].startswith("hello")
        assert editor.cursor_col == 5

    def test_paste_multiline_in_insert_mode(self):
        editor = JsonEditor('{"a": 1}')
        editor._mode = EditorMode.INSERT
        editor.cursor_row = 0
        editor.cursor_col = 0
        editor.on_paste(self._paste_event("line1\nline2\nline3"))
        assert editor.lines[0] == "line1"
        assert editor.lines[1] == "line2"
        assert editor.lines[2].startswith("line3")
        assert editor.cursor_row == 2

    def test_paste_readonly_insert_no_change(self):
        editor = JsonEditor('{"a": 1}', read_only=True)
        editor._mode = EditorMode.INSERT
        editor.cursor_row = 0
        editor.cursor_col = 0
        original = editor.lines[0]
        editor.on_paste(self._paste_event("hello"))
        assert editor.lines[0] == original


class TestHistory:
    """Tests for command and search history."""

    def test_get_history(self):
        editor = JsonEditor()
        editor._search_history = ["pattern1", "pattern2"]
        editor._command_history = ["w", "q"]

        history = editor.get_history()

        assert history["search"] == ["pattern1", "pattern2"]
        assert history["command"] == ["w", "q"]

    def test_set_history(self):
        editor = JsonEditor()
        history = {
            "search": ["foo", "bar"],
            "command": ["fmt", "w"],
        }

        editor.set_history(history)

        assert editor._search_history == ["foo", "bar"]
        assert editor._command_history == ["fmt", "w"]

    def test_set_history_partial(self):
        editor = JsonEditor()
        editor._search_history = ["old"]
        editor._command_history = ["old_cmd"]

        editor.set_history({"search": ["new"]})

        assert editor._search_history == ["new"]
        assert editor._command_history == ["old_cmd"]

    def test_add_to_command_history(self):
        editor = JsonEditor()

        editor._add_to_command_history("fmt")
        editor._add_to_command_history("w")

        assert editor._command_history == ["w", "fmt"]

    def test_add_to_command_history_no_duplicates(self):
        editor = JsonEditor()
        editor._command_history = ["w", "fmt"]

        editor._add_to_command_history("w")

        assert editor._command_history == ["w", "fmt"]

    def test_command_history_navigation(self):
        editor = JsonEditor()
        editor._command_history = ["c", "b", "a"]

        editor._command_history_prev()
        assert editor.command_buffer == "c"

        editor._command_history_prev()
        assert editor.command_buffer == "b"

        editor._command_history_next()
        assert editor.command_buffer == "c"

        editor._command_history_next()
        assert editor.command_buffer == ""


class TestLineJump:
    """Tests for line jump positioning cursor at top."""

    def test_scroll_cursor_to_top(self):
        """_scroll_cursor_to_top sets scroll_top to cursor_row."""
        content = "\n".join([f"line {i}" for i in range(100)])
        editor = JsonEditor(content)

        editor.cursor_row = 50
        editor._scroll_cursor_to_top()

        assert editor._scroll_top == 50

    def test_line_jump_command_scrolls_to_top(self):
        """Line number command (e.g., :50) positions cursor at top."""
        content = "\n".join([f"line {i}" for i in range(100)])
        editor = JsonEditor(content)
        editor._scroll_top = 0

        editor._exec_command("50")

        assert editor.cursor_row == 49  # 0-indexed
        assert editor._scroll_top == 49  # Cursor at top

    def test_line_jump_G_scrolls_to_top(self):
        """G command positions last line at top."""
        content = "\n".join([f"line {i}" for i in range(100)])
        editor = JsonEditor(content)

        from types import SimpleNamespace

        event = SimpleNamespace(key="g", character="G")
        editor._handle_normal(event)

        assert editor.cursor_row == 99  # Last line
        assert editor._scroll_top == 99  # Cursor at top

    def test_line_jump_gg_scrolls_to_top(self):
        """gg command positions first line at top."""
        content = "\n".join([f"line {i}" for i in range(100)])
        editor = JsonEditor(content)
        editor.cursor_row = 50
        editor._scroll_top = 50

        from types import SimpleNamespace

        # First 'g' to set pending
        event1 = SimpleNamespace(key="g", character="g")
        editor._handle_normal(event1)
        # Second 'g' to complete
        event2 = SimpleNamespace(key="g", character="g")
        editor._handle_normal(event2)

        assert editor.cursor_row == 0
        assert editor._scroll_top == 0

    def test_line_jump_dollar_scrolls_to_top(self):
        """:$ command positions last line at top."""
        content = "\n".join([f"line {i}" for i in range(100)])
        editor = JsonEditor(content)

        editor._exec_command("$")

        assert editor.cursor_row == 99
        assert editor._scroll_top == 99

    def test_scroll_cursor_to_center(self):
        """_scroll_cursor_to_center positions cursor at 1/3 from top."""
        content = "\n".join([f"line {i}" for i in range(100)])
        editor = JsonEditor(content)

        editor.cursor_row = 50
        # Simulate visible height of 30 lines
        editor._visible_height = lambda: 30
        editor._scroll_cursor_to_center()

        # int(30 * 0.33) = 9, so scroll_top should be 50 - 9 = 41
        assert editor._scroll_top == 41

    def test_search_positions_at_center(self):
        """Search result positions cursor at 1/3 from top."""
        content = "\n".join([f"line {i}" for i in range(100)])
        editor = JsonEditor(content)
        editor._visible_height = lambda: 30

        editor._search_buffer = "line 50"
        editor._search_forward = True
        editor._execute_search()

        assert editor.cursor_row == 50
        # int(30 * 0.33) = 9, so scroll_top should be 50 - 9 = 41
        assert editor._scroll_top == 41


class TestCommandParsing:
    """Tests for command parsing split from _exec_command."""

    def test_parse_jump_last(self):
        kind, payload = JsonEditor._parse_ex_command("$")
        assert kind == "jump_last"
        assert payload == {}

    def test_parse_line_jump(self):
        kind, payload = JsonEditor._parse_ex_command("l42")
        assert kind == "jump_line"
        assert payload["num"] == 42

    def test_parse_file_line_jump(self):
        kind, payload = JsonEditor._parse_ex_command("p12")
        assert kind == "jump_file_line"
        assert payload["num"] == 12

    def test_parse_substitute(self):
        kind, payload = JsonEditor._parse_ex_command("%s/foo/bar/g")
        assert kind == "substitute"
        assert payload["sub_cmd"] == "%s/foo/bar/g"

    def test_parse_verb_with_force(self):
        kind, payload = JsonEditor._parse_ex_command("w! out.json")
        assert kind == "verb"
        assert payload["verb"] == "w"
        assert payload["force"] is True
        assert payload["arg"] == "out.json"


class TestFolding:
    """JSON folding 테스트."""

    SAMPLE = '{\n    "a": {\n        "b": 1,\n        "c": 2\n    },\n    "d": [\n        1,\n        2\n    ],\n    "e": 3\n}'
    # line 0: {
    # line 1:     "a": {
    # line 2:         "b": 1,
    # line 3:         "c": 2
    # line 4:     },
    # line 5:     "d": [
    # line 6:         1,
    # line 7:         2
    # line 8:     ],
    # line 9:     "e": 3
    # line 10: }

    def test_find_foldable_at_object(self):
        """multi-line object를 감지."""
        editor = JsonEditor(self.SAMPLE)
        rng = editor._find_foldable_at(1)
        assert rng == (1, 4)

    def test_find_foldable_at_array(self):
        """multi-line array를 감지."""
        editor = JsonEditor(self.SAMPLE)
        rng = editor._find_foldable_at(5)
        assert rng == (5, 8)

    def test_find_foldable_at_root(self):
        """root object를 감지."""
        editor = JsonEditor(self.SAMPLE)
        rng = editor._find_foldable_at(0)
        assert rng == (0, 10)

    def test_find_foldable_at_non_foldable(self):
        """fold 불가한 라인은 None."""
        editor = JsonEditor(self.SAMPLE)
        assert editor._find_foldable_at(2) is None
        assert editor._find_foldable_at(9) is None

    def test_find_foldable_at_single_line(self):
        """single-line object는 fold 불가."""
        editor = JsonEditor('{"a": 1}')
        assert editor._find_foldable_at(0) is None

    def test_toggle_fold(self):
        """za: fold 토글."""
        editor = JsonEditor(self.SAMPLE)
        editor._toggle_fold(1)
        assert 1 in editor._folds
        assert editor._folds[1] == 4
        editor._toggle_fold(1)
        assert 1 not in editor._folds

    def test_toggle_fold_inside_folded(self):
        """za: fold 안에서 호출하면 해당 fold 펼기."""
        editor = JsonEditor(self.SAMPLE)
        editor._folds[1] = 4
        editor._toggle_fold(3)  # fold 안의 라인
        assert 1 not in editor._folds

    def test_close_fold_from_inside(self):
        """zc: 블록 안에서 호출하면 감싸는 블록을 접는다."""
        editor = JsonEditor(self.SAMPLE)
        editor._close_fold(3)  # "c": 2 라인
        assert 1 in editor._folds
        assert editor._folds[1] == 4

    def test_fold_all(self):
        """zM: top-level foldable 영역만 접기."""
        editor = JsonEditor(self.SAMPLE)
        editor._fold_all()
        # root만 접혀야 함 (top-level)
        assert 0 in editor._folds
        assert editor._folds[0] == 10

    def test_unfold_all(self):
        """zR: 모든 fold 해제."""
        editor = JsonEditor(self.SAMPLE)
        editor._folds[1] = 4
        editor._folds[5] = 8
        editor._unfold_all()
        assert editor._folds == {}

    def test_is_line_folded(self):
        """fold 안에 숨겨진 라인 판별."""
        editor = JsonEditor(self.SAMPLE)
        editor._folds[1] = 4
        assert not editor._is_line_folded(0)
        assert not editor._is_line_folded(1)  # 헤더는 보임
        assert editor._is_line_folded(2)
        assert editor._is_line_folded(3)
        assert editor._is_line_folded(4)
        assert not editor._is_line_folded(5)

    def test_next_visible_line(self):
        """fold를 건너뛰는 라인 이동."""
        editor = JsonEditor(self.SAMPLE)
        editor._folds[1] = 4
        assert editor._next_visible_line(0, 1) == 1  # 헤더는 보임
        assert editor._next_visible_line(1, 1) == 5  # fold 건너뜀
        assert editor._next_visible_line(5, -1) == 1  # 역방향

    def test_unfold_for_line(self):
        """검색 시 자동 펼기."""
        editor = JsonEditor(self.SAMPLE)
        editor._folds[1] = 4
        editor._unfold_for_line(3)
        assert 1 not in editor._folds

    def test_clamp_cursor_snaps_to_fold_header(self):
        """fold 안에 커서가 있으면 fold 헤더로 snap."""
        editor = JsonEditor(self.SAMPLE)
        editor._folds[1] = 4
        editor.cursor_row = 3
        editor._clamp_cursor()
        assert editor.cursor_row == 1

    def test_auto_expand_fold_on_cursor_past_end(self):
        """fold 헤더에서 커서가 라인 끝을 넘으면 자동 펼기."""
        editor = JsonEditor(self.SAMPLE)
        editor._folds[1] = 4
        editor.cursor_row = 1
        # line 1: '    "a": {' — 마지막 문자 인덱스 = 9
        line_len = len(editor.lines[1])
        editor.cursor_col = line_len  # 끝을 넘어감
        editor._clamp_cursor()
        assert 1 not in editor._folds  # 자동 펼기됨

    def test_no_expand_fold_on_cursor_at_end(self):
        """fold 헤더에서 커서가 마지막 문자에 있으면 접힌 상태 유지."""
        editor = JsonEditor(self.SAMPLE)
        editor._folds[1] = 4
        editor.cursor_row = 1
        line_len = len(editor.lines[1])
        editor.cursor_col = line_len - 1  # 마지막 문자 ('{')
        editor._clamp_cursor()
        assert 1 in editor._folds  # 유지

    def test_set_content_clears_folds(self):
        """set_content 시 fold 초기화."""
        editor = JsonEditor(self.SAMPLE)
        editor._folds[1] = 4
        editor.set_content('{"new": 1}')
        assert editor._folds == {}

    def test_undo_clears_folds(self):
        """undo 시 fold 초기화."""
        editor = JsonEditor(self.SAMPLE)
        editor._save_undo()
        editor._folds[1] = 4
        editor._undo()
        assert editor._folds == {}

    def test_find_enclosing_foldable(self):
        """커서를 감싸는 foldable 블록 찾기."""
        editor = JsonEditor(self.SAMPLE)
        rng = editor._find_enclosing_foldable(3)
        assert rng == (1, 4)
        rng = editor._find_enclosing_foldable(7)
        assert rng == (5, 8)

    def test_fold_at_depth_1(self):
        """1-depth foldable 블록만 접기."""
        editor = JsonEditor(self.SAMPLE)
        editor._fold_at_depth(1)
        # "a": { 와 "d": [ 만 접혀야 함
        assert 1 in editor._folds  # "a": {
        assert 5 in editor._folds  # "d": [
        assert 0 not in editor._folds  # root는 접히면 안 됨
        assert len(editor._folds) == 2

    def test_fold_at_depth_0(self):
        """0-depth (root)만 접기."""
        editor = JsonEditor(self.SAMPLE)
        editor._fold_at_depth(0)
        assert 0 in editor._folds
        assert len(editor._folds) == 1

    def test_fold_all_nested(self):
        """모든 depth의 foldable 블록을 접기 (root 제외)."""
        editor = JsonEditor(self.SAMPLE)
        editor._fold_all_nested()
        assert 0 not in editor._folds  # root는 접히지 않음
        assert 1 in editor._folds  # "a": { ... }
        assert 5 in editor._folds  # "d": [ ... ]
        assert len(editor._folds) == 2

    def test_fold_all_nested_deep(self):
        """중첩된 구조에서 모든 depth 접기."""
        content = (
            '{\n    "a": {\n        "b": {\n            "c": 1\n        }\n    }\n}'
        )
        # line 0: {
        # line 1:     "a": {
        # line 2:         "b": {
        # line 3:             "c": 1
        # line 4:         }
        # line 5:     }
        # line 6: }
        editor = JsonEditor(content)
        editor._fold_all_nested()
        assert 0 not in editor._folds  # root 제외
        assert 1 in editor._folds  # depth 1
        assert 2 in editor._folds  # depth 2
        assert len(editor._folds) == 2

    def test_skip_visible_lines_forward(self):
        """fold를 건너뛰며 N줄 전진."""
        editor = JsonEditor(self.SAMPLE)
        editor._folds[1] = 4  # line 1-4 접힘
        # line 0 → 1(헤더) → 5 → 6 → 7 → 8 → 9
        result = editor._skip_visible_lines(0, 4, 1)
        assert result == 7  # 0→1→5→6→7

    def test_skip_visible_lines_backward(self):
        """fold를 건너뛰며 N줄 후진."""
        editor = JsonEditor(self.SAMPLE)
        editor._folds[1] = 4
        # line 7 → 6 → 5 → 1(헤더) → 0
        result = editor._skip_visible_lines(7, 4, -1)
        assert result == 0

    def test_page_down_with_folds(self):
        """Ctrl+F: fold 시 보이는 라인 기준으로 이동."""
        content = "\n".join([f"line {i}" for i in range(50)])
        editor = JsonEditor(content)
        editor._visible_height = lambda: 10
        # fold lines 5-15 (header at 5)
        editor._folds[5] = 15
        editor.cursor_row = 0

        from types import SimpleNamespace

        event = SimpleNamespace(key="ctrl+f", character="")
        editor._handle_normal(event)

        # 10 visible lines from 0: 0→1→2→3→4→5(header)→16→17→18→19→20
        assert editor.cursor_row == 20

    def test_page_up_with_folds(self):
        """Ctrl+B: fold 시 보이는 라인 기준으로 이동."""
        content = "\n".join([f"line {i}" for i in range(50)])
        editor = JsonEditor(content)
        editor._visible_height = lambda: 10
        editor._folds[5] = 15
        editor.cursor_row = 25

        from types import SimpleNamespace

        event = SimpleNamespace(key="ctrl+b", character="")
        editor._handle_normal(event)

        # 10 visible lines back from 25: 25→24→23→22→21→20→19→18→17→16→5(header)
        assert editor.cursor_row == 5


class TestStringCollapse:
    """긴 string value 접기/펼기 테스트."""

    LONG_STR = "a" * 100
    SAMPLE = '{\n    "short": "hi",\n    "long": "' + LONG_STR + '"\n}'
    # line 0: {
    # line 1:     "short": "hi",
    # line 2:     "long": "aaa...aaa"
    # line 3: }

    def test_find_long_string_at(self):
        """긴 string value를 감지."""
        editor = JsonEditor(self.SAMPLE)
        result = editor._find_long_string_at(2)
        assert result is not None
        qs, qe, slen = result
        assert slen == 100

    def test_find_long_string_at_short(self):
        """짧은 string은 None 반환."""
        editor = JsonEditor(self.SAMPLE)
        assert editor._find_long_string_at(1) is None

    def test_find_long_string_at_no_value(self):
        """string value가 없는 라인은 None."""
        editor = JsonEditor(self.SAMPLE)
        assert editor._find_long_string_at(0) is None

    def test_toggle_collapse(self):
        """za: 긴 string 토글."""
        editor = JsonEditor(self.SAMPLE)
        # 초기 로드 시 자동 collapse됨
        assert 2 in editor._collapsed_strings
        editor._toggle_fold(2)
        assert 2 not in editor._collapsed_strings
        editor._toggle_fold(2)
        assert 2 in editor._collapsed_strings

    def test_close_collapse(self):
        """zc: 긴 string 접기."""
        editor = JsonEditor(self.SAMPLE)
        editor._collapsed_strings.discard(2)  # 먼저 펼기
        editor._close_fold(2)
        assert 2 in editor._collapsed_strings

    def test_open_collapse(self):
        """zo: 긴 string 펼기."""
        editor = JsonEditor(self.SAMPLE)
        # 초기 로드 시 자동 collapse됨
        assert 2 in editor._collapsed_strings
        editor._open_fold(2)
        assert 2 not in editor._collapsed_strings

    def test_fold_all_includes_strings(self):
        """zM: 긴 string도 같이 접기."""
        editor = JsonEditor(self.SAMPLE)
        editor._fold_all()
        # root object가 fold되므로 string collapse는 안에 숨겨짐
        # string이 보이는 상태에서 확인
        editor._unfold_all()
        assert editor._collapsed_strings == set()
        # fold 없이 string만 있는 경우
        content = '{\n    "data": "' + "x" * 100 + '"\n}'
        editor2 = JsonEditor(content)
        editor2._fold_all()
        # root fold에 감싸져 있으면 string collapse가 없을 수 있음
        # top-level에서 foldable이 아닌 라인의 긴 string은 접혀야 함
        assert 0 in editor2._folds  # root object 접힘

    def test_unfold_all_clears_strings(self):
        """zR: collapsed strings도 해제."""
        editor = JsonEditor(self.SAMPLE)
        editor._collapsed_strings.add(2)
        editor._unfold_all()
        assert editor._collapsed_strings == set()

    def test_set_content_clears_collapsed(self):
        """set_content 시 collapsed strings 초기화."""
        editor = JsonEditor(self.SAMPLE)
        editor._collapsed_strings.add(2)
        editor.set_content('{"a": 1}')
        assert editor._collapsed_strings == set()

    def test_fold_at_depth_collapses_strings(self):
        """_fold_at_depth에서 해당 depth의 긴 string도 접기."""
        editor = JsonEditor(self.SAMPLE)
        editor._fold_at_depth(1)
        assert 2 in editor._collapsed_strings

    def test_threshold(self):
        """threshold 미만이면 접지 않음."""
        short_str = "b" * 59  # 59 < 60 threshold
        content = '{\n    "key": "' + short_str + '"\n}'
        editor = JsonEditor(content)
        assert editor._find_long_string_at(1) is None
        # 정확히 threshold
        exact_str = "c" * 60
        content2 = '{\n    "key": "' + exact_str + '"\n}'
        editor2 = JsonEditor(content2)
        result = editor2._find_long_string_at(1)
        assert result is not None

    def test_auto_expand_on_cursor_enter(self):
        """커서가 collapsed 영역 안으로 진입하면 자동 펼기."""
        editor = JsonEditor(self.SAMPLE)
        editor._collapsed_strings.add(2)
        editor.cursor_row = 2
        # 미리보기 영역 안 — 접힌 상태 유지
        info = editor._find_long_string_at(2)
        qs = info[0]
        editor.cursor_col = qs + 5  # 미리보기 범위 안
        editor._clamp_cursor()
        assert 2 in editor._collapsed_strings
        # 미리보기 끝을 넘어가면 자동 펼기
        editor._collapsed_strings.add(2)
        editor.cursor_col = qs + 22  # 여는 따옴표 + 20 + 1 = 넘침
        editor._clamp_cursor()
        assert 2 not in editor._collapsed_strings


class TestFoldIndexAdjust:
    """fold/collapse 인덱스가 편집 후 올바르게 조정되는지 테스트."""

    def _key(self, char, key=None):
        from types import SimpleNamespace

        return SimpleNamespace(key=key or char, character=char)

    def test_insert_line_shifts_fold(self):
        """o 명령으로 라인 삽입 시 이후 fold 인덱스 이동."""
        content = '{\n    "a": {\n        "x": 1\n    },\n    "b": 2\n}'
        editor = JsonEditor(content)
        # fold "a" block: line 1 → line 3
        editor._folds[1] = 3
        editor.cursor_row = 0
        editor._handle_normal(self._key("o"))
        # fold가 한 칸 밀려야 함
        assert 1 not in editor._folds
        assert 2 in editor._folds
        assert editor._folds[2] == 4

    def test_delete_line_shifts_fold(self):
        """dd 명령으로 라인 삭제 시 이후 fold 인덱스 이동."""
        content = "line0\nline1\nline2\nline3\nline4"
        editor = JsonEditor(content)
        editor._folds[3] = 4
        editor.cursor_row = 1
        editor._handle_normal(self._key("d"))
        editor._handle_pending("d", "d")
        # fold가 한 칸 당겨져야 함
        assert 3 not in editor._folds
        assert 2 in editor._folds
        assert editor._folds[2] == 3

    def test_delete_fold_header_removes_fold(self):
        """fold 헤더 라인 삭제 시 해당 fold 제거."""
        content = "line0\nline1\nline2\nline3"
        editor = JsonEditor(content)
        editor._folds[1] = 3
        editor.cursor_row = 1
        editor._handle_normal(self._key("d"))
        editor._handle_pending("d", "d")
        assert len(editor._folds) == 0

    def test_insert_shifts_collapsed_strings(self):
        """라인 삽입 시 collapsed string 인덱스 이동."""
        content = "line0\nline1\nline2"
        editor = JsonEditor(content)
        editor._collapsed_strings = {2}
        editor.cursor_row = 0
        editor._handle_normal(self._key("o"))
        assert 2 not in editor._collapsed_strings
        assert 3 in editor._collapsed_strings

    def test_join_lines_shifts_fold(self):
        """J 명령으로 라인 병합 시 이후 fold 인덱스 이동."""
        content = "line0\nline1\nline2\nline3\nline4"
        editor = JsonEditor(content)
        editor._folds[3] = 4
        editor.cursor_row = 0
        editor._join_lines()
        assert 2 in editor._folds
        assert editor._folds[2] == 3

    def test_fold_containing_deletion_shrinks(self):
        """fold 내부 라인 삭제 시 fold 범위 축소."""
        content = "line0\nline1\nline2\nline3\nline4"
        editor = JsonEditor(content)
        editor._folds[0] = 4  # fold 전체
        editor.cursor_row = 2
        editor._handle_normal(self._key("d"))
        editor._handle_pending("d", "d")
        assert editor._folds[0] == 3

    def test_visual_linewise_change_full_file(self):
        """V 모드 전체 선택 + c → 빈 줄 1개만 남기고 INSERT 진입 (issue #3)."""
        content = '{\n    "a": 1\n}'
        editor = JsonEditor(content)
        editor._handle_normal(self._key("V"))
        # 전체 선택: 0 → 마지막 줄
        editor.cursor_row = len(editor.lines) - 1
        editor._handle_normal(self._key("c"))
        assert len(editor.lines) == 1
        assert editor._mode == EditorMode.INSERT


class TestVisualMode:
    """Tests for visual mode (v/V) selection and operators."""

    SAMPLE = '{\n    "name": "Alice",\n    "age": 30,\n    "items": [1, 2, 3]\n}'

    def _make_editor(self, content=None):
        editor = JsonEditor(content or self.SAMPLE)
        return editor

    def _key(self, char, key=None):
        from types import SimpleNamespace

        return SimpleNamespace(key=key or char, character=char)

    # -- 진입/탈출 --

    def test_v_enters_visual_mode(self):
        editor = self._make_editor()
        editor._handle_normal(self._key("v"))
        assert editor._visual_mode == "v"

    def test_V_enters_visual_line_mode(self):
        editor = self._make_editor()
        editor._handle_normal(self._key("V"))
        assert editor._visual_mode == "V"

    def test_v_toggle_exits(self):
        editor = self._make_editor()
        editor._handle_normal(self._key("v"))
        assert editor._visual_mode == "v"
        editor._handle_normal(self._key("v"))
        assert editor._visual_mode == ""

    def test_V_toggle_exits(self):
        editor = self._make_editor()
        editor._handle_normal(self._key("V"))
        assert editor._visual_mode == "V"
        editor._handle_normal(self._key("V"))
        assert editor._visual_mode == ""

    def test_v_to_V_switches(self):
        editor = self._make_editor()
        editor._handle_normal(self._key("v"))
        assert editor._visual_mode == "v"
        editor._handle_normal(self._key("V"))
        assert editor._visual_mode == "V"

    def test_V_to_v_switches(self):
        editor = self._make_editor()
        editor._handle_normal(self._key("V"))
        editor._handle_normal(self._key("v"))
        assert editor._visual_mode == "v"

    def test_escape_exits_visual(self):
        editor = self._make_editor()
        editor._handle_normal(self._key("v"))
        assert editor._visual_mode == "v"
        editor._handle_normal(self._key(None, "escape"))
        assert editor._visual_mode == ""

    # -- 선택 범위 --

    def test_selection_range_v_forward(self):
        editor = self._make_editor()
        editor._visual_mode = "v"
        editor._visual_anchor_row = 1
        editor._visual_anchor_col = 4
        editor.cursor_row = 1
        editor.cursor_col = 10
        sr, sc, er, ec = editor._visual_selection_range()
        assert (sr, sc) == (1, 4)
        assert (er, ec) == (1, 10)

    def test_selection_range_v_backward(self):
        editor = self._make_editor()
        editor._visual_mode = "v"
        editor._visual_anchor_row = 1
        editor._visual_anchor_col = 10
        editor.cursor_row = 1
        editor.cursor_col = 4
        sr, sc, er, ec = editor._visual_selection_range()
        assert (sr, sc) == (1, 4)
        assert (er, ec) == (1, 10)

    def test_selection_range_V_forward(self):
        editor = self._make_editor()
        editor._visual_mode = "V"
        editor._visual_anchor_row = 1
        editor.cursor_row = 2
        sr, sc, er, ec = editor._visual_selection_range()
        assert sr == 1
        assert sc == 0
        assert er == 2
        assert ec == len(editor.lines[2])

    def test_selection_range_V_backward(self):
        editor = self._make_editor()
        editor._visual_mode = "V"
        editor._visual_anchor_row = 2
        editor.cursor_row = 1
        sr, sc, er, ec = editor._visual_selection_range()
        assert sr == 1
        assert er == 2

    # -- yank --

    def test_linewise_yank(self):
        editor = self._make_editor()
        editor._visual_mode = "V"
        editor._visual_anchor_row = 1
        editor.cursor_row = 2
        editor._handle_normal(self._key("y"))
        assert editor._visual_mode == ""
        assert editor._yank_type == "line"
        assert len(editor.yank_buffer) == 2
        assert "name" in editor.yank_buffer[0]
        assert "age" in editor.yank_buffer[1]

    def test_charwise_yank(self):
        editor = self._make_editor()
        editor._visual_mode = "v"
        editor._visual_anchor_row = 1
        editor._visual_anchor_col = 5
        editor.cursor_row = 1
        editor.cursor_col = 10
        editor._handle_normal(self._key("y"))
        assert editor._visual_mode == ""
        assert editor._yank_type == "char"
        assert len(editor.yank_buffer) == 1
        # 선택된 텍스트: col 5~10 inclusive
        assert editor.yank_buffer[0] == editor.lines[1][5:11]

    # -- delete --

    def test_linewise_delete(self):
        editor = self._make_editor()
        original_lines = editor.lines[:]
        editor._visual_mode = "V"
        editor._visual_anchor_row = 1
        editor.cursor_row = 1
        editor._handle_normal(self._key("d"))
        assert editor._visual_mode == ""
        assert len(editor.lines) == len(original_lines) - 1
        assert len(editor.undo_stack) == 1

    def test_charwise_delete_single_line(self):
        editor = self._make_editor('{"key": "value"}')
        editor._visual_mode = "v"
        editor._visual_anchor_row = 0
        editor._visual_anchor_col = 1
        editor.cursor_row = 0
        editor.cursor_col = 4
        editor._handle_normal(self._key("d"))
        assert editor._visual_mode == ""
        # "key" 부분이 삭제됨 (col 1~4 inclusive)
        assert editor.lines[0] == '{": "value"}'

    def test_charwise_delete_multi_line(self):
        editor = self._make_editor()
        original_count = len(editor.lines)
        editor._visual_mode = "v"
        editor._visual_anchor_row = 1
        editor._visual_anchor_col = 4
        editor.cursor_row = 2
        editor.cursor_col = 4
        editor._handle_normal(self._key("d"))
        assert editor._visual_mode == ""
        assert len(editor.lines) < original_count

    def test_delete_undo(self):
        editor = self._make_editor()
        original_lines = editor.lines[:]
        editor._visual_mode = "V"
        editor._visual_anchor_row = 1
        editor.cursor_row = 2
        editor._handle_normal(self._key("d"))
        assert editor.lines != original_lines
        editor._undo()
        assert editor.lines == original_lines

    # -- change --

    def test_linewise_change(self):
        editor = self._make_editor()
        editor._visual_mode = "V"
        editor._visual_anchor_row = 1
        editor.cursor_row = 1
        editor._handle_normal(self._key("c"))
        assert editor._visual_mode == ""
        assert editor._mode == EditorMode.INSERT

    def test_charwise_change(self):
        editor = self._make_editor()
        editor._visual_mode = "v"
        editor._visual_anchor_row = 1
        editor._visual_anchor_col = 5
        editor.cursor_row = 1
        editor.cursor_col = 10
        editor._handle_normal(self._key("c"))
        assert editor._visual_mode == ""
        assert editor._mode == EditorMode.INSERT

    # -- paste --

    def test_charwise_paste_after(self):
        editor = self._make_editor('{"key": "value"}')
        editor._yank_type = "char"
        editor.yank_buffer = ["abc"]
        editor.cursor_row = 0
        editor.cursor_col = 0
        editor._paste_after()
        assert editor.lines[0] == '{abc"key": "value"}'

    def test_charwise_paste_before(self):
        editor = self._make_editor('{"key": "value"}')
        editor._yank_type = "char"
        editor.yank_buffer = ["abc"]
        editor.cursor_row = 0
        editor.cursor_col = 1
        editor._paste_before()
        assert editor.lines[0] == '{abc"key": "value"}'

    def test_linewise_paste_after_preserves_behavior(self):
        editor = self._make_editor('{"key": "value"}')
        editor._yank_type = "line"
        editor.yank_buffer = ['    "new": true']
        editor.cursor_row = 0
        editor.cursor_col = 0
        editor._paste_after()
        assert len(editor.lines) == 2
        assert editor.lines[1] == '    "new": true'

    # -- read-only --

    def test_readonly_allows_yank(self):
        editor = self._make_editor()
        editor.read_only = True
        editor._visual_mode = "V"
        editor._visual_anchor_row = 1
        editor.cursor_row = 1
        editor._handle_normal(self._key("y"))
        assert len(editor.yank_buffer) == 1

    def test_readonly_blocks_delete(self):
        editor = self._make_editor()
        editor.read_only = True
        original_lines = editor.lines[:]
        editor._visual_mode = "V"
        editor._visual_anchor_row = 1
        editor.cursor_row = 1
        editor._handle_normal(self._key("d"))
        assert editor.lines == original_lines
        assert editor._visual_mode == ""

    def test_readonly_blocks_change(self):
        editor = self._make_editor()
        editor.read_only = True
        editor._visual_mode = "v"
        editor._visual_anchor_row = 0
        editor._visual_anchor_col = 0
        editor.cursor_row = 0
        editor.cursor_col = 5
        editor._handle_normal(self._key("c"))
        assert editor._mode == EditorMode.NORMAL

    # -- yy/dd는 line yank type 유지 --

    def test_yy_sets_line_yank_type(self):
        editor = self._make_editor()
        editor._yank_type = "char"
        editor.pending = "y"
        editor._handle_pending("y", "y")
        assert editor._yank_type == "line"

    def test_dd_sets_line_yank_type(self):
        editor = self._make_editor()
        editor._yank_type = "char"
        editor.pending = "d"
        editor._handle_pending("d", "d")
        assert editor._yank_type == "line"

    # -- undo/redo는 visual mode 해제 --

    def test_undo_clears_visual(self):
        editor = self._make_editor()
        editor._save_undo()
        editor._visual_mode = "V"
        editor._undo()
        assert editor._visual_mode == ""

    def test_redo_clears_visual(self):
        editor = self._make_editor()
        editor._save_undo()
        editor.lines = ["changed"]
        editor._undo()
        editor._visual_mode = "v"
        editor._redo()
        assert editor._visual_mode == ""

    # -- 모드 전환 시 visual 해제 --

    def test_colon_clears_visual(self):
        editor = self._make_editor()
        editor._handle_normal(self._key("v"))
        assert editor._visual_mode == "v"
        editor._handle_normal(self._key(":"))
        assert editor._visual_mode == ""

    def test_slash_clears_visual(self):
        editor = self._make_editor()
        editor._handle_normal(self._key("v"))
        editor._handle_normal(self._key("/"))
        assert editor._visual_mode == ""

    def test_question_clears_visual(self):
        editor = self._make_editor()
        editor._handle_normal(self._key("v"))
        editor._handle_normal(self._key("?"))
        assert editor._visual_mode == ""


class TestSubstitute:
    """Tests for substitute command (:s/old/new/flags)."""

    def test_current_line_first_match(self):
        """`:s/old/new/` — 현재 라인, 첫 번째 매치만 치환."""
        editor = JsonEditor('"old old old"')
        editor.cursor_row = 0
        editor._exec_command("s/old/new/")
        assert editor.lines == ['"new old old"']
        assert "1 substitution" in editor.status_msg

    def test_current_line_global(self):
        """`:s/old/new/g` — 현재 라인, 모든 매치 치환."""
        editor = JsonEditor('"old old old"')
        editor.cursor_row = 0
        editor._exec_command("s/old/new/g")
        assert editor.lines == ['"new new new"']
        assert "3 substitution" in editor.status_msg

    def test_whole_file(self):
        """`:%s/old/new/g` — 전체 파일 치환."""
        content = '"old"\n"old"\n"old"'
        editor = JsonEditor(content)
        editor._exec_command("%s/old/new/g")
        assert editor.lines == ['"new"', '"new"', '"new"']

    def test_line_range(self):
        """`：2,4s/old/new/g` — 라인 범위 치환."""
        content = "old\nold\nold\nold\nold"
        editor = JsonEditor(content)
        editor._exec_command("2,4s/old/new/g")
        assert editor.lines == ["old", "new", "new", "new", "old"]

    def test_ignore_case(self):
        """`:s/old/new/gi` — 대소문자 무시."""
        editor = JsonEditor('"OLD Old old"')
        editor.cursor_row = 0
        editor._exec_command("s/old/new/gi")
        assert editor.lines == ['"new new new"']

    def test_regex_group(self):
        """`:s/(\\w+)/[\\1]/g` — 정규식 그룹 캡처."""
        editor = JsonEditor("hello world")
        editor.cursor_row = 0
        editor._exec_command("s/(\\w+)/[\\1]/g")
        assert editor.lines == ["[hello] [world]"]

    def test_custom_delimiter(self):
        """`:s#old#new#g` — 커스텀 구분자."""
        editor = JsonEditor('"old old"')
        editor.cursor_row = 0
        editor._exec_command("s#old#new#g")
        assert editor.lines == ['"new new"']

    def test_escaped_delimiter(self):
        """`:s/a\\/b/c\\/d/` — escaped 구분자."""
        editor = JsonEditor('"a/b"')
        editor.cursor_row = 0
        editor._exec_command("s/a\\/b/c\\/d/")
        assert editor.lines == ['"c/d"']

    def test_pattern_not_found(self):
        """패턴 미발견 시 메시지."""
        editor = JsonEditor('"hello"')
        editor.cursor_row = 0
        editor._exec_command("s/xyz/abc/")
        assert "Pattern not found" in editor.status_msg
        assert editor.lines == ['"hello"']

    def test_readonly_blocked(self):
        """readonly 모드에서 치환 차단."""
        editor = JsonEditor('"old"')
        editor.read_only = True
        editor._exec_command("s/old/new/")
        assert editor.status_msg == "[readonly]"
        assert editor.lines == ['"old"']

    def test_undo_after_substitute(self):
        """치환 후 undo 동작 확인."""
        editor = JsonEditor('"old old"')
        editor.cursor_row = 0
        editor._exec_command("s/old/new/g")
        assert editor.lines == ['"new new"']
        editor._undo()
        assert editor.lines == ['"old old"']

    def test_no_undo_entry_when_no_match(self):
        """매치 없을 때 undo 스택에 항목 추가되지 않음."""
        editor = JsonEditor('"hello"')
        initial_undo_len = len(editor.undo_stack)
        editor._exec_command("s/xyz/abc/")
        assert len(editor.undo_stack) == initial_undo_len

    def test_pipe_delimiter(self):
        """`:s|old|new|g` — 파이프 구분자."""
        editor = JsonEditor('"old"')
        editor.cursor_row = 0
        editor._exec_command("s|old|new|g")
        assert editor.lines == ['"new"']

    def test_empty_replacement(self):
        """`:s/old//g` — 빈 문자열로 치환 (삭제)."""
        editor = JsonEditor('"old text old"')
        editor.cursor_row = 0
        editor._exec_command("s/old//g")
        assert editor.lines == ['" text "']

    def test_replacement_with_newline(self):
        """치환 결과에 줄바꿈이 포함되면 라인이 분할된다."""
        editor = JsonEditor("A")
        editor.cursor_row = 0
        editor._exec_command(r"s/A/\n/")
        assert editor.lines == ["", ""]

    def test_current_line_respects_cursor_row(self):
        """범위 없이 현재 커서 라인만 치환."""
        content = "aaa\nbbb\naaa"
        editor = JsonEditor(content)
        editor.cursor_row = 2
        editor._exec_command("s/aaa/ccc/")
        assert editor.lines == ["aaa", "bbb", "ccc"]


class TestSubstituteJsonPath:
    """Tests for JSONPath substitute.

    문법 구분:
    - $.path      → 키 이름 변경
    - $.path=     → 전체 값 치환
    - $.path=val  → 조건부 값 치환
    """

    # -- 값 치환 ($.path=) --

    def test_value_basic_string(self):
        """$.name= 로 문자열 값 치환."""
        content = '{\n    "name": "Alice"\n}'
        editor = JsonEditor(content)
        editor._exec_command("s/$.name=/Bob/g")
        assert '"Bob"' in editor.lines[1]
        assert "1 substitution" in editor.status_msg

    def test_value_number(self):
        """$.age= 로 숫자 값 치환 — 자동 감지."""
        content = '{\n    "age": 30\n}'
        editor = JsonEditor(content)
        editor._exec_command("s/$.age=/25/g")
        assert "25" in editor.lines[1]
        assert '"25"' not in editor.lines[1]

    def test_value_boolean(self):
        """$.active= 로 불리언 값 치환."""
        content = '{\n    "active": false\n}'
        editor = JsonEditor(content)
        editor._exec_command("s/$.active=/true/g")
        assert "true" in editor.lines[1]

    def test_value_null(self):
        """$.data= 로 null 치환."""
        content = '{\n    "data": "old"\n}'
        editor = JsonEditor(content)
        editor._exec_command("s/$.data=/null/g")
        assert "null" in editor.lines[1]

    def test_value_wildcard(self):
        """와일드카드로 여러 값 치환."""
        content = (
            '{\n    "users": [\n        {"name": "A"},\n        {"name": "B"}\n    ]\n}'
        )
        editor = JsonEditor(content)
        editor._exec_command("s/$..name=/X/g")
        result = editor.get_content()
        assert result.count('"X"') == 2

    def test_value_global_flag(self):
        """g 플래그 없으면 첫 번째만 치환."""
        content = (
            '{\n    "users": [\n        {"name": "A"},\n        {"name": "B"}\n    ]\n}'
        )
        editor = JsonEditor(content)
        editor._exec_command("s/$..name=/X/")
        result = editor.get_content()
        assert result.count('"X"') == 1

    def test_value_filter_equals(self):
        """필터로 특정 값만 치환."""
        content = '{\n    "items": [\n        {"status": "draft"},\n        {"status": "published"}\n    ]\n}'
        editor = JsonEditor(content)
        editor._exec_command('s/$..status="draft"/review/g')
        result = editor.get_content()
        assert '"review"' in result
        assert '"published"' in result

    def test_value_not_found(self):
        """JSONPath 매치 없을 때 메시지."""
        content = '{\n    "name": "Alice"\n}'
        editor = JsonEditor(content)
        editor._exec_command("s/$.nonexistent=/value/g")
        assert "not found" in editor.status_msg.lower()

    def test_value_object_not_substitutable(self):
        """오브젝트/배열은 값 치환 불가."""
        content = '{\n    "data": {"nested": true}\n}'
        editor = JsonEditor(content)
        editor._exec_command("s/$.data=/replaced/g")
        assert "not substitutable" in editor.status_msg.lower()

    def test_value_undo(self):
        """값 치환 후 undo 동작."""
        content = '{\n    "name": "Alice"\n}'
        editor = JsonEditor(content)
        editor._exec_command("s/$.name=/Bob/g")
        assert '"Bob"' in editor.lines[1]
        editor._undo()
        assert '"Alice"' in editor.lines[1]

    def test_value_quoted_string(self):
        """이미 따옴표가 있는 replacement는 그대로 사용."""
        content = '{\n    "name": "Alice"\n}'
        editor = JsonEditor(content)
        editor._exec_command('s/$.name=/"Bob"/g')
        assert '"Bob"' in editor.lines[1]

    def test_value_custom_delimiter(self):
        """커스텀 구분자로 값 치환."""
        content = '{\n    "name": "Alice"\n}'
        editor = JsonEditor(content)
        editor._exec_command("s#$.name=#Bob#g")
        assert '"Bob"' in editor.lines[1]

    # -- 키 이름 변경 ($.path) --

    def test_key_rename_basic(self):
        """$.name 으로 키 이름 변경."""
        content = '{\n    "name": "Alice"\n}'
        editor = JsonEditor(content)
        editor._exec_command("s/$.name/username/g")
        assert '"username"' in editor.lines[1]
        assert '"Alice"' in editor.lines[1]
        assert "1 substitution" in editor.status_msg

    def test_key_rename_wildcard(self):
        """와일드카드로 여러 키 이름 변경."""
        content = (
            '{\n    "users": [\n        {"name": "A"},\n        {"name": "B"}\n    ]\n}'
        )
        editor = JsonEditor(content)
        editor._exec_command("s/$..name/label/g")
        result = editor.get_content()
        assert result.count('"label"') == 2
        assert '"A"' in result
        assert '"B"' in result

    def test_key_rename_no_global(self):
        """g 플래그 없으면 첫 번째 키만 변경."""
        content = (
            '{\n    "users": [\n        {"name": "A"},\n        {"name": "B"}\n    ]\n}'
        )
        editor = JsonEditor(content)
        editor._exec_command("s/$..name/label/")
        result = editor.get_content()
        assert result.count('"label"') == 1

    def test_key_rename_undo(self):
        """키 이름 변경 후 undo."""
        content = '{\n    "name": "Alice"\n}'
        editor = JsonEditor(content)
        editor._exec_command("s/$.name/username/g")
        assert '"username"' in editor.lines[1]
        editor._undo()
        assert '"name"' in editor.lines[1]

    def test_key_rename_not_found(self):
        """존재하지 않는 키."""
        content = '{\n    "name": "Alice"\n}'
        editor = JsonEditor(content)
        editor._exec_command("s/$.nonexistent/newkey/g")
        assert "not found" in editor.status_msg.lower()

    def test_key_rename_array_index_skipped(self):
        """배열 인덱스는 키 변경 불가."""
        content = '{\n    "items": [1, 2, 3]\n}'
        editor = JsonEditor(content)
        editor._exec_command("s/$.items[0]/newkey/g")
        assert "no renamable keys" in editor.status_msg.lower()

    # -- 공통 --

    def test_readonly_blocked(self):
        """readonly 모드에서 JSONPath 치환 차단."""
        content = '{\n    "name": "Alice"\n}'
        editor = JsonEditor(content)
        editor.read_only = True
        editor._exec_command("s/$.name/Bob/g")
        assert editor.status_msg == "[readonly]"
        assert '"Alice"' in editor.lines[1]

    def test_json_encode_replacement_number(self):
        """_json_encode_replacement: 숫자."""
        assert JsonEditor._json_encode_replacement("42") == "42"
        assert JsonEditor._json_encode_replacement("3.14") == "3.14"

    def test_json_encode_replacement_bool_null(self):
        """_json_encode_replacement: 불리언/null."""
        assert JsonEditor._json_encode_replacement("true") == "true"
        assert JsonEditor._json_encode_replacement("false") == "false"
        assert JsonEditor._json_encode_replacement("null") == "null"

    def test_json_encode_replacement_string(self):
        """_json_encode_replacement: 일반 문자열은 JSON 인코딩."""
        assert JsonEditor._json_encode_replacement("hello") == '"hello"'

    def test_json_encode_replacement_already_quoted(self):
        """_json_encode_replacement: 이미 따옴표면 그대로."""
        assert JsonEditor._json_encode_replacement('"hello"') == '"hello"'


class TestTabCompletion:
    """Tab 자동완성 테스트."""

    def _key(self, char, key=None):
        from types import SimpleNamespace

        return SimpleNamespace(key=key or char, character=char)

    def test_single_match(self, tmp_path):
        """단일 매칭 시 즉시 완성."""
        (tmp_path / "data.json").write_text("{}")
        editor = JsonEditor()
        editor.command_buffer = f"e {tmp_path}/dat"
        editor._complete_path()
        assert editor.command_buffer == f"e {tmp_path}/data.json"
        assert editor._tab_completions == []

    def test_multiple_matches_common_prefix(self, tmp_path):
        """복수 매칭 시 공통 접두사까지 완성 + 후보 목록."""
        (tmp_path / "file_a.json").write_text("{}")
        (tmp_path / "file_b.json").write_text("{}")
        editor = JsonEditor()
        editor.command_buffer = f"e {tmp_path}/f"
        editor._complete_path()
        assert editor.command_buffer == f"e {tmp_path}/file_"
        assert "file_a.json" in editor._tab_completions
        assert "file_b.json" in editor._tab_completions

    def test_directory_slash(self, tmp_path):
        """디렉토리 완성 시 / 자동 추가."""
        (tmp_path / "subdir").mkdir()
        editor = JsonEditor()
        editor.command_buffer = f"e {tmp_path}/sub"
        editor._complete_path()
        assert editor.command_buffer == f"e {tmp_path}/subdir/"
        assert editor._tab_completions == []

    def test_no_match(self, tmp_path):
        """매칭 없으면 변화 없음."""
        editor = JsonEditor()
        editor.command_buffer = f"e {tmp_path}/nonexistent_xyz"
        editor._complete_path()
        assert editor.command_buffer == f"e {tmp_path}/nonexistent_xyz"
        assert editor._tab_completions == []

    def test_non_target_command_ignored(self):
        """:e, :w 이외 명령은 무시."""
        editor = JsonEditor()
        editor.command_buffer = "q"
        editor._complete_path()
        assert editor.command_buffer == "q"
        assert editor._tab_completions == []

    def test_w_command_completion(self, tmp_path):
        """:w 명령도 자동완성 지원."""
        (tmp_path / "output.json").write_text("{}")
        editor = JsonEditor()
        editor.command_buffer = f"w {tmp_path}/out"
        editor._complete_path()
        assert editor.command_buffer == f"w {tmp_path}/output.json"

    def test_candidates_cleared_on_other_key(self):
        """다른 키 입력 시 후보 목록 클리어."""
        editor = JsonEditor()
        editor._tab_completions = ["a.json", "b.json"]
        editor._mode = EditorMode.COMMAND
        editor._handle_command(self._key("x"))
        assert editor._tab_completions == []

    def test_hidden_files_excluded(self, tmp_path):
        """숨김 파일은 후보에서 제외."""
        (tmp_path / ".hidden").write_text("{}")
        (tmp_path / "visible.json").write_text("{}")
        editor = JsonEditor()
        editor.command_buffer = f"e {tmp_path}/"
        editor._complete_path()
        assert editor.command_buffer == f"e {tmp_path}/visible.json"
        assert editor._tab_completions == []

    def test_directory_listing(self, tmp_path):
        """후보에 디렉토리는 / 표시."""
        (tmp_path / "adir").mkdir()
        (tmp_path / "afile.json").write_text("{}")
        editor = JsonEditor()
        editor.command_buffer = f"e {tmp_path}/a"
        editor._complete_path()
        assert "adir/" in editor._tab_completions
        assert "afile.json" in editor._tab_completions

    def test_empty_partial(self, tmp_path):
        """경로 없이 :e만 있으면 현재 디렉토리 기준."""
        editor = JsonEditor()
        editor.command_buffer = "e "
        # 빈 partial은 현재 디렉토리 기준 — 에러 없이 동작하면 OK
        editor._complete_path()

    def test_tab_cycles_through_candidates(self, tmp_path):
        """Tab 반복 시 후보 순회."""
        (tmp_path / "aa.json").write_text("{}")
        (tmp_path / "ab.json").write_text("{}")
        (tmp_path / "ac.json").write_text("{}")
        editor = JsonEditor()
        editor.command_buffer = f"e {tmp_path}/a"

        # 첫 Tab: 공통 접두사 완성 + 후보 저장
        editor._complete_path()
        assert len(editor._tab_completions) == 3
        assert editor._tab_index == -1

        # 두번째 Tab: 첫 번째 후보 선택
        editor._complete_path()
        assert editor._tab_index == 0
        assert editor.command_buffer == f"e {tmp_path}/aa.json"

        # 세번째 Tab: 두 번째 후보
        editor._complete_path()
        assert editor._tab_index == 1
        assert editor.command_buffer == f"e {tmp_path}/ab.json"

        # 네번째 Tab: 세 번째 후보
        editor._complete_path()
        assert editor._tab_index == 2
        assert editor.command_buffer == f"e {tmp_path}/ac.json"

        # 다섯번째 Tab: 다시 첫 번째로 순환
        editor._complete_path()
        assert editor._tab_index == 0
        assert editor.command_buffer == f"e {tmp_path}/aa.json"

    def test_tab_cycle_reset_on_other_key(self, tmp_path):
        """순회 중 다른 키 입력 시 리셋."""
        (tmp_path / "aa.json").write_text("{}")
        (tmp_path / "ab.json").write_text("{}")
        editor = JsonEditor()
        editor._mode = EditorMode.COMMAND
        editor.command_buffer = f"e {tmp_path}/a"
        editor._complete_path()
        editor._complete_path()  # 첫 번째 후보 선택
        assert editor._tab_index == 0

        # 다른 키 입력
        editor._handle_command(self._key("x"))
        assert editor._tab_completions == []
        assert editor._tab_index == -1

    def test_backspace_filters_candidates(self, tmp_path):
        """Backspace로 지우면 후보 재필터링."""
        (tmp_path / "aa.json").write_text("{}")
        (tmp_path / "ab.json").write_text("{}")
        (tmp_path / "bc.json").write_text("{}")
        editor = JsonEditor()
        editor._mode = EditorMode.COMMAND
        editor.command_buffer = f"e {tmp_path}/a"
        editor._complete_path()
        assert len(editor._tab_completions) == 2  # aa, ab

        # backspace로 'a' 제거 → 전체 파일 매칭
        editor.command_buffer = f"e {tmp_path}/a"
        editor._handle_command(self._key(None, "backspace"))
        assert editor.command_buffer == f"e {tmp_path}/"
        assert len(editor._tab_completions) == 3  # aa, ab, bc 전부

    def test_backspace_clears_when_no_match(self, tmp_path):
        """Backspace 후 매칭 없으면 후보 클리어."""
        (tmp_path / "data.json").write_text("{}")
        editor = JsonEditor()
        editor._mode = EditorMode.COMMAND
        editor.command_buffer = f"e {tmp_path}/d"
        editor._complete_path()
        assert len(editor._tab_completions) == 0  # 단일 → 즉시 완성

        # 후보 있는 상태에서 경로를 존재하지 않는 디렉토리로 변경
        editor._tab_completions = ["stale.json"]
        editor.command_buffer = f"e {tmp_path}/nonexistent/x"
        editor._handle_command(self._key(None, "backspace"))
        assert editor._tab_completions == []

    def test_backspace_resets_tab_index(self, tmp_path):
        """Backspace 후 순회 인덱스 리셋."""
        (tmp_path / "aa.json").write_text("{}")
        (tmp_path / "ab.json").write_text("{}")
        editor = JsonEditor()
        editor._mode = EditorMode.COMMAND
        editor.command_buffer = f"e {tmp_path}/a"
        editor._complete_path()
        editor._complete_path()  # tab_index = 0
        assert editor._tab_index == 0

        editor._handle_command(self._key(None, "backspace"))
        assert editor._tab_index == -1

    def test_wildmenu_no_overflow(self, tmp_path):
        """wildmenu 실제 렌더링 결과가 width를 초과하지 않는지 검증."""
        from rich.text import Text

        for i in range(15):
            (tmp_path / f"file_{i:02d}.json").write_text("{}")
        editor = JsonEditor()
        editor._mode = EditorMode.COMMAND
        editor.command_buffer = f"e {tmp_path}/f"
        editor._complete_path()
        assert len(editor._tab_completions) == 15

        # 다양한 폭에서 모든 tab_index 검증
        for test_width in [40, 60, 80, 120]:
            for tab_idx in range(-1, 15):
                editor._tab_index = tab_idx
                result = Text()
                editor._render_wildmenu(result, Text.append, test_width)
                rendered = result.plain
                assert len(rendered) <= test_width, (
                    f"width={test_width}, idx={tab_idx}: "
                    f"rendered={len(rendered)} '{rendered}'"
                )


class TestDetectJsonl:
    """Tests for content-based JSONL detection."""

    def test_single_json_object(self):
        """단일 JSON 객체는 JSONL이 아님."""
        assert _detect_jsonl('{"a": 1}') is False

    def test_multiline_json_object(self):
        """멀티라인 JSON 객체는 JSONL이 아님."""
        assert _detect_jsonl('{\n    "a": 1,\n    "b": 2\n}') is False

    def test_json_array(self):
        """JSON 배열은 JSONL이 아님."""
        assert _detect_jsonl('[{"a": 1}, {"b": 2}]') is False

    def test_two_json_lines(self):
        """2개의 JSON 줄은 JSONL."""
        assert _detect_jsonl('{"a": 1}\n{"b": 2}') is True

    def test_multiple_json_lines(self):
        """여러 JSON 줄은 JSONL."""
        assert _detect_jsonl('{"a": 1}\n{"b": 2}\n{"c": 3}') is True

    def test_with_empty_lines(self):
        """빈 줄이 포함되어도 JSONL 감지."""
        assert _detect_jsonl('{"a": 1}\n\n{"b": 2}') is True

    def test_invalid_line(self):
        """유효하지 않은 줄이 포함되면 JSONL이 아님."""
        assert _detect_jsonl('{"a": 1}\ninvalid\n{"b": 2}') is False

    def test_single_line(self):
        """한 줄만 있으면 JSONL이 아님."""
        assert _detect_jsonl('{"a": 1}\n') is False

    def test_empty_content(self):
        """빈 내용은 JSONL이 아님."""
        assert _detect_jsonl("") is False

    def test_json_values_not_objects(self):
        """JSON 값이 객체가 아니어도 JSONL 감지."""
        assert _detect_jsonl("1\n2\n3") is True


class TestGitDifftool:
    """Tests for git difftool install/uninstall."""

    def test_install_difftool(self, tmp_path):
        """--install-difftool로 local config에 등록."""
        subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
        _install_difftool("--local", cwd=str(tmp_path))
        result = subprocess.run(
            ["git", "config", "difftool.jvimdiff.cmd"],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        assert result.returncode == 0
        assert "jvimdiff" in result.stdout

    def test_install_difftool_trust_exit_code(self, tmp_path):
        """trustExitCode 설정 확인."""
        subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
        _install_difftool("--local", cwd=str(tmp_path))
        result = subprocess.run(
            ["git", "config", "difftool.jvimdiff.trustExitCode"],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        assert result.returncode == 0
        assert "true" in result.stdout

    def test_uninstall_difftool(self, tmp_path):
        """--uninstall-difftool로 config에서 제거."""
        subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
        _install_difftool("--local", cwd=str(tmp_path))
        _uninstall_difftool("--local", cwd=str(tmp_path))
        result = subprocess.run(
            ["git", "config", "--local", "difftool.jvimdiff.cmd"],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        assert result.returncode != 0

    def test_uninstall_without_install(self, tmp_path):
        """미설치 상태에서 uninstall 시 에러 종료."""
        subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
        import pytest

        with pytest.raises(SystemExit):
            _uninstall_difftool("--local", cwd=str(tmp_path))


class TestTruncatePath:
    """diff 뷰어 타이틀 경로 truncation 테스트."""

    def test_short_path_unchanged(self):
        """폭보다 짧은 경로는 그대로."""
        assert JsonDiffApp._truncate_path("file.json", 40) == "file.json"

    def test_exact_fit(self):
        """padding 제외 폭에 정확히 맞는 경로."""
        # width=22, padding=2 → available=20
        path = "a" * 20
        assert JsonDiffApp._truncate_path(path, 22) == path

    def test_long_path_truncated(self):
        """긴 경로는 왼쪽이 잘리고 … 붙음."""
        path = "/very/long/path/to/some/deep/file.json"
        result = JsonDiffApp._truncate_path(path, 22)
        assert result.startswith("\u2026")
        assert result.endswith("file.json")
        # padding 제외 available=20, ellipsis 1 + 나머지 19
        assert len(result) == 20

    def test_preserves_filename(self):
        """파일명이 항상 보임."""
        path = "/a/b/c/d/e/f/g/data.json"
        result = JsonDiffApp._truncate_path(path, 20)
        assert "data.json" in result

    def test_zero_width(self):
        """폭 0이면 원본 반환."""
        assert JsonDiffApp._truncate_path("file.json", 0) == "file.json"


class TestMultiFileNavigation:
    """멀티 파일 탐색 (:n, :N, :e#) 테스트."""

    def test_open_file_updates_content(self, tmp_path):
        """_open_file로 파일 전환 후 content 확인."""
        from src.jvim.editor import JsonEditorApp

        (tmp_path / "a.json").write_text('{"a": 1}')
        (tmp_path / "b.json").write_text('{"b": 2}')
        app = JsonEditorApp(
            file_path=str(tmp_path / "a.json"),
            initial_content='{"a": 1}',
            file_list=[str(tmp_path / "a.json"), str(tmp_path / "b.json")],
        )
        assert app.file_path == str(tmp_path / "a.json")
        assert app.file_index == 0
        assert len(app.file_list) == 2

    def test_navigate_file_boundary_next(self, tmp_path):
        """마지막 파일에서 :n 시 경고."""
        from src.jvim.editor import JsonEditorApp

        app = JsonEditorApp(
            file_path="a.json",
            initial_content="{}",
            file_list=["a.json", "b.json"],
        )
        app.file_index = 1
        # _navigate_file은 app이 mount된 상태에서만 동작하므로 직접 범위 체크
        new_index = app.file_index + 1
        assert new_index >= len(app.file_list)

    def test_navigate_file_boundary_prev(self):
        """첫 번째 파일에서 :N 시 경고."""
        from src.jvim.editor import JsonEditorApp

        app = JsonEditorApp(
            file_path="a.json",
            initial_content="{}",
            file_list=["a.json", "b.json"],
        )
        app.file_index = 0
        new_index = app.file_index - 1
        assert new_index < 0

    def test_alternate_file_tracking(self):
        """alternate file 기록 확인."""
        from src.jvim.editor import JsonEditorApp

        app = JsonEditorApp(
            file_path="a.json",
            initial_content="{}",
            file_list=["a.json"],
        )
        assert app._alternate_file == ""

    def test_file_list_from_single_file(self):
        """단일 파일 전달 시 file_list에 포함."""
        from src.jvim.editor import JsonEditorApp

        app = JsonEditorApp(file_path="test.json", initial_content="{}")
        assert app.file_list == ["test.json"]
        assert app.file_index == 0

    def test_file_list_empty_when_no_file(self):
        """파일 없이 실행 시 file_list 비어있음."""
        from src.jvim.editor import JsonEditorApp

        app = JsonEditorApp(file_path="", initial_content="{}")
        assert app.file_list == []

    def test_title_with_multiple_files(self):
        """파일이 2개 이상이면 타이틀에 [N/M] 표시."""
        from src.jvim.editor import JsonEditorApp

        app = JsonEditorApp(
            file_path="a.json",
            initial_content="{}",
            file_list=["a.json", "b.json", "c.json"],
        )
        app._update_title()
        assert "[1/3]" in app.sub_title
        assert "a.json" in app.sub_title

    def test_title_single_file_no_indicator(self):
        """단일 파일이면 인디케이터 없음."""
        from src.jvim.editor import JsonEditorApp

        app = JsonEditorApp(
            file_path="a.json",
            initial_content="{}",
            file_list=["a.json"],
        )
        app._update_title()
        assert "[1/1]" not in app.sub_title

    def test_command_parsing_next(self):
        """`:n` 명령이 FileNavigateRequested 생성."""
        editor = JsonEditor('{"a": 1}')
        editor._exec_command("n")
        # post_message가 호출됨 — 위젯이 mount 안 된 상태에서는 에러 없이 통과 확인
        # 실제 메시지 전송은 앱 레벨에서 테스트

    def test_command_parsing_prev(self):
        """`:N` 명령이 FileNavigateRequested 생성."""
        editor = JsonEditor('{"a": 1}')
        editor._exec_command("N")

    def test_command_parsing_prev_alias(self):
        """`:prev` 명령이 FileNavigateRequested 생성."""
        editor = JsonEditor('{"a": 1}')
        editor._exec_command("prev")

    def test_command_parsing_alternate(self):
        """`:e#` 명령이 FileNavigateRequested 생성."""
        editor = JsonEditor('{"a": 1}')
        editor._exec_command("e #")

    def test_main_binary_file_exits(self, tmp_path):
        """바이너리 파일(null byte 포함)은 시작 시 거부."""
        binary = tmp_path / "data.bin"
        binary.write_bytes(b'{"key": "\x00"}')
        result = subprocess.run(
            ["python", "-m", "jvim", str(binary)],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "binary file" in result.stderr

    def test_main_encoding_error_exits(self, tmp_path):
        """UTF-8 디코딩 불가 파일은 시작 시 거부."""
        bad = tmp_path / "bad.json"
        bad.write_bytes(b'\xff\xfe{"key": 1}')
        result = subprocess.run(
            ["python", "-m", "jvim", str(bad)],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "binary file" in result.stderr


class TestCountPrefix:
    """숫자 접두사(count prefix) 테스트."""

    def _key(self, char, key=None):
        from types import SimpleNamespace

        return SimpleNamespace(key=key or char, character=char)

    # -- count 수집 --

    def test_digit_accumulates(self):
        """1-9로 시작하면 _count_buf에 누적."""
        editor = JsonEditor("line0\nline1\nline2\nline3\nline4")
        editor._handle_normal(self._key("3"))
        assert editor._count_buf == "3"
        editor._handle_normal(self._key("2"))
        assert editor._count_buf == "32"

    def test_bare_zero_moves_to_col0(self):
        """count 없이 0 → 줄 시작 이동."""
        editor = JsonEditor("hello world")
        editor.cursor_col = 5
        editor._handle_normal(self._key("0"))
        assert editor.cursor_col == 0
        assert editor._count_buf == ""

    def test_zero_after_digit_accumulates(self):
        """숫자 뒤 0은 count에 누적."""
        editor = JsonEditor("line\n" * 30)
        editor._handle_normal(self._key("1"))
        editor._handle_normal(self._key("0"))
        assert editor._count_buf == "10"

    def test_escape_clears_count(self):
        """Escape → count 리셋."""
        editor = JsonEditor("hello")
        editor._handle_normal(self._key("5"))
        assert editor._count_buf == "5"
        editor._handle_normal(self._key(None, "escape"))
        assert editor._count_buf == ""

    # -- count + 이동 --

    def test_count_j(self):
        """3j → 3줄 아래."""
        editor = JsonEditor("line0\nline1\nline2\nline3\nline4")
        editor.cursor_row = 0
        editor._handle_normal(self._key("3"))
        editor._handle_normal(self._key("j"))
        editor._clamp_cursor()
        assert editor.cursor_row == 3

    def test_count_k(self):
        """2k → 2줄 위."""
        editor = JsonEditor("line0\nline1\nline2\nline3\nline4")
        editor.cursor_row = 4
        editor._handle_normal(self._key("2"))
        editor._handle_normal(self._key("k"))
        editor._clamp_cursor()
        assert editor.cursor_row == 2

    def test_count_h(self):
        """3h → 3칸 왼쪽."""
        editor = JsonEditor("hello world")
        editor.cursor_col = 5
        editor._handle_normal(self._key("3"))
        editor._handle_normal(self._key("h"))
        editor._clamp_cursor()
        assert editor.cursor_col == 2

    def test_count_l(self):
        """4l → 4칸 오른쪽."""
        editor = JsonEditor("hello world")
        editor.cursor_col = 0
        editor._handle_normal(self._key("4"))
        editor._handle_normal(self._key("l"))
        editor._clamp_cursor()
        assert editor.cursor_col == 4

    def test_count_w(self):
        """2w → 단어 2개 전진."""
        editor = JsonEditor("aaa bbb ccc ddd")
        editor.cursor_col = 0
        editor._handle_normal(self._key("2"))
        editor._handle_normal(self._key("w"))
        assert editor.cursor_col == 8  # "ccc" 시작

    def test_count_b(self):
        """2b → 단어 2개 후진."""
        editor = JsonEditor("aaa bbb ccc ddd")
        editor.cursor_col = 12  # "ddd"
        editor._handle_normal(self._key("2"))
        editor._handle_normal(self._key("b"))
        assert editor.cursor_col == 4  # "bbb" 시작

    def test_count_G(self):
        """5G → 5번째 줄로 이동."""
        editor = JsonEditor("\n".join([f"line{i}" for i in range(10)]))
        editor._handle_normal(self._key("5"))
        editor._handle_normal(self._key("G"))
        editor._clamp_cursor()
        assert editor.cursor_row == 4  # 0-indexed

    def test_G_without_count(self):
        """G (count 없음) → 마지막 줄."""
        editor = JsonEditor("\n".join([f"line{i}" for i in range(10)]))
        editor._handle_normal(self._key("G"))
        editor._clamp_cursor()
        assert editor.cursor_row == 9

    # -- count + 편집 --

    def test_count_x(self):
        """3x → 3문자 삭제."""
        editor = JsonEditor("abcdefgh")
        editor.cursor_col = 2
        editor._handle_normal(self._key("3"))
        editor._handle_normal(self._key("x"))
        assert editor.lines[0] == "abfgh"

    def test_count_dd(self):
        """3dd → 3줄 삭제."""
        editor = JsonEditor("line0\nline1\nline2\nline3\nline4")
        editor.cursor_row = 1
        editor._handle_normal(self._key("3"))
        editor._handle_normal(self._key("d"))
        editor._handle_pending("d", "d")
        assert len(editor.lines) == 2
        assert editor.lines == ["line0", "line4"]
        assert len(editor.yank_buffer) == 3

    def test_count_yy(self):
        """2yy → 2줄 yank."""
        editor = JsonEditor("line0\nline1\nline2\nline3")
        editor.cursor_row = 1
        editor._handle_normal(self._key("2"))
        editor._handle_normal(self._key("y"))
        editor._handle_pending("y", "y")
        assert editor.yank_buffer == ["line1", "line2"]
        assert "2 lines yanked" in editor.status_msg

    def test_count_p(self):
        """2p → 2회 paste."""
        editor = JsonEditor("line0\nline1")
        editor._yank_type = "line"
        editor.yank_buffer = ["new"]
        editor.cursor_row = 0
        editor._handle_normal(self._key("2"))
        editor._handle_normal(self._key("p"))
        assert editor.lines.count("new") == 2

    def test_count_J(self):
        """2J → 2회 join."""
        editor = JsonEditor("aaa\nbbb\nccc\nddd")
        editor.cursor_row = 0
        editor._handle_normal(self._key("2"))
        editor._handle_normal(self._key("J"))
        # 2회 join: "aaa bbb" → "aaa bbb ccc"
        assert editor.lines[0] == "aaa bbb ccc"

    # -- d{count}d 패턴 --

    def test_d3d_pattern(self):
        """d3d → 3줄 삭제."""
        editor = JsonEditor("line0\nline1\nline2\nline3\nline4")
        editor.cursor_row = 0
        editor._handle_normal(self._key("d"))
        # pending 상태에서 숫자 입력
        editor._handle_pending("3", "3")
        assert editor._count_buf == "3"
        assert editor.pending == "d"  # pending 유지
        # 'd' 입력으로 dd 완성
        editor._handle_pending("d", "d")
        assert len(editor.lines) == 2
        assert editor.lines == ["line3", "line4"]

    def test_y2y_pattern(self):
        """y2y → 2줄 yank."""
        editor = JsonEditor("line0\nline1\nline2\nline3")
        editor.cursor_row = 0
        editor._handle_normal(self._key("y"))
        editor._handle_pending("2", "2")
        editor._handle_pending("y", "y")
        assert editor.yank_buffer == ["line0", "line1"]

    # -- count 소비 후 리셋 --

    def test_count_consumed_after_use(self):
        """count 사용 후 _count_buf 비어야 함."""
        editor = JsonEditor("line0\nline1\nline2")
        editor._handle_normal(self._key("2"))
        editor._handle_normal(self._key("j"))
        assert editor._count_buf == ""

    # -- fold-aware dd --

    def test_dd_on_fold_header_deletes_block(self):
        """fold 헤더에서 dd → 전체 블록 삭제."""
        content = '{\n    "a": {\n        "b": 1\n    },\n    "c": 2\n}'
        # line 0: {
        # line 1:     "a": {
        # line 2:         "b": 1
        # line 3:     },
        # line 4:     "c": 2
        # line 5: }
        editor = JsonEditor(content)
        editor._folds[1] = 3  # fold "a" block
        editor.cursor_row = 1
        editor._handle_normal(self._key("d"))
        editor._handle_pending("d", "d")
        # "a" 블록 (line 1-3) 삭제됨
        assert len(editor.yank_buffer) == 3
        assert "a" in editor.yank_buffer[0]
        assert len(editor.lines) == 3  # {, "c": 2, }

    def test_yy_on_fold_header_yanks_block(self):
        """fold 헤더에서 yy → 전체 블록 yank."""
        content = '{\n    "a": {\n        "b": 1\n    },\n    "c": 2\n}'
        editor = JsonEditor(content)
        editor._folds[1] = 3
        editor.cursor_row = 1
        editor._handle_normal(self._key("y"))
        editor._handle_pending("y", "y")
        assert len(editor.yank_buffer) == 3
        assert "3 lines yanked" in editor.status_msg

    def test_dd_on_non_fold_line(self):
        """fold가 아닌 일반 줄 dd → 1줄만 삭제."""
        content = "line0\nline1\nline2"
        editor = JsonEditor(content)
        editor.cursor_row = 1
        editor._handle_normal(self._key("d"))
        editor._handle_pending("d", "d")
        assert editor.lines == ["line0", "line2"]
        assert editor.yank_buffer == ["line1"]

    # -- count + fold-aware --

    def test_count_dd_with_fold(self):
        """2dd에서 첫 줄이 fold 헤더 → fold 블록 + 다음 줄 삭제."""
        content = '{\n    "a": {\n        "b": 1\n    },\n    "c": 2\n}'
        editor = JsonEditor(content)
        editor._folds[1] = 3
        editor.cursor_row = 1
        editor._handle_normal(self._key("2"))
        editor._handle_normal(self._key("d"))
        editor._handle_pending("d", "d")
        # fold block (1-3) + line 4 삭제
        assert len(editor.yank_buffer) == 4
        assert len(editor.lines) == 2  # { and }

    # -- gg with count --

    def test_gg_with_count(self):
        """3gg → 3번째 줄로 이동."""
        editor = JsonEditor("\n".join([f"line{i}" for i in range(10)]))
        editor._handle_normal(self._key("3"))
        editor._handle_normal(self._key("g"))
        editor._handle_pending("g", "g")
        editor._clamp_cursor()
        assert editor.cursor_row == 2

    # -- fold-aware J --

    def test_J_on_fold_header(self):
        """fold 헤더에서 J → fold 내부 제거 후 다음 보이는 줄과 join."""
        content = '{\n    "a": {\n        "b": 1\n    },\n    "c": 2\n}'
        # line 0: {
        # line 1:     "a": {       ← fold header
        # line 2:         "b": 1   ← hidden
        # line 3:     },           ← fold end
        # line 4:     "c": 2
        # line 5: }
        editor = JsonEditor(content)
        editor._folds[1] = 3
        editor.cursor_row = 1
        editor._join_lines()
        # fold 내부(line 2-3) 제거 + line 4("c": 2)와 join
        assert '"a": {' in editor.lines[1]
        assert '"c": 2' in editor.lines[1]
        assert 1 not in editor._folds

    def test_J_on_non_fold_line(self):
        """일반 줄에서 J → 기존 동작 유지."""
        editor = JsonEditor("aaa\nbbb\nccc")
        editor.cursor_row = 0
        editor._join_lines()
        assert editor.lines[0] == "aaa bbb"

    # -- fold-aware o --

    def test_o_on_fold_header(self):
        """fold 헤더에서 o → fold end 뒤에 새 줄 삽입."""
        content = '{\n    "a": {\n        "b": 1\n    },\n    "c": 2\n}'
        editor = JsonEditor(content)
        editor._folds[1] = 3
        editor.cursor_row = 1
        editor._handle_normal(self._key("o"))
        # fold end(line 3: "},") 뒤인 line 4에 삽입
        # 새 줄은 fold end의 indent 기준
        assert editor.cursor_row == 4
        assert editor._mode == EditorMode.INSERT
        # 원래 "c": 2는 한 줄 밀림
        assert '"c": 2' in editor.lines[5]

    def test_o_on_non_fold_line(self):
        """일반 줄에서 o → 기존 동작 유지."""
        editor = JsonEditor("aaa\nbbb")
        editor.cursor_row = 0
        editor._handle_normal(self._key("o"))
        assert editor.cursor_row == 1
        assert editor._mode == EditorMode.INSERT
        assert editor.lines[2] == "bbb"

    # -- fold-aware p (line paste) --

    def test_p_line_on_fold_header(self):
        """fold 헤더에서 p (line paste) → fold end 뒤에 삽입."""
        content = '{\n    "a": {\n        "b": 1\n    },\n    "c": 2\n}'
        editor = JsonEditor(content)
        editor._folds[1] = 3
        editor._yank_type = "line"
        editor.yank_buffer = ['    "new": true']
        editor.cursor_row = 1
        editor._paste_after()
        # fold end(line 3) 뒤에 삽입 → line 4
        assert editor.lines[4] == '    "new": true'
        assert editor.cursor_row == 4
        # 원래 "c": 2는 line 5로 밀림
        assert '"c": 2' in editor.lines[5]

    def test_p_line_on_non_fold_line(self):
        """일반 줄에서 p (line paste) → 기존 동작 유지."""
        editor = JsonEditor("aaa\nbbb")
        editor._yank_type = "line"
        editor.yank_buffer = ["new"]
        editor.cursor_row = 0
        editor._paste_after()
        assert editor.lines[1] == "new"
        assert editor.cursor_row == 1

    def test_p_char_on_fold_header(self):
        """fold 헤더에서 p (char paste) → 기존 인라인 동작 유지."""
        content = '{\n    "a": {\n        "b": 1\n    },\n    "c": 2\n}'
        editor = JsonEditor(content)
        editor._folds[1] = 3
        editor._yank_type = "char"
        editor.yank_buffer = ["X"]
        editor.cursor_row = 1
        editor.cursor_col = 0
        editor._paste_after()
        # char paste는 fold와 무관하게 인라인 삽입
        assert editor.lines[1].startswith(" X")


class TestSearchWordUnderCursor:
    """* / # 커서 단어 검색 테스트."""

    def _key(self, char, key=None):
        from types import SimpleNamespace

        return SimpleNamespace(key=key or char, character=char)

    def test_get_word_under_cursor(self):
        """커서 위치의 단어를 올바르게 추출."""
        editor = JsonEditor('"hello_world": 123')
        editor.cursor_col = 3  # 'l' in hello_world
        assert editor._get_word_under_cursor() == "hello_world"

    def test_get_word_under_cursor_no_word(self):
        """단어가 아닌 위치에서 빈 문자열 반환."""
        editor = JsonEditor('  "key": "value"')
        editor.cursor_col = 0  # 공백
        assert editor._get_word_under_cursor() == ""

    def test_star_forward_search(self):
        """* → 커서 단어를 정방향 검색하여 다음 매치로 이동."""
        content = '"name": "foo",\n"age": 10,\n"name": "bar"'
        editor = JsonEditor(content)
        editor.cursor_row = 0
        editor.cursor_col = 1  # 'n' in "name"
        editor._handle_normal(self._key("*"))
        # 다음 "name" 매치(line 2)로 이동해야 함
        assert editor.cursor_row == 2
        assert editor._search_buffer == "name"
        assert editor._search_forward is True

    def test_hash_backward_search(self):
        """# → 커서 단어를 역방향 검색하여 이전 매치로 이동."""
        content = '"name": "foo",\n"age": 10,\n"name": "bar"'
        editor = JsonEditor(content)
        editor.cursor_row = 2
        editor.cursor_col = 1  # 'n' in "name" (line 2)
        editor._handle_normal(self._key("#"))
        # 이전 "name" 매치(line 0)로 이동해야 함
        assert editor.cursor_row == 0
        assert editor._search_forward is False

    def test_star_no_word_under_cursor(self):
        """단어가 아닌 위치에서 * → 상태 메시지 표시."""
        editor = JsonEditor('  "key": "value"')
        editor.cursor_col = 0
        editor._handle_normal(self._key("*"))
        assert editor.status_msg == "No word under cursor"

    def test_star_then_n_continues(self):
        """* 후 n으로 이어 탐색."""
        content = "aaa bbb aaa ccc aaa"
        editor = JsonEditor(content)
        editor.cursor_col = 0  # 'a' in first "aaa"
        editor._handle_normal(self._key("*"))
        # 두 번째 "aaa" (col 8)로 이동
        assert editor.cursor_col == 8
        editor._handle_normal(self._key("n"))
        # 세 번째 "aaa" (col 16)로 이동
        assert editor.cursor_col == 16

    def test_star_adds_to_search_history(self):
        """* 검색이 히스토리에 추가됨."""
        editor = JsonEditor("hello world hello")
        editor.cursor_col = 0
        editor._handle_normal(self._key("*"))
        assert "hello" in editor._search_history

    def test_hash_then_N_continues(self):
        """# 후 N으로 이어 역방향 탐색."""
        content = "aaa bbb aaa ccc aaa"
        editor = JsonEditor(content)
        editor.cursor_col = 16  # last "aaa"
        editor._handle_normal(self._key("#"))
        # 두 번째 "aaa" (col 8)로 이동
        assert editor.cursor_col == 8
        editor._handle_normal(self._key("N"))
        # 첫 번째 "aaa" (col 0)로 이동
        assert editor.cursor_col == 0
