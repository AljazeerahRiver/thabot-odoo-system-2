from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestThabotAiModels(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The module ships default configurations; clear them so the tests own the defaults.
        cls.env["thabot.ai.provider.config"].search([("is_default", "=", True)]).write(
            {"is_default": False}
        )
        cls.config = cls.env["thabot.ai.provider.config"].create(
            {
                "name": "Gemini Test Config",
                "provider": "gemini",
                "api_key_parameter": "thabot_ai_agent_studio.test_gemini_api_key",
                "default_model": "gemini-3.7-flash",
                "price_per_1k_input": 0.1,
                "price_per_1k_output": 0.4,
            }
        )
        cls.agent = cls.env["thabot.ai.agent"].create(
            {
                "name": "Test Camera Agent",
                "code": "test_camera_agent",
                "provider": "gemini",
                "provider_config_id": cls.config.id,
                "model_name": "gemini-3.7-flash",
                "system_prompt": "You support Thabot smart cameras.",
                "state": "active",
            }
        )

    # -- agent ------------------------------------------------------------
    def test_agent_defaults(self):
        agent = self.env["thabot.ai.agent"].create(
            {"name": "Defaults", "code": "defaults_agent"}
        )
        self.assertEqual(agent.provider, "gemini")
        self.assertEqual(agent.state, "draft")
        self.assertTrue(agent.active)
        self.assertEqual(agent.temperature, 0.7)
        self.assertEqual(agent.max_tokens, 2048)

    @mute_logger("odoo.sql_db")
    def test_agent_code_is_unique(self):
        with self.assertRaises(Exception):
            with self.cr.savepoint():
                self.env["thabot.ai.agent"].create(
                    {"name": "Duplicate", "code": "test_camera_agent"}
                )

    def test_agent_temperature_constraint(self):
        with self.assertRaises(ValidationError):
            self.agent.temperature = 5.0

    def test_agent_max_tokens_constraint(self):
        with self.assertRaises(ValidationError):
            self.agent.max_tokens = 0

    def test_agent_provider_config_must_match_provider(self):
        openai_config = self.env["thabot.ai.provider.config"].create(
            {"name": "OpenAI Test", "provider": "openai", "default_model": "gpt-4o-mini"}
        )
        with self.assertRaises(ValidationError):
            self.agent.provider_config_id = openai_config

    def test_agent_state_actions(self):
        agent = self.env["thabot.ai.agent"].create({"name": "Flow", "code": "flow_agent"})
        agent.action_activate()
        self.assertEqual(agent.state, "active")
        agent.action_archive_agent()
        self.assertEqual(agent.state, "archived")
        self.assertFalse(agent.active)
        agent.action_reset_to_draft()
        self.assertEqual(agent.state, "draft")

    def test_agent_chat_requires_active_state(self):
        agent = self.env["thabot.ai.agent"].create({"name": "Draft", "code": "draft_agent"})
        with self.assertRaises(UserError):
            agent.chat("Hello")

    def test_agent_conversation_count(self):
        self.env["thabot.ai.conversation"].create({"agent_id": self.agent.id})
        self.env["thabot.ai.conversation"].create({"agent_id": self.agent.id})
        self.agent.invalidate_recordset(["conversation_count"])
        self.assertEqual(self.agent.conversation_count, 2)

    # -- tool -------------------------------------------------------------
    def test_tool_function_schema(self):
        tool = self.env["thabot.ai.tool"].create(
            {
                "name": "Warranty",
                "technical_name": "warranty_lookup",
                "description": "Look up a warranty.",
                "implementation_type": "python_method",
                "target_method": "warranty_lookup",
                "parameters_schema": '{"type": "object", "properties": {"sn": {"type": "string"}}}',
            }
        )
        schema = tool.to_function_schema()
        self.assertEqual(schema["name"], "warranty_lookup")
        self.assertEqual(schema["description"], "Look up a warranty.")
        self.assertIn("sn", schema["parameters"]["properties"])

    def test_tool_technical_name_validation(self):
        with self.assertRaises(ValidationError):
            self.env["thabot.ai.tool"].create(
                {
                    "name": "Bad name",
                    "technical_name": "Bad Name",
                    "implementation_type": "python_method",
                    "target_method": "whatever",
                }
            )

    def test_tool_invalid_json_schema(self):
        with self.assertRaises(ValidationError):
            self.env["thabot.ai.tool"].create(
                {
                    "name": "Broken schema",
                    "technical_name": "broken_schema",
                    "implementation_type": "python_method",
                    "target_method": "whatever",
                    "parameters_schema": "{not json",
                }
            )

    def test_tool_requires_target_details(self):
        with self.assertRaises(ValidationError):
            self.env["thabot.ai.tool"].create(
                {
                    "name": "No endpoint",
                    "technical_name": "no_endpoint",
                    "implementation_type": "http_endpoint",
                }
            )

    # -- conversation & messages -----------------------------------------
    def test_conversation_history_includes_system_prompt(self):
        conversation = self.env["thabot.ai.conversation"].create({"agent_id": self.agent.id})
        self.env["thabot.ai.message"].create(
            {"conversation_id": conversation.id, "role": "user", "content": "Hello"}
        )
        history = conversation.get_history()
        self.assertEqual(history[0]["role"], "system")
        self.assertEqual(history[0]["content"], "You support Thabot smart cameras.")
        self.assertEqual(history[-1], {"role": "user", "content": "Hello"})

    def test_conversation_totals_and_cost(self):
        conversation = self.env["thabot.ai.conversation"].create({"agent_id": self.agent.id})
        self.env["thabot.ai.message"].create(
            {
                "conversation_id": conversation.id,
                "role": "assistant",
                "content": "Hi",
                "prompt_tokens": 1000,
                "completion_tokens": 500,
            }
        )
        self.assertEqual(conversation.message_count, 1)
        self.assertEqual(conversation.prompt_tokens, 1000)
        self.assertEqual(conversation.completion_tokens, 500)
        self.assertEqual(conversation.total_tokens, 1500)
        # 1000/1000 * 0.1 + 500/1000 * 0.4 = 0.3
        self.assertAlmostEqual(conversation.total_cost, 0.3, places=6)

    def test_conversation_message_sequence(self):
        conversation = self.env["thabot.ai.conversation"].create({"agent_id": self.agent.id})
        first = self.env["thabot.ai.message"].create(
            {"conversation_id": conversation.id, "role": "user", "content": "1"}
        )
        second = self.env["thabot.ai.message"].create(
            {"conversation_id": conversation.id, "role": "assistant", "content": "2"}
        )
        self.assertLess(first.sequence, second.sequence)

    def test_batch_created_messages_get_distinct_sequences(self):
        """Messages created in one call must not collapse onto the same sequence."""
        conversation = self.env["thabot.ai.conversation"].create({"agent_id": self.agent.id})
        messages = self.env["thabot.ai.message"].create(
            [
                {"conversation_id": conversation.id, "role": "user", "content": "1"},
                {"conversation_id": conversation.id, "role": "assistant", "content": "2"},
                {"conversation_id": conversation.id, "role": "user", "content": "3"},
            ]
        )
        sequences = messages.mapped("sequence")
        self.assertEqual(len(set(sequences)), 3, "each message needs its own sequence")
        self.assertEqual(sequences, sorted(sequences))
        # Ordering must reflect insertion order, which is what _order relies on.
        self.assertEqual(
            conversation.message_ids.sorted(lambda m: (m.sequence, m.id)).mapped("content"),
            ["1", "2", "3"],
        )

    def test_batch_sequences_continue_after_existing_messages(self):
        conversation = self.env["thabot.ai.conversation"].create({"agent_id": self.agent.id})
        existing = self.env["thabot.ai.message"].create(
            {"conversation_id": conversation.id, "role": "user", "content": "first"}
        )
        batch = self.env["thabot.ai.message"].create(
            [
                {"conversation_id": conversation.id, "role": "assistant", "content": "2"},
                {"conversation_id": conversation.id, "role": "user", "content": "3"},
            ]
        )
        self.assertTrue(all(seq > existing.sequence for seq in batch.mapped("sequence")))
        self.assertEqual(len(set(batch.mapped("sequence"))), 2)

    def test_batch_sequences_are_independent_per_conversation(self):
        first_conversation = self.env["thabot.ai.conversation"].create(
            {"agent_id": self.agent.id}
        )
        second_conversation = self.env["thabot.ai.conversation"].create(
            {"agent_id": self.agent.id}
        )
        messages = self.env["thabot.ai.message"].create(
            [
                {"conversation_id": first_conversation.id, "role": "user", "content": "a1"},
                {"conversation_id": second_conversation.id, "role": "user", "content": "b1"},
                {"conversation_id": first_conversation.id, "role": "user", "content": "a2"},
            ]
        )
        first_sequences = messages.filtered(
            lambda m: m.conversation_id == first_conversation
        ).mapped("sequence")
        self.assertEqual(len(set(first_sequences)), 2)
        # A busy conversation must not push the other one's numbering forward.
        self.assertEqual(second_conversation.message_ids.mapped("sequence"), [10])

    def test_conversation_send_prompt_requires_content(self):
        conversation = self.env["thabot.ai.conversation"].create({"agent_id": self.agent.id})
        with self.assertRaises(UserError):
            conversation.send_prompt("   ")

    def test_conversation_closed_refuses_prompt(self):
        conversation = self.env["thabot.ai.conversation"].create({"agent_id": self.agent.id})
        conversation.action_close()
        with self.assertRaises(UserError):
            conversation.send_prompt("Hello")

    # -- provider configuration ------------------------------------------
    def test_provider_config_single_default(self):
        self.config.is_default = True
        with self.assertRaises(ValidationError):
            self.env["thabot.ai.provider.config"].create(
                {
                    "name": "Second Gemini Default",
                    "provider": "gemini",
                    "is_default": True,
                    "default_model": "gemini-3.1-pro-preview",
                }
            )

    def test_provider_config_api_key_roundtrip(self):
        with self.assertRaises(UserError):
            self.config.get_api_key()
        self.config.api_key_input = "test-secret-value"
        self.config.flush_recordset()
        stored = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("thabot_ai_agent_studio.test_gemini_api_key")
        )
        self.assertEqual(stored, "test-secret-value")
        self.assertEqual(self.config.get_api_key(), "test-secret-value")
        # The secret is never exposed back through the record.
        self.config.invalidate_recordset(["api_key_input"])
        self.assertFalse(self.config.api_key_input)
        self.assertTrue(self.config.api_key_set)

    def test_provider_config_base_url_location(self):
        vertex = self.env["thabot.ai.provider.config"].create(
            {
                "name": "Vertex Test",
                "provider": "vertex_ai",
                "api_base_url": "https://{location}-aiplatform.googleapis.com/v1",
                "gcp_location": "europe-west4",
                "default_model": "gemini-3.7-flash",
            }
        )
        self.assertEqual(
            vertex.get_base_url(), "https://europe-west4-aiplatform.googleapis.com/v1"
        )

    def test_provider_config_timeout_constraint(self):
        with self.assertRaises(ValidationError):
            self.config.timeout = 0

    def test_agent_falls_back_to_default_config(self):
        self.config.is_default = True
        agent = self.env["thabot.ai.agent"].create(
            {"name": "No config", "code": "no_config_agent", "provider": "gemini"}
        )
        self.assertEqual(agent.get_provider_config(), self.config)

    # -- cost ---------------------------------------------------------------
    def test_cost_uses_default_config_when_agent_has_none(self):
        """An agent without an explicit config still bills against the default one.

        Reading ``agent_id.provider_config_id`` directly reported a silent 0.0 here,
        even though the very same fallback config was used to bill the tokens.
        """
        self.config.is_default = True
        agent = self.env["thabot.ai.agent"].create(
            {
                "name": "Fallback billing",
                "code": "fallback_billing_agent",
                "provider": "gemini",
                "state": "active",
            }
        )
        self.assertFalse(agent.provider_config_id)
        conversation = self.env["thabot.ai.conversation"].create({"agent_id": agent.id})
        message = self.env["thabot.ai.message"].create(
            {
                "conversation_id": conversation.id,
                "role": "assistant",
                "content": "Billed",
                "prompt_tokens": 1000,
                "completion_tokens": 500,
            }
        )
        self.assertEqual(message.provider_config_id, self.config)
        # 1000/1000 * 0.1 + 500/1000 * 0.4 = 0.3
        self.assertAlmostEqual(message.cost, 0.3, places=6)
        self.assertAlmostEqual(conversation.total_cost, 0.3, places=6)

    def test_cost_snapshot_survives_price_changes(self):
        """Historical cost must not be rewritten when tariffs change later."""
        conversation = self.env["thabot.ai.conversation"].create({"agent_id": self.agent.id})
        message = self.env["thabot.ai.message"].create(
            {
                "conversation_id": conversation.id,
                "role": "assistant",
                "content": "Old price",
                "prompt_tokens": 1000,
                "completion_tokens": 0,
            }
        )
        self.assertAlmostEqual(message.cost, 0.1, places=6)
        snapshot = message.provider_config_id
        self.assertEqual(snapshot, self.config)
        # Point the agent at a new, more expensive configuration.
        new_config = self.env["thabot.ai.provider.config"].create(
            {
                "name": "Gemini New Tariff",
                "provider": "gemini",
                "api_key_parameter": "thabot_ai_agent_studio.test_new_tariff_key",
                "price_per_1k_input": 9.0,
                "price_per_1k_output": 9.0,
            }
        )
        self.agent.provider_config_id = new_config
        message.invalidate_recordset()
        self.assertEqual(message.provider_config_id, snapshot)
        self.assertAlmostEqual(message.cost, 0.1, places=6)

    def test_cost_is_zero_without_any_config(self):
        self.env["thabot.ai.provider.config"].search(
            [("provider", "=", "openai")]
        ).write({"active": False})
        agent = self.env["thabot.ai.agent"].create(
            {
                "name": "Unconfigured",
                "code": "unconfigured_agent",
                "provider": "openai",
                "state": "active",
            }
        )
        conversation = self.env["thabot.ai.conversation"].create({"agent_id": agent.id})
        message = self.env["thabot.ai.message"].create(
            {
                "conversation_id": conversation.id,
                "role": "assistant",
                "content": "No config",
                "prompt_tokens": 1000,
                "completion_tokens": 1000,
            }
        )
        self.assertFalse(message.provider_config_id)
        self.assertEqual(message.cost, 0.0)

    def test_api_key_input_compute_has_dependencies(self):
        """The write-only helper must carry an explicit (empty) @api.depends().

        Checking ``field.depends`` alone would prove nothing: Odoo normalises a
        missing declaration to ``()`` as well. The decorator is what sets ``_depends``
        on the method itself, so that is what this asserts.
        """
        model = self.env["thabot.ai.provider.config"]
        method = getattr(type(model), "_compute_api_key_input")
        self.assertTrue(
            hasattr(method, "_depends"),
            "_compute_api_key_input must be decorated with @api.depends()",
        )
        self.assertEqual(tuple(method._depends), ())
        field = model._fields["api_key_input"]
        self.assertTrue(field.compute)
        self.assertFalse(field.store)
        # It must still never leak the stored secret.
        self.config.api_key_input = "secret-value"
        self.config.invalidate_recordset()
        self.assertFalse(self.config.api_key_input)
