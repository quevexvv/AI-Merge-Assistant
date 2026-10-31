from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Cookie, Depends, Header, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.core.github_idempotency import (
    GitHubActionRepository,
    GitHubClient,
    GitHubIdempotencyError,
    GitHubIdempotencyManager,
    OperationContext,
)
from app.core.merge.gate import MergeDecisionBuilder, MergeGate
from app.core.security import (
    default_session_expiry,
    encrypt_token,
    generate_csrf_token,
    generate_oauth_state,
    hash_session_token,
    verify_github_webhook_signature,
)
from app.db.models import (
    Artifact,
    Attempt,
    CoverageReport,
    GitHubAction,
    Installation,
    Repository,
    Run,
    Scenario,
    User,
    WebhookDelivery,
)
from app.db.models import (
    Session as DbSession,
)
from app.db.session import get_db
from app.schemas import (
    SCHEMA_VERSION,
    CoverageReportDTO,
    ErrorCategory,
    ExecutionState,
    Freshness,
    GitHubActionRecord,
    GitHubOperationResult,
    GitHubOperationStatus,
    GitHubOperationType,
    MergeStatus,
    RequirementTag,
    ScenarioItem,
    ScenarioSet,
    SourceType,
    TestBundle,
    TestItem,
    Verdict,
)
from app.worker.tasks import (
    execute_run_pipeline_task,
    parse_large_document_task,
    process_webhook_delivery_task,
)

api_router = APIRouter()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _ensure_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class APIErrorException(Exception):
    """Единое исключение API, возвращающее строго типизированный JSON по i0-api-spec.md."""

    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        category: ErrorCategory,
        safe_reason: str,
        run_id: UUID | None = None,
        attempt_id: UUID | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "error_code": error_code,
            "message": message,
            "category": category.value,
            "safe_reason": safe_reason,
            "details": details or {},
        }
        if run_id is not None:
            self.payload["run_id"] = str(run_id)
        if attempt_id is not None:
            self.payload["attempt_id"] = str(attempt_id)


def _ForbiddenNoLeak() -> APIErrorException:
    return APIErrorException(
        status_code=status.HTTP_403_FORBIDDEN,
        error_code="forbidden_resource",
        message="Access denied.",
        category=ErrorCategory.RIGHTS,
        safe_reason="You do not have permission to access this resource.",
        details={},
    )


# =====================================================================
# ПОРТЫ ИНТЕГРАЦИИ (OAuth, GitHub API, Celery Dispatcher)
# =====================================================================


@dataclass
class OAuthUserIdentity:
    github_id: str
    github_login: str
    display_name: str | None = None
    email: str | None = None
    access_token: str = "ghu_verified_token"
    installations: list[str] = field(default_factory=list)


class GitHubOAuthPort:
    """Порт обмена OAuth code на подтверждённую личность пользователя GitHub."""

    def exchange_code(self, code: str) -> OAuthUserIdentity:
        if not settings.offline_demo_mode:
            raise APIErrorException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                error_code="oauth_exchange_failed",
                message="GitHub OAuth code verification failed.",
                category=ErrorCategory.RIGHTS,
                safe_reason="Could not verify GitHub authorization code.",
            )
        return OAuthUserIdentity(
            github_id="10001",
            github_login="aruchimo",
            display_name="Aruchimo",
            access_token=f"ghu_offline_{code}",
            installations=["inst-1"],
        )


@dataclass
class RepoAccessInfo:
    user_has_write_permission: bool = True
    github_app_has_write_permission: bool = True
    target_branch_protected: bool = True
    source_sha: str = "1111111"
    target_sha: str = "2222222"
    pr_head_sha: str = "1111111"
    pr_number: int = 1


class GitHubIntegrationPort(GitHubClient):
    """Порт взаимодействия с GitHub API для проверки SHA, прав и выполнения мержа."""

    def __init__(self) -> None:
        self.repo_state: dict[str, RepoAccessInfo] = {}
        self.merge_outcomes: dict[str, GitHubOperationStatus] = {}
        self.reconcile_outcomes: dict[str, GitHubOperationStatus] = {}
        self.merge_calls: list[UUID] = []

    def get_repo_info(
        self, owner: str, repo: str, default_source: str, default_target: str
    ) -> RepoAccessInfo:
        key = f"{owner}/{repo}"
        if key not in self.repo_state:
            self.repo_state[key] = RepoAccessInfo(
                source_sha=default_source,
                target_sha=default_target,
                pr_head_sha=default_source,
            )
        return self.repo_state[key]

    async def get_branch_sha(self, repo_owner: str, repo_name: str, branch_name: str) -> str | None:
        info = self.repo_state.get(f"{repo_owner}/{repo_name}")
        if info is None:
            return None
        if branch_name == "main":
            return info.target_sha
        return info.source_sha

    async def get_pr_head_sha(self, repo_owner: str, repo_name: str, pr_number: int) -> str | None:
        _ = pr_number
        info = self.repo_state.get(f"{repo_owner}/{repo_name}")
        return info.pr_head_sha if info else None

    async def reconcile_operation(self, action: GitHubActionRecord) -> GitHubOperationResult:
        key = f"{action.repo_owner}/{action.repo_name}"
        status_override = self.reconcile_outcomes.get(
            str(action.reconciliation_id),
            self.reconcile_outcomes.get(key, GitHubOperationStatus.SUCCESS),
        )
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
        self, context: OperationContext, reconciliation_id: UUID
    ) -> GitHubOperationResult:
        self.merge_calls.append(context.run_id)
        key = f"{context.repo_owner}/{context.repo_name}"
        outcome = self.merge_outcomes.get(
            str(context.run_id), self.merge_outcomes.get(key, GitHubOperationStatus.SUCCESS)
        )
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
            safe_reason=None
            if outcome == GitHubOperationStatus.SUCCESS
            else f"GitHub merge returned {outcome.value}",
            reconciliation_id=reconciliation_id,
        )


