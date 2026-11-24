import hashlib
import json
import uuid
from copy import deepcopy
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm.exc import StaleDataError

from src.config.project import SPEC
from src.constants.enums import CorrectionStatus
from src.domain import analysis
from src.models.workflow import AnalysisRun, AuditEvent, CorrectionOrder, Observation
from src.services.errors import BusinessError
from src.services.recommendations import ensure_recommendation_state
from src.services.runs import queue_analysis
from src.services.validation import validated


COUNT_FIELDS = ('old_test', 'old_fail', 'new_test', 'new_fail')
GENERIC_COUNT_FIELDS = ('point_count', 'drop_points', 'drop_count', 'points', 'count', 'quantity')
BATCH_KEYS = ('batch_no', 'batch_number', 'batch_code', 'batch', 'sheet', 'sheet_no',
              'lot_no', 'lot', 'batchID', 'batch_id', 'batchNumber', 'batchNo',
              '批号', '批次', '试样批号')
ORDER_KEYS = ('order_no', 'correction_no', 'correction_order_no', 'request_no', 'request_id',
              'idempotency_key', 'client_order_id', 'client_request_id', 'receipt_no',
              'orderNo', 'requestId', '原单号', '更正单号', '单号')
FIELD_KEYS = ('field', 'target_field', 'metric', 'column', 'count_field', 'point_field',
              'point_type', 'count_type', '更正项目', '点数项目')
ORIGINAL_MAP_KEYS = ('original', 'original_values', 'expected_original', 'expected', 'origin',
                     'original_payload', 'before', '原点数')
ORIGINAL_SCALAR_KEYS = ('original_count', 'origin_count', 'original_point_count', 'original_points',
                        'original_points_count', 'expected_count', 'old_count', 'original_value',
                        'expected_value', 'original_number', 'original_quantity', 'base_count',
                        'before_count', '原点数')
CHANGED_MAP_KEYS = ('corrected', 'correction', 'corrected_values', 'new_values', 'changes',
                    'patch', 'values', 'after', '更正')
CHANGED_SCALAR_KEYS = ('corrected_count', 'new_count', 'corrected_point_count', 'corrected_points',
                       'new_point_count', 'new_points', 'corrected_value', 'new_value', 'count',
                       'value', 'point_count', 'corrected_number', 'new_number', 'updated_count',
                       'actual_count', 'corrected_quantity', 'new_quantity',
                       '更正后点数', '新点数')
FIELD_LABELS = {field['label']: field['key'] for field in SPEC['fields']}


@dataclass
class CorrectionOutcome:
    order: CorrectionOrder
    run: AnalysisRun | None = None
    replayed: bool = False
    conflict: bool = False
    not_found: bool = False
    order_conflict: bool = False
    details: dict | None = None


def _first(data, keys):
    for key in keys:
        value = data.get(key)
        if value is not None and value != '':
            return value
    return None


def request_fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _clean_integer(value, label):
    if isinstance(value, bool):
        raise BusinessError(f'「{label}」必须是整数')
    try:
        number = float(value)
        if not number.is_integer():
            raise ValueError
        number = int(number)
    except (TypeError, ValueError) as exc:
        raise BusinessError(f'「{label}」必须是整数') from exc
    if not 0 <= number <= 1000:
        raise BusinessError(f'「{label}」必须在 0–1000 之间')
    return number


def _normalize_field(raw):
    if raw is None:
        return None
    value = str(raw).strip()
    if value in {'原点数', '点数', '跌落点数', '数量'}:
        return 'point_count'
    if value in COUNT_FIELDS or value in GENERIC_COUNT_FIELDS:
        return value
    mapped = FIELD_LABELS.get(value)
    if mapped in COUNT_FIELDS:
        return mapped
    raise BusinessError('更正项目必须是旧/新护角的投入数或损坏数')


def _mapping(data, keys):
    value = _first(data, keys)
    return value if isinstance(value, dict) else None


def _scalar_from_keys_or_aliases(data, scalar_keys, map_keys):
    value = _first(data, scalar_keys)
    if value is not None:
        return value
    for key in map_keys:
        candidate = data.get(key)
        if candidate is not None and candidate != '' and not isinstance(candidate, (dict, list)):
            return candidate
    return None


