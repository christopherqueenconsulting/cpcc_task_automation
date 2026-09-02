#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for the online Attendance Tracker sync.

The two invariants that matter: a dry run never writes, and a failed read never
leads to an append.
"""

from unittest.mock import MagicMock, patch

import pytest

from cqc_cpcc.attendance_tracker import (
    SharePointExcelAdapter,
    TrackerSyncError,
    build_adapter,
    records_needing_sync,
    sync_records_to_tracker,
)
from cqc_cpcc.withdrawals import WithdrawalRecord, record_key

HEADER = [
    "Instructor", "Last Name", "First Name", "Student ID", "Student Email",
    "Course and Section", "Session Type", "Delivery Type", "Status",
    "Week of Last Activity", "Faculty Reason",
]


def make_record(student_id="123456", course="CSC-151-N855"):
    return WithdrawalRecord(
        instructor="Prof Queen", last_name="Doe", first_name="John",
        student_id=student_id, student_email="j@example.edu",
        course_and_section=course, session_type="Full Session",
        delivery_type="Online", status="W", week_of_last_activity="N/A",
        faculty_reason="reason",
    )


def make_driver():
    driver = MagicMock()
    driver.current_window_handle = "original"
    driver.window_handles = ["original", "tracker"]
    return driver


class FakeAdapter:
    """Adapter double that records whether an append was attempted."""

    def __init__(self, existing_keys=None, read_error=None):
        self.existing_keys = existing_keys or set()
        self.read_error = read_error
        self.append_calls = []

    def read_existing_keys(self, driver, wait):
        if self.read_error:
            raise self.read_error
        return self.existing_keys

    def append_records(self, driver, wait, records):
        self.append_calls.append(list(records))
        return len(records)


@pytest.mark.unit
class TestBuildAdapter:
    @pytest.mark.parametrize(
        "url",
        [
            "https://example.sharepoint.com/:x:/r/sites/x/Tracker.xlsx",
            "https://onedrive.live.com/edit.aspx?resid=1",
            "https://example-my.officeapps.live.com/x/_layouts/xlviewer.aspx",
        ],
    )
    def test_microsoft_hosts_use_the_sharepoint_adapter(self, url):
        assert isinstance(build_adapter(url), SharePointExcelAdapter)

    @pytest.mark.parametrize(
        "url", ["https://docs.google.com/spreadsheets/d/1", "", "not a url"]
    )
    def test_other_hosts_are_rejected_with_a_clear_error(self, url):
        with pytest.raises(
                TrackerSyncError, match="Unsupported Attendance Tracker host"
        ):
            build_adapter(url)


@pytest.mark.unit
class TestDryRun:
    def test_dry_run_never_appends(self):
        adapter = FakeAdapter(existing_keys=set())

        result = sync_records_to_tracker(
            make_driver(), MagicMock(), "https://x.sharepoint.com/t.xlsx",
            [make_record()], dry_run=True, adapter=adapter,
        )

        assert adapter.append_calls == []
        assert result.appended == 0
        assert len(result.to_append) == 1
        assert "dry run" in result.describe()

    def test_live_run_appends_only_the_missing_rows(self):
        present = make_record(student_id="111")
        adapter = FakeAdapter(existing_keys={record_key(present)})

        result = sync_records_to_tracker(
            make_driver(), MagicMock(), "https://x.sharepoint.com/t.xlsx",
            [present, make_record(student_id="222")], dry_run=False, adapter=adapter,
        )

        assert result.skipped_existing == 1
        assert result.appended == 1
        assert [r.student_id for r in adapter.append_calls[0]] == ["222"]

    def test_same_student_in_another_section_is_still_appended(self):
        present = make_record(student_id="111", course="CSC-151-N855")
        other_section = make_record(student_id="111", course="CSC-134-N801")
        adapter = FakeAdapter(existing_keys={record_key(present)})

        result = sync_records_to_tracker(
            make_driver(), MagicMock(), "https://x.sharepoint.com/t.xlsx",
            [present, other_section], dry_run=False, adapter=adapter,
        )

        assert result.appended == 1
        assert adapter.append_calls[0][0].course_and_section == "CSC-134-N801"


@pytest.mark.unit
class TestReadFailureAborts:
    """Appending after a failed read would duplicate the tracker."""

    def test_read_error_propagates_and_nothing_is_appended(self):
        adapter = FakeAdapter(read_error=TrackerSyncError("grid not found"))

        with pytest.raises(TrackerSyncError, match="grid not found"):
            sync_records_to_tracker(
                make_driver(), MagicMock(), "https://x.sharepoint.com/t.xlsx",
                [make_record()], dry_run=False, adapter=adapter,
            )

        assert adapter.append_calls == []

    def test_an_empty_but_valid_sheet_reads_as_zero_keys(self):
        """A fresh tracker legitimately has no rows; that is an answer, not a failure.

        The old DOM-scraping read could not tell "empty" from "scrape broke", so it
        refused on zero rows. An exact workbook read can, so it no longer does.
        """
        adapter = SharePointExcelAdapter()
        adapter.bind("https://x.sharepoint.com/personal/u/_layouts/15/Doc.aspx"
                     "?sourcedoc=%7B00000000-1111-2222-3333-444444444444%7D")
        adapter.fetch_workbook = lambda driver, url: TestSharePointRead.workbook_bytes(
            [TestSharePointRead.HEADER]
        )

        assert adapter.read_existing_keys(MagicMock(), MagicMock()) == set()

    def test_no_tracker_url_is_an_error(self):
        with pytest.raises(TrackerSyncError, match="No Attendance Tracker URL"):
            sync_records_to_tracker(make_driver(), MagicMock(), "", [make_record()])


@pytest.mark.unit
class TestSharePointRead:
    """Reads go through a workbook download, not the canvas grid.

    Verified live 2026-09-01: Excel Online renders the grid to <canvas> and exposes
    zero [role=row]/[role=gridcell] nodes, so DOM scraping is impossible rather
    than merely fragile.
    """

    HEADER = [
        "Instructor", "Student Lastname", "Student Firstname", "Student ID",
        "Student Email", "Course and Section", "Session Type", "Delivery Type",
        "Status (N/A, S, W)", "Week of Last Activity",
        "Faculty's Best Reason assessed for Stopped Attending/Withdrawal",
        "Navigator Notes and Outcomes",
    ]

    @staticmethod
    def workbook_bytes(rows):
        """Build a real .xlsx so the parser is exercised, not a stub."""
        import io

        import openpyxl

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "Instructor Inputs"
        for row in rows:
            sheet.append(row)
        buffer = io.BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()

    def adapter_with(self, rows):
        adapter = SharePointExcelAdapter()
        adapter.bind("https://x.sharepoint.com/personal/u/_layouts/15/Doc.aspx"
                     "?sourcedoc=%7B00000000-1111-2222-3333-444444444444%7D")
        adapter.fetch_workbook = lambda driver, url: self.workbook_bytes(rows)
        return adapter

    def test_download_url_is_derived_from_the_share_link(self):
        url = SharePointExcelAdapter.download_url(
            "https://example-my.sharepoint.com/:x:/r/personal/instructor_example_edu/_layouts/15/"
            "Doc.aspx?sourcedoc=%7B00000000-1111-2222-3333-444444444444%7D&file=x.xlsx"
        )

        assert url == (
            "https://example-my.sharepoint.com/personal/instructor_example_edu/_layouts/15/"
            "download.aspx?UniqueId=%7B00000000-1111-2222-3333-444444444444%7D"
        )

    def test_download_url_is_none_for_an_unrecognised_link(self):
        assert SharePointExcelAdapter.download_url(
            "https://example.com/file.xlsx"
        ) is None

    def test_reads_the_composite_keys(self):
        adapter = self.adapter_with([
            self.HEADER,
            ["Prof", "Doe", "John", "5518598", "e", "CSC-134-N801",
             "Full", "Online", "W", "NA", "r", ""],
            ["Prof", "Roe", "Jane", "4271456", "e", "csc-134-n802",
             "Full", "Online", "S", "NA", "r", ""],
        ])

        keys = adapter.read_existing_keys(MagicMock(), MagicMock())

        # Course casing normalised; student ids untouched.
        assert keys == {("CSC-134-N801", "5518598"), ("CSC-134-N802", "4271456")}

    def test_numeric_student_ids_do_not_gain_a_float_suffix(self):
        """openpyxl returns numeric cells as floats; ids must stay identifiers."""
        adapter = self.adapter_with([
            self.HEADER,
            ["Prof", "Doe", "John", 5518598, "e", "CSC-134-N801",
             "F", "O", "W", "NA", "r", ""],
        ])

        assert adapter.read_existing_keys(MagicMock(), MagicMock()) == {
            ("CSC-134-N801", "5518598")
        }

    def test_blank_spacer_rows_are_ignored(self):
        adapter = self.adapter_with([
            self.HEADER,
            [None] * 12,
            ["Prof", "Doe", "John", "5518598", "e", "CSC-134-N801",
             "F", "O", "W", "NA", "r", ""],
        ])

        assert len(adapter.read_existing_keys(MagicMock(), MagicMock())) == 1

    def test_missing_key_columns_abort_the_read(self):
        adapter = self.adapter_with([["Some", "Other", "Header"], ["a", "b", "c"]])

        with pytest.raises(TrackerSyncError, match="Could not find"):
            adapter.read_existing_keys(MagicMock(), MagicMock())

    def test_a_sign_in_page_instead_of_a_workbook_is_rejected(self):
        adapter = SharePointExcelAdapter()
        adapter.bind("https://x.sharepoint.com/personal/u/_layouts/15/Doc.aspx"
                     "?sourcedoc=%7B00000000-1111-2222-3333-444444444444%7D")
        driver = make_driver()
        driver.execute_async_script.return_value = {
            "status": 200, "b64": "PGh0bWw+", "size": 6,   # "<html>"
        }

        with pytest.raises(TrackerSyncError, match="not an .xlsx"):
            adapter.read_existing_keys(driver, MagicMock())

    def test_an_http_error_aborts_rather_than_returning_empty(self):
        adapter = SharePointExcelAdapter()
        adapter.bind("https://x.sharepoint.com/personal/u/_layouts/15/Doc.aspx"
                     "?sourcedoc=%7B00000000-1111-2222-3333-444444444444%7D")
        driver = make_driver()
        driver.execute_async_script.return_value = {"status": 401}

        with pytest.raises(TrackerSyncError, match="no content"):
            adapter.read_existing_keys(driver, MagicMock())

    def test_an_unusable_tracker_link_aborts(self):
        adapter = SharePointExcelAdapter()
        adapter.bind("https://example.com/nope")

        with pytest.raises(TrackerSyncError, match="download URL"):
            adapter.read_existing_keys(make_driver(), MagicMock())

    def test_appending_without_reading_first_is_refused(self):
        """The first free row is only known after a read.

        Typing blind is not allowed.
        """
        with pytest.raises(TrackerSyncError, match="has not been read"):
            SharePointExcelAdapter().append_records(
                make_driver(), MagicMock(), [make_record()]
            )


@pytest.mark.unit
class TestRecordsNeedingSync:
    def test_filters_by_composite_key(self):
        records = [make_record(student_id="1"), make_record(student_id="2")]
        existing = {record_key(records[0])}

        assert [r.student_id for r in records_needing_sync(records, existing)] == ["2"]

    def test_empty_records_short_circuits_without_a_browser(self):
        driver = make_driver()

        result = sync_records_to_tracker(
            driver, MagicMock(), "https://x.sharepoint.com/t.xlsx", []
        )

        assert result.to_append == []
        driver.switch_to.new_window.assert_not_called()


@pytest.mark.unit
class TestCellSanitizing:
    """Student-derived text goes into a spreadsheet, so it must not become a formula."""

    @pytest.mark.parametrize(
        "raw", ["=SUM(1+1)", "=cmd|'/c calc'!A1", "+1+1", "-1", "@SUM(A1)"]
    )
    def test_formula_triggers_are_forced_to_text(self, raw):
        assert SharePointExcelAdapter._sanitize_cell(raw).startswith("'")

    @pytest.mark.parametrize("raw, expected", [
        ("has\ttab", "has tab"),
        ("line\nbreak", "line break"),
        ("carriage\rreturn", "carriage return"),
    ])
    def test_separators_are_neutralised(self, raw, expected):
        """A stray tab would shift every later value one column right."""
        assert SharePointExcelAdapter._sanitize_cell(raw) == expected

    def test_ordinary_text_is_untouched(self):
        reason = "Student withdrew without contacting the instructor"

        assert SharePointExcelAdapter._sanitize_cell(reason) == reason

    def test_none_becomes_empty(self):
        assert SharePointExcelAdapter._sanitize_cell(None) == ""

    def test_numbers_survive(self):
        assert SharePointExcelAdapter._sanitize_cell(4390159) == "4390159"


@pytest.mark.unit
class TestKeyboardAppend:
    """Rows are appended by address, never by wherever the cursor happened to land."""

    def _adapter(self, next_free_row=11):
        adapter = SharePointExcelAdapter()
        adapter._next_free_row = next_free_row
        adapter._goto_cell_calls = []
        adapter._confirmed_row_counts = []
        adapter._enter_grid_frame = lambda driver: None
        adapter._goto_cell = (
            lambda driver, address: adapter._goto_cell_calls.append(address)
        )
        # Post-write confirmation re-downloads the workbook; it has its own tests
        # in TestAppendConfirmation below.
        adapter._confirm_rows_landed = (
            lambda driver, expected: adapter._confirmed_row_counts.append(expected)
        )
        return adapter

    def test_each_row_targets_its_own_address(self):
        adapter = self._adapter(next_free_row=11)

        with patch("cqc_cpcc.attendance_tracker.ActionChains"), \
                patch("cqc_cpcc.attendance_tracker.time.sleep"):
            written = adapter.append_records(
                make_driver(), MagicMock(),
                [make_record(student_id="1"),
                 make_record(student_id="2"),
                 make_record(student_id="3")],
            )

        assert written == 3
        # Consecutive rows starting at the first free one; never row 10 or earlier.
        assert adapter._goto_cell_calls == ["A11", "A12", "A13"]

    def test_starts_below_existing_data(self):
        adapter = self._adapter(next_free_row=660)

        with patch("cqc_cpcc.attendance_tracker.ActionChains"), \
                patch("cqc_cpcc.attendance_tracker.time.sleep"):
            adapter.append_records(make_driver(), MagicMock(), [make_record()])

        assert adapter._goto_cell_calls == ["A660"]

    def test_only_the_instructor_columns_are_typed(self):
        """Navigator columns L and M must never be written."""
        from cqc_cpcc.withdrawals import CSV_FIELDNAMES

        adapter = self._adapter()
        typed = []

        with patch("cqc_cpcc.attendance_tracker.ActionChains") as chains, \
                patch("cqc_cpcc.attendance_tracker.time.sleep"):
            chains.return_value.send_keys.side_effect = lambda v: (
                typed.append(v) or chains.return_value)
            adapter.append_records(make_driver(), MagicMock(), [make_record()])

        row_text = next(t for t in typed if isinstance(t, str) and "\t" in t)
        assert len(row_text.split("\t")) == len(CSV_FIELDNAMES) == 11
        assert "Navigator" not in row_text

    def test_the_frame_is_released_even_when_typing_fails(self):
        adapter = self._adapter()
        driver = make_driver()

        with patch("cqc_cpcc.attendance_tracker.ActionChains",
                   side_effect=RuntimeError("boom")), \
                patch("cqc_cpcc.attendance_tracker.time.sleep"), \
                pytest.raises(RuntimeError):
            adapter.append_records(driver, MagicMock(), [make_record()])

        driver.switch_to.default_content.assert_called()


@pytest.mark.unit
class TestGridNavigation:
    """Cell targeting is the safety boundary for every write.

    Rows are addressed explicitly rather than relying on where Excel leaves the
    cursor, so if either the grid frame or the Name Box cannot be found the
    adapter must refuse rather than type into whatever happens to be focused.
    """

    @staticmethod
    def _driver(found_selectors=()):
        """A driver whose find_elements only matches the named CSS selectors.

        Elements are cached per selector so a test can inspect the same object the
        code under test used.
        """
        driver = MagicMock()
        elements = {}

        def find(by, selector):
            if selector not in found_selectors:
                return []
            return [elements.setdefault(selector, MagicMock(name=selector))]

        driver.find_elements.side_effect = find
        driver.matched = elements
        return driver

    def test_the_grid_frame_is_entered_from_the_top_document(self):
        driver = self._driver({"iframe.WACFrame"})

        SharePointExcelAdapter()._enter_grid_frame(driver)

        driver.switch_to.default_content.assert_called_once()
        driver.switch_to.frame.assert_called_once()

    def test_a_missing_grid_frame_is_an_error_not_a_silent_pass(self):
        driver = self._driver(set())

        with pytest.raises(TrackerSyncError, match="web-application frame"):
            SharePointExcelAdapter()._enter_grid_frame(driver)

        driver.switch_to.frame.assert_not_called()

    def test_the_name_box_aria_label_is_matched_as_a_substring(self):
        """The live label is wrapped in Unicode LTR marks, so exact match fails."""
        driver = self._driver({"input[aria-label*='Name Box']"})

        with patch("cqc_cpcc.attendance_tracker.time.sleep"):
            SharePointExcelAdapter()._goto_cell(driver, "A11")

        name_box = driver.matched["input[aria-label*='Name Box']"]
        name_box.click.assert_called_once()
        typed = [call.args for call in name_box.send_keys.call_args_list]
        assert ("A11",) in typed, "the cell address must be typed into the Name Box"

    def test_without_a_name_box_it_refuses_to_type_blind(self):
        """Typing without a confirmed target could overwrite an existing row."""
        driver = self._driver(set())

        with pytest.raises(TrackerSyncError, match="Refusing to type blind"):
            SharePointExcelAdapter()._goto_cell(driver, "A11")


def make_workbook_bytes(rows, sheet_name="Instructor Inputs"):
    """A real .xlsx in memory, so the openpyxl read path is genuinely exercised."""
    import io

    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


TRACKER_URL = (
    "https://example-my.sharepoint.com/:x:/r/personal/instructor_example_edu/"
    "_layouts/15/Doc.aspx?sourcedoc=%7B00000000-1111-2222-3333-444444444444%7D"
)


def bound_adapter(tracker_url=TRACKER_URL):
    adapter = SharePointExcelAdapter()
    adapter.bind(tracker_url)
    return adapter


@pytest.mark.unit
class TestFetchWorkbook:
    """The download is the only read path, so each way it can fail must be distinct.

    Every failure here has to raise rather than return empty bytes: an empty read
    looks like an empty tracker, and an empty tracker means every record is
    "missing" and gets appended.
    """

    @staticmethod
    def _driver(script_result=None, script_error=None):
        driver = make_driver()
        if script_error is not None:
            driver.execute_async_script.side_effect = script_error
        else:
            driver.execute_async_script.return_value = script_result
        return driver

    def test_a_share_link_without_a_sourcedoc_guid_is_refused(self):
        adapter = bound_adapter("https://example-my.sharepoint.com/personal/u/Doc.aspx")

        with pytest.raises(TrackerSyncError, match="sourcedoc"):
            adapter.fetch_workbook(self._driver(), adapter._tracker_url)

    def test_a_driver_level_failure_is_reported_as_a_download_failure(self):
        adapter = bound_adapter()

        with pytest.raises(TrackerSyncError, match="Workbook download failed"):
            adapter.fetch_workbook(
                self._driver(script_error=RuntimeError("script timeout")),
                TRACKER_URL,
            )

    def test_an_in_page_fetch_error_is_reported_verbatim(self):
        adapter = bound_adapter()

        with pytest.raises(TrackerSyncError, match="TypeError: failed to fetch"):
            adapter.fetch_workbook(
                self._driver({"error": "TypeError: failed to fetch"}),
                TRACKER_URL,
            )

    def test_an_http_error_with_no_body_names_the_status(self):
        """A 401 means the session lost SharePoint, which is worth saying out loud."""
        adapter = bound_adapter()

        with pytest.raises(TrackerSyncError, match="HTTP 401"):
            adapter.fetch_workbook(self._driver({"status": 401}), TRACKER_URL)

    def test_a_sign_in_page_is_rejected_rather_than_parsed(self):
        """download.aspx serves HTML when the session expired -- not a workbook."""
        import base64

        adapter = bound_adapter()
        html = base64.b64encode(b"<html>Sign in</html>").decode()

        with pytest.raises(TrackerSyncError, match="not an .xlsx"):
            adapter.fetch_workbook(
                self._driver({"status": 200, "b64": html}), TRACKER_URL
            )

    def test_a_real_workbook_comes_back_as_bytes(self):
        import base64

        adapter = bound_adapter()
        workbook = make_workbook_bytes([HEADER, ["Prof Queen"] + [""] * 10])
        driver = self._driver(
            {"status": 200, "b64": base64.b64encode(workbook).decode()}
        )

        assert adapter.fetch_workbook(driver, TRACKER_URL) == workbook


@pytest.mark.unit
class TestLoadSheet:
    def test_a_sheet_with_no_rows_at_all_is_an_error_not_an_empty_read(self):
        adapter = SharePointExcelAdapter()

        with pytest.raises(TrackerSyncError, match="empty"):
            adapter.load_sheet(make_workbook_bytes([]))

    def test_the_first_sheet_is_used_when_the_named_one_is_absent(self):
        adapter = SharePointExcelAdapter()

        workbook = make_workbook_bytes(
            [HEADER, ["Prof Queen"] + [""] * 10], sheet_name="Sheet1"
        )

        header, data = adapter.load_sheet(workbook)

        assert header == HEADER
        assert len(data) == 1

    def test_entirely_blank_rows_are_not_counted_as_data(self):
        adapter = SharePointExcelAdapter()

        _, data = adapter.load_sheet(
            make_workbook_bytes([HEADER, [None] * 11, ["Prof Queen"] + [""] * 10])
        )

        assert len(data) == 1


@pytest.mark.unit
class TestReadExistingKeysCellHandling:
    """Keys come from real spreadsheet cells, which are not always clean strings."""

    @staticmethod
    def _keys_for(rows):
        adapter = bound_adapter()
        adapter.fetch_workbook = lambda driver, url: b""
        adapter.load_sheet = lambda data: (HEADER, rows)
        return adapter.read_existing_keys(MagicMock(), MagicMock())

    def test_a_numeric_student_id_loses_the_float_suffix(self):
        """openpyxl hands back 123456 as 123456.0; the key must not carry the .0."""
        row = ["Prof Queen", "Doe", "John", 123456.0, "j@example.edu",
               "CSC-151-N855", "", "", "W", "", ""]

        assert self._keys_for([row]) == {("CSC-151-N855", "123456")}

    def test_a_leading_zero_student_id_is_preserved(self):
        row = ["Prof Queen", "Doe", "John", "0123456", "j@example.edu",
               "CSC-151-N855", "", "", "W", "", ""]

        assert self._keys_for([row]) == {("CSC-151-N855", "0123456")}

    def test_a_truncated_row_is_skipped_rather_than_raising(self):
        """A short row would IndexError; it must be skipped so the read survives."""
        short = ["Prof Queen", "Doe"]
        full = ["Prof Queen", "Doe", "John", "999", "j@example.edu",
                "CSC-151-N855", "", "", "W", "", ""]

        assert self._keys_for([short, full]) == {("CSC-151-N855", "999")}

    def test_a_row_missing_the_course_or_id_contributes_no_key(self):
        no_course = ["Prof Queen", "Doe", "John", "999", "", "", "", "", "", "", ""]
        no_id = ["Prof Queen", "Doe", "John", "", "", "CSC-151-N855",
                 "", "", "", "", ""]

        assert self._keys_for([no_course, no_id]) == set()


@pytest.mark.unit
class TestAppendConfirmation:
    """A write is not finished until the tracker can be seen to hold it.

    SharePoint's download.aspx keeps serving the pre-save workbook after an
    autosave. A run that returned without confirming would leave the next run
    reading a snapshot without these rows -- and appending the same students
    again.
    """

    @staticmethod
    def _adapter(row_counts):
        """An adapter whose successive re-reads report the given data-row counts."""
        adapter = bound_adapter()
        adapter._next_free_row = 11
        adapter._enter_grid_frame = lambda driver: None
        adapter._goto_cell = lambda driver, address: None
        adapter.fetch_workbook = lambda driver, url: b""
        counts = list(row_counts)
        adapter.reads = []

        def load_sheet(_data):
            count = counts.pop(0) if counts else 0
            adapter.reads.append(count)
            if isinstance(count, Exception):
                raise count
            return HEADER, [["x"] * 11] * count

        adapter.load_sheet = load_sheet
        return adapter

    def _append_one(self, adapter):
        with patch("cqc_cpcc.attendance_tracker.ActionChains"), \
                patch("cqc_cpcc.attendance_tracker.time.sleep"):
            return adapter.append_records(make_driver(), MagicMock(), [make_record()])

    def test_a_write_visible_on_the_first_re_read_stops_polling(self):
        # 9 existing data rows (next free row 11 = 9 data + header + 1), +1 appended.
        adapter = self._adapter([10])

        assert self._append_one(adapter) == 1
        assert adapter.reads == [10]

    def test_a_lagging_endpoint_is_polled_until_the_rows_appear(self):
        adapter = self._adapter([9, 9, 10])

        assert self._append_one(adapter) == 1
        assert adapter.reads == [9, 9, 10]

    def test_a_read_error_mid_poll_does_not_abandon_the_confirmation(self):
        """A transient download failure is not proof the write is lost."""
        adapter = self._adapter([TrackerSyncError("HTTP 503"), 10])

        assert self._append_one(adapter) == 1
        assert len(adapter.reads) == 2

    def test_rows_that_never_appear_raise_rather_than_reporting_success(self):
        from cqc_cpcc.attendance_tracker import WORKBOOK_PROPAGATION_ATTEMPTS

        adapter = self._adapter([9] * WORKBOOK_PROPAGATION_ATTEMPTS)

        with pytest.raises(TrackerSyncError, match="cannot confirm"):
            self._append_one(adapter)

        assert len(adapter.reads) == WORKBOOK_PROPAGATION_ATTEMPTS

    def test_nothing_written_means_nothing_to_confirm(self):
        adapter = self._adapter([])

        with patch("cqc_cpcc.attendance_tracker.ActionChains"), \
                patch("cqc_cpcc.attendance_tracker.time.sleep"):
            written = adapter.append_records(make_driver(), MagicMock(), [])

        assert written == 0
        assert adapter.reads == []


@pytest.mark.unit
class TestSyncLogging:
    def test_the_adapter_is_bound_to_the_tracker_url_before_it_reads(self):
        """Without the URL the adapter cannot re-download the workbook for itself."""
        adapter = SharePointExcelAdapter()
        adapter.read_existing_keys = lambda driver, wait: set()
        adapter.append_records = lambda driver, wait, records: len(records)

        with patch("cqc_cpcc.attendance_tracker.open_attendance_tracker",
                   return_value="tracker"):
            sync_records_to_tracker(
                make_driver(), MagicMock(), TRACKER_URL,
                [make_record()], dry_run=True, adapter=adapter,
            )

        assert adapter._tracker_url == TRACKER_URL

    def test_a_fully_synced_tracker_says_so_instead_of_listing_nothing(self, caplog):
        record = make_record()
        adapter = FakeAdapter(existing_keys={record_key(record)})

        with patch("cqc_cpcc.attendance_tracker.open_attendance_tracker",
                   return_value="tracker"), caplog.at_level("INFO"):
            result = sync_records_to_tracker(
                make_driver(), MagicMock(), "https://example.sharepoint.com/x",
                [record], dry_run=True, adapter=adapter,
            )

        assert result.skipped_existing == 1
        assert "already on the tracker" in caplog.text
