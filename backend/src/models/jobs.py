from datetime import date, datetime
from sqlalchemy import JSON, Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
from src.constants.enums import Review, Status
from src.utils.time import now


class Schedule(Base):
    __tablename__ = 'schedules'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    # The batch series to report from.  Each report day locks the newest
    # revision of this series, so later revisions cannot move a delivered run.
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'))
    params: Mapped[dict] = mapped_column(JSON)
    recipient: Mapped[str] = mapped_column(String(100))
    next_at: Mapped[datetime] = mapped_column(index=True)
    # Nominal daily run time for repeated schedules.  It advances by one day
    # per completed report day and is *not* moved by retries, so a late
    # recovery still reports the original slot.
    slot_at: Mapped[datetime | None]
    # The report day that has not been settled yet.  It only moves forward
    # after a run for it completes; failures retry the same day.
    report_day: Mapped[date | None] = mapped_column(Date)
    retries: Mapped[int] = mapped_column(default=0)
    repeat_daily: Mapped[bool]
    enabled: Mapped[bool] = mapped_column(default=True)
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)


class Run(Base):
    __tablename__ = 'runs'
    __table_args__ = (UniqueConstraint('schedule_id', 'report_day'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'))
    # Immutable input snapshot captured when the run is queued.  Revisions
    # imported later are isolated from this run and from its report.
    dataset_revision: Mapped[int] = mapped_column(default=1)
    snapshot_checksum: Mapped[str] = mapped_column(String(64), default='')
    snapshot: Mapped[list] = mapped_column(JSON)
    schedule_id: Mapped[int | None] = mapped_column(ForeignKey('schedules.id'))
    scheduled_at: Mapped[datetime | None]
    report_day: Mapped[date | None] = mapped_column(Date)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default=Status.QUEUED, index=True)
    params: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(500))
    export_file: Mapped[str | None] = mapped_column(String(100))
    review: Mapped[str] = mapped_column(String(20), default=Review.PENDING)
    review_note: Mapped[str | None] = mapped_column(String(500))
    decision: Mapped[str | None] = mapped_column(String(120))
    attempt: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[datetime | None]
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)
    finished_at: Mapped[datetime | None]


class Delivery(Base):
    __tablename__ = 'deliveries'

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('runs.id'), unique=True)
    recipient: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(20))
    detail: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(default=now)
