from datetime import datetime
from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
from src.utils.time import now


class Dataset(Base):
    """Immutable market-day snapshot.

    A batch name identifies one reporting series (for example the daily gate
    log export).  Re-uploading content that is already represented by a
    revision is a no-op.  A changed upload becomes a new revision: the
    uploaded rows are merged onto the revision they supersede (a late
    backfill only carries the missing market-days), and the superseded
    revision is kept untouched so runs and reports stay reproducible.

    ``checksum`` is the fingerprint of the merged snapshot (what a run
    locks).  ``upload_checksum`` is the fingerprint of the rows as uploaded,
    kept for audit; it is deliberately not unique so a correction that
    reverts to an earlier value still creates a traceable revision.
    """

    __tablename__ = 'datasets'
    __table_args__ = (
        UniqueConstraint('name', 'revision'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    filename: Mapped[str] = mapped_column(String(160))
    checksum: Mapped[str] = mapped_column(String(64))
    upload_checksum: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(default=1)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey('datasets.id'))
    added_rows: Mapped[int] = mapped_column(default=0)
    updated_rows: Mapped[int] = mapped_column(default=0)
    rows: Mapped[list] = mapped_column(JSON)
    row_count: Mapped[int]
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)
