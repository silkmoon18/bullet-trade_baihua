import os
import subprocess
import sys
import textwrap


def test_reload_waiting_for_completed_connector_does_not_deadlock_on_runtime_check():
    script = textwrap.dedent(
        """
        import importlib
        import inspect
        import sys
        import threading
        import time

        from helpers import bullet_trade_jq_remote_helper as helper


        class FakeSocket:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True


        connector_entered = threading.Event()
        allow_connector_return = threading.Event()
        post_connector_paused = threading.Event()
        allow_delivery_check = threading.Event()
        reload_started = threading.Event()
        request_errors = []
        reload_errors = []
        fake_socket = FakeSocket()

        def connector(*args, **kwargs):
            connector_entered.set()
            if not allow_connector_return.wait(5):
                raise AssertionError("timed out waiting to return connector")
            return fake_socket

        helper.socket.create_connection = connector
        client = helper._ShortLivedClient(
            "127.0.0.1",
            58620,
            "unit-test-token",
            retries=0,
        )
        socket_code = helper._create_runtime_socket_with_lease.__code__
        socket_lines, socket_first_line = inspect.getsourcelines(socket_code)
        post_connector_line = socket_first_line + next(
            index
            for index, line in enumerate(socket_lines)
            if line.strip().startswith(
                "if type(socket_handoff_state) is list"
            )
        )

        def trace(frame, event, arg):
            if (
                frame.f_code is socket_code
                and event == "line"
                and frame.f_lineno == post_connector_line
                and not post_connector_paused.is_set()
            ):
                post_connector_paused.set()
                if not allow_delivery_check.wait(5):
                    raise AssertionError("timed out waiting for delivery check")
            return trace

        def request_worker():
            sys.settrace(trace)
            try:
                client.request("broker.place_order", {"amount": 1})
            except BaseException as exc:
                request_errors.append(exc)
            finally:
                sys.settrace(None)

        def reload_worker():
            global helper
            reload_started.set()
            try:
                helper = importlib.reload(helper)
            except BaseException as exc:
                reload_errors.append(exc)

        request_thread = threading.Thread(target=request_worker)
        reload_thread = threading.Thread(target=reload_worker)
        request_thread.start()
        assert connector_entered.wait(5)
        reload_thread.start()
        assert reload_started.wait(5)
        reload_thread.join(0.2)
        assert reload_thread.is_alive()

        allow_connector_return.set()
        assert post_connector_paused.wait(5)
        deadline = time.time() + 5
        gate_state_while_waiting = None
        while time.time() < deadline:
            gate_state_while_waiting = (
                helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]()
            )
            if gate_state_while_waiting[0]:
                break
            time.sleep(0.01)

        assert gate_state_while_waiting is not None
        assert gate_state_while_waiting[0] is True
        assert len(gate_state_while_waiting[1]) == 1
        allow_delivery_check.set()
        request_thread.join(10)
        reload_thread.join(10)

        assert not request_thread.is_alive()
        assert not reload_thread.is_alive()
        assert len(request_errors) == 1
        assert isinstance(request_errors[0], RuntimeError)
        assert reload_errors == []
        assert fake_socket.closed is True
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (True, ())
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
        print("POST_CONNECT_RUNTIME_CHECK_NO_DEADLOCK_OK")
        """
    )
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "POST_CONNECT_RUNTIME_CHECK_NO_DEADLOCK_OK" in result.stdout


def test_reload_ownership_probe_interrupt_does_not_enter_reverse_lock_cycle():
    script = textwrap.dedent(
        """
        import importlib
        import inspect
        import sys
        import threading

        from helpers import bullet_trade_jq_remote_helper as helper


        runtime_lock = helper._STRATEGY_RUNTIME_LOCK
        socket_lock = helper._STRATEGY_RUNTIME_SOCKET_LOCK
        gate_authority = helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY
        bootstrap = helper._runtime_reload_bootstrap_impl
        bootstrap_code = bootstrap.__code__
        bootstrap_lines, bootstrap_first_line = inspect.getsourcelines(bootstrap)
        ownership_probe_line = bootstrap_first_line + next(
            index
            for index, line in enumerate(bootstrap_lines)
            if line.strip().startswith(
                "candidate_owned = object.__getattribute__("
            )
        )

        socket_held = threading.Event()
        runtime_held = threading.Event()
        runtime_waiting_for_socket = threading.Event()
        allow_reload = threading.Event()
        ownership_probe_interrupted = threading.Event()
        runtime_errors = []
        reload_errors = []

        def reverse_lock_worker():
            try:
                with runtime_lock:
                    runtime_held.set()
                    runtime_waiting_for_socket.set()
                    with socket_lock:
                        pass
            except BaseException as exc:
                runtime_errors.append(exc)

        def trace(frame, event, arg):
            if (
                frame.f_code is bootstrap_code
                and event == "line"
                and frame.f_lineno == ownership_probe_line
                and not ownership_probe_interrupted.is_set()
            ):
                ownership_probe_interrupted.set()
                sys.settrace(None)
                raise KeyboardInterrupt("interrupt reload ownership probe")
            return trace

        def reload_worker():
            global helper
            with socket_lock:
                socket_held.set()
                if not runtime_held.wait(5):
                    raise AssertionError("runtime lock was not acquired")
                if not runtime_waiting_for_socket.wait(5):
                    raise AssertionError("runtime worker did not attempt socket lock")
                if not allow_reload.wait(5):
                    raise AssertionError("reload was not released")
                sys.settrace(trace)
                try:
                    helper = importlib.reload(helper)
                except BaseException as exc:
                    reload_errors.append(exc)
                finally:
                    sys.settrace(None)

        reload_thread = threading.Thread(target=reload_worker, daemon=True)
        reload_thread.start()
        assert socket_held.wait(5)
        runtime_thread = threading.Thread(target=reverse_lock_worker, daemon=True)
        runtime_thread.start()
        assert runtime_held.wait(5)
        assert runtime_waiting_for_socket.wait(5)
        allow_reload.set()

        reload_thread.join(5)
        runtime_thread.join(5)
        assert ownership_probe_interrupted.is_set()
        assert not reload_thread.is_alive(), "reload waited on runtime while owning socket"
        assert not runtime_thread.is_alive(), "runtime waited on socket held by reload"
        assert runtime_errors == []
        assert len(reload_errors) == 1
        assert isinstance(reload_errors[0], KeyboardInterrupt)
        assert gate_authority[1]() == (True, ())
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        print("OWNERSHIP_PROBE_INTERRUPT_NO_REVERSE_LOCK_CYCLE_OK")
        """
    )
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "OWNERSHIP_PROBE_INTERRUPT_NO_REVERSE_LOCK_CYCLE_OK"
        in result.stdout
    )