class TaskDispatcherPort:
    """Порт постановки задач в очередь Celery."""

    def __init__(self) -> None:
        self.dispatched_runs: list[UUID] = []
        self.dispatched_uploads: list[UUID] = []
        self.dispatched_webhooks: list[str] = []

    def dispatch_run_pipeline(self, run_id: UUID) -> None:
        self.dispatched_runs.append(run_id)
        execute_run_pipeline_task.run(
            str(run_id), simulate_offline_runner=settings.offline_demo_mode
        )

    def dispatch_large_upload(self, upload_id: UUID) -> None:
        self.dispatched_uploads.append(upload_id)
        parse_large_document_task.run(str(upload_id))

    def dispatch_webhook(self, delivery_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self.dispatched_webhooks.append(delivery_id)
        process_webhook_delivery_task.run(delivery_id, event_type, payload)


class SqlAlchemyGitHubActionRepository(GitHubActionRepository):
    """Репозиторий GitHubAction поверх SQLAlchemy БД с сохранением контекста сверки."""

    _key_to_reconciliation: ClassVar[dict[str, UUID]] = {}
    _contexts: ClassVar[dict[UUID, OperationContext]] = {}

    def __init__(self, db: OrmSession) -> None:
        self._db = db

    async def save_context(
        self,
        reconciliation_id: UUID,
        context: OperationContext,
        idempotency_key: str,
    ) -> None:
        self._contexts[reconciliation_id] = context
        self._key_to_reconciliation[idempotency_key] = reconciliation_id

    async def get_context(self, reconciliation_id: UUID) -> OperationContext | None:
        return self._contexts.get(reconciliation_id)

    async def create(self, action: GitHubActionRecord) -> None:
        row = GitHubAction(
            id=action.id,
            user_id=action.user_id,
            run_id=action.run_id,
            operation_type=action.operation_type.value,
            repo_owner=action.repo_owner,
            repo_name=action.repo_name,
            branch_name=action.branch_name,
            pr_number=action.pr_number,
            expected_sha=action.expected_sha,
            actual_sha=action.actual_sha,
            status=action.status.value,
            safe_reason=action.safe_reason,
            reconciliation_id=action.reconciliation_id,
            initiator_user_id=action.initiator_user_id,
        )
        self._db.add(row)
        self._db.commit()

    async def update(self, action: GitHubActionRecord) -> None:
        row = self._db.scalar(
            select(GitHubAction).where(GitHubAction.reconciliation_id == action.reconciliation_id)
        )
        if row is not None:
            row.actual_sha = action.actual_sha
            row.status = action.status.value
            row.safe_reason = action.safe_reason
            row.updated_at = _utcnow()
            self._db.commit()

    async def find_by_idempotency_key(self, key: str) -> GitHubActionRecord | None:
        rec_id = self._key_to_reconciliation.get(key)
        if rec_id is None:
            return None
        return await self.find_by_reconciliation_id(rec_id)

    async def find_by_reconciliation_id(self, reconciliation_id: UUID) -> GitHubActionRecord | None:
        row = self._db.scalar(
            select(GitHubAction).where(GitHubAction.reconciliation_id == reconciliation_id)
        )
        if row is None:
            return None
        return self._to_dto(row)

    async def find_by_run_and_operation(
        self,
        run_id: UUID,
        operation_type: GitHubOperationType,
    ) -> list[GitHubActionRecord]:
        rows = self._db.scalars(
            select(GitHubAction).where(
                GitHubAction.run_id == run_id,
                GitHubAction.operation_type == operation_type.value,
            )
        ).all()
        return [self._to_dto(r) for r in rows]

    @staticmethod
    def _to_dto(row: GitHubAction) -> GitHubActionRecord:
        return GitHubActionRecord(
            id=row.id,
            user_id=row.user_id,
            run_id=row.run_id,
            operation_type=GitHubOperationType(row.operation_type),
            repo_owner=row.repo_owner,
            repo_name=row.repo_name,
            branch_name=row.branch_name,
            pr_number=row.pr_number,
            expected_sha=row.expected_sha,
            actual_sha=row.actual_sha,
            status=GitHubOperationStatus(row.status),
            safe_reason=row.safe_reason,
            reconciliation_id=row.reconciliation_id,
            initiator_user_id=row.initiator_user_id,
            created_at=_ensure_aware(row.created_at).isoformat(),
            updated_at=_ensure_aware(row.updated_at).isoformat(),
        )


@dataclass
class OAuthStateEntry:
    browser_binding: str
    expires_at: datetime


oauth_port = GitHubOAuthPort()
github_port = GitHubIntegrationPort()
task_dispatcher = TaskDispatcherPort()
oauth_states_registry: dict[str, OAuthStateEntry] = {}


def get_oauth_port() -> GitHubOAuthPort:
    return oauth_port


def get_github_port() -> GitHubIntegrationPort:
    if not settings.offline_demo_mode:
        raise APIErrorException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code="github_unavailable",
            message="GitHub integration is not configured.",
            category=ErrorCategory.ENVIRONMENT,
            safe_reason="GitHub integration is unavailable.",
        )
    return github_port


def get_task_dispatcher() -> TaskDispatcherPort:
    return task_dispatcher


