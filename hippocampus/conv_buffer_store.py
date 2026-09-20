"""Durable home for the in-memory ConversationBuffer (v1.76.16).

WHY THIS EXISTS
---------------
`ConversationBuffer` is the only place a conversation window that has not been
summarized yet exists. It used to be pure memory, so a plugin reload discarded
every open window: the messages had already been captured into
`daily_messages`, but they never became long-term memory. On the live bot
AstrBot reloaded the plugin 7 times in a single day, and the 17:58 reload
dropped a window whose last message was 17:54.31 -- it was below
`summary_min_messages` and had not been idle long enough, and `terminate()`'s
`flush_all()` did not get to run.

So the buffer is persisted on every change (`ConversationBuffer.snapshot()`
after each feed, and after each flush so an already-summarized window cannot
resurrect) and rehydrated on startup (`ConversationBuffer.restore()`).

Design notes
------------
- Writes are atomic: a temp file in the same directory is `os.replace`d over the
  target, so a crash mid-write can never leave a half-written file. `fsync` is
  deliberately NOT called -- this protects against a process/reload, not a power
  loss (and a power loss costs at most the raw-message flush interval anyway).
- Every failure is logged and swallowed: persistence must never take the ingest
  path down, and losing the file is strictly better than losing the bot.
"""
from __future__ import annotations

import json
import os
import tempfile


class ConvBufferStore:
    """Tiny atomic JSON store for ConversationBuffer snapshots."""

    def __init__(self, path: str) -> None:
        self.path = path

    def load(self) -> dict:
        """Return the saved snapshot, or {} when absent/unreadable/corrupt."""
        if not self.path:
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return {}
        except Exception as e:
            print("[hippocampus] conv buffer restore failed: " + repr(e))
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, data: dict) -> bool:
        """Atomically write *data*. Returns False (and logs) on any failure."""
        if not self.path:
            return False
        directory = os.path.dirname(self.path) or "."
        tmp = ""
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".convbuf-",
                                       suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            os.replace(tmp, self.path)
            return True
        except Exception as e:
            print("[hippocampus] conv buffer persist failed: " + repr(e))
            return False
        finally:
            if tmp:
                try:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                except OSError:
                    pass

    def clear(self) -> None:
        """Remove the snapshot (best-effort)."""
        if not self.path:
            return
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        except OSError as e:
            print("[hippocampus] conv buffer clear failed: " + repr(e))
