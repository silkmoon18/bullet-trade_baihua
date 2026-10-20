# encoding:gbk
"""GoodETF\uff1a\u72ec\u7acb\u5927 QMT \u5185\u7f6e Python \u5355\u6587\u4ef6\u7b56\u7565\uff082026-10-15\uff09\u3002

\u76f4\u63a5\u7f16\u8f91\u672c\u6587\u4ef6\u9876\u90e8\u914d\u7f6e\uff0c\u7136\u540e\u5168\u9009\u590d\u5236\u5230 QMT Python \u7b56\u7565\u7f16\u8f91\u5668\u3002
\u4e0d\u4f9d\u8d56\u805a\u5bbd\u3001BulletTrade\u670d\u52a1\u5668\u3001bridge\u3001MiniQMT\u3001xtquant\u6216\u672c\u5730\u8f85\u52a9\u6a21\u5757\u3002
\u5df2\u6309\u7528\u6237\u6388\u6743\u6539\u4e3a1000\u5143\u7b56\u7565\u8d44\u91d1\u3001\u5b9e\u9645\u5411\u5f53\u524d\u9009\u62e9\u7684QMT\u8d26\u6237\u63d0\u4ea4\u59d4\u6258\u3002
\u5df2\u6709\u805a\u5bbd/\u670d\u52a1\u5668\u7248\u672c\u4e0d\u53d7\u5f71\u54cd\uff1b\u4ea4\u6613\u4e0e\u56de\u6d4b\u63a5\u53e3\u80fd\u529b\u4ecd\u987b\u5b9e\u9645\u9a8c\u8bc1\u3002
"""

# ETF开盘折价轮动_qmt；内部ID和现有账本不变。
# ==================== 用户配置：只需修改此区域 ====================

# ---- 账户、交易开关和策略归属资金 ----
ACCOUNT_ID = ""  # Use the account selected in QMT, or fill locally; never commit it.
STRATEGY_ID = "good_etf_big_qmt" # 同一账户只运行一个本策略实例，不与旧 remote 同时交易
STRATEGY_NAME = "ETF\u5f00\u76d8\u6298\u4ef7\u8f6e\u52a8_qmt"  # 展示名，不参与账本路径或订单归属
ENABLE_TRADING = True      # 实际向QMT当前绑定账户下单；需先停止同账户可能并发下单的旧策略
INITIAL_CAPITAL = 1000.0   # 仅交易模式使用；只选股不读取资金。已有账本不能改数字来重置
PAPER_TRADING = False      # 纸面模式关闭；仅在ENABLE_TRADING=False时才生效
PAPER_INITIAL_CAPITAL = 5000.0  # 仅内存虚拟资金；重启重置，不读/改任何QMT账户或真实账本
STATE_DIR = r"C:\qmt_good_etf_big_qmt_data"  # 客户端上的私有账本目录；不得与旧原生版/服务器共用

# ---- 数据与通知 ----
ETF_SECTOR = "\u6caa\u6df1ETF"      # 客户端全市场 ETF 板块名，请核对成员范围
NAV_FILE = ""              # 实时可选兜底：先读QMT PCF，缺失时再读前一交易日净值JSON
ALLOW_UNDATED_LIVE_NAV = True  # 实盘接受日期缺失的有效QMT nav并警告；仍需每天在QMT更新PCF。False=严格日期校验
AUTO_DOWNLOAD_DAILY = True   # 仅盘前分批补缺失日线；历史回测和开盘执行不下载
PREPARE_RETRY_SECONDS = 30  # 临时数据错误的准备重试间隔；接口不兼容当天不反复重试
DAILY_DOWNLOAD_START = "09:10"
DAILY_DOWNLOAD_CUTOFF = "09:25"  # 到点立即停止安排新下载，不延后09:30决策
DAILY_DOWNLOAD_BATCH = 10    # 每次timer最多补这些标的，单标的当天只提交一次下载
DAILY_DOWNLOAD_BUDGET_MS = 200  # 批次软预算；不能中断QMT正在执行的单次原生调用
BACKTEST_DATA_FILE = ""    # 回测可选兜底：留空先尝试QMT；缺少历史ETF池/前日净值/涨停价时再填JSON
TRACKED_INDEX_NAMES = {}    # 可选：{"510300.SH": "沪深300"}，补充跟踪指数名称
FEISHU_WEBHOOK = ""        # 可选飞书机器人 webhook；空值关闭通知，不要公开凭据

# ---- 选股与风控：保留原 GoodETF 参数及严格比较边界 ----
MAX_HOLD_NUM = 2           # 折价最深的前 N 只
MIN_MONEY = 5e6            # 前一交易日成交额严格大于 500 万元
MAX_MONEY = 2e7            # 前一交易日成交额严格小于 2000 万元
STOP_LOSS_RATIO = 0.95     # 现价严格低于策略持仓成本的 95% 时止损
TAKE_PROFIT_RATIO = 1.10   # 现价严格高于策略持仓成本的 110% 时止盈
DEPLOY_RATIO = 0.95        # 目标权重合计 95%，保留现金缓冲
SKIP_SUSPENDED_LIMITUP = True  # True=选股前排除停牌/涨停；False=不做此筛选
HK_WORDS = ("\u6e2f\u80a1", "\u6052\u751f", "H\u80a1", "\u9999\u6e2f", "\u6e2f\u80a1\u901a", "\u6caa\u6e2f\u6df1", "\u6caa\u6df1\u6e2f", "HK", "\u6052\u79d1", "\u6e2f\u7ea2\u5229")
HK_CODES = {"520590.SH", "520890.SH", "513320.SH", "159322.SZ"}  # 港股属性明确的代码兜底表

# ---- 决策时间：中国时区，格式 HH:MM ----
PREPARE_TIME = "09:20"      # 盘前准备；缺失时在开盘决策现场补跑
OPEN_TIME = "09:30"         # 每日选股调仓，不在错过该分钟后补做开盘决策
OPEN_QUOTE_WAIT_SECONDS = 10.0  # 从 OPEN_TIME 的00秒起最多等多久，须大于0小于60；超时不调仓
OPEN_QUOTE_LOG_SECONDS = 2.0    # 等待信息汇总间隔，不逐个刷缺失标的；只print
QUOTE_FUTURE_TOLERANCE_SECONDS = 2.0  # 允许行情时间领先本机的秒数，超出视为时钟异常
RISK_CHECK_TIMES = ("10:30", "13:30", "14:50")
SNAPSHOT_TIME = "14:55"     # 组合/净值/费用快照

# ---- 委托执行：参考价保持固定，ETF 不做股票价格笼子等待 ----
BUY_PREMIUM = 0.002        # 买入上限=开盘决策 lastPrice * 1.002，不使用卖一追价
PROFIT_DISCOUNT = 0.002    # 止盈卖出下限=触发时 lastPrice * 0.998
MARKET_PROTECTION = 0.015 # 调仓卖出/止损的保护距离=参考价的 1.5%
SH_NATIVE_MARKET = True   # True=沪市原生五档即成剩撤；False=保护价固定限价
SZ_NATIVE_MARKET = True   # True=深市原生五档即成剩撤；False=保护价限价
# 42/47需券商客户端支持；拒单会报告，不自动改下单类型。
QUERY_SECONDS = 5         # 委托/成交查询补漏间隔，单位秒；不是行情 tick 频率
RETRY_SECONDS = 3         # 确认剩余已撤且成交入账后，续单最小间隔（秒）
CASH_RESERVE = 5.0        # 每笔活动买单预留金额（元）；不记作手续费

# ---- 诊断日志：北京时间定点后台归档，不改变交易账本即时持久化 ----
LOG_BUFFER_MAX_CHARS = 8 * 1024 * 1024  # 内存日志上限；超限报告丢弃数量
LOG_ARCHIVE_TIMES = ("00:00", "06:00", "09:00", "10:00", "12:00", "14:00", "16:00", "20:00")

# ==================== 配置结束：以下通常无需修改 ====================

import hashlib
import json
import math
import os
import time
import uuid
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
import builtins
import datetime as dt
from typing import NamedTuple
import pandas as pd
import threading
import urllib.request
import functools
import sys
from contextlib import contextmanager
from collections import Counter, deque


_native_timing = threading.local()
_native_process_instance = uuid.uuid4().hex[:12]
_native_previous_output_ms = None


class NativeLogBuffer:
    """Diagnostics only. Scheduled file workers never call QMT or touch a ledger."""
    def __init__(self, max_chars):
        self.rows, self.chars, self.dropped = deque(), 0, 0
        self.max_chars = max(1024, int(max_chars))
        self.guard, self.writing, self.last_attempt = threading.Lock(), False, float('-inf')
        self.archive_slot = None

    def append(self, line, now):
        with self.guard:
            if len(line) > self.max_chars:
                self.dropped += 1
                return
            self.rows.append((now.strftime('%Y%m%d'), line))
            self.chars += len(line)
            while self.chars > self.max_chars or len(self.rows) > 20000:
                self.chars -= len(self.rows.popleft()[1])
                self.dropped += 1

    def request_flush(self, prefix, now, archive_times):
        times = sorted(set(archive_times))
        if not times:
            return False
        clock = now.strftime('%H:%M')
        elapsed = [point for point in times if point <= clock]
        day = now if elapsed else now - dt.timedelta(days=1)
        slot = day.strftime('%Y%m%d') + ':' + (elapsed[-1] if elapsed else times[-1])
        with self.guard:
            if self.archive_slot is None:
                # A mid-interval restart waits for the next point, not an opening flush.
                self.archive_slot = '' if clock in times else slot
            if slot <= self.archive_slot:
                return False
            if self.writing:
                return False
            if not self.rows and not self.dropped:
                self.archive_slot = slot
                return False
            if time.monotonic()-self.last_attempt < 60:
                return False
            self.writing, self.last_attempt = True, time.monotonic()
        try:
            threading.Thread(target=self._write, args=(prefix, now, slot), daemon=True).start()
        except Exception:
            with self.guard:
                self.writing = False
            return False
        return True

    def _write(self, prefix, now, slot):
        with self.guard:
            batch, missing = list(self.rows), self.dropped
            self.rows.clear()
            self.chars, self.dropped = 0, 0
        grouped, saved = {}, set()
        for day, line in batch:
            grouped.setdefault(day, []).append(line)
        if missing:
            grouped.setdefault(now.strftime('%Y%m%d'), []).append(
                '[log_archive_gap] dropped_records=' + str(missing))
        try:
            for day, lines in grouped.items():
                try:
                    path = prefix + '.' + day + '.log'
                    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
                    with open(path, 'a', encoding='utf-8', errors='backslashreplace') as stream:
                        stream.write('\n'.join(lines) + '\n')
                    saved.add(day)
                except OSError:
                    try:
                        print('[log_archive] save failed; pending memory retained')
                    except Exception:
                        pass
            with self.guard:
                failed = [(day, line) for day, line in batch if day not in saved]
                self.rows.extendleft(reversed(failed))
                self.chars += sum(len(line) for _, line in failed)
                if now.strftime('%Y%m%d') not in saved:
                    self.dropped += missing
                if len(saved) == len(grouped):
                    self.archive_slot = slot
                while self.chars > self.max_chars or len(self.rows) > 20000:
                    self.chars -= len(self.rows.popleft()[1])
                    self.dropped += 1
        finally:
            with self.guard:
                self.writing = False


def buffer_native_line(runtime, line, now):
    try:
        buffer = getattr(runtime, '_log_buffer', None)
        if buffer is None:
            buffer = runtime._log_buffer = NativeLogBuffer(
                getattr(runtime.settings, 'LOG_BUFFER_MAX_CHARS', 8*1024*1024))
        buffer.append(line, now)
    except Exception:
        pass


def maybe_archive_native_logs(runtime):
    try:
        buffer = getattr(runtime, '_log_buffer', None)
        if buffer is None:
            return
        if hasattr(runtime, 'executor'):
            prefix = runtime.executor.path
        else:
            prefix = os.path.join(runtime.settings.STATE_DIR,
                                 'paper' if runtime.paper is not None else 'selection')
        buffer.request_flush(prefix, china_now(), getattr(runtime.settings, 'LOG_ARCHIVE_TIMES',
                             ('00:00', '06:00', '09:00', '10:00', '12:00', '14:00', '16:00', '20:00')))
    except Exception:
        pass


