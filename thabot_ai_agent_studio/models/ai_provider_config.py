import logging
import os

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

DEFAULT_KEY_PARAMETERS = {
    "gemini": "thabot_ai_agent_studio.gemini_api_key",
    "vertex_ai": "thabot_ai_agent_studio.vertex_ai_access_token",
    "openai": "thabot_ai_agent_studio.openai_api_key",
    "custom": "thabot_ai_agent_studio.custom_api_key",
}

DEFAULT_BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "vertex_ai": "https://{location}-aiplatform.googleapis.com/v1",
    "openai": "https://api.openai.com/v1",
    "custom": "",
}


class ThabotAiProviderConfig(models.Model):
    _name = "thabot.ai.provider.config"
    _description = "AI Provider Configuration"
    _order = "provider, sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    provider = fields.Selection(
        selection=[
            ("gemini", "Google Gemini"),
            ("vertex_ai", "Google Vertex AI"),
            ("openai", "OpenAI"),
            ("custom", "Custom"),
        ],
        required=True,
        default="gemini",
    )
    is_default = fields.Boolean(
        string="Default for Provider",
        help="Use this configuration when an agent does not point to a specific one.",
    )
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
    )

    api_key_parameter = fields.Char(
        string="API Key System Parameter",
        required=True,
        default=lambda self: DEFAULT_KEY_PARAMETERS["gemini"],
        help="Name of the ir.config_parameter record holding the secret. "
        "The secret itself is never stored on this record.",
    )
    api_key_env_var = fields.Char(
        string="API Key Environment Variable",
        help="Optional fallback environment variable read when the system parameter is empty.",
    )
    api_key_input = fields.Char(
        string="Set API Key",
        compute="_compute_api_key_input",
        inverse="_inverse_api_key_input",
        readonly=False,
        store=False,
        help="Write-only helper: the value is moved to the system parameter and never kept here.",
    )
    api_key_set = fields.Boolean(
        string="API Key Configured",
        compute="_compute_api_key_set",
    )

    api_base_url = fields.Char(
        string="API Base URL",
        default=lambda self: DEFAULT_BASE_URLS["gemini"],
    )
    default_model = fields.Char(default="gemini-2.5-flash", required=True)
    gcp_project = fields.Char(string="GCP Project")
    gcp_location = fields.Char(string="GCP Location", default="us-central1")
    timeout = fields.Integer(default=60, help="HTTP timeout in seconds.")

    price_per_1k_input = fields.Float(
        string="Price / 1K Input Tokens",
        digits=(16, 6),
    )
    price_per_1k_output = fields.Float(
        string="Price / 1K Output Tokens",
        digits=(16, 6),
    )
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )

    agent_ids = fields.One2many("thabot.ai.agent", "provider_config_id")

    _name_uniq = models.Constraint(
        "unique(name)",
        "A provider configuration with this name already exists.",
    )

    @api.depends("api_key_parameter", "api_key_env_var")
    def _compute_api_key_set(self):
        for config in self:
            config.api_key_set = bool(config._read_api_key())

    @api.onchange("provider")
    def _onchange_provider(self):
        for config in self:
            if config.provider:
                config.api_key_parameter = DEFAULT_KEY_PARAMETERS.get(config.provider)
                config.api_base_url = DEFAULT_BASE_URLS.get(config.provider)

    @api.constrains("timeout")
    def _check_timeout(self):
        for config in self:
            if config.timeout <= 0:
                raise ValidationError(_("The HTTP timeout must be strictly positive."))

    @api.constrains("provider", "is_default", "active")
    def _check_single_default(self):
        for config in self:
            if not (config.is_default and config.active):
                continue
            duplicate = self.search_count(
                [
                    ("id", "!=", config.id),
                    ("provider", "=", config.provider),
                    ("is_default", "=", True),
                ]
            )
            if duplicate:
                raise ValidationError(
                    _(
                        "Only one default configuration is allowed for provider %(provider)s.",
                        provider=config.provider,
                    )
                )

    def _compute_api_key_input(self):
        # Never expose the stored secret in the UI.
        for config in self:
            config.api_key_input = False

    def _inverse_api_key_input(self):
        for config in self:
            if config.api_key_input:
                config._store_api_key(config.api_key_input)

    def _store_api_key(self, secret):
        """Persist the secret in ir.config_parameter, never on this model."""
        self.ensure_one()
        if not self.api_key_parameter:
            raise UserError(_("Define the API key system parameter name first."))
        self.env["ir.config_parameter"].sudo().set_param(
            self.api_key_parameter, secret.strip()
        )

    def _read_api_key(self):
        self.ensure_one()
        key = ""
        if self.api_key_parameter:
            key = self.env["ir.config_parameter"].sudo().get_param(
                self.api_key_parameter, default=""
            )
        if not key and self.api_key_env_var:
            key = os.environ.get(self.api_key_env_var, "")
        return (key or "").strip()

    def get_api_key(self):
        """Return the configured secret or raise a user friendly error."""
        self.ensure_one()
        key = self._read_api_key()
        if not key:
            raise UserError(
                _(
                    "No API key found for %(name)s. Set the system parameter "
                    "'%(param)s' or the environment variable '%(env)s'.",
                    name=self.name,
                    param=self.api_key_parameter or "-",
                    env=self.api_key_env_var or "-",
                )
            )
        return key

    def get_base_url(self):
        self.ensure_one()
        base_url = self.api_base_url or DEFAULT_BASE_URLS.get(self.provider) or ""
        if self.provider == "vertex_ai":
            base_url = base_url.replace("{location}", self.gcp_location or "us-central1")
        return base_url.rstrip("/")

    @api.model
    def get_default_config(self, provider):
        """Return the default configuration for ``provider`` (may be empty)."""
        domain = [("provider", "=", provider)]
        config = self.search(domain + [("is_default", "=", True)], limit=1)
        return config or self.search(domain, limit=1)

    def action_clear_api_key(self):
        for config in self:
            if config.api_key_parameter:
                self.env["ir.config_parameter"].sudo().set_param(
                    config.api_key_parameter, False
                )
        return True
