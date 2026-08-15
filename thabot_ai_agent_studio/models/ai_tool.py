import json
import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

TECHNICAL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

DEFAULT_PARAMETERS_SCHEMA = json.dumps(
    {"type": "object", "properties": {}, "required": []}, indent=4
)


class ThabotAiTool(models.Model):
    _name = "thabot.ai.tool"
    _description = "AI Agent Tool"
    _order = "name, id"

    name = fields.Char(required=True, translate=True)
    technical_name = fields.Char(
        required=True,
        help="Function name exposed to the model. Lowercase letters, digits and underscores.",
    )
    active = fields.Boolean(default=True)
    description = fields.Text(
        translate=True,
        help="Explains to the model when and why this tool should be called.",
    )
    parameters_schema = fields.Text(
        string="Parameters (JSON Schema)",
        default=DEFAULT_PARAMETERS_SCHEMA,
        help="JSON Schema object describing the tool arguments.",
    )
    implementation_type = fields.Selection(
        selection=[
            ("python_method", "Python Method"),
            ("http_endpoint", "HTTP Endpoint"),
            ("odoo_model_method", "Odoo Model Method"),
        ],
        required=True,
        default="odoo_model_method",
    )
    target_model_id = fields.Many2one("ir.model", string="Target Model", ondelete="cascade")
    target_model = fields.Char(related="target_model_id.model", store=True, readonly=True)
    target_method = fields.Char()
    endpoint_url = fields.Char(string="Endpoint URL")
    http_method = fields.Selection(
        selection=[("get", "GET"), ("post", "POST"), ("put", "PUT"), ("delete", "DELETE")],
        default="post",
    )
    agent_ids = fields.Many2many(
        "thabot.ai.agent",
        "thabot_ai_agent_tool_rel",
        "tool_id",
        "agent_id",
        string="Agents",
    )

    _technical_name_uniq = models.Constraint(
        "unique(technical_name)",
        "The technical name of a tool must be unique.",
    )

    @api.constrains("technical_name")
    def _check_technical_name(self):
        for tool in self:
            if not TECHNICAL_NAME_RE.match(tool.technical_name or ""):
                raise ValidationError(
                    _(
                        "Invalid technical name '%(value)s': use lowercase letters, "
                        "digits and underscores, starting with a letter.",
                        value=tool.technical_name or "",
                    )
                )

    @api.constrains("parameters_schema")
    def _check_parameters_schema(self):
        for tool in self:
            schema = (tool.parameters_schema or "").strip()
            if not schema:
                continue
            try:
                parsed = json.loads(schema)
            except ValueError as error:
                raise ValidationError(
                    _("The parameters schema of '%(name)s' is not valid JSON: %(error)s",
                      name=tool.name, error=error)
                ) from error
            if not isinstance(parsed, dict):
                raise ValidationError(
                    _("The parameters schema of '%(name)s' must be a JSON object.",
                      name=tool.name)
                )

    @api.constrains("implementation_type", "target_model_id", "target_method", "endpoint_url")
    def _check_implementation(self):
        for tool in self:
            if tool.implementation_type == "odoo_model_method" and not (
                tool.target_model_id and tool.target_method
            ):
                raise ValidationError(
                    _("Tool '%(name)s' requires a target model and method.", name=tool.name)
                )
            if tool.implementation_type == "python_method" and not tool.target_method:
                raise ValidationError(
                    _("Tool '%(name)s' requires a target method.", name=tool.name)
                )
            if tool.implementation_type == "http_endpoint" and not tool.endpoint_url:
                raise ValidationError(
                    _("Tool '%(name)s' requires an endpoint URL.", name=tool.name)
                )

    def get_parameters_schema(self):
        """Return the parameters schema as a Python dict."""
        self.ensure_one()
        schema = (self.parameters_schema or "").strip()
        if not schema:
            return {"type": "object", "properties": {}}
        return json.loads(schema)

    def to_function_schema(self):
        """Provider neutral function declaration for this tool."""
        self.ensure_one()
        return {
            "name": self.technical_name,
            "description": self.description or self.name,
            "parameters": self.get_parameters_schema(),
        }
