#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for find_student.py module."""

import pytest
from unittest.mock import MagicMock, patch

from cqc_cpcc.find_student import FindStudents


@pytest.mark.unit
class TestFindStudents:
    """Test the FindStudents class."""
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_init_processes_student_info(self, mock_get_driver, mock_my_colleges_class):
        """__init__ should process student info from MyColleges."""
        # Setup mocks
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        mock_mc_instance.get_student_info.return_value = {
            "123": ("Student A", "a@email.com", "Course 1"),
            "456": ("Student B", "b@email.com", "Course 2")
        }
        mock_my_colleges_class.return_value = mock_mc_instance
        
        # Create FindStudents instance
        finder = FindStudents(active_courses_only=True)
        
        # Verify initialization
        mock_get_driver.assert_called_once()
        mock_my_colleges_class.assert_called_once_with(mock_driver, mock_wait)
        mock_mc_instance.process_student_info.assert_called_once_with(True)
        assert finder.student_info is not None
        assert not finder.is_running()
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_init_with_active_courses_false(self, mock_get_driver, mock_my_colleges_class):
        """__init__ should pass active_courses_only parameter correctly."""
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        mock_mc_instance.get_student_info.return_value = {}
        mock_my_colleges_class.return_value = mock_mc_instance
        
        finder = FindStudents(active_courses_only=False)
        
        mock_mc_instance.process_student_info.assert_called_once_with(False)
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_get_student_info_items_returns_items(self, mock_get_driver, mock_my_colleges_class):
        """get_student_info_items should return dictionary items."""
        mock_driver, mock_wait = MagicMock(), MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        student_info = {
            "123": ("Student A", "a@email.com", "Course 1"),
            "456": ("Student B", "b@email.com", "Course 2")
        }
        mock_mc_instance.get_student_info.return_value = student_info
        mock_my_colleges_class.return_value = mock_mc_instance
        
        finder = FindStudents()
        items = list(finder.get_student_info_items())
        
        assert len(items) == 2
        assert ("123", ("Student A", "a@email.com", "Course 1")) in items


@pytest.mark.unit
class TestGetStudentByEmail:
    """Test get_student_by_email method."""
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_finds_student_with_matching_email(self, mock_get_driver, mock_my_colleges_class):
        """Should find student with exact email match."""
        mock_driver, mock_wait = MagicMock(), MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        student_info = {
            "123": ("John Doe", "john@email.com", "CTS101"),
            "456": ("Jane Smith", "jane@email.com", "CTS102"),
        }
        mock_mc_instance.get_student_info.return_value = student_info
        mock_my_colleges_class.return_value = mock_mc_instance
        
        finder = FindStudents()
        results = finder.get_student_by_email("john@email.com")
        
        assert len(results) == 1
        assert results[0] == ("123", "John Doe", "john@email.com", "CTS101")
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_finds_multiple_students_with_same_email(self, mock_get_driver, mock_my_colleges_class):
        """Should find all students with matching email (duplicate enrollments)."""
        mock_driver, mock_wait = MagicMock(), MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        student_info = {
            "123": ("John Doe", "john@email.com", "CTS101"),
            "456": ("John Doe", "john@email.com", "CTS102"),
        }
        mock_mc_instance.get_student_info.return_value = student_info
        mock_my_colleges_class.return_value = mock_mc_instance
        
        finder = FindStudents()
        results = finder.get_student_by_email("john@email.com")
        
        assert len(results) == 2
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_returns_empty_list_when_no_match(self, mock_get_driver, mock_my_colleges_class):
        """Should return empty list when email not found."""
        mock_driver, mock_wait = MagicMock(), MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        student_info = {
            "123": ("John Doe", "john@email.com", "CTS101"),
        }
        mock_mc_instance.get_student_info.return_value = student_info
        mock_my_colleges_class.return_value = mock_mc_instance
        
        finder = FindStudents()
        results = finder.get_student_by_email("nonexistent@email.com")
        
        assert results == []


@pytest.mark.unit
class TestGetStudentByStudentId:
    """Test get_student_by_student_id method."""
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_finds_student_with_matching_id(self, mock_get_driver, mock_my_colleges_class):
        """Should find student with exact ID match."""
        mock_driver, mock_wait = MagicMock(), MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        student_info = {
            "123456": ("John Doe", "john@email.com", "CTS101"),
            "789012": ("Jane Smith", "jane@email.com", "CTS102"),
        }
        mock_mc_instance.get_student_info.return_value = student_info
        mock_my_colleges_class.return_value = mock_mc_instance
        
        finder = FindStudents()
        results = finder.get_student_by_student_id("123456")
        
        assert len(results) == 1
        assert results[0] == ("123456", "John Doe", "john@email.com", "CTS101")
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_returns_empty_list_when_id_not_found(self, mock_get_driver, mock_my_colleges_class):
        """Should return empty list when student ID not found."""
        mock_driver, mock_wait = MagicMock(), MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        student_info = {
            "123456": ("John Doe", "john@email.com", "CTS101"),
        }
        mock_mc_instance.get_student_info.return_value = student_info
        mock_my_colleges_class.return_value = mock_mc_instance
        
        finder = FindStudents()
        results = finder.get_student_by_student_id("999999")
        
        assert results == []


