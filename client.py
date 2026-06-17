"""Async, OpenAI-compatible multi-model gateway with strict JSON validation.

Adds, on top of the Day 1-7 baseline:

- Day 8-9   Concurrency semaphore + tenacity retry (exp backoff + jitter, 429/5xx/timeouts only)
- Day 13-14 Token-by-token streaming via `chat_stream`
- Safety    Hard `max_tokens` ceiling and per-request `timeout=30s` circuit breaker
- 12-player Per-model semaphore + usage stats + optional JSONL call log
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from .config import GatewaySettings
from .schemas import JSON_CONTRACT_HINT, AgentResponse

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


# ---------- error taxonomy ----------
class GatewayError(RuntimeError):
    """Base class for all gateway-side errors."""


class RetryableHTTPError(GatewayError):
    """429 / 5xx / network timeout — safe to retry with backoff."""


class FatalHTTPError(GatewayError):
    """4xx other than 429 — retrying will not help (auth, bad request, ...)."""


# ---------- gateway ----------
class LLMGateway:
    """Single entry point for all agent → LLM traffic.

    Usage:
        async with LLMGateway() as gw:
            resp = await gw.chat_json(role="werewolf", messages=[...])
    """

    def __init__(self, settings: Optional[GatewaySettings] = None) -> None:
        self.settings = settings or GatewaySettings()
        self._client: Optional[httpx.AsyncClient] = None
        self._sem: Optional[asyncio.Semaphore] = None
        self._model_sems: Dict[str, asyncio.Semaphore] = {}
        self.stats = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        self._log_fp = None

    def _get_model_sem(self, model: str) -> asyncio.Semaphore:
        sem = self._model_sems.get(model)
        if sem is None:
            sem = asyncio.Semaphore(self.settings.max_concurrency_per_model)
            self._model_sems[model] = sem
        return sem

    def _maybe_open_log(self) -> None:
        if self._log_fp is not None or not self.settings.log_dir:
            return
        Path(self.settings.log_dir).mkdir(parents=True, exist_ok=True)
        path = Path(self.settings.log_dir) / f"calls-{int(time.time())}-{os.getpid()}.jsonl"
        self._log_fp = open(path, "a", encoding="utf-8")

    def _log(self, record: Dict[str, Any]) -> None:
        if self._log_fp is None:
            return
        self._log_fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._log_fp.flush()

    # ---- lifecycle ----
    async def __aenter__(self) -> "LLMGateway":
        self._open()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    def _open(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.settings.base_url,
                timeout=httpx.Timeout(self.settings.timeout, connect=10.0),
                headers={
                    "Authorization": f"Bearer {self.settings.api_key}",
                    "Content-Type": "application/json",
                },
            )
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.settings.max_concurrency)
        self._maybe_open_log()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        if self._log_fp is not None:
            self._log_fp.close()
            self._log_fp = None

    # ---- low-level POST with semaphore + tenacity ----
    async def _post_chat(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._open()
        assert self._client is not None and self._sem is not None
        model_sem = self._get_model_sem(payload["model"])
        call_id = uuid.uuid4().hex[:8]

        async def _do() -> Dict[str, Any]:
            t0 = time.perf_counter()
            async with self._sem:  # type: ignore[union-attr]
                async with model_sem:
                    resp = await self._client.post("/chat/completions", json=payload)  # type: ignore[union-attr]
            latency_ms = int((time.perf_counter() - t0) * 1000)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise RetryableHTTPError(
                    f"HTTP {resp.status_code}: {resp.text[:300]}"
                )
            if resp.status_code >= 400:
                raise FatalHTTPError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            usage = data.get("usage") or {}
            self.stats["calls"] += 1
            self.stats["prompt_tokens"] += int(usage.get("prompt_tokens", 0))
            self.stats["completion_tokens"] += int(usage.get("completion_tokens", 0))
            self._log({
                "ts": time.time(),
                "call_id": call_id,
                "model": payload["model"],
                "latency_ms": latency_ms,
                "usage": usage,
                "status": resp.status_code,
            })
            return data

        try:
            async for attempt in AsyncRetrying(
                reraise=True,
                stop=stop_after_attempt(self.settings.max_retries),
                wait=wait_random_exponential(multiplier=1, max=20),
                retry=retry_if_exception_type(
                    (RetryableHTTPError, httpx.TimeoutException, httpx.NetworkError)
                ),
            ):
                with attempt:
                    return await _do()
        except RetryError as e:
            raise GatewayError(f"retry exhausted: {e.last_attempt.exception()}") from e
        raise GatewayError("unreachable")  # for type checker

    # ---- streaming POST ----
    async def _stream_chat(self, payload: Dict[str, Any]) -> AsyncIterator[str]:
        self._open()
        assert self._client is not None and self._sem is not None
        payload = {**payload, "stream": True}

        async with self._sem:  # type: ignore[union-attr]
            async with self._client.stream(  # type: ignore[union-attr]
                "POST", "/chat/completions", json=payload
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")[:300]
                    if resp.status_code == 429 or resp.status_code >= 500:
                        raise RetryableHTTPError(f"HTTP {resp.status_code}: {body}")
                    raise FatalHTTPError(f"HTTP {resp.status_code}: {body}")
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                        delta = (
                            chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                        )
                        if delta:
                            yield delta
                    except json.JSONDecodeError:
                        continue

    # ---------- public ----------
    async def chat_raw(
        self,
        role: str,
        messages: List[Dict[str, str]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
        extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Return the raw assistant text. `role` is either a werewolf role or model alias."""
        payload = self._build_payload(
            role=role,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            extra=extra,
        )
        data = await self._post_chat(payload)
        return data["choices"][0]["message"]["content"] or ""

    async def chat_stream(
        self,
        role: str,
        messages: List[Dict[str, str]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[str]:
        """Yield content deltas as they arrive (Day 13-14 streaming hook for 成员5)."""
        payload = self._build_payload(
            role=role,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=False,  # streaming + json_object can be flaky on some providers
            extra=extra,
        )
        async for delta in self._stream_chat(payload):
            yield delta

    async def chat_json(
        self,
        role: str,
        messages: List[Dict[str, str]],
        *,
        schema: Type[T] = AgentResponse,  # type: ignore[assignment]
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> T:
        """Return a Pydantic-validated response.

        On JSON / schema failure, re-prompts the model with the validation error
        up to settings.max_retries times.
        """
        messages = _ensure_json_contract(messages)
        attempts = max(1, self.settings.max_retries)
        repair_messages = list(messages)
        last_err: Optional[Exception] = None
        last_text = ""

        for _ in range(attempts):
            try:
                last_text = await self.chat_raw(
                    role=role,
                    messages=repair_messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=True,
                )
                obj = _extract_json(last_text)
                return schema.model_validate(obj)
            except (json.JSONDecodeError, ValidationError) as e:
                last_err = e
                repair_messages = repair_messages + [
                    {"role": "assistant", "content": last_text},
                    {
                        "role": "user",
                        "content": (
                            "Your previous reply was not valid against the required "
                            f"JSON schema. Error: {e}. "
                            "Respond again with a single JSON object that satisfies the schema."
                        ),
                    },
                ]
        raise GatewayError(f"chat_json failed after {attempts} attempts: {last_err}")

    # ---- helpers ----
    def _build_payload(
        self,
        *,
        role: str,
        messages: List[Dict[str, str]],
        temperature: Optional[float],
        max_tokens: Optional[int],
        json_mode: bool,
        extra: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        model = self.settings.resolve_model(role)
        clamped = self.settings.clamp_max_tokens(
            max_tokens if max_tokens is not None else self.settings.max_tokens_default
        )
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": (
                self.settings.default_temperature if temperature is None else temperature
            ),
            "max_tokens": clamped,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if extra:
            payload.update(extra)
        return payload


# ---------- module-level helpers ----------
def _ensure_json_contract(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Make sure the JSON contract hint is present in the system prompt."""
    if messages and messages[0].get("role") == "system":
        sys = messages[0]
        if "single JSON object" not in sys.get("content", ""):
            messages = [
                {"role": "system", "content": sys["content"] + "\n\n" + JSON_CONTRACT_HINT}
            ] + messages[1:]
        return messages
    return [{"role": "system", "content": JSON_CONTRACT_HINT}] + messages


def _extract_json(text: str) -> Any:
    """Tolerant JSON extraction: strips ```json fences and leading prose."""
    text = text.strip()
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise json.JSONDecodeError("no JSON object found in response", text, 0)
