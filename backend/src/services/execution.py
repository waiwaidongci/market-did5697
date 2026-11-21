import logging
from datetime import timedelta

from sqlalchemy import and_, or_, select

from src.config.database import Session
from src.constants.enums import Status
from src.domain.analysis import analyze
from src.domain.spec import SPEC
from src.models.jobs import Delivery, Run, Schedule
from src.models.source import Dataset
from src.services.audit import record
from src.utils.exports import write_report
from src.utils.time import now

log = logging.getLogger(__name__)

LEASE_SECONDS = 180
MAX_ATTEMPTS = 3
MAX_DAY_RETRIES = 3
RETRY_MINUTES = 5
BUSINESS_UTC_OFFSET = timedelta(hours=8)


def business_day(stamp):
    """Report day as seen by the market service (Asia/Shanghai)."""
    return (stamp + BUSINESS_UTC_OFFSET).date()


def _series_head(db, schedule):
    """Newest revision of the batch series a schedule was created against.

    Locking the series (not a single dataset id) means a daily report always
    uses the latest backfill, while every run keeps its own frozen snapshot.
    """
    source = db.get(Dataset, schedule.dataset_id)
    if source is None:
        return None
    return db.scalar(select(Dataset).where(Dataset.name == source.name)
                     .order_by(Dataset.revision.desc()).limit(1))


def schedule_snapshot(dataset):
    return {'dataset_id': dataset.id, 'dataset_revision': dataset.revision,
            'snapshot_checksum': dataset.checksum, 'snapshot': dataset.rows}


def _advance(db, schedule, run):
    """Settle a successful report day: move the day pointer forward once."""
    if not schedule.repeat_daily:
        schedule.enabled = False
        schedule.next_at = now()
        return
    schedule.report_day = run.report_day + timedelta(days=1)
    schedule.retries = 0
    # Keep the original daily slot; do not drift with retry times.
    base = schedule.slot_at or run.scheduled_at or now()
    schedule.slot_at = base + timedelta(days=1)
    schedule.next_at = schedule.slot_at
    if schedule.next_at <= now():
        schedule.next_at = now()


def _defer(db, schedule):
    # A retry wakes sooner than the next daily slot but never moves the slot.
    schedule.next_at = now() + timedelta(minutes=RETRY_MINUTES)


def materialize_due():
    """Create at most one run per schedule report day.

    A report day only advances after that day's run is complete.  A failed
    run is retried in place on the *same* report day; earlier delivered
    reports are never touched.  ``(schedule_id, report_day)`` is unique, so a
    second scheduler pass cannot create a duplicate day.
    """
    if not SPEC.get('scheduled'):
        return
    stamp = now()
    with Session.begin() as db:
        schedules = db.scalars(select(Schedule).where(Schedule.enabled.is_(True),
                                                      Schedule.next_at <= stamp)
                               .order_by(Schedule.next_at).limit(20)
                               .with_for_update(skip_locked=True))
        for item in schedules:
            if item.report_day is None:
                item.report_day = business_day(stamp)
            if item.slot_at is None:
                item.slot_at = item.next_at
            day = item.report_day
            existing = db.scalar(select(Run).where(Run.schedule_id == item.id, Run.report_day == day))
            if existing is not None:
                if existing.status == Status.COMPLETE:
                    _advance(db, item, existing)
                elif existing.status == Status.FAILED:
                    if item.retries < MAX_DAY_RETRIES:
                        _requeue(db, item, existing)
                    else:
                        # Hold at the same report day; do not skip ahead.
                        _defer(db, item)
                else:
                    # Still queued or leased: let the worker finish it.
                    _defer(db, item)
                continue
            dataset = _series_head(db, item)
            if dataset is None:
                _defer(db, item)
                continue
            run = Run(**schedule_snapshot(dataset), schedule_id=item.id, scheduled_at=item.slot_at,
                      report_day=day, name=f"{item.name} · {day.isoformat()}", params=dict(item.params),
                      author=item.author)
            db.add(run)
            db.flush()
            record(db, 'scheduler', 'analysis.queued', run.id, schedule_id=item.id,
                   report_day=day.isoformat(), dataset_revision=dataset.revision)
            _defer(db, item)


def _requeue(db, schedule, run):
    """Give the same report day another execution attempt."""
    schedule.retries += 1
    run.status = Status.QUEUED
    run.attempt = 0
    run.lease_until = None
    run.finished_at = None
    run.error = None
    run.export_file = None
    _defer(db, schedule)
    record(db, 'scheduler', 'analysis.retry', run.id, report_day=run.report_day.isoformat(),
           retry=schedule.retries)


