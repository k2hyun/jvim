"""Command mode mixin for JsonEditor."""

from __future__ import annotations

import os
import re
from pathlib import Path


class CommandMixin:
    """Command mode key handler and command execution for JsonEditor."""

    def _handle_command(self, event) -> None:
        EditorMode = self._mode.__class__
        key = event.key
        char = event.character

        if key == "escape":
            self._mode = EditorMode.NORMAL
            self.command_buffer = ""
            self._command_history_idx = -1
            self._tab_completions = []
            self._tab_index = -1
            self.status_msg = ""
            return

        if key == "enter":
            cmd = self.command_buffer.strip()
            if cmd:
                self._add_to_command_history(cmd)
            self._exec_command(cmd)
            if self._mode == EditorMode.COMMAND:
                self._mode = EditorMode.NORMAL
            self.command_buffer = ""
            self._command_history_idx = -1
            self._tab_completions = []
            self._tab_index = -1
            return

        if key == "backspace":
            if self.command_buffer:
                self.command_buffer = self.command_buffer[:-1]
                self._command_history_idx = -1
                self._refresh_completions()
            else:
                self._mode = EditorMode.NORMAL
                self._command_history_idx = -1
                self._tab_completions = []
                self._tab_index = -1
            return

        # History navigation
        if key == "up":
            self._command_history_prev()
            return
        if key == "down":
            self._command_history_next()
            return

        if key == "tab":
            self._complete_path()
            return

        # 다른 키 입력 시 후보 목록 클리어
        self._tab_completions = []
        self._tab_index = -1

        if char and char.isprintable():
            self.command_buffer += char
            self._command_history_idx = -1

    def _add_to_command_history(self, cmd: str) -> None:
        """Add command to history, avoiding duplicates."""
        if not cmd:
            return
        if cmd in self._command_history:
            self._command_history.remove(cmd)
        self._command_history.insert(0, cmd)
        if len(self._command_history) > self._command_history_max:
            self._command_history.pop()

    def _command_history_prev(self) -> None:
        """Navigate to previous command in history."""
        if not self._command_history:
            return
        if self._command_history_idx < len(self._command_history) - 1:
            self._command_history_idx += 1
            self.command_buffer = self._command_history[self._command_history_idx]

    def _command_history_next(self) -> None:
        """Navigate to next command in history."""
        if self._command_history_idx > 0:
            self._command_history_idx -= 1
            self.command_buffer = self._command_history[self._command_history_idx]
        elif self._command_history_idx == 0:
            self._command_history_idx = -1
            self.command_buffer = ""

    def _complete_path(self) -> None:
        """Tab 키로 :e, :w 명령의 파일 경로 자동완성.

        첫 Tab: 단일 매칭이면 즉시 완성, 복수면 공통 접두사 + 후보 표시.
        이후 Tab: 후보를 하나씩 순회하며 command buffer에 반영.
        """
        # 이미 후보가 있으면 다음 후보로 순회
        if self._tab_completions:
            self._tab_index = (self._tab_index + 1) % len(self._tab_completions)
            self._apply_tab_selection()
            return

        parts = self.command_buffer.split(None, 1)
        verb = parts[0] if parts else ""
        if verb not in ("e", "w"):
            return

        partial = parts[1] if len(parts) > 1 else ""
        candidates = self._list_path_candidates(partial)
        if not candidates:
            return

        if len(candidates) == 1:
            # 단일 매칭: 즉시 완성
            self._set_completion(verb, candidates[0])
        else:
            # 복수 매칭: 공통 접두사까지 완성 + 후보 저장
            self._tab_completions = [self._candidate_display(c) for c in candidates]
            self._tab_base_dir = str(candidates[0].parent)
            self._tab_verb = verb
            self._tab_index = -1
            names = [c.name for c in candidates]
            common = os.path.commonprefix(names)
            prefix = Path(partial).name if partial and not partial.endswith("/") else ""
            if common and len(common) > len(prefix):
                completed = str(candidates[0].parent / common)
                self.command_buffer = f"{verb} {completed}"

    def _list_path_candidates(self, partial: str) -> list[Path]:
        """partial 경로에 매칭되는 후보 목록 반환."""
        p = Path(partial).expanduser() if partial else Path(".")
        if partial and partial.endswith("/"):
            parent, prefix = p, ""
        elif partial:
            parent, prefix = p.parent, p.name
        else:
            parent, prefix = Path("."), ""
        if not parent.is_dir():
            return []
        try:
            return [
                entry
                for entry in sorted(parent.iterdir())
                if not entry.name.startswith(".") and entry.name.startswith(prefix)
            ]
        except PermissionError:
            return []

    @staticmethod
    def _candidate_display(entry: Path) -> str:
        return entry.name + "/" if entry.is_dir() else entry.name

    def _set_completion(self, verb: str, entry: Path) -> None:
        """단일 후보를 command buffer에 반영."""
        completed = str(entry)
        if entry.is_dir():
            completed += "/"
        self.command_buffer = f"{verb} {completed}"

    def _apply_tab_selection(self) -> None:
        """현재 _tab_index의 후보를 command buffer에 반영."""
        name = self._tab_completions[self._tab_index]
        path = str(Path(self._tab_base_dir) / name)
        self.command_buffer = f"{self._tab_verb} {path}"

    def _refresh_completions(self) -> None:
        """command_buffer 변경 후 후보 목록 재필터링."""
        if not self._tab_completions:
            return
        parts = self.command_buffer.split(None, 1)
        verb = parts[0] if parts else ""
        if verb not in ("e", "w"):
            self._tab_completions = []
            self._tab_index = -1
            return
        partial = parts[1] if len(parts) > 1 else ""
        candidates = self._list_path_candidates(partial)
        if not candidates:
            self._tab_completions = []
            self._tab_index = -1
            return
        self._tab_completions = [self._candidate_display(c) for c in candidates]
        self._tab_base_dir = str(candidates[0].parent)
        self._tab_verb = verb
        self._tab_index = -1

    def _exec_command(self, cmd: str) -> None:
        stripped = cmd.strip()

        # :$ → jump to last line
        if stripped == "$":
            self.cursor_row = len(self.lines) - 1
            self.cursor_col = 0
            self._scroll_cursor_to_top()
            return

        # Line jump: :l<num> → editor line; :<num> or :p<num> → file line (JSONL record)
        if len(stripped) > 1 and stripped[0] == "l" and stripped[1:].isdigit():
            num = int(stripped[1:])
            self.cursor_row = max(0, min(num - 1, len(self.lines) - 1))
            self.cursor_col = 0
            self._scroll_cursor_to_top()
            return
        if stripped.isdigit() or (
            len(stripped) > 1 and stripped[0] == "p" and stripped[1:].isdigit()
        ):
            num = int(stripped if stripped.isdigit() else stripped[1:])
            if self.jsonl:
                records = self._jsonl_line_records()
                for i, rec in enumerate(records):
                    if rec == num:
                        self.cursor_row = i
                        self.cursor_col = 0
                        self._scroll_cursor_to_top()
                        return
                self.status_msg = f"record {num} not found"
                return
            self.cursor_row = max(0, min(num - 1, len(self.lines) - 1))
            self.cursor_col = 0
            self._scroll_cursor_to_top()
            return

        # 치환 명령: :s/old/new/g, :%s/old/new/g, :N,Ms/old/new/g
        sub_match = re.match(r"^(%|(\d+),(\d+))?s(.)(.*)$", stripped)
        if sub_match:
            self._execute_substitute(stripped)
            return

        parts = cmd.split(None, 1)
        verb = parts[0] if parts else ""
        arg = parts[1] if len(parts) > 1 else ""

        force = verb.endswith("!")
        if force:
            verb = verb[:-1]

        if verb == "w":
            if self.read_only:
                self.status_msg = "[readonly]"
                return
            content = self.get_content()
            if not force:
                valid, err = self._check_content(content)
                if not valid:
                    self.status_msg = err
                    return
            save = self._pretty_to_jsonl(content) if self.jsonl else content
            self.post_message(self.FileSaveRequested(content=save, file_path=arg))
        elif verb == "q":
            if force:
                self.post_message(self.ForceQuit())
            else:
                self.post_message(self.Quit())
        elif verb in ("wq", "x"):
            if self.read_only:
                # read-only: just quit without saving
                self.post_message(self.Quit())
                return
            content = self.get_content()
            if not force:
                valid, err = self._check_content(content)
                if not valid:
                    self.status_msg = err
                    return
            save = self._pretty_to_jsonl(content) if self.jsonl else content
            self.post_message(
                self.FileSaveRequested(content=save, file_path=arg, quit_after=True)
            )
        elif verb == "e":
            if arg == "#":
                self.post_message(self.FileNavigateRequested(action="alternate"))
            elif not arg:
                self.status_msg = "Usage: :e <file>"
            else:
                self.post_message(self.FileOpenRequested(file_path=arg))
        elif verb == "n":
            self.post_message(self.FileNavigateRequested(action="next"))
        elif verb in ("N", "prev"):
            self.post_message(self.FileNavigateRequested(action="prev"))
        elif verb in ("fmt", "format"):
            if self.read_only:
                self.status_msg = "[readonly]"
            else:
                self._format_json()
        elif verb == "help":
            self.post_message(self.HelpToggleRequested())
        else:
            self.status_msg = f"unknown command: :{cmd}"