@pytest.mark.unit
class TestGetStudentByName:
    """Test get_student_by_name method."""
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_finds_student_with_matching_name_parts(self, mock_get_driver, mock_my_colleges_class):
        """Should find student when at least 2 name parts match."""
        mock_driver, mock_wait = MagicMock(), MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        student_info = {
            "123": ("Doe, John Michael", "john@email.com", "CTS101"),
            "456": ("Smith, Jane", "jane@email.com", "CTS102"),
        }
        mock_mc_instance.get_student_info.return_value = student_info
        mock_my_colleges_class.return_value = mock_mc_instance
        
        finder = FindStudents()
        results = finder.get_student_by_name("John Doe")
        
        assert len(results) == 1
        assert results[0][1] == "Doe, John Michael"
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_returns_empty_list_when_no_match(self, mock_get_driver, mock_my_colleges_class):
        """Should return empty list when name doesn't match."""
        mock_driver, mock_wait = MagicMock(), MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        student_info = {
            "123": ("Doe, John", "john@email.com", "CTS101"),
        }
        mock_mc_instance.get_student_info.return_value = student_info
        mock_my_colleges_class.return_value = mock_mc_instance
        
        finder = FindStudents()
        results = finder.get_student_by_name("Bob Smith")
        
        assert results == []


@pytest.mark.unit
class TestDynamicNameSearch:
    """Names are matched by word, in any order and case, not as one strict string."""

    ROSTER = {
        "1": ("Doe, John Michael", "a@example.edu", "CTS101"),
        "2": ("Doe, Jane A.", "b@example.edu", "CTS102"),
        "3": ("Smith, Jane", "c@example.edu", "CTS103"),
        "4": ("Garcia-Lopez, Maria", "d@example.edu", "CTS104"),
        "5": ("O'Brien, Sean", "e@example.edu", "CTS105"),
        "6": ("Smyth, Joan", "f@example.edu", "CTS106"),
        "7": ("Pe\u00f1a, Rosa", "g@example.edu", "CTS107"),
        "8": ("\u0418\u0432\u0430\u043d\u043e\u0432, \u041f\u0451\u0442\u0440", "h@example.edu", "CTS108"),
        "9": ("\u00d8rsted, \u0141ukasz", "i@example.edu", "CTS109"),
        "10": ("\u738b, \u5c0f\u660e", "j@example.edu", "CTS110"),
    }

    def _finder(self):
        return FindStudents(student_info=dict(self.ROSTER))

    def _ids(self, query):
        return [s[0] for s in self._finder().get_student_by_name(query)]

    @pytest.mark.parametrize("query,expected", [
        ("john", ["1"]),                     # first name alone
        ("DOE", ["2", "1"]),                 # last name alone, any case; sorted by name
        ("michael", ["1"]),                  # middle name alone
        ("a", ["2"]),                        # an initial matches the start of a name part
        ("m", ["1", "4"]),                   # Michael, Maria
        ("jo", ["1", "6"]),                  # the start of a name
        ("garcia", ["4"]),                   # one half of a hyphenated name
        ("obrien", ["5"]),                   # apostrophe ignored
        ("pena", ["7"]),                     # accents ignored
        ("smyth", ["6", "3"]),               # a one-letter difference still finds Smith, ranked lower
        ("\u0438\u0432\u0430\u043d\u043e\u0432", ["8"]),   # Cyrillic name found by itself
        ("\u041f\u0451\u0442\u0440", ["8"]),               # Cyrillic given name
        ("\u00f8rsted", ["9"]),                 # o-slash has no ASCII fold; kept as is
        ("\u0142ukasz", ["9"]),                 # l-stroke
        ("\u738b", ["10"]),                      # CJK
        ("zzz", []),
        ("", []),
        ("  ,  ", []),
    ])
    def test_single_word(self, query, expected):
        assert self._ids(query) == expected

    def test_more_matching_words_rank_first(self):
        ranked = self._finder().rank_students_by_name("jane doe")
        assert [(hits, words, s[0]) for hits, words, s in ranked] == [
            (2, 2, "2"),  # Doe, Jane A.: both words
            (1, 2, "1"),  # Doe, John Michael: Doe only
            (1, 2, "3"),  # Smith, Jane: Jane only
        ]

    def test_word_order_comma_and_case_do_not_matter(self):
        assert self._ids("John Doe") == self._ids("doe, JOHN") == self._ids("Doe John")
        assert self._ids("john doe")[0] == "1"

    def test_one_name_part_counts_for_one_word(self):
        # "john john" is one distinct word, so it cannot score 2 on a single John.
        ranked = self._finder().rank_students_by_name("john john")
        assert [(hits, words) for hits, words, _ in ranked] == [(1, 1)]

    def test_middle_initial_with_first_and_last(self):
        ranked = self._finder().rank_students_by_name("doe jane a")
        assert (ranked[0][0], ranked[0][1], ranked[0][2][0]) == (3, 3, "2")

    def test_short_words_are_not_fuzzy(self):
        # "jon" is a prefix of nothing here and too short for a close match to Joan/John.
        assert self._ids("jon") == []

    def test_existing_exact_two_word_search_still_works(self):
        assert self._ids("John Doe")[0] == "1"


