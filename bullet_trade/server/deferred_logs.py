"""Bounded diagnostic memory, persisted only outside the trading window.

This is not the financial ledger. A crash or intraday restart can lose pending
diagnostics; order/fill/idempotency persistence must never use this buffer.
"""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import threading
from typing import Optional


def china_now():
    return datetime.now(timezone(timedelta(hours=8)))


def archive_allowed(now):
    # No file I/O even during lunch; the gate is independent of host timezone.
    return now.strftime('%H:%M') < '08:50' or now.strftime('%H:%M') >= '15:10'


class DeferredLogStore:
    def __init__(self, business_path, *, max_records=20000,
                 max_chars=16 * 1024 * 1024, clock=china_now):
        self.business_path = Path(business_path).absolute()
        self.clock, self.max_records, self.max_chars = clock, max_records, max_chars
        self.pending, self.chars = deque(), 0
        self.dropped, self.saved_drop_count = 0, 0
        self.guard, self.writer_guard = threading.Lock(), threading.Lock()

    def path_for(self, kind, day):
        base = self.business_path
        if kind == 'timing':
            return base.parent / ('execution-timing-' + day + '.log')
        return base.with_name(base.stem + '-' + day + base.suffix)

    def append(self, kind, line, now=None):
        """Only bounded memory work: no filesystem, logger, network or QMT API."""
        try:
            text = str(line)
            with self.guard:
                if len(text) > self.max_chars:
                    self.dropped += 1
                    return
                self.pending.append((kind, (now or self.clock()).strftime('%Y%m%d'), text))
                self.chars += len(text)
                while len(self.pending) > self.max_records or self.chars > self.max_chars:
                    self.chars -= len(self.pending.popleft()[2])
                    self.dropped += 1
        except Exception:
            pass  # Diagnostics never affect an order/fill result.

    def tail(self, limit):
        with self.guard:
            return [row[2] for row in self.pending if row[0] == 'business'][-limit:]

    def flush_ready(self, now=None):
        """Worker-thread operation, after close only; failures keep unsaved rows."""
        now = now or self.clock()
        if not archive_allowed(now) or not self.writer_guard.acquire(blocking=False):
            return False
        try:
            with self.guard:
                batch = list(self.pending)
                self.pending.clear()
                self.chars = 0
                missing = self.dropped - self.saved_drop_count
            groups = {}
            for kind, day, line in batch:
                groups.setdefault((kind, day), []).append(line)
            if missing:
                groups.setdefault(('timing', now.strftime('%Y%m%d')), []).append(
                    now.isoformat() + ' log_archive_gap | dropped_records=' + str(missing))
            successful = set()
            for (kind, day), lines in groups.items():
                try:
                    path = self.path_for(kind, day)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open('a', encoding='utf-8', errors='backslashreplace') as stream:
                        stream.write('\n'.join(lines) + '\n')
                    successful.add((kind, day))
                except OSError:
                    # A partial append can be repeated. These are diagnostics,
                    # never used to count trades or advance financial state.
                    continue
            with self.guard:
                failed = [row for row in batch if row[:2] not in successful]
                self.pending.extendleft(reversed(failed))
                self.chars += sum(len(row[2]) for row in failed)
                while len(self.pending) > self.max_records or self.chars > self.max_chars:
                    self.chars -= len(self.pending.popleft()[2])
                    self.dropped += 1
                if ('timing', now.strftime('%Y%m%d')) in successful:
                    self.saved_drop_count += missing
            return bool(successful)
        finally:
            self.writer_guard.release()


class DeferredFileHandler(logging.Handler):
    def __init__(self, store):
        super().__init__()
        self.store = store
        self.baseFilename = str(store.business_path)

    def emit(self, record):
        try:
            self.store.append('business', self.format(record))
        except Exception:
            pass

    # logging.shutdown/flush/close must not force disk I/O during trading.


class ServerLogCapture:
    """Own the server-only sink; do not change backtest/live CLI logging."""
    def __init__(self, logger, path=None):
        old = getattr(logger, '_file_handler', None)
        target = path or getattr(old, 'baseFilename', None) or '.data/logs/server.log'
        self.logger, self.previous_handler = logger, old
        self.store = DeferredLogStore(target)
        self.handler = DeferredFileHandler(self.store)
        self.handler.setLevel(old.level if old else logging.INFO)
        self.handler.setFormatter(old.formatter if old else logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        self.task: Optional[asyncio.Task] = None
        self.previous_sink = None

    async def __aenter__(self):
        from . import timing
        self.previous_sink = timing.set_archive_sink(self.store)
        if self.previous_handler is not None:
            self.logger.logger.removeHandler(self.previous_handler)
        self.logger.logger.addHandler(self.handler)
        self.logger._file_handler = self.handler
        self.logger._sync_standard_logger()
        self.task = asyncio.create_task(self._archive_loop(), name='after-close-log-archive')
        return self

    async def _archive_loop(self):
        while True:
            if archive_allowed(self.store.clock()):
                await asyncio.get_running_loop().run_in_executor(None, self.store.flush_ready)
            await asyncio.sleep(60)

    async def __aexit__(self, *args):
        from . import timing
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        # Intraday shutdown deliberately does NOT flush. Make the limitation
        # visible on stdout rather than silently adding hot-path file writes.
        if archive_allowed(self.store.clock()):
            await asyncio.get_running_loop().run_in_executor(None, self.store.flush_ready)
        elif self.store.pending:
            timing.print_timing('log_archive_pending | records=%s | intraday shutdown; memory diagnostics may be lost',
                                len(self.store.pending))
        timing.set_archive_sink(self.previous_sink)
        self.logger.logger.removeHandler(self.handler)
        if self.previous_handler is not None:
            self.logger.logger.addHandler(self.previous_handler)
        self.logger._file_handler = self.previous_handler
        self.logger._sync_standard_logger()
        self.handler.close()
