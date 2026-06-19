"""B10 BackupHandler: page API surface for backup/restore.

Backs the /backups (GET) and /backups/restore (POST) endpoints added
to PluginPageApi. The BackupManager instance lives on
PluginInitializer.backup_manager; when backup is disabled the handler
returns a graceful {status: error, message: ...} rather than 500.
"""
from __future__ import annotations
from typing import Any, Optional


class BackupHandler:
    def __init__(self, utils) -> None:
        self.utils = utils

    def list_backups(self, manager) -> dict[str, Any]:
        if manager is None:
            return self.utils.err("backup is disabled (enable_backup=false or interval=0)")
        try:
            recs = manager.list_backups()
            data = [{
                "backup_id": r.backup_id,
                "created_at": r.created_at,
                "reason": r.reason,
                "version": r.version,
                "engram_count": r.engram_count,
                "byte_size": r.byte_size,
            } for r in recs]
            return self.utils.ok({"backups": data, "count": len(data),
                                  "backup_dir": manager.backup_dir})
        except Exception as e:
            return self.utils.err("list_backups failed: " + repr(e))

    def restore_backup(self, manager, *, backup_id: str = "") -> dict[str, Any]:
        if manager is None:
            return self.utils.err("backup is disabled")
        if not backup_id:
            return self.utils.err("missing backup_id")
        # Refuse to restore the live db path; refuse unknown ids
        if backup_id.endswith(".db") or "/" in backup_id or "\\" in backup_id:
            return self.utils.err("invalid backup_id format")
        recs = manager.list_backups()
        known = {r.backup_id for r in recs}
        if backup_id not in known:
            return self.utils.err("backup_id not found: " + backup_id)
        try:
            ok = manager.restore(backup_id)
            if not ok:
                return self.utils.err("restore returned false")
            return self.utils.ok({"restored": backup_id,
                                  "note": "service connection may need reopen"})
        except Exception as e:
            return self.utils.err("restore failed: " + repr(e))