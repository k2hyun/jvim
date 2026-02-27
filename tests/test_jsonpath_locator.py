"""Unit tests for shared JSONPath locator utilities."""

from __future__ import annotations

import json

from jvim.action.jsonpath_locator import build_key_index, find_json_value_position_fast
from jvim.action.jsonpath import jsonpath_find


def test_locator_same_key_different_parent():
    data = {"a": {"id": "left"}, "b": {"id": "right"}}
    lines = json.dumps(data).split("\n")
    idx = build_key_index(lines)
    path = jsonpath_find(data, "$.b.id")[0]
    pos = find_json_value_position_fast(lines, data, path, idx)
    assert pos is not None
    row, cs, ce = pos
    assert lines[row][cs:ce] == '"right"'


def test_locator_array_index_compact():
    data = {"items": [10, 20, 30]}
    lines = json.dumps(data).split("\n")
    idx = build_key_index(lines)
    path = jsonpath_find(data, "$.items[1]")[0]
    pos = find_json_value_position_fast(lines, data, path, idx)
    assert pos is not None
    row, cs, ce = pos
    assert lines[row][cs:ce] == "20"


def test_locator_return_key():
    data = {"name": "jvim"}
    lines = json.dumps(data).split("\n")
    idx = build_key_index(lines)
    path = jsonpath_find(data, "$.name")[0]
    pos = find_json_value_position_fast(lines, data, path, idx, return_key=True)
    assert pos is not None
    row, cs, ce = pos
    assert lines[row][cs:ce] == '"name"'


def test_locator_respects_start_line_and_min_col():
    data = {"k": 1, "nested": {"k": 2}}
    lines = json.dumps(data, indent=4).split("\n")
    idx = build_key_index(lines)
    path = jsonpath_find(data, "$.nested.k")[0]
    pos = find_json_value_position_fast(lines, data, path, idx, start_line=1, min_col=0)
    assert pos is not None
    row, _cs, _ce = pos
    assert row >= 2
