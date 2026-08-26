"""Provider abstraction for the Thabot AI Agent Studio.

The service layer keeps Odoo models free of any provider specific detail. A provider
is a small class implementing :meth:`BaseAIProvider.generate`; registering a new one
is a single call to :func:`register_provider`.

No credential is ever hardcoded here: every secret comes from
``thabot.ai.provider.config``, which reads ``ir.config_parameter`` or an environment
variable.
"""

import json
import logging

import requests

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60


class AIProviderResponse:
    """Provider neutral result of a generation call."""

    def __init__(self, content="", prompt_tokens=0, completion_tokens=0, tool_calls=None, raw=None):
        self.content = content or ""
        self.prompt_tokens = prompt_tokens or 0
        self.completion_tokens = completion_tokens or 0
        self.tool_calls = tool_calls or []
        self.raw = raw or {}

    def to_dict(self):
        return {
            "content": self.content,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tool_calls": self.tool_calls,
        }


class BaseAIProvider:
    """Base class every provider implementation must extend."""

    technical_name = "base"

    def __init__(self, env, config):
        self.env = env
        self.config = config

    # -- to implement -----------------------------------------------------
    def generate(self, agent, messages, tools=None):
        """Return an :class:`AIProviderResponse` for ``messages``."""
        raise NotImplementedError

    # -- helpers ----------------------------------------------------------
    @property
    def timeout(self):
        return self.config.timeout or DEFAULT_TIMEOUT

    def _post_json(self, url, payload, headers=None, params=None):
        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers or {"Content-Type": "application/json"},
                params=params or {},
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as error:
            _logger.warning("AI provider %s call failed: %s", self.technical_name, error)
            raise UserError(
                _("The AI provider could not be reached: %(error)s", error=error)
            ) from error
        except ValueError as error:
            raise UserError(_("The AI provider returned an invalid response.")) from error

    @staticmethod
    def _split_system(messages):
        """Split ``messages`` into the system instruction and the remaining turns."""
        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        turns = [m for m in messages if m.get("role") != "system"]
        return "\n\n".join(part for part in system_parts if part), turns


class GeminiProvider(BaseAIProvider):
    """Default implementation: Google Gemini through the Generative Language API."""

    technical_name = "gemini"

    def _endpoint(self, model_name):
        return "%s/models/%s:generateContent" % (self.config.get_base_url(), model_name)

    def _request_params(self):
        return {"key": self.config.get_api_key()}

    def _request_headers(self):
        return {"Content-Type": "application/json"}

    def _build_payload(self, agent, messages, tools=None):
        system_instruction, turns = self._split_system(messages)
        contents = [
            {
                "role": "model" if turn.get("role") == "assistant" else "user",
                "parts": [{"text": turn.get("content") or ""}],
            }
            for turn in turns
        ]
        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": agent.temperature,
                "maxOutputTokens": agent.max_tokens,
            },
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        declarations = [tool.to_function_schema() for tool in (tools or [])]
        if declarations:
            payload["tools"] = [{"functionDeclarations": declarations}]
        return payload

    def _parse_response(self, data):
        candidates = data.get("candidates") or []
        text_parts = []
        tool_calls = []
        for candidate in candidates[:1]:
            for part in (candidate.get("content") or {}).get("parts") or []:
                if part.get("text"):
                    text_parts.append(part["text"])
                if part.get("functionCall"):
                    tool_calls.append(
                        {
                            "name": part["functionCall"].get("name"),
                            "arguments": part["functionCall"].get("args") or {},
                        }
                    )
        usage = data.get("usageMetadata") or {}
        return AIProviderResponse(
            content="".join(text_parts),
            prompt_tokens=usage.get("promptTokenCount", 0),
            completion_tokens=usage.get("candidatesTokenCount", 0),
            tool_calls=tool_calls,
            raw=data,
        )

    def generate(self, agent, messages, tools=None):
        model_name = agent.model_name or self.config.default_model
        data = self._post_json(
            self._endpoint(model_name),
            self._build_payload(agent, messages, tools),
            headers=self._request_headers(),
            params=self._request_params(),
        )
        return self._parse_response(data)


