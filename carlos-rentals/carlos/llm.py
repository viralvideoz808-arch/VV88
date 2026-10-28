"""Claude, used when ANTHROPIC_API_KEY is set. Every agent also has a rule-based path, so nothing breaks without it."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from carlos.config import Settings

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMUnavailable(Exception):
    pass


class Claude:
    def __init__(self, settings: Settings, client=None):
        self.s = settings
        self._client = client

    @property
    def available(self) -> bool:
        return self._client is not None or self.s.llm_enabled

    def client(self):
        if self._client is None:
            if not self.s.llm_enabled:
                raise LLMUnavailable("ANTHROPIC_API_KEY not set")
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.s.anthropic_api_key, timeout=120.0, max_retries=2)
        return self._client

    def _create(self, **kwargs):
        import anthropic
        try:
            resp = self.client().beta.messages.create(
                model=self.s.model, betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
        except anthropic.BadRequestError as e:
            raise LLMUnavailable(f"bad request: {e.message}") from e
        except anthropic.AuthenticationError as e:
            raise LLMUnavailable("ANTHROPIC_API_KEY was rejected") from e
        except anthropic.RateLimitError as e:
            raise LLMUnavailable("rate limited") from e
        except anthropic.APIStatusError as e:
            raise LLMUnavailable(f"API error {e.status_code}") from e
        except anthropic.APIConnectionError as e:
            raise LLMUnavailable("network error reaching the Claude API") from e
        if resp.stop_reason == "refusal":
            raise LLMUnavailable("request declined")
        return resp

    def structured(self, system: str, prompt: str, schema: dict, effort: str = "low", max_tokens: int = 8000) -> dict:
        resp = self._create(
            max_tokens=max_tokens, system=system,
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMUnavailable("response was not valid JSON") from e

    def text(self, system: str, prompt: str, effort: str = "low", max_tokens: int = 4000) -> str:
        resp = self._create(max_tokens=max_tokens, system=system, output_config={"effort": effort},
                            messages=[{"role": "user", "content": prompt}])
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    def tool_loop(self, system: str, messages: list[dict], tools: list[dict],
                  run_tool: Callable[[str, dict], Any], effort: str = "low", max_turns: int = 6) -> str:
        """Manual agentic loop; returns the final reply text. The full assistant content is appended every turn."""
        messages = list(messages)
        for _ in range(max_turns):
            resp = self._create(max_tokens=8000, system=system, tools=tools, output_config={"effort": effort},
                                messages=messages)
            if resp.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            calls = [b for b in resp.content if b.type == "tool_use"]
            if resp.stop_reason != "tool_use" or not calls:
                return "".join(b.text for b in resp.content if b.type == "text").strip()
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for call in calls:
                try:
                    output = run_tool(call.name, dict(call.input or {}))
                    results.append({"type": "tool_result", "tool_use_id": call.id,
                                    "content": json.dumps(output, default=str)[:12000]})
                except Exception as e:  # tool errors go back to Claude rather than crashing the agent
                    results.append({"type": "tool_result", "tool_use_id": call.id, "content": f"Error: {e}",
                                    "is_error": True})
            messages.append({"role": "user", "content": results})
        raise LLMUnavailable("conversation needed too many tool calls")
