"""Content management mixin for JsonEditor."""

from __future__ import annotations

import json


class ContentMixin:
    """Content get/set, validation, formatting, and embedded JSON methods for JsonEditor."""

    # -- Public API --------------------------------------------------------

    def get_content(self) -> str:
        return "\n".join(self.lines)

    def _get_parseable_content(self) -> str:
        """JSON 파싱용 콘텐츠 반환. DiffEditor에서 filler 행 제외하도록 오버라이드."""
        return self.get_content()

    def set_content(self, content: str) -> None:
        if self.jsonl and content:
            content = self._jsonl_to_pretty(content)
        self.lines = content.split("\n") if content else [""]
        self.cursor_row = 0
        self.cursor_col = 0
        self._reset_fold_state()
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
            self._reset_fold_state()
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
        self._reset_fold_state()
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
                edit_kind="json",
            )
        )

    def _edit_embedded_string(self, min_newlines: int = 1) -> None:
        """Handle es command to edit a multiline string value."""
        result = self._find_string_at_cursor()
        if result is None:
            self.status_msg = "cursor not on a string value"
            return

        col_start, col_end, content = result
        newline_count = content.count("\n")
        if newline_count < min_newlines:
            plural = "" if min_newlines == 1 else "s"
            self.status_msg = f"string has fewer than {min_newlines} newline{plural}"
            return

        self.post_message(
            self.EmbeddedEditRequested(
                content=content,
                source_row=self.cursor_row,
                source_col_start=col_start,
                source_col_end=col_end,
                edit_kind="string",
                min_newlines=min_newlines,
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
        blocks = ContentMixin._split_jsonl_blocks(content)
        lines: list[str] = []
        for block in blocks:
            try:
                parsed = json.loads(block)
                lines.append(json.dumps(parsed, ensure_ascii=False))
            except json.JSONDecodeError:
                lines.append(" ".join(block.split()))
        return "\n".join(lines)
