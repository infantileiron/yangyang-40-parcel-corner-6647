from src.utils.formatting import timestamp


def review_view(item):
    return {'decision': item.decision, 'note': item.note, 'reviewer': item.reviewer,
            'at': timestamp(item.created_at)} if item else None


def run_view(item, review=None, *, include_result=False, latest_revision=None, include_snapshot=False):
    view = {'id': item.id, 'title': item.title, 'owner': item.owner, 'status': item.status,
            'parameters': item.parameters, 'sample_size': item.sample_size,
            'snapshot_id': item.last_observation_id, 'created_at': timestamp(item.created_at),
            'finished_at': timestamp(item.finished_at), 'review': review_view(review), 'error': item.error,
            'data_revision': getattr(item, 'data_revision', 0),
            'base_stage': getattr(item, 'base_stage', None),
            'earliest_affected_stage': getattr(item, 'base_stage', None),
            'correction_id': getattr(item, 'correction_id', None),
            'is_current': bool(getattr(item, 'is_current_recommendation', False)),
            'current_recommendation': bool(getattr(item, 'is_current_recommendation', False))}
    if latest_revision is not None:
        view['stale'] = item.data_revision < latest_revision
    else:
        view['stale'] = not view['is_current'] and item.status == 'succeeded' and item.data_revision > 0
    view['outdated'] = view['stale']
    view['is_latest'] = not view['stale']
    if include_result:
        view['result'] = item.result
    if include_snapshot:
        view['sample_snapshot'] = item.sample_snapshot
    return view


def observation_view(item):
    return {'id': item.id, 'data': item.payload, 'version': getattr(item, 'version', 0),
            'created_at': timestamp(item.created_at)}


def correction_view(item, *, replayed=False, run=None):
    scalar_field = item.target_field
    original_count = None
    corrected_count = None
    if scalar_field and isinstance(item.expected_payload, dict):
        original_count = item.expected_payload.get(scalar_field)
    if original_count is None and isinstance(item.expected_payload, dict):
        original_values = [value for key, value in item.expected_payload.items()
                           if key in {'old_test', 'old_fail', 'new_test', 'new_fail'}]
        if len(original_values) == 1:
            original_count = original_values[0]
    if scalar_field and isinstance(item.after_payload, dict):
        corrected_count = item.after_payload.get(scalar_field)
    if corrected_count is None and isinstance(item.after_payload, dict):
        corrected_values = [value for key, value in item.after_payload.items()
                            if key in {'old_test', 'old_fail', 'new_test', 'new_fail'}]
        if len(corrected_values) == 1:
            corrected_count = corrected_values[0]
    return {
        'id': item.id,
        'order_no': item.order_no,
        'receipt_no': item.order_no,
        'original_order_no': item.order_no,
        'idempotency_key': item.order_no,
        'request_no': item.order_no,
        'request_id': item.order_no,
        'batch_no': item.batch_no,
        'batch': item.batch_no,
        'sheet': item.batch_no,
        'observation_id': item.observation_id,
        'field': item.target_field,
        'status': item.status,
        'accepted': item.status == 'applied',
        'posted': item.status == 'applied',
        'replayed': replayed,
        'duplicate': replayed,
        'idempotent_replay': replayed,
        'original': item.expected_payload,
        'original_values': item.expected_payload,
        'original_count': original_count,
        'origin_count': original_count,
        'expected_original': item.expected_payload,
        'before': item.before_payload,
        'corrected': item.after_payload,
        'corrected_values': item.after_payload,
        'corrected_count': corrected_count,
        'new_count': corrected_count,
        'after': item.after_payload,
        'conflict_reason': item.conflict_reason,
        'data_revision': item.data_revision,
        'revision': item.data_revision,
        'base_stage': item.base_stage,
        'earliest_affected_stage': item.base_stage,
        'recompute_run_id': item.recompute_run_id or (run.id if run else None),
        'run_id': item.recompute_run_id or (run.id if run else None),
        'created_at': timestamp(item.created_at),
    }


def schedule_view(item):
    return {'id': item.id, 'run_id': item.run_id, 'recipient': item.recipient,
            'interval_minutes': item.interval_minutes, 'next_at': timestamp(item.next_at), 'active': item.active}


def delivery_view(delivery, schedule):
    return {'id': delivery.id, 'schedule_id': schedule.id, 'recipient': schedule.recipient,
            'run_id': schedule.run_id, 'at': timestamp(delivery.delivered_at),
            'scheduled_at': timestamp(delivery.scheduled_at)}
