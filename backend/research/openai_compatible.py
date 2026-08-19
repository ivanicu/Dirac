from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .provider_registry import (
    AiProviderConfigurationError,
    ResolvedProviderProfile,
    validate_provider_url,
)
from .metrics import METRICS, model_family, status_class


RETRYABLE_HTTP_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER_SECONDS = 10.0
GENERATION_UNSUPPORTED_SCHEMA_KEYS = frozenset({"propertyNames", "uniqueItems"})
GENERATION_MAX_ITEMS = 1
GENERATION_MAX_TEXT_LENGTH = 512


class ProviderUnavailable(RuntimeError):
    code = "PROVIDER_UNAVAILABLE"

    def __init__(self, reason: str, *, attempts: int = 0):
        super().__init__(reason)
        self.reason = reason
        self.attempts = attempts


class ModelOutputInvalid(RuntimeError):
    code = "MODEL_OUTPUT_INVALID"

    def __init__(self, reason: str, *, attempts: int = 1):
        super().__init__(reason)
        self.reason = reason
        self.attempts = attempts


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _generation_schema(value: Any) -> Any:
    """Keep grammar-expressible constraints; DIRAC still validates the frozen schema."""

    if isinstance(value, Mapping):
        result = {
            key: _generation_schema(item)
            for key, item in value.items()
            if key not in GENERATION_UNSUPPORTED_SCHEMA_KEYS
        }
        if isinstance(result.get("maxItems"), int):
            result["maxItems"] = min(result["maxItems"], GENERATION_MAX_ITEMS)
        if isinstance(result.get("maxLength"), int):
            result["maxLength"] = min(
                result["maxLength"], GENERATION_MAX_TEXT_LENGTH
            )
        return result
    if isinstance(value, list):
        return [_generation_schema(item) for item in value]
    return value


@dataclass(frozen=True)
class ProviderChatResult:
    content: str
    configured_model: str
    resolved_model: str | None
    provider_request_id: str | None
    usage: dict[str, Any]
    attempts: int
    transport_events: tuple[dict[str, Any], ...]


