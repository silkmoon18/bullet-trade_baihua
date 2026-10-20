"""Bounded, best-effort execution tracing; never changes a trading result."""
from __future__ import annotations

import contextvars
import functools
import inspect
import itertools
import json
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

_current = contextvars.ContextVar('bt_execution_timing', default=None)
_archive_sink = None
_process_instance = uuid.uuid4().hex[:12]
_previous_output_ms = 0.0
_trace_ids = itertools.count(1)


def set_archive_sink(sink):
    global _archive_sink
    previous, _archive_sink = _archive_sink, sink
    return previous


def archive_sink():
    return _archive_sink


def print_timing(message, *args):
    """Diagnostics only: bypass application/file log handlers; never affect trading."""
    global _previous_output_ms
    started = time.perf_counter()
    try:
        line = '{} {}'.format(Trace.wall(), message % args if args else message)
        if _archive_sink is not None:
            try:
                _archive_sink.append('timing', line)
            except Exception:
                pass
        try:
            print(line)
        except Exception:
            pass
    except Exception:
        pass
    finally:
        _previous_output_ms = (time.perf_counter() - started) * 1000


class Trace:
    def __init__(self, phase, trace_id=''):
        self.phase, self.trace_id = phase, str(trace_id)[:200]
        self.started, self.started_at = time.perf_counter(), self.wall()
        self.events, self.totals = [], {}
        self.events_dropped = 0
        parent = current_trace()
        self.span_id = '{}:{}'.format(_process_instance, next(_trace_ids))
        self.parent_span_id = parent.span_id if parent else None
        self.closed = False
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
            self.events.append(dict(stage=stage, ms=round(elapsed, 3), at=self.wall(),
                                    start_offset_ms=round((started-self.started)*1000, 3),
                                    finish_offset_ms=round((time.perf_counter()-self.started)*1000, 3), **fields))
        else:
            self.events_dropped += 1

    def summary(self):
        return dict(trace_id=self.trace_id, phase=self.phase, started_at=self.started_at,
                    process_instance=_process_instance, previous_timing_output_ms=round(_previous_output_ms, 3),
                    span_id=self.span_id, parent_span_id=self.parent_span_id, events_dropped=self.events_dropped,
                    finished_at=self.wall(), elapsed_ms=round((time.perf_counter()-self.started)*1000, 3),
                    tags=self.tags, stages={key: {k: round(v, 3) if isinstance(v, float) else v for k, v in value.items()}
                            for key, value in self.totals.items()}, events=self.events)

    def flush(self):
        self.closed = True
        if self.tags.get('had_active_intent') is False:
            return  # Don't log every idle account/position heartbeat.
        try:
            print_timing('执行链路耗时 | %s', json.dumps(self.summary(), ensure_ascii=False, allow_nan=False))
        except Exception:
            pass


def current_trace():
    trace = _current.get()
    return trace if trace is not None and not trace.closed else None


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
