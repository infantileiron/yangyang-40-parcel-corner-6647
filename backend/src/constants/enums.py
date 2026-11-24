from enum import StrEnum


class Role(StrEnum):
    VIEWER = 'viewer'
    ANALYST = 'analyst'
    SUPERVISOR = 'supervisor'


class RunStatus(StrEnum):
    QUEUED = 'queued'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'


class Decision(StrEnum):
    APPROVED = 'approved'
    RETURNED = 'returned'


class CorrectionStatus(StrEnum):
    APPLIED = 'applied'
    REJECTED = 'rejected'


class RecommendationStatus(StrEnum):
    IDLE = 'idle'
    CURRENT = 'current'
    PENDING = 'pending_update'
    FAILED = 'update_failed'
