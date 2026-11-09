"""Plain data structures shared by the sync layers.

Deliberately free of Qt and of calibre GUI imports: these objects cross from
the worker that reads the device into the dialog that shows the changes.
"""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple


class Side(IntEnum):
    """Which side a change is written to."""
    CALIBRE = 1
    DEVICE = 2

    @property
    def label(self):
        return 'calibre' if self is Side.CALIBRE else 'device'


class MatchConfidence(IntEnum):
    """How sure we are that a calibre book is a given book on the device."""
    METADATA = 1        # title + author matched, path did not
    BASENAME = 2        # unique filename match
    BASENAME_SIZE = 3   # unique filename match confirmed by file size
    EXACT_PATH = 4      # full relative path matched

    @property
    def label(self):
        return {
            MatchConfidence.EXACT_PATH: 'exact path',
            MatchConfidence.BASENAME_SIZE: 'filename + size',
            MatchConfidence.BASENAME: 'filename',
            MatchConfidence.METADATA: 'title + author',
        }[self]

    @property
    def is_trusted(self):
        """Matches below this are pre-unticked in the review dialog."""
        return self >= MatchConfidence.BASENAME


@dataclass(frozen=True)
class DeviceFileRow:
    """One row of ``files`` joined to ``folders`` and ``books_impl``."""
    book_id: int
    storage_id: int
    folder_name: str
    filename: str
    size: Optional[int] = None
    ext: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    firstauthor: Optional[str] = None
    series: Optional[str] = None
    numinseries: Optional[int] = None
    relpath: str = ''
    device_path: str = ''


@dataclass(frozen=True)
class BookLink:
    """A calibre book paired with a book on the device."""
    calibre_id: int
    device_book_id: int
    lpath: str
    device_path: str
    confidence: MatchConfidence
    title: str = ''

    @property
    def is_trusted(self):
        return self.confidence.is_trusted


@dataclass(frozen=True)
class DeviceState:
    """Reading state for one book, from ``books_settings``."""
    book_id: int
    completed: Optional[int] = None
    completed_ts: Optional[int] = None
    favorite: Optional[int] = None
    favorite_ts: Optional[int] = None
    position: Optional[str] = None
    position_ts: Optional[int] = None
    cpage: Optional[int] = None
    npage: Optional[int] = None
    opentime: Optional[int] = None
    exists: bool = True

    @property
    def progress(self):
        """Percent read, or ``None`` when the device has no page counts."""
        if not self.npage:
            return None
        if self.cpage is None:
            return None
        return round(min(100.0, 100.0 * self.cpage / self.npage), 2)


@dataclass
class ProposedChange:
    link: BookLink
    field: str
    direction: Side
    old: Any
    new: Any
    reason: str
    selected: bool = True
    #: What to show the user. ``new`` is whatever the target side stores,
    #: which for the device means a raw 1 or 0 where a reader expects
    #: "Yes" or "No". These hold the human form for both sides.
    display_old: Any = None
    display_new: Any = None

    def __post_init__(self):
        if self.display_old is None:
            self.display_old = self.old
        if self.display_new is None:
            self.display_new = self.new

    @property
    def confidence(self):
        return self.link.confidence


@dataclass
class SyncPlan:
    calibre_changes: List[ProposedChange] = field(default_factory=list)
    device_changes: List[ProposedChange] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)
    unmatched_calibre: List[int] = field(default_factory=list)
    device_only: List[DeviceFileRow] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)

    @property
    def all_changes(self):
        return self.calibre_changes + self.device_changes

    @property
    def is_empty(self):
        return not self.calibre_changes and not self.device_changes

    def selected(self, side):
        source = (self.calibre_changes if side is Side.CALIBRE
                  else self.device_changes)
        return [c for c in source if c.selected]

    def summary(self):
        return ('%d change(s) for calibre, %d for the device, '
                '%d book(s) not matched, %d device book(s) not in the library'
                % (len(self.calibre_changes), len(self.device_changes),
                   len(self.unmatched_calibre), len(self.device_only)))