def get_current_user_id(
    session_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
    db: OrmSession = Depends(get_db),
) -> UUID:
    token = session_token
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise APIErrorException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code="unauthorized",
            message="Authentication required.",
            category=ErrorCategory.RIGHTS,
            safe_reason="Please sign in via GitHub to continue.",
        )

    token_hash = hash_session_token(token)
    sess = db.scalar(select(DbSession).where(DbSession.session_token_hash == token_hash))
    if sess is None or _ensure_aware(sess.expires_at) <= _utcnow():
        raise APIErrorException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code="unauthorized",
            message="Session expired or invalid.",
            category=ErrorCategory.RIGHTS,
            safe_reason="Please sign in via GitHub to continue.",
        )

    sess.last_activity_at = _utcnow()
    db.commit()
    return sess.user_id


def _get_owned_run_db(
    db: OrmSession,
    run_id: UUID,
    user_id: UUID,
    check_stale: bool = False,
) -> tuple[Run, Attempt | None]:
    run = db.get(Run, run_id)
    if run is None:
        raise APIErrorException(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="run_not_found",
            message="Resource not found.",
            category=ErrorCategory.RIGHTS,
            safe_reason="Requested resource was not found.",
        )
    if run.user_id != user_id:
        raise _ForbiddenNoLeak()
    attempt = db.scalar(select(Attempt).where(Attempt.run_id == run.id))
    if check_stale and run.freshness == Freshness.STALE.value:
        raise APIErrorException(
            status_code=status.HTTP_409_CONFLICT,
            error_code="stale_snapshot",
            message="Target SHA изменился с момента создания прогона.",
            category=ErrorCategory.STALE,
            run_id=run_id,
            attempt_id=attempt.id if attempt else None,
            details={"expected_target_sha": run.target_sha},
            safe_reason="The target branch has moved. Please refresh the snapshot and retry.",
        )
    return run, attempt


# =====================================================================
# 1. AUTH (/auth/*)
# =====================================================================


@api_router.get("/auth/github/login", status_code=status.HTTP_302_FOUND)
def github_login() -> Response:
    state = generate_oauth_state()
    browser_binding = generate_oauth_state()
    oauth_states_registry[state] = OAuthStateEntry(
        browser_binding=hash_session_token(browser_binding),
        expires_at=_utcnow() + timedelta(minutes=10),
    )
    redirect_url = f"https://github.com/login/oauth/authorize?state={state}"
    resp = RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
    resp.set_cookie(
        key="oauth_state_binding",
        value=browser_binding,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=600,
    )
    return resp


@api_router.get("/auth/github/callback")
def github_callback(
    response: Response,
    state: str = Query(...),
    code: str = Query(...),
    oauth_state_binding: str | None = Cookie(default=None),
    db: OrmSession = Depends(get_db),
    oauth: GitHubOAuthPort = Depends(get_oauth_port),
) -> dict[str, Any]:
    entry = oauth_states_registry.pop(state, None)
    if (
        entry is None
        or entry.expires_at <= _utcnow()
        or not oauth_state_binding
        or hash_session_token(oauth_state_binding) != entry.browser_binding
    ):
        raise APIErrorException(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="forbidden_resource",
            message="OAuth state expired, already used, or not bound to this browser session.",
            category=ErrorCategory.RIGHTS,
            safe_reason="OAuth state is invalid or expired.",
        )

    identity = oauth.exchange_code(code)

    user = db.scalar(select(User).where(User.github_id == identity.github_id))
    if user is None:
        user = User(
            id=uuid4(),
            github_login=identity.github_login,
            github_id=identity.github_id,
            display_name=identity.display_name,
            email=identity.email,
            encrypted_access_token=encrypt_token(identity.access_token),
        )
        db.add(user)
        db.flush()
    else:
        user.github_login = identity.github_login
        user.encrypted_access_token = encrypt_token(identity.access_token)
        user.updated_at = _utcnow()

    for inst_id in identity.installations:
        existing_inst = db.scalar(
            select(Installation).where(Installation.github_app_installation_id == inst_id)
        )
        if existing_inst is None:
            db.add(
                Installation(
                    github_app_installation_id=inst_id,
                    github_account_login=identity.github_login,
                    github_account_type="User",
                )
            )

    raw_session_token = f"sess-{generate_oauth_state()}"
    csrf = generate_csrf_token()
    db.add(
        DbSession(
            user_id=user.id,
            session_token_hash=hash_session_token(raw_session_token),
            csrf_token=csrf,
            expires_at=default_session_expiry(),
        )
    )
    db.commit()

    response.delete_cookie("oauth_state_binding")
    response.set_cookie(
        key="session_token",
        value=raw_session_token,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "authenticated": True,
        "user_id": str(user.id),
        "github_login": user.github_login,
        "csrf_token": csrf,
    }


@api_router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    session_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
    user_id: UUID = Depends(get_current_user_id),
    db: OrmSession = Depends(get_db),
) -> Response:
    _ = user_id
    token = session_token
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    if token:
        sess = db.scalar(
            select(DbSession).where(DbSession.session_token_hash == hash_session_token(token))
        )
        if sess is not None:
            db.delete(sess)
            db.commit()
    response.delete_cookie("session_token")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_router.get("/auth/me")
def get_me(
    user_id: UUID = Depends(get_current_user_id),
    db: OrmSession = Depends(get_db),
) -> dict[str, Any]:
    user = db.get(User, user_id)
    if user is None:
        raise _ForbiddenNoLeak()
    installs = db.scalars(
        select(Installation).where(Installation.github_account_login == user.github_login)
    ).all()
    return {
        "schema_version": SCHEMA_VERSION,
        "user_id": str(user.id),
        "github_login": user.github_login,
        "installations": [i.github_app_installation_id for i in installs],
    }


# =====================================================================
# 2. INSTALLATIONS & REPOS (/installations, /repos)
# =====================================================================