def claim():
    """Lease one runnable job, recovering expired leases.

    A lease left by a crashed worker is reclaimed by the next claim.  Every
    claim bumps ``attempt``; ``execute`` only commits if its attempt is still
    current, so two workers racing for the same job cannot publish two
    results — the stale attempt is discarded.
    """
    stamp = now()
    with Session.begin() as db:
        query = select(Run).where(or_(Run.status == Status.QUEUED,
                                      and_(Run.status == Status.RUNNING, Run.lease_until < stamp)))
        job = db.scalar(query.order_by(Run.id).limit(1).with_for_update(skip_locked=True))
        if job is None:
            return None
        if job.attempt >= MAX_ATTEMPTS:
            job.status = Status.FAILED
            job.error = '后台进程多次中断，请发起新的运行'
            job.finished_at = stamp
            job.lease_until = None
            record(db, 'worker', 'analysis.failed', job.id, reason=job.error)
            plan = db.get(Schedule, job.schedule_id) if job.schedule_id else None
            if plan:
                _defer(db, plan)
            return None
        job.status = Status.RUNNING
        job.attempt += 1
        job.lease_until = stamp + timedelta(seconds=LEASE_SECONDS)
        # The frozen snapshot is authoritative; fall back only for legacy rows.
        source = db.get(Dataset, job.dataset_id)
        rows = job.snapshot or (source.rows if source else [])
        meta = {'run_id': job.id, 'dataset_name': source.name if source else '',
                'dataset_revision': job.dataset_revision,
                'snapshot_checksum': job.snapshot_checksum,
                'report_day': job.report_day.isoformat() if job.report_day else ''}
        db.flush()
        return job.id, job.attempt, rows, job.params, meta


def _deliver(db, job, plan, failure, result):
    ready = not failure and result.get('ready', True)
    detail = '已保存 CSV 到站内收件箱' if ready else (failure or result.get('headline', '结果未通过发布门槛'))
    receipt = db.scalar(select(Delivery).where(Delivery.run_id == job.id))
    if receipt is None:
        receipt = Delivery(run_id=job.id, recipient=plan.recipient,
                           state='delivered' if ready else 'blocked', detail=detail[:500])
        db.add(receipt)
    else:
        receipt.recipient = plan.recipient
        receipt.state = 'delivered' if ready else 'blocked'
        receipt.detail = detail[:500]
    record(db, 'worker', f'delivery.{receipt.state}', job.id, recipient=plan.recipient)


def _settle_after_run(db, job, plan, failure):
    # The failed run stays FAILED (and keeps its error) until the next retry
    # slot.  ``materialize_due`` then re-queues that same report day; the
    # delivered reports of earlier days are never modified.
    if failure:
        _defer(db, plan)
    else:
        _advance(db, plan, job)


def execute(lease):
    job_id, attempt, rows, params, meta = lease
    result = export = failure = None
    try:
        result = analyze(rows, params)
        export = write_report(job_id, attempt, result, snapshot_params=params, snapshot=meta)
    except ValueError as error:
        failure = str(error)[:500]
    except Exception:
        log.exception('Analysis %s failed', job_id)
        failure = '计算失败，请检查字段口径后重试'
    with Session.begin() as db:
        job = db.get(Run, job_id, with_for_update=True)
        # A newer lease superseded this attempt: never publish a second result.
        if job.attempt != attempt or job.status != Status.RUNNING:
            return
        job.lease_until = None
        job.finished_at = now()
        job.status = Status.FAILED if failure else Status.COMPLETE
        job.result = None if failure else result
        job.export_file = export
        job.error = failure
        record(db, 'worker', f'analysis.{job.status}', job.id, attempt=attempt, reason=failure)
        plan = db.get(Schedule, job.schedule_id) if job.schedule_id else None
        if plan:
            _deliver(db, job, plan, failure, result or {})
            _settle_after_run(db, job, plan, failure)


def recover():
    """Return runs orphaned by a dead worker to the queue."""
    stamp = now()
    with Session.begin() as db:
        stale = db.scalars(select(Run).where(Run.status == Status.RUNNING,
                                             Run.lease_until < stamp - timedelta(seconds=LEASE_SECONDS))
                           .with_for_update(skip_locked=True))
        for job in stale:
            record(db, 'worker', 'analysis.lease_expired', job.id, attempt=job.attempt)
            if job.attempt >= MAX_ATTEMPTS:
                job.status = Status.FAILED
                job.error = '后台进程多次中断，请发起新的运行'
                job.finished_at = stamp
                job.lease_until = None
                plan = db.get(Schedule, job.schedule_id) if job.schedule_id else None
                if plan:
                    _settle_after_run(db, job, plan, job.error)
            else:
                job.status = Status.QUEUED
                job.lease_until = None
