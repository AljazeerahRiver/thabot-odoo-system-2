from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class ThabotAiAgent(models.Model):
    _name = "thabot.ai.agent"
    _description = "AI Agent"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "sequence, name, id"

    name = fields.Char(required=True, translate=True, tracking=True)
    code = fields.Char(
        required=True,
        copy=False,
        tracking=True,
        help="Unique technical identifier used to reference the agent from code or data.",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    description = fields.Text(translate=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)

    provider = fields.Selection(
        selection=[
            ("gemini", "Google Gemini"),
            ("vertex_ai", "Google Vertex AI"),
            ("openai", "OpenAI"),
            ("custom", "Custom"),
        ],
        required=True,
        default="gemini",
        tracking=True,
    )
    provider_config_id = fields.Many2one(
        "thabot.ai.provider.config",
        string="Provider Configuration",
        domain="[('provider', '=', provider)]",
        help="Leave empty to use the default configuration of the selected provider.",
    )
    model_name = fields.Char(
        string="Model",
        required=True,
        default="gemini-3.7-flash",
        tracking=True,
    )
    system_prompt = fields.Text(
        translate=True,
        help="Instructions always prepended to the conversation.",
    )
    temperature = fields.Float(default=0.7, digits=(3, 2))
    max_tokens = fields.Integer(string="Max Output Tokens", default=2048)
    tool_ids = fields.Many2many(
        "thabot.ai.tool",
        "thabot_ai_agent_tool_rel",
        "agent_id",
        "tool_id",
        string="Tools",
    )
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("active", "Active"),
            ("archived", "Archived"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    conversation_ids = fields.One2many("thabot.ai.conversation", "agent_id")
    conversation_count = fields.Integer(compute="_compute_conversation_count")

    _code_uniq = models.Constraint("unique(code)", "The agent code must be unique.")

    @api.depends("conversation_ids")
    def _compute_conversation_count(self):
        grouped = self.env["thabot.ai.conversation"]._read_group(
            [("agent_id", "in", self.ids)],
            groupby=["agent_id"],
            aggregates=["__count"],
        )
        counts = {agent.id: count for agent, count in grouped}
        for agent in self:
            agent.conversation_count = counts.get(agent.id, 0)

    @api.constrains("temperature")
    def _check_temperature(self):
        for agent in self:
            if not 0.0 <= agent.temperature <= 2.0:
                raise ValidationError(
                    _("The temperature of '%(name)s' must be between 0 and 2.", name=agent.name)
                )

    @api.constrains("max_tokens")
    def _check_max_tokens(self):
        for agent in self:
            if agent.max_tokens <= 0:
                raise ValidationError(
                    _("The maximum number of output tokens of '%(name)s' must be positive.",
                      name=agent.name)
                )

    @api.constrains("provider", "provider_config_id")
    def _check_provider_config(self):
        for agent in self:
            config = agent.provider_config_id
            if config and config.provider != agent.provider:
                raise ValidationError(
                    _("The provider configuration of '%(name)s' does not match its provider.",
                      name=agent.name)
                )

    @api.onchange("provider")
    def _onchange_provider(self):
        for agent in self:
            if agent.provider_config_id.provider != agent.provider:
                agent.provider_config_id = False

    def get_provider_config(self):
        """Return the configuration to use, falling back to the provider default."""
        self.ensure_one()
        if self.provider_config_id:
            return self.provider_config_id
        config = self.env["thabot.ai.provider.config"].get_default_config(self.provider)
        if not config:
            raise UserError(
                _("No provider configuration found for '%(provider)s'.", provider=self.provider)
            )
        return config

    def action_activate(self):
        self.write({"state": "active"})
        return True

    def action_archive_agent(self):
        self.write({"state": "archived", "active": False})
        return True

    def action_reset_to_draft(self):
        self.write({"state": "draft"})
        return True

    def action_open_conversations(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Conversations"),
            "res_model": "thabot.ai.conversation",
            "view_mode": "list,form",
            "domain": [("agent_id", "=", self.id)],
            "context": {"default_agent_id": self.id},
        }

    def action_start_conversation(self):
        self.ensure_one()
        conversation = self.env["thabot.ai.conversation"].create({"agent_id": self.id})
        return {
            "type": "ir.actions.act_window",
            "name": _("Conversation"),
            "res_model": "thabot.ai.conversation",
            "view_mode": "form",
            "res_id": conversation.id,
        }

    def chat(self, prompt, conversation=None):
        """Send ``prompt`` to the agent and return the created assistant message."""
        self.ensure_one()
        if self.state != "active":
            raise UserError(
                _("Agent '%(name)s' must be active before it can be used.", name=self.name)
            )
        if conversation is None:
            conversation = self.env["thabot.ai.conversation"].create({"agent_id": self.id})
        return conversation.send_prompt(prompt)