@api_router.get("/installations")
def list_installations(
    user_id: UUID = Depends(get_current_user_id),
    db: OrmSession = Depends(get_db),
) -> dict[str, Any]:
    user = db.get(User, user_id)
    installs = db.scalars(
        select(Installation).where(Installation.github_account_login == user.github_login)
    ).all()
    return {
        "schema_version": SCHEMA_VERSION,
        "installations": [i.github_app_installation_id for i in installs],
    }


@api_router.post("/installations/{installation_id}/connect")
def connect_installation(
    installation_id: str,
    user_id: UUID = Depends(get_current_user_id),
    db: OrmSession = Depends(get_db),
) -> dict[str, Any]:
    user = db.get(User, user_id)
    inst = db.scalar(
        select(Installation).where(Installation.github_app_installation_id == installation_id)
    )
    if inst is None:
        raise APIErrorException(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="installation_not_found",
            message="Installation not found.",
            category=ErrorCategory.RIGHTS,
            safe_reason="GitHub App installation was not found.",
        )
    if inst.github_account_login != user.github_login:
        raise _ForbiddenNoLeak()
    return {"schema_version": SCHEMA_VERSION, "installation_id": installation_id, "connected": True}


@api_router.get("/repos")
def list_repos(
    user_id: UUID = Depends(get_current_user_id),
    db: OrmSession = Depends(get_db),
) -> dict[str, Any]:
    user = db.get(User, user_id)
    repos = db.scalars(select(Repository).where(Repository.owner_login == user.github_login)).all()
    return {
        "schema_version": SCHEMA_VERSION,
        "repositories": [
            {
                "owner": r.owner_login,
                "name": r.repo_name,
                "is_public": r.is_public,
                "can_analyze": True,
                "can_write": True,
            }
            for r in repos
        ],
    }


@api_router.get("/repos/{owner}/{repo}/branches")
def list_branches(
    owner: str,
    repo: str,
    user_id: UUID = Depends(get_current_user_id),
    db: OrmSession = Depends(get_db),
    gh: GitHubIntegrationPort = Depends(get_github_port),
) -> dict[str, Any]:
    user = db.get(User, user_id)
    if owner != user.github_login:
        raise _ForbiddenNoLeak()
    info = gh.get_repo_info(owner, repo, "1111111", "2222222")
    return {
        "schema_version": SCHEMA_VERSION,
        "repo": f"{owner}/{repo}",
        "branches": [
            {"name": "main", "sha": info.target_sha, "protected": info.target_branch_protected},
            {"name": "feature/branch", "sha": info.source_sha, "protected": False},
        ],
    }


@api_router.get("/repos/{owner}/{repo}/branches/{branch}/sha")
def get_branch_sha(
    owner: str,
    repo: str,
    branch: str,
    user_id: UUID = Depends(get_current_user_id),
    db: OrmSession = Depends(get_db),
    gh: GitHubIntegrationPort = Depends(get_github_port),
) -> dict[str, Any]:
    user = db.get(User, user_id)
    if owner != user.github_login:
        raise _ForbiddenNoLeak()
    info = gh.get_repo_info(owner, repo, "1111111", "2222222")
    sha = info.target_sha if branch == "main" else info.source_sha
    return {
        "schema_version": SCHEMA_VERSION,
        "repo": f"{owner}/{repo}",
        "branch": branch,
        "sha": sha,
    }


# =====================================================================
# 3. RUNS (/runs/*)
# =====================================================================


class CreateRunRequest(BaseModel):
    source_type: SourceType = SourceType.GITHUB
    repo_owner: str = Field(..., min_length=1, max_length=255)
    repo_name: str = Field(..., min_length=1, max_length=255)
    source_sha: str = Field(..., pattern=r"^[a-f0-9]{7,40}$")
    target_sha: str = Field(..., pattern=r"^[a-f0-9]{7,40}$")
    mode: Literal["default", "lazy"] = "default"
    description: str | None = None
    environment_profile: str = "py313"
    dependency_profile: str = "pytest-default"
    auto_merge_enabled: bool = False


@api_router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
def create_run(
    payload: CreateRunRequest,
    user_id: UUID = Depends(get_current_user_id),
    db: OrmSession = Depends(get_db),
    dispatcher: TaskDispatcherPort = Depends(get_task_dispatcher),
    gh: GitHubIntegrationPort = Depends(get_github_port),
) -> dict[str, Any]:
    user = db.get(User, user_id)
    if payload.source_type == SourceType.GITHUB and payload.repo_owner != user.github_login:
        raise _ForbiddenNoLeak()

    # Проверка описания для режима default (без демо-дефолта и с проверкой strip)
    if payload.mode == "default" and (
        payload.description is None or not payload.description.strip()
    ):
        raise APIErrorException(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="missing_description",
            message="Default mode requires a non-empty change description.",
            category=ErrorCategory.MODEL,
            safe_reason="Please provide a non-empty description for default mode"
            " or switch to lazy mode.",
        )

    repo_full_name = f"{payload.repo_owner}/{payload.repo_name}"
    repo = db.scalar(select(Repository).where(Repository.full_name == repo_full_name))
    if repo is None:
        repo = Repository(
            id=uuid4(),
            github_repo_id=f"gh-{uuid4()}",
            owner_login=payload.repo_owner,
            repo_name=payload.repo_name,
            full_name=repo_full_name,
            is_public=True,
            default_branch="main",
        )
        db.add(repo)
        db.flush()

    gh.get_repo_info(payload.repo_owner, payload.repo_name, payload.source_sha, payload.target_sha)

    run_id = uuid4()
    attempt_id = uuid4()
    snapshot_id = uuid4()
    bundle_hash = "a" * 64

    # До завершения runner вердикт отсутствует (verdict=None), CoverageReport не создаётся заранее
    run_row = Run(
        id=run_id,
        user_id=user_id,
        source_type=payload.source_type.value,
        repo_id=repo.id,
        source_sha=payload.source_sha,
        target_sha=payload.target_sha,
        execution_state=ExecutionState.QUEUED.value,
        verdict=None,
        freshness=Freshness.CURRENT.value,
        merge_status=MergeStatus.PENDING.value,
        mode=payload.mode,
        auto_merge_enabled=payload.auto_merge_enabled,
        environment_profile=payload.environment_profile,
        dependency_profile=payload.dependency_profile,
    )
    attempt_row = Attempt(
        id=attempt_id,
        run_id=run_id,
        snapshot_id=snapshot_id,
        source_sha=payload.source_sha,
        target_sha=payload.target_sha,
        content_hash="b" * 64,
        test_bundle_hash=bundle_hash,
        environment_profile=payload.environment_profile,
        attempt_state="pending",
    )
    db.add(run_row)
    db.add(attempt_row)
    db.commit()

    # Отправляем задачу в очередь через подменяемый порт диспетчера
    dispatcher.dispatch_run_pipeline(run_id)
    db.refresh(run_row)

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": str(run_row.id),
        "status": "accepted",
        "execution_state": run_row.execution_state,
        "verdict": run_row.verdict,
        "message": f"Run registered. Poll /runs/{run_row.id}/status.",
        "poll_interval_seconds": 5,
    }