class VertexAIProvider(GeminiProvider):
    """Same Gemini payloads, served by Vertex AI with a bearer token."""

    technical_name = "vertex_ai"

    def _endpoint(self, model_name):
        if not self.config.gcp_project:
            raise UserError(_("Set the GCP project on the Vertex AI configuration."))
        return "%s/projects/%s/locations/%s/publishers/google/models/%s:generateContent" % (
            self.config.get_base_url(),
            self.config.gcp_project,
            self.config.gcp_location or "us-central1",
            model_name,
        )

    def _request_params(self):
        return {}

    def _request_headers(self):
        return {
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % self.config.get_api_key(),
        }


class OpenAIProvider(BaseAIProvider):
    technical_name = "openai"

    def generate(self, agent, messages, tools=None):
        payload = {
            "model": agent.model_name or self.config.default_model,
            "messages": [
                {"role": turn.get("role"), "content": turn.get("content") or ""}
                for turn in messages
            ],
            "temperature": agent.temperature,
            "max_tokens": agent.max_tokens,
        }
        declarations = [tool.to_function_schema() for tool in (tools or [])]
        if declarations:
            payload["tools"] = [
                {"type": "function", "function": declaration} for declaration in declarations
            ]
        data = self._post_json(
            "%s/chat/completions" % self.config.get_base_url(),
            payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer %s" % self.config.get_api_key(),
            },
        )
        choices = data.get("choices") or []
        message = (choices[0].get("message") if choices else {}) or {}
        usage = data.get("usage") or {}
        tool_calls = [
            {
                "name": (call.get("function") or {}).get("name"),
                "arguments": json.loads((call.get("function") or {}).get("arguments") or "{}"),
            }
            for call in message.get("tool_calls") or []
        ]
        return AIProviderResponse(
            content=message.get("content") or "",
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            tool_calls=tool_calls,
            raw=data,
        )


class CustomProvider(BaseAIProvider):
    """Minimal contract for self hosted or third party gateways."""

    technical_name = "custom"

    def generate(self, agent, messages, tools=None):
        base_url = self.config.get_base_url()
        if not base_url:
            raise UserError(_("Set the API base URL of the custom provider configuration."))
        payload = {
            "model": agent.model_name or self.config.default_model,
            "messages": messages,
            "temperature": agent.temperature,
            "max_tokens": agent.max_tokens,
            "tools": [tool.to_function_schema() for tool in (tools or [])],
        }
        data = self._post_json(
            base_url,
            payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer %s" % self.config.get_api_key(),
            },
        )
        return AIProviderResponse(
            content=data.get("content") or "",
            prompt_tokens=data.get("prompt_tokens", 0),
            completion_tokens=data.get("completion_tokens", 0),
            tool_calls=data.get("tool_calls") or [],
            raw=data,
        )


PROVIDER_REGISTRY = {}


def register_provider(provider_class):
    """Register (or override) the implementation of a provider."""
    PROVIDER_REGISTRY[provider_class.technical_name] = provider_class
    return provider_class


for _provider_class in (GeminiProvider, VertexAIProvider, OpenAIProvider, CustomProvider):
    register_provider(_provider_class)


class ThabotAiService(models.AbstractModel):
    """Entry point used by the models; override it to customise the pipeline."""

    _name = "thabot.ai.service"
    _description = "AI Service"

    @api.model
    def get_provider(self, agent, config=None):
        config = config or agent.get_provider_config()
        provider_class = PROVIDER_REGISTRY.get(agent.provider)
        if not provider_class:
            raise UserError(
                _("No implementation registered for provider '%(provider)s'.",
                  provider=agent.provider)
            )
        return provider_class(self.env, config)

    @api.model
    def dispatch(self, agent, messages, tools=None):
        """Run one generation round and return a plain dict."""
        if not messages:
            raise UserError(_("Cannot call the AI provider without any message."))
        provider = self.get_provider(agent)
        tools = agent.tool_ids if tools is None else tools
        response = provider.generate(agent, messages, tools)
        if not isinstance(response, AIProviderResponse):
            raise UserError(_("The provider implementation returned an unexpected result."))
        return response.to_dict()
