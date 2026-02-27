"""Shared JSONPath text-position locator utilities."""

from __future__ import annotations

import json


def build_key_index(lines: list[str]) -> dict[str, list[tuple[int, int]]]:
    """Build an index of JSON keys to their (row, col) positions."""
    index: dict[str, list[tuple[int, int]]] = {}
    for row, line in enumerate(lines):
        col = 0
        while col < len(line):
            quote_pos = line.find('"', col)
            if quote_pos == -1:
                break
            end_pos = quote_pos + 1
            while end_pos < len(line):
                if line[end_pos] == '"' and line[end_pos - 1] != "\\":
                    break
                end_pos += 1
            if end_pos >= len(line):
                break
            after = end_pos + 1
            while after < len(line) and line[after] in " \t":
                after += 1
            if after < len(line) and line[after] == ":":
                key = line[quote_pos : end_pos + 1]
                if key not in index:
                    index[key] = []
                index[key].append((row, quote_pos))
            col = end_pos + 1
    return index


def _find_char_from(
    lines: list[str], row: int, col: int, target: str
) -> tuple[int, int] | None:
    """Find first target character from row:col."""
    for r in range(row, len(lines)):
        line = lines[r]
        start = col if r == row else 0
        idx = line.find(target, start)
        if idx >= 0:
            return (r, idx)
    return None


def _find_array_element(
    lines: list[str], row: int, col: int, index: int
) -> tuple[int, int] | None:
    """Return the start position of index-th element after '['."""
    depth = 0
    count = 0
    in_string = False
    r, c = row, col
    while r < len(lines):
        line = lines[r]
        while c < len(line):
            ch = line[c]
            if in_string:
                if ch == '"' and (c == 0 or line[c - 1] != "\\"):
                    in_string = False
                c += 1
                continue
            if ch == '"':
                if depth == 0 and count == index:
                    return (r, c)
                in_string = True
                c += 1
                continue
            if ch in " \t\n\r":
                c += 1
                continue
            if ch == ",":
                if depth == 0:
                    count += 1
                c += 1
                continue
            if ch in ("{", "["):
                if depth == 0 and count == index:
                    return (r, c)
                depth += 1
                c += 1
                continue
            if ch in ("}", "]"):
                depth -= 1
                if depth < 0:
                    return None
                c += 1
                continue
            # number, true, false, null...
            if depth == 0 and count == index:
                return (r, c)
            c += 1
        r += 1
        c = 0
    return None


def _find_value_end(line: str, start: int) -> int:
    """Find end position of JSON value starting at start."""
    if start >= len(line):
        return start
    ch = line[start]
    if ch == '"':
        i = start + 1
        while i < len(line):
            if line[i] == '"' and line[i - 1] != "\\":
                return i + 1
            i += 1
        return len(line)
    if ch in "-0123456789":
        i = start + 1
        while i < len(line) and line[i] in "0123456789.eE+-":
            i += 1
        return i
    if line[start : start + 4] == "true":
        return start + 4
    if line[start : start + 5] == "false":
        return start + 5
    if line[start : start + 4] == "null":
        return start + 4
    return start


def find_json_value_position_fast(
    lines: list[str],
    data: object,
    path: list[str | int],
    key_index: dict[str, list[tuple[int, int]]],
    start_line: int = 0,
    min_col: int = 0,
    return_key: bool = False,
) -> tuple[int, int, int] | None:
    """Find text position of JSONPath result from pre-built key index."""
    current = data
    for key in path:
        if isinstance(current, dict):
            if key not in current:
                return None
            current = current[key]
        elif isinstance(current, list) and isinstance(key, int):
            if 0 <= key < len(current):
                current = current[key]
            else:
                return None
        else:
            return None

    is_complex = isinstance(current, (dict, list))
    if not path:
        return None

    search_line = start_line
    search_col = min_col

    for i, key in enumerate(path):
        is_last = i == len(path) - 1

        if isinstance(key, str):
            key_pattern = json.dumps(key, ensure_ascii=False)
            positions = key_index.get(key_pattern, [])

            found_pos = None
            for row, col in positions:
                if row > search_line or (row == search_line and col >= search_col):
                    found_pos = (row, col)
                    break

            if found_pos is None:
                return None

            row, col = found_pos
            if is_last:
                if return_key:
                    return (row, col, col + len(key_pattern))
                if is_complex:
                    return (row, col, col + len(key_pattern))
                line = lines[row]
                value_start = col + len(key_pattern)
                while value_start < len(line) and line[value_start] in ": \t":
                    value_start += 1
                target_str = json.dumps(current, ensure_ascii=False)
                if line[value_start:].startswith(target_str):
                    return (row, value_start, value_start + len(target_str))
                value_end = _find_value_end(line, value_start)
                if value_end > value_start:
                    return (row, value_start, value_end)
                return None

            search_line = row
            search_col = col + len(key_pattern)
            continue

        if isinstance(key, int):
            bracket_pos = _find_char_from(lines, search_line, search_col, "[")
            if bracket_pos is None:
                return None
            elem_pos = _find_array_element(lines, bracket_pos[0], bracket_pos[1] + 1, key)
            if elem_pos is None:
                return None
            search_line, search_col = elem_pos
            if is_last:
                if is_complex:
                    return (search_line, search_col, search_col + 1)
                target_str = json.dumps(current, ensure_ascii=False)
                line = lines[search_line]
                pos = line.find(target_str, search_col)
                if pos >= 0:
                    return (search_line, pos, pos + len(target_str))
                return None

    return None
