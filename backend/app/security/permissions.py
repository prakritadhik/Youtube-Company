"""Actors and permissions. Every service call carries an ``Actor`` and checks it in the backend."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.enums import ActorKind, AgentLevel, Role


class PermissionDenied(Exception):
    def __init__(self, actor: Actor, permission: str):
        super().__init__(f"{actor.kind.value}:{actor.id} lacks permission '{permission}'")
        self.actor = actor
        self.permission = permission


class P:
    """Permission names."""

    EXEC_READ = "exec:read"  # Layer C (owner-facing executive layer)
    DIAG_READ = "diagnostics:read"  # Layers A and B (operations + CEO verification detail)
    BOARD_READ = "board:read"
    BOARD_PROPOSE = "board:propose"
    BOARD_INVESTIGATE = "board:investigate"
    BOARD_VERIFY = "board:verify"
    BOARD_APPROVE_TESTING = "board:approve_testing"
    BOARD_ACTIVATE = "board:activate"
    BOARD_RETIRE = "board:retire"
    TASK_CREATE = "task:create"
    TASK_ASSIGN = "task:assign"
    TASK_WORK = "task:work"
    TASK_REVIEW = "task:review"
    TASK_CANCEL = "task:cancel"
    TASK_RUN = "task:run"  # queue runner only
    REPORT_SUBMIT = "report:submit"
    REPORT_CONSOLIDATE = "report:consolidate"
    REPORT_VERIFY = "report:verify"
    APPROVAL_REQUEST = "approval:request"
    APPROVAL_DECIDE = "approval:decide"  # owner only
    SETTINGS_READ = "settings:read"
    SETTINGS_WRITE = "settings:write"  # owner only
    CONTROL_PAUSE = "control:pause"
    CONTROL_RESUME = "control:resume"  # owner only
    VIDEO_MANAGE = "video:manage"
    ORIGINALITY_REVIEW = "originality:review"
    RIGHTS_VERIFY = "rights:verify"  # CEO's independent inspection
    FINANCE_RECORD = "finance:record"
    SYSTEM_MAINTENANCE = "system:maintenance"
    USERS_MANAGE = "users:manage"


_WORKER = {P.BOARD_READ, P.BOARD_PROPOSE, P.TASK_WORK}
_MANAGER = _WORKER | {
    P.BOARD_INVESTIGATE, P.TASK_CREATE, P.TASK_ASSIGN, P.TASK_REVIEW, P.REPORT_SUBMIT,
    P.APPROVAL_REQUEST, P.VIDEO_MANAGE,
}
_COO = _MANAGER | {P.REPORT_CONSOLIDATE, P.TASK_CANCEL}
_CEO = _COO | {
    P.BOARD_VERIFY, P.BOARD_APPROVE_TESTING, P.BOARD_ACTIVATE, P.BOARD_RETIRE, P.REPORT_VERIFY,
    P.RIGHTS_VERIFY, P.CONTROL_PAUSE, P.SETTINGS_READ, P.EXEC_READ,
}

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    Role.OWNER.value: frozenset(
        {
            P.EXEC_READ, P.DIAG_READ, P.BOARD_READ, P.BOARD_PROPOSE, P.BOARD_INVESTIGATE,
            P.BOARD_ACTIVATE, P.BOARD_RETIRE, P.APPROVAL_DECIDE, P.APPROVAL_REQUEST,
            P.SETTINGS_READ, P.SETTINGS_WRITE, P.CONTROL_PAUSE, P.CONTROL_RESUME, P.TASK_CREATE,
            P.TASK_CANCEL, P.VIDEO_MANAGE, P.USERS_MANAGE, P.SYSTEM_MAINTENANCE,
        }
    ),
    Role.VIEWER.value: frozenset({P.EXEC_READ, P.BOARD_READ}),
}

LEVEL_PERMISSIONS: dict[str, frozenset[str]] = {
    AgentLevel.WORKER.value: frozenset(_WORKER),
    AgentLevel.MANAGER.value: frozenset(_MANAGER),
    AgentLevel.COO.value: frozenset(_COO),
    AgentLevel.CEO.value: frozenset(_CEO),
}

# The only extra permissions an agent row may carry. Owner-only permissions are never grantable.
AGENT_GRANTABLE = frozenset({P.ORIGINALITY_REVIEW, P.FINANCE_RECORD, P.VIDEO_MANAGE, P.REPORT_SUBMIT})

OWNER_ONLY = frozenset({P.APPROVAL_DECIDE, P.SETTINGS_WRITE, P.CONTROL_RESUME, P.USERS_MANAGE})

SYSTEM_PERMISSIONS = frozenset(
    {P.TASK_RUN, P.TASK_CREATE, P.TASK_WORK, P.BOARD_READ, P.FINANCE_RECORD, P.SYSTEM_MAINTENANCE, P.APPROVAL_REQUEST,
     P.CONTROL_PAUSE, P.REPORT_SUBMIT, P.VIDEO_MANAGE}
)


@dataclass(frozen=True)
class Actor:
    kind: ActorKind
    id: str
    role: str
    permissions: frozenset[str] = field(default_factory=frozenset)
    agent_id: int | None = None
    user_id: int | None = None
    tools: frozenset[str] = field(default_factory=frozenset)

    @property
    def label(self) -> str:
        return f"{self.kind.value}:{self.id}"

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    def require(self, permission: str) -> None:
        if permission not in self.permissions:
            raise PermissionDenied(self, permission)

    @property
    def is_owner(self) -> bool:
        return self.kind is ActorKind.USER and self.role == Role.OWNER.value

    @staticmethod
    def for_user(user_id: int, username: str, role: str) -> Actor:
        return Actor(
            ActorKind.USER, username, role, ROLE_PERMISSIONS.get(role, frozenset()), user_id=user_id
        )

    @staticmethod
    def for_agent(
        agent_id: int,
        slug: str,
        level: str,
        extra: list[str] | None = None,
        tools: list[str] | None = None,
    ) -> Actor:
        # Extra permissions stored on an agent row are filtered through a code-defined allowlist, so
        # a tampered database row can never grant an agent an owner-only permission.
        granted = {p for p in (extra or []) if p in AGENT_GRANTABLE}
        perms = set(LEVEL_PERMISSIONS.get(level, frozenset())) | granted
        return Actor(
            ActorKind.AGENT, slug, level, frozenset(perms), agent_id=agent_id, tools=frozenset(tools or [])
        )

    def require_tool(self, tool: str) -> None:
        if self.kind is ActorKind.AGENT and tool not in self.tools:
            raise PermissionDenied(self, f"tool:{tool}")

    @staticmethod
    def system(name: str = "system") -> Actor:
        return Actor(ActorKind.SYSTEM, name, "system", SYSTEM_PERMISSIONS)
