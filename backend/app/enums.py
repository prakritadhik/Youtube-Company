from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    VIEWER = "viewer"


class AgentLevel(StrEnum):
    CEO = "ceo"
    COO = "coo"
    MANAGER = "manager"
    WORKER = "worker"


class ActorKind(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class TaskStatus(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_FOR_DEPENDENCY = "WAITING_FOR_DEPENDENCY"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    SUBMITTED = "SUBMITTED"  # worker claims done; awaiting manager verification
    REVISION_REQUESTED = "REVISION_REQUESTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


TERMINAL_TASK_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED})


class EvidenceKind(StrEnum):
    FILE = "file"
    DB_RECORD = "db_record"
    API_RESPONSE = "api_response"
    URL = "url"
    TEST_RESULT = "test_result"
    NOTE = "note"  # human-readable note: never sufficient on its own


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class ApprovalKind(StrEnum):
    PUBLISHING_PERMISSION = "publishing_permission"
    BUDGET_CHANGE = "budget_change"
    HIGH_RISK_CONTENT = "high_risk_content"
    MEDICAL_REVIEW = "medical_review"
    RIGHTS_CONCERN = "rights_concern"
    UNUSUAL_ACTION = "unusual_action"
    COMMENT_REPLY = "comment_reply"
    STRATEGY_CHANGE = "strategy_change"
    NEW_INTEGRATION = "new_integration"
    IRREVERSIBLE_ACTION = "irreversible_action"
    BOARD_ACTIVATION = "board_activation"
    VIDEO_PUBLICATION = "video_publication"
    GATE_EXCEPTION = "gate_exception"
    OTHER = "other"


class BoardStatus(StrEnum):
    OBSERVED = "OBSERVED"
    UNDER_INVESTIGATION = "UNDER_INVESTIGATION"
    VERIFIED = "VERIFIED"
    APPROVED_FOR_TESTING = "APPROVED_FOR_TESTING"
    ACTIVE_INSTRUCTION = "ACTIVE_INSTRUCTION"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class BoardCategory(StrEnum):
    CHANNEL_OBJECTIVE = "channel_objective"
    AUDIENCE = "audience"
    CREATIVE = "creative"
    WEAK_PATTERN = "weak_pattern"
    RESEARCH = "research"
    SEO = "seo"
    THUMBNAIL_TITLE = "thumbnail_title"
    FEEDBACK = "feedback"
    RETENTION = "retention"
    SENTIMENT = "sentiment"
    QUALITY = "quality"
    ORIGINALITY = "originality"
    LICENSING = "licensing"
    WORKFLOW = "workflow"
    BOTTLENECK = "bottleneck"
    COST = "cost"
    FAILED_EXPERIMENT = "failed_experiment"
    PROCEDURE = "procedure"
    EXPERIMENT = "experiment"
    POLICY = "policy"


# Categories whose meaning depends on one audience: they may never be company-wide.
CHANNEL_SCOPED_CATEGORIES = frozenset(
    {
        BoardCategory.CHANNEL_OBJECTIVE,
        BoardCategory.AUDIENCE,
        BoardCategory.CREATIVE,
        BoardCategory.WEAK_PATTERN,
        BoardCategory.SEO,
        BoardCategory.THUMBNAIL_TITLE,
        BoardCategory.FEEDBACK,
        BoardCategory.RETENTION,
        BoardCategory.SENTIMENT,
        BoardCategory.FAILED_EXPERIMENT,
        BoardCategory.EXPERIMENT,
    }
)


class RiskClass(StrEnum):
    ROUTINE = "routine"
    POLICY = "policy"  # changes rules; needs owner approval to become active
    STRATEGY = "strategy"
    SPENDING = "spending"


class OperatingMode(StrEnum):
    DEVELOPMENT = "DEVELOPMENT"
    APPROVAL_FIRST = "APPROVAL_FIRST"
    CONTROLLED_AUTONOMY = "CONTROLLED_AUTONOMY"
    PAUSED = "PAUSED"


class ClaimClass(StrEnum):
    VERIFIED_FACT = "verified_fact"
    UNVERIFIED_CLAIM = "unverified_claim"
    ESTIMATE = "estimate"
    FORECAST = "forecast"
    HYPOTHESIS = "hypothesis"
    RECOMMENDATION = "recommendation"
    IMPLEMENTED_DECISION = "implemented_decision"
    NEEDS_OWNER_APPROVAL = "needs_owner_approval"
    UNAVAILABLE = "unavailable"


class ReportKind(StrEnum):
    DEPARTMENT = "department"
    OPERATIONS = "operations"
    EXECUTIVE = "executive"


class VideoStage(StrEnum):
    IDEA = "IDEA"
    RESEARCH = "RESEARCH"
    BRIEF = "BRIEF"
    SCRIPT = "SCRIPT"
    PRODUCTION = "PRODUCTION"
    QA = "QA"
    PACKAGING = "PACKAGING"
    READY = "READY"  # cleared for the publishing queue
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    REJECTED = "REJECTED"


class OriginalityStatus(StrEnum):
    NOT_REVIEWED = "NOT_REVIEWED"
    PASS = "PASS"
    NEEDS_REVISION = "NEEDS_REVISION"
    BLOCKED = "BLOCKED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"


class CostKind(StrEnum):
    ACTUAL = "actual"
    ESTIMATE = "estimate"
    INCLUDED = "included_in_subscription"
