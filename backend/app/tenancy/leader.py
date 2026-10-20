"""Scheduler leader election (docs/PLATFORM_CONTRACT.md 6.2).

Multi mode: only the process holding ``pg_try_advisory_lock(LEADER_LOCK_KEY)``
on a dedicated platform-database connection runs the schedulers, so two web
processes never run a job twice. A watchdog thread pings that connection;
losing it calls ``on_lost`` (stop the schedulers) and goes back to trying to
become leader. Single mode: no lock (always leader, as before).
"""

import logging
import threading
from typing import Callable, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)

LEADER_LOCK_KEY = 0x53544F5245  # "STORE"


class Leadership:
    """One attempt to hold the leader lock on `url`."""

    def __init__(self, url: str, key: int = LEADER_LOCK_KEY):
        self.url = url
        self.key = key
        self._engine = None
        self._conn = None

    @property
    def held(self) -> bool:
        return self._conn is not None

    def try_acquire(self) -> bool:
        if self._conn is not None:
            return True
        from app.tenancy.engines import postgres_connect_args

        if self._engine is None:
            kwargs = {"poolclass": NullPool}
            if self.url.startswith("postgresql"):
                # A session connection (the advisory lock lives on it): the
                # direct URL, never through PgBouncer's transaction pool.
                kwargs["connect_args"] = postgres_connect_args(direct=True)
            self._engine = create_engine(self.url, **kwargs).execution_options(isolation_level="AUTOCOMMIT")
        conn = self._engine.connect()
        try:
            got = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": self.key}).scalar())
        except Exception:
            conn.close()
            raise
        if not got:
            conn.close()
            return False
        self._conn = conn
        return True

    def alive(self) -> bool:
        if self._conn is None:
            return False
        try:
            self._conn.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001 - any failure means the lock is gone
            self._drop()
            return False

    def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def release(self) -> None:
        if self._conn is not None:
            try:
                self._conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": self.key})
            except Exception:  # noqa: BLE001
                pass
        self._drop()
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None


class LeaderLoop:
    """Keeps trying to be leader; calls on_elected / on_lost."""

    def __init__(
        self,
        url: str,
        on_elected: Callable[[], None],
        on_lost: Callable[[], None],
        interval: float = 30.0,
        key: int = LEADER_LOCK_KEY,
    ):
        self.leadership = Leadership(url, key)
        self.on_elected = on_elected
        self.on_lost = on_lost
        self.interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def is_leader(self) -> bool:
        return self.leadership.held

    def tick(self) -> None:
        if self.leadership.held:
            if not self.leadership.alive():
                logger.error("Scheduler leader lock lost - stopping the schedulers")
                try:
                    self.on_lost()
                except Exception:  # noqa: BLE001
                    logger.exception("on_lost failed")
            return
        try:
            got = self.leadership.try_acquire()
        except Exception:  # noqa: BLE001 - the platform DB is down: try again later
            logger.exception("Scheduler leader election failed")
            return
        if got:
            logger.info("This process is the scheduler leader")
            try:
                self.on_elected()
            except Exception:  # noqa: BLE001
                logger.exception("on_elected failed")

    def start(self) -> None:
        self.tick()
        self._thread = threading.Thread(target=self._run, name="scheduler-leader", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.tick()

    def stop(self) -> None:
        self._stop.set()
        if self.leadership.held:
            try:
                self.on_lost()
            except Exception:  # noqa: BLE001
                logger.exception("on_lost failed")
        self.leadership.release()
