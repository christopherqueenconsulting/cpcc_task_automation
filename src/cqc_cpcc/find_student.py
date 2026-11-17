#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import difflib
import re
import threading
import unicodedata

from cqc_cpcc.my_colleges import MyColleges
from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.selenium_util import get_session_driver

PHASE_RUNNING = "running"
PHASE_SUCCEEDED = "succeeded"
PHASE_FAILED = "failed"
PHASE_CANCELLED = "cancelled"
FINISHED_PHASES = (PHASE_SUCCEEDED, PHASE_FAILED, PHASE_CANCELLED)


# --- Name matching -----------------------------------------------------------------
# A search word matches a name part when it is the whole part, the start of it (so "J" or
# "Jo" find John, and a middle initial finds a middle name), or, for words of 4+ letters,
# close to it (one letter off: "Smyth" finds Smith). Better matches rank higher.
_SCORE_EXACT = 3.0
_SCORE_PREFIX = 2.0
_SCORE_FUZZY = 1.0
_FUZZY_MIN_LEN = 4
_FUZZY_RATIO = 0.8


def _fold_char(ch: str) -> str:
    """Drop the accent of a Latin letter (e -> e); keep every other character as it is."""
    base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
    return base if base and base.isascii() else ch


def name_tokens(text: str) -> list[str]:
    """Lower-case, accent-free parts of a name or query: "Doe, Jo-Ann M." -> doe, jo, ann, m.

    Letters of any script are kept (Cyrillic, Greek, CJK, o-slash, l-stroke), so those names are
    found by themselves; only Latin accents are folded away.
    """
    folded = "".join(_fold_char(ch) for ch in (text or "")).casefold()
    folded = re.sub(r"['\u2019.]", "", folded)  # O'Brien -> obrien, "M." -> m
    tokens: list[str] = []
    current: list[str] = []
    for ch in folded:
        if unicodedata.category(ch)[0] in "LNM":  # letters, numbers and their combining marks
            current.append(ch)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def _word_score(word: str, token: str) -> float:
    if word == token:
        return _SCORE_EXACT
    if token.startswith(word):
        return _SCORE_PREFIX
    if len(word) >= _FUZZY_MIN_LEN and len(token) >= _FUZZY_MIN_LEN - 1 \
            and difflib.SequenceMatcher(None, word, token).ratio() >= _FUZZY_RATIO:
        return _SCORE_FUZZY
    return 0.0


def match_name_words(words: list[str], tokens: list[str]) -> tuple[int, float]:
    """How many search words find a part of the name, and how well (hits, quality).

    Each name part counts for one word only, so "john john" does not count one John twice.
    """
    pairs = sorted(
        ((_word_score(w, t), wi, ti) for wi, w in enumerate(words) for ti, t in enumerate(tokens)),
        key=lambda p: -p[0])
    used_words: set[int] = set()
    used_tokens: set[int] = set()
    hits, quality = 0, 0.0
    for score, wi, ti in pairs:
        if score <= 0 or wi in used_words or ti in used_tokens:
            continue
        used_words.add(wi)
        used_tokens.add(ti)
        hits += 1
        quality += score
    return hits, quality


