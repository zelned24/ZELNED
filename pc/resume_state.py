"""
Zel.NeD — Resume State Manager
================================
Saves and loads transfer session state so interrupted transfers
can be resumed automatically after reconnection.

State file location: ~/.zelned/resume/<session_id>.json
"""

import json
import hashlib
import time
from pathlib import Path
from typing import Optional, Dict, Any

RESUME_DIR = Path.home() / ".zelned" / "resume"


def _session_id(ip: str, remote_path: str) -> str:
    """Deterministic session ID from target IP and remote file path."""
    key = f"{ip}:{remote_path}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


class ResumeState:
    """
    Manages a single file's resume state.

    Usage:
        rs = ResumeState(ip, remote_path)
        offset = rs.load()          # 0 if no saved state
        rs.save(bytes_received)     # call periodically / on disconnect
        rs.clear()                  # call on successful completion
    """

    def __init__(self, ip: str, remote_path: str):
        self.ip          = ip
        self.remote_path = remote_path
        self._id         = _session_id(ip, remote_path)
        self._path       = RESUME_DIR / f"{self._id}.json"

    def load(self) -> int:
        """Return saved byte offset, or 0 if no resume state exists."""
        if not self._path.exists():
            return 0
        try:
            data: Dict[str, Any] = json.loads(self._path.read_text(encoding="utf-8"))
            # Validate the state is for the same file
            if data.get("remote_path") != self.remote_path:
                return 0
            return int(data.get("offset", 0))
        except (json.JSONDecodeError, ValueError, KeyError):
            return 0

    def save(self, offset: int):
        """Persist the current byte offset to disk."""
        RESUME_DIR.mkdir(parents=True, exist_ok=True)
        state = {
            "ip":          self.ip,
            "remote_path": self.remote_path,
            "offset":      offset,
            "saved_at":    time.time(),
        }
        self._path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def clear(self):
        """Remove the resume state after a successful transfer."""
        if self._path.exists():
            self._path.unlink()

    @staticmethod
    def list_all() -> list:
        """Return all currently saved resume states as dicts."""
        if not RESUME_DIR.exists():
            return []
        states = []
        for f in RESUME_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                states.append(data)
            except (json.JSONDecodeError, OSError):
                continue
        return states

    @staticmethod
    def clear_all():
        """Delete all saved resume states."""
        if RESUME_DIR.exists():
            for f in RESUME_DIR.glob("*.json"):
                f.unlink(missing_ok=True)
