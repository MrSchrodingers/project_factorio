from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelProvider:
    provider_id: str
    base_url: str
    model: str
    priority: int
    cost_class: str
    api_key_env: str | None = None
    enabled: bool = True
    timeout_s: float = 120.0
    disable_thinking: bool = False

    def available_credentials(self) -> bool:
        return self.api_key_env is None or bool(os.getenv(self.api_key_env))

    def is_allowed(self) -> bool:
        return (
            self.enabled
            and self.cost_class in {"local", "free"}
            and self.available_credentials()
        )


class FreeModelRouter:
    """OpenAI-compatible router that never falls through to paid providers."""

    def __init__(self, providers: Iterable[ModelProvider]) -> None:
        self.providers = tuple(sorted(providers, key=lambda item: item.priority))
        if not self.providers:
            raise ValueError("at least one provider is required")

    def eligible(self) -> tuple[ModelProvider, ...]:
        return tuple(
            provider for provider in self.providers if provider.is_allowed()
        )

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 768,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        errors: list[str] = []
        for provider in self.eligible():
            try:
                return self._chat_provider(
                    provider,
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                )
            except (
                OSError,
                RuntimeError,
                ValueError,
                json.JSONDecodeError,
                urllib.error.URLError,
            ) as exc:
                errors.append(
                    f"{provider.provider_id}: {type(exc).__name__}: {exc}"
                )
        raise RuntimeError(
            "no free/local model completed the request: " + " | ".join(errors)
        )

    def route_engine(self, task: str) -> dict[str, str]:
        schema = {
            "type": "json_schema",
            "json_schema": {
                "name": "engine_decision",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "engine": {
                            "type": "string",
                            "enum": ["astar", "optimizer", "llm"],
                        },
                        "reason": {"type": "string"},
                    },
                    "required": ["engine", "reason"],
                    "additionalProperties": False,
                },
            },
        }
        result = self.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Select the execution engine for a Factorio subproblem. "
                        "Use astar for grid pathfinding, optimizer for constrained "
                        "production/allocation, and llm for semantic planning or "
                        "program synthesis. Keep reason under 16 words."
                    ),
                },
                {"role": "user", "content": task},
            ],
            temperature=0.0,
            max_tokens=128,
            response_format=schema,
        )
        content = result["choices"][0]["message"]["content"]
        decision = json.loads(content)
        engine = decision.get("engine")
        reason = decision.get("reason")
        if engine not in {"astar", "optimizer", "llm"}:
            raise ValueError(f"invalid engine decision: {engine!r}")
        if not isinstance(reason, str) or not reason:
            raise ValueError("engine decision reason must be a non-empty string")
        return {"engine": engine, "reason": reason}


    def _chat_provider(
        self,
        provider: ModelProvider,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        response_format: dict[str, Any] | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": provider.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if provider.disable_thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        if response_format is not None:
            body["response_format"] = response_format

        payload = json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        if provider.api_key_env:
            headers["Authorization"] = (
                f"Bearer {os.environ[provider.api_key_env]}"
            )

        request = urllib.request.Request(
            provider.base_url.rstrip("/") + "/chat/completions",
            data=payload,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=provider.timeout_s,
            ) as response:
                result = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode(errors="replace")
            raise RuntimeError(
                f"HTTP {exc.code}: {body_text[:500]}"
            ) from exc

        result["_router"] = {
            "provider_id": provider.provider_id,
            "cost_class": provider.cost_class,
            "model": provider.model,
        }
        return result


def default_free_router() -> FreeModelRouter:
    return FreeModelRouter(
        [
            ModelProvider(
                provider_id="local-qwen",
                base_url="http://127.0.0.1:18081/v1",
                model="qwen3-4b",
                priority=10,
                cost_class="local",
                disable_thinking=True,
            ),
            ModelProvider(
                provider_id="openrouter-free",
                base_url="https://openrouter.ai/api/v1",
                model="openrouter/free",
                priority=20,
                cost_class="free",
                api_key_env="OPENROUTER_API_KEY",
                enabled=True,
            ),
        ]
    )
