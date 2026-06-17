"""Optional adapter for the `instructor` library + Pydantic.

`instructor` patches an OpenAI-compatible client so `.chat.completions.create(..., response_model=Foo)`
returns a `Foo` instance directly, with built-in re-prompt-on-validation-failure.

Use it only if you've installed the optional deps:
    pip install instructor openai

Why we still keep our own `chat_json`:
- Some teammates may prefer one fewer dependency;
- Streaming + retries + concurrency live in our `LLMGateway`, not in instructor;
- This adapter is a clean fallback for anyone who really wants instructor's ergonomics.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Type, TypeVar

from pydantic import BaseModel

from .config import GatewaySettings
from .schemas import JSON_CONTRACT_HINT, AgentResponse

if TYPE_CHECKING:
    pass

T = TypeVar("T", bound=BaseModel)


def get_instructor_client(settings: Optional[GatewaySettings] = None):
    """Return a sync instructor-patched OpenAI client pointing at our gateway.

    Raises ImportError with a friendly message if optional deps are missing.
    """
    try:
        import instructor
        from openai import OpenAI
    except ImportError as e:
        raise ImportError(
            "instructor adapter requires `pip install instructor openai`"
        ) from e

    s = settings or GatewaySettings()
    base = OpenAI(api_key=s.api_key, base_url=s.base_url, timeout=s.timeout)
    return instructor.from_openai(base, mode=instructor.Mode.JSON)


def ask(
    role: str,
    messages: List[dict],
    *,
    schema: Type[T] = AgentResponse,  # type: ignore[assignment]
    settings: Optional[GatewaySettings] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> T:
    """Synchronous one-shot helper. Mirrors `LLMGateway.chat_json` semantics.

    Example:
        from werewolf_gateway.instructor_adapter import ask
        from werewolf_gateway import AgentResponse
        resp = ask("werewolf", [{"role":"user","content":"vote now"}])
    """
    s = settings or GatewaySettings()
    client = get_instructor_client(s)
    if not messages or messages[0].get("role") != "system":
        messages = [{"role": "system", "content": JSON_CONTRACT_HINT}] + messages
    return client.chat.completions.create(
        model=s.resolve_model(role),
        messages=messages,
        response_model=schema,
        max_tokens=s.clamp_max_tokens(
            max_tokens if max_tokens is not None else s.max_tokens_default
        ),
        temperature=s.default_temperature if temperature is None else temperature,
        max_retries=s.max_retries,
    )
