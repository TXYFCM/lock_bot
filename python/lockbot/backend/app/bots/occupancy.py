"""
Occupancy record model and service — tracks who occupied which node, when, and for how long.

Records are written when a lock is released (manual unlock, auto-expiry, or kickout).
Data older than 8 days is cleaned up automatically on each write.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, Session, mapped_column

from lockbot.backend.app.database import Base, SessionLocal

logger = logging.getLogger(__name__)

# Number of days to retain occupancy records
RETENTION_DAYS = 8


class OccupancyRecord(Base):
    __tablename__ = "occupancy_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    node_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    lock_mode: Mapped[str] = mapped_column(String(16), nullable=False)  # exclusive | shared
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    __table_args__ = ({"sqlite_autoincrement": True},)


def record_occupancy(
    bot_id: int,
    node_key: str,
    user_id: str,
    start_time: int,
    end_time: int,
    lock_mode: str,
) -> None:
    """Write one occupancy record and lazily clean up expired records.

    All parameters are epoch seconds.  Failures are logged and never propagate.
    """
    try:
        db = SessionLocal()
        try:
            _cleanup_old_records(db, bot_id)
            record = OccupancyRecord(
                bot_id=bot_id,
                node_key=node_key,
                user_id=user_id,
                lock_mode=lock_mode,
                start_time=datetime.fromtimestamp(start_time, tz=timezone.utc),
                end_time=datetime.fromtimestamp(end_time, tz=timezone.utc),
                duration_seconds=max(0, end_time - start_time),
            )
            db.add(record)
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.warning(
            "Failed to record occupancy bot=%d node=%s user=%s",
            bot_id,
            node_key,
            user_id,
            exc_info=True,
        )


def _cleanup_old_records(db: Session, bot_id: int) -> None:
    """Delete occupancy records older than RETENTION_DAYS for a given bot."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    try:
        db.query(OccupancyRecord).filter(
            OccupancyRecord.bot_id == bot_id,
            OccupancyRecord.created_at < cutoff,
        ).delete()
    except Exception:
        logger.warning("Failed to clean up old occupancy records", exc_info=True)


def query_occupancy(
    bot_id: int,
    date_str: str | None = None,
    node_key: str | None = None,
) -> list[dict]:
    """Query occupancy records for a bot on a given date (YYYY-MM-DD).

    Returns a list of dicts with keys:
        node_key, user_id, lock_mode, start_time, end_time, duration_seconds.
    """
    db = SessionLocal()
    try:
        q = db.query(OccupancyRecord).filter(OccupancyRecord.bot_id == bot_id)
        if date_str:
            try:
                day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                return []
            next_day = day + timedelta(days=1)
            q = q.filter(
                OccupancyRecord.start_time >= day,
                OccupancyRecord.start_time < next_day,
            )
        if node_key:
            q = q.filter(OccupancyRecord.node_key == node_key)
        records = q.order_by(OccupancyRecord.start_time.asc()).all()
        return [
            {
                "node_key": r.node_key,
                "user_id": r.user_id,
                "lock_mode": r.lock_mode,
                "start_time": r.start_time.isoformat(),
                "end_time": r.end_time.isoformat(),
                "duration_seconds": r.duration_seconds,
            }
            for r in records
        ]
    finally:
        db.close()
