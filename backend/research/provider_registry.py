from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import pathlib
import socket
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit, urlunsplit

import jsonschema


ROOT = pathlib.Path(__file__).resolve().parents[2]
PROFILE_SCHEMA_PATH = (
    ROOT / "contracts/domain/research/ai-provider-profile.schema.json"
)
CONFIG_ENV = "DIRAC_AI_PROVIDER_CONFIG"
ALLOWLIST_ENV = "DIRAC_AI_PROVIDER_HOST_ALLOWLIST"


class AiProviderConfigurationError(ValueError):
    """A fail-closed profile refusal whose text never includes a secret."""

    def __init__(self, reason: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.details = dict(details or {})


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_digest(value: Any) -> str:
    raw = value if isinstance(value, bytes) else canonical_json(value)
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _profile_digest(profile: Mapping[str, Any], base_url: str) -> str:
    document = dict(profile)
    document["resolved_base_url"] = base_url
    return sha256_digest(document)


def _safe_base_url(raw: str) -> str:
    parts = urlsplit(raw.strip())
    if not parts.scheme or not parts.hostname:
        raise AiProviderConfigurationError("provider_base_url_is_not_absolute")
    if parts.username is not None or parts.password is not None:
        raise AiProviderConfigurationError("provider_url_userinfo_is_forbidden")
    if parts.query or parts.fragment:
        raise AiProviderConfigurationError("provider_url_query_or_fragment_is_forbidden")
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc, path, "", ""))


