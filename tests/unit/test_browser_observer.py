#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Wait retries and progress reach the web app's observer, on its own thread only."""

import threading
from collections import namedtuple
from unittest.mock import MagicMock, patch

import pytest
from selenium.common import TimeoutException

from cqc_cpcc.utilities import selenium_util as su


class Observer:
    def __init__(self):
        self.waits = []
        self.progress = []

    def on_wait_retry(self, driver, wait_text):
        self.waits.append(wait_text)

    def on_progress(self, message):
        self.progress.append(message)


@pytest.mark.unit
class TestBrowserObserverScope:
    def test_scope_is_per_thread_and_restored(self):
        observer = Observer()
        seen = {}
        with su.browser_observer_scope(observer):
            assert su.current_browser_observer() is observer
            other = threading.Thread(
                target=lambda: seen.setdefault("other", su.current_browser_observer()))
            other.start()
            other.join()
        assert seen["other"] is None
        assert su.current_browser_observer() is None

    def test_notices_reach_the_observer(self):
        observer = Observer()
        with su.browser_observer_scope(observer):
            su.notify_wait_retry(MagicMock(), "Waiting for Deadline Dates")
            su.notify_progress("Course 1 of 2")
        assert observer.waits == ["Waiting for Deadline Dates"]
        assert observer.progress == ["Course 1 of 2"]

    def test_warnings_reach_the_observer(self):
        seen = []
        observer = type("WarnObserver", (), {"on_warning": lambda self, m: seen.append(m)})()
        with su.browser_observer_scope(observer):
            su.notify_warning("Attendance is not working for CSC-134-N801")
        assert seen == ["Attendance is not working for CSC-134-N801"]

    def test_warnings_without_a_hook_or_with_a_failing_one_only_log(self):
        su.notify_warning("no observer")
        with su.browser_observer_scope(object()):
            su.notify_warning("no hook")
        observer = MagicMock()
        observer.on_warning.side_effect = RuntimeError("boom")
        with su.browser_observer_scope(observer):
            su.notify_warning("failing hook")

    def test_notices_without_an_observer_only_log(self):
        su.notify_wait_retry(MagicMock(), "")
        su.notify_progress("Course 1 of 1")

    def test_observer_without_hooks_is_fine(self):
        with su.browser_observer_scope(object()):
            su.notify_wait_retry(MagicMock(), "Waiting")
            su.notify_progress("Course 1 of 1")

    def test_a_failing_observer_never_breaks_the_run(self):
        observer = MagicMock()
        observer.on_wait_retry.side_effect = RuntimeError("boom")
        observer.on_progress.side_effect = RuntimeError("boom")
        with su.browser_observer_scope(observer):
            su.notify_wait_retry(MagicMock(), "Waiting")
            su.notify_progress("Course 1 of 1")


@pytest.mark.unit
class TestDescribePage:
    def test_title_and_path_without_the_query(self):
        driver = MagicMock(title="Section Details",
                           current_url="https://mycollegess.cpcc.edu/Student/Faculty/Section?id=1")
        assert su.describe_page(driver) == "'Section Details' at /Student/Faculty/Section"

    def test_unreadable_page(self):
        driver = MagicMock()
        type(driver).title = property(lambda self: (_ for _ in ()).throw(RuntimeError("crashed")))
        assert su.describe_page(driver) == "(page unavailable)"

    def test_blank_title_and_unparseable_url(self):
        driver = MagicMock(title="", current_url=12)
        assert su.describe_page(driver) == "'(no title)' at ?"


@pytest.mark.unit
class TestRetriesNotifyTheObserver:
    def test_get_element_retry_reports_each_failed_wait(self, monkeypatch):
        monkeypatch.setattr(su.time, "sleep", lambda seconds: None)
        wait = MagicMock()
        wait.until.side_effect = TimeoutException("never")
        observer = Observer()
        with su.browser_observer_scope(observer), pytest.raises(TimeoutException):
            su.get_element_wait_retry(MagicMock(), wait, "x", "Waiting for X", max_try=2)
        assert observer.waits == ["Waiting for X", "Waiting for X"]

    def test_click_retry_reports_the_failed_wait(self, monkeypatch):
        monkeypatch.setattr(su.time, "sleep", lambda seconds: None)
        observer = Observer()
        with su.browser_observer_scope(observer), \
                patch.object(su, "get_element_wait_retry",
                             side_effect=TimeoutException("never")), \
                pytest.raises(TimeoutException):
            su.click_element_wait_retry(MagicMock(), MagicMock(), "x", "Waiting for Y", max_try=0)
        assert observer.waits == ["Waiting for Y"]


Stat = namedtuple("Stat", "f_frsize f_blocks")


@pytest.mark.unit
class TestDevShmIsSmall:
    def test_container_sized_shm_is_small(self, monkeypatch):
        monkeypatch.setattr(su.os, "statvfs", lambda path: Stat(4096, 16384), raising=False)
        assert su.dev_shm_is_small() is True

    def test_large_shm_is_fine(self, monkeypatch):
        monkeypatch.setattr(su.os, "statvfs", lambda path: Stat(4096, 1024 * 1024), raising=False)
        assert su.dev_shm_is_small() is False

    def test_missing_shm_is_fine(self, monkeypatch):
        def missing(path):
            raise FileNotFoundError(path)

        monkeypatch.setattr(su.os, "statvfs", missing, raising=False)
        assert su.dev_shm_is_small() is False
