"""Shared JSON contract between Agent (成员2) and the state machine (成员1)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


ActionType = Literal[
    "speak",       # day-phase free speech
    "vote",        # day-phase vote
    "no_vote",     # explicit abstention
    "kill",        # werewolf night kill
    "check",       # seer inspect
    "save",        # witch antidote
    "poison",      # witch poison
    "protect",     # guard protect
    "shoot",       # hunter shot on death
    "skip",        # use a skill / pass
]


class AgentResponse(BaseModel):
    """Every agent turn MUST return exactly this structure."""

    thought: str = Field(
        ..., description="Private chain-of-thought, never shown to other players."
    )
    speech: str = Field(
        "", description="Public utterance shown to other players. Empty for night actions."
    )
    action: ActionType = Field(..., description="Discrete action token consumed by the state machine.")
    target: Optional[int] = Field(
        None,
        description="Seat number (1-indexed) the action applies to, or null when N/A.",
        ge=0,
    )
    confidence: float = Field(
        0.5, ge=0.0, le=1.0, description="Self-rated confidence in the chosen action."
    )


JSON_CONTRACT_HINT = """You MUST respond with a single JSON object and nothing else.
The object must match this schema exactly:
{
  "thought":    string,                  // private reasoning
  "speech":     string,                  // what you say out loud (empty at night)
  "action":     one of ["speak","vote","no_vote","kill","check","save",
                        "poison","protect","shoot","skip"],
  "target":     integer seat number or null,
  "confidence": float in [0,1]
}
Do NOT wrap the JSON in markdown fences. Do NOT add commentary before or after."""