def _addresses(
    hostname: str,
    port: int,
    resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        resolved = resolver(hostname, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise AiProviderConfigurationError(
            "provider_hostname_resolution_failed",
            details={"exception": type(error).__name__},
        ) from None
    addresses = {ipaddress.ip_address(item[4][0]) for item in resolved}
    if not addresses:
        raise AiProviderConfigurationError("provider_hostname_resolved_to_nothing")
    return addresses


def validate_provider_url(
    base_url: str,
    locality: str,
    *,
    allowed_local_hosts: set[str] | None = None,
    resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
) -> str:
    """Validate scheme, host ownership and DNS category immediately before use."""

    normalized = _safe_base_url(base_url)
    parts = urlsplit(normalized)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    allowlist = {item.lower() for item in (allowed_local_hosts or set())}

    if locality == "external_cloud":
        if parts.scheme != "https":
            raise AiProviderConfigurationError("external_provider_requires_https")
        for address in _addresses(host, port, resolver):
            if not address.is_global:
                raise AiProviderConfigurationError(
                    "external_provider_resolved_outside_public_network"
                )
        return normalized

    if locality != "local_network":
        raise AiProviderConfigurationError("unknown_provider_locality")
    if parts.scheme not in {"http", "https"}:
        raise AiProviderConfigurationError("local_provider_scheme_is_forbidden")

    literal = None
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        pass
    if not (literal and literal.is_loopback) and host.lower() not in allowlist:
        raise AiProviderConfigurationError("local_provider_host_not_allowlisted")
    for address in _addresses(host, port, resolver):
        if not (address.is_loopback or address.is_private or address.is_link_local):
            raise AiProviderConfigurationError(
                "local_provider_resolved_outside_local_network"
            )
    return normalized


@dataclass(frozen=True)
class ResolvedProviderProfile:
    document: Mapping[str, Any]
    base_url: str = field(repr=False)
    api_key: str = field(repr=False)
    profile_digest: str
    allowed_local_hosts: frozenset[str] = field(default_factory=frozenset, repr=False)

    @property
    def profile_id(self) -> str:
        return str(self.document["profile_id"])

    @property
    def configured_model(self) -> str:
        return str(self.document["model"])

    def to_public_dict(self) -> dict[str, Any]:
        policy = self.document["data_policy"]
        return {
            "profile_id": self.profile_id,
            "profile_digest": self.profile_digest,
            "label": self.document["label"],
            "configured_model": self.configured_model,
            "locality": self.document["locality"],
            "external_egress": bool(policy["external_egress"]),
            "allowed_classifications": list(policy["allowed_classifications"]),
            "configured": True,
        }

    def to_provenance(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "profile_digest": self.profile_digest,
            "configured_model": self.configured_model,
            "locality": self.document["locality"],
            "resource_isolation": self.document["resource_isolation"],
        }


class AiProviderRegistry(Protocol):
    def list_public(self) -> list[dict[str, Any]]: ...

    def resolve(self, profile_id: str) -> ResolvedProviderProfile: ...

    def attest(
        self, profile_id: str, expected_digest: str, data_classification: str
    ) -> dict[str, Any]: ...


class FileAiProviderRegistry:
    def __init__(
        self,
        config_path: str | os.PathLike[str] | None,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._environ = environ if environ is not None else os.environ
        self._profiles: dict[str, dict[str, Any]] = {}
        self._schema = json.loads(PROFILE_SCHEMA_PATH.read_text(encoding="utf-8"))
        if config_path is None:
            return
        source = pathlib.Path(config_path)
        try:
            root = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AiProviderConfigurationError(
                "provider_config_cannot_be_loaded",
                details={"exception": type(error).__name__},
            ) from None
        documents = root.get("profiles") if isinstance(root, dict) else None
        if not isinstance(documents, list):
            raise AiProviderConfigurationError("provider_config_requires_profiles_array")
        for index, document in enumerate(documents):
            try:
                jsonschema.Draft202012Validator(self._schema).validate(document)
            except jsonschema.ValidationError as error:
                raise AiProviderConfigurationError(
                    "provider_profile_schema_invalid",
                    details={"index": index, "pointer": list(error.absolute_path)},
                ) from None
            profile_id = document["profile_id"]
            if profile_id in self._profiles:
                raise AiProviderConfigurationError(
                    "duplicate_provider_profile_id", details={"profile_id": profile_id}
                )
            self._profiles[profile_id] = document

    def _resolve_document(self, profile_id: str) -> tuple[dict[str, Any], str, str]:
        document = self._profiles.get(profile_id)
        if document is None:
            raise AiProviderConfigurationError(
                "provider_profile_not_found", details={"profile_id": profile_id}
            )
        if document["resource_isolation"] == "shared_dirac_gpu":
            raise AiProviderConfigurationError(
                "shared_gpu_provider_not_supported_in_v0",
                details={
                    "reason": "shared_gpu_provider_not_supported_in_v0",
                    "recovery": (
                        "use cloud inference or a separately isolated model endpoint"
                    ),
                },
            )
        base_url = self._environ.get(document["base_url_env"], "").strip()
        api_key = self._environ.get(document["api_key_env"], "").strip()
        if not base_url or not api_key:
            missing = []
            if not base_url:
                missing.append(document["base_url_env"])
            if not api_key:
                missing.append(document["api_key_env"])
            raise AiProviderConfigurationError(
                "provider_profile_is_unconfigured", details={"missing_env": missing}
            )
        return document, _safe_base_url(base_url), api_key

    def resolve(self, profile_id: str) -> ResolvedProviderProfile:
        document, base_url, api_key = self._resolve_document(profile_id)
        allowlist = frozenset(
            value.strip().lower()
            for value in self._environ.get(ALLOWLIST_ENV, "").split(",")
            if value.strip()
        )
        return ResolvedProviderProfile(
            document=document,
            base_url=base_url,
            api_key=api_key,
            profile_digest=_profile_digest(document, base_url),
            allowed_local_hosts=allowlist,
        )

    def list_public(self) -> list[dict[str, Any]]:
        result = []
        for profile_id, document in sorted(self._profiles.items()):
            try:
                result.append(self.resolve(profile_id).to_public_dict())
            except AiProviderConfigurationError as error:
                result.append(
                    {
                        "profile_id": profile_id,
                        "label": document["label"],
                        "configured_model": document["model"],
                        "locality": document["locality"],
                        "external_egress": bool(
                            document["data_policy"]["external_egress"]
                        ),
                        "allowed_classifications": list(
                            document["data_policy"]["allowed_classifications"]
                        ),
                        "configured": False,
                        "reason": error.reason,
                    }
                )
        return result

    def attest(
        self, profile_id: str, expected_digest: str, data_classification: str
    ) -> dict[str, Any]:
        profile = self.resolve(profile_id)
        if profile.profile_digest != expected_digest:
            raise AiProviderConfigurationError("provider_profile_digest_mismatch")
        allowed = profile.document["data_policy"]["allowed_classifications"]
        if data_classification not in allowed:
            raise AiProviderConfigurationError(
                "provider_data_classification_denied",
                details={"data_classification": data_classification},
            )
        witness = profile.to_provenance()
        witness.update(
            {
                "external_egress": bool(
                    profile.document["data_policy"]["external_egress"]
                ),
                "data_classification": data_classification,
            }
        )
        return witness


def default_ai_provider_registry() -> FileAiProviderRegistry:
    return FileAiProviderRegistry(os.environ.get(CONFIG_ENV))