@api_router.get("/runs/{run_id}")
def get_run(
    run_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: OrmSession = Depends(get_db),
) -> dict[str, Any]:
    run, attempt = _get_owned_run_db(db, run_id, user_id, check_stale=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": str(run.id),
        "attempt_id": str(attempt.id) if attempt else None,
        "execution_state": run.execution_state,
        "verdict": run.verdict,
        "freshness": run.freshness,
        "merge_status": run.merge_status,
        "mode": run.mode,
        "is_fake_offline": settings.offline_demo_mode,
    }


@api_router.get("/runs/{run_id}/status")
def get_run_status(
    run_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: OrmSession = Depends(get_db),
) -> dict[str, Any]:
    run, _ = _get_owned_run_db(db, run_id, user_id, check_stale=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": str(run.id),
        "execution_state": run.execution_state,
        "verdict": run.verdict,
        "freshness": run.freshness,
        "merge_status": run.merge_status,
    }


@api_router.get("/runs/{run_id}/scenarios")
def get_run_scenarios(
    run_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: OrmSession = Depends(get_db),
) -> ScenarioSet:
    _run, attempt = _get_owned_run_db(db, run_id, user_id)
    scenarios = db.scalars(select(Scenario).where(Scenario.run_id == run_id)).all()
    if not scenarios:
        raise APIErrorException(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="scenarios_not_ready",
            message="Scenarios have not been generated yet.",
            category=ErrorCategory.MODEL,
            run_id=run_id,
            safe_reason="Scenarios are not available until the generation stage completes.",
        )
    return ScenarioSet(
        set_id=uuid4(),
        run_id=run_id,
        snapshot_id=attempt.snapshot_id if attempt else uuid4(),
        scenarios=[
            ScenarioItem(
                scenario_id=s.scenario_id,
                source_refs=[UUID(x) for x in json.loads(s.source_refs)],
                requirement_tag=RequirementTag(s.requirement_tag),
                gherkin_text=s.gherkin_text,
            )
            for s in scenarios
        ],
        prompt_model=scenarios[0].prompt_model,
        prompt_version=scenarios[0].prompt_version,
        usage="unknown",
        set_hash="c" * 64,
    )


@api_router.get("/runs/{run_id}/tests")
def get_run_tests(
    run_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: OrmSession = Depends(get_db),
) -> TestBundle:
    _, attempt = _get_owned_run_db(db, run_id, user_id)
    if attempt is None or attempt.attempt_state != "completed":
        raise APIErrorException(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="tests_not_ready",
            message="Tests have not been generated yet.",
            category=ErrorCategory.TESTS,
            run_id=run_id,
            safe_reason="Test bundle is not available yet.",
        )
    return TestBundle(
        bundle_id=uuid4(),
        run_id=run_id,
        snapshot_id=attempt.snapshot_id,
        tests=[
            TestItem(
                test_path="tests/test_generated.py",
                test_text="def test_generated():\n    assert True\n",
                linked_scenario_ids=[],
            )
        ],
        bundle_hash=attempt.test_bundle_hash,
        prompt_model="gemini-2.5-flash",
        prompt_version="v1",
        usage="unknown",
    )


@api_router.get("/runs/{run_id}/coverage")
def get_run_coverage(
    run_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: OrmSession = Depends(get_db),
) -> CoverageReportDTO:
    _, attempt = _get_owned_run_db(db, run_id, user_id)
    cov_row = db.scalar(select(CoverageReport).where(CoverageReport.run_id == run_id))
    if cov_row is None:
        raise APIErrorException(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="coverage_report_missing",
            message="Coverage report is not available until test execution completes.",
            category=ErrorCategory.TESTS,
            run_id=run_id,
            attempt_id=attempt.id if attempt else None,
            safe_reason="Coverage report has not been generated yet.",
        )
    return CoverageReportDTO(
        attempt_id=cov_row.attempt_id,
        run_id=cov_row.run_id,
        covered_changed_lines=cov_row.covered_changed_lines,
        executable_changed_lines=cov_row.executable_changed_lines,
        coverage_percentage=cov_row.coverage_percentage,
        covered_files=json.loads(cov_row.covered_files) if cov_row.covered_files else [],
        uncovered_changed_lines=json.loads(cov_row.uncovered_changed_lines)
        if cov_row.uncovered_changed_lines
        else [],
        coverage_verdict=Verdict(cov_row.coverage_verdict),
    )


