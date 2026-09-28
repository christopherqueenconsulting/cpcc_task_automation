#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Unit tests for runtime student-PII redaction (synthetic identities only)."""

import logging

import pytest

from cqc_cpcc.utilities import pii_redaction as pii

FAKE_NAME = "Ada Example"
FAKE_ID = "0712345"
FAKE_EMAIL = "ada.example@students.example.edu"


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    monkeypatch.setenv(pii.ALIAS_KEY_ENV, "unit-test-key")
    pii._reset_alias_key_for_tests()
    pii.clear_registry()
    yield
    pii.clear_registry()
    pii._reset_alias_key_for_tests()


@pytest.mark.unit
class TestAlias:
    def test_alias_is_stable_and_prefixed(self):
        first = pii.alias(FAKE_NAME)
        assert first.startswith(pii.ALIAS_PREFIX)
        assert first == pii.alias(FAKE_NAME)
        assert first == pii.alias("  ada   EXAMPLE ")

    def test_alias_differs_per_student(self):
        assert pii.alias(FAKE_NAME) != pii.alias("Ben Sample")

    def test_alias_depends_on_the_machine_key(self, monkeypatch):
        before = pii.alias(FAKE_NAME)
        monkeypatch.setenv(pii.ALIAS_KEY_ENV, "another-key")
        pii._reset_alias_key_for_tests()
        assert pii.alias(FAKE_NAME) != before

    def test_alias_does_not_contain_the_name(self):
        assert "ada" not in pii.alias(FAKE_NAME).lower()

    def test_empty_values(self):
        assert pii.alias(None) == "student_unknown"
        assert pii.alias("  ") == "student_unknown"

    def test_already_an_alias_is_unchanged(self):
        value = pii.alias(FAKE_NAME)
        assert pii.alias(value) == value

    def test_registered_spellings_share_one_alias(self):
        registered = pii.register_student("Example, Ada", student_id=FAKE_ID)
        assert pii.alias("Ada Example") == registered
        assert pii.alias(FAKE_ID) == registered

    def test_key_file_is_created_private(self, monkeypatch, tmp_path):
        monkeypatch.delenv(pii.ALIAS_KEY_ENV)
        key_file = tmp_path / "sub" / "alias.key"
        monkeypatch.setenv(pii.ALIAS_KEY_FILE_ENV, str(key_file))
        pii._reset_alias_key_for_tests()
        first = pii.alias(FAKE_NAME)
        assert key_file.exists()
        assert (key_file.stat().st_mode & 0o777) == 0o600
        pii._reset_alias_key_for_tests()
        assert pii.alias(FAKE_NAME) == first  # reused, not regenerated


@pytest.mark.unit
class TestScrubStructural:
    def test_submission_folder_name(self):
        text = "Extracted 10001-500001 - Ada Example - Sep 1, 2026 1022 AM/Main.java"
        out = pii.scrub(text)
        assert FAKE_NAME not in out
        assert "10001" not in out
        assert "Main.java" in out

    def test_folder_name_registers_the_student(self):
        pii.scrub("10001-500001 - Ada Example")
        assert FAKE_NAME not in pii.scrub("Grading Ada Example now")

    def test_email(self):
        assert FAKE_EMAIL not in pii.scrub("mail " + FAKE_EMAIL)

    @pytest.mark.parametrize("param", ["ou=200001", "qi=3000001", "db=600001", "userId: 10002",
                                       "orgUnitId=200001"])
    def test_brightspace_ids(self, param):
        out = pii.scrub("opening " + param)
        assert not any(ch.isdigit() for ch in out.split()[-1])

    def test_d2l_onclick_tokens(self):
        out = pii.scrub("clicking mark,555555,10001 and feedback,10001")
        assert "555555" not in out and "10001" not in out

    def test_url_query_and_numeric_path(self):
        url = "https://brightspace.example.edu/d2l/le/activities/iterator/123456?ou=200001&qi=3000001"
        out = pii.scrub("Navigated to " + url)
        assert "123456" not in out and "200001" not in out
        assert "brightspace.example.edu/d2l/le/activities/iterator/" in out

    def test_viewfile_path_drops_the_filename(self):
        url = "https://bs.example.edu/d2l/common/viewFile.d2lfile/Database/12/AdaExample_P1.java?ou=1"
        assert "AdaExample" not in pii.scrub(url)

    def test_long_digit_runs(self):
        assert FAKE_ID not in pii.scrub("student id " + FAKE_ID)

    def test_ordinary_log_text_survives(self):
        text = "Graded 12 files in 3.5s, score 42/200, tokens=1234, correlation_id=a1b2c3d4"
        assert pii.scrub(text) == text

    def test_non_string_input(self):
        assert pii.scrub(None) == ""
        assert pii.scrub(12) == "12"