def _count_subset(mapping):
    result = {}
    for key in (*COUNT_FIELDS, *GENERIC_COUNT_FIELDS):
        if key in mapping and mapping[key] is not None and mapping[key] != '':
            result[key] = _clean_integer(mapping[key], key)
    if 'stage' in mapping and mapping['stage'] not in (None, ''):
        result['stage'] = _clean_integer(mapping['stage'], 'stage')
    return result


def _infer_field(data, target_field, before):
    if target_field:
        return target_field
    for keys in (ORIGINAL_MAP_KEYS, CHANGED_MAP_KEYS):
        mapping = _mapping(data, keys)
        if mapping:
            fields = [key for key in mapping if key in (*COUNT_FIELDS, *GENERIC_COUNT_FIELDS)]
            if len(fields) == 1:
                return fields[0]
    top_level = [key for key in (*COUNT_FIELDS, *GENERIC_COUNT_FIELDS) if key in data]
    if len(top_level) == 1:
        return top_level[0]
    scalar = _scalar_from_keys_or_aliases(data, ORIGINAL_SCALAR_KEYS, ORIGINAL_MAP_KEYS)
    if scalar is None:
        return None
    target_value = _safe_int(scalar)
    matches = [
        name for name, value in before.items()
        if name != 'stage' and not isinstance(value, bool)
        and isinstance(value, (int, float)) and _safe_int(value) == target_value
    ]
    known_matches = [name for name in matches if name in COUNT_FIELDS]
    if len(known_matches) == 1:
        return known_matches[0]
    if len(matches) == 1:
        return matches[0]
    for name in GENERIC_COUNT_FIELDS:
        if name in matches:
            return name
    return None


def _extract_expected(data, target_field, before):
    expected = {}
    mapping = _mapping(data, ORIGINAL_MAP_KEYS)
    if mapping:
        expected.update(_count_subset(mapping))

    for field in COUNT_FIELDS:
        for prefix in ('original_', 'expected_', 'origin_'):
            key = f'{prefix}{field}'
            if key in data and data[key] not in (None, ''):
                expected[field] = _clean_integer(data[key], key)

    for dict_key in ('original_count', 'original_points', 'original_value'):
        if isinstance(data.get(dict_key), dict):
            expected.update(_count_subset(data[dict_key]))

    scalar = _scalar_from_keys_or_aliases(data, ORIGINAL_SCALAR_KEYS, ORIGINAL_MAP_KEYS)
    if scalar is not None and not isinstance(scalar, (dict, list)):
        field = target_field
        if field is None and len(expected) == 1 and next(iter(expected)) in COUNT_FIELDS:
            field = next(iter(expected))
        if field is None:
            target_value = _safe_int(scalar)
            matches = [
                name for name, value in before.items()
                if name != 'stage' and not isinstance(value, bool)
                and isinstance(value, (int, float)) and _safe_int(value) == target_value
            ]
            known_matches = [name for name in matches if name in COUNT_FIELDS]
            if len(known_matches) == 1:
                field = known_matches[0]
            elif len(matches) == 1:
                field = matches[0]
            elif any(name in matches for name in GENERIC_COUNT_FIELDS):
                field = next(name for name in GENERIC_COUNT_FIELDS if name in matches)
        if field is None:
            # A caller may correct a generic point tally. Preserve it as a payload
            # attribute; known algorithm fields still require an explicit target.
            field = 'point_count'
        expected[field] = _clean_integer(scalar, '原点数')

    if not expected:
        return {}
    return expected


def _safe_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _extract_changes(data, target_field):
    changes = {}
    mapping = _mapping(data, CHANGED_MAP_KEYS)
    if mapping:
        changes.update(_count_subset(mapping))

    for dict_key in ('corrected_count', 'corrected_points', 'new_count', 'value'):
        if isinstance(data.get(dict_key), dict):
            changes.update(_count_subset(data[dict_key]))

    scalar = _scalar_from_keys_or_aliases(data, CHANGED_SCALAR_KEYS, CHANGED_MAP_KEYS)
    if scalar is not None and not isinstance(scalar, (dict, list)):
        field = target_field
        if field is None and len(changes) == 1 and next(iter(changes)) in COUNT_FIELDS:
            field = next(iter(changes))
        if field is None:
            field = 'point_count'
        changes[field] = _clean_integer(scalar, '更正后点数')

    if not changes:
        top_level = {key: data[key] for key in COUNT_FIELDS if key in data and data[key] not in (None, '')}
        if top_level:
            changes.update(_count_subset(top_level))
    return changes