class FindStudents:
    def __init__(self, active_courses_only: bool = True, student_info: dict | None = None):
        """Gather the roster from MyColleges, or search an already gathered one."""
        self._running = True
        self.driver = self.wait = None
        if student_info is None:
            self.driver, self.wait = get_session_driver()
            mc = MyColleges(self.driver, self.wait)
            mc.process_student_info(active_courses_only)
            student_info = mc.get_student_info()
        self.student_info = student_info
        self._running = False

    def is_running(self):
        return self._running

    def get_student_info_items(self):
        return self.student_info.items()

    def get_student_by_email(self, email: str):
        found_students = []

        #  Search through the student_info list for the student with the matching email
        for student_id, (student_name, student_email, course_name) in self.get_student_info_items():
            if student_email == email:
                # append the student info back to found students list
                found_students.append((student_id, student_name, student_email, course_name))

        return found_students

    def get_student_by_student_id(self, id: str):
        found_students = []

        #  Search through the student_info list for the student with the matching student_id
        for student_id, (student_name, student_email, course_name) in self.get_student_info_items():

            if student_id == id:
                # append the student info back to found students list
                found_students.append((student_id, student_name, student_email, course_name))

        return found_students

    def rank_students_by_name(self, searchfor_student_name: str) -> list[tuple[int, int, tuple]]:
        """Students whose name has at least one of the searched words, best first.

        Returns ``(hits, words, student)`` where ``student`` is ``(id, name, email, course)``,
        ``hits`` is how many of the ``words`` searched were found in the name. Sorted by hits,
        then match quality, then name.
        """
        words = list(dict.fromkeys(name_tokens(searchfor_student_name)))
        if not words:
            return []
        ranked = []
        for student_id, (student_name, student_email, course_name) in self.get_student_info_items():
            hits, quality = match_name_words(words, name_tokens(student_name))
            if hits:
                ranked.append((hits, quality, student_name or "",
                               (student_id, student_name, student_email, course_name)))
        ranked.sort(key=lambda r: (-r[0], -r[1], r[2].casefold()))
        return [(hits, len(words), student) for hits, _quality, _name, student in ranked]

    def get_student_by_name(self, searchfor_student_name: str):
        """Students matching any searched word (first, middle, last name or initial), best first."""
        return [student for _hits, _words, student in self.rank_students_by_name(searchfor_student_name)]

    def terminate(self):
        self._running = False
        if self.driver is not None:
            self.driver.quit()
        logger.debug("Find Students Process Terminated")


class FindStudentJob:
    """Gather every student of the instructor's courses on a background thread.

    The page polls it, so the sign-in's Authenticator number (published to the
    ``MfaBridge`` through ``mfa_handler_scope``) shows on the page while the run
    waits for approval. The thread never touches Streamlit.
    """

    def __init__(self, active_courses_only: bool = True, bridge=None):
        if bridge is None:
            from cqc_cpcc.utilities.selenium_util import MfaBridge
            bridge = MfaBridge()
        self.active_courses_only = active_courses_only
        self.bridge = bridge
        self.finder: FindStudents | None = None
        self.error: str | None = None
        self._phase = PHASE_RUNNING
        self._progress: list[str] = []
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="find-students")

    def start(self) -> None:
        self._thread.start()

    def cancel(self) -> None:
        self._cancelled.set()
        self.bridge.cancel()

    @property
    def phase(self) -> str:
        with self._lock:
            return self._phase

    def latest_progress(self) -> str | None:
        with self._lock:
            return self._progress[-1] if self._progress else None

    # Browser observer (see ``selenium_util.browser_observer_scope``).
    def on_progress(self, message: str) -> None:
        with self._lock:
            self._progress.append(message)

    def on_wait_retry(self, driver, wait_text: str) -> None:
        self.on_progress("Still %s. Retrying..." % (wait_text.lower() if wait_text else "waiting"))

    def _set_phase(self, phase: str) -> None:
        with self._lock:
            self._phase = phase

    def _run(self) -> None:
        from cqc_cpcc.utilities.selenium_util import (
            browser_observer_scope,
            unattended_browser_problem,
        )
        from cqc_cpcc.utilities.utils import MfaCancelled, mfa_handler_scope

        try:
            # No console on this thread: a browser-choice prompt would hang it.
            problem = unattended_browser_problem()
            if problem:
                raise RuntimeError(problem + " Add it to .env (or the app's secrets).")
            with mfa_handler_scope(self.bridge), browser_observer_scope(self):
                self.on_progress("Signing in to MyColleges and reading your course rosters...")
                driver, wait = get_session_driver()
                try:
                    mc = MyColleges(driver, wait)
                    mc.process_student_info(self.active_courses_only)
                    info = mc.get_student_info()
                finally:
                    try:
                        driver.quit()
                    except Exception:  # noqa: BLE001
                        logger.debug("Driver quit failed.", exc_info=True)
            self.finder = FindStudents(student_info=info)
            self.on_progress("Found %d student(s)." % len(info))
            self._set_phase(PHASE_SUCCEEDED)
        except Exception as error:  # noqa: BLE001 - surfaced to the page
            if isinstance(error, MfaCancelled) or self._cancelled.is_set():
                self._set_phase(PHASE_CANCELLED)
            else:
                self.error = "%s: %s" % (type(error).__name__, error)
                logger.exception("Find Student run failed")
                self._set_phase(PHASE_FAILED)