@pytest.mark.unit
class TestScrubRegistered:
    def test_every_spelling_of_a_registered_name(self):
        pii.register_student("Watson, Mary Jane", student_id=FAKE_ID, email=FAKE_EMAIL)
        text = ("Watson, Mary Jane | Watson,Mary Jane | Mary Jane Watson | Mary Watson | "
                "MARY JANE WATSON | " + FAKE_ID + " | " + FAKE_EMAIL)
        out = pii.scrub(text)
        for fragment in ("Mary", "Watson", "MARY", FAKE_ID, FAKE_EMAIL):
            assert fragment not in out
        assert set(out.replace(" ", "").split("|")) == {pii.alias("Watson, Mary Jane")}

    def test_single_word_names_are_not_registered(self):
        pii.register_student("Will")
        assert pii.scrub("Will this work?") == "Will this work?"

    def test_register_students_skips_empty(self):
        pii.register_students([FAKE_NAME, None, ""])
        assert FAKE_NAME not in pii.scrub("hello " + FAKE_NAME)

    def test_clear_registry(self):
        pii.register_student(FAKE_NAME)
        pii.clear_registry()
        assert FAKE_NAME in pii.scrub("hello " + FAKE_NAME)


@pytest.mark.unit
class TestScrubObj:
    def test_nested_structures_and_secret_keys(self):
        pii.register_student(FAKE_NAME)
        data = {"api_key": "sk-x", "rows": [{"name": FAKE_NAME}, (FAKE_EMAIL,)], "n": 3}
        out = pii.scrub_obj(data)
        assert out["api_key"] == "***REDACTED***"
        assert FAKE_NAME not in str(out) and FAKE_EMAIL not in str(out)
        assert out["n"] == 3


@pytest.mark.unit
class TestLoggingFilter:
    def _logger_with_capture(self):
        records = []

        class ListHandler(logging.Handler):
            def emit(self, record):
                records.append(self.format(record))

        handler = ListHandler()
        pii.install_log_redaction(handler)
        test_logger = logging.getLogger("cqc_test_pii_redaction")
        test_logger.handlers = [handler]
        test_logger.propagate = False
        test_logger.setLevel(logging.DEBUG)
        return test_logger, records

    def test_message_and_args_are_scrubbed(self):
        pii.register_student(FAKE_NAME)
        test_logger, records = self._logger_with_capture()
        test_logger.info("Marked Present: %s (%s)", FAKE_NAME, FAKE_EMAIL)
        assert FAKE_NAME not in records[0] and FAKE_EMAIL not in records[0]
        assert pii.alias(FAKE_NAME) in records[0]

    def test_traceback_is_scrubbed(self):
        pii.register_student(FAKE_NAME)
        test_logger, records = self._logger_with_capture()
        try:
            raise ValueError("bad submission for " + FAKE_NAME)
        except ValueError:
            test_logger.exception("grading failed")
        assert "ValueError" in records[0]
        assert FAKE_NAME not in records[0]

    def test_install_is_idempotent(self):
        handler = logging.NullHandler()
        pii.install_log_redaction(handler)
        pii.install_log_redaction(handler)
        assert len(handler.filters) == 1

    def test_project_logger_handlers_are_filtered(self):
        from cqc_cpcc.utilities import logger as logger_module

        assert any(isinstance(f, pii.PiiRedactionFilter)
                   for f in logger_module.file_handler.filters)
        assert any(isinstance(f, pii.PiiRedactionFilter)
                   for f in logger_module.openai_debug_handler.filters)


@pytest.mark.unit
class TestSourcesRegisterStudents:
    def test_withdrawal_record_registers_name_id_and_email(self):
        from cqc_cpcc.withdrawals import WithdrawalRecord

        WithdrawalRecord(last_name="Example", first_name="Ada", student_id=FAKE_ID,
                         student_email=FAKE_EMAIL)
        out = pii.scrub(f"Example, Ada / {FAKE_ID} / {FAKE_EMAIL}")
        assert "Ada" not in out and FAKE_ID not in out and FAKE_EMAIL not in out

    def test_submission_folder_parser_registers_name(self):
        from cqc_cpcc.utilities.zip_grading_utils import parse_student_folder_name

        assert parse_student_folder_name("Assignment1 - Ada Example") == FAKE_NAME
        assert FAKE_NAME not in pii.scrub("Grading Ada Example")


@pytest.mark.unit
class TestPromptFilenames:
    def test_registered_name_is_removed_from_filename_variants(self):
        pii.register_student(FAKE_NAME)
        for filename in ("AdaExample_Project1.java", "Ada_Example.java", "ada-example.py"):
            assert "ada" not in pii.scrub(filename).lower(), filename

    def test_prompt_file_name_line_is_scrubbed(self, tmp_path, monkeypatch):
        from cqc_cpcc.utilities import zip_grading_utils

        pii.register_student(FAKE_NAME)
        source = tmp_path / "Ada_Example_Main.java"
        source.write_text("class Main {}")
        monkeypatch.setenv("READABLE_FILE_ROOTS", str(tmp_path))
        text = zip_grading_utils.build_submission_text_with_token_limit(
            {"Ada_Example_Main.java": str(source)}
        )
        assert "Ada" not in text
        assert "class Main {}" in text
