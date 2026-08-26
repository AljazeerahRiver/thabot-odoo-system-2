# Asem's Odoo 19.0 AI Agent Studio - Thab-out

`thabot_ai_agent_studio` — an AI agent studio for **شركة ثابوت (Thabot)**, a Saudi company
that sells and maintains smart cameras.

Define AI agents, give them callable tools, keep the full conversation history with token
and cost accounting, and plug in the provider of your choice. **Google Gemini is the
default implementation**; Vertex AI, OpenAI and any custom HTTP gateway use the same
abstraction.

## Features

- **Agents** (`thabot.ai.agent`) — provider, model, system prompt, temperature, max tokens,
  tools, lifecycle (draft → active → archived) and full chatter (`mail.thread`,
  `mail.activity.mixin`).
- **Tools** (`thabot.ai.tool`) — function declarations the model can call, with a validated
  JSON Schema and three implementation types: Odoo model method, Python method or HTTP
  endpoint.
- **Conversations & messages** (`thabot.ai.conversation`, `thabot.ai.message`) — chat-style
  history with roles, token counters and a computed cost based on provider pricing.
- **Provider configurations** (`thabot.ai.provider.config`) — endpoint, model, timeout and
  pricing. **Secrets are never stored on the record**: they live in `ir.config_parameter`,
  with an environment variable as a fallback.
- **Pluggable service layer** (`models/ai_service.py`) — a provider is one class plus one
  `register_provider()` call.
- **Security** — two groups (`group_thabot_ai_user`, `group_thabot_ai_manager`), access
  rights and record rules (users only see their own conversations).
- **Arabic translation** (`i18n/ar.po`).

## Installation

1. Copy `thabot_ai_agent_studio` into your Odoo 19 addons path.
2. Update the apps list and install **Asem's Odoo 19.0 AI Agent Studio - Thab-out**.
3. Requires the `requests` Python package (already an Odoo dependency).

## Configuring a provider

Go to **AI Agent Studio → Configuration → Providers** and open *Gemini (default)*.

Set the API key in one of the two supported ways — never in source code:

- **System parameter** (recommended): paste the key in the *Set API Key* field. It is
  written to `ir.config_parameter` under `thabot_ai_agent_studio.gemini_api_key` and
  immediately cleared from the form. You can also set it manually in
  *Settings → Technical → System Parameters*.
- **Environment variable**: export `GEMINI_API_KEY` before starting Odoo and leave the
  system parameter empty.

Vertex AI additionally needs a GCP project and location, and expects a short-lived OAuth
access token (for example the output of `gcloud auth print-access-token`) in
`thabot_ai_agent_studio.vertex_ai_access_token` or `VERTEX_AI_ACCESS_TOKEN`.

## Usage

1. **AI Agent Studio → Agents** — create an agent, write its system prompt, attach tools
   and click *Activate*.
2. Click *Start Conversation*, type a message and press *Send*.
3. Token usage and cost are computed per message and totalled on the conversation.

From Python:

```python
agent = env.ref("thabot_ai_agent_studio.agent_camera_support")
message = agent.chat("The gate camera stopped recording at night.")
print(message.content)
```

## Adding a provider

```python
from odoo.addons.thabot_ai_agent_studio.models.ai_service import (
    AIProviderResponse, BaseAIProvider, register_provider,
)


@register_provider
class MyProvider(BaseAIProvider):
    technical_name = "custom"  # or a new value added to the provider selections

    def generate(self, agent, messages, tools=None):
        data = self._post_json(self.config.get_base_url(), {"messages": messages})
        return AIProviderResponse(
            content=data["text"],
            prompt_tokens=data.get("prompt_tokens", 0),
            completion_tokens=data.get("completion_tokens", 0),
        )
```

## Tests

```bash
odoo -d <database> -i thabot_ai_agent_studio --test-enable --stop-after-init
```

Tests use `odoo.tests.common.TransactionCase` with a mocked provider and assert that
`requests.post` is never called, so they run fully offline.

## License

LGPL-3.
