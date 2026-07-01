#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for the BrightSpace "Download All" popup handling.

These cover the pure-logic pieces (no live browser): deriving the download body
URL from the D2L popup window URL and extracting the ZIP link from the body HTML.
"""

from unittest.mock import MagicMock

import pytest

import cqc_cpcc.utilities.brightspace_fetch as bf

POPUP_URL = (
    "https://brightspace.cpcc.edu/d2l/common/popup/popup.d2l?ou=338334"
    "&queryString=ou%3D338334%26db%3D789761%26dwt%3D3%26addp%3D2%26fn%3DGetListFiles"
    "&footerMsg=&buttonOffset=0&popBodySrc=%2Fd2l%2Flms%2Fdropbox%2Fdownload_files.d2l"
    "&width=750&height=350"
)
EXPECTED_BODY_URL = (
    "https://brightspace.cpcc.edu/d2l/lms/dropbox/download_files.d2l"
    "?ou=338334&db=789761&dwt=3&addp=2&fn=GetListFiles"
)


@pytest.mark.unit
def test_zip_link_regex_matches_viewfile_href():
    html = (
        '<span id="z_b"><a href="/d2l/common/viewFile.d2lfile/Temp/178/'
        'Project%201%20Download%20Jun%2025,%202026%20149%20PM.zip?ou=338334&amp;fid=ABC"'
        ' title="Open">x</a></span>'
    )
    match = bf._ZIP_LINK_RE.search(html)
    assert match is not None
    assert match.group(1).startswith("/d2l/common/viewFile.d2lfile/")
    assert match.group(1).endswith("fid=ABC")


@pytest.mark.unit
def test_find_download_popup_body_url_from_popup_window():
    driver = MagicMock()
    driver.window_handles = ["main", "popup"]

    def switch_window(handle):
        driver.current_url = "https://brightspace.cpcc.edu/d2l/le/12345" \
            if handle == "main" else POPUP_URL

    driver.switch_to.window.side_effect = switch_window

    body_url, popup_handle = bf._find_download_popup_body_url(driver, "main")
    assert popup_handle == "popup"
    assert body_url == EXPECTED_BODY_URL


@pytest.mark.unit
def test_find_download_popup_body_url_none_when_no_popup():
    driver = MagicMock()
    driver.window_handles = ["main"]

    def switch_window(handle):
        driver.current_url = "https://brightspace.cpcc.edu/d2l/le/12345"

    driver.switch_to.window.side_effect = switch_window

    body_url, popup_handle = bf._find_download_popup_body_url(driver, "main")
    assert body_url is None and popup_handle is None


@pytest.mark.unit
def test_extract_ready_zip_href_parses_body_html(mocker):
    driver = MagicMock()
    fake_session = MagicMock()
    fake_resp = MagicMock()
    fake_resp.text = (
        '<div><label>Your file is ready to download.</label>'
        '<a href="/d2l/common/viewFile.d2lfile/Temp/1/Project%201.zip?ou=1&amp;fid=Z">'
        'Project 1.zip</a></div>'
    )
    fake_resp.raise_for_status.return_value = None
    fake_session.get.return_value = fake_resp
    mocker.patch.object(bf, "_session_from_driver", return_value=fake_session)

    href = bf._extract_ready_zip_href(driver, "https://brightspace.cpcc.edu/body")
    # HTML entity decoded (&amp; -> &).
    assert href == "/d2l/common/viewFile.d2lfile/Temp/1/Project%201.zip?ou=1&fid=Z"


@pytest.mark.unit
def test_extract_ready_zip_href_none_while_preparing(mocker):
    driver = MagicMock()
    fake_session = MagicMock()
    fake_resp = MagicMock()
    fake_resp.text = "<div>Preparing your files, please wait...</div>"
    fake_resp.raise_for_status.return_value = None
    fake_session.get.return_value = fake_resp
    mocker.patch.object(bf, "_session_from_driver", return_value=fake_session)

    assert bf._extract_ready_zip_href(driver, "https://brightspace.cpcc.edu/body") is None


# ---------------------------------------------------------------------------
# Instructions scraping
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_collect_instructions_text_uses_js_result():
    driver = MagicMock()
    driver.execute_script.return_value = "  Write a program that sums two numbers.  "
    assert bf._collect_instructions_text(driver) == "Write a program that sums two numbers."
    # XPath fallback not consulted when JS already returns text.
    driver.find_elements.assert_not_called()


@pytest.mark.unit
def test_collect_instructions_text_falls_back_to_xpath():
    driver = MagicMock()
    driver.execute_script.return_value = ""  # JS found nothing
    el = MagicMock()
    el.text = "Instructions from inline block"
    driver.find_elements.return_value = [el]
    assert bf._collect_instructions_text(driver) == "Instructions from inline block"


@pytest.mark.unit
def test_collect_instructions_text_returns_none_when_empty():
    driver = MagicMock()
    driver.execute_script.return_value = None
    driver.find_elements.return_value = []
    assert bf._collect_instructions_text(driver) is None


@pytest.mark.unit
def test_fetch_assignment_instructions_prefers_editor(mocker):
    driver = MagicMock()
    wait = MagicMock()
    mocker.patch.object(bf, "login_if_needed")
    mocker.patch.object(bf, "wait_for_ajax")
    # The editor is the authoritative source and must be tried FIRST — view-mode
    # scraping is unreliable on the submissions/marking page (it would grab student
    # submission text), so _collect_instructions_text must NOT be consulted when the
    # editor yields text.
    mocker.patch.object(bf, "_await_brightspace_after_login", return_value=True)
    edit = mocker.patch.object(bf, "_open_assignment_editor", return_value=True)
    mocker.patch.object(bf, "_read_editor_instructions", return_value="Real instructions")
    collect = mocker.patch.object(bf, "_collect_instructions_text", return_value="STUDENT SUBMISSION TEXT")

    text = bf.fetch_assignment_instructions(driver, wait, "https://bs/d2l/lms/dropbox/x")
    assert text == "Real instructions"
    driver.get.assert_called_once()
    edit.assert_called_once()
    # Editor short-circuits before any view-mode scraping.
    collect.assert_not_called()


@pytest.mark.unit
def test_fetch_assignment_instructions_opens_editor_when_inline_missing(mocker):
    driver = MagicMock()
    wait = MagicMock()
    mocker.patch.object(bf, "login_if_needed")
    mocker.patch.object(bf, "wait_for_ajax")
    # No inline instructions -> must open the editor and read it.
    mocker.patch.object(bf, "_await_brightspace_after_login", return_value=True)
    mocker.patch.object(bf, "_collect_instructions_text", return_value=None)
    edit = mocker.patch.object(bf, "_open_assignment_editor", return_value=True)
    mocker.patch.object(bf, "_read_editor_instructions",
                        return_value="Write a program that sums two numbers.")

    text = bf.fetch_assignment_instructions(driver, wait, "https://bs/d2l/lms/dropbox/x")
    assert text == "Write a program that sums two numbers."
    edit.assert_called_once()


@pytest.mark.unit
def test_read_editor_instructions_polls_until_text(mocker):
    driver = MagicMock()
    # First poll empty (editor initializing), second poll has content.
    driver.execute_script.side_effect = ["", "  Instructions body text  "]
    mocker.patch.object(bf.time, "sleep")  # don't actually wait
    assert bf._read_editor_instructions(driver, timeout=5) == "Instructions body text"


@pytest.mark.unit
def test_find_edit_assignment_location_reads_data_location():
    driver = MagicMock()
    el = MagicMock()
    el.get_attribute.return_value = "/d2l/le/activities/edit/ABC?cft=dropbox"
    driver.find_element.return_value = el
    loc = bf._find_edit_assignment_location(driver)
    assert loc == "https://brightspace.cpcc.edu/d2l/le/activities/edit/ABC?cft=dropbox"


@pytest.mark.unit
def test_find_edit_assignment_location_falls_back_to_js(mocker):
    driver = MagicMock()
    driver.find_element.side_effect = bf.NoSuchElementException("nope")
    driver.execute_script.return_value = "/d2l/le/activities/edit/XYZ"
    loc = bf._find_edit_assignment_location(driver)
    assert loc == "https://brightspace.cpcc.edu/d2l/le/activities/edit/XYZ"


@pytest.mark.unit
def test_open_assignment_editor_navigates_to_data_location(mocker):
    driver = MagicMock()
    wait = MagicMock()
    mocker.patch.object(bf, "_find_edit_assignment_location",
                        return_value="https://brightspace.cpcc.edu/d2l/le/activities/edit/ABC")
    mocker.patch.object(bf, "wait_for_ajax")
    assert bf._open_assignment_editor(driver, wait) is True
    driver.get.assert_called_once_with("https://brightspace.cpcc.edu/d2l/le/activities/edit/ABC")


@pytest.mark.unit
def test_on_brightspace_distinguishes_login_host():
    driver = MagicMock()
    driver.current_url = ("https://brightspace.cpcc.edu/d2l/lms/dropbox/admin/mark/"
                          "folder_submissions_users.d2l?db=1&ou=2")
    assert bf._on_brightspace(driver) is True
    # Mid-SSO on the Microsoft login host must NOT count as "on BrightSpace".
    driver.current_url = "https://login.microsoftonline.com/abc/saml2?SAMLRequest=xyz"
    assert bf._on_brightspace(driver) is False
    # A lookalike host that merely *contains* the domain as a substring must NOT
    # pass (guards against incomplete URL-substring sanitization).
    driver.current_url = "https://brightspace.cpcc.edu.evil.example.com/d2l/home"
    assert bf._on_brightspace(driver) is False
    # A legitimate subdomain still counts.
    driver.current_url = "https://learn.brightspace.cpcc.edu/d2l/home"
    assert bf._on_brightspace(driver) is True


@pytest.mark.unit
def test_await_brightspace_after_login_renavigates_to_target(mocker):
    driver = MagicMock()
    mocker.patch.object(bf, "wait_for_ajax")
    mocker.patch.object(bf.time, "sleep")
    url = ("https://brightspace.cpcc.edu/d2l/lms/dropbox/admin/mark/"
           "folder_submissions_users.d2l?db=1&ou=2")
    # Post-SSO we land on /home (on BrightSpace but NOT the requested page); the gate
    # must re-navigate to the target URL so the scrape runs on the right page.
    driver.current_url = "https://brightspace.cpcc.edu/d2l/home"
    driver.get.side_effect = lambda u: setattr(driver, "current_url", u)
    assert bf._await_brightspace_after_login(driver, url, lambda *_: None, timeout=3) is True
    driver.get.assert_called_once_with(url)


@pytest.mark.unit
def test_fetch_quiz_instructions_prefers_first_question(mocker):
    driver = MagicMock()
    wait = MagicMock()
    mocker.patch.object(bf, "login_if_needed")
    mocker.patch.object(bf, "wait_for_ajax")
    q1 = MagicMock(); q1.text = "Question 1: explain recursion."
    q2 = MagicMock(); q2.text = "Question 2: something else."
    driver.find_elements.return_value = [q1, q2]

    text = bf.fetch_quiz_instructions(driver, wait, "https://bs/d2l/lms/quizzing/x")
    assert text == "Question 1: explain recursion."


# ---------------------------------------------------------------------------
# Quiz route — Consistent Evaluation rewrite
# ---------------------------------------------------------------------------

QUIZ_EDIT_URL = (
    "https://brightspace.cpcc.edu/d2l/le/activities/edit/"
    "TmpZd05sODFNVEF3TUY4eE1ERTFORGMwLjMwNDA0OA?cft=quiz&ou=304048"
    "&returnUrl=%2Fd2l%2Flms%2Fquizzing%2Fadmin%2Fquizzes_manage.d2l%3Fou%3D304048"
    "&qi=1015474"
)


@pytest.mark.unit
def test_derive_quiz_grading_url_from_query():
    out = bf.derive_quiz_grading_url(QUIZ_EDIT_URL)
    assert out == (
        "https://brightspace.cpcc.edu/d2l/lms/quizzing/admin/mark/"
        "quiz_mark_users.d2l?ou=304048&qi=1015474"
    )


@pytest.mark.unit
def test_derive_quiz_grading_url_finds_qi_in_return_url():
    # qi/ou only present inside an encoded returnUrl, not the top-level query.
    url = (
        "https://brightspace.cpcc.edu/d2l/le/activities/edit/X?cft=quiz"
        "&returnUrl=%2Fd2l%2Flms%2Fquizzing%2Fadmin%2Fmark%2Fquiz_mark_users.d2l"
        "%3Fou%3D304048%26qi%3D1015474"
    )
    out = bf.derive_quiz_grading_url(url)
    assert out.endswith("quiz_mark_users.d2l?ou=304048&qi=1015474")


@pytest.mark.unit
def test_derive_quiz_grading_url_raises_without_ou_qi():
    with pytest.raises(ValueError):
        bf.derive_quiz_grading_url("https://brightspace.cpcc.edu/d2l/le/activities/edit/X?cft=quiz")


@pytest.mark.unit
def test_attempt_index_prefers_label_then_attempt_id():
    assert bf._attempt_index({"label": "attempt 3", "attemptId": "999"}) == 3
    assert bf._attempt_index({"label": "", "attemptId": "42"}) == 42
    assert bf._attempt_index({"label": "overall", "attemptId": "x"}) == 0


@pytest.mark.unit
def test_keep_last_attempt_per_user_keeps_latest():
    attempts = [
        {"userId": "1", "attemptId": "10", "label": "attempt 1", "name": "Ann"},
        {"userId": "1", "attemptId": "20", "label": "attempt 2", "name": "Ann"},
        {"userId": "2", "attemptId": "30", "label": "attempt 1", "name": "Bob"},
    ]
    kept = bf._keep_last_attempt_per_user(attempts)
    by_user = {a["userId"]: a for a in kept}
    assert set(by_user) == {"1", "2"}
    assert by_user["1"]["attemptId"] == "20"  # Ann's 2nd attempt wins
    assert by_user["2"]["attemptId"] == "30"


@pytest.mark.unit
def test_written_response_ext_uses_first_accepted_then_txt():
    assert bf._written_response_ext([".java", ".txt"]) == "java"
    assert bf._written_response_ext(["py"]) == "py"
    assert bf._written_response_ext([]) == "txt"


@pytest.mark.unit
def test_gather_quiz_attempts_filters_incomplete_rows():
    driver = MagicMock()
    driver.execute_script.return_value = [
        {"attemptId": "10", "userId": "1", "name": "Ann", "label": "attempt 1"},
        {"attemptId": "", "userId": "2", "name": "Bad", "label": "attempt 1"},  # no attemptId
        {"attemptId": "11", "userId": "3", "name": "", "label": "attempt 1"},   # name backfilled
    ]
    rows = bf._gather_quiz_attempts(driver)
    assert [r["userId"] for r in rows] == ["1", "3"]
    assert rows[1]["name"] == "user_3"


@pytest.mark.unit
def test_capture_quiz_attempt_saves_written_response_and_files(tmp_path, mocker):
    driver = MagicMock()
    driver.execute_script.return_value = {
        "responses": ["public class Main { }"],
        "attachments": [
            {"href": "https://bs/d2l/le/viewFile?fileId=99", "name": "Main.java"},
        ],
    }
    downloaded = []
    mocker.patch.object(
        bf, "download_with_driver_session",
        side_effect=lambda d, href, dest: downloaded.append((href, dest)) or True,
    )
    folder = str(tmp_path / "Ann")
    saved = bf._capture_quiz_attempt(driver, folder, [".java"], lambda *_: None, "Ann")

    assert saved == 2  # one attachment + one written-response file
    # Written response saved with the first accepted extension.
    resp_path = tmp_path / "Ann" / "response.java"
    assert resp_path.read_text() == "public class Main { }"
    assert downloaded and downloaded[0][0].endswith("fileId=99")


@pytest.mark.unit
def test_fetch_quiz_file_uploads_orchestrates_attempts(mocker, tmp_path):
    driver = MagicMock()
    wait = MagicMock()
    mocker.patch.object(bf, "_open_and_login")
    mocker.patch.object(bf, "_set_max_results_per_page")
    mocker.patch.object(bf.tempfile, "mkdtemp", return_value=str(tmp_path))
    mocker.patch.object(bf, "_gather_quiz_attempts", return_value=[
        {"userId": "1", "attemptId": "10", "label": "attempt 1", "name": "Ann"},
        {"userId": "1", "attemptId": "20", "label": "attempt 2", "name": "Ann"},
        {"userId": "2", "attemptId": "30", "label": "attempt 1", "name": "Bob"},
    ])
    opened = mocker.patch.object(bf, "_open_quiz_attempt", return_value=True)
    captured = mocker.patch.object(bf, "_capture_quiz_attempt", return_value=1)

    out = bf.fetch_quiz_file_uploads(driver, wait, QUIZ_EDIT_URL, [".java"])
    assert out == str(tmp_path)
    # Pruned to one attempt per user => 2 attempts opened/captured, Ann's latest (20).
    assert opened.call_count == 2
    assert captured.call_count == 2
    opened_ids = {c.args[3]["attemptId"] for c in opened.call_args_list}
    assert opened_ids == {"20", "30"}


@pytest.mark.unit
def test_fetch_quiz_file_uploads_skips_attempt_that_wont_open(mocker, tmp_path):
    driver = MagicMock(); wait = MagicMock()
    mocker.patch.object(bf, "_open_and_login")
    mocker.patch.object(bf, "_set_max_results_per_page")
    mocker.patch.object(bf.tempfile, "mkdtemp", return_value=str(tmp_path))
    mocker.patch.object(bf, "_gather_quiz_attempts", return_value=[
        {"userId": "1", "attemptId": "10", "label": "attempt 1", "name": "Ann"},
    ])
    mocker.patch.object(bf, "_open_quiz_attempt", return_value=False)  # cannot open
    captured = mocker.patch.object(bf, "_capture_quiz_attempt")
    bf.fetch_quiz_file_uploads(driver, wait, QUIZ_EDIT_URL, [".java"])
    captured.assert_not_called()  # never capture when the attempt page won't open


@pytest.mark.unit
def test_gather_quiz_attempts_returns_empty_on_js_error():
    driver = MagicMock()
    driver.execute_script.side_effect = RuntimeError("boom")
    assert bf._gather_quiz_attempts(driver) == []


@pytest.mark.unit
def test_open_quiz_attempt_clicks_link_and_waits(mocker):
    driver = MagicMock(); wait = MagicMock()
    mocker.patch.object(bf, "wait_for_ajax")
    link = MagicMock()
    driver.find_element.return_value = link
    ok = bf._open_quiz_attempt(driver, wait, "https://grid",
                               {"attemptId": "10", "userId": "1", "name": "Ann"})
    assert ok is True
    driver.get.assert_called_once_with("https://grid")
    link.click.assert_called_once()


@pytest.mark.unit
def test_open_quiz_attempt_returns_false_when_link_missing(mocker):
    from selenium.common import NoSuchElementException
    driver = MagicMock(); wait = MagicMock()
    mocker.patch.object(bf, "wait_for_ajax")
    driver.find_element.side_effect = NoSuchElementException("no link")
    ok = bf._open_quiz_attempt(driver, wait, "https://grid",
                               {"attemptId": "10", "userId": "1", "name": "Ann"})
    assert ok is False


@pytest.mark.unit
def test_capture_quiz_attempt_reports_zero_when_no_answer(mocker, tmp_path):
    driver = MagicMock()
    driver.execute_script.return_value = {"responses": [], "attachments": []}
    msgs = []
    o = bf._capture_quiz_attempt(driver, str(tmp_path / "Ann"), [".java"], msgs.append, "Ann")
    assert o == 0
    assert any("No answer files" in m for m in msgs)
