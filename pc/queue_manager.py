"""
Zel.NeD — Queue Manager
=========================
Manages the ordered list of files/folders to be sent to the 3DS.
Supports: add, remove, reorder, expand folders, and track status.
"""

import threading
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import List, Optional, Tuple


class ItemStatus(Enum):
    PENDING   = auto()
    SENDING   = auto()
    DONE      = auto()
    FAILED    = auto()
    CANCELLED = auto()
    PAUSED    = auto()


@dataclass
class QueueItem:
    local_path: Path          # Absolute path on PC
    remote_path: str          # Relative destination path on sdmc:/
    file_size: int            # Bytes (0 for directories)
    is_dir: bool              # True = directory creation only
    status: ItemStatus        = field(default=ItemStatus.PENDING)
    bytes_sent: int           = 0
    error_msg: Optional[str]  = None

    @property
    def progress(self) -> float:
        """0.0 – 1.0 progress fraction."""
        if self.file_size == 0:
            return 1.0 if self.status == ItemStatus.DONE else 0.0
        return min(self.bytes_sent / self.file_size, 1.0)

    @property
    def display_name(self) -> str:
        return self.local_path.name + ("/" if self.is_dir else "")

    @property
    def size_str(self) -> str:
        if self.is_dir:
            return "—"
        mb = self.file_size / 1_048_576
        if mb < 1.0:
            return f"{self.file_size / 1024:.1f} KB"
        return f"{mb:.1f} MB"


class QueueManager:
    """
    Thread-safe queue of files and directories to transfer to the 3DS.

    All public methods are safe to call from multiple threads (GUI + worker).
    """

    def __init__(self):
        self._items: List[QueueItem] = []
        self._lock = threading.Lock()

    # ── Adding items ──────────────────────────────────────────────────────────

    def add_path(self, local_path: Path, base_remote: str = "") -> int:
        """
        Add a file or recursively expand a directory.
        Returns the number of items added to the queue.
        """
        local_path = Path(local_path)
        items_to_add = self._expand(local_path, base_remote)
        with self._lock:
            self._items.extend(items_to_add)
        return len(items_to_add)

    def _expand(self, path: Path, base_remote: str) -> List[QueueItem]:
        """Recursively expand path into a flat list of QueueItems."""
        result = []
        if path.is_dir():
            remote = (base_remote + "/" + path.name).lstrip("/")
            result.append(QueueItem(
                local_path=path,
                remote_path=remote,
                file_size=0,
                is_dir=True,
            ))
            for child in sorted(path.iterdir()):
                result.extend(self._expand(child, remote))
        elif path.is_file():
            remote = (base_remote + "/" + path.name).lstrip("/")
            result.append(QueueItem(
                local_path=path,
                remote_path=remote,
                file_size=path.stat().st_size,
                is_dir=False,
            ))
        return result

    # ── Querying ──────────────────────────────────────────────────────────────

    def snapshot(self) -> List[QueueItem]:
        """Return a copy of the current queue."""
        with self._lock:
            return list(self._items)

    def next_pending(self) -> Optional[QueueItem]:
        """Return the first PENDING item without removing it."""
        with self._lock:
            for item in self._items:
                if item.status == ItemStatus.PENDING:
                    return item
        return None

    def all_done(self) -> bool:
        with self._lock:
            return all(i.status in (ItemStatus.DONE, ItemStatus.CANCELLED, ItemStatus.FAILED)
                       for i in self._items)

    def total_stats(self) -> Tuple[int, int, int]:
        """Returns (total_files, done_files, total_bytes)."""
        with self._lock:
            files = [i for i in self._items if not i.is_dir]
            done  = [i for i in files if i.status == ItemStatus.DONE]
            return len(files), len(done), sum(i.file_size for i in files)

    # ── Mutating ──────────────────────────────────────────────────────────────

    def mark_sending(self, item: QueueItem):
        with self._lock:
            item.status = ItemStatus.SENDING

    def update_progress(self, item: QueueItem, bytes_sent: int):
        with self._lock:
            item.bytes_sent = bytes_sent

    def mark_done(self, item: QueueItem):
        with self._lock:
            item.status = ItemStatus.DONE
            item.bytes_sent = item.file_size

    def mark_failed(self, item: QueueItem, msg: str):
        with self._lock:
            item.status = ItemStatus.FAILED
            item.error_msg = msg

    def cancel_item(self, index: int):
        with self._lock:
            if 0 <= index < len(self._items):
                item = self._items[index]
                if item.status == ItemStatus.PENDING:
                    item.status = ItemStatus.CANCELLED

    def remove_done(self):
        with self._lock:
            self._items = [i for i in self._items
                           if i.status not in (ItemStatus.DONE, ItemStatus.CANCELLED)]

    def move_up(self, index: int):
        with self._lock:
            if index > 0 and index < len(self._items):
                self._items[index - 1], self._items[index] = \
                    self._items[index], self._items[index - 1]

    def move_down(self, index: int):
        with self._lock:
            if 0 <= index < len(self._items) - 1:
                self._items[index], self._items[index + 1] = \
                    self._items[index + 1], self._items[index]

    def clear(self):
        with self._lock:
            self._items.clear()