def archive_after_callback(fn):
    @functools.wraps(fn)
    def wrapped(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        finally:
            maybe_archive_native_logs(self)
            if fn.__name__ == 'close':
                buffer = getattr(self, '_log_buffer', None)
                if buffer is not None and buffer.rows and not buffer.writing:
                    try:
                        print('[log_archive_pending] records={} | intraday restart may lose unsaved diagnostics'.format(len(buffer.rows)))
                    except Exception:
                        pass
    return wrapped


@contextmanager
def native_guard(lock):
    with native_span('local.runtime_guard_wait'):
        lock.acquire()
    try:
        yield
    finally:
        lock.release()


def print_timing_only(text, line):
    """Return True for optional timing output, with no file/notification writes."""
    if not text.startswith(("\u6267\u884c\u94fe\u8def\u8017\u65f6 | ",
                            "\u6267\u884c\u56de\u62a5\u8017\u65f6 | ",
                            "\u5b9e\u5355\u8017\u65f6 | ",
                            "\u7eb8\u9762\u8017\u65f6 | ", "[opening_quotes]", "[quote_status]")):
        return False
    try:
        print(line)
    except Exception:
        pass  # Diagnostic output must not change an order or fill result.
    return True


class TimingTrace:
    def __init__(self, phase, trace_id):
        self.phase, self.trace_id = phase, trace_id
        self.started, self.started_at = time.perf_counter(), china_now().isoformat(timespec="milliseconds")
        self.stages = {}
        self.events = []
        self.events_dropped = 0

    def record(self, stage, started, outcome="ok"):
        elapsed = max(0., (time.perf_counter()-started)*1000)
        row = self.stages.setdefault(stage, dict(calls=0, ms=0., max_ms=0., errors=0))
        row.update(calls=row['calls']+1, ms=row['ms']+elapsed,
                   max_ms=max(row['max_ms'], elapsed), errors=row['errors']+(outcome != 'ok'))
        row.setdefault('first_finished_at', china_now().isoformat(timespec='milliseconds'))
        row['last_finished_at'] = china_now().isoformat(timespec='milliseconds')
        if len(self.events) < 128:
            self.events.append(dict(stage=stage, ms=elapsed, outcome=outcome,
                                    start_offset_ms=(started-self.started)*1000,
                                    finish_offset_ms=(time.perf_counter()-self.started)*1000))
        else:
            self.events_dropped += 1

    def message(self):
        return "\u6267\u884c\u94fe\u8def\u8017\u65f6 | " + json.dumps(dict(
            trace_id=self.trace_id, phase=self.phase, started_at=self.started_at,
            finished_at=china_now().isoformat(timespec="milliseconds"),
            elapsed_ms=(time.perf_counter()-self.started)*1000, stages=self.stages,
            events=self.events, events_dropped=self.events_dropped, process_instance=_native_process_instance,
            previous_timing_output_ms=_native_previous_output_ms), ensure_ascii=False)


@contextmanager
def native_span(stage):
    trace, started = getattr(_native_timing, 'current', None), time.perf_counter()
    outcome = 'ok'
    try:
        yield
    except BaseException:
        outcome = 'error'
        raise
    finally:
        if trace is not None:
            try:
                trace.record(stage, started, outcome)
            except Exception:
                pass


def native_measured(stage):
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            with native_span(stage):
                return fn(*args, **kwargs)
        return wrapped
    return decorate


def native_traced(phase):
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(self, *args, **kwargs):
            global _native_previous_output_ms
            if phase == 'native.timer':
                now = kwargs.get('now') or (args[0] if args else None) or china_now()
                today, clock = now.strftime('%Y%m%d'), now.strftime('%H:%M')
                state = self.executor.state
                times = getattr(self, 'times', ())
                scheduled = bool(times) and (clock in (times[0], times[1], times[3]) or clock in times[2])
                pending = any(not settled(order) for order in state['orders'].values()) or any(
                    target['day'] == today and target['qty'] != state['positions'].get(code, {}).get('qty', 0)
                    for code, target in state['targets'].items())
                if today == self.day and not (scheduled or ('09:15' <= clock <= '15:01' and pending)):
                    return fn(self, *args, **kwargs)
            if getattr(_native_timing, 'current', None) is not None:
                with native_span(phase):
                    return fn(self, *args, **kwargs)
            trace = TimingTrace(phase, self.settings.STRATEGY_ID + ':open-' + china_now().strftime('%Y%m%d'))
            _native_timing.current = trace
            try:
                with native_span(phase):
                    return fn(self, *args, **kwargs)
            finally:
                _native_timing.current = None
                # No per-second idle/tick flood. Every actual native API and
                # durable state write remains visible while executing work.
                if any(key.startswith(('qmt.api.', 'local.ledger_save')) for key in trace.stages):
                    try:
                        output_started = time.perf_counter()
                        self.log(trace.message())
                        _native_previous_output_ms = (time.perf_counter()-output_started)*1000
                    except Exception:
                        pass
        return wrapped
    return decorate


class Decision(NamedTuple):
    weights: dict
    marks: dict


# ==================== 策略决策与 QMT 入口 ====================

@native_measured('phase.prepare_pool_and_factors')
def prepare(data, previous, today):
    codes = [code for code, name, index in data.universe(previous)
             if not is_hong_kong_etf(code, name, index)]
    frame = data.daily(codes, previous)
    frame = frame[(frame["money"] > MIN_MONEY) & (frame["money"] < MAX_MONEY)]
    if frame.empty:
        return None  # 和原策略一致：预处理无候选，不等于清空持仓
    return frame.join(data.nav(frame.index.tolist(), previous, today), how="inner")


@native_measured('local.select_rank_weights')
def select(frame, quotes):
    # 个别ETF没有有效开盘行情时仅排除该ETF，不让全市场选股一起失败。
    frame = frame.loc[[code for code in frame.index if code in quotes]].copy()
    frame["last_price"] = [quotes[code]["last_price"] for code in frame.index]
    if SKIP_SUSPENDED_LIMITUP:
        frame = frame.loc[[code for code in frame.index
                           if not quotes[code]["paused"]
                           and frame.loc[code, "last_price"] < quotes[code]["high_limit"]]]
    frame["premium"] = (frame["last_price"] / frame["unit_net_value"] - 1) * 100
    selected = frame[frame["premium"] < 0].sort_values(["premium"], ascending=True).head(MAX_HOLD_NUM)
    raw = selected["premium"].abs().tolist()
    total = sum(raw) or 1.0
    return Decision(
        {code: float(weight / total * DEPLOY_RATIO) for code, weight in zip(selected.index, raw)},
        {code: float(selected.loc[code, "last_price"]) for code in selected.index})


def risk_signal(cost, price):
    if price < cost * STOP_LOSS_RATIO:
        return "stop_loss"
    if price > cost * TAKE_PROFIT_RATIO:
        return "take_profit"
    return None


_runtime = None


def init(ContextInfo):
    global _runtime
    if _runtime is not None:
        raise RuntimeError("\u672c\u7b56\u7565\u5b9e\u4f8b\u4ecd\u5728\u8fd0\u884c\uff0c\u8bf7\u5148\u5728 QMT \u4e2d\u505c\u6b62\u518d\u542f\u52a8")
    _runtime = create_runtime(ContextInfo, globals(), Settings(), prepare, select, risk_signal)
    try:
        _runtime.start(PREPARE_TIME, OPEN_TIME, RISK_CHECK_TIMES, SNAPSHOT_TIME)
    except BaseException:
        _runtime.close()
        _runtime = None
        raise


def stop(ContextInfo):
    global _runtime
    if _runtime is not None:
        _runtime.close()
        _runtime = None


def handlebar(ContextInfo):
    runtime = _runtime
    if runtime is not None:
        runtime.handlebar()  # 仅回测处理历史 bar，实时仍不在这里报单


def on_timer(ContextInfo):
    runtime = _runtime
    if runtime is not None:
        runtime.tick()


def order_callback(ContextInfo, orderInfo):
    runtime = _runtime
    if runtime is not None:
        runtime.on_report("order", orderInfo)


def deal_callback(ContextInfo, dealInfo):
    runtime = _runtime
    if runtime is not None:
        runtime.on_report("deal", dealInfo)


def orderError_callback(ContextInfo, orderArgs, errMsg):
    runtime = _runtime
    if runtime is not None:
        runtime.on_error(orderArgs, errMsg)


# ==================== QMT 数据、账本与执行辅助 ====================

class Settings:
    """\u4e3a\u6267\u884c\u8f85\u52a9\u4ee3\u7801\u4fdd\u5b58\u9876\u90e8\u914d\u7f6e\u5feb\u7167\uff1b\u4e0d\u9700\u8981\u7528\u6237\u53e6\u884c\u7ef4\u62a4\u914d\u7f6e\u6587\u4ef6\u3002"""

    def __init__(self):
        self.__dict__.update((name, setting) for name, setting in globals().items()
                             if name.isupper())


def is_hong_kong_etf(code, name, index_name=""):
    text = "{} {}".format(name, index_name).lower()
    return code in HK_CODES or any(word.lower() in text for word in HK_WORDS)


def value(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def positive(number):
    try:
        number = float(number)
        return number if math.isfinite(number) and 0 < number < 1e12 else None
    except (ValueError, TypeError):
        return None


def date_text(raw):
    text = str(raw).strip()
    # QMT/Pandas indexes can be numpy.int64 or numeric strings, not builtin int.
    digits = text.split('.', 1)[0]
    if digits.isdigit() and len(digits) in (10, 13):
        try:
            stamp = float(text) / (1000 if len(digits) == 13 else 1)
            return dt.datetime.fromtimestamp(stamp, dt.timezone(dt.timedelta(hours=8))).strftime("%Y%m%d")
        except (OverflowError, OSError, ValueError):
            pass
    return text.replace("-", "")[:8]


def quote_datetime(raw):
    """Normalize QMT timestamps in Beijing, including numpy scalars and numeric strings."""
    zone = dt.timezone(dt.timedelta(hours=8))
    if isinstance(raw, dt.datetime):
        if raw != raw:  # pandas NaT is datetime-like but is not a valid source timestamp.
            return None
        return raw.replace(tzinfo=zone) if raw.tzinfo is None else raw.astimezone(zone)
    text = str(raw).strip()
    digits = text.split('.', 1)[0]
    try:
        if digits.isdigit() and len(digits) in (10, 13):
            number = float(text)
            if not math.isfinite(number):
                return None
            return dt.datetime.fromtimestamp(number / (1000 if len(digits) == 13 else 1), zone)
        if digits.isdigit() and len(digits) in (14, 17):
            if len(digits) == 14 and '.' in text:
                return dt.datetime.strptime(text, '%Y%m%d%H%M%S.%f').replace(tzinfo=zone)
            parsed = dt.datetime.strptime(digits[:14], '%Y%m%d%H%M%S')
            if len(digits) == 17:
                parsed = parsed.replace(microsecond=int(digits[14:]) * 1000)
            return parsed.replace(tzinfo=zone)
        for fmt in ('%Y%m%d %H:%M:%S.%f', '%Y%m%d %H:%M:%S',
                    '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S',
                    '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S'):
            try:
                return dt.datetime.strptime(text, fmt).replace(tzinfo=zone)
            except ValueError:
                pass
    except (ValueError, TypeError, OverflowError, OSError):
        pass
    return None


class QuoteIssue(NamedTuple):
    security: str
    reason: str
    raw_time: str
    time_type: str
    parsed_time: str
    raw_price: str


class QuoteBatch(NamedTuple):
    rows: dict
    issues: tuple
    paused: int


class OpeningQuotesTimeout(RuntimeError):
    pass


class DailyDataUnavailable(RuntimeError):
    pass


def frame_from_rows(rows, columns):
    """QMT pandas 0.22.0 has no from_dict(columns=...). Keep explicit order."""
    if not rows:
        return pd.DataFrame(columns=list(columns))
    frame = pd.DataFrame.from_dict(rows, orient="index")
    frame.columns = list(columns)
    return frame


def check_pandas_environment():
    """Synthetic only: fail before account binding, ledger allocation or timers."""
    try:
        daily = frame_from_rows({'B': [3., 1., 9.], 'A': [2., 1., 8.], 'MISSING': [4., 1., 8.]},
                                ['high_price', 'low_price', 'money'])
        nav = frame_from_rows({'A': 2., 'B': 3.}, ['unit_net_value'])
        frame = daily[(daily['money'] > 7.) & (daily['money'] < 10.)].join(nav, how='inner')
        frame['last_price'] = [1.8 if code == 'A' else 2.85 for code in frame.index]
        frame['premium'] = (frame['last_price'] / frame['unit_net_value'] - 1) * 100
        ranked = frame[frame['premium'] < 0].sort_values(['premium'], ascending=True).head(2)
        discounts = ranked['premium'].abs().tolist()
        weights = [number / sum(discounts) * .95 for number in discounts]
        if (ranked.index.tolist() != ['A', 'B']
                or list(frame.columns) != ['high_price', 'low_price', 'money', 'unit_net_value', 'last_price', 'premium']
                or abs(weights[0] - .95 * 2 / 3) > 1e-10
                or abs(weights[1] - .95 / 3) > 1e-10
                or frame_from_rows({}, ['unit_net_value']).columns.tolist() != ['unit_net_value']):
            raise ValueError('DataFrame schema, join, sort or weight check differs')
    except Exception as exc:
        raise RuntimeError('[native_environment] dataframe_check=FAIL; {}: {}; no runtime started'.format(
            type(exc).__name__, exc)) from exc
    print('[native_environment] build=20261019_pandas_v1 python={} pandas={} dataframe_check=PASS; '
          'synthetic only, not market data or order verification'.format(
              sys.version.split()[0], getattr(pd, '__version__', 'unknown')))


class PreparationGuard:
    """Throttle preparation only; never pause report booking, exits or live orders."""
    def __init__(self, settings):
        self.settings, self.day, self.next_at, self.fatal = settings, '', 0., False

    def ready(self, today):
        if self.day != today:
            self.day, self.next_at, self.fatal = today, 0., False
        return not self.fatal and time.monotonic() >= self.next_at

    def failed(self, error):
        self.fatal = isinstance(error, (TypeError, AttributeError, ImportError, NotImplementedError))
        if self.fatal:
            return '\u5f53\u65e5\u51c6\u5907\u5df2\u505c\u6b62\uff0c\u8bf7\u4fee\u590d\u517c\u5bb9\u6027\u540e\u91cd\u542f\uff1b\u4e0d\u8865\u53d1\u9519\u8fc7\u7684\u8ba1\u5212'
        delay = max(5., self.settings.PREPARE_RETRY_SECONDS)
        self.next_at = time.monotonic() + delay
        return '{:.0f}\u79d2\u540e\u91cd\u67e5\u51c6\u5907\uff0c\u4ec5\u5728\u539f\u5b9a\u51c6\u5907/\u9009\u80a1\u65f6\u6bb5\u5185\u91cd\u8bd5'.format(delay)


def china_now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).replace(tzinfo=None)


def reported_side(row):
    """\u80a1\u7968\u6210\u4ea4\u7684m_nDirection\u56fa\u5b9a\u4e3a48\uff0c\u4e0d\u80fd\u636e\u6b64\u533a\u5206\u4e70\u5356\u3002"""
    sides = []
    for field, mapping in (("m_nOpType", {23: "BUY", 24: "SELL"}),
                           ("m_nOffsetFlag", {48: "BUY", 49: "SELL"})):
        try:
            side = mapping.get(int(value(row, field)))
        except (TypeError, ValueError):
            side = None
        if side:
            sides.append(side)
    if len(set(sides)) > 1:
        raise RuntimeError("\u56de\u62a5\u81ea\u8eab\u7684\u65b9\u5411\u5b57\u6bb5\u51b2\u7a81")
    return sides[0] if sides else None


class QmtData:
    def __init__(self, context, namespace, settings, log=print, allow_undated_nav=False):
        self.c, self.namespace, self.settings, self.log = context, namespace, settings, log
        self.details = {}
        self.last_quote_time = "\u672a\u83b7\u53d6"
        self.allow_undated_nav, self.unverified_nav = allow_undated_nav, set()
        self.nav_details, self.nav_read_at = {}, "\u672a\u8bfb\u53d6"
        self.universe_count, self.daily_count = 0, 0
        self.daily_warmup, self.daily_missing_key = None, None
        self.quote_watch_key, self.quote_watch_id = None, None
        self.opening_key, self.opening_log_at, self.opening_status = None, None, ''
        self.opening_attempts = 0

    def warm_quotes(self, codes, now):
        key = (now.strftime('%Y%m%d'), tuple(sorted(set(codes))))
        if key == self.quote_watch_key or not key[1]:
            return
        self.close_quote_watch()
        self.quote_watch_key = key
        subscribe = getattr(self.c, 'subscribe_whole_quote', None)
        if not callable(subscribe):
            self.log('[opening_quotes] subscription unavailable; use batch snapshot reads on timers')
            return
        try:
            sub = self.call('subscribe_whole_quote', subscribe, list(key[1]))
            if not isinstance(sub, int) or isinstance(sub, bool) or sub <= 0:
                raise RuntimeError('invalid quote subscription id')
            self.quote_watch_id = sub
            self.log('[opening_quotes] prewarmed candidates={}'.format(len(key[1])))
        except Exception as exc:
            self.log('[opening_quotes] subscription failed: {}; use checked snapshots'.format(type(exc).__name__))

    def close_quote_watch(self):
        sub, self.quote_watch_id, self.quote_watch_key = self.quote_watch_id, None, None
        unsubscribe = getattr(self.c, 'unsubscribe_quote', None)
        if sub is not None and callable(unsubscribe):
            try:
                unsubscribe(sub)  # Only this model's subscription; never broker cancel.
            except Exception:
                pass

    @staticmethod
    def quote_summary(batch, count):
        return 'candidates={} ready={} paused={} pending={} reasons={} samples={}'.format(
            count, len(batch.rows) - batch.paused, batch.paused, len(batch.issues),
            dict(Counter(item.reason for item in batch.issues)),
            [item._asdict() for item in batch.issues[:5]])

    def opening_quotes(self, codes, now, open_at):
        scheduled = now.replace(hour=int(open_at[:2]), minute=int(open_at[3:]), second=0, microsecond=0)
        wait = float(self.settings.OPEN_QUOTE_WAIT_SECONDS)
        if not math.isfinite(wait) or not 0 < wait < 60:
            raise ValueError('OPEN_QUOTE_WAIT_SECONDS must be between 0 and 60')
        key = (now.strftime('%Y%m%d'), open_at)
        if self.opening_key != key:
            self.opening_key, self.opening_log_at, self.opening_attempts = key, None, 0
            self.opening_status = '[opening_quotes] no snapshot read in execution window'
        deadline = scheduled + dt.timedelta(seconds=wait)
        if now > deadline:
            raise OpeningQuotesTimeout(self.opening_status + '; deadline=' + deadline.isoformat())
        self.opening_attempts += 1
        started = time.perf_counter()
        try:
            batch = self.quote_batch(codes, now, allow_suspended=True)
        except Exception as exc:
            batch = QuoteBatch({}, tuple(QuoteIssue(code, 'query_failed', '', '', '',
                               type(exc).__name__) for code in codes), 0)
        self.opening_status = '[opening_quotes] attempt={} delay_ms={:.1f} {}'.format(
            self.opening_attempts, (now - scheduled).total_seconds() * 1000,
            self.quote_summary(batch, len(codes)))
        observed_now = now + dt.timedelta(seconds=max(0., time.perf_counter() - started))
        if observed_now > deadline:
            raise OpeningQuotesTimeout(self.opening_status + '; deadline=' + deadline.isoformat())
        if not batch.issues:
            return batch.rows
        if now >= deadline:
            raise OpeningQuotesTimeout(self.opening_status + '; deadline=' + deadline.isoformat())
        mono = time.monotonic()
        if self.opening_log_at is None or mono - self.opening_log_at >= self.settings.OPEN_QUOTE_LOG_SECONDS:
            self.log(self.opening_status)
            self.opening_log_at = mono
        return None  # Retry on the existing 1-second timer, never sleep or partially plan.

    def api(self, name):
        fn = self.namespace.get(name) or getattr(builtins, name, None)
        if not callable(fn):
            raise RuntimeError("QMT \u5185\u7f6e\u63a5\u53e3\u4e0d\u53ef\u7528: " + name)
        @functools.wraps(fn)
        def observed(*args, **kwargs):
            suffix = '.' + str(args[2]) if name == 'get_trade_detail_data' and len(args) > 2 else ''
            return self.call(name + suffix, fn, *args, **kwargs)
        return observed

    def call(self, name, fn, *args, **kwargs):
        with native_span('qmt.api.' + name):
            return fn(*args, **kwargs)

    def calendar(self, now):
        end = now.strftime("%Y%m%d")
        dates = self.call('get_trading_dates', self.c.get_trading_dates, "000001.SH", "", end, 10, "1d")
        days = sorted({date_text(day) for day in dates if date_text(day) <= end})
        previous = [day for day in days if day < end]
        if not previous:
            raise RuntimeError("\u4ea4\u6613\u65e5\u5386\u7f3a\u5931\uff0c\u5148\u8865\u5145 QMT \u884c\u60c5\u6570\u636e")
        return previous[-1], end in days

    def detail(self, code):
        if code not in self.details:
            method = getattr(self.c, "get_instrument_detail", None) or self.c.get_instrumentdetail
            row = self.call('get_instrument_detail', method, code)
            if not row:
                raise RuntimeError("\u5408\u7ea6\u8d44\u6599\u7f3a\u5931: " + code)
            self.details[code] = row
        return self.details[code]

    def label(self, code):
        return "{}({})".format(code, self.detail(code).get("InstrumentName", "\u672a\u77e5\u540d\u79f0"))

    def universe(self, previous, asof=None):
        self.details.clear()  # 涨跌停/交易状态等按日重新读取
        codes = (self.call('get_stock_list_in_sector', self.c.get_stock_list_in_sector, self.settings.ETF_SECTOR, asof)
                 if asof is not None else self.call('get_stock_list_in_sector', self.c.get_stock_list_in_sector, self.settings.ETF_SECTOR))
        if not codes:
            raise RuntimeError("ETF \u677f\u5757\u4e3a\u7a7a\uff0c\u8bf7\u68c0\u67e5 ETF_SECTOR \u548c\u5ba2\u6237\u7aef\u6570\u636e")
        rows = []
        for code in codes:
            detail = self.detail(code)
            if date_text(detail.get("OpenDate", "")) > previous:
                continue
            end = date_text(detail.get("ExpireDate", ""))
            if end and end != "0" and end <= previous:
                continue
            rows.append((code, detail.get("InstrumentName", ""),
                         self.settings.TRACKED_INDEX_NAMES.get(code, "")))
        self.log("ETF \u5168\u5e02\u573a={}\uff1bQMT \u65e0\u805a\u5bbd\u57fa\u91d1\u6307\u6570\u5e93\uff0c\u7f3a\u5931\u8ddf\u8e2a\u6307\u6570\u65f6\u4f7f\u7528\u540d\u79f0/\u4ee3\u7801\u8fc7\u6ee4".format(len(rows)))
        self.universe_count = len(rows)
        return rows

    def read_daily(self, codes, previous):
        return self.call('get_market_data_ex', self.c.get_market_data_ex,
            ["high", "low", "amount"], codes, period="1d", end_time=previous + "235959",
            count=1, dividend_type="none", fill_data=False, subscribe=False)

    @staticmethod
    def daily_rows(data, codes, previous):
        rows, missing, samples = {}, [], []
        for code in codes:
            frame = (data or {}).get(code)
            reason = 'no_result' if frame is None else 'empty'
            raw_date = None
            if frame is not None and not frame.empty:
                raw_date = frame.index[-1]
                observed = date_text(raw_date)
                reason = 'wrong_day:' + observed
                if observed == previous:
                    try:
                        bar = frame.iloc[-1]
                        fields = [float(bar[name]) for name in ('high', 'low', 'amount')]
                        rows[code] = fields  # Preserve existing NaN/inner-join and liquidity semantics.
                        continue
                    except (KeyError, TypeError, ValueError):
                        reason = 'missing_or_invalid_fields'
            if code not in rows:
                missing.append(code)
                if len(samples) < 5:
                    samples.append((code, reason, str(raw_date)[:40], type(raw_date).__name__))
        return rows, missing, samples

    def warm_daily_cache(self, previous, now):
        """Bounded pre-open cache repair; never calls account/order APIs or changes factors."""
        clock = now.strftime('%H:%M')
        if (not self.settings.AUTO_DOWNLOAD_DAILY
                or not self.settings.DAILY_DOWNLOAD_START <= clock < self.settings.DAILY_DOWNLOAD_CUTOFF
                or clock >= self.settings.OPEN_TIME):
            return False
        download = self.namespace.get('download_history_data') or getattr(builtins, 'download_history_data', None)
        if not callable(download):
            return False  # Missing terminal API is diagnosed by daily(), never invented.
        if self.daily_warmup is None or self.daily_warmup['previous'] != previous:
            codes = list(self.call('get_stock_list_in_sector', self.c.get_stock_list_in_sector, self.settings.ETF_SECTOR) or [])
            if not codes:
                return False
            _, missing, _ = self.daily_rows(self.read_daily(codes, previous), codes, previous)
            self.daily_warmup = dict(previous=previous, queue=missing, at=0, next_check=0., done=not missing, failures=0)
            if missing:
                self.log('[daily_cache] previous={} missing={}; pre-open downloads only'.format(previous, len(missing)))
        state = self.daily_warmup
        if state['done']:
            return False
        started = time.perf_counter()
        batch = max(1, min(100, int(self.settings.DAILY_DOWNLOAD_BATCH)))
        for _ in range(batch):
            if state['at'] >= len(state['queue']):
                break
            # Check the real wall clock again: a slow native call may cross the cutoff.
            actual_clock = china_now().strftime('%H:%M')
            if actual_clock >= min(self.settings.DAILY_DOWNLOAD_CUTOFF, self.settings.OPEN_TIME):
                return False
            code = state['queue'][state['at']]
            state['at'] += 1  # Unknown download outcomes are not repeatedly re-issued.
            try:
                self.call('download_history_data', download, code, '1d', previous, previous)
            except Exception:
                state['failures'] += 1
            if (time.perf_counter() - started) * 1000 >= max(1., self.settings.DAILY_DOWNLOAD_BUDGET_MS):
                break
        if state['at'] < len(state['queue']):
            return True
        if time.monotonic() >= state['next_check']:
            state['next_check'] = time.monotonic() + 5
            rows, missing, _ = self.daily_rows(self.read_daily(state['queue'], previous), state['queue'], previous)
            if not missing or (rows and clock >= self.settings.PREPARE_TIME):
                state['done'] = True
                if not missing:
                    self.log('[daily_cache] previous={} verified; downloads={}'.format(previous, state['at']))
                else:
                    self.log('[daily_cache] previous={} valid={}/{} missing={} download_errors={}; '
                             'continue preparation; missing symbols retain original exclusion policy'.format(
                                 previous, len(rows), len(state['queue']), len(missing), state['failures']))
                return False
        return True

    def daily(self, codes, previous):
        self.daily_count = len(codes)
        if not codes:
            return pd.DataFrame(columns=["high_price", "low_price", "money"])
        rows, missing, samples = self.daily_rows(self.read_daily(codes, previous), codes, previous)
        diagnostic = (previous, len(missing), tuple(samples))
        if missing and diagnostic != self.daily_missing_key:
            self.daily_missing_key = diagnostic
            self.log('[daily_cache] previous={} valid={}/{} missing_samples={}; download QMT ETF daily data, not PCF'.format(
                previous, len(rows), len(codes), samples))
        if not rows:
            raise DailyDataUnavailable("\u5168\u4f53\u524d\u65e5\u65e5\u7ebf\u7f3a\u5931\uff0c\u8bf7\u5148\u8865\u5145 QMT \u65e5\u7ebf\u6570\u636e")
        return frame_from_rows(rows, ["high_price", "low_price", "money"])

    def pcf_nav(self, codes, previous, today):
        """\u6309\u8c03\u7528\u65b9\u7b56\u7565\u63a5\u53d7\u7f3a\u5931\u65e5\u671f\uff1b\u660e\u786e\u9519\u8bef\u65e5\u671f\u59cb\u7ec8\u62d2\u7edd\uff0c\u4e0d\u4f2a\u9020\u66f4\u65b0\u8bc1\u660e\u3002"""
        rows, failures = {}, []
        self.unverified_nav = set()
        self.nav_details, self.nav_read_at = {}, china_now().isoformat(timespec="milliseconds")
        for code in codes:
            try:
                info = self.api("get_etf_info")(code)
                raw_prior, raw_current = info.get("preTradingDay"), info.get("tradingDay")
                dates = [date_text(raw).strip() if raw is not None else "" for raw in (raw_prior, raw_current)]
                prior, current = ["" if text in ("", "0", "0.0", "00000000", "None") else text for text in dates]
                nav = None if isinstance(info.get("nav"), bool) else positive(info.get("nav"))
                if prior == previous and current == today and nav is not None:
                    rows[code] = nav
                elif (self.allow_undated_nav and nav is not None
                      and prior in (previous, "") and current in (today, "")):
                    rows[code] = nav
                    self.unverified_nav.add(code)
                else:
                    failures.append("{}: preTradingDay={} tradingDay={} nav={}".format(
                        code, prior or "<\u7a7a>", current or "<\u7a7a>", info.get("nav")))
                if code in rows:
                    self.nav_details[code] = dict(nav=nav, preTradingDay=raw_prior,
                                                 tradingDay=raw_current, date_verified=code not in self.unverified_nav)
            except Exception as exc:
                failures.append("{}: {}: {}".format(code, type(exc).__name__, exc))
        self.log("QMT\u51c0\u503c\u63a5\u53e3 | \u671f\u671b\u524d\u65e5={} \u5f53\u65e5={} | \u65e5\u671f\u6709\u6548\u8986\u76d6={}/{} | \u65e5\u671f\u672a\u6838\u9a8c={} | \u8bfb\u53d6\u65f6\u95f4={} | \u7f3a\u5931\u6837\u4f8b={}".format(
            previous, today, len(rows) - len(self.unverified_nav), len(codes),
            len(self.unverified_nav), self.nav_read_at, failures[:3]))
        if self.unverified_nav:
            self.log("\u51c0\u503c\u65e5\u671f\u672a\u6838\u9a8c | {} \u53ea\u6309QMT\u8fd4\u56de\u7684\u6709\u6548nav\u7ee7\u7eed\u4f7f\u7528\uff1b\u65e5\u671f\u7f3a\u5931\uff0c\u65e0\u6cd5\u8bc1\u660ePCF\u5df2\u66f4\u65b0\uff1b"
                     "\u8bfb\u53d6\u65f6\u95f4\u4e0d\u7b49\u4e8e\u51c0\u503c\u65e5\u671f\uff0c\u8bf7\u5728QMT\u6570\u636e\u7ba1\u7406\u4e2d\u66f4\u65b0\u5f53\u65e5ETF\u7533\u8d4e\u6e05\u5355".format(len(self.unverified_nav)))
        return rows

    def nav(self, codes, previous, today):
        rows = self.pcf_nav(codes, previous, today)
        missing = [code for code in codes if code not in rows]
        if missing and self.settings.NAV_FILE:
            with open(self.settings.NAV_FILE, encoding="utf-8-sig") as stream:
                document = json.load(stream)
            if date_text(document.get("date")) != previous:
                raise RuntimeError("NAV_FILE \u65e5\u671f\u5fc5\u987b\u662f\u524d\u4e00\u4ea4\u6613\u65e5 " + previous)
            count = len(rows)
            for code in missing:
                nav = positive(document["nav"].get(code))
                if nav is not None:
                    rows[code] = nav
            self.log("NAV_FILE\u4ec5\u8865QMT\u7f3a\u5931\u51c0\u503c {} \u53ea".format(len(rows) - count))
        missing = [code for code in codes if code not in rows]
        if missing:
            self.log("\u524d\u65e5\u5355\u4f4d\u51c0\u503c\u7f3a\u5931/\u65e5\u671f\u4e0d\u660e {} \u53ea: {}\uff1b\u672a\u4f7f\u7528 IOPV".format(len(missing), missing[:10]))
        if not rows:
            raise RuntimeError("\u65e0\u53ef\u7528\u7684\u524d\u65e5\u5355\u4f4d\u51c0\u503c\uff0c\u8df3\u8fc7\u4ea4\u6613\uff1b\u8bf7\u68c0\u67e5QMT PCF\uff0cNAV_FILE\u4ec5\u4e3a\u53ef\u9009\u515c\u5e95")
        return frame_from_rows(rows, ["unit_net_value"])

    def quote_batch(self, codes, now, allow_suspended=False):
        if not codes:
            return QuoteBatch({}, (), 0)
        ticks = self.call('get_full_tick', self.c.get_full_tick, list(codes))
        rows, issues, paused_count = {}, [], 0
        zone = dt.timezone(dt.timedelta(hours=8))
        local_now = now.replace(tzinfo=zone) if now.tzinfo is None else now.astimezone(zone)
        market_open = local_now.replace(hour=9, minute=30, second=0, microsecond=0)
        for code in codes:
            tick = ticks.get(code, {})
            price = positive(tick.get("lastPrice"))
            stamp = tick.get("time") or tick.get("stime") or tick.get("timetag")
            parsed = quote_datetime(stamp)
            detail = self.detail(code)
            paused = tick.get("openInt") in (1, 16, 17, 20) or detail.get("IsTrading") is False
            if paused and allow_suspended and SKIP_SUSPENDED_LIMITUP:
                # 停牌没有新 tick 属正常情况，仍返回停牌标记交由决策过滤。
                paused_count += 1
                rows[code] = dict(last_price=price or 0., paused=True,
                                  high_limit=positive(detail.get("UpStopPrice")) or float("inf"))
                continue
            reason = ('invalid_price' if price is None else
                      'invalid_time' if parsed is None else
                      'wrong_day' if parsed.date() != local_now.date() else
                      'future_time' if parsed > local_now + dt.timedelta(seconds=self.settings.QUOTE_FUTURE_TOLERANCE_SECONDS) else
                      'before_open' if local_now >= market_open and parsed < market_open else '')
            if reason:
                issues.append(QuoteIssue(code, reason, repr(stamp)[:100], type(stamp).__name__,
                                        parsed.isoformat() if parsed is not None else '',
                                        repr(tick.get('lastPrice'))[:100]))
                continue
            self.last_quote_time = str(stamp)
            rows[code] = dict(last_price=price, paused=paused,
                              high_limit=positive(detail.get("UpStopPrice")) or float("inf"))
        return QuoteBatch(rows, tuple(issues), paused_count)

    def quotes(self, codes, now, strict=True):
        if not codes:
            return {}
        batch = self.quote_batch(codes, now)
        if batch.issues:
            if strict:
                first = batch.issues[0]
                message = ("\u7b49\u5f85\u5f00\u76d8\u540e\u7684 lastPrice: " if first.reason == 'before_open'
                           else "\u6700\u65b0\u884c\u60c5\u65e0\u6548/\u4e0d\u662f\u4eca\u5929: ")
                raise RuntimeError(message + first.security)
            self.log('[quote_status] ' + self.quote_summary(batch, len(codes)))
        if not batch.rows:
            raise RuntimeError("\u5168\u4f53\u5019\u9009ETF\u65e0\u6709\u6548\u6700\u65b0\u884c\u60c5\uff0c\u4fdd\u7559\u6301\u4ed3\uff0c\u7b49\u5f85\u672c\u5206\u949f\u91cd\u67e5")
        return batch.rows


# 迅投内置 Python 枚举：53 部撤、54 已撤、55 部成、56 已成、57 废单。
TERMINAL = {53, 54, 56, 57}


def settled(order):
    return (order["status"] in TERMINAL and order["booked"] >= order["filled"]
            and (order["status"] != 56 or order["booked"] == order["qty"]))


class Executor:
    def __init__(self, data, settings, account, emit):
        self.data, self.settings, self.account, self.emit = data, settings, account, emit
        self.timings, self.rebalance_timing = {}, None
        identity = account + ":" + settings.STRATEGY_ID
        self.prefix = "bg" + hashlib.sha256(identity.encode()).hexdigest()[:8]
        os.makedirs(settings.STATE_DIR, exist_ok=True)
        self.path = os.path.join(settings.STATE_DIR, self.prefix + ".json")
        self.lock_file = open(self.path + ".lock", "a+b")
        try:
            self.lock_file.seek(0)
            # QMT 停止策略不一定退出 Python 进程，必须由 stop 主动关闭句柄。
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("\u8d26\u672c\u9501\u83b7\u53d6\u5931\u8d25\uff0c\u53ef\u80fd\u4ecd\u6709\u540c\u4e00\u8d26\u6237/\u7b56\u7565\u7684\u5b9e\u4f8b\u5360\u7528\uff1b"
                                   "\u8bf7\u505c\u6b62\u91cd\u590d\u5b9e\u4f8b\uff0c\u65e7\u7248\u505c\u6b62\u540e\u4ecd\u62a5\u9519\u53ef\u91cd\u542f QMT\uff1b"
                                   "\u4e0d\u8981\u5220\u9664\u8d26\u672c\u6216\u66f4\u6362\u7b56\u7565 ID \u7ed5\u8fc7\u9501: " + self.path) from exc
            self.state = dict(identity=identity, capital=settings.INITIAL_CAPITAL,
                              cash=settings.INITIAL_CAPITAL, positions={}, targets={},
                              orders={}, fills={}, jobs=[], allocated=False)
            if os.path.exists(self.path):
                with open(self.path, encoding="utf-8") as stream:
                    self.state = json.load(stream)
                if (self.state["identity"] != identity
                        or self.state["capital"] != settings.INITIAL_CAPITAL):
                    raise RuntimeError("\u8d26\u672c\u8eab\u4efd\u6216\u521d\u59cb\u8d44\u91d1\u4e0d\u4e00\u81f4\uff1b\u4e0d\u80fd\u9760\u6539\u914d\u7f6e\u91cd\u7f6e\u5df2\u4ea4\u6613\u8d26\u672c")
            self.save()
        except BaseException:
            self.close()
            raise

    def close(self):
        self.lock_file.close()

    @native_measured('local.ledger_save_json_fsync_replace')
    def save(self):
        with open(self.path + ".tmp", "w", encoding="utf-8") as stream:
            with native_span('local.ledger_json_write'):
                json.dump(self.state, stream, ensure_ascii=False, allow_nan=False)
            with native_span('local.ledger_flush_fsync'):
                stream.flush()
                os.fsync(stream.fileno())
        with native_span('local.ledger_atomic_replace'):
            os.replace(self.path + ".tmp", self.path)

    def query(self, kind):
        rows = self.data.api("get_trade_detail_data")(self.account, "stock", kind)
        if rows is None:
            raise RuntimeError("QMT \u67e5\u8be2\u672a\u8fd4\u56de\u6570\u636e: " + kind)
        return list(rows)

    def broker_cash(self):
        rows = self.query("account")
        if len(rows) != 1:
            raise RuntimeError("QMT \u8d26\u6237\u672a\u5c31\u7eea")
        cash = float(value(rows[0], "m_dAvailable", float("nan")))
        if not math.isfinite(cash) or cash < 0:
            raise RuntimeError("QMT \u53ef\u7528\u8d44\u91d1\u65e0\u6548")
        return cash

    def positions(self):
        return {str(value(p, "m_strInstrumentID")) + "." + str(value(p, "m_strExchangeID")): p
                for p in self.query("position")}

    def allocate(self):
        cash = self.broker_cash()
        if not self.state["allocated"]:
            if cash < self.state["capital"]:
                raise RuntimeError("\u8bc1\u5238\u8d26\u6237\u53ef\u7528\u8d44\u91d1\u4e0d\u8db3\u4ee5\u5206\u914d\u7b56\u7565\u521d\u59cb\u8d44\u91d1")
            self.state["allocated"] = True
            self.save()

    def find_order(self, row):
        if str(value(row, "m_strAccountID", self.account)) != self.account:
            return None
        tag = str(value(row, "m_strRemark") or value(row, "m_strUserOrderId")
                  or value(row, "m_strOrderRemark") or "")
        if tag in self.state["orders"]:
            order = self.state["orders"][tag]
            day = date_text(value(row, "m_strTradeDate") or value(row, "m_strInsertDate"))
            return order if not day or day == order["day"] else None
        if tag.startswith(self.prefix):
            raise RuntimeError("\u53d1\u73b0\u672c\u7b56\u7565\u59d4\u6258\u4f46\u672c\u5730\u8bb0\u5f55\u7f3a\u5931\uff1b\u8bf7\u6062\u590d\u539f\u8d26\u672c\uff0c\u4e0d\u53ef\u81ea\u52a8\u63a5\u7ba1")
        sysid = str(value(row, "m_strOrderSysID", ""))
        day = date_text(value(row, "m_strTradeDate") or value(row, "m_strInsertDate"))
        matches = [order for order in self.state["orders"].values()
                   if sysid not in ("", "0", "None") and order["sysid"] == sysid and order["day"] == day]
        return matches[0] if len(matches) == 1 else None

    @native_measured('phase.report_match_book_persist')
    def report(self, kind, row, source="\u67e5\u8be2", received_at=None, received_mono=None):
        received_at = received_at or china_now()
        received_mono = time.perf_counter() if received_mono is None else received_mono
        order = self.find_order(row)
        if order is None:
            return
        before = dict(order)
        code = str(value(row, "m_strInstrumentID")) + "." + str(value(row, "m_strExchangeID"))
        side = reported_side(row)
        # 投资备注或同交易日柜台委托号已经唯一关联本地委托，方向缺失可用原委托。
        if code != order["code"] or (side is not None and side != order["side"]):
            raise RuntimeError("\u59d4\u6258/\u6210\u4ea4\u6807\u7684\u6216\u65b9\u5411\u4e0e\u539f\u4e0b\u5355\u4e0d\u4e00\u81f4: " + order["code"])
        sysid = str(value(row, "m_strOrderSysID", ""))
        if sysid not in ("", "0", "None"):
            if order["sysid"] and order["sysid"] != sysid:
                raise RuntimeError("\u540c\u4e00\u6295\u8d44\u5907\u6ce8\u5bf9\u5e94\u591a\u7b14\u59d4\u6258\uff0c\u505c\u6b62\u63a8\u8fdb")
            order["sysid"] = sysid
        if kind == "order":
            status = int(value(row, "m_nOrderStatus", 255))
            filled = int(value(row, "m_nVolumeTraded", 0))
            if filled < 0 or filled > order["qty"]:
                raise RuntimeError("\u59d4\u6258\u7d2f\u8ba1\u6210\u4ea4\u91cf\u65e0\u6548")
            # 防止较早的查询覆盖终态回调，但累计成交仍取最大值。
            if order["status"] not in TERMINAL:
                order["status"] = status
            order["filled"] = max(order["filled"], filled)
            if status == 57:
                order["error"] = str(value(row, "m_strCancelInfo") or value(row, "m_strErrorMsg") or "\u672a\u63d0\u4f9b\u5e9f\u5355\u539f\u56e0")
                self.emit("\u59d4\u6258\u62d2\u7edd", code, "\u539f\u56e0: " + order["error"])
        else:
            self.book_fill(order, row)
        if order != before:
            self.save()
        try:
            self.record_report_timing(order, kind, row, source, received_at, received_mono)
        except Exception:
            pass  # 附加耗时日志失败不能改变已核验的委托/成交入账。

    def record_report_timing(self, order, kind, row, source, received_at, received_mono):
        timing = self.timings.setdefault(order["tag"], {})
        signature = (kind, source, str(value(row, "m_strTradeID", "")),
                     value(row, "m_nOrderStatus"), value(row, "m_nVolumeTraded"))
        observed = timing.setdefault("observed", set())
        if signature in observed:
            return
        observed.add(signature)
        elapsed = ("{:.1f}ms".format((received_mono - timing["submitted"]) * 1000)
                   if "submitted" in timing else "\u672a\u77e5\uff08\u91cd\u542f\u524d\u59d4\u6258\uff0c\u65e0\u5355\u8c03\u65f6\u949f\u91c7\u6837\uff09")
        broker_time = value(row, "m_strTradeTime" if kind == "deal" else "m_strInsertTime")
        self.data.log("\u6267\u884c\u56de\u62a5\u8017\u65f6 | {} \u65b9\u5411={} \u7c7b\u578b={} \u6765\u6e90={} \u672c\u5730\u63a5\u6536\u65f6\u95f4={} "
                      "\u5238\u5546\u539f\u59cb\u65f6\u95f4={} \u63d0\u4ea4\u81f3\u672c\u6b21\u89c2\u5bdf={} \u72b6\u6001={} \u7d2f\u8ba1\u6210\u4ea4={} | "
                      "\u67e5\u8be2\u4e3a\u53d1\u73b0\u65f6\u95f4\uff0c\u56de\u8c03\u4e3a\u672c\u5730\u63a5\u6536\u65f6\u95f4\uff0c\u5747\u975e\u64ae\u5408\u77ac\u95f4".format(
                          self.data.label(order["code"]), order["side"], kind, source,
                          received_at.isoformat(timespec="milliseconds"), broker_time or "\u672a\u63d0\u4f9b",
                          elapsed, order["status"], order["booked"]))

    def book_fill(self, order, row):
        trade_id = str(value(row, "m_strTradeID", ""))
        if trade_id in ("", "0", "None"):
            raise RuntimeError("\u771f\u5b9e\u6210\u4ea4\u7f16\u53f7\u7f3a\u5931\uff0c\u4e0d\u5f97\u8bb0\u6210 0 \u6216\u91cd\u590d\u5165\u8d26")
        qty = int(value(row, "m_nVolume", 0))
        if qty <= 0:
            raise RuntimeError("\u771f\u5b9e\u6210\u4ea4\u6570\u91cf\u65e0\u6548")
        key = order["tag"] + ":" + trade_id
        raw_price = value(row, "m_dTradePrice")
        if positive(raw_price) is None:
            raw_price = value(row, "m_dPrice", raw_price)
        raw_amount = value(row, "m_dTradeAmount")
        price = positive(raw_price)
        native_amount = positive(raw_amount)
        fee = value(row, "m_dCommission")
        if fee is None:
            fee = value(row, "m_dComission")
        try:
            fee = float(fee) if fee is not None else None
        except (TypeError, ValueError):
            fee = None
        if fee is not None and (not math.isfinite(fee) or fee < 0):
            fee = None
        if key in self.state["fills"]:
            old = self.state["fills"][key]
            if old["qty"] != qty:
                raise RuntimeError("\u540c\u4e00\u6210\u4ea4\u7f16\u53f7\u7684\u6570\u636e\u53d8\u5316\uff0c\u9700\u4eba\u5de5\u6838\u5bf9")
            if old.get("estimated"):
                if (price is not None and str(raw_price) != old.get("raw_price")
                        and (raw_amount is None or (native_amount is not None
                             and abs(native_amount - qty * price) <= qty * .001 + .01))):
                    self.emit("\u4f30\u7b97\u6210\u4ea4\u5f85\u6838\u5bf9", order["code"],
                              "\u539f\u56e0: \u5df2\u5165\u8d26\u4f30\u7b97\u6210\u4ea4\u51fa\u73b0\u4e0d\u540c\u7684\u6709\u6548\u56de\u62a5\u4ef7\n\u5904\u7406: \u4fdd\u7559\u539f\u91d1\u989d\u548c\u4f30\u7b97\u6807\u8bb0\uff0c\u4e0d\u4f2a\u9020\u7cbe\u786e\u6536\u76ca")
            elif price is not None and old["price"] != price:
                raise RuntimeError("\u540c\u4e00\u6210\u4ea4\u7f16\u53f7\u7684\u6570\u636e\u53d8\u5316\uff0c\u9700\u4eba\u5de5\u6838\u5bf9")
            elif native_amount is not None and abs(native_amount - old["amount"]) > .01:
                raise RuntimeError("\u540c\u4e00\u6210\u4ea4\u7f16\u53f7\u7684\u91d1\u989d\u53d8\u5316\uff0c\u9700\u4eba\u5de5\u6838\u5bf9")
            if fee is not None and old["fee"] != fee:
                self.state["cash"] -= fee - (old["fee"] or 0)
                old["fee"] = fee
                self.save()
            return
        tick = positive(self.data.detail(order["code"]).get("PriceTick")) or .001
        # 允许一跳和金额分位舍入；只有异常回报才额外查询最新价。
        mismatch = (raw_amount is not None and (native_amount is None or price is None
                    or abs(native_amount - qty * price) > qty * tick + .01))
        estimated = price is None or mismatch
        if estimated:
            quote = self.data.quotes([order["code"]], china_now())[order["code"]]
            price = quote["last_price"]
            amount = qty * price
            source = "\u6700\u65b0\u884c\u60c5\u4f30\u7b97"
        else:
            amount = native_amount if native_amount is not None else qty * price
            source = "\u5238\u5546\u6210\u4ea4\u56de\u62a5"
        if fee is not None and fee >= amount:
            fee = None
        if order["booked"] + qty > order["qty"]:
            raise RuntimeError("\u7d2f\u8ba1\u6210\u4ea4\u8d85\u8fc7\u539f\u59d4\u6258\u91cf")
        pos = self.state["positions"].setdefault(order["code"], dict(qty=0, cost=0.0))
        if order["side"] == "BUY":
            pos["cost"] += amount
            pos["qty"] += qty
            self.state["cash"] -= amount + (fee or 0)
        else:
            if qty > pos["qty"]:
                raise RuntimeError("\u6210\u4ea4\u5356\u51fa\u6570\u91cf\u8d85\u8fc7\u7b56\u7565\u5f52\u5c5e\u6301\u4ed3")
            pos["cost"] *= (pos["qty"] - qty) / pos["qty"]
            pos["qty"] -= qty
            self.state["cash"] += amount - (fee or 0)
        order["booked"] += qty
        order["filled"] = max(order["filled"], order["booked"])
        self.state["fills"][key] = dict(qty=qty, price=price, amount=amount, fee=fee,
                                       estimated=estimated,
                                       raw_price=None if raw_price is None else str(raw_price),
                                       raw_amount=None if raw_amount is None else str(raw_amount))
        # 先落盘再通知；通知失败不得导致成交重复入账。
        self.save()
        self.emit("\u6210\u4ea4\u56de\u62a5", order["code"], "\u65b9\u5411: {}\n\u6570\u91cf: {}\n\u5355\u4ef7: {:.3f}\n\u91d1\u989d: {:.2f}\n\u624b\u7eed\u8d39: {}\n\u4ef7\u683c\u6765\u6e90: {}\n\u6536\u76ca\u53e3\u5f84: {}".format(
            order["side"], qty, price, amount, "\u672a\u77e5" if fee is None else fee, source,
            "\u975e\u7cbe\u786e\u6536\u76ca" if estimated or fee is None else "\u5df2\u77e5\u56de\u62a5\u8d39\u7528\u53e3\u5f84"))

    @native_measured('phase.sync_and_book_reports')
    def sync(self, today):
        for kind in ("order", "deal"):
            for row in self.query(kind):
                self.report(kind, row)
        # 只为未结清的跨日委托查历史，不假设“今天查不到=已撤”。
        days = {order["day"] for order in self.state["orders"].values()
                if order["day"] < today and not settled(order)}
        if days:
            for kind in ("order", "deal"):
                groups = self.data.api("get_history_trade_detail_data")(
                    self.account, "stock", kind, min(days), today)
                for _, rows in groups:
                    for row in rows:
                        self.report(kind, row)

    @native_measured('phase.mark_to_market')
    def mark_to_market(self, now):
        positions = {code: pos for code, pos in self.state["positions"].items() if pos["qty"]}
        quotes = self.data.quotes(positions, now)
        return self.state["cash"] + sum(pos["qty"] * quotes[code]["last_price"] for code, pos in positions.items())

    def target(self, qty, reference, style, day):
        return dict(qty=qty, reference=reference, style=style, day=day)

    @native_measured('phase.prepare_targets')
    def rebalance(self, decision, now):
        day = now.strftime("%Y%m%d")
        self.sync(day)
        if any(not settled(o) for o in self.state["orders"].values()):
            raise RuntimeError("\u65e7\u59d4\u6258\u5c1a\u672a\u7ed3\u6e05\uff0c\u4e0d\u80fd\u521b\u5efa\u65b0\u8c03\u4ed3")
        equity = self.mark_to_market(now)
        positions = self.state["positions"]
        old_codes = [code for code, pos in positions.items() if pos["qty"] and code not in decision.marks]
        marks = dict(decision.marks)
        marks.update({code: row["last_price"] for code, row in self.data.quotes(old_codes, now).items()})
        targets = {}
        for code, ref in marks.items():
            qty = int(equity * decision.weights.get(code, 0) / ref / 100) * 100
            current = positions.get(code, {}).get("qty", 0)
            targets[code] = self.target(qty, ref, "market" if current > qty else "limit", day)
            self.emit("\u76ee\u6807\u8ba1\u5212", code, "\u76ee\u6807\u6570\u91cf: {}\n\u5355\u4ef7: {:.3f}\n\u76ee\u6807\u91d1\u989d: {:.2f}\n\u6743\u91cd: {:.2%}".format(
                qty, ref, qty * ref, decision.weights.get(code, 0)))
        # 观察模式不生成可以在重启后意外恢复的交易目标。
        if self.settings.ENABLE_TRADING:
            self.state["targets"] = targets
            self.save()

    def risk(self, signal, now):
        positions = {code: pos for code, pos in self.state["positions"].items() if pos["qty"]}
        quotes = self.data.quotes(positions, now)
        for code, pos in positions.items():
            price = quotes[code]["last_price"]
            reason = signal(pos["cost"] / pos["qty"], price)
            if not reason:
                continue
            style = "market" if reason == "stop_loss" else "profit"
            existing = self.state["targets"].get(code)
            if existing and existing["qty"] == 0 and existing["style"] == style:
                continue  # 已在退出，不重设固定参考价或反复撤原单
            self.emit("\u98ce\u63a7\u76ee\u6807", code, "\u539f\u56e0: {}\n\u5355\u4ef7: {:.3f}\n\u76ee\u6807\u6570\u91cf: 0".format(reason, price))
            if self.settings.ENABLE_TRADING:
                self.state["targets"][code] = self.target(0, price, style, now.strftime("%Y%m%d"))
                self.save()
                for order in self.state["orders"].values():
                    if order["code"] == code and not settled(order):
                        self.cancel(order)

    def cancel(self, order):
        if not self.settings.ENABLE_TRADING or order["status"] in TERMINAL:
            return
        if not order["sysid"]:
            raise RuntimeError("\u5f85\u64a4\u59d4\u6258\u5c1a\u65e0\u67dc\u53f0\u7f16\u53f7\uff0c\u7b49\u5f85\u56de\u62a5\uff0c\u4e0d\u91cd\u53d1")
        if time.time() - order.get("cancel_at", 0) < 30:
            return
        order["cancel_at"] = time.time()
        self.save()
        accepted = self.data.api("cancel")(order["sysid"], self.account, "stock", self.data.c)
        self.emit("\u64a4\u5355\u8bf7\u6c42", order["code"], "\u5df2\u53d1\u9001: {}\uff1b\u5b9e\u9645\u64a4\u5355\u4ee5\u59d4\u6258\u7ec8\u6001\u4e3a\u51c6".format(bool(accepted)))

    def expire(self, today):
        for order in self.state["orders"].values():
            if order["day"] < today and not settled(order):
                self.cancel(order)
        targets = {code: target for code, target in self.state["targets"].items() if target["day"] == today}
        if targets != self.state["targets"]:
            self.state["targets"] = targets
            self.save()

    def order_price(self, code, target, side):
        rate = (1 + self.settings.BUY_PREMIUM) if side == "BUY" else (
            1 - (self.settings.PROFIT_DISCOUNT if target["style"] == "profit" else self.settings.MARKET_PROTECTION))
        detail = self.data.detail(code)
        tick = positive(detail.get("PriceTick")) or 0.001
        price = target["reference"] * rate
        upper, lower = positive(detail.get("UpStopPrice")), positive(detail.get("DownStopPrice"))
        if upper:
            price = min(price, upper)
        if lower:
            price = max(price, lower)
        # 不超出策略可接受价格；ETF 精度不套用股票的两位小数。
        rounding = ROUND_FLOOR if side == "BUY" else ROUND_CEILING
        price = float((Decimal(str(price)) / Decimal(str(tick))).to_integral_value(rounding=rounding) * Decimal(str(tick)))
        pr_type = 11
        if side == "SELL" and target["style"] == "market":
            if code.endswith(".SH") and self.settings.SH_NATIVE_MARKET:
                pr_type = 42
            elif code.endswith(".SZ") and self.settings.SZ_NATIVE_MARKET:
                pr_type = 47
        return price, pr_type

    @native_measured('phase.advance_orders')
    def advance(self, now):
        if not self.settings.ENABLE_TRADING:
            return
        today, clock = now.strftime("%Y%m%d"), now.strftime("%H:%M:%S")
        if not ("09:30:00" <= clock < "11:30:00" or "13:00:00" <= clock < "14:57:00"):
            return
        targets, orders, positions = self.state["targets"], self.state["orders"], self.state["positions"]
        if not targets:
            return
        self.allocate()
        physical = self.positions()
        for code, pos in positions.items():
            if pos["qty"] > int(value(physical.get(code), "m_nVolume", 0)):
                raise RuntimeError("\u5238\u5546\u6301\u4ed3\u4e0d\u8db3\u7b56\u7565\u5f52\u5c5e\u6570\u91cf: " + code + "\uff1b\u53ef\u80fd\u624b\u52a8\u5356\u51fa\u6216\u56de\u62a5\u5c1a\u672a\u540c\u6b65")
        if any(o["day"] != today and not settled(o) for o in orders.values()):
            raise RuntimeError("\u8de8\u65e5\u59d4\u6258\u5c1a\u672a\u7ed3\u6e05\uff0c\u7b49\u5f85\u5386\u53f2\u56de\u62a5")
        sell_pending = any(positions.get(code, {}).get("qty", 0) > target["qty"] for code, target in targets.items())
        sell_pending = sell_pending or any(o["side"] == "SELL" and not settled(o) for o in orders.values())
        reserved = sum((o["qty"] - o["booked"]) * o["price"] + self.settings.CASH_RESERVE
                       for o in orders.values() if o["side"] == "BUY" and not settled(o))
        cash = min(self.state["cash"] - reserved, self.broker_cash())
        for code, target in targets.items():
            if target["day"] != today:
                continue
            prior = [o for o in orders.values() if o["code"] == code and o["day"] == today]
            if any(not settled(o) for o in prior):
                for order in prior:
                    if not settled(order) and order.get("target") != target:
                        self.cancel(order)
                    elif not settled(order) and not order["sysid"] and time.time() - order["sent_at"] >= 30:
                        self.emit("\u7b49\u5f85\u59d4\u6258\u56de\u62a5", code, "\u539f\u56e0: \u4e0b\u5355\u540e\u5c1a\u65e0\u67dc\u53f0\u7f16\u53f7\n\u5904\u7406: \u67e5\u8be2\u786e\u8ba4\uff0c\u4e0d\u91cd\u590d\u53d1\u9001\n\u5907\u6ce8: " + order["tag"])
                continue
            if prior and time.time() - prior[-1]["sent_at"] < self.settings.RETRY_SECONDS:
                continue
            if any(o["status"] == 57 for o in prior):
                continue  # ETF 不做股票笼子重试；不支持类型/余额等废单需查看原因
            current = positions.get(code, {}).get("qty", 0)
            delta = target["qty"] - current
            if delta == 0 or (delta > 0 and sell_pending):
                continue
            side = "BUY" if delta > 0 else "SELL"
            price, pr_type = self.order_price(code, target, side)
            if not positive(price):
                raise RuntimeError("\u59d4\u6258\u4ef7\u683c\u65e0\u6548: " + code)
            if side == "BUY":
                qty = min(delta, max(0, int((cash - self.settings.CASH_RESERVE) / price / 100) * 100))
            else:
                available = int(value(physical.get(code), "m_nCanUseVolume", 0))
                qty = min(-delta, available)
                if qty < current:
                    qty = qty // 100 * 100
            if qty <= 0:
                continue  # T+1 可卖为0不报账本损坏；等待，且不跳过先卖后买
            order = dict(tag=self.prefix + uuid.uuid4().hex[:12], code=code, side=side, qty=qty,
                         price=price, day=today, sysid="", status=48, filled=0, booked=0,
                         sent_at=time.time(), error="", target=dict(target))
            orders[order["tag"]] = order
            self.save()  # 必须先保存投资备注，再发送；异常结果不允许自动重发
            submitted_at, submitted = china_now(), time.perf_counter()
            self.timings[order["tag"]] = {"submitted": submitted}
            opening = self.rebalance_timing
            opening_elapsed = None
            if opening is not None and opening["day"] == today:
                opening_elapsed = (submitted - opening["started"]) * 1000
                if opening.get("first_submit_at") is None:
                    opening["first_submit_at"] = submitted_at.isoformat(timespec="milliseconds")
                    opening["first_submit_ms"] = opening_elapsed
            try:
                self.data.api("passorder")(23 if side == "BUY" else 24, 1101, self.account,
                    code, pr_type, price, qty, self.settings.STRATEGY_ID, 2, order["tag"], self.data.c)
            except Exception as exc:
                order["error"] = "\u4e0b\u5355\u7ed3\u679c\u672a\u77e5: " + str(exc)
                self.save()
                self.data.log("\u4e0b\u5355\u63a5\u53e3\u5f02\u5e38 | {} | \u8c03\u7528\u65f6\u95f4={} \u63a5\u53e3\u8017\u65f6={:.1f}ms | \u7ed3\u679c\u672a\u77e5\uff0c\u4e0d\u91cd\u53d1".format(
                    self.data.label(code), submitted_at.isoformat(timespec="milliseconds"),
                    (time.perf_counter() - submitted) * 1000))
                raise RuntimeError(order["error"])
            returned, returned_at = time.perf_counter(), china_now()
            self.emit("\u4e0b\u5355\u8bf7\u6c42", code, "\u65b9\u5411: {}\n\u6570\u91cf: {}\n\u5355\u4ef7: {:.3f}\n\u91d1\u989d: {:.2f}\n\u7c7b\u578b: {}\n\u72b6\u6001: \u7b49\u5f85\u67dc\u53f0\u56de\u62a5\n\u5907\u6ce8: {}"
                      "\n\u63a5\u53e3\u8c03\u7528\u65f6\u95f4: {}\n\u63a5\u53e3\u8fd4\u56de\u65f6\u95f4: {}\n\u63a5\u53e3\u8017\u65f6: {:.1f}ms\n\u5f00\u76d8\u56de\u8c03\u81f3\u672c\u5355: {}".format(
                side, qty, price, qty * price, "\u4e94\u6863\u5373\u6210\u5269\u64a4" if pr_type in (42, 47) else "\u56fa\u5b9a\u9650\u4ef7", order["tag"],
                submitted_at.isoformat(timespec="milliseconds"), returned_at.isoformat(timespec="milliseconds"),
                (returned - submitted) * 1000,
                "{:.1f}ms".format(opening_elapsed) if opening_elapsed is not None else "\u975e\u672c\u6b21\u5f00\u76d8\u76ee\u6807"))
            if side == "BUY":
                cash -= qty * price + self.settings.CASH_RESERVE


class PaperAccount:
    """\u53ea\u4e3a\u89c2\u5bdf\u672c\u5730\u4e0b\u5355\u65f6\u70b9\uff1a\u6309lastPrice\u5047\u8bbe\u6210\u4ea4\uff0c\u4e0d\u662f\u5238\u5546\u64ae\u5408\u3002"""
    def __init__(self, data, settings):
        self.data, self.settings = data, settings
        self.cash = positive(settings.PAPER_INITIAL_CAPITAL)
        if self.cash is None:
            raise ValueError("PAPER_INITIAL_CAPITAL\u5fc5\u987b\u4e3a\u6709\u6548\u6b63\u6570")
        self.positions, self.orders = {}, []

    def rebalance(self, decision, quotes, now, timing):
        started = time.perf_counter()
        marks = dict(quotes)
        missing = [code for code, pos in self.positions.items() if pos["qty"] and code not in marks]
        marks.update(self.data.quotes(missing, now))
        equity = self.cash + sum(pos["qty"] * marks[code]["last_price"]
                                 for code, pos in self.positions.items() if pos["qty"])
        held = {code for code, pos in self.positions.items() if pos["qty"]}
        codes = list(decision.weights) + sorted(held - set(decision.weights))
        targets = {code: int(equity * decision.weights.get(code, 0)
                             / decision.marks.get(code, marks[code]["last_price"]) / 100) * 100
                   for code in codes}
        timing["target_ms"] = (time.perf_counter() - started) * 1000
        events = []
        sell_pending = False
        for side in ("SELL", "BUY"):
            if side == "BUY" and sell_pending:
                break
            for code in codes:
                position = self.positions.setdefault(code, dict(qty=0, cost=0.0))
                delta = targets[code] - position["qty"]
                if delta == 0 or (delta > 0) != (side == "BUY"):
                    continue
                quote = marks[code]
                price = quote["last_price"]
                reference = decision.marks.get(code, price)
                # Reuse the existing pure price calculation; never construct
                # an Executor or call any account/submit/cancel API.
                limit, price_type = Executor.order_price(self, code, dict(
                    reference=reference, style="market" if side == "SELL" else "limit"), side)
                quantity = abs(delta)
                if side == "BUY":
                    quantity = min(quantity, max(0, int((self.cash - self.settings.CASH_RESERVE)
                                                       / limit / 100) * 100))
                if quantity <= 0:
                    events.append("\u7eb8\u9762\u8df3\u8fc7 | {} | \u8d44\u91d1\u4e0d\u8db3\u4e00\u624b\uff0c\u4e0d\u8865\u4e70".format(self.data.label(code)))
                    continue
                sent_at, sent_mono = china_now(), time.perf_counter()
                if timing.get("first_paper_order_ms") is None:
                    timing["first_paper_order_at"] = sent_at.isoformat(timespec="milliseconds")
                    timing["first_paper_order_ms"] = (sent_mono - timing["started"]) * 1000
                filled = (not quote["paused"] and (price_type in (42, 47)
                          or (price <= limit if side == "BUY" else price >= limit)))
                order = dict(code=code, side=side, quantity=quantity, limit_price=limit,
                             assumed_price=price if filled else None,
                             submitted_at=sent_at.isoformat(timespec="milliseconds"), filled=filled)
                self.orders.append(order)
                if filled:
                    if side == "BUY":
                        position["qty"] += quantity
                        position["cost"] += quantity * price
                        self.cash -= quantity * price
                    else:
                        position["cost"] *= (position["qty"] - quantity) / position["qty"]
                        position["qty"] -= quantity
                        self.cash += quantity * price
                elif side == "SELL":
                    sell_pending = True
                events.append("\u7eb8\u9762\u4e0b\u5355 | {} | \u65b9\u5411={} \u6570\u91cf={} \u5355\u4ef7={:.4f} \u91d1\u989d={:.2f} \u7c7b\u578b={} "
                              "\u4e0b\u5355\u65f6\u95f4={} \u56de\u8c03\u81f3\u4e0b\u5355={:.1f}ms | {}".format(
                                  self.data.label(code), side, quantity, limit, limit * quantity,
                                  "\u5e02\u4ef7" if price_type in (42, 47) else "\u9650\u4ef7",
                                  order["submitted_at"], (sent_mono - timing["started"]) * 1000,
                                  "\u6309lastPrice={:.4f}\u5047\u8bbe\u7acb\u5373\u6210\u4ea4\uff08\u975e\u771f\u5b9e\u6210\u4ea4\uff09".format(price)
                                  if filled else "\u672a\u5047\u8bbe\u6210\u4ea4\uff0c\u4e0d\u8865\u5355\u3001\u4e0d\u8ffd\u5355"))
        timing["paper_order_count"] = sum(text.startswith("\u7eb8\u9762\u4e0b\u5355") for text in events)
        timing["paper_ms"] = (time.perf_counter() - started) * 1000
        return events


class SelectionRuntime:
    """\u5b9e\u65f6\u53ea\u8bfb\u9009\u80a1\uff1a\u6ca1\u6709\u8d26\u6237\u3001Executor\u3001\u8d44\u91d1\u6216\u6301\u4ed3\u8d26\u672c\u3002"""
    def __init__(self, context, namespace, settings, prepare_fn, select_fn, risk_fn):
        if settings.ENABLE_TRADING or getattr(context, "do_back_test", False):
            raise RuntimeError("\u4ec5\u9009\u80a1\u9a8c\u8bc1\u4e0d\u80fd\u7528\u4e8e\u4ea4\u6613\u6216\u5386\u53f2\u56de\u6d4b")
        self.c, self.settings = context, settings
        self.prepare_fn, self.select_fn = prepare_fn, select_fn
        self.data = QmtData(context, namespace, settings, self.log, allow_undated_nav=True)
        self.guard, self.closed = threading.RLock(), False
        self.day, self.jobs, self.frame, self.last_decision = "", set(), None, None
        self.is_trading_day, self.calendar_checked_at, self.retry_at = False, 0, 0
        self.last_error = None
        self.preparation = PreparationGuard(settings)
        self.paper = PaperAccount(self.data, settings) if settings.PAPER_TRADING else None
        self.last_timing = None

    @native_measured('local.log_stdout_and_memory')
    def log(self, text):
        now = china_now()
        mode = "\u7eb8\u9762\u6a21\u62df" if self.paper is not None else "\u4ec5\u9009\u80a1"
        line = "{} [{}][{}] {}".format(now.isoformat(sep=" ", timespec="milliseconds"), self.settings.STRATEGY_ID, mode, text)
        buffer_native_line(self, line, now)
        if print_timing_only(text, line):
            return
        try:
            print(line)
        except Exception:
            pass

    def start(self, prepare_time, open_time, risk_times, snapshot_time):
        self.times = prepare_time, open_time, tuple(risk_times), snapshot_time
        self.log("[strategy_name] " + self.settings.STRATEGY_NAME)
        self.c.run_time("on_timer", "1nSecond", china_now().strftime("%Y-%m-%d %H:%M:%S"))
        self.log('[opening_quotes] build=20261020_quotes_v1 wait_seconds={} require_all_tradable=True'.format(
            self.settings.OPEN_QUOTE_WAIT_SECONDS))
        self.log("\u542f\u52a8 | \u4e0d\u7ed1\u5b9a\u8d26\u6237\u3001\u4e0d\u67e5\u8be2\u8d44\u91d1/\u6301\u4ed3\u3001\u4e0d\u5efa\u4ea4\u6613\u8d26\u672c\u3001\u4e0d\u4e0b\u5355\u3001\u4e0d\u64a4\u5355\u3001\u4e0d\u53d1\u9001\u98de\u4e66\uff1b\u5df2\u6709\u670d\u52a1\u4e0d\u53d7\u5f71\u54cd")
        self.log("\u8c03\u5ea6 | {}\u51c6\u5907 | {}\u9009\u80a1 | {}\u9009\u80a1\u6458\u8981\uff1b\u9519\u8fc7\u9009\u80a1\u5206\u949f\u4e0d\u8865\u505a".format(prepare_time, open_time, snapshot_time))
        self.log("\u6570\u636e | \u5f53\u524dQMT ETF\u6c60\u3001\u524d\u65e5\u65e5\u7ebf\u3001PCF\u51c0\u503c\uff1b\u65e5\u671f\u4e3a0/\u7f3a\u5931\u4ec5\u7528\u4e8e\u4eba\u5de5\u5bf9\u7167\uff0c\u5e76\u6807\u8bb0\u672a\u6838\u9a8c")
        if self.paper is not None:
            self.log("\u7eb8\u9762\u6a21\u578b | \u865a\u62df\u8d44\u91d1={:.2f}\uff1b\u4ec5\u6a21\u62df\u5f00\u76d8\u8c03\u4ed3\uff0c\u5148\u5356\u540e\u4e70\uff0c\u4e70\u4ef7\u4ecd\u4e3a\u51b3\u7b56lastPrice\u4e0a\u6d6e0.2%\uff1b"
                     "\u6309lastPrice\u5047\u8bbe\u7acb\u5373\u6210\u4ea4\uff0c\u4e0d\u6a21\u62df\u6392\u961f/\u6df1\u5ea6/\u90e8\u5206\u6210\u4ea4/\u8d39\u7528/T+1/\u98ce\u63a7\uff1b"
                     "\u6ca1\u6709QMT\u63a5\u53e3\u63d0\u4ea4\u3001\u59d4\u6258\u6216\u6210\u4ea4\u56de\u62a5\uff0c\u8017\u65f6\u4e0d\u4ee3\u8868\u5b9e\u76d8\u6210\u4ea4\u5ef6\u8fdf\uff1b\u91cd\u542f\u865a\u62df\u8d44\u91d1\u548c\u6301\u4ed3\u91cd\u7f6e".format(self.paper.cash))

    @archive_after_callback
    def tick(self, now=None):
        now = now or china_now()
        callback_started = time.perf_counter()
        with native_guard(self.guard):
            if self.closed or time.monotonic() < self.retry_at:
                return
            preparing = False
            try:
                today, clock = now.strftime("%Y%m%d"), now.strftime("%H:%M")
                if today != self.day or (not self.is_trading_day and "09:00" <= clock <= "15:00"
                                         and time.time() - self.calendar_checked_at >= 60):
                    previous, trading = self.data.calendar(now)
                    self.calendar_checked_at = time.time()
                    if today != self.day:
                        self.day, self.jobs, self.frame, self.last_decision = today, set(), None, None
                    self.previous, self.is_trading_day = previous, trading
                if not self.is_trading_day:
                    return
                prepare_at, open_at, _, snapshot_at = self.times
                prepare_key = today + ':' + prepare_at
                prepare_due = prepare_at <= clock < open_at and prepare_key not in self.jobs
                warming_due = (self.settings.AUTO_DOWNLOAD_DAILY
                    and self.settings.DAILY_DOWNLOAD_START <= clock < self.settings.DAILY_DOWNLOAD_CUTOFF)
                if (prepare_due or clock == open_at or warming_due) and not self.preparation.ready(today):
                    return
                preparing = True
                warming = self.data.warm_daily_cache(self.previous, now)
                preparing = False
                if prepare_due and warming:
                    return  # Continue bounded downloads on later timers, not repeated full preparation.
                key = today + ":" + clock
                if prepare_due:
                    key = prepare_key
                if key in self.jobs or (not prepare_due and clock not in (open_at, snapshot_at)):
                    return
                if (prepare_due or clock == open_at) and self.frame is None:
                    preparing = True
                    self.frame = self.prepare_fn(self.data, self.previous, today)
                    preparing = False
                    if self.frame is not None and not self.frame.empty:
                        self.data.warm_quotes(self.frame.index.tolist(), now)
                    self.log("\u76d8\u524d\u7b5b\u9009 | \u524d\u65e5\u65e5\u7ebf={} | \u5f53\u524dETF\u6c60={} | \u6e2f\u80a1\u8fc7\u6ee4\u540e={} | \u6d41\u52a8\u6027\u53ca\u51c0\u503c\u7b5b\u9009\u540e={} | \u6210\u4ea4\u989d\u8303\u56f4=({:.0f},{:.0f})".format(
                        self.previous, self.data.universe_count, self.data.daily_count,
                        0 if self.frame is None else len(self.frame), MIN_MONEY, MAX_MONEY))
                if clock == open_at:
                    if self.frame is None or self.frame.empty:
                        self.log("\u9009\u80a1\u8df3\u8fc7 | \u9884\u5904\u7406\u65e0\u5019\u9009\uff1b\u6ca1\u6709\u4efb\u4f55\u4ea4\u6613\u64cd\u4f5c")
                    else:
                        quote_started, started = china_now(), time.perf_counter()
                        quote_now = now + dt.timedelta(seconds=max(0., time.perf_counter() - callback_started))
                        quotes = self.data.opening_quotes(self.frame.index.tolist(), quote_now, open_at)
                        if quotes is None:
                            return  # Preserve the full frame; retry only the quote gate next timer.
                        quote_done = time.perf_counter()
                        self.last_decision = self.select_fn(self.frame, quotes)
                        decision_done = china_now()
                        selected_at = time.perf_counter()
                        self.last_timing = dict(
                            started=callback_started, callback_at=now.isoformat(timespec="milliseconds"),
                            quote_started_at=quote_started.isoformat(timespec="milliseconds"),
                            decision_completed_at=decision_done.isoformat(timespec="milliseconds"),
                            quote_ms=(quote_done - started) * 1000,
                            selection_ms=(selected_at - quote_done) * 1000,
                            callback_to_decision_ms=(selected_at - callback_started) * 1000,
                            first_paper_order_ms=None, first_paper_order_at=None)
                        # Record paper submit times before printing/writing any
                        # decision diagnostics; logging isn't part of the hot path.
                        paper_events = (self.paper.rebalance(self.last_decision, quotes, now, self.last_timing)
                                        if self.paper is not None else [])
                        self.log(self.data.opening_status)
                        self.log("\u9009\u80a1\u7ed3\u679c | \u56de\u8c03\u5f00\u59cb\u65f6\u95f4={} | \u884c\u60c5\u8bfb\u53d6\u5f00\u59cb={} | \u8ba1\u7b97\u5b8c\u6210={} | \u884c\u60c5\u53ca\u9009\u80a1\u8017\u65f6={:.1f}ms | \u9009\u4e2d={} | \u76ee\u6807\u6743\u91cd={}".format(
                            now.strftime("%Y-%m-%d %H:%M:%S"), quote_started.strftime("%H:%M:%S.%f")[:-3],
                            decision_done.strftime("%H:%M:%S.%f")[:-3], (selected_at - started) * 1000,
                            [self.data.label(code) for code in self.last_decision.weights],
                            {self.data.label(code): weight for code, weight in self.last_decision.weights.items()}))
                        for code, weight in self.last_decision.weights.items():
                            price, nav = self.last_decision.marks[code], float(self.frame.loc[code, "unit_net_value"])
                            self.log("\u9009\u80a1\u660e\u7ec6 | {} | \u5355\u4ef7={:.4f} \u5355\u4f4d\u51c0\u503c={:.4f} \u6298\u4ef7\u7387={:.4f}% \u6743\u91cd={:.6f}% \u524d\u65e5\u6210\u4ea4\u989d={:.2f} \u51c0\u503c\u65e5\u671f={}".format(
                                self.data.label(code), price, nav, (price / nav - 1) * 100,
                                weight * 100, float(self.frame.loc[code, "money"]),
                                "\u672a\u6838\u9a8c\uff08PCF\u65e5\u671f\u7f3a\u5931\uff0c\u4e0d\u7528\u4e8e\u4ea4\u6613\uff09" if code in self.data.unverified_nav else self.previous))
                        self.log("\u884c\u60c5\u65f6\u95f4 | \u6700\u540e\u8bfb\u53d6={}\uff1b\u65e0QMT\u4e0b\u5355\u63a5\u53e3\u8c03\u7528".format(self.data.last_quote_time))
                        for event in paper_events:
                            self.log(event)
                        if self.paper is not None:
                            timing = self.last_timing
                            scheduled = now.replace(hour=int(open_at[:2]), minute=int(open_at[3:]), second=0, microsecond=0)
                            self.log("\u7eb8\u9762\u8017\u65f6 | \u8ba1\u5212\u65f6\u95f4={} \u56de\u8c03\u65f6\u95f4={} \u5b9a\u65f6\u89e6\u53d1\u5ef6\u540e={:.1f}ms \u884c\u60c5={:.1f}ms "
                                     "\u9009\u80a1={:.1f}ms \u56de\u8c03\u81f3\u51b3\u7b56={:.1f}ms \u76ee\u6807\u8ba1\u7b97={:.1f}ms \u9996\u7b14\u7eb8\u9762\u4e0b\u5355={} "
                                     "\u56de\u8c03\u81f3\u9996\u7b14={} \u7eb8\u9762\u5904\u7406={:.1f}ms \u7b14\u6570={} | \u4e0d\u542b\u67dc\u53f0/\u7f51\u7edc/\u64ae\u5408\u8017\u65f6".format(
                                         scheduled.isoformat(timespec="milliseconds"), timing["callback_at"],
                                         (now - scheduled).total_seconds() * 1000, timing["quote_ms"], timing["selection_ms"],
                                         timing["callback_to_decision_ms"], timing["target_ms"],
                                         timing["first_paper_order_at"] or "\u65e0\u59d4\u6258",
                                         "{:.1f}ms".format(timing["first_paper_order_ms"])
                                         if timing["first_paper_order_ms"] is not None else "\u65e0\u59d4\u6258",
                                         timing["paper_ms"], timing["paper_order_count"]))
                elif clock == snapshot_at:
                    self.log("\u9009\u80a1\u6458\u8981 | \u4eca\u65e5\u9009\u4e2d={}\uff1b\u4e0d\u8ba1\u7b97\u6301\u4ed3/\u6536\u76ca\uff0c\u4e0d\u4ee3\u8868\u5df2\u6210\u4ea4".format(
                        None if self.last_decision is None else [self.data.label(code) for code in self.last_decision.weights]))
                    if self.paper is not None:
                        self.log("\u7eb8\u9762\u8d26\u6237\u6458\u8981 | \u865a\u62df\u73b0\u91d1={:.2f} \u865a\u62df\u6301\u4ed3={}\uff1b\u4e0d\u662f\u771f\u5b9e\u8d26\u6237\uff0c\u8d39\u7528/\u6ed1\u70b9\u672a\u6a21\u62df\uff0c\u4e0d\u4f5c\u4e3a\u6536\u76ca\u62a5\u544a".format(
                            self.paper.cash, {self.data.label(code): pos["qty"] for code, pos in self.paper.positions.items() if pos["qty"]}))
                self.jobs.add(key)
                self.last_error = None
            except OpeningQuotesTimeout as exc:
                self.jobs.add(today + ':' + open_at)
                self.log('\u5f00\u76d8\u884c\u60c5\u7b49\u5f85\u8d85\u65f6\uff0c\u4fdd\u7559\u539f\u6301\u4ed3\uff0c\u672c\u8f6e\u4e0d\u8c03\u4ed3 | ' + str(exc))
            except Exception as exc:
                action = (self.preparation.failed(exc) if preparing else
                          'retry after query interval; no order was submitted by selection-only mode')
                message = "\u9009\u80a1\u9a8c\u8bc1\u5931\u8d25 | \u539f\u56e0={} | {}".format(exc, action)
                if message != self.last_error:
                    self.log(message)
                    self.last_error = message
                self.retry_at = time.monotonic() + max(1, self.settings.QUERY_SECONDS)

    def handlebar(self):
        pass  # 初始化历史bar不参与实时选股，只有实时定时器触发。

    def on_report(self, kind, row):
        pass  # 不认领或维护任何账户订单/成交。

    def on_error(self, args, error):
        pass

    @archive_after_callback
    def close(self):
        with native_guard(self.guard):
            self.closed = True  # 没有账本锁或柜台委托需要处理。
            self.data.close_quote_watch()


class Runtime:
    def __init__(self, context, namespace, settings, prepare_fn, select_fn, risk_fn):
        self.c, self.settings = context, settings
        self.prepare_fn, self.select_fn, self.risk_fn = prepare_fn, select_fn, risk_fn
        self.data = QmtData(context, namespace, settings, self.log,
                            allow_undated_nav=settings.ALLOW_UNDATED_LIVE_NAV)
        self.account = settings.ACCOUNT_ID or str(namespace.get("account", ""))
        if not self.account:
            raise RuntimeError("\u8bf7\u5728 QMT \u7b56\u7565\u4ea4\u6613\u754c\u9762\u9009\u62e9\u8d26\u6237\uff0c\u6216\u586b\u5199 ACCOUNT_ID")
        if positive_capital(settings.INITIAL_CAPITAL) is False:
            raise RuntimeError("INITIAL_CAPITAL \u5fc5\u987b\u4e3a\u6b63\u6570")
        if getattr(context, "do_back_test", False):
            raise RuntimeError("\u672c\u7248\u672c\u4f7f\u7528\u6bcf\u65e5\u66f4\u65b0 PCF\uff0c\u4ec5\u652f\u6301\u5b9e\u65f6\u8fd0\u884c\uff0c\u4e0d\u652f\u6301\u5386\u53f2\u56de\u6d4b")
        self.guard = threading.RLock()
        self.closed = False
        self.notices, self.last_sync, self.day, self.frame = {}, 0, "", None
        self.preparation = PreparationGuard(settings)
        self.calendar_checked_at = 0
        self.executor = Executor(self.data, settings, self.account, self.emit)
        self.last_timing = None

    @native_measured('local.log_stdout_and_memory')
    def log(self, text):
        now = china_now()
        line = "{} [{}] {}".format(now.isoformat(sep=" ", timespec="milliseconds"), self.settings.STRATEGY_ID, text)
        buffer_native_line(self, line, now)
        if print_timing_only(text, line):
            return
        try:
            print(line)
        except Exception:
            pass

    def handlebar(self):
        pass  # 实时初始化遍历历史 bar，不能在这里触发 quickTrade=2

    def emit(self, title, code="", body=""):
        try:
            label = self.data.label(code) if code else ""
        except Exception:
            label = code  # 名称查询失败不能影响成交入账或发单状态
        title = "[{}] {} {}".format(self.settings.STRATEGY_ID, title, label).strip()
        key = (title, body)
        if key in self.notices:  # 相同原因本次运行只通知一次，避免每秒刷卡片
            return
        self.notices[key] = True
        self.log(title + " | " + body.replace("\n", " | "))
        if not self.settings.FEISHU_WEBHOOK:
            return
        payload = {"msg_type": "interactive", "card": {
            "header": {"title": {"tag": "plain_text", "content": title}},
            "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": "\u7b56\u7565ID: " + self.settings.STRATEGY_ID + "\n" + body}}]}}
        # 只有通知线程可访问网络，不在工作线程调用任何 QMT API。
        def send():
            try:
                request = urllib.request.Request(self.settings.FEISHU_WEBHOOK,
                    data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=5) as response:
                    result = json.load(response)
                if result.get("code", result.get("StatusCode", 0)) != 0:
                    self.log("\u98de\u4e66\u901a\u77e5\u5931\u8d25\uff08\u4ea4\u6613\u5165\u8d26\u4e0d\u53d7\u5f71\u54cd\uff09")
            except Exception:
                self.log("\u98de\u4e66\u901a\u77e5\u53d1\u9001\u5931\u8d25\uff08\u4ea4\u6613\u5165\u8d26\u4e0d\u53d7\u5f71\u54cd\uff09")
        threading.Thread(target=send, daemon=True).start()

    def start(self, prepare_time, open_time, risk_times, snapshot_time):
        self.times = prepare_time, open_time, tuple(risk_times), snapshot_time
        self.log("[strategy_name] " + self.settings.STRATEGY_NAME)
        self.c.set_account(self.account)
        self.c.run_time("on_timer", "1nSecond", china_now().strftime("%Y-%m-%d %H:%M:%S"))
        self.log('[opening_quotes] build=20261020_quotes_v1 wait_seconds={} require_all_tradable=True'.format(
            self.settings.OPEN_QUOTE_WAIT_SECONDS))
        self.log("\u72ec\u7acb\u5927QMT\u7248 | \u4ea4\u6613={} | \u8d26\u672c={} | \u4e0d\u4f9d\u8d56\u805a\u5bbd/\u670d\u52a1\u5668/bridge\uff1b\u540c\u8d26\u6237\u52ff\u4e0e\u65e7remote\u540c\u65f6\u4ea4\u6613".format(
            self.settings.ENABLE_TRADING, self.executor.path))
        self.log("\u6570\u636e\u6765\u6e90: QMT\u4f18\u5148\uff1bPCF\u7f3a\u5931\u65f6\u53ef\u7528\u524d\u65e5NAV_FILE\u515c\u5e95\uff1b\u4e0d\u7528IOPV\u6216\u4eca\u65e5\u6570\u636e\u66ff\u4ee3\u5386\u53f2\u51c0\u503c")
        self.log("\u51c0\u503c\u89c4\u5219 | \u63a5\u53d7\u7f3a\u5931\u65e5\u671f={} | \u660e\u786e\u9519\u8bef\u65e5\u671f\u4ecd\u6392\u9664\uff1b\u7f3a\u5931\u65e5\u671f\u4e0d\u8bc1\u660ePCF\u5df2\u66f4\u65b0\uff0c"
                 "\u8bf7\u6bcf\u65e5\u5728QMT\u6570\u636e\u7ba1\u7406\u66f4\u65b0ETF\u7533\u8d4e\u6e05\u5355".format(self.settings.ALLOW_UNDATED_LIVE_NAV))

    @archive_after_callback
    def close(self):
        # stop 时仅释放自有行情订阅和本地资源，不查询账户、下单或撤单。
        with native_guard(self.guard):
            if self.closed:
                return
            self.closed = True
            self.data.close_quote_watch()
            self.executor.close()
            self.log("\u7b56\u7565\u5df2\u505c\u6b62 | \u8d26\u672c\u9501\u5df2\u91ca\u653e | \u8d26\u672c\u4fdd\u7559\uff1b\u672a\u64a4\u9500\u67dc\u53f0\u59d4\u6258")

    @native_traced('native.report_callback')
    def on_report(self, kind, row):
        received_at, received_mono = china_now(), time.perf_counter()
        with native_guard(self.guard):
            if self.closed:
                return
            try:
                self.executor.report(kind, row, source="\u56de\u8c03", received_at=received_at, received_mono=received_mono)
                self.last_sync = 0  # 下一次定时器查询补全，并推进剩余量；回调内不递归下单
            except Exception as exc:
                self.emit("\u56de\u62a5\u5904\u7406\u963b\u65ad", body="\u539f\u56e0: " + str(exc))

    def on_error(self, args, error):
        # 官方 orderArgs.strategyName 的格式为 策略名_&&&_投资备注。
        tag = str(value(args, "strategyName", "")).rsplit("_&&&_", 1)[-1]
        with native_guard(self.guard):
            if self.closed:
                return
            order = self.executor.state["orders"].get(tag)
            if order is not None:
                order["error"] = str(error)
                self.executor.save()
                self.emit("\u4e0b\u5355\u5f02\u5e38", order["code"], "\u539f\u56e0: " + str(error) + "\n\u5904\u7406: \u7b49\u5f85\u59d4\u6258\u67e5\u8be2\u786e\u8ba4\uff0c\u4e0d\u76f2\u76ee\u91cd\u53d1")
            elif str(value(args, "strategyName", "")).startswith(self.settings.STRATEGY_ID):
                self.emit("\u4e0b\u5355\u5f02\u5e38", body="\u539f\u56e0: " + str(error) + "\n\u5904\u7406: \u672a\u80fd\u5173\u8054\u6295\u8d44\u5907\u6ce8\uff0c\u8bf7\u68c0\u67e5\u67dc\u53f0\u59d4\u6258")

    @archive_after_callback
    @native_traced('native.timer')
    def tick(self, now=None):
        now = now or china_now()
        callback_started = time.perf_counter()
        with native_guard(self.guard):
            if self.closed:
                return
            preparing = False
            try:
                opening_timing, sync_ms = None, 0.0
                today, clock = now.strftime("%Y%m%d"), now.strftime("%H:%M")
                if today != self.day or (not self.is_trading_day and "09:00" <= clock <= "15:00"
                                         and time.time() - self.calendar_checked_at >= 60):
                    self.previous, self.is_trading_day = self.data.calendar(now)
                    self.calendar_checked_at = time.time()
                    if today != self.day:
                        self.frame, self.day, self.notices = None, today, {}
                if time.time() - self.last_sync >= self.settings.QUERY_SECONDS:
                    sync_started = time.perf_counter()
                    self.executor.sync(today)
                    self.executor.expire(today)  # 午夜/重启处理隔日委托，绝不把未知直接当已撤
                    self.last_sync = time.time()
                    sync_ms = (time.perf_counter() - sync_started) * 1000
                if not self.is_trading_day:
                    return
                prepare_at, open_at, risk_times, snapshot_at = self.times
                prepare_key = today + ':' + prepare_at
                prepare_due = prepare_at <= clock < open_at and prepare_key not in self.executor.state['jobs']
                warming_due = (self.settings.AUTO_DOWNLOAD_DAILY
                    and self.settings.DAILY_DOWNLOAD_START <= clock < self.settings.DAILY_DOWNLOAD_CUTOFF)
                if (prepare_due or clock == open_at or warming_due) and not self.preparation.ready(today):
                    self.executor.advance(now)
                    return
                preparing = True
                warming = self.data.warm_daily_cache(self.previous, now)
                preparing = False
                key = today + ":" + clock
                if key not in self.executor.state["jobs"]:
                    if prepare_due:
                        if warming:
                            key = None
                        else:
                            preparing = True
                            self.frame = self.prepare_fn(self.data, self.previous, today)
                            preparing = False
                            if self.frame is not None and not self.frame.empty:
                                self.data.warm_quotes(self.frame.index.tolist(), now)
                            key = prepare_key
                            self.log("\u76d8\u524d\u5019\u9009: {}".format(0 if self.frame is None else len(self.frame)))
                    elif clock == open_at:
                        if self.frame is None:
                            preparing = True
                            self.frame = self.prepare_fn(self.data, self.previous, today)
                            preparing = False
                            if self.frame is not None and not self.frame.empty:
                                self.data.warm_quotes(self.frame.index.tolist(), now)
                        if self.frame is not None and not self.frame.empty:
                            quote_started = time.perf_counter()
                            quote_now = now + dt.timedelta(seconds=max(0., time.perf_counter() - callback_started))
                            quotes = self.data.opening_quotes(self.frame.index.tolist(), quote_now, open_at)
                            if quotes is None:
                                self.executor.advance(now)  # Existing targets/reports are not blocked.
                                return
                            quote_done = time.perf_counter()
                            decision = self.select_fn(self.frame, quotes)
                            decision_done = time.perf_counter()
                            opening_timing = dict(day=today, started=callback_started,
                                callback_at=now.isoformat(timespec="milliseconds"), sync_ms=sync_ms,
                                quote_ms=(quote_done - quote_started) * 1000,
                                selection_ms=(decision_done - quote_done) * 1000,
                                first_submit_at=None, first_submit_ms=None)
                            self.last_timing = self.executor.rebalance_timing = opening_timing
                            opening_timing["nav_details"] = {
                                code: dict(self.data.nav_details[code]) for code in decision.weights
                                if code in self.data.nav_details}
                            self.executor.rebalance(decision, now)
                            opening_timing["target_preparation_ms"] = (time.perf_counter() - decision_done) * 1000
                        else:
                            self.log("\u9884\u5904\u7406\u65e0\u5019\u9009\uff0c\u8df3\u8fc7\u672c\u8f6e\uff1b\u6ca1\u6709\u53d1\u51fa\u6e05\u4ed3\u6307\u4ee4")
                    elif clock in risk_times:
                        self.executor.rebalance_timing = None
                        self.executor.risk(self.risk_fn, now)
                    elif clock == snapshot_at:
                        equity = self.executor.mark_to_market(now)
                        fills = list(self.executor.state["fills"].values())
                        unknown = sum(row["fee"] is None for row in fills)
                        estimated = sum(bool(row.get("estimated")) for row in fills)
                        self.emit("\u5c3e\u76d8\u5feb\u7167", body="\u7b56\u7565\u6743\u76ca: {:.2f}\n\u51c0\u503c: {:.6f}\n\u5df2\u77e5\u624b\u7eed\u8d39: {:.2f}\n\u672a\u77e5\u624b\u7eed\u8d39\u6210\u4ea4: {} \u7b14\n\u4f30\u7b97\u6210\u4ea4: {} \u7b14\n\u6536\u76ca\u53e3\u5f84: {}\n\u6700\u540e\u884c\u60c5\u65f6\u95f4: {}".format(
                            equity, equity / self.settings.INITIAL_CAPITAL,
                            sum(row["fee"] or 0 for row in fills), unknown, estimated,
                            "\u975e\u7cbe\u786e\u6536\u76ca" if unknown or estimated else "\u5df2\u77e5\u56de\u62a5\u8d39\u7528\u53e3\u5f84", self.data.last_quote_time))
                    else:
                        key = None
                    if key:
                        self.executor.state["jobs"].append(key)
                        self.executor.save()
                self.executor.advance(now)
                if opening_timing is not None:
                    self.log(self.data.opening_status)  # Diagnostics after the first possible submit.
                    scheduled = now.replace(hour=int(open_at[:2]), minute=int(open_at[3:]), second=0, microsecond=0)
                    self.log("\u5b9e\u5355\u8017\u65f6 | \u8ba1\u5212\u65f6\u95f4={} \u56de\u8c03\u65f6\u95f4={} \u5b9a\u65f6\u89e6\u53d1\u5ef6\u540e={:.1f}ms \u5b9a\u65f6\u67e5\u8be2={:.1f}ms "
                             "\u884c\u60c5={:.1f}ms \u9009\u80a1={:.1f}ms \u76ee\u6807\u51c6\u5907\uff08\u542b\u67e5\u8be2/\u4f30\u503c/\u843d\u76d8\uff09={:.1f}ms "
                             "\u9996\u7b14\u63a5\u53e3\u8c03\u7528\u65f6\u95f4={} \u56de\u8c03\u81f3\u9996\u7b14={} | \u63a5\u53e3\u8c03\u7528\u4e0d\u4ee3\u8868\u59d4\u6258\u63a5\u53d7\u6216\u6210\u4ea4\uff0c\u987b\u7ed3\u5408\u56de\u62a5\u65e5\u5fd7".format(
                                 scheduled.isoformat(timespec="milliseconds"), opening_timing["callback_at"],
                                 (now - scheduled).total_seconds() * 1000, sync_ms, opening_timing["quote_ms"],
                                 opening_timing["selection_ms"], opening_timing["target_preparation_ms"],
                                 opening_timing["first_submit_at"] or "\u5c1a\u65e0\u59d4\u6258",
                                 "{:.1f}ms".format(opening_timing["first_submit_ms"])
                                 if opening_timing["first_submit_ms"] is not None else "\u7b49\u5f85\u6267\u884c\u6761\u4ef6"))
                    for code, detail in opening_timing["nav_details"].items():
                        self.log("\u5165\u9009\u51c0\u503c | {} nav={} \u539f\u59cbpreTradingDay={} \u539f\u59cbtradingDay={} \u65e5\u671f={} "
                                 "PCF\u8bfb\u53d6\u65f6\u95f4={} | \u8bfb\u53d6\u65f6\u95f4\u4e0d\u662f\u51c0\u503c\u6240\u5c5e\u65e5\u671f".format(
                                     self.data.label(code), detail["nav"], detail["preTradingDay"], detail["tradingDay"],
                                     self.previous if detail["date_verified"] else "\u672a\u6838\u9a8c\uff0c\u6309QMT\u8fd4\u56de\u503c\u4f7f\u7528",
                                     self.data.nav_read_at))
            except OpeningQuotesTimeout as exc:
                job = today + ':' + open_at
                if job not in self.executor.state['jobs']:
                    self.executor.state['jobs'].append(job)
                    self.executor.save()  # Restart must not retry a timed-out opening decision.
                self.emit('\u5f00\u76d8\u884c\u60c5\u7b49\u5f85\u8d85\u65f6', body='\u4fdd\u7559\u539f\u6301\u4ed3\uff0c\u672c\u8f6e\u4e0d\u8c03\u4ed3\n' + str(exc))
                self.executor.advance(now)
            except Exception as exc:
                action = (self.preparation.failed(exc) if preparing else
                          '\u4e0b\u4e00\u6b21\u8c03\u5ea6\u91cd\u65b0\u68c0\u67e5')
                self.emit("\u8fd0\u884c\u963b\u65ad", body="\u539f\u56e0: " + str(exc)
                          + "\n\u5904\u7406: \u4fdd\u7559\u8d26\u672c\u4e0e\u59d4\u6258; " + action)


