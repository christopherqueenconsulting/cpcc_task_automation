#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for the headless-web MFA bridge (number matching)."""

from unittest.mock import MagicMock

import pytest

from cqc_cpcc.utilities.selenium_util import (
    MfaBridge,
    MfaChallenge,
    capture_mfa_challenge,
    extract_mfa_number,
)


class _FakeEl:
    def __init__(self, text):
        self.text = text


def _driver_with(elements_by_selector):
    """Build a fake WebDriver whose find_elements returns canned elements."""
    driver = MagicMock()

    def find_elements(by, sel):
        return elements_by_selector.get(sel, [])

    driver.find_elements.side_effect = find_elements
    driver.get_screenshot_as_png.return_value = b"PNGDATA"
    return driver


@pytest.mark.unit
def test_extract_mfa_number_microsoft_display_sign():
    driver = _driver_with({"idRichContext_DisplaySign": [_FakeEl("47")]})
    assert extract_mfa_number(driver) == "47"


@pytest.mark.unit
def test_extract_mfa_number_handles_surrounding_text():
    driver = _driver_with({".verification-code": [_FakeEl("Code: 88 ")]})
    assert extract_mfa_number(driver) == "88"


@pytest.mark.unit
def test_extract_mfa_number_none_when_absent():
    assert extract_mfa_number(_driver_with({})) is None


@pytest.mark.unit
def test_capture_mfa_challenge_includes_screenshot():
    driver = _driver_with({"idRichContext_DisplaySign": [_FakeEl("12")]})
    challenge = capture_mfa_challenge(driver, "microsoft")
    assert challenge.context == "microsoft"
    assert challenge.number == "12"
    assert challenge.screenshot_png == b"PNGDATA"


@pytest.mark.unit
def test_wait_for_mfa_approval_returns_when_prompt_clears():
    from cqc_cpcc.utilities.utils import _wait_for_mfa_approval

    driver = MagicMock()
    # Element present once, then gone -> approval detected.
    driver.find_elements.side_effect = [["el"], []]
    _wait_for_mfa_approval(driver, "//x", mfa_handler=None, timeout=5)
    assert driver.find_elements.call_count >= 1


@pytest.mark.unit
def test_wait_for_mfa_approval_raises_on_cancel():
    from cqc_cpcc.utilities.utils import MfaCancelled, _wait_for_mfa_approval

    driver = MagicMock()
    driver.find_elements.return_value = ["el"]  # never clears
    bridge = MfaBridge()
    bridge.cancel()
    with pytest.raises(MfaCancelled):
        _wait_for_mfa_approval(driver, "//x", mfa_handler=bridge, timeout=5)


@pytest.mark.unit
def test_wait_for_mfa_approval_republishes_number_each_poll(mocker):
    """While the prompt is present, the number/screenshot is re-published so a
    first-capture miss (number still animating) gets corrected on a later poll."""
    from cqc_cpcc.utilities.utils import _wait_for_mfa_approval

    driver = MagicMock()
    # Prompt present for 2 polls, then clears.
    driver.find_elements.side_effect = [["el"], ["el"], []]
    bridge = MfaBridge()

    # First capture reads no number (animating), second reads "73".
    # _publish_mfa_challenge imports capture_mfa_challenge from selenium_util at
    # call time, so patch it there.
    captures = [
        MfaChallenge(context="microsoft", number=None, screenshot_png=b"A"),
        MfaChallenge(context="microsoft", number="73", screenshot_png=b"B"),
    ]
    mocker.patch(
        "cqc_cpcc.utilities.selenium_util.capture_mfa_challenge",
        side_effect=captures,
    )

    _wait_for_mfa_approval(
        driver, "//x", mfa_handler=bridge, timeout=5,
        context="microsoft", message="enter it",
    )

    # The latest published challenge carries the corrected number.
    assert bridge.challenge is not None
    assert bridge.challenge.number == "73"


@pytest.mark.unit
def test_mfa_bridge_relays_challenge_and_lifecycle():
    bridge = MfaBridge()
    assert bridge.challenge is None
    assert not bridge.resolved and not bridge.cancelled

    bridge.on_challenge(MfaChallenge(context="duo", number="55", message="approve"))
    assert bridge.challenge.number == "55"

    bridge.cancel()
    bridge.on_resolved()
    assert bridge.cancelled and bridge.resolved


@pytest.mark.unit
def test_pending_challenge_clears_on_approval_and_returns_on_the_next_login():
    bridge = MfaBridge()
    bridge.on_challenge(MfaChallenge(context="microsoft", number="12"))
    assert bridge.pending_challenge.number == "12"

    bridge.on_resolved()
    assert bridge.pending_challenge is None

    # A later sign-in in the same run (e.g. BrightSpace after MyColleges).
    bridge.on_challenge(MfaChallenge(context="microsoft", number="34"))
    assert not bridge.resolved
    assert bridge.pending_challenge.number == "34"


@pytest.mark.unit
def test_login_if_needed_uses_the_thread_mfa_handler(mocker):
    from cqc_cpcc.utilities import utils

    mocker.patch.object(utils.time, "sleep")
    login = mocker.patch.object(utils, "microsoft_login")
    driver = MagicMock(title="Sign in to your account")
    handler = MfaBridge()

    with utils.mfa_handler_scope(handler):
        utils.login_if_needed(driver)
    login.assert_called_once_with(driver, mfa_handler=handler)

    # An explicit handler still wins, and the scope does not leak.
    other = MfaBridge()
    with utils.mfa_handler_scope(handler):
        utils.login_if_needed(driver, mfa_handler=other)
    assert login.call_args.kwargs["mfa_handler"] is other
    assert utils.current_mfa_handler() is None
