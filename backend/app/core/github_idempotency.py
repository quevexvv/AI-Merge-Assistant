"""
GitHubIdempotencyManager — управление идемпотентными операциями с GitHub.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.schemas import (
    ErrorCategory,
    GitHubActionRecord,
    GitHubOperationResult,
    GitHubOperationStatus,
    GitHubOperationType,
)

logger = logging.getLogger(__name__)


class GitHubIdempotencyError(Exception):
    """Ошибка менеджера идемпотентности GitHub операций."""

    def __init__(self, category: ErrorCategory, message: str, status_code: int = 400):
        self.category = category
        self.status_code = status_code
        super().__init__(f"[{category.value}] {message}")


@dataclass(frozen=True, slots=True)
class IdempotencyCheckResult:
    """Результат проверки идемпотентности перед выполнением операции."""

    can_proceed: bool
    reason: str | None = None
    existing_action: GitHubActionRecord | None = None
    requires_reconciliation: bool = False
    reconciliation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class OperationContext:
    """Полный контекст для выполнения и последующей сверки (reconciliation) GitHub операции."""

    operation_type: GitHubOperationType
    owner_user_id: UUID
    owner_github_login: str
    run_id: UUID
    attempt_id: UUID
    repo_owner: str
    repo_name: str
    branch_name: str
    expected_source_sha: str
    expected_target_sha: str
    expected_head_sha: str | None = None
    pr_number: int | None = None
    target_branch: str = "main"
    idempotency_key: str | None = None


class GitHubIdempotencyManager:
    """
    Менеджер идемпотентности для GitHub write-операций.
    Сохраняет полный контекст операции (ветки, source/target/head SHA) для точной сверки.
    """

    def __init__(
        self,
        action_repository: GitHubActionRepository,
        github_client: GitHubClient,
    ):
        self._actions = action_repository
        self._github = github_client
        self._contexts_by_reconciliation: dict[UUID, OperationContext] = {}

    async def prepare_operation(
        self,
        context: OperationContext,
    ) -> IdempotencyCheckResult:
        idempotency_key = context.idempotency_key or self._generate_idempotency_key(context)

        existing = await self._actions.find_by_idempotency_key(idempotency_key)
        if existing:
            self._contexts_by_reconciliation.setdefault(existing.reconciliation_id, context)
            return await self._handle_existing_action(existing, context)

        stale_check = await self._check_snapshot_freshness(context)
        if not stale_check.can_proceed:
            return stale_check

        reconciliation_id = uuid4()
        action = GitHubActionRecord(
            id=uuid4(),
            user_id=context.owner_user_id,
            run_id=context.run_id,
            operation_type=context.operation_type,
            repo_owner=context.repo_owner,
            repo_name=context.repo_name,
            branch_name=context.branch_name,
            pr_number=context.pr_number,
            expected_sha=context.expected_head_sha or context.expected_source_sha,
            actual_sha=None,
            status=GitHubOperationStatus.UNKNOWN,
            safe_reason=None,
            reconciliation_id=reconciliation_id,
            initiator_user_id=context.owner_user_id,
            created_at=datetime.now(UTC).isoformat(),
            updated_at=datetime.now(UTC).isoformat(),
        )

        self._contexts_by_reconciliation[reconciliation_id] = context
        if hasattr(self._actions, "save_context"):
            await self._actions.save_context(reconciliation_id, context, idempotency_key)
        await self._actions.create(action)

        return IdempotencyCheckResult(
            can_proceed=True,
            existing_action=action,
            requires_reconciliation=False,
            reconciliation_id=action.reconciliation_id,
        )

    async def complete_operation(
        self,
        reconciliation_id: UUID,
        actual_sha: str,
        status: GitHubOperationStatus,
        safe_reason: str | None = None,
    ) -> GitHubActionRecord:
        action = await self._actions.find_by_reconciliation_id(reconciliation_id)
        if not action:
            raise GitHubIdempotencyError(
                ErrorCategory.MODEL,
                f"Action record not found for reconciliation_id: {reconciliation_id}",
                status_code=404,
            )

        updated = GitHubActionRecord(
            id=action.id,
            user_id=action.user_id,
            run_id=action.run_id,
            operation_type=action.operation_type,
            repo_owner=action.repo_owner,
            repo_name=action.repo_name,
            branch_name=action.branch_name,
            pr_number=action.pr_number,
            expected_sha=action.expected_sha,
            actual_sha=actual_sha,
            status=status,
            safe_reason=safe_reason,
            reconciliation_id=action.reconciliation_id,
            initiator_user_id=action.initiator_user_id,
            created_at=action.created_at,
            updated_at=datetime.now(UTC).isoformat(),
        )

        await self._actions.update(updated)
        return updated

    async def reconcile_unknown(
        self,
        reconciliation_id: UUID,
    ) -> GitHubOperationResult:
        """
        Сверка состояния для операции со статусом unknown (правило 10).
        Сначала восстанавливает полный контекст операции и сверяет фактический исход
        в GitHub, не теряя expected_target_sha / expected_head_sha.
        """
        action = await self._actions.find_by_reconciliation_id(reconciliation_id)
        if not action:
            raise GitHubIdempotencyError(
                ErrorCategory.MODEL,
                f"Action record not found for reconciliation_id: {reconciliation_id}",
                status_code=404,
            )

        if action.status != GitHubOperationStatus.UNKNOWN:
            return GitHubOperationResult(
                operation_type=action.operation_type,
                repo_owner=action.repo_owner,
                repo_name=action.repo_name,
                branch_name=action.branch_name,
                pr_number=action.pr_number,
                expected_sha=action.expected_sha,
                actual_sha=action.actual_sha,
                status=action.status,
                safe_reason=action.safe_reason,
                reconciliation_id=action.reconciliation_id,
            )

        context = self._contexts_by_reconciliation.get(reconciliation_id)
        if context is None and hasattr(self._actions, "get_context"):
            context = await self._actions.get_context(reconciliation_id)

        # 1. Сначала сверяем фактический исход в GitHub (на случай,
        #    если мерж/пуш уже прошёл до таймаута)
        result = await self._github.reconcile_operation(action)
        if result.status == GitHubOperationStatus.SUCCESS:
            await self.complete_operation(
                reconciliation_id=reconciliation_id,
                actual_sha=result.actual_sha or action.expected_sha or "",
                status=GitHubOperationStatus.SUCCESS,
                safe_reason=result.safe_reason,
            )
            return result

        # 2. Если операция ещё не выполнена, проверяем актуальность снимка по полному контексту
        if context is not None:
            stale_check = await self._check_snapshot_freshness(context)
            if not stale_check.can_proceed:
                await self.complete_operation(
                    reconciliation_id=reconciliation_id,
                    actual_sha=action.expected_sha or "",
                    status=GitHubOperationStatus.CONFLICT,
                    safe_reason=stale_check.reason
                    or "Snapshot became stale (source/target SHA changed)",
                )
                raise GitHubIdempotencyError(
                    ErrorCategory.STALE,
                    stale_check.reason or "Snapshot became stale",
                    status_code=409,
                )

        # 3. Записываем результат сверки
        await self.complete_operation(
            reconciliation_id=reconciliation_id,
            actual_sha=result.actual_sha or action.expected_sha or "",
            status=result.status,
            safe_reason=result.safe_reason,
        )
        if result.status == GitHubOperationStatus.CONFLICT:
            raise GitHubIdempotencyError(
                ErrorCategory.STALE,
                result.safe_reason or "Reconciliation detected conflict",
                status_code=409,
            )
        return result

    async def get_action_status(
        self,
        reconciliation_id: UUID,
    ) -> GitHubOperationResult | None:
        action = await self._actions.find_by_reconciliation_id(reconciliation_id)
        if not action:
            return None

        return GitHubOperationResult(
            operation_type=action.operation_type,
            repo_owner=action.repo_owner,
            repo_name=action.repo_name,
            branch_name=action.branch_name,
            pr_number=action.pr_number,
            expected_sha=action.expected_sha,
            actual_sha=action.actual_sha,
            status=action.status,
            safe_reason=action.safe_reason,
            reconciliation_id=action.reconciliation_id,
        )

    async def _handle_existing_action(
        self,
        existing: GitHubActionRecord,
        context: OperationContext,
    ) -> IdempotencyCheckResult:
        if existing.status == GitHubOperationStatus.SUCCESS:
            return IdempotencyCheckResult(
                can_proceed=False,
                reason=f"Operation already completed successfully: {existing.operation_type.value}",
                existing_action=existing,
                requires_reconciliation=False,
            )

        if existing.status in (GitHubOperationStatus.CONFLICT, GitHubOperationStatus.FORBIDDEN):
            return IdempotencyCheckResult(
                can_proceed=False,
                reason=f"Operation previously failed with {existing.status.value}: "
                f"{existing.safe_reason}",
                existing_action=existing,
                requires_reconciliation=False,
            )

        if existing.status == GitHubOperationStatus.UNKNOWN:
            stale_check = await self._check_snapshot_freshness(context)
            if not stale_check.can_proceed:
                await self.complete_operation(
                    reconciliation_id=existing.reconciliation_id,
                    actual_sha=existing.expected_sha or "",
                    status=GitHubOperationStatus.CONFLICT,
                    safe_reason=stale_check.reason or "Snapshot became stale during reconciliation",
                )
                return IdempotencyCheckResult(
                    can_proceed=False,
                    reason=stale_check.reason or "Snapshot became stale",
                    existing_action=existing,
                    requires_reconciliation=False,
                )

            return IdempotencyCheckResult(
                can_proceed=False,
                reason="Previous operation status unknown — reconciliation required",
                existing_action=existing,
                requires_reconciliation=True,
                reconciliation_id=existing.reconciliation_id,
            )

        return IdempotencyCheckResult(
            can_proceed=False,
            reason=f"Unexpected action status: {existing.status.value}",
            existing_action=existing,
            requires_reconciliation=False,
        )

    async def _check_snapshot_freshness(
        self,
        context: OperationContext,
    ) -> IdempotencyCheckResult:
        if context.operation_type in (
            GitHubOperationType.PUSH_TESTS,
            GitHubOperationType.CREATE_PR,
        ):
            actual_source_sha = await self._github.get_branch_sha(
                context.repo_owner,
                context.repo_name,
                context.branch_name
                if context.operation_type == GitHubOperationType.PUSH_TESTS
                else f"ai-tests/{context.run_id}",
            )
            if actual_source_sha and actual_source_sha != context.expected_source_sha:
                return IdempotencyCheckResult(
                    can_proceed=False,
                    reason=(
                        f"Source SHA changed: expected {context.expected_source_sha}, "
                        f"got {actual_source_sha}. Snapshot is stale."
                    ),
                )

        if context.operation_type in (
            GitHubOperationType.CREATE_PR,
            GitHubOperationType.MERGE_PR,
        ):
            actual_target_sha = await self._github.get_branch_sha(
                context.repo_owner,
                context.repo_name,
                context.target_branch,
            )
            if actual_target_sha and actual_target_sha != context.expected_target_sha:
                return IdempotencyCheckResult(
                    can_proceed=False,
                    reason=(
                        f"Target SHA changed: expected {context.expected_target_sha}, "
                        f"got {actual_target_sha}. Snapshot is stale."
                    ),
                )

        if (
            context.operation_type == GitHubOperationType.MERGE_PR
            and context.pr_number
            and context.expected_head_sha
        ):
            pr_head_sha = await self._github.get_pr_head_sha(
                context.repo_owner,
                context.repo_name,
                context.pr_number,
            )
            if pr_head_sha and pr_head_sha != context.expected_head_sha:
                return IdempotencyCheckResult(
                    can_proceed=False,
                    reason=(
                        f"PR head SHA changed: expected {context.expected_head_sha}, "
                        f"got {pr_head_sha}. Snapshot is stale."
                    ),
                )

        return IdempotencyCheckResult(can_proceed=True)

    @staticmethod
    def _generate_idempotency_key(context: OperationContext) -> str:
        parts = [
            context.operation_type.value,
            str(context.owner_user_id),
            str(context.run_id),
            str(context.attempt_id),
            context.repo_owner,
            context.repo_name,
            context.branch_name,
            context.expected_source_sha,
            context.expected_target_sha,
        ]
        if context.pr_number:
            parts.append(str(context.pr_number))
        if context.expected_head_sha:
            parts.append(context.expected_head_sha)
        return "|".join(parts)


class GitHubActionRepository:
    """Абстрактный репозиторий для GitHubActionRecord."""

    async def create(self, action: GitHubActionRecord) -> None:
        raise NotImplementedError

    async def update(self, action: GitHubActionRecord) -> None:
        raise NotImplementedError

    async def find_by_idempotency_key(self, key: str) -> GitHubActionRecord | None:
        raise NotImplementedError

    async def find_by_reconciliation_id(self, reconciliation_id: UUID) -> GitHubActionRecord | None:
        raise NotImplementedError

    async def find_by_run_and_operation(
        self,
        run_id: UUID,
        operation_type: GitHubOperationType,
    ) -> list[GitHubActionRecord]:
        raise NotImplementedError

    async def save_context(
        self,
        reconciliation_id: UUID,
        context: OperationContext,
        idempotency_key: str,
    ) -> None:
        """Сохранить контекст операции для последующей сверки."""
        raise NotImplementedError

    async def get_context(self, reconciliation_id: UUID) -> OperationContext | None:
        """Восстановить контекст операции по reconciliation_id."""
        raise NotImplementedError


class GitHubClient:
    """Абстрактный клиент для read-only операций сверки с GitHub."""

    async def get_branch_sha(
        self,
        repo_owner: str,
        repo_name: str,
        branch_name: str,
    ) -> str | None:
        raise NotImplementedError

    async def get_pr_head_sha(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
    ) -> str | None:
        raise NotImplementedError

    async def reconcile_operation(
        self,
        action: GitHubActionRecord,
    ) -> GitHubOperationResult:
        raise NotImplementedError

    async def execute_merge(
        self,
        context: OperationContext,
        reconciliation_id: UUID,
    ) -> GitHubOperationResult:
        """Выполнить мерж PR через GitHub merge API с передачей ожидаемого head SHA."""
        raise NotImplementedError
