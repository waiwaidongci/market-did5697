from src.utils.time import iso


def dataset(item):
    return {'id': item.id, 'name': item.name, 'filename': item.filename, 'row_count': item.row_count,
            'checksum': item.checksum, 'revision': item.revision, 'supersedes_id': item.supersedes_id,
            'added_rows': item.added_rows, 'updated_rows': item.updated_rows,
            'author': item.author, 'created_at': iso(item.created_at)}


def run(item, detail=False):
    item_view = {'id': item.id, 'dataset_id': item.dataset_id, 'name': item.name, 'status': item.status,
                 'author': item.author, 'review': item.review, 'decision': item.decision,
                 'dataset_revision': item.dataset_revision, 'snapshot_checksum': item.snapshot_checksum,
                 'report_day': item.report_day.isoformat() if item.report_day else None,
                 'created_at': iso(item.created_at), 'finished_at': iso(item.finished_at),
                 'scheduled_at': iso(item.scheduled_at), 'schedule_id': item.schedule_id,
                 'has_export': bool(item.export_file), 'error': item.error}
    if detail:
        item_view.update(params=item.params, result=item.result, review_note=item.review_note,
                         attempt=item.attempt, report=report_ref(item))
    return item_view


def report_ref(item):
    """Immutable identity of the delivered artifact a page reads."""
    return {'run_id': item.id, 'dataset_id': item.dataset_id, 'dataset_revision': item.dataset_revision,
            'snapshot_checksum': item.snapshot_checksum, 'report_day': item.report_day.isoformat()
            if item.report_day else None, 'export_file': item.export_file,
            'review': item.review, 'decision': item.decision, 'status': item.status}


def schedule(item):
    from src.domain.spec import SPEC
    return {'id': item.id, 'name': item.name, 'dataset_id': item.dataset_id, 'recipient': item.recipient,
            'next_at': iso(item.next_at), 'slot_at': iso(item.slot_at), 'repeat_daily': item.repeat_daily,
            'enabled': item.enabled, 'retries': item.retries,
            'report_day': item.report_day.isoformat() if item.report_day else None,
            'next_report_day': item.report_day.isoformat() if item.enabled and item.report_day else None,
            'date_param': SPEC.get('schedule_date_param')}


def delivery(item):
    return {'id': item.id, 'run_id': item.run_id, 'recipient': item.recipient,
            'state': item.state, 'detail': item.detail, 'created_at': iso(item.created_at)}
