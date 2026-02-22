"""Action mixins and utilities for JsonEditor."""

from jvim.action.clipboard import ClipboardMixin
from jvim.action.folding import FoldMixin
from jvim.action.navigation import NavigationMixin
from jvim.action.substitute import SubstituteMixin
from jvim.action.visual import VisualMixin

__all__ = [
    "ClipboardMixin",
    "FoldMixin",
    "NavigationMixin",
    "SubstituteMixin",
    "VisualMixin",
]