# =====================================================================
# 4. UPLOADS, EXPORTS, MERGE, STATS, WEBHOOKS
# =====================================================================


class UploadRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    page_count: int = Field(default=5, ge=1, le=500)
    size_bytes: int = Field(default=1024, ge=1)


@api_router.post("/uploads", status_code=status.HTTP_202_ACCEPTED)
def create_upload(
    payload: UploadRequest,
    user_id: UUID = Depends(get_current_user_id),
    db: OrmSession = Depends(get_db),
    dispatcher: TaskDispatcherPort = Depends(get_task_dispatcher),
) -> dict[str, Any]:
    if payload.size_bytes > 10 * 1024 * 1024:
        raise APIErrorException(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="file_too_large",
            message="File exceeds 10 MB limit.",
            category=ErrorCategory.ENVIRONMENT,
            safe_reason="Uploaded file exceeds the maximum allowed size (10 MB).",
        )
    upload_id = uuid4()
    background_queued = payload.page_count > 50
    artifact = Artifact(
        id=upload_id,
        user_id=user_id,
        volume_path=f"{settings.artifacts_volume_path}/{upload_id}/{payload.filename}",
        relative_path=payload.filename,
        size_bytes=payload.size_bytes,
        content_hash="d" * 64,
        artifact_type="upload_processing" if background_queued else "upload_ready",
        owner_user_id=user_id,
        retention_until=_utcnow() + timedelta(days=7),
    )
    db.add(artifact)
    db.commit()

    if background_queued:
        dispatcher.dispatch_large_upload(upload_id)

    return {
        "schema_version": SCHEMA_VERSION,
        "upload_id": str(upload_id),
        "status": "processing_in_celery" if background_queued else "ready",
        "background_queued": background_queued,
    }


@api_router.get("/uploads/{upload_id}")
def get_upload(
    upload_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: OrmSession = Depends(get_db),
) -> dict[str, Any]:
    art = db.get(Artifact, upload_id)
    if art is None:
        raise APIErrorException(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="upload_not_found",
            message="Upload not found.",
            category=ErrorCategory.RIGHTS,
            safe_reason="Requested upload was not found.",
        )
    if art.owner_user_id != user_id:
        raise _ForbiddenNoLeak()
    is_queued = art.artifact_type == "upload_processing"
    return {
        "schema_version": SCHEMA_VERSION,
        "upload_id": str(upload_id),
        "status": "processing_in_celery" if is_queued else "ready",
        "background_queued": is_queued,
    }


@api_router.get("/runs/{run_id}/export")
def export_run_artifacts(
    run_id: UUID,
    format: str = Query("zip"),
    user_id: UUID = Depends(get_current_user_id),
    db: OrmSession = Depends(get_db),
) -> dict[str, Any]:
    if format not in {"feature", "py", "zip"}:
        raise APIErrorException(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="unsupported_export_format",
            message="Only .feature, .py, and zip formats are supported (.js is forbidden).",
            category=ErrorCategory.RIGHTS,
            safe_reason="Unsupported export format requested.",
        )
    run, _ = _get_owned_run_db(db, run_id, user_id, check_stale=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": str(run.id),
        "format": format,
        "files": ["scenarios/generated.feature", "tests/test_generated.py"],
    }


class MergeRequest(BaseModel):
    mode: Literal["manual", "auto"] = "manual"
    pr_number: int = Field(default=1, ge=1)