def positive_capital(amount):
    from math import isfinite
    return isinstance(amount, (int, float)) and isfinite(amount) and amount > 0


# ==================== QMT 原生回测适配（不创建实时账本） ====================

def backtest_time(raw):
    """QMT \u6beb\u79d2\u65f6\u95f4\u6233\u6309\u4e2d\u56fd\u65f6\u533a\u89e3\u6790\uff1b\u517c\u5bb9\u5386\u53f2 DataFrame \u7684\u65e5\u671f\u7d22\u5f15\u3002"""
    if isinstance(raw, (dt.datetime, pd.Timestamp)):
        if raw.tzinfo is not None:
            raw = raw.astimezone(dt.timezone(dt.timedelta(hours=8)))
        return raw.replace(tzinfo=None)
    text = str(raw)
    if text.isdigit() and len(text) == 14:
        return dt.datetime.strptime(text, "%Y%m%d%H%M%S")
    if isinstance(raw, (int, float)) or (text.isdigit() and len(text) == 13):
        return dt.datetime.fromtimestamp(float(raw) / 1000, dt.timezone(dt.timedelta(hours=8))).replace(tzinfo=None)
    return pd.to_datetime(text).to_pydatetime().replace(tzinfo=None)


class HistoricalQuoteMissing(RuntimeError):
    pass