class OpenAICompatibleChatProvider:
    """Bounded non-streaming JSON chat transport with no provider SDK."""

    def __init__(
        self,
        *,
        opener: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        resolver: Callable[..., list[tuple]] | None = None,
    ) -> None:
        self._opener = opener or urllib.request.build_opener(_NoRedirect())
        self._sleep = sleep
        self._monotonic = monotonic
        self._resolver = resolver

    def _endpoint(self, profile: ResolvedProviderProfile) -> str:
        kw = {"allowed_local_hosts": set(profile.allowed_local_hosts)}
        if self._resolver is not None:
            kw["resolver"] = self._resolver
        try:
            base_url = validate_provider_url(
                profile.base_url, str(profile.document["locality"]), **kw
            )
        except AiProviderConfigurationError as error:
            raise ProviderUnavailable(error.reason) from None
        return base_url.rstrip("/") + "/chat/completions"

    @staticmethod
    def _retry_delay(headers: Any, attempt: int) -> float:
        raw = headers.get("Retry-After") if headers is not None else None
        if raw is not None:
            try:
                return min(MAX_RETRY_AFTER_SECONDS, max(0.0, float(raw)))
            except (TypeError, ValueError):
                pass
        return min(MAX_RETRY_AFTER_SECONDS, 0.25 * (2 ** max(0, attempt - 1)))

    @staticmethod
    def _request_body(
        profile: ResolvedProviderProfile, system_prompt: str, context_json: str,
        output_schema: Mapping[str, Any] | None,
    ) -> bytes:
        if "JSON" not in system_prompt.upper() or "JSON" not in context_json.upper():
            raise ModelOutputInvalid("messages_must_explicitly_request_json")
        response_mode = str(profile.document["response_mode"])
        if response_mode == "json_schema":
            if not isinstance(output_schema, Mapping):
                raise ModelOutputInvalid("json_schema_profile_requires_output_schema")
            response_format: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "dirac_research_proposal",
                    "strict": True,
                    "schema": _generation_schema(output_schema),
                },
            }
        else:
            response_format = {"type": "json_object"}
        body: dict[str, Any] = {
            "model": profile.configured_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context_json},
            ],
            "response_format": response_format,
            "stream": False,
        }
        body.update(dict(profile.document["static_request_fields"]))
        return json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    @staticmethod
    def _decode_response(
        payload: bytes, profile: ResolvedProviderProfile, attempts: int,
        events: list[dict[str, Any]], headers: Any,
    ) -> ProviderChatResult:
        try:
            document = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ModelOutputInvalid("provider_response_is_not_json", attempts=attempts) from None
        if not isinstance(document, dict):
            raise ModelOutputInvalid("provider_response_root_is_not_object", attempts=attempts)
        choices = document.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ModelOutputInvalid("provider_response_has_no_choice", attempts=attempts)
        choice = choices[0]
        if choice.get("finish_reason") == "length":
            raise ModelOutputInvalid("provider_output_was_truncated", attempts=attempts)
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ModelOutputInvalid("provider_choice_has_no_message", attempts=attempts)
        if message.get("tool_calls") or message.get("function_call"):
            raise ModelOutputInvalid("provider_attempted_tool_call", attempts=attempts)
        content = message.get("content")
        if not isinstance(content, str):
            raise ModelOutputInvalid("provider_message_content_is_not_text", attempts=attempts)
        usage = document.get("usage") if isinstance(document.get("usage"), dict) else {}
        request_id = None
        if headers is not None:
            request_id = headers.get("x-request-id") or headers.get("request-id")
        if request_id is None and isinstance(document.get("id"), str):
            request_id = document["id"]
        return ProviderChatResult(
            content=content,
            configured_model=profile.configured_model,
            resolved_model=(document.get("model") if isinstance(document.get("model"), str) else None),
            provider_request_id=request_id,
            usage=dict(usage),
            attempts=attempts,
            transport_events=tuple(events),
        )

    def complete_json(
        self,
        profile: ResolvedProviderProfile,
        *,
        system_prompt: str,
        context_json: str,
        output_schema: Mapping[str, Any] | None = None,
    ) -> ProviderChatResult:
        started = self._monotonic()
        attempts = 0
        try:
            result = self._complete_json(
                profile, system_prompt=system_prompt, context_json=context_json,
                output_schema=output_schema)
            attempts = result.attempts
            return result
        except (ProviderUnavailable, ModelOutputInvalid) as error:
            attempts = error.attempts
            raise
        finally:
            METRICS.observe(
                "dirac_research_loop_reasoner_seconds",
                max(0.0, self._monotonic() - started),
                {"profile_id": profile.profile_id,
                 "model_family": model_family(profile.configured_model)},
            )
            METRICS.observe(
                "dirac_research_loop_provider_attempts", attempts,
                {"profile_id": profile.profile_id},
            )

    def _complete_json(
        self,
        profile: ResolvedProviderProfile,
        *,
        system_prompt: str,
        context_json: str,
        output_schema: Mapping[str, Any] | None,
    ) -> ProviderChatResult:
        endpoint = self._endpoint(profile)
        payload = self._request_body(
            profile, system_prompt, context_json, output_schema)
        max_request = int(profile.document["bounds"]["max_request_bytes"])
        max_response = int(profile.document["bounds"]["max_response_bytes"])
        if len(payload) > max_request:
            raise ModelOutputInvalid("provider_request_exceeds_profile_bound")
        maximum_attempts = int(profile.document["bounds"]["max_provider_attempts"])
        timeout = float(profile.document["timeouts"]["request_seconds"])
        events: list[dict[str, Any]] = []

        for attempt in range(1, maximum_attempts + 1):
            request = urllib.request.Request(
                endpoint,
                data=payload,
                method="POST",
                headers={
                    "Authorization": "Bearer " + profile.api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            started = self._monotonic()
            try:
                with self._opener.open(request, timeout=timeout) as response:
                    body = response.read(max_response + 1)
                    duration = max(0.0, self._monotonic() - started)
                    events.append(
                        {
                            "attempt": attempt,
                            "status": int(response.status),
                            "duration_seconds": duration,
                            "response_bytes": len(body),
                        }
                    )
                    METRICS.counter(
                        "dirac_research_loop_provider_http_total",
                        {"profile_id": profile.profile_id,
                         "status_class": status_class(int(response.status))},
                    )
                    if len(body) > max_response:
                        raise ModelOutputInvalid(
                            "provider_response_exceeds_profile_bound", attempts=attempt
                        )
                    return self._decode_response(
                        body, profile, attempt, events, response.headers
                    )
            except urllib.error.HTTPError as error:
                duration = max(0.0, self._monotonic() - started)
                status = int(error.code)
                error_headers = error.headers
                error.close()
                events.append(
                    {
                        "attempt": attempt,
                        "status": status,
                        "duration_seconds": duration,
                        "response_bytes": 0,
                    }
                )
                METRICS.counter(
                    "dirac_research_loop_provider_http_total",
                    {"profile_id": profile.profile_id,
                     "status_class": status_class(status)},
                )
                if status in {301, 302, 303, 307, 308}:
                    raise ProviderUnavailable("provider_redirect_refused", attempts=attempt) from None
                if status not in RETRYABLE_HTTP_STATUS or attempt >= maximum_attempts:
                    reason = (
                        "provider_authentication_failed"
                        if status in {401, 403}
                        else "provider_http_failure"
                    )
                    raise ProviderUnavailable(reason, attempts=attempt) from None
                self._sleep(self._retry_delay(error_headers, attempt))
            except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as error:
                duration = max(0.0, self._monotonic() - started)
                events.append(
                    {
                        "attempt": attempt,
                        "status": "transport_error",
                        "duration_seconds": duration,
                        "response_bytes": 0,
                        "exception": type(error).__name__,
                    }
                )
                METRICS.counter(
                    "dirac_research_loop_provider_http_total",
                    {"profile_id": profile.profile_id,
                     "status_class": "transport_error"},
                )
                if attempt >= maximum_attempts:
                    raise ProviderUnavailable(
                        "provider_transport_failure", attempts=attempt
                    ) from None
                self._sleep(self._retry_delay(None, attempt))

        raise ProviderUnavailable("provider_attempt_budget_exhausted", attempts=maximum_attempts)
