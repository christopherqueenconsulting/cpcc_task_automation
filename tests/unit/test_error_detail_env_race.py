#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""``_secret_env_values`` survives an environment variable removed while it iterates.

Real crash (A/B run 40159103912): ``KeyError: 'CQC_MODEL_REGISTRY_PATH'`` from ``os.environ.items()``, which looks
each key up again after the names were listed; the variable can vanish while the environment is iterated.
"""

import pytest

from cqc_cpcc.model_eval import error_detail

pytestmark = pytest.mark.unit

# 12-31 chars and no shape the _SECRETS patterns match, so only the env-value replacement can redact it.
SECRET = "not-a-real-credential"


class VanishingEnviron:
    """Lists the ``vanished`` names, but ``[]`` raises KeyError and ``.get`` returns None for them."""

    def __init__(self, present, vanished=("CQC_MODEL_REGISTRY_PATH",)):
        self._present = dict(present)
        self._vanished = tuple(vanished)

    def __iter__(self):
        yield from self._vanished
        yield from self._present

    def __getitem__(self, key):
        if key in self._vanished:
            raise KeyError(key)
        return self._present[key]

    def get(self, key, default=None):
        return self._present.get(key, default)

    def items(self):  # the old code path: re-reads every key
        return ((k, self[k]) for k in self)


def test_removed_mid_iteration_does_not_raise(monkeypatch):
    with monkeypatch.context() as m:  # undone before pytest touches os.environ again at teardown
        m.setattr(error_detail.os, "environ", VanishingEnviron({"MY_API_KEY": SECRET}))
        assert list(error_detail._secret_env_values()) == [SECRET]
        scrubbed = error_detail.scrub_log(f"boom {SECRET} end")
        out = error_detail.redact_message(f"failed with {SECRET} here")
    assert SECRET not in scrubbed and "[redacted]" in scrubbed
    assert SECRET not in out and "[redacted]" in out


def test_secret_named_variable_vanishing_yields_nothing(monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(error_detail.os, "environ", VanishingEnviron({"OTHER_API_KEY": SECRET}, vanished=("MY_API_KEY",)))
        assert list(error_detail._secret_env_values()) == [SECRET]  # nothing yielded for MY_API_KEY, no raise
        m.setattr(error_detail.os, "environ", VanishingEnviron({}, vanished=("MY_API_KEY",)))
        assert list(error_detail._secret_env_values()) == []


def test_key_named_variable_is_still_redacted(monkeypatch):
    monkeypatch.setenv("SOME_SERVICE_KEY", SECRET)
    assert SECRET not in error_detail.redact_message(f"x {SECRET} y")
    assert SECRET not in error_detail.scrub_log(f"x {SECRET} y")


def test_short_or_unrelated_values_are_not_secrets(monkeypatch):
    monkeypatch.setenv("SOME_SERVICE_KEY", SECRET)
    monkeypatch.setenv("SHORT_API_KEY", "short")
    monkeypatch.setenv("PLAIN_SETTING", "a-long-plain-value-123")
    values = list(error_detail._secret_env_values())
    assert SECRET in values
    assert "short" not in values and "a-long-plain-value-123" not in values


def test_registry_path_unset_works(monkeypatch):
    monkeypatch.delenv("CQC_MODEL_REGISTRY_PATH", raising=False)
    assert error_detail.scrub_log("plain text") == "plain text"
    assert error_detail.redact_message("plain text") == "plain text"
