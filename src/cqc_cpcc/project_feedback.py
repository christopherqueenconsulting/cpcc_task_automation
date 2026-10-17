import datetime as DT
import json
import re
from pathlib import Path
from typing import Optional, Annotated, List, Literal, TypeVar

from cqc_cpcc.utilities.AI import posthog_telemetry as telemetry
from cqc_cpcc.exam_review import JavaCode
from cqc_cpcc.utilities.AI import llm_gateway
from cqc_cpcc.utilities.date import get_datetime
from cqc_cpcc.utilities.env_constants import *
from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.selenium_util import get_session_driver, click_element_wait_retry, \
    get_elements_text_as_list_wait_stale, \
    get_elements_href_as_list_wait_stale
from cqc_cpcc.utilities.utils import are_you_satisfied, ExtendedEnum, CodeError, ErrorHolder, duo_login
from docx import Document
from docx.shared import Pt
from pydantic import BaseModel, Field
from selenium.common import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

COMMENTS_MISSING_STRING = "The code does not include sufficient commenting throughout"
T = TypeVar("T", bound=BaseModel)


def parse_error_type_enum_name(enum_name: str):
    parts = enum_name.split('_')  # Split the string by underscores
    course = parts[0] + " " + parts[1]  # First part is the course
    project = parts[3]  # Second part is the exam
    name = '_'.join(parts[4:])  # Join the remaining parts to get the name
    return course, project, name


class FeedbackType(ExtendedEnum):
    """Enum representing various types of feedback for code."""

    # General feedback for all programming languages
    COMMENTS_MISSING = "The code does not include sufficient commenting throughout"
    SYNTAX_ERROR = "There are syntax errors in the code"
    SPELLING_ERROR = "There are spelling mistakes in the code"
    OUTPUT_ALIGNMENT_ERROR = "There are output alignment issues in the code that will affect grades"
    PROGRAMMING_STYLE = "There are programming style issues that do not adhere to language standards"
    ADDITIONAL_TIPS_PROVIDED = "Additional insights regarding the code and learning"

    # Specific feedback for Java
    JAVA_NAMING_CONVENTION = "Naming conventions are not followed in the Java code"
    JAVA_CONSTANTS_ERROR = "Constants are not properly declared or used in the Java code"
    JAVA_INEFFICIENT_CODE = "The Java code is inefficient and can be optimized"
    JAVA_OUTPUT_FORMATTING = "There are issues with the expected Java code output formatting"
    JAVA_SCANNER_CLASS_ERROR = "There are errors related to the use of the Scanner class in Java"

    # Specific feedback for C++
    CPP_POINTER_ERROR = "There are errors related to the use of pointers in the C++ code"
    CPP_MEMORY_LEAK = "Potential memory leaks detected in the C++ code"
    CPP_SYNTAX_ERROR = "There are syntax errors specific to C++"
    CPP_NAMING_CONVENTION = "Naming conventions are not followed in the C++ code"
    CPP_CONSTANTS_ERROR = "Constants are not properly declared or used in the C++ code"

    # Future entry-level programming feedback
    VARIABLE_NAMING = "Variable names are not descriptive or do not follow naming conventions"
    FUNCTION_NAMING = "Function names are not descriptive or do not follow naming conventions"
    CODE_INDENTATION = "Code indentation is inconsistent or does not follow best practices"
    UNUSED_VARIABLES = "There are variables declared that are not used in the program"
    INCORRECT_DATA_TYPE = "The program has the incorrect data type(s) used"
    DOES_NOT_COMPILE = "The program does not compile"
    LOGIC_ERROR = "There are logical errors in the code that affect the program's functionality"
    MISSING_FUNCTIONALITY = "The code is missing required functionality as per the assignment instructions"