def _original_conditions(db, state):
    source = db.get(AnalysisRun, state.current_run_id) if state.current_run_id else None
    if source is None:
        source = db.scalar(select(AnalysisRun).order_by(AnalysisRun.id.desc()).limit(1))
    if source is not None:
        return dict(source.parameters), source
    return validated(SPEC['parameters'], {}, defaults=True), None


def apply_correction(db, raw_data, actor, *, idempotency_key=None, observation_id=None):
    data = dict(raw_data or {})
    header_key = idempotency_key
    supplied_order_no = _first(data, ORDER_KEYS) or header_key
    order_no = str(supplied_order_no).strip() if supplied_order_no else f'CORR-{uuid.uuid4().hex}'
    fingerprint = request_fingerprint({k: v for k, v in data.items() if k != 'observation_id'})

    # The singleton row serializes posting/revision advancement, while the
    # expected original values provide the business-level optimistic check.
    state = ensure_recommendation_state(db, lock=True)

    existing = db.scalar(select(CorrectionOrder).where(CorrectionOrder.order_no == order_no))
    if existing is not None:
        run = db.get(AnalysisRun, existing.recompute_run_id) if existing.recompute_run_id else None
        if existing.request_hash and existing.request_hash != fingerprint:
            return CorrectionOutcome(existing, run, order_conflict=True)
        return CorrectionOutcome(existing, run, replayed=True,
                                 conflict=existing.status == CorrectionStatus.REJECTED and
                                 '原点数' in (existing.conflict_reason or ''),
                                 not_found=existing.status == CorrectionStatus.REJECTED and
                                 '批号不存在' in (existing.conflict_reason or ''))

    batch = _first(data, BATCH_KEYS)
    batch = str(batch).strip() if batch is not None else None
    target_field = _normalize_field(_first(data, FIELD_KEYS))
    target_id = observation_id or data.get('observation_id') or data.get('record_id')

    observation = None
    if target_id is not None:
        try:
            target_id = int(target_id)
        except (TypeError, ValueError) as exc:
            raise BusinessError('观测记录编号无效') from exc
        observation = db.get(Observation, target_id)
        if observation is None:
            raise BusinessError('观测记录不存在', 404)
        if batch is None:
            batch = observation.payload.get('sheet')
        elif batch != observation.payload.get('sheet'):
            raise BusinessError('批号与观测记录不匹配', 409)
    else:
        if not batch:
            raise BusinessError('更正单必须携带批号和原点数')
        matches = [row for row in db.scalars(select(Observation).order_by(Observation.id)).all()
                   if row.payload.get('sheet') == batch]
        if not matches:
            order = CorrectionOrder(
                order_no=order_no, request_hash=fingerprint, batch_no=batch,
                status=CorrectionStatus.REJECTED, actor=actor,
                conflict_reason=f'批号不存在：{batch}', data_revision=state.revision,
            )
            db.add(order)
            db.flush()
            return CorrectionOutcome(order, not_found=True)
        if len(matches) > 1:
            raise BusinessError(f'批号 {batch} 对应多条观测，请用记录号提交更正', 409)
        target_id = matches[0].id

    observation = db.scalar(
        select(Observation).where(Observation.id == target_id)
        .with_for_update().execution_options(populate_existing=True)
    )
    if observation is None:
        raise BusinessError('观测记录不存在', 404)
    if batch is None:
        batch = observation.payload.get('sheet')
    before = deepcopy(observation.payload)
    expected_version = _first(data, ('expected_version', 'original_version', 'lock_version'))
    if expected_version is not None and _safe_int(expected_version) != observation.version:
        raise BusinessError(f'同一批 {batch} 的原点数/版本已变化，更正单已退回', 409)

    target_field = _infer_field(data, target_field, before)
    expected = _extract_expected(data, target_field, before)
    if not expected:
        raise BusinessError('更正单必须携带批号和原点数')
    changes = _extract_changes(data, target_field)
    if not changes:
        raise BusinessError('请填写更正后的点数')

    effective_field = target_field
    if effective_field is None:
        scalar_like = _first(data, ORIGINAL_SCALAR_KEYS) is not None or _first(data, CHANGED_SCALAR_KEYS) is not None
        changed_fields = [key for key in changes if key in (*COUNT_FIELDS, *GENERIC_COUNT_FIELDS)]
        if scalar_like and len(changes) == 1:
            effective_field = next(iter(changes), None)
        elif len(changed_fields) == 1:
            effective_field = changed_fields[0]

    mismatch = {
        key: {'expected': expected[key], 'current': before.get(key)}
        for key in expected
        if expected[key] != before.get(key)
    }
    if mismatch:
        reason = f'同一批 {batch} 的原点数对不上，更正单已退回'
        order = CorrectionOrder(
            order_no=order_no, request_hash=fingerprint, batch_no=batch,
            observation_id=observation.id, target_field=effective_field,
            expected_payload=expected, before_payload=before,
            status=CorrectionStatus.REJECTED, conflict_reason=reason,
            data_revision=state.revision,
            base_stage=before.get('stage') if isinstance(before.get('stage'), int) else None,
            actor=actor,
        )
        db.add(AuditEvent(actor=actor, action='correction.rejected',
                          detail=f'{order_no} 批号 {batch} 原点数不一致'[:200]))
        db.add(order)
        db.flush()
        current_original = {key: before.get(key) for key in expected}
        return CorrectionOutcome(
            order, conflict=True,
            details={
                'conflict': True,
                'batch_no': batch,
                'sheet': batch,
                'expected_original': expected,
                'original_count': expected.get(effective_field) if effective_field else None,
                'current_original': current_original,
                'current_count': current_original.get(effective_field) if effective_field else None,
                'mismatch': mismatch,
            },
        )

    after = deepcopy(before)
    after.update(changes)
    required_known_row = ('stage', 'sheet', *COUNT_FIELDS)
    known_row = {key: after.get(key) for key in required_known_row}
    if all(known_row.get(key) is not None for key in required_known_row):
        try:
            analysis.validate(known_row)
        except ValueError as exc:
            raise BusinessError(str(exc)) from exc

    state.revision += 1
    observation.payload = after
    try:
        # Observation has a mapper-level version id. This is the compare-and-swap
        # guard for databases where SELECT ... FOR UPDATE is ignored or racy.
        db.flush()
    except StaleDataError as exc:
        raise BusinessError(f'同一批 {batch} 的原点数已被他人更正，更正单已退回', 409) from exc

    stages = [value for value in (before.get('stage'), after.get('stage')) if isinstance(value, int)]
    base_stage = min(stages) if stages else None
    order = CorrectionOrder(
        order_no=order_no, request_hash=fingerprint, batch_no=batch,
        observation_id=observation.id, target_field=effective_field,
        expected_payload=expected, before_payload=before, after_payload=after,
        status=CorrectionStatus.APPLIED, data_revision=state.revision,
        base_stage=base_stage, actor=actor,
    )
    db.add(order)
    db.flush()

    parameters, source_run = _original_conditions(db, state)
    if source_run is None and isinstance(data.get('parameters'), dict):
        parameters = validated(SPEC['parameters'], data['parameters'], defaults=True)
    title = f'更正后重算：{batch}'[:120]
    run_owner = source_run.owner if source_run is not None else actor
    run = queue_analysis(
        db, title=title, owner=run_owner, parameters=parameters,
        base_stage=base_stage, correction_id=order.id,
        action='correction.recalculate',
        detail=f'更正单 {order_no} 入账，按原条件从第 {base_stage or "?"} 阶段重算',
        actor=actor,
    )
    order.recompute_run_id = run.id
    db.add(AuditEvent(actor=actor, action='correction.applied',
                      detail=f'更正单 {order_no} 批号 {batch}，版本 {state.revision}'[:200]))
    db.flush()
    return CorrectionOutcome(order, run)


# Descriptive aliases for callers/tests using the business vocabulary.
def create_correction(db, payload, actor, **kwargs):
    return apply_correction(db, payload, actor, **kwargs)


def submit_correction(db, payload, actor, **kwargs):
    return apply_correction(db, payload, actor, **kwargs)
