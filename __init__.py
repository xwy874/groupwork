"""Unified multi-model LLM gateway for the Werewolf project (成员3).

Public surface kept minimal on purpose — 成员1 / 成员2 only need:

    from werewolf_gateway import LLMGateway, AgentResponse, SlidingWindowContext
"""
from .client import (
    FatalHTTPError,
    GatewayError,
    LLMGateway,
    RetryableHTTPError,
)
from .config import DEFAULT_ROLE_MODEL, MODEL_CATALOGUE, GatewaySettings
from .context import SlidingWindowContext
from .engine import (
    DEFAULT_ROSTER,
    EventType,
    Faction,
    GameEvent,
    IllegalTransition,
    LEGAL_TRANSITIONS,
    Phase,
    PlayerState,
    WerewolfGameEngine,
    generate_random_roster,
)
from .schemas import ActionType, AgentResponse
from .session import AgentSession, GameSession

try:
    # 成员4 分析依赖 numpy（可选）；缺失时不影响引擎本体导入。
    from .analysis_member4 import analyze, print_report
    _HAS_ANALYSIS = True
except ImportError:
    _HAS_ANALYSIS = False

__all__ = [
    "LLMGateway",
    "GatewayError",
    "RetryableHTTPError",
    "FatalHTTPError",
    "GatewaySettings",
    "AgentResponse",
    "ActionType",
    "MODEL_CATALOGUE",
    "DEFAULT_ROLE_MODEL",
    "SlidingWindowContext",
    "GameSession",
    "AgentSession",
    # 成员1：状态机引擎
    "WerewolfGameEngine",
    "Phase",
    "PlayerState",
    "Faction",
    "EventType",
    "GameEvent",
    "IllegalTransition",
    "LEGAL_TRANSITIONS",
    "DEFAULT_ROSTER",
    "generate_random_roster",
]

if _HAS_ANALYSIS:
    __all__ += ["analyze", "print_report"]
