from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.constants.enums import Role, RunStatus
from src.controllers.views import run_view
from src.models.base import utc_now
from src.models.workflow import AnalysisRun, RecommendationState, Review


def ensure_recommendation_state(db, *, lock=False):
    state = db.get(RecommendationState, 1)
    created = state is None
    if created:
        try:
            with db.begin_nested():
                state = RecommendationState(id=1, revision=0)
                db.add(state)
                db.flush()
        except IntegrityError:
            state = db.get(RecommendationState, 1)
            created = False
    if created and state is not None:
        latest_succeeded = db.scalar(select(AnalysisRun)
                                     .where(AnalysisRun.status == RunStatus.SUCCEEDED)
                                     .order_by(AnalysisRun.id.desc()).limit(1))
        if latest_succeeded is not None:
            latest_succeeded.is_current_recommendation = True
            state.current_run_id = latest_succeeded.id
    if lock:
        db.execute(select(RecommendationState).where(RecommendationState.id == 1).with_for_update()).scalar_one()
        db.refresh(state)
    return state


def accept_fresh_run(db, run):
    """Atomically decide whether a finished run may own the recommendation."""
    state = ensure_recommendation_state(db, lock=True)
    current_id = state.current_run_id
    monotonic = current_id is None or run.id >= current_id
    fresh = run.data_revision == state.revision
    if fresh and monotonic and run.status == RunStatus.SUCCEEDED:
        for old_run in db.scalars(select(AnalysisRun).where(AnalysisRun.is_current_recommendation.is_(True))):
            old_run.is_current_recommendation = False
        run.is_current_recommendation = True
        state.current_run_id = run.id
        state.updated_at = utc_now()
        return True
    run.is_current_recommendation = False
    return False


def recommendation_view(db, user=None):
    state = ensure_recommendation_state(db)
    current = db.get(AnalysisRun, state.current_run_id) if state.current_run_id else None
    current_review = db.scalar(select(Review).where(Review.run_id == current.id)) if current else None
    latest = db.scalar(select(AnalysisRun).order_by(AnalysisRun.id.desc()).limit(1))
    queued = db.scalar(select(AnalysisRun).where(AnalysisRun.status == RunStatus.QUEUED)
                      .order_by(AnalysisRun.id.desc()).limit(1))

    pending_update = bool(
        state.revision > 0
        and (current is None or current.data_revision < state.revision)
    )
    update_failed = bool(
        latest is not None
        and latest.status == RunStatus.FAILED
        and latest.data_revision >= state.revision
        and pending_update
        and queued is None
    )
    if state.revision == 0 and current is None:
        status = 'idle'
    elif pending_update:
        status = 'update_failed' if update_failed else 'pending_update'
    else:
        status = 'current'

    can_see_current = user is None or user.get('role') != Role.VIEWER or (
        current_review is not None and current_review.decision == 'approved'
    )
    current_view = None
    if current is not None and can_see_current:
        current_view = run_view(current, current_review, include_result=True,
                               latest_revision=state.revision, include_snapshot=True)

    latest_review = db.scalar(select(Review).where(Review.run_id == latest.id)) if latest else None
    can_see_latest = latest is not None and (
        user is None or user.get('role') != Role.VIEWER or (
            latest_review is not None and latest_review.decision == 'approved'
        )
    )
    latest_view = None
    if latest is not None and can_see_latest:
        latest_view = run_view(latest, latest_review, include_result=False,
                               latest_revision=state.revision)

    return {
        'status': status,
        'state': status,
        'pending_update': pending_update,
        'pending': pending_update,
        'is_pending': pending_update,
        'advice_pending': pending_update,
        'outdated': pending_update,
        'stale': pending_update,
        'update_failed': update_failed,
        'message': '扩投建议待更新' if pending_update else '现行建议已追上最新点数' if current else '暂无现行建议',
        'revision': state.revision,
        'latest_revision': state.revision,
        'current_revision': current.data_revision if current else 0,
        'current_run_id': state.current_run_id if can_see_current else None,
        'current_recommendation_id': state.current_run_id if can_see_current else None,
        'run_id': state.current_run_id if can_see_current else None,
        'latest_run_id': latest.id if latest is not None and can_see_latest else None,
        'queued_run_id': queued.id if queued is not None and (
            user is None or user.get('role') != Role.VIEWER
        ) else None,
        'current_accessible': bool(can_see_current and current is not None),
        'latest_accessible': bool(can_see_latest),
        'current': current_view,
        'run': current_view,
        'recommendation': current_view,
        'latest': latest_view,
    }
