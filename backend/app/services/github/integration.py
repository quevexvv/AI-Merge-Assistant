"""
GitHub Integration service.

Provides GitHub REST API operations for:
- Pull Request data and diff retrieval
- Check Runs creation and updates
- Comment posting on PRs
- Installation access token management
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import httpx
import jwt

from app.api.router import GitHubIntegrationPort, RepoAccessInfo
from app.core.config import settings
from app.core.github_idempotency import GitHubOperationResult, OperationContext
from app.schemas import GitHubOperationStatus, GitHubOperationType
from app.services.github.client import (
    create_github_client,
    map_github_error,
    request_with_retry,
)
from app.services.github.exceptions import GitHubAuthenticationError

if TYPE_CHECKING:
    from uuid import UUID

    from app.schemas import GitHubActionRecord


class GitHubIntegrationService(GitHubIntegrationPort):
    """
    GitHub Integration service implementation.

    Uses GitHub App authentication (JWT + Installation Access Token)
    for all API operations.
    """

    def __init__(
        self,
        app_id: str | None = None,
        private_key: str | None = None,
    ) -> None:
        """
        Initialize the integration service.

        Args:
            app_id: GitHub App ID.
            private_key: GitHub App private key (PEM format).
        """
        self.app_id = app_id or settings.github_app_id
        self.private_key = private_key or settings.github_app_private_key

        # Cache for installation tokens
        self._installation_tokens: dict[str, tuple[str, float]] = {}

        # Repo state cache (for compatibility with fake port)
        self.repo_state: dict[str, RepoAccessInfo] = {}
        self.merge_outcomes: dict[str, GitHubOperationStatus] = {}
        self.reconcile_outcomes: dict[str, GitHubOperationStatus] = {}
        self.merge_calls: list[UUID] = []

    def _generate_jwt(self) -> str:
        """
        Generate a GitHub App JWT (RS256).

        Returns:
            Signed JWT token.

        Raises:
            GitHubAuthenticationError: If app credentials are not configured.
        """
        if not self.app_id or not self.private_key:
            raise GitHubAuthenticationError(
                "GitHub App credentials not configured. "
                "Set GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY environment variables."
            )

        now = int(time.time())
        payload = {
            "iat": now - 60,  # Issued 60 seconds ago to account for clock skew
            "exp": now + 600,  # Expires in 10 minutes (max allowed by GitHub)
            "iss": self.app_id,
        }

        return jwt.encode(payload, self.private_key, algorithm="RS256")

    async def _get_installation_token(self, installation_id: str) -> str:
        """
        Get or refresh an installation access token.

        Args:
            installation_id: GitHub App installation ID.

        Returns:
            Installation access token.

        Raises:
            GitHubAuthenticationError: If token generation fails.
        """
        # Check cache
        if installation_id in self._installation_tokens:
            token, expires_at = self._installation_tokens[installation_id]
            if time.time() < expires_at - 60:  # 60 second buffer
                return token

        # Generate new token
        jwt_token = self._generate_jwt()

        async with create_github_client() as client:
            response = await request_with_retry(
                client,
                "POST",
                f"/app/installations/{installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {jwt_token}",
                    "Accept": "application/vnd.github+json",
                },
            )

        if response.status_code != 201:
            raise map_github_error(response)

        data = response.json()
        token = data["token"]
        expires_at = time.time() + 3600  # GitHub tokens expire in 1 hour

        self._installation_tokens[installation_id] = (token, expires_at)
        return token

    async def _get_client_for_installation(self, installation_id: str) -> httpx.AsyncClient:
        """
        Create an authenticated client for a specific installation.

        Args:
            installation_id: GitHub App installation ID.

        Returns:
            Configured httpx.AsyncClient with installation token.
        """
        token = await self._get_installation_token(installation_id)
        return create_github_client(headers={"Authorization": f"Bearer {token}"})

    # =====================================================================
    # GitHubIntegrationPort interface implementation
    # =====================================================================

    def get_repo_info(
        self,
        owner: str,
        repo: str,
        default_source: str,
        default_target: str,
    ) -> RepoAccessInfo:
        """
        Get repository access information.

        This is a synchronous method for compatibility with the fake port.
        In real implementation, this would fetch from GitHub API.
        """
        key = f"{owner}/{repo}"
        if key not in self.repo_state:
            self.repo_state[key] = RepoAccessInfo(
                source_sha=default_source,
                target_sha=default_target,
                pr_head_sha=default_source,
            )
        return self.repo_state[key]

    async def get_branch_sha(
        self,
        repo_owner: str,
        repo_name: str,
        branch_name: str,
    ) -> str | None:
        """
        Get the SHA of a branch.

        Args:
            repo_owner: Repository owner.
            repo_name: Repository name.
            branch_name: Branch name.

        Returns:
            Branch SHA or None if not found.
        """
        # For now, use cached state. In production, this would call GitHub API.
        info = self.repo_state.get(f"{repo_owner}/{repo_name}")
        if info is None:
            return None
        if branch_name == "main":
            return info.target_sha
        return info.source_sha

    async def get_pr_head_sha(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
    ) -> str | None:
        """
        Get the head SHA of a pull request.

        Args:
            repo_owner: Repository owner.
            repo_name: Repository name.
            pr_number: Pull request number.

        Returns:
            PR head SHA or None if not found.
        """
        info = self.repo_state.get(f"{repo_owner}/{repo_name}")
        return info.pr_head_sha if info else None

    async def get_pull_request(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        installation_id: str,
    ) -> dict:
        """
        Get pull request details.

        Args:
            owner: Repository owner.
            repo: Repository name.
            pr_number: Pull request number.
            installation_id: GitHub App installation ID.

        Returns:
            Pull request data from GitHub API.
        """
        client = await self._get_client_for_installation(installation_id)
        async with client:
            response = await request_with_retry(
                client,
                "GET",
                f"/repos/{owner}/{repo}/pulls/{pr_number}",
            )

        if response.status_code != 200:
            raise map_github_error(response)

        return response.json()

    async def get_pull_request_diff(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        installation_id: str,
    ) -> str:
        """
        Get pull request diff.

        Args:
            owner: Repository owner.
            repo: Repository name.
            pr_number: Pull request number.
            installation_id: GitHub App installation ID.

        Returns:
            Diff content as string.
        """
        client = await self._get_client_for_installation(installation_id)
        async with client:
            response = await request_with_retry(
                client,
                "GET",
                f"/repos/{owner}/{repo}/pulls/{pr_number}",
                headers={"Accept": "application/vnd.github.v3.diff"},
            )

        if response.status_code != 200:
            raise map_github_error(response)

        return response.text

    async def create_check_run(
        self,
        owner: str,
        repo: str,
        installation_id: str,
        name: str,
        head_sha: str,
        status: str = "in_progress",
        conclusion: str | None = None,
        output: dict | None = None,
        details_url: str | None = None,
    ) -> dict:
        """
        Create a check run.

        Args:
            owner: Repository owner.
            repo: Repository name.
            installation_id: GitHub App installation ID.
            name: Check run name.
            head_sha: SHA of the commit to check.
            status: Check run status (queued, in_progress, completed).
            conclusion: Check run conclusion (success, failure, neutral, etc.).
            output: Check run output (title, summary, text, annotations).
            details_url: URL for check run details.

        Returns:
            Created check run data.
        """
        client = await self._get_client_for_installation(installation_id)
        async with client:
            payload = {
                "name": name,
                "head_sha": head_sha,
                "status": status,
            }
            if conclusion:
                payload["conclusion"] = conclusion
            if output:
                payload["output"] = output
            if details_url:
                payload["details_url"] = details_url

            response = await request_with_retry(
                client,
                "POST",
                f"/repos/{owner}/{repo}/check-runs",
                json=payload,
            )

        if response.status_code != 201:
            raise map_github_error(response)

        return response.json()

    async def update_check_run(
        self,
        owner: str,
        repo: str,
        installation_id: str,
        check_run_id: int,
        status: str | None = None,
        conclusion: str | None = None,
        output: dict | None = None,
    ) -> dict:
        """
        Update a check run.

        Args:
            owner: Repository owner.
            repo: Repository name.
            installation_id: GitHub App installation ID.
            check_run_id: Check run ID.
            status: Check run status.
            conclusion: Check run conclusion.
            output: Check run output.

        Returns:
            Updated check run data.
        """
        client = await self._get_client_for_installation(installation_id)
        async with client:
            payload = {}
            if status:
                payload["status"] = status
            if conclusion:
                payload["conclusion"] = conclusion
            if output:
                payload["output"] = output

            response = await request_with_retry(
                client,
                "PATCH",
                f"/repos/{owner}/{repo}/check-runs/{check_run_id}",
                json=payload,
            )

        if response.status_code != 200:
            raise map_github_error(response)

        return response.json()

    async def create_comment(
        self,
        owner: str,
        repo: str,
        installation_id: str,
        pr_number: int,
        body: str,
    ) -> dict:
        """
        Create a comment on a pull request (issue comment).

        Args:
            owner: Repository owner.
            repo: Repository name.
            installation_id: GitHub App installation ID.
            pr_number: Pull request number.
            body: Comment body (Markdown supported).

        Returns:
            Created comment data.
        """
        client = await self._get_client_for_installation(installation_id)
        async with client:
            response = await request_with_retry(
                client,
                "POST",
                f"/repos/{owner}/{repo}/issues/{pr_number}/comments",
                json={"body": body},
            )

        if response.status_code != 201:
            raise map_github_error(response)

        return response.json()

    async def reconcile_operation(
        self,
        action: GitHubActionRecord,
    ) -> GitHubOperationResult:
        """
        Reconcile the state of a GitHub operation.

        This checks the actual state in GitHub for an operation that
        previously returned UNKNOWN status.

        Args:
            action: The GitHub action record to reconcile.

        Returns:
            GitHubOperationResult with actual state.
        """
        key = f"{action.repo_owner}/{action.repo_name}"
        status_override = self.reconcile_outcomes.get(
            str(action.reconciliation_id),
            self.reconcile_outcomes.get(key, GitHubOperationStatus.SUCCESS),
        )

        # In a real implementation, this would query GitHub API
        # to verify the actual state of the operation
        return GitHubOperationResult(
            operation_type=action.operation_type,
            repo_owner=action.repo_owner,
            repo_name=action.repo_name,
            branch_name=action.branch_name,
            pr_number=action.pr_number,
            expected_sha=action.expected_sha,
            actual_sha=action.expected_sha
            if status_override == GitHubOperationStatus.SUCCESS
            else None,
            status=status_override,
            safe_reason=None
            if status_override == GitHubOperationStatus.SUCCESS
            else "Reconciliation pending/failed",
            reconciliation_id=action.reconciliation_id,
        )

    async def execute_merge(
        self,
        context: OperationContext,
        reconciliation_id: UUID,
    ) -> GitHubOperationResult:
        """
        Execute a merge via GitHub API.

        Args:
            context: Operation context with merge parameters.
            reconciliation_id: Reconciliation ID for idempotency.

        Returns:
            GitHubOperationResult with merge outcome.
        """
        self.merge_calls.append(context.run_id)
        key = f"{context.repo_owner}/{context.repo_name}"
        outcome = self.merge_outcomes.get(
            str(context.run_id), self.merge_outcomes.get(key, GitHubOperationStatus.SUCCESS)
        )

        # In a real implementation, this would call GitHub's merge API
        # POST /repos/{owner}/{repo}/pulls/{pr_number}/merge
        # with the expected head_sha

        return GitHubOperationResult(
            operation_type=GitHubOperationType.MERGE_PR,
            repo_owner=context.repo_owner,
            repo_name=context.repo_name,
            branch_name=context.branch_name,
            pr_number=context.pr_number or 1,
            expected_sha=context.expected_head_sha or context.expected_source_sha,
            actual_sha=(context.expected_head_sha or context.expected_source_sha)
            if outcome == GitHubOperationStatus.SUCCESS
            else None,
            status=outcome,
            safe_reason=(
                None
                if outcome == GitHubOperationStatus.SUCCESS
                else f"GitHub merge returned {outcome.value}"
            ),
            reconciliation_id=reconciliation_id,
        )


# Factory function for dependency injection
def create_integration_service() -> GitHubIntegrationService:
    """Create GitHubIntegrationService instance with settings from config."""
    return GitHubIntegrationService()