@pytest.mark.unit
class TestTerminate:
    """Test terminate method."""
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    @patch('cqc_cpcc.find_student.logger')
    def test_terminate_quits_driver_and_logs(self, mock_logger, mock_get_driver, mock_my_colleges_class):
        """Terminate should quit driver and log message."""
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        mock_mc_instance.get_student_info.return_value = {}
        mock_my_colleges_class.return_value = mock_mc_instance
        
        finder = FindStudents()
        finder.terminate()
        
        assert not finder.is_running()
        mock_driver.quit.assert_called_once()
        mock_logger.debug.assert_called_once_with("Find Students Process Terminated")


@pytest.mark.unit
class TestFindStudentsFromGatheredInfo:
    def test_search_works_without_a_browser(self):
        finder = FindStudents(student_info={"123": ("Adams, Ann", "a@x.edu", "CSC-134")})
        assert finder.driver is None
        assert finder.get_student_by_student_id("123")[0][1] == "Adams, Ann"
        finder.terminate()  # nothing to quit


def _wait_done(job, timeout=5):
    import threading
    gate = threading.Event()
    for _ in range(int(timeout / 0.02)):
        if job.phase != "running":
            return
        gate.wait(0.02)
    raise AssertionError("job still running")


@pytest.mark.unit
class TestFindStudentJob:
    @patch('cqc_cpcc.utilities.selenium_util.unattended_browser_problem', return_value=None)
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_gathers_on_a_thread_with_the_page_mfa_handler(self, get_driver, mc_class, _p):
        from cqc_cpcc.find_student import PHASE_SUCCEEDED, FindStudentJob
        from cqc_cpcc.utilities.utils import current_mfa_handler

        driver = MagicMock()
        get_driver.return_value = (driver, MagicMock())
        seen = {}

        def process(active_only):
            seen["handler"] = current_mfa_handler()
            seen["active_only"] = active_only

        mc_class.return_value.process_student_info.side_effect = process
        mc_class.return_value.get_student_info.return_value = {"1": ("A, B", "e", "C")}
        job = FindStudentJob(active_courses_only=False)
        job.start()
        _wait_done(job)
        assert job.phase == PHASE_SUCCEEDED
        assert seen == {"handler": job.bridge, "active_only": False}
        assert job.finder.get_student_by_student_id("1")
        driver.quit.assert_called_once()
        assert job.latest_progress() == "Found 1 student(s)."

    @patch('cqc_cpcc.utilities.selenium_util.unattended_browser_problem', return_value=None)
    @patch('cqc_cpcc.find_student.get_session_driver', side_effect=RuntimeError("no grid"))
    def test_failure_is_reported(self, _driver, _p):
        from cqc_cpcc.find_student import PHASE_FAILED, FindStudentJob

        job = FindStudentJob()
        job.start()
        _wait_done(job)
        assert job.phase == PHASE_FAILED and "no grid" in job.error

    @patch('cqc_cpcc.utilities.selenium_util.unattended_browser_problem',
           return_value="BROWSER_TYPE is not set.")
    def test_missing_browser_setting_fails_fast_instead_of_prompting(self, _p):
        from cqc_cpcc.find_student import PHASE_FAILED, FindStudentJob

        job = FindStudentJob()
        job.start()
        _wait_done(job)
        assert job.phase == PHASE_FAILED and "BROWSER_TYPE" in job.error

    @patch('cqc_cpcc.utilities.selenium_util.unattended_browser_problem', return_value=None)
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_cancel_reads_as_cancelled(self, get_driver, _p):
        from cqc_cpcc.find_student import PHASE_CANCELLED, FindStudentJob

        job = FindStudentJob()
        get_driver.side_effect = lambda: (job.cancel(), (_ for _ in ()).throw(RuntimeError("x")))
        job.start()
        _wait_done(job)
        assert job.phase == PHASE_CANCELLED
