"""page_api_modules: per-domain handlers for the AstrBot Dashboard page API.

B9: 4 handler modules. Each handler class takes a PageApiUtils and a
MemoryService (passed at call time, not constructor time, so the
plugin's service ref can be re-bound without rebuilding handlers).
"""
from .utils import PageApiUtils
from .stats import StatsHandler
from .memory import MemoryHandler
from .recall import RecallHandler
from .graph import GraphHandler
from .backup import BackupHandler

__all__ = [
    "PageApiUtils",
    "StatsHandler",
    "MemoryHandler",
    "RecallHandler",
    "GraphHandler",
    "BackupHandler",
]
