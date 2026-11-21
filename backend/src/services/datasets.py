import csv
import hashlib
import io
import json
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from src.domain import analysis
from src.domain.demo import demo_rows
from src.domain.spec import SPEC
from src.models.source import Dataset


def _row_key(row):
    return (row['date'], row['market_id'])


def _canonical(rows):
    ordered = sorted(rows, key=_row_key)
    raw = json.dumps(ordered, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest(), ordered


def checksum(rows):
    return _canonical(rows)[0]


def _merge(base_rows, uploaded):
    """Merge a late upload onto a revision without dropping any market-day.

    Returns (rows, added, updated).  A repeated row with an identical value is
    neutral; a corrected value counts as an update.  The superseded revision
    keeps the original rows, so the change stays traceable.
    """
    merged = {(row['market_id'], row['date']): row for row in base_rows}
    added = updated = 0
    for row in uploaded:
        key = (row['market_id'], row['date'])
        previous = merged.get(key)
        if previous is None:
            added += 1
        elif previous != row:
            updated += 1
        merged[key] = row
    return list(merged.values()), added, updated


def save(db, name, filename, rows, actor):
    """Store an upload as an immutable revision of its batch series.

    - An upload whose rows are already represented by the current head (same
      content, or a subset already present) produces no new revision.
    - Otherwise the upload is merged onto the head and stored as a new
      revision; the head is kept untouched so runs and reports locked to it
      stay reproducible.
    """
    upload_checksum, uploaded_rows = _canonical(rows)
    current = db.scalar(select(Dataset).where(Dataset.name == name)
                        .order_by(Dataset.revision.desc()).limit(1).with_for_update())

    if current is None:
        merged, added, updated = uploaded_rows, len(uploaded_rows), 0
        revision, supersedes = 1, None
    else:
        merged, added, updated = _merge(current.rows, uploaded_rows)
        if not added and not updated:
            return current, False
        revision, supersedes = current.revision + 1, current.id

    analysis.validate_rows(merged)
    snapshot_checksum, ordered = _canonical(merged)
    dataset = Dataset(name=name, filename=filename, checksum=snapshot_checksum,
                      upload_checksum=upload_checksum, revision=revision,
                      supersedes_id=supersedes, added_rows=added, updated_rows=updated,
                      rows=ordered, row_count=len(ordered), author=actor)
    try:
        with db.begin_nested():
            db.add(dataset)
            db.flush()
    except IntegrityError:
        # A concurrent upload produced this revision first.  Reuse the
        # identical snapshot; a different winner means the caller must retry
        # against the new head.
        existing = db.scalar(select(Dataset).where(Dataset.name == name,
                                                   Dataset.checksum == snapshot_checksum))
        if existing:
            return existing, False
        raise ValueError('该批次刚被其他人更新，请刷新后重新导入。') from None
    return dataset, True


def template():
    fields = [field['key'] for field in SPEC['fields']]
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    # The complete representative dataset also works as a reproducible import fixture.
    writer.writerows(demo_rows())
    return '﻿' + buffer.getvalue()
