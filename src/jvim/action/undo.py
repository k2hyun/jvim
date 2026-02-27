"""Undo/redo and dot-repeat mixin for JsonEditor."""

from __future__ import annotations


class UndoMixin:
    """Undo, redo, and dot-repeat methods for JsonEditor."""

    def _save_undo(self) -> None:
        self.undo_stack.append((self.lines[:], self.cursor_row, self.cursor_col))
        # Clear redo stack on new edit
        if self.redo_stack:
            self.redo_stack.clear()
        self._invalidate_caches()

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
        self._reset_fold_state()
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
        self._reset_fold_state()
        self._invalidate_caches()
        self.status_msg = "redone"

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

        from jvim.widget import EditorMode

        self._dot_replaying = True
        for rkey, rchar in self._dot_buffer:
            mock = SimpleNamespace(key=rkey, character=rchar)
            if self._mode == EditorMode.NORMAL:
                self._handle_normal(mock)
            elif self._mode == EditorMode.INSERT:
                self._handle_insert(mock)
            self._clamp_cursor()
        self._dot_replaying = False
