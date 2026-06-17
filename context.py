"""Day 10-12: Sliding-window context manager with async summary buffer.

Goal: keep the prompt cheap as the game drags on.

Strategy:
    [system] + [running summary] + [last K (user, assistant) turns verbatim]

When a new turn pushes the window beyond K, the oldest turn is handed off to a
cheap model (configurable via LLM_CTX_SUMMARY_MODEL, default minimax-m2.7) which
folds it into the running summary asynchronously, so the next API call already
sees a compact prefix.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from .client import LLMGateway


_SUMMARY_PROMPT = (
    "You are a compression engine for a Werewolf game transcript. "
    "Merge the existing running summary with the new turn into ONE concise paragraph "
    "(<=120 words) that preserves: who said what, voting outcomes, accusations, "
    "claimed roles, and night-action results. Output the paragraph only — no preface."
)


@dataclass
class _Turn:
    user: str
    assistant: str


@dataclass
class SlidingWindowContext:
    """Per-agent rolling context.

    Args:
        system: the agent's static role/system prompt (never summarized)
        keep_turns: how many most-recent (user, assistant) pairs to keep verbatim
    """

    system: str
    keep_turns: int = 2
    summary: str = ""
    _turns: List[_Turn] = field(default_factory=list)
    _pending_eviction: Optional[_Turn] = None
    _summary_task: Optional[asyncio.Task] = None

    # ---- mutation ----
    def add_user(self, content: str) -> None:
        self._turns.append(_Turn(user=content, assistant=""))

    def add_assistant(self, content: str) -> None:
        if not self._turns or self._turns[-1].assistant:
            self._turns.append(_Turn(user="", assistant=content))
        else:
            self._turns[-1].assistant = content

    # ---- read ----
    def messages(self) -> List[Dict[str, str]]:
        """Build the message list to send to the LLM."""
        sys = self.system
        if self.summary:
            sys = f"{sys}\n\n[Running summary of earlier rounds]\n{self.summary}"
        out: List[Dict[str, str]] = [{"role": "system", "content": sys}]
        for t in self._turns:
            if t.user:
                out.append({"role": "user", "content": t.user})
            if t.assistant:
                out.append({"role": "assistant", "content": t.assistant})
        return out

    # ---- maintenance ----
    async def maintain(self, gw: "LLMGateway") -> None:
        """Evict the oldest turn beyond `keep_turns` and fold it into the summary.

        Call this once per agent turn, *after* you've appended the assistant reply.
        Cheap when nothing needs evicting.
        """
        # Wait for any prior summary task before deciding what to evict.
        if self._summary_task is not None and not self._summary_task.done():
            await self._summary_task

        while len(self._turns) > self.keep_turns:
            self._pending_eviction = self._turns.pop(0)
            await self._fold_pending(gw)

    async def _fold_pending(self, gw: "LLMGateway") -> None:
        if self._pending_eviction is None:
            return
        evicted = self._pending_eviction
        self._pending_eviction = None
        prompt_user = (
            f"EXISTING SUMMARY:\n{self.summary or '(empty)'}\n\n"
            f"NEW TURN TO MERGE:\nUser said: {evicted.user}\n"
            f"Assistant replied: {evicted.assistant}"
        )
        new_summary = await gw.chat_raw(
            role=gw.settings.ctx_summary_model,
            messages=[
                {"role": "system", "content": _SUMMARY_PROMPT},
                {"role": "user", "content": prompt_user},
            ],
            temperature=0.2,
            max_tokens=200,
        )
        self.summary = new_summary.strip()

    # ---- introspection ----
    def approx_token_count(self) -> int:
        """Cheap token estimate (chars/4) — useful for logging, not billing."""
        total = len(self.system) + len(self.summary)
        for t in self._turns:
            total += len(t.user) + len(t.assistant)
        return total // 4