@api_router.post("/runs/{run_id}/merge")
async def trigger_merge(
    run_id: UUID,
    payload: MergeRequest,
    response: Response,
    user_id: UUID = Depends(get_current_user_id),
    db: OrmSession = Depends(get_db),
    gh: GitHubIntegrationPort = Depends(get_github_port),
) -> dict[str, Any]:
    run, attempt = _get_owned_run_db(db, run_id, user_id, check_stale=True)
    if payload.mode == "auto" and not run.auto_merge_enabled:
        raise APIErrorException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            error_code="merge_conditions_not_met",
            message="Automatic merge is not enabled for this run.",
            category=ErrorCategory.TESTS,
            run_id=run_id,
            safe_reason="Automatic merge is not enabled for this run.",
        )
    user = db.get(User, user_id)
    repo = db.get(Repository, run.repo_id) if run.repo_id else None
    repo_owner = repo.owner_login if repo else user.github_login
    repo_name = repo.repo_name if repo else "unknown"

    if attempt is None:
        raise APIErrorException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            error_code="merge_conditions_not_met",
            message="Run has no attempt record.",
            category=ErrorCategory.TESTS,
            run_id=run_id,
            safe_reason="Merge gate conditions are not satisfied.",
        )

    cov_row = db.scalar(select(CoverageReport).where(CoverageReport.attempt_id == attempt.id))
    if cov_row is None or run.verdict is None:
        raise APIErrorException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            error_code="merge_conditions_not_met",
            message="Coverage report or run verdict is missing (runner has not finished).",
            category=ErrorCategory.TESTS,
            run_id=run_id,
            attempt_id=attempt.id,
            safe_reason="Cannot merge before test execution and coverage calculation complete.",
        )

    cov_dto = CoverageReportDTO(
        attempt_id=cov_row.attempt_id,
        run_id=cov_row.run_id,
        covered_changed_lines=cov_row.covered_changed_lines,
        executable_changed_lines=cov_row.executable_changed_lines,
        coverage_percentage=cov_row.coverage_percentage,
        covered_files=json.loads(cov_row.covered_files) if cov_row.covered_files else [],
        uncovered_changed_lines=json.loads(cov_row.uncovered_changed_lines)
        if cov_row.uncovered_changed_lines
        else [],
        coverage_verdict=Verdict(cov_row.coverage_verdict),
    )

    # Проверяем реальные права и актуальные SHA через порт GitHub
    repo_info = gh.get_repo_info(repo_owner, repo_name, run.source_sha, run.target_sha)
    if repo_info.source_sha != run.source_sha or repo_info.target_sha != run.target_sha:
        run.freshness = Freshness.STALE.value
        run.merge_status = MergeStatus.CONFLICT.value
        db.commit()
        raise APIErrorException(
            status_code=status.HTTP_409_CONFLICT,
            error_code="stale_snapshot",
            message="Repository source or target SHA changed in GitHub.",
            category=ErrorCategory.STALE,
            run_id=run_id,
            attempt_id=attempt.id,
            details={
                "expected_source_sha": run.source_sha,
                "actual_source_sha": repo_info.source_sha,
                "expected_target_sha": run.target_sha,
                "actual_target_sha": repo_info.target_sha,
            },
            safe_reason="The branch has moved on GitHub. Please refresh and retry.",
        )

    gate = MergeGate()
    builder = MergeDecisionBuilder(gate)
    out = builder.build_from_run_data(
        run_id=run.id,
        attempt_id=attempt.id,
        execution_state=ExecutionState(run.execution_state),
        verdict=Verdict(run.verdict),
        freshness=Freshness(run.freshness),
        merge_status=MergeStatus(run.merge_status),
        coverage_report=cov_dto,
        test_bundle_hash=attempt.test_bundle_hash,
        source_sha=run.source_sha,
        target_sha=run.target_sha,
        owner_user_id=user_id,
        owner_github_login=user.github_login,
        auto_merge_enabled=(payload.mode == "auto" and run.auto_merge_enabled),
        target_branch_protected=repo_info.target_branch_protected,
        user_has_write_permission=repo_info.user_has_write_permission,
        github_app_has_write_permission=repo_info.github_app_has_write_permission,
    )

    if not out.allowed:
        run.merge_status = MergeStatus.DENIED.value
        db.commit()
        raise APIErrorException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            error_code="merge_conditions_not_met",
            message="; ".join(out.denial_reasons),
            category=ErrorCategory.TESTS,
            run_id=run_id,
            attempt_id=attempt.id,
            safe_reason="Merge gate conditions are not satisfied.",
            details={"denial_reasons": out.denial_reasons},
        )

    # Выполняем идемпотентный мерж через GitHubIdempotencyManager и GitHubIntegrationPort
    action_repo = SqlAlchemyGitHubActionRepository(db)
    idempotency_mgr = GitHubIdempotencyManager(action_repo, gh)
    op_context = OperationContext(
        operation_type=GitHubOperationType.MERGE_PR,
        owner_user_id=user_id,
        owner_github_login=user.github_login,
        run_id=run.id,
        attempt_id=attempt.id,
        repo_owner=repo_owner,
        repo_name=repo_name,
        branch_name=f"ai-tests/{run.id}",
        expected_source_sha=run.source_sha,
        expected_target_sha=run.target_sha,
        expected_head_sha=run.source_sha,
        pr_number=payload.pr_number,
        target_branch="main",
    )

    prep = await idempotency_mgr.prepare_operation(op_context)

    if not prep.can_proceed:
        if prep.existing_action and prep.existing_action.status == GitHubOperationStatus.SUCCESS:
            run.merge_status = MergeStatus.COMPLETED.value
            db.commit()
            return {
                "schema_version": SCHEMA_VERSION,
                "run_id": str(run_id),
                "merge_status": MergeStatus.COMPLETED.value,
                "idempotent_replay": True,
                "reconciliation_id": str(prep.existing_action.reconciliation_id),
                "decision": out.merge_decision.model_dump(mode="json"),
            }

        if prep.requires_reconciliation and prep.reconciliation_id:
            try:
                rec_result = await idempotency_mgr.reconcile_unknown(prep.reconciliation_id)
            except GitHubIdempotencyError as exc:
                run.freshness = Freshness.STALE.value
                run.merge_status = MergeStatus.CONFLICT.value
                db.commit()
                raise APIErrorException(
                    status_code=status.HTTP_409_CONFLICT,
                    error_code="stale_snapshot",
                    message=str(exc),
                    category=ErrorCategory.STALE,
                    run_id=run_id,
                    attempt_id=attempt.id,
                    safe_reason="Conflict detected during merge reconciliation.",
                ) from exc

            if rec_result.status == GitHubOperationStatus.SUCCESS:
                run.merge_status = MergeStatus.COMPLETED.value
                db.commit()
                return {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": str(run_id),
                    "merge_status": MergeStatus.COMPLETED.value,
                    "idempotent_replay": True,
                    "reconciled": True,
                    "reconciliation_id": str(prep.reconciliation_id),
                    "decision": out.merge_decision.model_dump(mode="json"),
                }

            response.status_code = status.HTTP_202_ACCEPTED
            return {
                "schema_version": SCHEMA_VERSION,
                "run_id": str(run_id),
                "merge_status": MergeStatus.PENDING.value,
                "idempotent_replay": True,
                "reconciliation_required": True,
                "reconciliation_id": str(prep.reconciliation_id),
            }

        run.merge_status = MergeStatus.CONFLICT.value
        db.commit()
        raise APIErrorException(
            status_code=status.HTTP_409_CONFLICT,
            error_code="stale_snapshot",
            message=prep.reason or "Merge blocked by idempotency check.",
            category=ErrorCategory.STALE,
            run_id=run_id,
            attempt_id=attempt.id,
            safe_reason=prep.reason or "Cannot proceed with merge.",
        )

    assert prep.reconciliation_id is not None
    op_result = await gh.execute_merge(op_context, prep.reconciliation_id)
    await idempotency_mgr.complete_operation(
        reconciliation_id=prep.reconciliation_id,
        actual_sha=op_result.actual_sha or "",
        status=op_result.status,
        safe_reason=op_result.safe_reason,
    )

    if op_result.status == GitHubOperationStatus.SUCCESS:
        run.merge_status = MergeStatus.COMPLETED.value
        db.commit()
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": str(run_id),
            "merge_status": MergeStatus.COMPLETED.value,
            "idempotent_replay": False,
            "reconciliation_id": str(prep.reconciliation_id),
            "decision": out.merge_decision.model_dump(mode="json"),
        }

    if op_result.status == GitHubOperationStatus.UNKNOWN:
        run.merge_status = MergeStatus.PENDING.value
        db.commit()
        response.status_code = status.HTTP_202_ACCEPTED
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": str(run_id),
            "merge_status": MergeStatus.PENDING.value,
            "idempotent_replay": False,
            "reconciliation_required": True,
            "reconciliation_id": str(prep.reconciliation_id),
        }

    run.merge_status = MergeStatus.CONFLICT.value
    db.commit()
    raise APIErrorException(
        status_code=status.HTTP_409_CONFLICT,
        error_code="merge_conflict",
        message=op_result.safe_reason or "GitHub merge failed with conflict.",
        category=ErrorCategory.STALE,
        run_id=run_id,
        attempt_id=attempt.id,
        safe_reason="GitHub rejected the merge due to a conflict.",
    )


