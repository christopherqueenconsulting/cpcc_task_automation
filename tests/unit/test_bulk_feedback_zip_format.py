"""The ZIP BrightSpace's dropbox "Add Feedback Files" accepts: the format contract.

These tests memorialise a live finding. Do not loosen them without a one-learner live test
(see docs/BRIGHTSPACE_AUTOMATION_HANDOFF.md, "Bulk feedback ZIP format").

BrightSpace says "Upload compressed zip file in the same format that it was downloaded".
Verified live on 2026-11-11 (one learner, a throwaway .docx per variant, draft only):

    ZIP folder                                           result
    "<userId>-<dropboxId> - <name> - <date time>/"       attached (the download's own name)
    "<userId>-<dropboxId> - <name>/"                     attached (no date needed)
    "<userId>-<dropboxId>/"                              NOT attached: "Distribute Feedback ...
                                                         Identifiers were not found on these files"
    "<name>/" (the grader's bare key, our old ZIP)       attached nothing

All names, ids and the dropbox id below are synthetic.
"""
import re
import zipfile
from unittest.mock import MagicMock

import pytest

from cqc_cpcc.utilities import brightspace_writeback as wb

DROPBOX = "900"
# BrightSpace's own folder shape, minus the optional date: "<userId>-<dropboxId> - <name>"
ACCEPTED_FOLDER = re.compile(r"^\d+-\d+ - \S.*$")
# The two shapes BrightSpace did not match.
ID_ONLY_FOLDER = re.compile(r"^\d+-\d+$")
BARE_NAME_FOLDER = re.compile(r"^[^\d].*$")

# Texts copied from the live dialog (2026-11-11).
LIVE_PROCESSING_TEXT = "Processing Extracted Files... Loading... Processing..."
LIVE_DISTRIBUTE_TEXT = (
    "Distribute Feedback Close this Dialog Feedback requiring distribution: 1 "
    "Identifiers were not found on these files. Attach each file to a submission. "
    "Select File for Feedback TEST_C_Feedback_DELETE_ME")


def _build(tmp_path, people):
    """``people``: [(user_id, display_name)] -> the ZIP the product would upload."""
    docs, folders = {}, {}
    for uid, name in people:
        doc = tmp_path / f"tmp{uid}.docx"          # temp-style names, like the app's docs
        doc.write_bytes(b"x")
        docs[name] = str(doc)
        folders[name] = wb.bulk_feedback_folder_name(uid, DROPBOX, name)
    path = wb.build_feedback_docs_zip(
        docs, str(tmp_path / "out.zip"), folder_names=folders, file_name=wb.BULK_FEEDBACK_FILE_NAME)
    return zipfile.ZipFile(path)


@pytest.mark.unit
def test_every_zip_entry_is_a_download_style_folder_with_the_named_file(tmp_path):
    z = _build(tmp_path, [("100003", "Ben Sample"), ("100004", "Ada Example")])
    assert sorted(z.namelist()) == ["100003-900 - Ben Sample/Feedback.docx",
                                    "100004-900 - Ada Example/Feedback.docx"]
    for entry in z.namelist():
        folder, _, filename = entry.partition("/")
        assert ACCEPTED_FOLDER.match(folder), entry
        assert filename == wb.BULK_FEEDBACK_FILE_NAME == "Feedback.docx"


@pytest.mark.unit
def test_folders_sit_at_the_zip_root_with_no_wrapper_and_no_extra_files(tmp_path):
    """Same layout as the download: folders at the root (the real one also has an
    index.html, which was NOT needed for the import to match)."""
    names = _build(tmp_path, [("100003", "Ben Sample")]).namelist()
    assert all(n.count("/") == 1 for n in names)               # root/folder/file, nothing deeper
    assert not any(n.startswith("/") or "index.html" in n for n in names)


@pytest.mark.unit
def test_the_user_id_and_dropbox_id_lead_the_folder(tmp_path):
    folder = wb.bulk_feedback_folder_name("100003", DROPBOX, "Ben Sample")
    assert folder == "100003-900 - Ben Sample"
    assert folder.split(" - ")[0] == "100003-900"               # <userId>-<dropboxId>


@pytest.mark.unit
def test_the_date_suffix_is_not_part_of_the_folder_name():
    """Verified live: the date is optional, so we do not invent one."""
    folder = wb.bulk_feedback_folder_name("100003", DROPBOX, "Ben Sample")
    assert not re.search(r"\d{4}|AM|PM", folder.split(" - ", 1)[1])


