from odoo import api, fields, models


class ThabotAiMessage(models.Model):
    _name = "thabot.ai.message"
    _description = "AI Conversation Message"
    _order = "conversation_id, sequence, id"

    conversation_id = fields.Many2one(
        "thabot.ai.conversation",
        required=True,
        ondelete="cascade",
        index=True,
    )
    agent_id = fields.Many2one(
        "thabot.ai.agent",
        related="conversation_id.agent_id",
        store=True,
        readonly=True,
    )
    user_id = fields.Many2one(
        "res.users",
        related="conversation_id.user_id",
        store=True,
        readonly=True,
    )
    sequence = fields.Integer(default=10)
    role = fields.Selection(
        selection=[
            ("system", "System"),
            ("user", "User"),
            ("assistant", "Assistant"),
            ("tool", "Tool"),
        ],
        required=True,
        default="user",
    )
    content = fields.Text()
    tool_id = fields.Many2one("thabot.ai.tool", ondelete="set null")
    tool_call_payload = fields.Text(
        string="Tool Call Payload",
        help="Raw JSON arguments or result exchanged with the tool.",
    )
    prompt_tokens = fields.Integer(default=0)
    completion_tokens = fields.Integer(default=0)
    total_tokens = fields.Integer(compute="_compute_total_tokens", store=True)
    provider_config_id = fields.Many2one(
        "thabot.ai.provider.config",
        string="Provider Configuration",
        readonly=True,
        ondelete="restrict",
        help="Configuration actually used for this message. Stored as a snapshot so "
        "historical costs stay correct when prices change later.",
    )
    cost = fields.Monetary(
        compute="_compute_cost",
        store=True,
        currency_field="currency_id",
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="conversation_id.currency_id",
        store=True,
        readonly=True,
    )

    @api.depends("prompt_tokens", "completion_tokens")
    def _compute_total_tokens(self):
        for message in self:
            message.total_tokens = message.prompt_tokens + message.completion_tokens

    @api.depends(
        "prompt_tokens",
        "completion_tokens",
        "currency_id",
        "provider_config_id.price_per_1k_input",
        "provider_config_id.price_per_1k_output",
    )
    def _compute_cost(self):
        for message in self:
            config = message.provider_config_id
            if not config:
                message.cost = 0.0
                continue
            message.cost = (
                message.prompt_tokens / 1000.0 * config.price_per_1k_input
                + message.completion_tokens / 1000.0 * config.price_per_1k_output
            )

    @api.model
    def _resolve_provider_config(self, conversation_id):
        """Return the config a message on this conversation would actually use.

        Mirrors :meth:`thabot.ai.agent.get_provider_config` including its fallback to
        the provider default, but never raises: a missing configuration must not stop
        a message from being stored.
        """
        conversation = self.env["thabot.ai.conversation"].browse(conversation_id)
        agent = conversation.agent_id
        if not agent:
            return self.env["thabot.ai.provider.config"]
        if agent.provider_config_id:
            return agent.provider_config_id
        return self.env["thabot.ai.provider.config"].get_default_config(agent.provider)

    @api.model_create_multi
    def create(self, vals_list):
        # Resolve the last sequence once per conversation, then increment locally:
        # querying inside the loop gives every message of a batch the same value.
        next_sequence = {}
        config_cache = {}
        for vals in vals_list:
            conversation_id = vals.get("conversation_id")
            if not vals.get("provider_config_id") and conversation_id:
                if conversation_id not in config_cache:
                    config_cache[conversation_id] = self._resolve_provider_config(
                        conversation_id
                    )
                config = config_cache[conversation_id]
                if config:
                    vals["provider_config_id"] = config.id
            if vals.get("sequence"):
                continue
            if conversation_id not in next_sequence:
                last = self.search(
                    [("conversation_id", "=", conversation_id)],
                    order="sequence desc, id desc",
                    limit=1,
                )
                next_sequence[conversation_id] = (last.sequence or 0) + 10
            vals["sequence"] = next_sequence[conversation_id]
            next_sequence[conversation_id] += 10
        return super().create(vals_list)
