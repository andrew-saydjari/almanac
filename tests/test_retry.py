"""Tests for retry-with-backoff on database queries (workstream L2)."""

import pytest
from peewee import InterfaceError, OperationalError

import almanac.retry as retry_module
from almanac.retry import retry_on_database_error


@pytest.fixture
def no_sleep_or_reconnect(monkeypatch):
    calls = {"sleep": [], "reconnect": 0}
    monkeypatch.setattr(retry_module, "sleep", lambda s: calls["sleep"].append(s))
    monkeypatch.setattr(
        retry_module,
        "reconnect",
        lambda: calls.__setitem__("reconnect", calls["reconnect"] + 1) or True,
    )
    return calls


def flaky(n_failures, exception_class=OperationalError, result=42):
    state = {"calls": 0}

    def fn():
        state["calls"] += 1
        if state["calls"] <= n_failures:
            raise exception_class("connection dropped")
        return result

    fn.state = state
    return fn


class TestRetryOnDatabaseError:
    def test_succeeds_after_transient_failures(self, no_sleep_or_reconnect):
        fn = retry_on_database_error(flaky(2), attempts=4, backoff=1.0)
        assert fn() == 42
        assert no_sleep_or_reconnect["reconnect"] == 2
        # Exponential backoff: 1, 2 seconds.
        assert no_sleep_or_reconnect["sleep"] == [1.0, 2.0]

    def test_interface_error_is_retryable(self, no_sleep_or_reconnect):
        fn = retry_on_database_error(
            flaky(1, exception_class=InterfaceError), attempts=3, backoff=0.0
        )
        assert fn() == 42
        assert no_sleep_or_reconnect["reconnect"] == 1

    def test_exhausted_attempts_reraises(self, no_sleep_or_reconnect):
        fn = retry_on_database_error(flaky(10), attempts=3, backoff=0.0)
        with pytest.raises(OperationalError):
            fn()
        assert no_sleep_or_reconnect["reconnect"] == 2  # attempts - 1

    def test_non_retryable_exception_passes_through(self, no_sleep_or_reconnect):
        fn = retry_on_database_error(
            flaky(1, exception_class=ValueError), attempts=3, backoff=0.0
        )
        with pytest.raises(ValueError):
            fn()
        assert no_sleep_or_reconnect["reconnect"] == 0

    def test_backoff_capped_at_max_delay(self, no_sleep_or_reconnect):
        fn = retry_on_database_error(
            flaky(3), attempts=4, backoff=100.0, max_delay=150.0
        )
        assert fn() == 42
        assert no_sleep_or_reconnect["sleep"] == [100.0, 150.0, 150.0]

    def test_bare_decorator_usage(self, no_sleep_or_reconnect):
        raw = flaky(1)

        @retry_on_database_error
        def wrapped():
            return raw()

        assert wrapped() == 42
