"""
GitHub HTTP client with retry logic and error mapping.

Provides a configured httpx.AsyncClient for GitHub API interactions
with proper timeouts, headers, retry policies, and error handling.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import httpx
from httpx import Response

from app.core.config import settings
from app.schemas import ErrorCategory
from app.services.github.exceptions import (
    GitHubAuthenticationError,
    GitHubClientError,
    GitHubConflictError,
    GitHubNetworkError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubServerError,
    GitHubValidationError,
)

logger = logging.getLogger(__name__)

# Default timeouts for GitHub API calls
DEFAULT_TIMEOUT = httpx.Timeout(
    connect=10.0,
    read=30.0,
    write=10.0,
    pool=5.0,
)

# Retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF_FACTOR = 0.5  # seconds
RETRY_STATUSES = {403, 429, 502, 503, 504}


def map_github_error(response: Response) -> GitHubClientError:
    """
    Map GitHub API HTTP response to appropriate domain exception.

    Args:
        response: The httpx Response object from GitHub API.

    Returns:
        Appropriate GitHubClientError subclass instance.
    """
    status = response.status_code
    body = response.text

    # Try to parse error message from JSON response
    error_message = f"GitHub API error: {status}"
    try:
        json_data = response.json()
        if isinstance(json_data, dict):
            error_message = json_data.get("message", error_message)
            # Check for specific OAuth errors
            if "error" in json_data:
                desc = json_data.get("error_description", error_message)
                error_message = f"{json_data['error']}: {desc}"
    except Exception:
        pass

    # Rate limit detection
    if status == 403 and "rate limit" in body.lower():
        reset_at = None
        reset_header = response.headers.get("X-RateLimit-Reset")
        if reset_header:
            with contextlib.suppress(ValueError):
                reset_at = int(reset_header)
        return GitHubRateLimitError(
            f"GitHub rate limit exceeded: {error_message}",
            reset_at=reset_at,
            status_code=status,
            response_body=body,
        )

    # Map status codes to exceptions
    if status == 401:
        return GitHubAuthenticationError(
            f"GitHub authentication failed: {error_message}",
            status_code=status,
            response_body=body,
        )
    if status == 403:
        return GitHubAuthenticationError(
            f"GitHub access forbidden: {error_message}",
            status_code=status,
            response_body=body,
        )
    if status == 404:
        return GitHubNotFoundError(
            f"GitHub resource not found: {error_message}",
            status_code=status,
            response_body=body,
        )
    if status == 409:
        return GitHubConflictError(
            f"GitHub conflict: {error_message}",
            status_code=status,
            response_body=body,
        )
    if status == 422:
        return GitHubValidationError(
            f"GitHub validation failed: {error_message}",
            status_code=status,
            response_body=body,
        )
    if 500 <= status < 600:
        return GitHubServerError(
            f"GitHub server error: {error_message}",
            status_code=status,
            response_body=body,
        )

    # Default for other status codes
    return GitHubClientError(
        f"GitHub API error ({status}): {error_message}",
        category=ErrorCategory.ENVIRONMENT,
        status_code=status,
        response_body=body,
        retryable=status in RETRY_STATUSES,
    )


def create_github_client(
    base_url: str = "https://api.github.com",
    timeout: httpx.Timeout | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    """
    Create a configured httpx.AsyncClient for GitHub API.

    Args:
        base_url: Base URL for GitHub API (default: https://api.github.com).
        timeout: Custom timeout configuration.
        headers: Additional headers to include.

    Returns:
        Configured httpx.AsyncClient instance.
    """
    default_headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"{settings.app_name}/1.0",
    }
    if headers:
        default_headers.update(headers)

    return httpx.AsyncClient(
        base_url=base_url,
        timeout=timeout or DEFAULT_TIMEOUT,
        headers=default_headers,
        follow_redirects=True,
    )


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_retries: int = MAX_RETRIES,
    backoff_factor: float = RETRY_BACKOFF_FACTOR,
    **kwargs,
) -> Response:
    """
    Execute an HTTP request with retry logic for transient failures.

    Args:
        client: The httpx.AsyncClient to use.
        method: HTTP method (GET, POST, etc.).
        url: Request URL.
        max_retries: Maximum number of retry attempts.
        backoff_factor: Exponential backoff factor in seconds.
        **kwargs: Additional arguments passed to client.request().

    Returns:
        The successful httpx.Response.

    Raises:
        GitHubClientError: If all retries are exhausted or a non-retryable error occurs.
    """
    last_error: GitHubClientError | None = None

    for attempt in range(max_retries + 1):
        try:
            response = await client.request(method, url, **kwargs)

            # Check for retryable status codes
            if response.status_code in RETRY_STATUSES and attempt < max_retries:
                error = map_github_error(response)
                if error.retryable:
                    wait_time = backoff_factor * (2**attempt)
                    logger.warning(
                        "GitHub API request failed with retryable status %d, "
                        "retrying in %.1fs (attempt %d/%d)",
                        response.status_code,
                        wait_time,
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(wait_time)
                    continue

            # Non-retryable or successful - return response
            return response

        except httpx.TimeoutException as e:
            last_error = GitHubNetworkError(f"Request timeout: {e}", original_error=e)
            if attempt < max_retries:
                wait_time = backoff_factor * (2**attempt)
                logger.warning(
                    "GitHub API request timeout, retrying in %.1fs (attempt %d/%d)",
                    wait_time,
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(wait_time)
                continue
            raise last_error from e

        except httpx.NetworkError as e:
            last_error = GitHubNetworkError(f"Network error: {e}", original_error=e)
            if attempt < max_retries:
                wait_time = backoff_factor * (2**attempt)
                logger.warning(
                    "GitHub API network error, retrying in %.1fs (attempt %d/%d)",
                    wait_time,
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(wait_time)
                continue
            raise last_error from e

        except httpx.HTTPError as e:
            last_error = GitHubNetworkError(f"HTTP error: {e}", original_error=e)
            if attempt < max_retries:
                wait_time = backoff_factor * (2**attempt)
                logger.warning(
                    "GitHub API HTTP error, retrying in %.1fs (attempt %d/%d)",
                    wait_time,
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(wait_time)
                continue
            raise last_error from e

    # All retries exhausted
    if last_error:
        raise last_error
    raise GitHubClientError(
        "Max retries exceeded",
        category=ErrorCategory.ENVIRONMENT,
        retryable=False,
    )