class DefaultFeedbackType(ExtendedEnum):
    """Enum representing various feedback types."""

    #### ------- Below is for JAVA expected submissions ------- #####
    """Checked for comments (indicated by // or /*) throughout the code."""
    CSC_151_PROJECT_ALL_COMMENTS_MISSING = COMMENTS_MISSING_STRING

    """Identified and addressed any syntax errors present."""
    CSC_151_PROJECT_ALL_SYNTAX_ERROR = "There are syntax errors in the code"

    """Scrutinized for spelling mistakes in the code."""
    CSC_151_PROJECT_ALL_SPELLING_ERROR = "There are spelling mistakes in the code"

    """Referenced any Output Alignment differences and emphasized its importance."""
    CSC_151_PROJECT_ALL_OUTPUT_ALIGNMENT_ERROR = "There are output alignment issues in the code that will affect exam grades"

    """Evaluated the programming style for adherence to language standards."""
    CSC_151_PROJECT_ALL_PROGRAMMING_STYLE = "There are programming style issues that do not adhere to java language standards"

    """Pointed out logic errors: wrong conditions, calculations or missing steps that change the program's results."""
    CSC_151_PROJECT_ALL_LOGIC_ERROR = "There are logical errors in the code that affect the program's functionality"

    """Offered additional insights, knowledge, or tips."""
    CSC_151_PROJECT_ALL_ADDITIONAL_TIPS_PROVIDED = "Helpful insights regarding the submission to enhance learning"

    #### ------- Below is for DOCX expected submissions ------- #####
    # """Feedback to reference the expected input"""
    # EXPECTED_INPUTS = "There are missing variables or their corresponding data types in the Input column to ensure proper storage of user-entered values"
    # """Feedback to reference an ideal process"""
    # ALGORITHM_LOGIC = "The Process column does not clearly indicate the connection between the expected inputs from the Input column and desired output from the Output column"
    # """Feedback to reference expected output"""
    # EXPECTED_OUTPUT = "The Output column fails to show the desired outputs or maintain proper formatting for the desired outputs"
    # """Feedback to share additional insight regarding their submission as related to the instructions and example solution"""
    # FINAL_THOUGHTS = "Additional insight regarding possible improvements"


def schema_feedback_types(offered: list) -> list[str]:
    """The offered feedback types as the schema's ``FeedbackType`` values.

    The page offers ``DefaultFeedbackType`` descriptions, but the model can only answer
    with ``FeedbackType`` values, and three descriptions differ ("... affect exam grades"
    vs "... affect grades"). Listing the schema's own wording lets the model pick them
    (prompt project-feedback v2). Types with no schema counterpart pass through unchanged.
    """
    by_value = {d.value: d.name for d in DefaultFeedbackType}
    out = []
    for text in offered or []:
        name = by_value.get(text, "")
        member = FeedbackType.__members__.get(name.split("_ALL_", 1)[-1]) if name else None
        out.append(member.value if member else text)
    return list(dict.fromkeys(out))


FEEDBACK_GUIDANCE_TEMPLATES_PATH = Path(__file__).parent / "config" / "feedback_guidance_templates.json"


def load_feedback_guidance_templates(path: Path = FEEDBACK_GUIDANCE_TEMPLATES_PATH) -> list[dict]:
    """Templates that pre-fill the Give Feedback page's "Extra prompt guidance" box.

    Each entry is ``{"id", "label", "text"}``. Edit the JSON file to add or change one.
    A missing or malformed file returns ``[]`` so the page still works without templates.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning("Feedback guidance templates not loaded: %s", e)
        return []
    entries = data.get("templates", []) if isinstance(data, dict) else []
    return [t for t in entries
            if isinstance(t, dict) and str(t.get("label", "")).strip() and str(t.get("text", "")).strip()]


class Feedback(CodeError):
    """Class representing various types of feedback for coding."""
    # DefaultFeedbackType,
    error_type: Annotated[
        FeedbackType,
        Field(description="The type of feedback for code.")
    ]


class FeedbackGuide(ErrorHolder):
    """Class representing feedback after reviewing a student's submission"""
    all_feedback: Optional[List[Feedback]] = Field(description="List of all the feedback for the submission.")

    def get_feedback_unique(self) -> None | List[Feedback]:
        feedback_errors = None
        if self.all_feedback is not None:
            feedback_errors = self.get_combined_errors_by_type(self.all_feedback)
        return feedback_errors


