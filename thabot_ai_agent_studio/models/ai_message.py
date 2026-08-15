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
        "conversation_id.agent_id.provider_config_id.price_per_1k_input",
        "conversation_id.agent_id.provider_config_id.price_per_1k_output",
    )
    def _compute_cost(self):
        for message in self:
            config = message.conversation_id.agent_id.provider_config_id
            if not config:
                message.cost = 0.0
                continue
            message.cost = (
                message.prompt_tokens / 1000.0 * config.price_per_1k_input
                + message.completion_tokens / 1000.0 * config.price_per_1k_output
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("sequence"):
                continue
            conversation_id = vals.get("conversation_id")
            last = self.search(
                [("conversation_id", "=", conversation_id)],
                order="sequence desc, id desc",
                limit=1,
            )
            vals["sequence"] = (last.sequence or 0) + 10
        return super().create(vals_list)
