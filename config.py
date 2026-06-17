"""Gateway configuration: env loading + role→model routing table."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

from dotenv import load_dotenv

_PKG_DIR = Path(__file__).resolve().parent
load_dotenv(_PKG_DIR / ".env")
load_dotenv()  # also pick up a .env in the caller's cwd if present


# Canonical model catalogue. DeepSeek 官方端点直接用真实模型名。
MODEL_CATALOGUE: Dict[str, str] = {
    "deepseek-v4-pro": "deepseek-v4-pro",       # 强推理模型，狼/神职/裁判用
    "deepseek-v4-flash": "deepseek-v4-flash",   # 轻量推理模型，平民用
    "deepseek-chat": "deepseek-chat",           # 非推理，快/便宜，给上下文摘要器用
    "deepseek-reasoner": "deepseek-reasoner",
    # 兼容旧别名（SJTU 网关时期），保留以防其他成员代码引用
    "minimax": "minimax-m2.7",
    "minimax-m2.7": "minimax-m2.7",
    "glm": "glm-5.1",
    "glm-5.1": "glm-5.1",
    "qwen": "qwen3.5-27b",
    "qwen3.5-27b": "qwen3.5-27b",
}


# Werewolf role → model alias. 多模型对抗：deception-heavy 的狼+神职用强推理模型
# deepseek-v4-pro，平民用更轻量的 deepseek-v4-flash —— 这样才有“强模型 vs 弱模型”的
# 策略代差对照（成员4 分析的前提）。评价者 moderator 也用 v4-pro（裁决/评估需强推理）；
# 上下文摘要器是内部压缩工具，仍用 deepseek-chat（见 .env LLM_CTX_SUMMARY_MODEL，
# 小 max_tokens 调用，换推理模型会空 content）。
_STRONG = "deepseek-v4-pro"
_WEAK = "deepseek-v4-flash"

# 反转开关：设环境变量 WW_REVERSE_ROLES=1 时，强/弱模型在角色上对调
# （狼+神职→弱 flash，村民→强 pro），用于“模型换边”对照实验。
# moderator 始终用强模型（裁判需强推理，不参与对照）。
# 全弱基线：设 WW_ALL_FLASH=1 时，所有角色（含 moderator）统一用 flash —— 无模型代差
# 的对照组，胜负只由阵营平衡决定。优先级高于 reverse 开关。
_ALL_FLASH = os.getenv("WW_ALL_FLASH", "").strip() in ("1", "true", "True")
_ALL_PRO = os.getenv("WW_ALL_PRO", "").strip() in ("1", "true", "True")
_REVERSE = os.getenv("WW_REVERSE_ROLES", "").strip() in ("1", "true", "True")
if _ALL_PRO:
    # 全强基线：所有角色（含 moderator）统一用 pro —— 与全弱基线对称的对照组，
    # 无模型代差，胜负只由阵营平衡决定。优先级最高。
    _DECEPTION_SIDE = _VILLAGER_SIDE = _MODERATOR = _STRONG
elif _ALL_FLASH:
    _DECEPTION_SIDE = _VILLAGER_SIDE = _MODERATOR = _WEAK
else:
    _DECEPTION_SIDE = _WEAK if _REVERSE else _STRONG   # 狼+神职
    _VILLAGER_SIDE = _STRONG if _REVERSE else _WEAK    # 村民
    _MODERATOR = _STRONG

DEFAULT_ROLE_MODEL: Dict[str, str] = {
    "villager":  _VILLAGER_SIDE,
    "werewolf":  _DECEPTION_SIDE,
    "seer":      _DECEPTION_SIDE,
    "witch":     _DECEPTION_SIDE,
    "hunter":    _DECEPTION_SIDE,
    "guard":     _DECEPTION_SIDE,
    "moderator": _MODERATOR,
}


@dataclass
class GatewaySettings:
    base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "").rstrip("/"))
    api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    timeout: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT", "30")))
    max_retries: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_RETRIES", "5")))
    default_temperature: float = field(
        default_factory=lambda: float(os.getenv("LLM_DEFAULT_TEMPERATURE", "0.7"))
    )
    max_tokens_default: int = field(
        default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS_DEFAULT", "512"))
    )
    max_tokens_hard_cap: int = field(
        default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS_HARD_CAP", "2048"))
    )
    max_concurrency: int = field(
        default_factory=lambda: int(os.getenv("LLM_MAX_CONCURRENCY", "10"))
    )
    max_concurrency_per_model: int = field(
        default_factory=lambda: int(os.getenv("LLM_MAX_CONCURRENCY_PER_MODEL", "6"))
    )
    ctx_keep_turns: int = field(
        default_factory=lambda: int(os.getenv("LLM_CTX_KEEP_TURNS", "3"))
    )
    ctx_summary_model: str = field(
        default_factory=lambda: os.getenv("LLM_CTX_SUMMARY_MODEL", "minimax-m2.7")
    )
    log_dir: str = field(
        default_factory=lambda: os.getenv("LLM_LOG_DIR", "")
    )
    role_model: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.base_url or not self.api_key:
            raise RuntimeError(
                "LLM_BASE_URL and LLM_API_KEY must be set (see .env.example)."
            )
        merged = dict(DEFAULT_ROLE_MODEL)
        for role in list(merged.keys()):
            override = os.getenv(f"MODEL_{role.upper()}")
            if override:
                merged[role] = override
        merged.update(self.role_model)
        self.role_model = merged

    def resolve_model(self, role_or_model: str) -> str:
        """Accept either a role name ('werewolf') or a model alias ('qwen').

        Resolution order: role table → catalogue alias → raw passthrough.
        """
        key = role_or_model.lower().strip()
        if key in self.role_model:
            key = self.role_model[key]
        return MODEL_CATALOGUE.get(key, key)

    def clamp_max_tokens(self, requested: int) -> int:
        """Apply the hard ceiling so a runaway prompt cannot drain the budget."""
        if requested <= 0:
            return self.max_tokens_default
        return min(requested, self.max_tokens_hard_cap)