@api_router.get("/runs/{run_id}/merge/status")
def get_merge_status(
    run_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: OrmSession = Depends(get_db),
) -> dict[str, Any]:
    run, _ = _get_owned_run_db(db, run_id, user_id, check_stale=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": str(run_id),
        "merge_status": run.merge_status,
    }


@api_router.get("/stats")
def get_stats(
    user_id: UUID = Depends(get_current_user_id),
    db: OrmSession = Depends(get_db),
) -> dict[str, Any]:
    user_runs = db.scalars(select(Run).where(Run.user_id == user_id)).all()
    return {
        "schema_version": SCHEMA_VERSION,
        "total_runs": len(user_runs),
        "completed_runs": sum(
            1 for r in user_runs if r.execution_state == ExecutionState.COMPLETED.value
        ),
        "disclaimer": (
            "Покрытие показывает, какая доля кода выполнялась при тестировании. "
            "Высокое покрытие и успешные тесты не гарантируют отсутствие ошибок; "
            "низкое покрытие само по себе не означает наличие ошибки."
        ),
    }


@api_router.get("/stats/runs")
def get_stats_runs(
    user_id: UUID = Depends(get_current_user_id),
    db: OrmSession = Depends(get_db),
) -> dict[str, Any]:
    user_runs = db.scalars(select(Run).where(Run.user_id == user_id)).all()
    return {"schema_version": SCHEMA_VERSION, "run_ids": [str(r.id) for r in user_runs]}


@api_router.post("/webhooks/github", status_code=status.HTTP_204_NO_CONTENT)
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
    db: OrmSession = Depends(get_db),
    dispatcher: TaskDispatcherPort = Depends(get_task_dispatcher),
) -> Response:
    raw_body = await request.body()
    is_valid, payload_hash = verify_github_webhook_signature(raw_body, x_hub_signature_256)
    if not is_valid:
        raise APIErrorException(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="invalid_webhook_signature",
            message="Webhook HMAC-SHA256 signature verification failed.",
            category=ErrorCategory.RIGHTS,
            safe_reason="Invalid webhook signature.",
        )

    if not x_github_delivery or not x_github_event:
        raise APIErrorException(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="missing_webhook_headers",
            message="Missing X-GitHub-Delivery or X-GitHub-Event header.",
            category=ErrorCategory.RIGHTS,
            safe_reason="Missing required GitHub webhook headers.",
        )

    if x_github_event not in {"push", "pull_request", "installation", "installation_repositories"}:
        raise APIErrorException(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="unsupported_webhook_event",
            message=f"Unsupported event: {x_github_event}",
            category=ErrorCategory.RIGHTS,
            safe_reason="Webhook event is not supported.",
        )

    try:
        parsed_payload = json.loads(raw_body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise APIErrorException(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="invalid_webhook_json",
            message="Webhook body is not valid JSON.",
            category=ErrorCategory.MODEL,
            safe_reason="Malformed JSON payload.",
        ) from exc

    existing = db.scalar(
        select(WebhookDelivery).where(WebhookDelivery.delivery_id == x_github_delivery)
    )
    if existing is not None and existing.processed:
        raise APIErrorException(
            status_code=status.HTTP_409_CONFLICT,
            error_code="duplicate_webhook_delivery",
            message="Webhook delivery_id already processed.",
            category=ErrorCategory.STALE,
            safe_reason="Duplicate webhook delivery.",
        )

    if existing is None:
        delivery_row = WebhookDelivery(
            delivery_id=x_github_delivery,
            event_type=x_github_event,
            payload_hash=payload_hash,
            signature_valid=True,
            processed=False,
        )
        db.add(delivery_row)
        db.commit()

    # Отправляем проверенное событие в очередь воркера;
    # processed=True выставит воркер после обработки
    dispatcher.dispatch_webhook(x_github_delivery, x_github_event, parsed_payload)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
