"""Tests for GitHubIntegrationService with respx HTTP mocking.

Covers:
- GitHub App JWT generation and installation token acquisition
- Pull request and diff retrieval
- Check-run creation
- Comment posting
- Retry policy on 502/503/504
- Rate limit handling (403/429)
- Error mapping for 401/404/409/422
"""

from __future__ import annotations

import time
from unittest.mock import patch

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from app.services.github.exceptions import (
    GitHubAuthenticationError,
    GitHubConflictError,
    GitHubNetworkError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubServerError,
    GitHubValidationError,
)
from app.services.github.integration import GitHubIntegrationService


def _generate_test_private_key() -> str:
    """Generate a test RSA private key in PEM format."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    return private_key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    ).decode("utf-8")


# Module-level test key (generated once for performance)
TEST_PRIVATE_KEY = _generate_test_private_key()
TEST_APP_ID = "123456"
TEST_INSTALLATION_ID = "inst-42"


def _make_service() -> GitHubIntegrationService:
    """Create an IntegrationService with test credentials."""
    return GitHubIntegrationService(
        app_id=TEST_APP_ID,
        private_key=TEST_PRIVATE_KEY,
    )


# =====================================================================
# JWT generation
# =====================================================================


def test_generate_jwt_creates_valid_rs256_token():
    """JWT is signed with RS256, contains iss=app_id, and valid timing."""
    svc = _make_service()
    token = svc._generate_jwt()

    # Decode without verification to inspect claims
    decoded = jwt.decode(token, options={"verify_signature": False})
    assert decoded["iss"] == TEST_APP_ID
    assert decoded["exp"] > decoded["iat"]
    # Token should expire within ~11 minutes from now (600s + 60s skew)
    assert decoded["exp"] - decoded["iat"] <= 660


def test_generate_jwt_without_credentials_raises():
    """Missing credentials raises GitHubAuthenticationError."""
    svc = GitHubIntegrationService(app_id=None, private_key=None)
    with pytest.raises(GitHubAuthenticationError, match="not configured"):
        svc._generate_jwt()


# =====================================================================
# Installation token
# =====================================================================


@pytest.mark.anyio
@respx.mock
async def test_get_installation_token():
    """POST /app/installations/{id}/access_tokens → cached token."""
    respx.post(
        f"https://api.github.com/app/installations/{TEST_INSTALLATION_ID}/access_tokens",
    ).mock(
        return_value=httpx.Response(
            201,
            json={"token": "ghs_installation_token_42", "expires_at": "2099-01-01T00:00:00Z"},
        ),
    )

    svc = _make_service()
    token = await svc._get_installation_token(TEST_INSTALLATION_ID)
    assert token == "ghs_installation_token_42"

    # Second call should return cached value (no second HTTP call)
    token2 = await svc._get_installation_token(TEST_INSTALLATION_ID)
    assert token2 == "ghs_installation_token_42"
    assert respx.calls.call_count == 1


@pytest.mark.anyio
@respx.mock
async def test_get_installation_token_refreshes_expired():
    """Expired cached token is refreshed."""
    route = respx.post(
        f"https://api.github.com/app/installations/{TEST_INSTALLATION_ID}/access_tokens",
    ).mock(
        return_value=httpx.Response(
            201,
            json={"token": "ghs_fresh_token", "expires_at": "2099-01-01T00:00:00Z"},
        ),
    )

    svc = _make_service()
    # Pre-populate cache with expired token
    svc._installation_tokens[TEST_INSTALLATION_ID] = ("ghs_old_token", time.time() - 100)

    token = await svc._get_installation_token(TEST_INSTALLATION_ID)
    assert token == "ghs_fresh_token"
    assert route.call_count == 1


@pytest.mark.anyio
@respx.mock
async def test_get_installation_token_401_raises():
    """401 from installation token endpoint raises GitHubAuthenticationError."""
    respx.post(
        f"https://api.github.com/app/installations/{TEST_INSTALLATION_ID}/access_tokens",
    ).mock(
        return_value=httpx.Response(401, json={"message": "Bad credentials"}),
    )

    svc = _make_service()
    with pytest.raises(GitHubAuthenticationError):
        await svc._get_installation_token(TEST_INSTALLATION_ID)


# =====================================================================
# Pull request retrieval
# =====================================================================


def _mock_installation_token():
    """Mock installation token endpoint for all tests needing auth."""
    respx.post(
        f"https://api.github.com/app/installations/{TEST_INSTALLATION_ID}/access_tokens",
    ).mock(
        return_value=httpx.Response(
            201, json={"token": "ghs_tok", "expires_at": "2099-01-01T00:00:00Z"},
        ),
    )


@pytest.mark.anyio
@respx.mock
async def test_get_pull_request():
    """GET /repos/{owner}/{repo}/pulls/{num} → PR data dict."""
    _mock_installation_token()
    pr_data = {
        "number": 7,
        "title": "Add feature X",
        "head": {"sha": "abc1234"},
        "base": {"sha": "def5678"},
    }
    respx.get("https://api.github.com/repos/octocat/hello/pulls/7").mock(
        return_value=httpx.Response(200, json=pr_data),
    )

    svc = _make_service()
    result = await svc.get_pull_request("octocat", "hello", 7, TEST_INSTALLATION_ID)
    assert result["number"] == 7
    assert result["title"] == "Add feature X"


@pytest.mark.anyio
@respx.mock
async def test_get_pull_request_404():
    """404 → GitHubNotFoundError."""
    _mock_installation_token()
    respx.get("https://api.github.com/repos/octocat/hello/pulls/999").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"}),
    )

    svc = _make_service()
    with pytest.raises(GitHubNotFoundError):
        await svc.get_pull_request("octocat", "hello", 999, TEST_INSTALLATION_ID)


# =====================================================================
# Diff retrieval
# =====================================================================


@pytest.mark.anyio
@respx.mock
async def test_get_pull_request_diff():
    """GET /repos/{owner}/{repo}/pulls/{num} with diff Accept header."""
    _mock_installation_token()
    diff_text = "diff --git a/file.py b/file.py\n+added line\n"
    respx.get("https://api.github.com/repos/octocat/hello/pulls/7").mock(
        return_value=httpx.Response(200, text=diff_text),
    )

    svc = _make_service()
    result = await svc.get_pull_request_diff("octocat", "hello", 7, TEST_INSTALLATION_ID)
    assert "added line" in result


# =====================================================================
# Check runs
# =====================================================================


@pytest.mark.anyio
@respx.mock
async def test_create_check_run():
    """POST /repos/{owner}/{repo}/check-runs → created check-run data."""
    _mock_installation_token()
    respx.post("https://api.github.com/repos/octocat/hello/check-runs").mock(
        return_value=httpx.Response(
            201,
            json={"id": 1001, "name": "AI-Merge-Check", "status": "in_progress"},
        ),
    )

    svc = _make_service()
    result = await svc.create_check_run(
        owner="octocat",
        repo="hello",
        installation_id=TEST_INSTALLATION_ID,
        name="AI-Merge-Check",
        head_sha="abc1234def5678",
    )
    assert result["id"] == 1001
    assert result["name"] == "AI-Merge-Check"


@pytest.mark.anyio
@respx.mock
async def test_create_check_run_422():
    """422 validation error → GitHubValidationError."""
    _mock_installation_token()
    respx.post("https://api.github.com/repos/octocat/hello/check-runs").mock(
        return_value=httpx.Response(
            422,
            json={"message": "Validation Failed", "errors": [{"code": "invalid"}]},
        ),
    )

    svc = _make_service()
    with pytest.raises(GitHubValidationError, match="validation failed"):
        await svc.create_check_run(
            owner="octocat",
            repo="hello",
            installation_id=TEST_INSTALLATION_ID,
            name="check",
            head_sha="abc",
        )


# =====================================================================
# Comment posting
# =====================================================================


@pytest.mark.anyio
@respx.mock
async def test_create_comment():
    """POST /repos/{owner}/{repo}/issues/{num}/comments → comment data."""
    _mock_installation_token()
    respx.post("https://api.github.com/repos/octocat/hello/issues/7/comments").mock(
        return_value=httpx.Response(
            201, json={"id": 2001, "body": "Review comment"},
        ),
    )

    svc = _make_service()
    result = await svc.create_comment(
        owner="octocat",
        repo="hello",
        installation_id=TEST_INSTALLATION_ID,
        pr_number=7,
        body="Review comment",
    )
    assert result["id"] == 2001


@pytest.mark.anyio
@respx.mock
async def test_create_comment_409():
    """409 conflict → GitHubConflictError."""
    _mock_installation_token()
    respx.post("https://api.github.com/repos/octocat/hello/issues/7/comments").mock(
        return_value=httpx.Response(409, json={"message": "Conflict"}),
    )

    svc = _make_service()
    with pytest.raises(GitHubConflictError):
        await svc.create_comment(
            owner="octocat",
            repo="hello",
            installation_id=TEST_INSTALLATION_ID,
            pr_number=7,
            body="text",
        )


# =====================================================================
# Retry policy on 502/503/504
# =====================================================================


@pytest.mark.anyio
@respx.mock
async def test_retry_on_502_then_success():
    """502 followed by 200 succeeds after retry."""
    _mock_installation_token()
    route = respx.get("https://api.github.com/repos/octocat/hello/pulls/1")
    route.side_effect = [
        httpx.Response(502, json={"message": "Bad Gateway"}),
        httpx.Response(200, json={"number": 1, "title": "OK"}),
    ]

    svc = _make_service()
    with patch("app.services.github.client.asyncio.sleep", return_value=None):
        result = await svc.get_pull_request("octocat", "hello", 1, TEST_INSTALLATION_ID)
    assert result["title"] == "OK"
    assert route.call_count == 2


@pytest.mark.anyio
@respx.mock
async def test_retry_on_503_then_success():
    """503 followed by 200 succeeds after retry."""
    _mock_installation_token()
    route = respx.get("https://api.github.com/repos/octocat/hello/pulls/2")
    route.side_effect = [
        httpx.Response(503, json={"message": "Service Unavailable"}),
        httpx.Response(200, json={"number": 2, "title": "Recovered"}),
    ]

    svc = _make_service()
    with patch("app.services.github.client.asyncio.sleep", return_value=None):
        result = await svc.get_pull_request("octocat", "hello", 2, TEST_INSTALLATION_ID)
    assert result["title"] == "Recovered"


@pytest.mark.anyio
@respx.mock
async def test_retry_on_504_exhausted():
    """504 on all attempts → GitHubServerError after retries exhausted."""
    _mock_installation_token()
    route = respx.get("https://api.github.com/repos/octocat/hello/pulls/3")
    # 4 attempts: initial + 3 retries
    route.side_effect = [
        httpx.Response(504, json={"message": "Gateway Timeout"}),
        httpx.Response(504, json={"message": "Gateway Timeout"}),
        httpx.Response(504, json={"message": "Gateway Timeout"}),
        httpx.Response(504, json={"message": "Gateway Timeout"}),
    ]

    svc = _make_service()
    with (
        patch("app.services.github.client.asyncio.sleep", return_value=None),
        # When all retries exhausted, the last response is returned (not raised)
        # because request_with_retry returns the response after max attempts.
        # The caller (get_pull_request) then maps it to an error.
        pytest.raises(GitHubServerError),
    ):
        await svc.get_pull_request("octocat", "hello", 3, TEST_INSTALLATION_ID)


# =====================================================================
# Rate limit handling (403 with rate-limit body, 429)
# =====================================================================


@pytest.mark.anyio
@respx.mock
async def test_rate_limit_403_with_rate_limit_body():
    """403 with 'rate limit' in body → GitHubRateLimitError."""
    _mock_installation_token()
    respx.get("https://api.github.com/repos/octocat/hello/pulls/5").mock(
        return_value=httpx.Response(
            403,
            json={"message": "API rate limit exceeded for installation."},
            headers={"X-RateLimit-Reset": "1700000000"},
        ),
    )

    svc = _make_service()
    with patch("app.services.github.client.asyncio.sleep", return_value=None):
        with pytest.raises(GitHubRateLimitError) as exc_info:
            await svc.get_pull_request("octocat", "hello", 5, TEST_INSTALLATION_ID)
        assert exc_info.value.reset_at == 1700000000
        assert exc_info.value.retryable is True


@pytest.mark.anyio
@respx.mock
async def test_rate_limit_429_is_retried():
    """429 is retried, then succeeds on second attempt."""
    _mock_installation_token()
    route = respx.get("https://api.github.com/repos/octocat/hello/pulls/6")
    route.side_effect = [
        httpx.Response(429, json={"message": "Too Many Requests"}),
        httpx.Response(200, json={"number": 6, "title": "After rate limit"}),
    ]

    svc = _make_service()
    with patch("app.services.github.client.asyncio.sleep", return_value=None):
        result = await svc.get_pull_request("octocat", "hello", 6, TEST_INSTALLATION_ID)
    assert result["title"] == "After rate limit"


# =====================================================================
# Error mapping: 401/404/409/422
# =====================================================================


@pytest.mark.anyio
@respx.mock
async def test_error_401_authentication():
    """401 → GitHubAuthenticationError."""
    _mock_installation_token()
    respx.get("https://api.github.com/repos/octocat/hello/pulls/10").mock(
        return_value=httpx.Response(401, json={"message": "Bad credentials"}),
    )

    svc = _make_service()
    with pytest.raises(GitHubAuthenticationError):
        await svc.get_pull_request("octocat", "hello", 10, TEST_INSTALLATION_ID)


@pytest.mark.anyio
@respx.mock
async def test_error_404_not_found():
    """404 → GitHubNotFoundError."""
    _mock_installation_token()
    respx.get("https://api.github.com/repos/octocat/hello/pulls/11").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"}),
    )

    svc = _make_service()
    with pytest.raises(GitHubNotFoundError):
        await svc.get_pull_request("octocat", "hello", 11, TEST_INSTALLATION_ID)


@pytest.mark.anyio
@respx.mock
async def test_error_409_conflict():
    """409 → GitHubConflictError."""
    _mock_installation_token()
    respx.post("https://api.github.com/repos/octocat/hello/check-runs").mock(
        return_value=httpx.Response(409, json={"message": "Conflict"}),
    )

    svc = _make_service()
    with pytest.raises(GitHubConflictError):
        await svc.create_check_run(
            owner="octocat",
            repo="hello",
            installation_id=TEST_INSTALLATION_ID,
            name="check",
            head_sha="abc",
        )


@pytest.mark.anyio
@respx.mock
async def test_error_422_validation():
    """422 → GitHubValidationError."""
    _mock_installation_token()
    respx.post("https://api.github.com/repos/octocat/hello/issues/7/comments").mock(
        return_value=httpx.Response(
            422, json={"message": "Validation Failed"},
        ),
    )

    svc = _make_service()
    with pytest.raises(GitHubValidationError, match="validation failed"):
        await svc.create_comment(
            owner="octocat",
            repo="hello",
            installation_id=TEST_INSTALLATION_ID,
            pr_number=7,
            body="x",
        )


# =====================================================================
# Network error during API call
# =====================================================================


@pytest.mark.anyio
@respx.mock
async def test_network_error_during_pr_fetch():
    """Network error during PR fetch → GitHubNetworkError after retries."""
    _mock_installation_token()
    respx.get("https://api.github.com/repos/octocat/hello/pulls/20").mock(
        side_effect=httpx.ConnectError("Connection refused"),
    )

    svc = _make_service()
    with (
        patch("app.services.github.client.asyncio.sleep", return_value=None),
        pytest.raises(GitHubNetworkError, match="Network error"),
    ):
        await svc.get_pull_request("octocat", "hello", 20, TEST_INSTALLATION_ID)


# =====================================================================
# Update check run
# =====================================================================


@pytest.mark.anyio
@respx.mock
async def test_update_check_run():
    """PATCH /repos/{owner}/{repo}/check-runs/{id} → updated data."""
    _mock_installation_token()
    respx.patch("https://api.github.com/repos/octocat/hello/check-runs/1001").mock(
        return_value=httpx.Response(
            200,
            json={"id": 1001, "status": "completed", "conclusion": "success"},
        ),
    )

    svc = _make_service()
    result = await svc.update_check_run(
        owner="octocat",
        repo="hello",
        installation_id=TEST_INSTALLATION_ID,
        check_run_id=1001,
        status="completed",
        conclusion="success",
    )
    assert result["conclusion"] == "success"


# =====================================================================
# get_repo_info / get_branch_sha / get_pr_head_sha (sync state helpers)
# =====================================================================


def test_get_repo_info_initializes_state():
    """get_repo_info creates and caches RepoAccessInfo."""
    svc = _make_service()
    info = svc.get_repo_info("owner", "repo", "aaa", "bbb")
    assert info.source_sha == "aaa"
    assert info.target_sha == "bbb"

    # Same call returns cached
    info2 = svc.get_repo_info("owner", "repo", "ccc", "ddd")
    assert info2.source_sha == "aaa"  # First call values preserved


@pytest.mark.anyio
async def test_get_branch_sha_returns_correct_shas():
    """get_branch_sha returns correct sha for main vs feature branches."""
    svc = _make_service()
    svc.get_repo_info("o", "r", "src_sha", "tgt_sha")

    assert await svc.get_branch_sha("o", "r", "main") == "tgt_sha"
    assert await svc.get_branch_sha("o", "r", "feature") == "src_sha"
    assert await svc.get_branch_sha("o", "r2", "main") is None


@pytest.mark.anyio
async def test_get_pr_head_sha():
    """get_pr_head_sha returns pr_head_sha from repo state."""
    svc = _make_service()
    svc.get_repo_info("o", "r", "src", "tgt")

    assert await svc.get_pr_head_sha("o", "r", 1) == "src"
    assert await svc.get_pr_head_sha("o", "unknown", 1) is None
