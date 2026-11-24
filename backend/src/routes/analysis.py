from fastapi import APIRouter, Depends
from fastapi.responses import Response

from src.config.project import SPEC
from src.constants.enums import Role, RunStatus
from src.controllers.views import review_view, run_view
from src.models.base import Session
from src.schemas.requests import CreateRun, ReviewInput
from src.services.auth import current_user, roles
from src.services.broker import wake_worker
from src.services.errors import BusinessError
from src.services.recommendations import ensure_recommendation_state, recommendation_view
from src.services.runs import create_run, rerun_analysis, submit_review, visible_runs
from src.utils.formatting import csv_text

router = APIRouter(prefix='/api/runs')


@router.get('')
def index(user=Depends(current_user)):
    with Session() as db:
        revision = ensure_recommendation_state(db).revision
        return [run_view(run, review, latest_revision=revision)
                for run, review in visible_runs(db, user)]


@router.get('/recommendation')
def run_recommendation(user=Depends(current_user)):
    with Session() as db:
        return recommendation_view(db, user)


@router.get('/current')
def current_run_alias(user=Depends(current_user)):
    with Session() as db:
        return recommendation_view(db, user)


@router.get('/latest')
def latest_run(user=Depends(current_user)):
    with Session() as db:
        pairs = visible_runs(db, user)
        if not pairs:
            raise BusinessError('没有可查看的分析结果', 404)
        run, review = pairs[0]
        revision = ensure_recommendation_state(db).revision
        return run_view(run, review, include_result=True, latest_revision=revision,
                        include_snapshot=True)


@router.post('', status_code=201)
def create(request: CreateRun, user=Depends(roles(Role.ANALYST))):
    with Session.begin() as db:
        item = create_run(db, request, user['sub'])
        view = run_view(item)
    wake_worker()
    return view


@router.get('/{run_id}')
def detail(run_id: int, user=Depends(current_user)):
    with Session() as db:
        run, review = visible_runs(db, user, run_id)
        revision = ensure_recommendation_state(db).revision
        return run_view(run, review, include_result=True, latest_revision=revision,
                        include_snapshot=True)


@router.post('/{run_id}/recalculate', status_code=201)
@router.post('/{run_id}/recompute', status_code=201)
def recalculate(run_id: int, user=Depends(roles(Role.ANALYST, Role.SUPERVISOR))):
    with Session.begin() as db:
        item = rerun_analysis(db, run_id, user['sub'])
        view = run_view(item)
    wake_worker()
    return view


@router.post('/{run_id}/review', status_code=201)
def decision(run_id: int, request: ReviewInput, user=Depends(roles(Role.SUPERVISOR))):
    with Session.begin() as db:
        return review_view(submit_review(db, run_id, request, user['sub']))


@router.get('/{run_id}/export')
def export(run_id: int, user=Depends(current_user)):
    with Session() as db:
        run, _ = visible_runs(db, user, run_id)
        if run.status != RunStatus.SUCCEEDED:
            raise BusinessError('分析完成后才能下载明细', 409)
        result = run.result
        file = f'{SPEC["slug"]}-run-{run_id}.csv'
        return Response(csv_text(result['columns'], result['rows']), media_type='text/csv',
                        headers={'Content-Disposition': f'attachment; filename={file}'})
