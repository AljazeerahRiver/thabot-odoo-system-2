{
    "name": "Asem's Odoo 19.0 AI Agent Studio - Thab-out",
    "summary": "Design, configure and run AI agents (Gemini / Vertex AI / OpenAI) inside Odoo.",
    "description": """
Asem's Odoo 19.0 AI Agent Studio - Thab-out
===========================================

An AI agent studio for شركة ثابوت (Thabot), a Saudi company selling and maintaining
smart cameras. Define AI agents, give them callable tools, keep conversation history
with token/cost accounting, and plug in any provider.

Gemini is the default provider implementation; Vertex AI, OpenAI and custom HTTP
providers are supported through the same abstraction. API keys are never stored in
source: they are read from system parameters (``ir.config_parameter``) or environment
variables.
""",
    "version": "19.0.1.0.0",
    "category": "Productivity/AI",
    "license": "LGPL-3",
    "author": "Asem Alkahtani - شركة ثابوت (Thabot)",
    "website": "https://github.com/AljazeerahRiver/thabot-odoo-system-2",
    "depends": ["base", "mail", "web"],
    "data": [
        "security/ai_security.xml",
        "security/ir.model.access.csv",
        "views/ai_provider_config_views.xml",
        "views/ai_tool_views.xml",
        "views/ai_agent_views.xml",
        "views/ai_conversation_views.xml",
        "views/menus.xml",
        "data/ai_provider_config_data.xml",
        "data/ai_agent_data.xml",
    ],
    "demo": [
        "data/ai_agent_demo.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "thabot_ai_agent_studio/static/src/scss/ai_conversation.scss",
        ],
    },
    "external_dependencies": {
        "python": ["requests"],
    },
    "application": True,
    "installable": True,
    "auto_install": False,
}
