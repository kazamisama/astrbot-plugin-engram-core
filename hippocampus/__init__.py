from .service import MemoryService
from .config import MemoryConfig
from .types import (Engram, Cue, RecallResult, SemanticRecallResult,
                    Entity, Relation, Trigger, MemoryType, TriggerStatus)
from .embeddings import EmbeddingProvider, HashEmbeddingProvider
from .llm import LLMProvider, RuleLLMProvider, OpenAILLMProvider, AstrBotLLMProvider, ProxyLLMProvider
from .providers import (ProviderRegistry, OpenAIEmbeddingProvider, ProxyEmbeddingProvider,
                        default_registry)
from .semantic import SemanticStore, EntityExtractor
from .prospective import ProspectiveStore, TimeParser, ProspectiveScheduler
from .profile import ProfileStore, ProfileFact
from .persona import PersonaStore, Persona
from .activation import SpreadingActivation
from .retrieval import (RRFFusion, RankedCandidate, FusedCandidate,
                       rrf_fuse, RRF_K_DEFAULT,
                       DualRouteRetriever, DualRouteConfig, RouteKind,
                       GraphRetriever, EntityMatch,
                       GraphKeywordRetriever, GraphVectorRetriever)
from .graph_store import GraphStore
from .tools import MemoryTool, build_recall_tool, build_memorize_tool, all_tools
from .types import MemoryAtom, AtomStatus, AtomType, DecayType
from .atom_store import AtomStore
from .atom_lifecycle_manager import AtomLifecycleManager
from .config_manager import ConfigManager, LABELS
from .managers.backup_manager import BackupManager, BackupRecord

# 单一版本事实源:metadata.yaml / @register / export payload 都引用这里。
__version__ = "1.47.0"
# 导出 JSON 的格式版本(与插件版本解耦,仅在导出结构变化时才 bump)。
EXPORT_FORMAT_VERSION = "1.1"
__all__ = [
    "__version__", "EXPORT_FORMAT_VERSION",
    "MemoryService", "MemoryConfig",
    "Engram", "Cue", "RecallResult", "SemanticRecallResult",
    "Entity", "Relation", "Trigger", "MemoryType", "TriggerStatus",
    "EmbeddingProvider", "HashEmbeddingProvider",
    "LLMProvider", "RuleLLMProvider", "OpenAILLMProvider", "AstrBotLLMProvider", "ProxyLLMProvider",
    "ProviderRegistry", "OpenAIEmbeddingProvider", "ProxyEmbeddingProvider", "default_registry",
    "SemanticStore", "EntityExtractor",
    "ProspectiveStore", "TimeParser", "ProspectiveScheduler",
    "ProfileStore", "ProfileFact", "PersonaStore", "Persona", "SpreadingActivation",
    "RRFFusion", "RankedCandidate", "FusedCandidate", "rrf_fuse", "RRF_K_DEFAULT",
    "DualRouteRetriever", "DualRouteConfig", "RouteKind",
    "GraphRetriever", "EntityMatch",
    "GraphKeywordRetriever", "GraphVectorRetriever",
    "GraphStore",
    "MemoryTool", "build_recall_tool", "build_memorize_tool", "all_tools",
    "MemoryAtom", "AtomStatus", "AtomType", "DecayType",
    "AtomStore", "AtomLifecycleManager",
    "ConfigManager", "LABELS",
    "BackupManager", "BackupRecord",
]
