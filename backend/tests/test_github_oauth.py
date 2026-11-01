"""Tests for GitHubOAuthService with respx HTTP mocking.

Covers:
- Exchange code for access token (POST /login/oauth/access_token)
- Fetch user profile (GET /user)
- OAuth error handling (bad_verification_code)
- Network failures and HTTP status mapping to domain exceptions
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.services.github.exceptions import (
    GitHubAuthenticationError,
    GitHubNetworkError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubServerError,
)
from app.services.github.oauth import GitHubOAuthService


def _make_service() -> GitHubOAuthService:
    """Create an OAuthService with test credentials."""
    return GitHubOAuthService(
        client_id="test-client-id",
        client_secret="test-client-secret",
        redirect_uri="http://localhost:3000/callback",
    )


# =====================================================================
# Happy path: full exchange_code flow
# =====================================================================


@pytest.mark.anyio
@respx.mock
async def test_exchange_code_happy_path():
    """Full OAuth flow: code → token → user → emails → installations."""
    respx.post(
        "https://github.com/login/oauth/access_token",
    ).mock(
        return_value=httpx.Response(
            200,
            json={"access_token": "ghu_test_token_123", "token_type": "bearer"},
        ),
    )
    respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 42,
                "login": "testuser",
                "name": "Test User",
                "email": "test@example.com",
            },
        ),
    )
    respx.get("https://api.github.com/user/emails").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"email": "primary@example.com", "primary": True, "verified": True},
                {"email": "secondary@example.com", "primary": False, "verified": True},
            ],
        ),
    )
    respx.get("https://api.github.com/user/installations").mock(
        return_value=httpx.Response(
            200,
            json={
                "total_count": 1,
                "installations": [{"id": 12345}],
            },
        ),
    )

    svc = _make_service()
    identity = await svc.exchange_code("valid-code-123")

    assert identity.github_id == "42"
    assert identity.github_login == "testuser"
    assert identity.display_name == "Test User"
    assert identity.email == "primary@example.com"
    assert identity.access_token == "ghu_test_token_123"
    assert identity.installations == ["12345"]


@pytest.mark.anyio
@respx.mock
async def test_exchange_code_no_primary_email_falls_back_to_profile():
    """When no primary verified email, falls back to profile email."""
    respx.post(
        "https://github.com/login/oauth/access_token",
    ).mock(
        return_value=httpx.Response(
            200, json={"access_token": "ghu_tok", "token_type": "bearer"},
        ),
    )
    respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(
            200,
            json={"id": 99, "login": "u", "name": None, "email": "fallback@x.com"},
        ),
    )
    respx.get("https://api.github.com/user/emails").mock(
        return_value=httpx.Response(
            200,
            json=[{"email": "unverified@x.com", "primary": True, "verified": False}],
        ),
    )
    respx.get("https://api.github.com/user/installations").mock(
        return_value=httpx.Response(200, json={"installations": []}),
    )

    identity = await _make_service().exchange_code("code")
    assert identity.email == "fallback@x.com"
    assert identity.installations == []


# =====================================================================
# Token exchange errors
# =====================================================================


@pytest.mark.anyio
@respx.mock
async def test_exchange_code_bad_verification_code():
    """GitHub returns error=bad_verification_code → GitHubAuthenticationError."""
    respx.post(
        "https://github.com/login/oauth/access_token",
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "error": "bad_verification_code",
                "error_description": "The code passed is incorrect or expired.",
            },
        ),
    )

    svc = _make_service()
    with pytest.raises(GitHubAuthenticationError, match="access token"):
        await svc.exchange_code("expired-code")


@pytest.mark.anyio
@respx.mock
async def test_exchange_code_token_endpoint_401():
    """Token endpoint returns 401 → GitHubAuthenticationError."""
    respx.post(
        "https://github.com/login/oauth/access_token",
    ).mock(
        return_value=httpx.Response(
            401,
            json={"message": "Bad credentials"},
        ),
    )

    with pytest.raises(GitHubAuthenticationError, match="authentication failed"):
        await _make_service().exchange_code("code")


@pytest.mark.anyio
@respx.mock
async def test_exchange_code_token_endpoint_server_error():
    """Token endpoint returns 500 → GitHubServerError."""
    respx.post(
        "https://github.com/login/oauth/access_token",
    ).mock(
        return_value=httpx.Response(
            500,
            json={"message": "Internal Server Error"},
        ),
    )

    with pytest.raises(GitHubServerError):
        await _make_service().exchange_code("code")


@pytest.mark.anyio
async def test_exchange_code_missing_credentials():
    """Service without credentials raises GitHubAuthenticationError."""
    svc = GitHubOAuthService(client_id=None, client_secret=None)

    with pytest.raises(GitHubAuthenticationError, match="not configured"):
        await svc.exchange_code("code")


# =====================================================================
# User profile fetch errors
# =====================================================================


@pytest.mark.anyio
@respx.mock
async def test_exchange_code_user_endpoint_401():
    """User endpoint returns 401 → GitHubAuthenticationError."""
    respx.post(
        "https://github.com/login/oauth/access_token",
    ).mock(
        return_value=httpx.Response(
            200, json={"access_token": "ghu_tok", "token_type": "bearer"},
        ),
    )
    respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(401, json={"message": "Bad credentials"}),
    )

    with pytest.raises(GitHubAuthenticationError, match="authentication failed"):
        await _make_service().exchange_code("code")


@pytest.mark.anyio
@respx.mock
async def test_exchange_code_user_endpoint_404():
    """User endpoint returns 404 → GitHubNotFoundError."""
    respx.post(
        "https://github.com/login/oauth/access_token",
    ).mock(
        return_value=httpx.Response(
            200, json={"access_token": "ghu_tok", "token_type": "bearer"},
        ),
    )
    respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"}),
    )

    with pytest.raises(GitHubNotFoundError):
        await _make_service().exchange_code("code")


# =====================================================================
# Rate limit handling
# =====================================================================


@pytest.mark.anyio
@respx.mock
async def test_exchange_code_rate_limit_on_user_endpoint():
    """Rate limit on /user → GitHubRateLimitError with reset_at."""
    respx.post(
        "https://github.com/login/oauth/access_token",
    ).mock(
        return_value=httpx.Response(
            200, json={"access_token": "ghu_tok", "token_type": "bearer"},
        ),
    )
    respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={"X-RateLimit-Reset": "1700000000"},
        ),
    )

    with pytest.raises(GitHubRateLimitError) as exc_info:
        await _make_service().exchange_code("code")
    assert exc_info.value.reset_at == 1700000000


# =====================================================================
# Network errors
# =====================================================================


@pytest.mark.anyio
@respx.mock
async def test_exchange_code_network_error():
    """Network failure during token exchange → GitHubNetworkError."""
    respx.post(
        "https://github.com/login/oauth/access_token",
    ).mock(side_effect=httpx.ConnectError("Connection refused"))

    with pytest.raises(GitHubNetworkError, match="Network error"):
        await _make_service().exchange_code("code")


@pytest.mark.anyio
@respx.mock
async def test_exchange_code_timeout():
    """Timeout during token exchange → GitHubNetworkError."""
    respx.post(
        "https://github.com/login/oauth/access_token",
    ).mock(side_effect=httpx.ReadTimeout("Read timed out"))

    with pytest.raises(GitHubNetworkError, match="timeout"):
        await _make_service().exchange_code("code")


# =====================================================================
# Email / installations endpoint resilience
# =====================================================================


@pytest.mark.anyio
@respx.mock
async def test_exchange_code_emails_failure_returns_empty():
    """If /user/emails fails, email falls back to profile and no crash."""
    respx.post(
        "https://github.com/login/oauth/access_token",
    ).mock(
        return_value=httpx.Response(
            200, json={"access_token": "ghu_tok", "token_type": "bearer"},
        ),
    )
    respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(
            200,
            json={"id": 1, "login": "u", "name": "U", "email": "p@example.com"},
        ),
    )
    respx.get("https://api.github.com/user/emails").mock(
        return_value=httpx.Response(403, json={"message": "Forbidden"}),
    )
    respx.get("https://api.github.com/user/installations").mock(
        return_value=httpx.Response(200, json={"installations": []}),
    )

    identity = await _make_service().exchange_code("code")
    assert identity.email == "p@example.com"


@pytest.mark.anyio
@respx.mock
async def test_exchange_code_installations_failure_returns_empty():
    """If /user/installations fails, installations list is empty."""
    respx.post(
        "https://github.com/login/oauth/access_token",
    ).mock(
        return_value=httpx.Response(
            200, json={"access_token": "ghu_tok", "token_type": "bearer"},
        ),
    )
    respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(
            200, json={"id": 1, "login": "u", "name": "U", "email": None},
        ),
    )
    respx.get("https://api.github.com/user/emails").mock(
        return_value=httpx.Response(200, json=[]),
    )
    respx.get("https://api.github.com/user/installations").mock(
        return_value=httpx.Response(500, json={"message": "Internal Server Error"}),
    )

    identity = await _make_service().exchange_code("code")
    assert identity.installations == []
