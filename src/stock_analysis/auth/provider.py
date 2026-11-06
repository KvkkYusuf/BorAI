"""External identity provider (OAuth2 / OIDC / SSO) integration adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExternalIdentity:
    """Standardized representation of a user profile retrieved from external provider."""

    provider: str
    provider_user_id: str
    email: str
    is_verified: bool
    display_name: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


class IdentityProviderAdapter(ABC):
    """Abstract contract for third-party identity providers (Google, GitHub, OIDC, etc.)."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the identity provider (e.g. 'google', 'oidc_corp')."""

    @abstractmethod
    async def exchange_code_for_identity(self, auth_code: str) -> ExternalIdentity:
        """Exchange an authorization code for validated user profile identity claims."""

    @abstractmethod
    def get_authorization_url(self, state: str, redirect_uri: str) -> str:
        """Construct the OAuth2 authorization URL."""


class MockIdentityProvider(IdentityProviderAdapter):
    """Deterministic mock provider for testing and offline development."""

    def __init__(self, provider_name: str = "mock_oidc") -> None:
        self._name = provider_name
        self._registered_identities: dict[str, ExternalIdentity] = {}

    @property
    def provider_name(self) -> str:
        return self._name

    def register_test_identity(self, auth_code: str, identity: ExternalIdentity) -> None:
        """Register an identity for a specific auth code."""
        self._registered_identities[auth_code] = identity

    async def exchange_code_for_identity(self, auth_code: str) -> ExternalIdentity:
        if auth_code not in self._registered_identities:
            msg = f"Geçersiz yetkilendirme kodu: {auth_code}"
            raise ValueError(msg)
        return self._registered_identities[auth_code]

    def get_authorization_url(self, state: str, redirect_uri: str) -> str:
        return f"https://auth.example.com/oauth/authorize?provider={self._name}&state={state}&redirect_uri={redirect_uri}"