# ---------------------------------------------------------------------------
# Give Feedback v2 (prompt project-feedback v5): course-aware, student-facing feedback.
# ---------------------------------------------------------------------------

GENERAL_CATEGORY_ID = "GENERAL"
REQUIREMENTS_CATEGORY_ID = "REQUIREMENTS"
LEGACY_GENERAL_CATEGORY_ID = "ADDITIONAL_TIPS_PROVIDED"

SEVERITY_LABELS = {
    "major": "Would cost major points on a graded submission",
    "minor": "Would cost minor points on a graded submission",
    "suggestion": "Suggestion (would not cost points)",
}


class FeedbackCategory(BaseModel):
    """A kind of problem the feedback may report, offered to the model by id."""
    id: str
    name: str
    description: str
    severity: str = "minor"


class FeedbackItem(BaseModel):
    """One problem in the submission, written so the student can act on it."""
    category_id: Annotated[str, Field(description="Exactly one id from the Feedback Categories list.")]
    title: Annotated[str, Field(description="Short, specific title for this problem.")]
    would_lose_points: Annotated[
        Literal["major", "minor", "suggestion"],
        Field(description="How a graded submission would be marked down for this: major, minor, "
                          "or suggestion (no points). Follow the category's severity.")]
    what_we_found: Annotated[str, Field(
        description="What is wrong and where (method, line or behavior), in this student's own code.")]
    why_it_matters: Annotated[str, Field(
        description="What goes wrong because of it and why a graded submission would lose points.")]
    how_to_fix: Annotated[str, Field(
        description="The direction and steps to fix it, as hints. Never the corrected code.")]
    how_to_approach: Annotated[str, Field(
        description="A strategy to find, test or debug this kind of problem.")]
    learn_more: Annotated[str, Field(
        description="The concept to review: textbook topic, course module, or official documentation "
                    "page name. No URLs.")]
    code_error_lines: Optional[List[str]] = Field(
        default=None, description="Optional: short snippets (first 25 characters) of the relevant lines.")
    line_numbers: Optional[List[int]] = Field(
        default=None, description="Leave empty; line numbers are filled in after the review.")


class FeedbackReport(BaseModel):
    """Structured, non-graded feedback on one submission."""
    summary: Annotated[str, Field(
        description="2-4 sentences: the overall state of the submission and what already works.")]
    items: Annotated[List[FeedbackItem], Field(
        description="One entry per distinct problem, most important first. Empty if none.")]
    next_steps: Annotated[List[str], Field(
        description="3-6 ordered, concrete steps the student should take next.")]


FILE_HEADER_PATTERN = re.compile(r"^\s*// File: ")


