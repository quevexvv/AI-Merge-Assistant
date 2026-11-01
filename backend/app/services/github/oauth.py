"""
GitHub OAuth implementation.

Provides OAuth 2.0 flow for GitHub authentication:
- Exchange authorization code for access token
- Fetch user profile and emails
"""

from __future__ import annotations

from app.api.router import GitHubOAuthPort, OAuthUserIdentity
from app.core.config import settings
from app.services.github.client import (
    create_github_client,
    map_github_error,
    request_with_retry,
)
from app.services.github.exceptions import (
    GitHubAuthenticationError,
)


class GitHubOAuthService(GitHubOAuthPort):
    """
    GitHub OAuth 2.0 service implementation.

    Handles the OAuth flow:
    1. Exchange authorization code for access token (POST /login/oauth/access_token)
    2. Fetch user profile (GET /user)
    3. Optionally fetch user emails (GET /user/emails)
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
    ) -> None:
        """
        Initialize the OAuth service.

        Args:
            client_id: GitHub OAuth App client ID.
            client_secret: GitHub OAuth App client secret.
            redirect_uri: OAuth redirect URI.
        """
        self.client_id = client_id or settings.github_client_id
        self.client_secret = client_secret or settings.github_client_secret
        self.redirect_uri = redirect_uri or settings.github_redirect_uri

        # OAuth endpoints
        self.token_url = "https://github.com/login/oauth/access_token"
        self.api_base_url = "https://api.github.com"

    async def exchange_code(self, code: str) -> OAuthUserIdentity:
        """
        Exchange authorization code for access token and fetch user identity.

        Args:
            code: Authorization code received from GitHub OAuth callback.

        Returns:
            OAuthUserIdentity with user info and access token.

        Raises:
            GitHubAuthenticationError: If code exchange fails or token is invalid.
            GitHubRateLimitError: If GitHub rate limit is exceeded.
            GitHubClientError: For other GitHub API errors.
        """
        if not self.client_id or not self.client_secret:
            raise GitHubAuthenticationError(
                "GitHub OAuth credentials not configured. "
                "Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET environment variables."
            )

        # Exchange code for access token
        token_data = await self._exchange_code_for_token(code)

        access_token = token_data.get("access_token")
        if not access_token:
            desc = token_data.get("error_description", "Unknown error")
            raise GitHubAuthenticationError(
                f"Failed to obtain access token: {desc}"
            )

        # Fetch user profile
        user_data = await self._fetch_user(access_token)

        # Fetch user emails (optional)
        emails = await self._fetch_user_emails(access_token)
        primary_email = next(
            (e["email"] for e in emails if e.get("primary") and e.get("verified")),
            None,
        )

        # Fetch user installations (optional)
        installations = await self._fetch_user_installations(access_token)

        return OAuthUserIdentity(
            github_id=str(user_data["id"]),
            github_login=user_data["login"],
            display_name=user_data.get("name"),
            email=primary_email or user_data.get("email"),
            access_token=access_token,
            installations=installations,
        )

    async def _exchange_code_for_token(self, code: str) -> dict:
        """
        Exchange authorization code for access token.

        POST https://github.com/login/oauth/access_token
        """
        async with create_github_client(base_url="https://github.com") as client:
            response = await request_with_retry(
                client,
                "POST",
                "/login/oauth/access_token",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                },
                headers={"Accept": "application/json"},
            )

        if response.status_code != 200:
            error = map_github_error(response)
            if isinstance(error, GitHubAuthenticationError):
                # Check for specific OAuth errors
                try:
                    json_data = response.json()
                    if json_data.get("error") == "bad_verification_code":
                        raise GitHubAuthenticationError(
                            "Invalid or expired authorization code",
                            status_code=response.status_code,
                            response_body=response.text,
                        )
                except Exception:
                    pass
            raise error

        return response.json()

    async def _fetch_user(self, access_token: str) -> dict:
        """
        Fetch authenticated user profile.

        GET https://api.github.com/user
        """
        async with create_github_client() as client:
            response = await request_with_retry(
                client,
                "GET",
                "/user",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if response.status_code != 200:
            raise map_github_error(response)

        return response.json()

    async def _fetch_user_emails(self, access_token: str) -> list[dict]:
        """
        Fetch user emails.

        GET https://api.github.com/user/emails
        """
        async with create_github_client() as client:
            response = await request_with_retry(
                client,
                "GET",
                "/user/emails",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if response.status_code != 200:
            # Emails are optional, return empty list on failure
            return []

        return response.json()

    async def _fetch_user_installations(self, access_token: str) -> list[str]:
        """
        Fetch user's GitHub App installations.

        GET https://api.github.com/user/installations
        """
        async with create_github_client() as client:
            response = await request_with_retry(
                client,
                "GET",
                "/user/installations",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if response.status_code != 200:
            return []

        data = response.json()
        return [str(inst["id"]) for inst in data.get("installations", [])]


# Factory function for dependency injection
def create_oauth_service() -> GitHubOAuthService:
    """Create GitHubOAuthService instance with settings from config."""
    return GitHubOAuthService()
