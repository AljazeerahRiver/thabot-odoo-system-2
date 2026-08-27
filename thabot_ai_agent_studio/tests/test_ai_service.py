from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from ..models.ai_service import (
    PROVIDER_REGISTRY,
    AIProviderResponse,
    BaseAIProvider,
    GeminiProvider,
    register_provider,
)


class DummyProvider(BaseAIProvider):
    """In-memory provider used to test the abstraction without any network call."""

    technical_name = "custom"
    calls = []

    def generate(self, agent, messages, tools=None):
        DummyProvider.calls.append(
            {
                "agent": agent,
                "messages": messages,
                "tools": [tool.technical_name for tool in (tools or [])],
            }
        )
        return AIProviderResponse(
            content="Dummy answer",
            prompt_tokens=120,
            completion_tokens=80,
        )


class BrokenProvider(BaseAIProvider):
    technical_name = "custom"

    def generate(self, agent, messages, tools=None):
        return "not a provider response"


@tagged("post_install", "-at_install")
class TestThabotAiService(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["thabot.ai.provider.config"].search([("is_default", "=", True)]).write(
            {"is_default": False}
        )
        cls.config = cls.env["thabot.ai.provider.config"].create(
            {
                "name": "Dummy Provider Config",
                "provider": "custom",
                "is_default": True,
                "api_base_url": "https://example.invalid/generate",
                "api_key_parameter": "thabot_ai_agent_studio.test_custom_api_key",
                "default_model": "dummy-model",
                "price_per_1k_input": 1.0,
                "price_per_1k_output": 2.0,
            }
        )
        cls.tool = cls.env["thabot.ai.tool"].create(
            {
                "name": "Warranty Lookup",
                "technical_name": "warranty_lookup",
                "description": "Look up a camera warranty.",
                "implementation_type": "python_method",
                "target_method": "warranty_lookup",
                "parameters_schema": '{"type": "object", "properties": '
                '{"serial_number": {"type": "string"}}, "required": ["serial_number"]}',
            }
        )
        cls.agent = cls.env["thabot.ai.agent"].create(
            {
                "name": "Dummy Agent",
                "code": "dummy_agent",
                "provider": "custom",
                "provider_config_id": cls.config.id,
                "model_name": "dummy-model",
                "system_prompt": "You are the Thabot camera assistant.",
                "temperature": 0.2,
                "max_tokens": 512,
                "state": "active",
                "tool_ids": [(6, 0, [cls.tool.id])],
            }
        )

    def setUp(self):
        super().setUp()
        DummyProvider.calls = []
        previous = PROVIDER_REGISTRY.get("custom")
        register_provider(DummyProvider)
        self.addCleanup(PROVIDER_REGISTRY.__setitem__, "custom", previous)
        # Guarantee that no test in this class can reach the network.
        patcher = patch(
            "odoo.addons.thabot_ai_agent_studio.models.ai_service.requests.post",
            side_effect=AssertionError("No HTTP call must happen in tests"),
        )
        self.requests_post = patcher.start()
        self.addCleanup(patcher.stop)

    # -- registry ---------------------------------------------------------
    def test_registry_returns_registered_provider(self):
        provider = self.env["thabot.ai.service"].get_provider(self.agent)
        self.assertIsInstance(provider, DummyProvider)
        self.assertEqual(provider.config, self.config)

    def test_registry_unknown_provider_raises(self):
        previous = PROVIDER_REGISTRY.pop("custom")
        self.addCleanup(PROVIDER_REGISTRY.__setitem__, "custom", previous)
        with self.assertRaises(UserError):
            self.env["thabot.ai.service"].get_provider(self.agent)

    def test_dispatch_requires_messages(self):
        with self.assertRaises(UserError):
            self.env["thabot.ai.service"].dispatch(self.agent, [])

    def test_dispatch_rejects_invalid_provider_result(self):
        register_provider(BrokenProvider)
        with self.assertRaises(UserError):
            self.env["thabot.ai.service"].dispatch(
                self.agent, [{"role": "user", "content": "Hello"}]
            )

    def test_dispatch_passes_messages_and_tools(self):
        result = self.env["thabot.ai.service"].dispatch(
            self.agent, [{"role": "user", "content": "Hello"}]
        )
        self.assertEqual(result["content"], "Dummy answer")
        self.assertEqual(result["prompt_tokens"], 120)
        self.assertEqual(result["completion_tokens"], 80)
        self.assertEqual(len(DummyProvider.calls), 1)
        self.assertEqual(DummyProvider.calls[0]["tools"], ["warranty_lookup"])
        self.requests_post.assert_not_called()

    # -- conversation integration ----------------------------------------
    def test_conversation_send_prompt_persists_messages(self):
        conversation = self.env["thabot.ai.conversation"].create({"agent_id": self.agent.id})
        answer = conversation.send_prompt("The gate camera is offline")

        self.assertEqual(answer.role, "assistant")
        self.assertEqual(answer.content, "Dummy answer")
        self.assertEqual(conversation.message_count, 2)
        self.assertEqual(conversation.message_ids[0].role, "user")
        self.assertEqual(conversation.total_tokens, 200)
        # 120/1000 * 1.0 + 80/1000 * 2.0 = 0.28
        self.assertAlmostEqual(conversation.total_cost, 0.28, places=6)

        sent_messages = DummyProvider.calls[0]["messages"]
        self.assertEqual(sent_messages[0]["role"], "system")
        self.assertEqual(sent_messages[0]["content"], "You are the Thabot camera assistant.")
        self.assertEqual(sent_messages[-1]["content"], "The gate camera is offline")
        self.requests_post.assert_not_called()

    def test_agent_chat_creates_conversation(self):
        message = self.agent.chat("Hello there")
        self.assertEqual(message.role, "assistant")
        self.assertEqual(message.conversation_id.agent_id, self.agent)
        self.assertEqual(message.conversation_id.message_count, 2)

    def test_action_send_message_clears_input(self):
        conversation = self.env["thabot.ai.conversation"].create(
            {"agent_id": self.agent.id, "prompt_input": "Need help"}
        )
        conversation.action_send_message()
        self.assertFalse(conversation.prompt_input)
        self.assertEqual(conversation.message_count, 2)

    # -- Gemini payload mapping (offline) --------------------------------
    def test_gemini_payload_mapping(self):
        gemini_config = self.env["thabot.ai.provider.config"].create(
            {
                "name": "Gemini Mapping",
                "provider": "gemini",
                "api_base_url": "https://generativelanguage.googleapis.com/v1beta",
                "default_model": "gemini-3.7-flash",
            }
        )
        gemini_agent = self.env["thabot.ai.agent"].create(
            {
                "name": "Gemini Agent",
                "code": "gemini_mapping_agent",
                "provider": "gemini",
                "provider_config_id": gemini_config.id,
                "model_name": "gemini-3.7-flash",
                "temperature": 0.3,
                "max_tokens": 256,
                "tool_ids": [(6, 0, [self.tool.id])],
            }
        )
        provider = GeminiProvider(self.env, gemini_config)
        payload = provider._build_payload(
            gemini_agent,
            [
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
            ],
            gemini_agent.tool_ids,
        )
        self.assertEqual(payload["systemInstruction"]["parts"][0]["text"], "Be concise.")
        self.assertEqual([content["role"] for content in payload["contents"]], ["user", "model"])
        self.assertEqual(payload["generationConfig"]["temperature"], 0.3)
        self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 256)
        self.assertEqual(
            payload["tools"][0]["functionDeclarations"][0]["name"], "warranty_lookup"
        )
        self.assertEqual(
            provider._endpoint("gemini-3.7-flash"),
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.7-flash:generateContent",
        )

    def test_gemini_response_parsing(self):
        gemini_config = self.env["thabot.ai.provider.config"].create(
            {"name": "Gemini Parsing", "provider": "gemini", "default_model": "gemini-3.7-flash"}
        )
        provider = GeminiProvider(self.env, gemini_config)
        response = provider._parse_response(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "The warranty is valid."},
                                {
                                    "functionCall": {
                                        "name": "warranty_lookup",
                                        "args": {"serial_number": "TH-CAM-4821"},
                                    }
                                },
                            ]
                        }
                    }
                ],
                "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 7},
            }
        )
        self.assertEqual(response.content, "The warranty is valid.")
        self.assertEqual(response.prompt_tokens, 12)
        self.assertEqual(response.completion_tokens, 7)
        self.assertEqual(response.tool_calls[0]["name"], "warranty_lookup")
        self.assertEqual(
            response.tool_calls[0]["arguments"], {"serial_number": "TH-CAM-4821"}
        )

    def test_gemini_requires_api_key(self):
        gemini_config = self.env["thabot.ai.provider.config"].create(
            {
                "name": "Gemini No Key",
                "provider": "gemini",
                "api_key_parameter": "thabot_ai_agent_studio.missing_key",
                "default_model": "gemini-3.7-flash",
            }
        )
        provider = GeminiProvider(self.env, gemini_config)
        with self.assertRaises(UserError):
            provider._request_params()
        self.requests_post.assert_not_called()

    def test_vertex_requires_project(self):
        vertex_config = self.env["thabot.ai.provider.config"].create(
            {
                "name": "Vertex No Project",
                "provider": "vertex_ai",
                "api_base_url": "https://{location}-aiplatform.googleapis.com/v1",
                "default_model": "gemini-3.7-flash",
            }
        )
        provider = PROVIDER_REGISTRY["vertex_ai"](self.env, vertex_config)
        with self.assertRaises(UserError):
            provider._endpoint("gemini-3.7-flash")
