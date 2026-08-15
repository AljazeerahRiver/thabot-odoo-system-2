from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ThabotAiConversation(models.Model):
    _name = "thabot.ai.conversation"
    _description = "AI Conversation"
    _order = "create_date desc, id desc"

    name = fields.Char(required=True, default=lambda self: _("New Conversation"), copy=False)
    agent_id = fields.Many2one(
        "thabot.ai.agent",
        required=True,
        ondelete="restrict",
        index=True,
    )
    user_id = fields.Many2one(
        "res.users",
        string="Owner",
        required=True,
        default=lambda self: self.env.user,
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        related="agent_id.company_id",
        store=True,
        readonly=True,
    )
    active = fields.Boolean(default=True)
    state = fields.Selection(
        selection=[("open", "Open"), ("closed", "Closed")],
        default="open",
        required=True,
    )
    message_ids = fields.One2many("thabot.ai.message", "conversation_id")
    message_count = fields.Integer(compute="_compute_statistics", store=True)
    prompt_tokens = fields.Integer(compute="_compute_statistics", store=True)
    completion_tokens = fields.Integer(compute="_compute_statistics", store=True)
    total_tokens = fields.Integer(compute="_compute_statistics", store=True)
    total_cost = fields.Monetary(
        compute="_compute_statistics",
        store=True,
        currency_field="currency_id",
    )
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
        readonly=True,
    )
    prompt_input = fields.Text(
        string="Your Message",
        copy=False,
        help="Type your message here and press Send.",
    )

    @api.depends(
        "message_ids",
        "message_ids.prompt_tokens",
        "message_ids.completion_tokens",
        "message_ids.cost",
    )
    def _compute_statistics(self):
        for conversation in self:
            messages = conversation.message_ids
            conversation.message_count = len(messages)
            conversation.prompt_tokens = sum(messages.mapped("prompt_tokens"))
            conversation.completion_tokens = sum(messages.mapped("completion_tokens"))
            conversation.total_tokens = (
                conversation.prompt_tokens + conversation.completion_tokens
            )
            conversation.total_cost = sum(messages.mapped("cost"))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == _("New Conversation"):
                agent = self.env["thabot.ai.agent"].browse(vals.get("agent_id"))
                vals["name"] = _(
                    "%(agent)s - %(date)s",
                    agent=agent.name or _("Agent"),
                    date=fields.Datetime.to_string(fields.Datetime.now()),
                )
        return super().create(vals_list)

    def get_history(self):
        """Return the provider neutral message history, system prompt included."""
        self.ensure_one()
        history = []
        if self.agent_id.system_prompt:
            history.append({"role": "system", "content": self.agent_id.system_prompt})
        for message in self.message_ids.sorted(lambda m: (m.sequence, m.id)):
            if message.role == "system":
                continue
            history.append({"role": message.role, "content": message.content or ""})
        return history

    def send_prompt(self, prompt):
        """Persist the user prompt, call the provider and store the answer."""
        self.ensure_one()
        prompt = (prompt or "").strip()
        if not prompt:
            raise UserError(_("Please type a message before sending it."))
        if self.state != "open":
            raise UserError(_("This conversation is closed."))

        message_model = self.env["thabot.ai.message"]
        message_model.create(
            {
                "conversation_id": self.id,
                "role": "user",
                "content": prompt,
            }
        )
        response = self.env["thabot.ai.service"].dispatch(self.agent_id, self.get_history())
        return message_model.create(
            {
                "conversation_id": self.id,
                "role": "assistant",
                "content": response.get("content") or "",
                "prompt_tokens": response.get("prompt_tokens", 0),
                "completion_tokens": response.get("completion_tokens", 0),
            }
        )

    def action_send_message(self):
        self.ensure_one()
        self.send_prompt(self.prompt_input)
        self.prompt_input = False
        return True

    def action_close(self):
        return self.write({"state": "closed"})

    def action_reopen(self):
        return self.write({"state": "open"})
