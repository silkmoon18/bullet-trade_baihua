"""Bounded, best-effort execution tracing; never changes a trading result."""
from __future__ import annotations

import contextvars
import functools
import inspect
import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

_current = contextvars.ContextVar('bt_execution_timing', default=None)


def print_timing(message, *args):
    """Diagnostics only: bypass application/file log handlers; never affect trading."""
    try:
        print('{} {}'.format(Trace.wall(), message % args if args else message))
    except Exception:
        pass


class Trace:
    def __init__(self, phase, trace_id=''):
        self.phase, self.trace_id = phase, str(trace_id)[:200]
        self.started, self.started_at = time.perf_counter(), self.wall()
        self.events, self.totals = [], {}
        self.tags = {}

    @staticmethod
    def wall():
        return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='milliseconds')

    def record(self, stage, started, **fields):
        elapsed = max(0., (time.perf_counter() - started) * 1000)
        total = self.totals.setdefault(stage, {'calls': 0, 'ms': 0., 'max_ms': 0.})
        total.update(calls=total['calls'] + 1, ms=total['ms'] + elapsed,
                     max_ms=max(total['max_ms'], elapsed))
        if len(self.events) < 128:
            self.events.append(dict(stage=stage, ms=round(elapsed, 3), at=self.wall(), **fields))

    def summary(self):
        return dict(trace_id=self.trace_id, phase=self.phase, started_at=self.started_at,
                    finished_at=self.wall(), elapsed_ms=round((time.perf_counter()-self.started)*1000, 3),
                    tags=self.tags, stages={key: {k: round(v, 3) if isinstance(v, float) else v for k, v in value.items()}
                            for key, value in self.totals.items()}, events=self.events)

    def flush(self):
        if self.tags.get('had_active_intent') is False:
            return  # Don't log every idle account/position heartbeat.
        try:
            print_timing('执行链路耗时 | %s', json.dumps(self.summary(), ensure_ascii=False, allow_nan=False))
        except Exception:
            pass


def current_trace():
    return _current.get()


def record_elapsed(stage, started):
    trace = current_trace()
    if trace is not None:
        try:
            trace.record(stage, started)
        except Exception:
            pass


@contextmanager
def span(stage):
    trace, started = current_trace(), time.perf_counter()
    outcome = 'ok'
    try:
        yield
    except BaseException:
        outcome = 'error'
        raise
    finally:
        if trace is not None:
            try:
                trace.record(stage, started, outcome=outcome)
            except Exception:
                pass


def measured(stage):
    def decorate(fn):
        @functools.wraps(fn)
        async def wrapped(*args, **kwargs):
            with span(stage):
                return await fn(*args, **kwargs)
        return wrapped
    return decorate


def traced(phase):
    def decorate(fn):
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        async def wrapped(*args, **kwargs):
            bound = signature.bind(*args, **kwargs).arguments
            payload = bound.get('payload') or {}
            identity = payload.get('trace_id') or '{}:{}'.format(
                payload.get('strategy_id', ''), payload.get('idempotency_key', bound.get('intent_id', '')))
            trace = Trace(phase, identity)
            token = _current.set(trace)
            try:
                with span(phase):
                    result = await fn(*args, **kwargs)
                if isinstance(result, dict):
                    try:
                        result = dict(result, _bt_server_timing={k: v for k, v in trace.summary().items() if k != 'events'})
                    except Exception:
                        pass
                return result
            finally:
                _current.reset(token)
                trace.flush()
        return wrapped
    return decorate
