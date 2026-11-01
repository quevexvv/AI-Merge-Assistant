"""
GitHub service exceptions.

Domain-specific exceptions for GitHub API interactions.
"""

from __future__ import annotations

from app.schemas import ErrorCategory


class GitHubServiceError(Exception):
    """Base exception for GitHub service errors."""

    def __init__(
        self,
        message: str,
        category: ErrorCategory,
        status_code: int | None = None,
        response_body: str | None = None,
        retryable: bool = False,
    ) -> None:
        self.category = category
        self.status_code = status_code
        self.response_body = response_body
        self.retryable = retryable
        super().__init__(message)


class GitHubAuthenticationError(GitHubServiceError):
    """Raised when GitHub authentication fails (401, 403)."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: str | None = None,
    ) -> None:
        super().__init__(
            message,
            category=ErrorCategory.RIGHTS,
            status_code=status_code,
            response_body=response_body,
            retryable=False,
        )


class GitHubRateLimitError(GitHubServiceError):
    """Raised when GitHub rate limit is exceeded (403 with rate limit headers)."""

    def __init__(
        self,
        message: str,
        reset_at: int | None = None,
        status_code: int | None = None,
        response_body: str | None = None,
    ) -> None:
        super().__init__(
            message,
            category=ErrorCategory.QUOTA,
            status_code=status_code,
            response_body=response_body,
            retryable=True,
        )
        self.reset_at = reset_at


class GitHubNotFoundError(GitHubServiceError):
    """Raised when a GitHub resource is not found (404)."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: str | None = None,
    ) -> None:
        super().__init__(
            message,
            category=ErrorCategory.RIGHTS,
            status_code=status_code,
            response_body=response_body,
            retryable=False,
        )


class GitHubConflictError(GitHubServiceError):
    """Raised when a GitHub operation conflicts (409)."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: str | None = None,
    ) -> None:
        super().__init__(
            message,
            category=ErrorCategory.STALE,
            status_code=status_code,
            response_body=response_body,
            retryable=False,
        )


class GitHubServerError(GitHubServiceError):
    """Raised when GitHub returns a server error (5xx)."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: str | None = None,
    ) -> None:
        super().__init__(
            message,
            category=ErrorCategory.ENVIRONMENT,
            status_code=status_code,
            response_body=response_body,
            retryable=True,
        )


class GitHubNetworkError(GitHubServiceError):
    """Raised when a network error occurs."""

    def __init__(
        self,
        message: str,
        original_error: Exception | None = None,
    ) -> None:
        super().__init__(
            message,
            category=ErrorCategory.ENVIRONMENT,
            status_code=None,
            response_body=None,
            retryable=True,
        )
        self.original_error = original_error


class GitHubValidationError(GitHubServiceError):
    """Raised when GitHub returns a validation error (422)."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: str | None = None,
    ) -> None:
        super().__init__(
            message,
            category=ErrorCategory.MODEL,
            status_code=status_code,
            response_body=response_body,
            retryable=False,
        )


# Backward-compatible alias used by client.py for generic/unmapped errors.
GitHubClientError = GitHubServiceError

