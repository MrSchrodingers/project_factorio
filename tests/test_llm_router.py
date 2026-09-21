import os
import unittest
from unittest.mock import patch

from factorio_ai_lab.agents.llm_router import (
    FreeModelRouter,
    ModelProvider,
    default_free_router,
)


class FreeModelRouterTests(unittest.TestCase):
    def test_default_router_uses_local_without_external_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            eligible = default_free_router().eligible()
        self.assertEqual([provider.provider_id for provider in eligible], ["local-qwen"])

    def test_free_external_provider_requires_credential(self) -> None:
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test"}, clear=True):
            eligible = default_free_router().eligible()
        self.assertEqual(
            [provider.provider_id for provider in eligible],
            ["local-qwen", "openrouter-free"],
        )


    def test_route_engine_parses_typed_decision(self) -> None:
        router = FreeModelRouter(
            [
                ModelProvider(
                    provider_id="local",
                    base_url="http://127.0.0.1:1/v1",
                    model="test",
                    priority=1,
                    cost_class="local",
                )
            ]
        )
        router.chat = lambda *args, **kwargs: {
            "choices": [
                {
                    "message": {
                        "content": '{"engine":"astar","reason":"grid pathfinding"}'
                    }
                }
            ]
        }
        self.assertEqual(
            router.route_engine("route belts"),
            {"engine": "astar", "reason": "grid pathfinding"},
        )

    def test_paid_provider_is_never_eligible(self) -> None:
        router = FreeModelRouter(
            [
                ModelProvider(
                    provider_id="paid",
                    base_url="https://example.invalid/v1",
                    model="paid-model",
                    priority=1,
                    cost_class="paid",
                )
            ]
        )
        self.assertEqual(router.eligible(), ())


if __name__ == "__main__":
    unittest.main()