class BacktestData(QmtData):
    def __init__(self, context, namespace, settings, log):
        super().__init__(context, namespace, settings, log)
        self.sessions, self.session, self.limits = {}, None, {}
        if settings.BACKTEST_DATA_FILE:
            with open(settings.BACKTEST_DATA_FILE, encoding="utf-8-sig") as stream:
                document = json.load(stream)
            if document.get("schema") != 1 or not document.get("sessions"):
                raise RuntimeError("\u56de\u6d4b\u6570\u636e\u683c\u5f0f\u9519\u8bef\uff1a\u9700\u8981 schema=1 \u548c\u975e\u7a7a sessions")
            self.sessions = document["sessions"]

    def set_day(self, today, previous):
        session = self.sessions.get(today)
        if (previous >= today or (self.sessions and (not session
                or session.get("previous_date") != previous
                or not isinstance(session.get("securities"), dict)))):
            raise RuntimeError("\u5386\u53f2\u6570\u636e\u7f3a\u5931\u6216\u524d\u4ea4\u6613\u65e5\u4e0d\u5339\u914d: " + today + " / " + previous)
        fallback = session["securities"] if session else {}
        self.today, self.limits = today, {}
        asof = dt.datetime.strptime(previous + "235959", "%Y%m%d%H%M%S")
        asof = int(asof.replace(tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp() * 1000)
        try:
            pool = super().universe(previous, asof=asof)
        except Exception as exc:
            self.log("QMT\u5386\u53f2ETF\u6c60\u8bfb\u53d6\u5931\u8d25 | \u65e5\u671f={} | \u539f\u56e0={}".format(previous, exc))
            if not session:
                raise RuntimeError("QMT\u5386\u53f2ETF\u6c60\u4e0d\u53ef\u7528\uff1b\u8bf7\u68c0\u67e5ETF_SECTOR/\u5386\u53f2\u677f\u5757\u63a5\u53e3\uff0c\u6216\u586b\u5199BACKTEST_DATA_FILE") from exc
            self.session = session
            self.log("\u5386\u53f2ETF\u6c60\u4f7f\u7528BACKTEST_DATA_FILE\u515c\u5e95")
        else:
            securities = {code: dict(fallback.get(code, {}), name=name, index_name=index)
                          for code, name, index in pool}
            self.session = dict(previous_date=previous, securities=securities)
            self.log("QMT\u5386\u53f2ETF\u6c60 | \u8bf7\u6c42\u524d\u65e5={} | \u6570\u91cf={}\uff1b\u7b80\u79f0\u53d6\u5ba2\u6237\u7aef\u8d44\u6599\uff0c\u4e0d\u627f\u8bfa\u5386\u53f2\u540d\u79f0\u7248\u672c".format(previous, len(pool)))

    def universe(self, previous):
        if previous != self.session["previous_date"]:
            raise RuntimeError("\u5386\u53f2 ETF \u6c60\u65e5\u671f\u4e0d\u5339\u914d")
        return [(code, row["name"], row.get("index_name", ""))
                for code, row in self.session["securities"].items()]

    def nav(self, codes, previous, today):
        if today != self.today or previous != self.session["previous_date"]:
            raise RuntimeError("\u5386\u53f2\u5355\u4f4d\u51c0\u503c\u65e5\u671f\u4e0d\u5339\u914d")
        rows = self.pcf_nav(codes, previous, today)
        count = len(rows)
        for code in codes:
            if code not in rows:
                nav = positive(self.session["securities"].get(code, {}).get("nav"))
                if nav is not None:
                    rows[code] = nav
        if len(rows) > count:
            self.log("BACKTEST_DATA_FILE\u4ec5\u8865QMT\u7f3a\u5931\u51c0\u503c {} \u53ea".format(len(rows) - count))
        missing = [code for code in codes if code not in rows]
        if missing:
            self.log("\u5386\u53f2\u524d\u65e5\u51c0\u503c\u7f3a\u5931 {} \u53ea: {}\uff1b\u6309\u539f\u903b\u8f91\u6392\u9664\uff0c\u4e0d\u4f7f\u7528 PCF/IOPV".format(len(missing), missing[:10]))
        if not rows:
            raise RuntimeError("QMT\u672a\u8fd4\u56de\u53ef\u9a8c\u8bc1\u7684\u5386\u53f2\u524d\u65e5\u5355\u4f4d\u51c0\u503c: {}\uff1b\u8bf7\u770bQMT\u51c0\u503c\u63a5\u53e3\u65e5\u5fd7\uff0c\u82e5\u8fd4\u56de\u6700\u65b0PCF\u6216\u6743\u9650\u4e0d\u8db3\uff0c\u53ef\u586b\u5199BACKTEST_DATA_FILE\uff1b\u4e0d\u7528IOPV\u66ff\u4ee3".format(previous))
        return frame_from_rows(rows, ["unit_net_value"])

    def high_limits(self, codes, now):
        missing = [code for code in codes if code not in self.limits]
        if not missing:
            return
        self.limits.update({code: None for code in missing})
        try:
            frames = self.c.get_market_data_ex(
                [], missing, period="stoppricedata", start_time=self.today + "000000",
                end_time=now.strftime("%Y%m%d%H%M%S"), count=1,
                dividend_type="none", fill_data=False, subscribe=False)
            for code in missing:
                frame = frames.get(code)
                if frame is None or frame.empty:
                    continue
                bar = frame.iloc[-1]
                stamp = backtest_time(bar["time"])
                if stamp.strftime("%Y%m%d") != self.today or stamp > now:
                    continue  # 不能使用别日/未来的涨跌停数据
                ceiling = positive(bar.get("\u6da8\u505c\u4ef7"))
                if ceiling is not None:
                    self.limits[code] = ceiling
        except Exception as exc:
            self.log("QMT\u5386\u53f2\u6da8\u505c\u4ef7\u63a5\u53e3\u4e0d\u53ef\u7528 | period=stoppricedata | \u539f\u56e0={}".format(exc))
        count = sum(self.limits[code] is not None for code in missing)
        self.log("QMT\u5386\u53f2\u6da8\u505c\u4ef7 | \u65e5\u671f={} | \u6709\u6548\u8986\u76d6={}/{}\uff1b\u7f3a\u5931\u65f6\u4ec5\u7528\u540c\u65e5\u6587\u4ef6\u515c\u5e95\uff0c\u4e0d\u630910%\u731c\u6d4b".format(self.today, count, len(missing)))

    def label(self, code):
        row = self.session["securities"].get(code, {}) if self.session else {}
        return "{}({})".format(code, row.get("name", "\u5386\u53f2\u540d\u79f0\u7f3a\u5931"))

    def quotes(self, codes, now):
        if not codes:
            return {}
        end = now.strftime("%Y%m%d%H%M%S")
        frames = self.c.get_market_data_ex(
            ["close", "suspendFlag"], list(codes), period="1m",
            start_time=now.strftime("%Y%m%d") + "093000", end_time=end, count=1,
            dividend_type="none", fill_data=False, subscribe=False)
        rows = {}
        for code in codes:
            frame = frames.get(code)
            if frame is None or frame.empty:
                raise HistoricalQuoteMissing("\u5386\u53f2\u5206\u949f\u884c\u60c5\u7f3a\u5931: " + code + " / " + end)
            stamp, bar = backtest_time(frame.index[-1]), frame.iloc[-1]
            if stamp < now:
                raise HistoricalQuoteMissing("\u5386\u53f2\u5206\u949f\u884c\u60c5\u8fc7\u671f: " + code + " / " + end)
            if stamp > now:
                raise RuntimeError("\u5386\u53f2\u63a5\u53e3\u8fd4\u56de\u672a\u6765\u884c\u60c5\uff0c\u62d2\u7edd\u4f7f\u7528: " + code + " / " + end)
            price = positive(bar.get("close"))
            flag = bar.get("suspendFlag")
            if price is None or flag not in (0, 1):
                raise RuntimeError("\u5386\u53f2\u5206\u949f\u4ef7\u683c\u6216 suspendFlag \u7f3a\u5931: " + code)
            rows[code] = dict(last_price=price, paused=bool(flag))
        # 09:30尚无完整bar时先等待，不提前缓存一次失败的涨停价查询。
        self.high_limits(codes, now)
        fallback_count = 0
        for code, row in rows.items():
            meta = self.session["securities"].get(code, {})
            ceiling = self.limits.get(code)
            if ceiling is None:
                if "high_limit" not in meta:
                    raise RuntimeError("QMT\u5386\u53f2\u6da8\u505c\u4ef7\u7f3a\u5931\uff08stoppricedata\u53ef\u80fd\u9700\u6743\u9650/\u4e0b\u8f7d\uff0c\u4e0d\u80fd\u5957\u7528\u4eca\u5929\u7684\u6da8\u505c\u4ef7\uff09: " + code + "\uff1b\u53ef\u586b\u5199BACKTEST_DATA_FILE")
                ceiling = meta["high_limit"]
                fallback_count += 1
            if ceiling is not None and positive(ceiling) is None:
                raise RuntimeError("\u5386\u53f2\u6da8\u505c\u4ef7\u65e0\u6548: " + code)
            row["high_limit"] = float("inf") if ceiling is None else float(ceiling)
        if fallback_count:
            self.log("\u5386\u53f2\u6da8\u505c\u4ef7\u4f7f\u7528BACKTEST_DATA_FILE\u515c\u5e95 {} \u53ea".format(fallback_count))
        self.last_quote_time = end
        return rows


class BacktestRuntime:
    """\u4ec5\u4f7f\u7528 QMT \u56de\u6d4b\u8d26\u6237\u548c\u64ae\u5408\uff1b\u4e0d\u590d\u7528\u5b9e\u65f6\u8ba2\u5355\u72b6\u6001\u673a\u6216\u6301\u4e45\u8d26\u672c\u3002"""
    def __init__(self, context, namespace, settings, prepare_fn, select_fn, risk_fn):
        if not getattr(context, "do_back_test", False):
            raise RuntimeError("\u56de\u6d4b\u9002\u914d\u5668\u4e0d\u80fd\u7528\u4e8e\u5b9e\u65f6\u4ea4\u6613")
        if str(context.period).lower() != "1m":
            raise RuntimeError("GoodETF \u56de\u6d4b\u8bf7\u5728 QMT \u8bbe\u7f6e 1 \u5206\u949f\u5468\u671f\uff0c\u4e0d\u652f\u6301\u7528\u65e5\u7ebf\u4f2a\u9020\u76d8\u4e2d\u98ce\u63a7")
        if context.dividend_type != "none":
            raise RuntimeError("GoodETF \u56de\u6d4b\u8bf7\u8bbe\u7f6e\u4e0d\u590d\u6743\uff0c\u4e0d\u80fd\u7528\u590d\u6743\u6210\u4ea4\u4ef7\u4e0e\u5355\u4f4d\u51c0\u503c\u6df7\u7b97\u6298\u4ef7")
        if not positive_capital(context.capital):
            raise RuntimeError("\u8bf7\u5728 QMT \u56de\u6d4b\u9762\u677f\u8bbe\u7f6e\u6709\u6548\u521d\u59cb\u8d44\u91d1")
        self.c, self.settings = context, settings
        self.prepare_fn, self.select_fn, self.risk_fn = prepare_fn, select_fn, risk_fn
        self.now, self.day, self.jobs, self.closed, self.last_bar = None, "", set(), False, None
        self.data = BacktestData(context, namespace, settings, self.log)
        self.account = "good_etf_backtest"  # 虚拟标识；绝不采用 ACCOUNT_ID 或界面柜台账号
        self.c.account_id = self.account

    def start(self, prepare_time, open_time, risk_times, snapshot_time):
        self.times = prepare_time, open_time, tuple(risk_times), snapshot_time
        self.log("QMT\u5386\u53f2\u56de\u6d4b | \u6570\u636eQMT\u4f18\u5148\uff0c\u5916\u90e8\u6587\u4ef6\u53ef\u9009\u515c\u5e95 | \u8d44\u91d1/\u8d39\u7528/\u6ed1\u70b9\u53d6\u56de\u6d4b\u9762\u677f | 1\u5206\u949f\u5df2\u5b8c\u6210K\u7ebf | \u5f00\u76d8\u5141\u8bb809:31\u9996bar | \u539f\u751f\u64ae\u5408\uff0c\u4e0d\u6a21\u62df\u67dc\u53f0\u8ffd\u5355")

    def log(self, text):
        print("{} [{}][\u56de\u6d4b] {}".format(self.now or "\u521d\u59cb\u5316", self.settings.STRATEGY_ID, text))

    def query(self, kind):
        if self.closed or not getattr(self.c, "do_back_test", False):
            raise RuntimeError("\u56de\u6d4b\u5df2\u505c\u6b62\u6216\u73af\u5883\u5df2\u5207\u6362\uff0c\u62d2\u7edd\u67e5\u8be2\u8d26\u6237")
        rows = self.data.api("get_trade_detail_data")(self.account, "stock", kind)
        if rows is None:
            raise RuntimeError("QMT \u56de\u6d4b\u67e5\u8be2\u672a\u8fd4\u56de\u6570\u636e: " + kind)
        return list(rows)

    def portfolio(self):
        accounts = self.query("account")
        equity = float(value(accounts[0], "m_dBalance", float("nan"))) if len(accounts) == 1 else float("nan")
        if not math.isfinite(equity) or equity < 0:
            raise RuntimeError("QMT \u56de\u6d4b\u8d26\u6237\u672a\u5c31\u7eea\uff0c\u4e0d\u80fd\u4f7f\u7528\u67dc\u53f0\u8d44\u91d1\u66ff\u4ee3")
        positions = {value(row, "m_strInstrumentID") + "." + value(row, "m_strExchangeID"): row
                     for row in self.query("position") if value(row, "m_nVolume", 0) > 0}
        return equity, positions

    def submit(self, code, target):
        if self.closed or not getattr(self.c, "do_back_test", False):
            raise RuntimeError("\u56de\u6d4b\u5df2\u505c\u6b62\u6216\u73af\u5883\u5df2\u5207\u6362\uff0c\u62d2\u7edd\u63d0\u4ea4")
        self.data.api("order_target_value")(code, float(target), self.c, self.account)
        self.log("\u56de\u6d4b\u76ee\u6807\u5e02\u503c | {} -> {:.2f} | LATEST\uff1b\u5b9e\u9645\u6210\u4ea4\u4ee5QMT\u56de\u6d4b\u9762\u677f\u4e3a\u51c6".format(self.data.label(code), target))

    def rebalance(self, decision):
        equity, positions = self.portfolio()
        codes = list(dict.fromkeys(list(positions) + list(decision.weights)))
        quotes = self.data.quotes(codes, self.now)  # 提交前检查整组行情，避免一半提交后才发现数据错误
        targets = {code: equity * decision.weights.get(code, 0) for code in codes}
        reductions, increases = [], []
        for code in codes:
            current = value(positions.get(code, {}), "m_nVolume", 0) * quotes[code]["last_price"]
            if targets[code] < current:
                reductions.append(code)
            elif targets[code] > current:
                increases.append(code)
        for code in reductions + increases:
            self.submit(code, targets[code])

    def risk(self):
        _, positions = self.portfolio()
        quotes = self.data.quotes(list(positions), self.now)
        for code, row in positions.items():
            cost, price = positive(value(row, "m_dOpenPrice")), quotes[code]["last_price"]
            if cost is None:
                raise RuntimeError("QMT \u56de\u6d4b\u6301\u4ed3\u6210\u672c\u7f3a\u5931: " + code)
            signal = self.risk_fn(cost, price)
            self.log("\u6301\u4ed3\u68c0\u67e5 | {} \u6210\u672c={:.4f} \u73b0\u4ef7={:.4f} \u4fe1\u53f7={}".format(self.data.label(code), cost, price, signal))
            if signal and not quotes[code]["paused"]:
                self.submit(code, 0)

    def handlebar(self):
        try:
            self._handlebar()
        except Exception as exc:
            self.closed = True
            self.log("\u56de\u6d4b\u5df2\u505c\u6b62 | \u539f\u56e0: {} | \u672c\u6b21\u7ed3\u679c\u4e0d\u5b8c\u6574\uff0c\u4e0d\u5e94\u4f5c\u4e3a\u6709\u6548\u6536\u76ca\u62a5\u544a".format(exc))
            raise

    def _handlebar(self):
        if self.closed:
            return
        if not getattr(self.c, "do_back_test", False):
            raise RuntimeError("\u56de\u6d4b\u73af\u5883\u5df2\u5207\u6362\uff0c\u62d2\u7edd\u6267\u884c\u5386\u53f2bar")
        now = backtest_time(self.c.get_bar_timetag(self.c.barpos))
        start, end = getattr(self.c, "start", ""), getattr(self.c, "end", "")
        if (start and now < backtest_time(start)) or (end and now > backtest_time(end)):
            return  # QMT 若遍历区间外预热 bar，不将其作为正式回测交易
        if self.last_bar is not None and now <= self.last_bar:
            return
        self.now, self.last_bar = now, now
        today, clock = now.strftime("%Y%m%d"), now.strftime("%H:%M")
        if today != self.day:
            previous, trading = self.data.calendar(now)
            if not trading:
                return
            self.data.set_day(today, previous)
            self.day, self.jobs, self.frame = today, set(), None
        prepare_at, open_at, risk_times, snapshot_at = self.times
        opening_end = (dt.datetime.strptime(open_at, "%H:%M") + dt.timedelta(minutes=1)).strftime("%H:%M")
        if prepare_at <= clock <= opening_end and "prepare" not in self.jobs:
            self.frame = self.prepare_fn(self.data, self.data.session["previous_date"], today)
            self.jobs.add("prepare")
            self.log("\u76d8\u524d\u5019\u9009: {}".format(0 if self.frame is None else len(self.frame)))
        if open_at <= clock <= opening_end and "open" not in self.jobs:
            if self.frame is not None and not self.frame.empty:
                try:
                    quotes = self.data.quotes(self.frame.index.tolist(), now)
                except HistoricalQuoteMissing:
                    if clock == open_at:
                        self.log("\u5f00\u76d8\u5206\u949f\u7ebf\u5c1a\u672a\u7ed3\u675f\uff0c\u7b49\u5f85\u4e0b\u4e00\u5206\u949f\u9996\u6839\u5b8c\u6574bar\uff1b\u4e0d\u8bfb\u53d6\u672a\u6765\u884c\u60c5")
                        return
                    raise
                decision = self.select_fn(self.frame, quotes)
                self.log("\u5f00\u76d8\u9009\u80a1 | \u76ee\u6807\u6743\u91cd={}".format(decision.weights))
                self.rebalance(decision)
            else:
                self.log("\u9884\u5904\u7406\u65e0\u5019\u9009\uff0c\u8df3\u8fc7\u672c\u8f6e\uff1b\u6ca1\u6709\u53d1\u51fa\u6e05\u4ed3\u6307\u4ee4")
            self.jobs.add("open")
        if clock in risk_times and clock not in self.jobs:
            self.jobs.add(clock)
            self.risk()
        if clock == snapshot_at and "snapshot" not in self.jobs:
            self.jobs.add("snapshot")
            equity, positions = self.portfolio()
            self.log("\u5c3e\u76d8\u5feb\u7167 | \u56de\u6d4b\u6743\u76ca={:.2f} \u51c0\u503c={:.6f} \u6301\u4ed3\u6570={}".format(equity, equity / self.c.capital, len(positions)))

    def tick(self):
        pass  # 不注册也不处理实时定时器

    def on_report(self, kind, row):
        pass  # 回测成交由平台入账，不重复维护现金/持仓

    def on_error(self, args, error):
        pass

    def close(self):
        self.closed = True


def create_runtime(context, namespace, settings, prepare_fn, select_fn, risk_fn):
    check_pandas_environment()
    if getattr(context, "do_back_test", False):
        runtime_type = BacktestRuntime
    else:
        runtime_type = Runtime if settings.ENABLE_TRADING else SelectionRuntime
    return runtime_type(context, namespace, settings, prepare_fn, select_fn, risk_fn)

