from fastapi import APIRouter, Depends, Request, Response

from src.constants.enums import Role
from src.controllers.views import correction_view
from src.models.base import Session
from src.models.workflow import CorrectionOrder
from src.services.auth import current_user, roles
from src.services.broker import wake_worker
from src.services.corrections import apply_correction
from src.services.errors import BusinessError
from src.services.recommendations import recommendation_view
from sqlalchemy import select

router = APIRouter(prefix='/api')
editor = roles(Role.ANALYST, Role.SUPERVISOR)


def idempotency_header(request: Request):
    for name in ('idempotency-key', 'x-idempotency-key', 'x-request-id', 'request-id', 'x-order-no'):
        value = request.headers.get(name)
        if value and value.strip():
            return value.strip()
    return None


def _submit_correction(payload, response, user, idempotency_key=None, observation_id=None,
                       created_code=201):
    with Session.begin() as db:
        outcome = apply_correction(
            db, payload, user['sub'], idempotency_key=idempotency_key,
            observation_id=observation_id,
        )
        view = correction_view(outcome.order, replayed=outcome.replayed, run=outcome.run)
        replayed = outcome.replayed
        order_conflict = outcome.order_conflict
        not_found = outcome.not_found
        conflict = outcome.conflict
        reason = outcome.order.conflict_reason
        details = outcome.details

    if order_conflict:
        raise BusinessError('原单号已用于内容不同的更正单，不能改写原回执', 409)
    if not_found:
        raise BusinessError(reason or '批号不存在', 404)
    if conflict:
        raise BusinessError(reason or '原点数不一致，更正单已退回', 409, details=details)

    response.status_code = 200 if replayed else created_code
    wake_worker()
    return view


@router.post('/corrections', status_code=201)
@router.post('/records/corrections', status_code=201)
@router.post('/observations/corrections', status_code=201)
def create_correction(payload: dict, response: Response, user=Depends(editor),
                      idempotency_key: str | None = Depends(idempotency_header)):
    return _submit_correction(payload, response, user, idempotency_key)


@router.post('/correction', status_code=201)
def create_correction_singular(payload: dict, response: Response, user=Depends(editor),
                               idempotency_key: str | None = Depends(idempotency_header)):
    return _submit_correction(payload, response, user, idempotency_key)


@router.post('/observations/correct', status_code=201)
def correct_observation(payload: dict, response: Response, user=Depends(editor),
                        idempotency_key: str | None = Depends(idempotency_header)):
    return _submit_correction(payload, response, user, idempotency_key)


@router.post('/observations/{observation_id}/corrections', status_code=201)
@router.post('/observations/{observation_id}/correction', status_code=201)
@router.post('/records/{observation_id}/corrections', status_code=201)
def correct_observation_by_id(observation_id: int, payload: dict, response: Response,
                              user=Depends(editor),
                              idempotency_key: str | None = Depends(idempotency_header)):
    return _submit_correction(payload, response, user, idempotency_key, observation_id)


@router.patch('/observations/{observation_id}')
@router.put('/observations/{observation_id}')
def patch_observation_by_id(observation_id: int, payload: dict, response: Response,
                            user=Depends(editor),
                            idempotency_key: str | None = Depends(idempotency_header)):
    return _submit_correction(payload, response, user, idempotency_key, observation_id, created_code=200)


@router.post('/batches/{batch_no}/corrections', status_code=201)
@router.post('/batches/{batch_no}/correct', status_code=201)
@router.post('/batch/{batch_no}/corrections', status_code=201)
def correct_batch_by_path(batch_no: str, payload: dict, response: Response, user=Depends(editor),
                          idempotency_key: str | None = Depends(idempotency_header)):
    payload = {**(payload or {}), 'batch_no': batch_no}
    return _submit_correction(payload, response, user, idempotency_key)


@router.get('/corrections')
def list_corrections(user=Depends(current_user)):
    with Session() as db:
        rows = db.scalars(select(CorrectionOrder).order_by(CorrectionOrder.id.desc()).limit(100))
        return [correction_view(row) for row in rows]


@router.get('/corrections/{order_no}')
def correction_receipt(order_no: str, user=Depends(current_user)):
    with Session() as db:
        row = db.scalar(select(CorrectionOrder).where(CorrectionOrder.order_no == order_no))
        if row is None:
            raise BusinessError('更正单回执不存在', 404)
        run = None
        if row.recompute_run_id:
            from src.models.workflow import AnalysisRun
            run = db.get(AnalysisRun, row.recompute_run_id)
        return correction_view(row, run=run)


@router.get('/recommendation')
def recommendation(user=Depends(current_user)):
    with Session() as db:
        return recommendation_view(db, user)


@router.get('/recommendations/current')
@router.get('/recommendation/current')
@router.get('/advice/current')
@router.get('/recommendations')
@router.get('/current-recommendation')
def current_recommendation_alias(user=Depends(current_user)):
    with Session() as db:
        return recommendation_view(db, user)


@router.get('/advice')
def advice_alias(user=Depends(current_user)):
    with Session() as db:
        return recommendation_view(db, user)
