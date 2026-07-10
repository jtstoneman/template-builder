"""Thin wrapper around the Claude API.

This is the ONLY module that talks to an LLM. Everything that calls it sits
UPSTREAM of the approval boundary: template build (`tb build`), term-sheet
intake (`tb intake`), skill distillation (`tb skill update`), moot-court
replay (`tb skill replay`) and negotiation drafting (`tb negotiate` /
`tb matter round --negotiate`). Rendering, validation, hashing and approval
never touch it — that separation is the whole point of the tool.

Every call goes through `client.messages.parse` with a Pydantic model as the
output format: the API constrains generation to the model's JSON schema and
the SDK validates the response, so callers receive a typed, validated
instance — never raw JSON.
"""

import os
import threading
from typing import Any

from pydantic import BaseModel, ValidationError

DEFAULT_MODEL = os.environ.get("TB_MODEL", "claude-opus-4-8")

_client: Any = None
_client_lock = threading.Lock()  # atomise() runs in a thread pool


class LLMError(RuntimeError):
    pass


def _get_client() -> Any:
    global _client
    with _client_lock:
        if _client is None:
            try:
                import anthropic
            except ImportError:
                raise LLMError("the 'anthropic' package is not installed; "
                               "run: pip install anthropic") from None
            try:
                _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY / auth profile
            except Exception as e:
                raise LLMError(f"could not initialise the Claude client — is "
                               f"ANTHROPIC_API_KEY set? ({e})") from None
        return _client


def complete[T: BaseModel](
    system: str,
    prompt: str,
    output: type[T],
    *,
    max_tokens: int = 16000,
    model: str | None = None,
) -> T:
    """One LLM call, constrained to and validated against a Pydantic model."""
    client = _get_client()  # before `import anthropic`: its LLMError must be reachable
    import anthropic

    for attempt in (1, 2):
        try:
            response = client.messages.parse(
                model=model or DEFAULT_MODEL,
                max_tokens=max_tokens,
                system=system,
                thinking={"type": "adaptive"},
                output_format=output,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.AuthenticationError:
            raise LLMError("Claude API authentication failed — set ANTHROPIC_API_KEY") from None
        except anthropic.APIStatusError as e:
            raise LLMError(f"Claude API error ({e.status_code}): {e.message}") from None
        except anthropic.APIConnectionError as e:
            raise LLMError(f"could not reach the Claude API: {e}") from None
        except ValidationError as e:
            # The SDK validates the structured output while parsing the response;
            # a truncated turn surfaces here, before stop_reason is reachable.
            raise LLMError(f"model output did not match the {output.__name__} schema "
                           f"(possibly truncated at {max_tokens} tokens): {e}") from None

        match response.stop_reason:
            case "refusal":
                raise LLMError("the model declined this request (stop_reason=refusal)")
            case "max_tokens":
                raise LLMError(f"model output was truncated at {max_tokens} tokens — "
                               f"the input document may be too long for one call")
            case "model_context_window_exceeded":
                raise LLMError("the input exceeds the model's context window — "
                               "split the document or shorten the prompt")
            case "pause_turn" if attempt == 1:
                continue  # transient by design: reissue the request once
            case "pause_turn":
                raise LLMError("the model paused mid-turn twice (stop_reason="
                               "pause_turn) — retry the command")

        parsed = response.parsed_output
        if parsed is None:
            raise LLMError(f"model returned no parseable {output.__name__} output")
        return parsed
    raise AssertionError("unreachable")
