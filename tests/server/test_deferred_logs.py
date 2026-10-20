"""Diagnostic buffering must never become trading-path disk I/O."""
import asyncio
from datetime import datetime, timedelta, timezone
import io
import logging
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from bullet_trade.server import timing
from bullet_trade.server.app import ServerApplication
from bullet_trade.server.deferred_logs import DeferredLogStore, DeferredFileHandler, ServerLogCapture


NOW = datetime(2026, 10, 22, 9, 30, tzinfo=timezone(timedelta(hours=8)))


def test_intraday_append_trace_and_flush_never_touch_disk(tmp_path, monkeypatch):
    store = DeferredLogStore(tmp_path / 'server.log', clock=lambda: NOW)
    previous = timing.set_archive_sink(store)
    def forbidden(*a, **k):
        pytest.fail('No intraday diagnostic file I/O')
    try:
        with monkeypatch.context() as patch:
            patch.setattr(Path, 'open', forbidden)
            patch.setattr(Path, 'mkdir', forbidden)
            handler = DeferredFileHandler(store)
            handler.emit(logging.LogRecord('test', logging.INFO, '', 0, 'business event', (), None))
            trace = timing.Trace('test.background', 'intent-key')
            trace.record('local.plan', trace.started)
            trace.flush()
            assert not store.flush_ready()
        assert len(store.pending) == 2
        assert store.tail(10) == ['business event']
        assert not list(tmp_path.iterdir())
    finally:
        timing.set_archive_sink(previous)


@pytest.mark.parametrize('hour,minute', [(8, 50), (9, 26), (9, 30), (11, 30), (13, 0), (15, 9)])
def test_lunch_and_boundaries_do_not_flush(tmp_path, hour, minute):
    store = DeferredLogStore(tmp_path/'server.log')
    store.append('timing', 'pending', NOW)
    assert not store.flush_ready(NOW.replace(hour=hour, minute=minute))
    assert len(store.pending) == 1 and not list(tmp_path.iterdir())


def test_after_close_preserves_original_day_and_does_not_duplicate(tmp_path):
    store = DeferredLogStore(tmp_path/'server.log', clock=lambda: NOW)
    store.append('business', 'business event')
    store.append('timing', 'timing event')
    assert store.flush_ready(NOW.replace(hour=15, minute=10))
    assert store.path_for('business', '20261022').read_text(encoding='utf8') == 'business event\n'
    assert store.path_for('timing', '20261022').read_text(encoding='utf8') == 'timing event\n'
    assert not store.flush_ready(NOW.replace(day=23, hour=16))
    assert len(list(tmp_path.iterdir())) == 2 and not store.pending


def test_overflow_is_bounded_and_explicit_after_close(tmp_path):
    store = DeferredLogStore(tmp_path/'server.log', max_records=2, max_chars=20, clock=lambda: NOW)
    for i in range(10):
        store.append('timing', 'entry' + str(i))
    assert len(store.pending) == 2 and store.dropped == 8 and store.chars <= 20
    assert store.flush_ready(NOW.replace(hour=16))
    saved = store.path_for('timing', '20261022').read_text(encoding='utf8')
    assert 'entry8' in saved and 'entry9' in saved and 'dropped_records=8' in saved


def test_file_error_keeps_pending_without_affecting_next_order(tmp_path, monkeypatch):
    store = DeferredLogStore(tmp_path/'server.log', clock=lambda: NOW)
    store.append('timing', 'observed event')
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', lambda *a, **k: (_ for _ in ()).throw(OSError('disk unavailable')))
        assert not store.flush_ready(NOW.replace(hour=16))
    assert len(store.pending) == 1
    assert store.flush_ready(NOW.replace(hour=16))
    assert store.path_for('timing', '20261022').read_text(encoding='utf8') == 'observed event\n'


@pytest.mark.asyncio
async def test_server_capture_restores_handlers_and_does_not_force_intraday_exit_write(tmp_path, monkeypatch):
    logger = logging.Logger('isolated-server-test', logging.INFO)
    original = logging.StreamHandler(io.StringIO())
    logger.addHandler(original)
    host = NS(logger=logger, _file_handler=original, _sync_standard_logger=lambda: None)
    capture = ServerLogCapture(host, tmp_path/'server.log')
    capture.store.clock = lambda: NOW
    previous = timing.archive_sink()
    async with capture:
        logger.info('accepted in memory')
        timing.print_timing('background event')
        await asyncio.sleep(0)
        assert capture.store.tail(1) == ['accepted in memory']
        assert not list(tmp_path.iterdir())
    assert not list(tmp_path.iterdir())
    assert logger.handlers == [original] and host._file_handler is original
    assert timing.archive_sink() is previous


def test_archive_failure_still_allows_console_output(monkeypatch, capsys):
    previous = timing.set_archive_sink(NS(append=lambda *a: (_ for _ in ()).throw(OSError('memory unavailable'))))
    try:
        timing.print_timing('still visible')
        assert 'still visible' in capsys.readouterr().out
    finally:
        timing.set_archive_sink(previous)


def test_dashboard_uses_memory_without_reading_disk_when_tail_is_available(tmp_path, monkeypatch):
    store = DeferredLogStore(tmp_path/'server.log', clock=lambda: NOW)
    store.append('business', 'first')
    store.append('business', 'second')
    previous = timing.set_archive_sink(store)
    try:
        monkeypatch.setattr('bullet_trade.server.app.tail_log_file',
                            lambda *a: pytest.fail('In-memory tail must not read files'))
        assert ServerApplication._recent_server_logs(NS(), 1) == ['second']
        assert ServerApplication._recent_server_logs(NS(), 0) == []
    finally:
        timing.set_archive_sink(previous)


@pytest.mark.asyncio
async def test_server_cli_installs_deferred_capture_instead_of_realtime_file_handler(tmp_path, monkeypatch):
    from bullet_trade.server import cli
    logger = logging.Logger('isolated-cli-test', logging.INFO)
    host = NS(logger=logger, _file_handler=None, _sync_standard_logger=lambda: None,
              info=logger.info, warning=logger.warning)
    config = NS(log_file=str(tmp_path/'server.log'), accounts=(), server_type='fake', generated_token=False)
    captures = []
    def capture_factory(logger, path):
        capture = ServerLogCapture(logger, path)
        capture.store.clock = lambda: NOW
        captures.append(capture)
        return capture
    class Application:
        def __init__(self, **kw):
            pass
        async def start(self):
            assert timing.archive_sink() is captures[0].store
            host.info('CLI business event')
            timing.print_timing('CLI background trace')
    monkeypatch.setattr(cli, 'log', host)
    monkeypatch.setattr(cli, 'build_server_config', lambda args: config)
    monkeypatch.setattr(cli, 'AccountRouter', lambda accounts: object())
    monkeypatch.setattr(cli, 'get_adapter', lambda kind: lambda config, router: object())
    monkeypatch.setattr(cli, 'ServerApplication', Application)
    monkeypatch.setattr(cli, '_install_signal_handlers', lambda app: None)
    monkeypatch.setattr(cli, 'ServerLogCapture', capture_factory)
    assert await cli._async_main(NS(env_file=None)) == 0
    assert not list(tmp_path.iterdir())
    assert len(captures[0].store.tail(1)) == 1
    assert captures[0].store.tail(1)[0].endswith('CLI business event')