@pytest.mark.unit
@pytest.mark.parametrize("uid,db,name", [
    ("100003", DROPBOX, ""),          # id-only shape: BrightSpace: "Identifiers were not found"
    ("100003", DROPBOX, "  /:*  "),   # nothing left once ZIP-unsafe characters are removed
    ("", DROPBOX, "Ben Sample"),      # no user id
    (None, DROPBOX, "Ben Sample"),
    ("100003", "", "Ben Sample"),     # no dropbox id
    ("abc", DROPBOX, "Ben Sample"),   # not a BrightSpace numeric id
])
def test_a_folder_that_would_not_match_is_refused_rather_than_built(uid, db, name):
    with pytest.raises(ValueError):
        wb.bulk_feedback_folder_name(uid, db, name)


@pytest.mark.unit
def test_the_shapes_brightspace_rejected_are_never_what_we_build(tmp_path):
    for entry in _build(tmp_path, [("100003", "Ben Sample")]).namelist():
        folder = entry.split("/")[0]
        assert not ID_ONLY_FOLDER.match(folder)
        assert not BARE_NAME_FOLDER.match(folder)               # the grader's bare name


@pytest.mark.unit
def test_zip_unsafe_characters_leave_the_id_prefix_intact():
    folder = wb.bulk_feedback_folder_name("100003", DROPBOX, 'Ben "B/J" Sample')
    assert folder == "100003-900 - Ben B J Sample" and ACCEPTED_FOLDER.match(folder)


@pytest.mark.unit
def test_the_grader_key_is_a_bare_name_so_the_folder_must_be_rebuilt():
    """Why the key cannot be the folder: the grader strips the id and the date."""
    from cqc_cpcc.utilities.zip_grading_utils import _parse_student_folder_name
    key = _parse_student_folder_name("100003-900 - Ben Sample - Nov 8, 2026 6:10 PM")
    assert key == "Ben Sample"
    assert not ACCEPTED_FOLDER.match(key)


@pytest.mark.unit
def test_dropbox_id_is_the_db_of_the_assignment_url():
    url = "https://bs.example/d2l/lms/dropbox/admin/mark/folder_submissions_users.d2l?ou=1&db=900"
    assert wb._dropbox_id(url) == DROPBOX
    assert wb._dropbox_id(url.replace("db=900", "db=7") + "&x=1") == "7"


# ---- the dialog's behaviour around the ZIP -------------------------------------------------

def _dialog_driver(dialog_text):
    from selenium.webdriver.common.by import By
    iframe = MagicMock()
    iframe.get_attribute.return_value = "Add Feedback Files"
    driver = MagicMock()

    def script(js, *args):
        if js is wb._CLICK_ADD_FEEDBACK_FILES_JS or js is wb._FILE_LISTED_JS \
                or js is wb._CLICK_TEXT_EXACT_JS:
            return True
        if js is wb._DIALOG_TEXT_JS:
            return dialog_text
        return None
    driver.execute_script.side_effect = script
    driver.find_elements.side_effect = lambda by, value: (
        [iframe] if (by == By.TAG_NAME and value == "iframe")
        else [MagicMock()] if ("addbuttons" in value or "input[type=file]" in value) else [])
    return driver


@pytest.mark.unit
def test_the_live_processing_text_counts_as_still_busy():
    assert wb._IMPORT_BUSY_RE.search(LIVE_PROCESSING_TEXT)
    assert not wb._IMPORT_ERROR_RE.search(LIVE_PROCESSING_TEXT)


@pytest.mark.unit
def test_the_live_distribute_feedback_prompt_is_a_failed_import(mocker, tmp_path):
    """BrightSpace could not match the ZIP and asked for manual matching: not a success."""
    mocker.patch("time.sleep")
    assert wb._IMPORT_ERROR_RE.search(LIVE_DISTRIBUTE_TEXT)
    z = tmp_path / "fb.zip"; z.write_bytes(b"PK\x03\x04")
    msgs: list = []
    assert wb.import_feedback_zip(_dialog_driver(LIVE_DISTRIBUTE_TEXT), str(z), msgs.append) is False
    assert any("Identifiers were not found" in m for m in msgs)


@pytest.mark.unit
def test_an_attached_import_is_confirmed_by_the_file_name_on_the_learners_page(mocker):
    """The dialog's closing page text is not proof; the file on the learner's page is."""
    mocker.patch("time.sleep")
    driver = MagicMock()
    driver.execute_script.return_value = ["Feedback.docx"]
    assert wb._wait_for_feedback_attachment(driver, timeout=1, name=wb.BULK_FEEDBACK_FILE_NAME)
    driver.execute_script.return_value = ["Some_Other_File.docx"]
    assert not wb._wait_for_feedback_attachment(driver, timeout=1, name=wb.BULK_FEEDBACK_FILE_NAME)