def category_slug(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_") or GENERAL_CATEGORY_ID


def _always_on_categories() -> list[FeedbackCategory]:
    return [
        FeedbackCategory(id=REQUIREMENTS_CATEGORY_ID, name="Requirements Not Met", severity="major",
                         description="A required feature, input or output behavior from the "
                                     "assignment instructions is missing or wrong"),
        FeedbackCategory(id=GENERAL_CATEGORY_ID, name="General Advice", severity="suggestion",
                         description="Other advice that would improve the submission or the "
                                     "student's understanding"),
    ]


def _categories_from_definitions(definitions) -> list[FeedbackCategory]:
    out: dict[str, FeedbackCategory] = {}
    for d in definitions:
        if not getattr(d, "enabled", True):
            continue
        cid = category_slug(d.name)
        out.setdefault(cid, FeedbackCategory(id=cid, name=d.name, description=d.description,
                                             severity=d.severity_category or "minor"))
    for always in _always_on_categories():
        out.setdefault(always.id, always)
    return list(out.values())


def feedback_category_sources(course_name: Optional[str]) -> list[tuple[Optional[str], str]]:
    """``(assignment_id, label)`` choices for the page: the course's registry assignments
    (Grade Assignment's error definitions), then the general intro set (``None``)."""
    from cqc_cpcc.course_identifier import normalize_course_id
    from cqc_cpcc.error_definitions_config import get_assignments_for_course

    sources: list[tuple[Optional[str], str]] = []
    if course_name:
        try:
            sources = [(a.assignment_id, a.assignment_name)
                       for a in get_assignments_for_course(normalize_course_id(course_name))]
        except Exception as e:  # noqa: BLE001 - a missing registry leaves the general set
            logger.warning("Error definitions not loaded for feedback categories: %s", e)
    return sources + [(None, "General intro set")]


def feedback_categories_for(course_name: Optional[str], assignment_id: Optional[str] = None
                            ) -> list[FeedbackCategory]:
    """Feedback categories built from Grade Assignment's error definitions.

    With an ``assignment_id`` the course's definitions for that assignment are used. Without
    one, the general intro set: definitions that appear in at least two courses (so
    course-specific ones such as Scanner or ArrayList are left out). ``REQUIREMENTS`` and
    ``GENERAL`` are always added.
    """
    from cqc_cpcc.course_identifier import normalize_course_id
    from cqc_cpcc.error_definitions_config import get_error_definitions, load_error_config_registry

    try:
        registry = load_error_config_registry()
    except Exception as e:  # noqa: BLE001 - the always-on categories still work
        logger.warning("Error definitions not loaded for feedback categories: %s", e)
        return _always_on_categories()

    if course_name and assignment_id:
        definitions = get_error_definitions(normalize_course_id(course_name), assignment_id, registry)
        if definitions:
            return _categories_from_definitions(definitions)

    courses_by_name: dict[str, set] = {}
    first_by_name: dict[str, object] = {}
    for course in registry.courses:
        for assignment in course.assignments:
            for d in assignment.error_definitions:
                courses_by_name.setdefault(d.name, set()).add(normalize_course_id(course.course_id))
                first_by_name.setdefault(d.name, d)
    shared = [first_by_name[n] for n, courses in courses_by_name.items() if len(courses) >= 2]
    return _categories_from_definitions(shared)


def legacy_feedback_categories(feedback_type_list: list) -> list[FeedbackCategory]:
    """Categories for the old feedback-type strings (eval dataset ``project-feedback/v1``).

    Each id is the ``FeedbackType`` member name, so the dataset's labels still match.
    """
    by_value = {t.value: t.name for t in FeedbackType}
    out: dict[str, FeedbackCategory] = {}
    for text in schema_feedback_types(feedback_type_list):
        cid = by_value.get(text) or category_slug(text)
        out.setdefault(cid, FeedbackCategory(id=cid, name=cid.replace("_", " ").title(), description=text))
    return list(out.values())


def _is_documentation_category(category_id: str) -> bool:
    return any(w in category_id for w in ("DOCUMENTATION", "COMMENT"))


def init_page(driver: WebDriver, wait: WebDriverWait) -> str:
    driver.get(BRIGHTSPACE_URL)
    # Wait for title to change
    wait.until(EC.title_is("Web Login Service"))

    original_window = driver.current_window_handle

    # Login
    duo_login(driver)

    # Wait for title to change
    wait.until(EC.title_is(BRIGHTSPACE_HOMEPAGE_TITLE))

    # Return the original_window
    return original_window


@telemetry.tracked_run("project_feedback")
def give_project_feedback():
    driver, wait = get_session_driver()

    # original_window = init_page(driver, wait)

    # Find All Courses in Brightspace
    course_urls = get_course_urls(driver, wait)
    telemetry.update_run(courses=len(course_urls))
    logger.info("Course Urls:\n%s" % "\n".join(course_urls))

    # Process submissions for each course
    for url in course_urls:
        process_submissions_from_course_url(driver, wait, url)

    logger.info("Feedback Complete")


def get_course_urls(driver: WebDriver, wait: WebDriverWait) -> list[str]:
    # Click on the course menu
    click_element_wait_retry(driver, wait, "d2l-navigation-s-course-menu", "Waiting for Course Menu", By.CLASS_NAME)

    # Get the course url
    course_links = wait.until(
        lambda d: d.find_elements(By.XPATH,
                                  "//div[contains(@class,'d2l-course-selector-item')]/a[contains(@class,'d2l-link') and not(contains(text(),'Sandbox'))]"),
        "Waiting for Course Links")
    course_urls = map(lambda b: b.get_attribute("href"), course_links)

    return list(course_urls)


def process_submissions_from_course_url(driver: WebDriver, wait: WebDriverWait, url: str):
    handles = driver.window_handles

    # Opens a new tab and switches to new tab
    driver.switch_to.new_window('tab')

    # Wait for the new window or tab
    wait.until(EC.new_window_is_opened(handles))

    # Keep track of current tab
    # current_tab = driver.current_window_handle

    # Navigate to course url
    driver.get(url)

    # Navigate to Course Tools -> Assignments
    assignments_link = wait.until(
        lambda d: d.find_element(By.XPATH,
                                 "//d2l-menu-item-link[@text='Assignments' and contains(@class,'d2l-navigation-s-menu-item')]"),
        'Waiting for Assignments link')
    assignments_url = BRIGHTSPACE_URL + assignments_link.get_attribute('href')
    logger.info("Assignments URL: %s", assignments_url)

    driver.get(assignments_url)

    # Find Projects with Past Due Dates
    # Criteria - Name contains "Project", Has Submissions
    try:
        due_dates = get_elements_text_as_list_wait_stale(driver, wait,
                                                         "//tr[descendant::a[contains(@title,'Project')] and descendant::a[contains(@class,'d2l-link') and contains(@title,'new')]]//*[contains(text(),'Due')]",
                                                         "Waiting on Due Dates")
    except TimeoutException as te:
        logger.info("No projects with new submissions. Returning...")
        return

    due_dates = list(map(lambda b: b.replace("Due on ", ""), due_dates))
    logger.info("Due Dates:\n%s" % "\n".join(due_dates))

    submission_links = get_elements_href_as_list_wait_stale(driver, wait,
                                                            "//tr[descendant::a[contains(@title,'Project')] and descendant::a[contains(@class,'d2l-link') and contains(@title,'new')]]//a[contains(@class,'d2l-link') and contains(@title,'new')]",
                                                            "Waiting on submission links")

    logger.info("Submission Links:\n%s" % "\n".join(submission_links))

    due_date_submission_links_dict = dict(zip(due_dates, submission_links))
    logger.info("Due Date Submission Links:\n%s" % str(due_date_submission_links_dict))

    shrt_frmt = "%m/%d"
    today = DT.date.today()
    today_string = today.strftime(shrt_frmt)
    yesterday = today - DT.timedelta(days=1)
    # yesterday_string = yesterday.strftime(shrt_frmt)
    week_ago = yesterday - DT.timedelta(days=7)
    week_ago_string = week_ago.strftime(shrt_frmt)

    for due_date in due_date_submission_links_dict:
        proper_date = get_datetime(due_date)
        proper_date_str = proper_date.strftime(shrt_frmt)
        if week_ago <= proper_date.date() <= today:
            logger.info("%s IS between %s - %s" % (proper_date_str, week_ago_string, today_string))
            submission_link = due_date_submission_links_dict[due_date]
            logger.info("Submission Link: %s" % submission_link)

            process_submission_from_link(driver, wait, submission_link)
        else:
            logger.info("%s is NOT between %s - %s" % (proper_date_str, week_ago_string, today_string))

    # TODO: Give feedback to missing submissions

    are_you_satisfied()
    # Close the tab
    driver.close()


def process_submission_from_link(driver: WebDriver, wait: WebDriverWait, url: str):
    handles = driver.window_handles

    # Opens a new tab and switches to new tab
    driver.switch_to.new_window('tab')

    # Wait for the new window or tab
    wait.until(EC.new_window_is_opened(handles))

    # Keep track of current tab
    # current_tab = driver.current_window_handle

    # Navigate to course url
    driver.get(url)

    # Loop until header numbers match i.e 17 - 17

    # Download and parse the .Java Files

    # TODO: Leave feedback

    # TODO: Add Feedback signature to end of feedback
    # FEEDBACK_SIGNATURE

    # Save as Draft !!!

    are_you_satisfied()
    # Close the tab
    driver.close()


class FeedbackGiver:
    # Configuration for OpenAI structured completion
    model_name: str = None
    temperature: float = 0.2
    course_name: str = None
    assignment_instructions: str = None
    assignment_solution: str = None
    feedback_type_list: list = None

    def __init__(self,
                 course_name: str,
                 assignment_instructions: str,
                 assignment_solution: str,
                 feedback_llm: str = None,
                 feedback_type_list: list = None,
                 temperature: float = 0.2,
                 extra_guidance: str = "",
                 categories: Optional[List[FeedbackCategory]] = None,
                 ):
        # Store configuration for generating feedback
        # None means the registry's "feedback" role decides the model.
        self.model_name = feedback_llm or None
        self.temperature = temperature
        self.course_name = course_name
        self.assignment_instructions = assignment_instructions
        self.assignment_solution = assignment_solution
        self.feedback_type_list = feedback_type_list if feedback_type_list else []
        self.extra_guidance = (extra_guidance or "").strip()
        # Categories win; the old feedback-type strings still work (eval dataset v1).
        if categories:
            self.categories = list(categories)
        elif self.feedback_type_list:
            self.categories = legacy_feedback_categories(self.feedback_type_list)
        else:
            self.categories = feedback_categories_for(course_name)

    @property
    def general_category_id(self) -> str:
        ids = [c.id for c in self.categories]
        for cid in (GENERAL_CATEGORY_ID, LEGACY_GENERAL_CATEGORY_ID):
            if cid in ids:
                return cid
        return ids[-1] if ids else GENERAL_CATEGORY_ID

    def category_name(self, category_id: str) -> str:
        return next((c.name for c in self.categories if c.id == category_id), category_id)

    def build_prompt(self, student_submission: str) -> str:
        """The exact prompt ``generate_feedback`` sends for this submission."""
        from cqc_cpcc.prompts.project_feedback import CODE_ASSIGNMENT_FEEDBACK_PROMPT_OPENAI

        categories = "\n".join(f"- {c.id} | {c.name} | {c.severity} | {c.description}"
                               for c in self.categories) or "N/A"
        return CODE_ASSIGNMENT_FEEDBACK_PROMPT_OPENAI.format(
            course_name=self.course_name,
            assignment=self.assignment_instructions,
            solution=self.assignment_solution,
            submission=student_submission,
            feedback_categories=categories,
            extra_guidance=self.extra_guidance or "None",
        )

    async def generate_feedback(self, student_submission: str, callback=None) -> FeedbackReport:
        """Generate feedback using OpenAI structured completion.

        Args:
            student_submission: The student's code or submission text
            callback: (Deprecated) Callback handler for compatibility, not used

        Returns:
            The feedback report for this submission. Nothing is stored on the instance,
            so one giver can serve concurrent students without their feedback crossing.
        """
        report = await llm_gateway.structured(
            role="feedback",
            prompt=self.build_prompt(student_submission),
            schema_model=FeedbackReport,
            override=self.model_name,
            prompt_id="project-feedback",
        )
        return self._postprocess(report, student_submission)

    def _postprocess(self, report: FeedbackReport, student_submission: str) -> FeedbackReport:
        """Deterministic clean-up: valid category ids, real line numbers, comment check."""
        valid = {c.id for c in self.categories}
        # The page prefixes each file with a "// File: name" header; it is not the student's
        # comment and must not count toward documentation or shift line numbers.
        code = "\n".join(line for line in student_submission.splitlines()
                         if not FILE_HEADER_PATTERN.match(line))
        jc = JavaCode(entire_raw_code=code)
        items = []
        for item in report.items:
            if item.category_id not in valid:
                item.category_id = self.general_category_id
            line_numbers = []
            for code_line in item.code_error_lines or []:
                line_numbers.extend(jc.get_line_number(code_line) or [])
            item.line_numbers = sorted(set(line_numbers)) or None
            items.append(item)

        # The comment count is checked in code, not trusted to the model.
        doc_ids = [cid for cid in valid if _is_documentation_category(cid)]
        if doc_ids:
            if jc.sufficient_amount_of_comments:
                items = [i for i in items if not _is_documentation_category(i.category_id)]
            elif not any(_is_documentation_category(i.category_id) for i in items):
                items.insert(0, self._missing_comments_item(doc_ids[0]))
        report.items = items
        return report

    def _missing_comments_item(self, category_id: str) -> FeedbackItem:
        severity = next((c.severity for c in self.categories if c.id == category_id), "minor")
        return FeedbackItem(
            category_id=category_id,
            title="Not enough comments in the code",
            would_lose_points=severity if severity in SEVERITY_LABELS else "minor",
            what_we_found="The code has too few comments to explain its purpose, its main steps "
                          "and what each method or block does.",
            why_it_matters="Comments are part of the documentation requirement. Without them a "
                           "reader cannot follow your logic, and documentation is graded.",
            how_to_fix="Add a header comment (program purpose, author, date). Then add a short "
                       "comment above each method and each main block (input, processing, "
                       "output) that says what it does and why.",
            how_to_approach="Read your code top to bottom as if you were a classmate. Wherever "
                            "you would have to stop and think, add a comment that answers that "
                            "question.",
            learn_more="Your textbook or course module on comments, documentation and "
                       "programming style.",
        )

    @staticmethod
    def set_document_style(document: Document):
        for style_name, size in (("Heading 2", 18), ("Heading 3", 15)):
            font = document.styles[style_name].font
            font.name = 'Lato'
            font.size = Pt(size)

    def save_feedback_to_docx(self, file_path: str, report: FeedbackReport):
        """Write the report as a student-facing .docx."""
        document = Document()
        self.set_document_style(document)

        document.add_heading("Summary", 2)
        document.add_paragraph(report.summary)

        document.add_heading("Feedback", 2)
        if not report.items:
            document.add_paragraph("No problems found. See the next steps to take your work further.")
        for number, item in enumerate(report.items, 1):
            document.add_heading(f"{number}. {item.title}", 3)
            meta = f"{self.category_name(item.category_id)} · " \
                   f"{SEVERITY_LABELS.get(item.would_lose_points, item.would_lose_points)}"
            if item.line_numbers:
                meta += " · Line(s) " + ", ".join(map(str, item.line_numbers))
            document.add_paragraph().add_run(meta).italic = True
            for label, text in (("What we found", item.what_we_found),
                                ("Why it matters", item.why_it_matters),
                                ("How to fix it", item.how_to_fix),
                                ("How to approach it", item.how_to_approach),
                                ("Learn more", item.learn_more)):
                if text and text.strip():
                    paragraph = document.add_paragraph()
                    paragraph.add_run(label + ": ").bold = True
                    paragraph.add_run(text.strip())

        if report.next_steps:
            document.add_heading("Next steps", 2)
            for step in report.next_steps:
                document.add_paragraph(step, style="List Number")

        document.save(file_path)
        logger.info("Feedback saved to: %s", file_path)
