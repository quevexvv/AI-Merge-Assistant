"""
GitHub services package.

Provides real implementations of GitHub OAuth and Integration ports
using httpx for HTTP communication with GitHub API.
"""

from __future__ import annotations

from app.services.github.client import (
    DEFAULT_TIMEOUT,
    MAX_RETRIES,
    RETRY_BACKOFF_FACTOR,
    RETRY_STATUSES,
    create_github_client,
    map_github_error,
    request_with_retry,
)
from app.services.github.exceptions import (
    GitHubAuthenticationError,
    GitHubClientError,
    GitHubConflictError,
    GitHubNetworkError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubServerError,
    GitHubServiceError,
    GitHubValidationError,
)
from app.services.github.integration import (
    GitHubIntegrationService,
    create_integration_service,
)
from app.services.github.oauth import (
    GitHubOAuthService,
    create_oauth_service,
)

__all__ = [
    "DEFAULT_TIMEOUT",
    "MAX_RETRIES",
    "RETRY_BACKOFF_FACTOR",
    "RETRY_STATUSES",
    "GitHubAuthenticationError",
    "GitHubClientError",
    "GitHubConflictError",
    "GitHubIntegrationService",
    "GitHubNetworkError",
    "GitHubNotFoundError",
    "GitHubOAuthService",
    "GitHubRateLimitError",
    "GitHubServerError",
    "GitHubServiceError",
    "GitHubValidationError",
    "create_github_client",
    "create_integration_service",
    "create_oauth_service",
    "map_github_error",
    "request_with_retry",
]
