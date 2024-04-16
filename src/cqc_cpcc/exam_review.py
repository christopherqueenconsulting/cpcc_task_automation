from typing import List, Optional, Annotated

from docx import Document
from docx.shared import Pt
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic.v1 import Field, BaseModel, StrictStr

from cqc_cpcc.utilities.AI.llm.chains import get_exam_error_definition_from_completion_chain, \
    get_exam_error_definitions_completion_chain
from cqc_cpcc.utilities.utils import ExtendedEnum, CodeError, ErrorHolder, merge_lists

# model = 'gpt-3.5-turbo-1106'
# model = 'gpt-4-1106-preview'
model = 'gpt-3.5-turbo-16k-0613'
# model = "gpt-4"
temperature = .2  # .2 <- More deterministic | More Creative -> .8
default_llm = ChatOpenAI(temperature=temperature, model=model)


class MajorErrorType(ExtendedEnum):
    """Enum representing various types of coding issues."""

    """No documentation or insufficient documentation (comments)."""
    INSUFFICIENT_DOCUMENTATION = "No documentation or insufficient amount of comments in the code"

    """Errors in coding sequence and selection."""
    # SEQUENCE_AND_SELECTION_ERROR = "Errors in the sequence and selection structures in the code"
    SEQUENCE_AND_SELECTION_ERROR_V2 = "Errors in the coding sequence, selection and looping including incorrect use of comparison operators"

    # METHOD_ERRORS = """Method errors in the code such as:
    # - passing the incorrect number of arguments
    # - incorrect data types for arguments and parameter variables
    # - failing to include the data type of parameter variables in the method header"""

    """Errors that adversely impact the output (calculation errors, omissions, etc.)."""
    OUTPUT_IMPACT_ERROR = "Errors that adversely impact the expected output, such as calculation errors or omissions"


class MinorErrorType(ExtendedEnum):
    """Enum representing various types of minor coding errors."""

    """Syntax error in the code."""
    SYNTAX_ERROR = "There are syntax errors in the code"

    """Violation of naming conventions."""
    NAMING_CONVENTION = "Naming conventions are not followed"

    """Failure to declare and use constants."""
    CONSTANTS_ERROR = "Constants are not properly declared or used"

    """Inefficient code practices."""
    INEFFICIENT_CODE = "The code is inefficient and can be optimized"

    """Issues with the code output formatting."""
    OUTPUT_FORMATTING = "There are issues with the expected code output formatting (spacing, decimal places, etc.)"

    """Programming style issues (indentation, white space, etc.)."""
    PROGRAMMING_STYLE = "There are programming style issues that do not adhere to language standards (indentation, white space, etc.)"

    """Error related to the use of the Scanner class."""
    SCANNER_CLASS = "There are errors related to the use of the Scanner class"


class MinorError(CodeError):
    """Class representing various types of minor coding errors."""
    error_type: Annotated[
        MinorErrorType,
        Field(description="The type of minor coding error.")
    ]


class MajorError(CodeError):
    """Class representing various types of major coding errors."""
    error_type: Annotated[
        MajorErrorType,
        Field(description="The type of major coding error.")
    ]


class JavaCode(BaseModel):
    """Class representing java code with functions to reference by line numbers"""

    entire_raw_code: StrictStr = Field(description="This raw string containing the entire Java code program")

    def get_line_number(self, line_of_code: StrictStr) -> List:
        line_number = None

        line_of_code = line_of_code.strip()

        # Escape the newlines characters
        # escaped_code = self.raw_code.encode('unicode_escape').decode('utf-8')
        # escaped_code = self.raw_code.replace("\n","\\\\n")
        escaped_code = self.entire_raw_code.strip()

        # print(escaped_code)

        # Split by remaining new lines
        code_lines = self.code_lines

        # print("Line count: %s" % str(len(code_lines)))

        # find line in text
        # if line_of_code in split_code:
        line_numbers = [code_lines.index(x) + 1 for x in code_lines if line_of_code in x]

        # print("Lines containing code: %s" % str(len(line_numbers)))

        if line_numbers is None:
            line_numbers = []

        # Return the line numbers
        return line_numbers

    @property
    def comments_count(self) -> int:
        return self.entire_raw_code.count("//") + self.entire_raw_code.count("/*")

    @property
    def code_lines(self) -> list[str]:
        return self.entire_raw_code.splitlines(keepends=True)

    @property
    def code_lines_count(self) -> int:
        return len(self.code_lines)

    @property
    def sufficient_amount_of_comments(self) -> bool:
        return (self.comments_count / self.code_lines_count) > .01


class ErrorDefinitions(ErrorHolder):
    """Class representing major and minor errors identified after doing a code analysis"""

    all_major_errors: Optional[List[MajorError]] = Field(description="List of the major code errors found in the code.")
    all_minor_errors: Optional[List[MinorError]] = Field(description="List of minor code errors found in the code.")

    def get_minor_errors_unique(self) -> None | List[MinorError]:
        minor_errors = None
        if self.all_minor_errors is not None:
            minor_errors = self.get_combined_errors_by_type(self.all_minor_errors)
        return minor_errors

    def get_major_errors_unique(self) -> None | List[MajorError]:
        major_errors = None
        if self.all_major_errors is not None:
            major_errors = self.get_combined_errors_by_type(self.all_major_errors)
        return major_errors


class CodeGrader:
    max_points: int
    major_errors: List[MajorError] = None
    minor_errors: List[MinorError] = None
    deduction_per_major_error: int
    deduction_per_minor_error: int
    error_definitions_completion_chain = None
    error_definitions_parser = None
    error_definitions_prompt = None

    def __init__(self, max_points: int, exam_instructions: str, exam_solution: str, deduction_per_major_error: int = 20,
                 deduction_per_minor_error: int = 5, wrap_code_in_markdown: bool = True, grader_llm: BaseChatModel = default_llm):
        self.max_points = max_points
        self.deduction_per_major_error = deduction_per_major_error
        self.deduction_per_minor_error = deduction_per_minor_error
        self.error_definitions_completion_chain, self.error_definitions_parser, self.error_definitions_prompt = get_exam_error_definitions_completion_chain(
            llm=grader_llm,
            pydantic_object=ErrorDefinitions,
            major_error_type_list=MajorErrorType.list(),
            minor_error_type_list=MinorErrorType.list(),
            exam_instructions=exam_instructions,
            exam_solution=exam_solution,
            wrap_code_in_markdown=wrap_code_in_markdown
        )

    @property
    def major_deduction_total(self):
        return len(self.major_errors) * self.deduction_per_major_error

    @property
    def minor_deduction_total(self):
        return len(self.minor_errors) * self.deduction_per_minor_error

    @property
    def total_deduction(self):
        return self.major_deduction_total + self.minor_deduction_total

    @property
    def points(self) -> int:
        # Ensure the calculated points are non-negative
        return max(self.max_points - self.total_deduction, 0)

    @property
    def final_score_text(self) -> str:
        return "Final Score: " + str(self.points) + " / " + str(self.max_points)

    @property
    def major_code_deduction_points_text(self) -> str:
        return "Major Code Errors: (-" + str(self.major_deduction_total) + " points)"

    @property
    def minor_code_deduction_points_text(self) -> str:
        return "Minor Code Errors: (-" + str(self.minor_deduction_total) + " points)"

    def get_text_feedback(self) -> str:
        grade_feedback = ""
        if self.major_errors is not None:
            grade_feedback += "\n" + self.major_code_deduction_points_text
            for error in self.major_errors:
                grade_feedback += "\n\n\t" + str(error).replace("\n", "\n\t")

        if self.minor_errors is not None:
            grade_feedback += "\n\n" + self.minor_code_deduction_points_text
            for error in self.minor_errors:
                grade_feedback += "\n\n\t" + str(error).replace("\n", "\n\t")

        grade_feedback += "\n\n" + self.final_score_text
        return grade_feedback

    def grade_submission(self, student_submission: str):
        #print("Identifying Errors")
        error_definitions_from_llm = get_exam_error_definition_from_completion_chain(
            student_submission=student_submission,
            completion_chain=self.error_definitions_completion_chain,
            parser=self.error_definitions_parser,
            prompt=self.error_definitions_prompt

        )
        #print("Errors Identified")

        # print("\n\nFinal Output:")
        # pprint(finalOutput)

        error_definitions = ErrorDefinitions.parse_obj(error_definitions_from_llm)

        #print("\n\nCompiling Unique Errors")
        unique_major_errors = error_definitions.get_major_errors_unique()
        unique_minor_errors = error_definitions.get_minor_errors_unique()

        # Set major and minor errors to empty list if set to None
        if unique_major_errors is None:
            unique_major_errors = []
        if unique_minor_errors is None:
            unique_minor_errors = []

        #print("\n\nFinding Correct Error Line Numbers")
        jc = JavaCode(entire_raw_code=student_submission)

        # Remove or add Sufficient Commenting Error
        insufficient_comment_error = MajorError(error_type=MajorErrorType.INSUFFICIENT_DOCUMENTATION,
                                                error_details="There is not enough comments to help others understand the purpose, functionality, and structure of the code.")

        if jc.sufficient_amount_of_comments:
            #print("Found Insufficient comments error by LLM but not true so removing")
            # Sufficient comments so remove this error type
            unique_major_errors = [x for x in unique_major_errors if
                                   x.error_type != MajorErrorType.INSUFFICIENT_DOCUMENTATION]
        elif MajorErrorType.INSUFFICIENT_DOCUMENTATION not in [x.error_type for x in unique_major_errors]:
            #print("Did not find Insufficient comments error by LLM but true so adding it")
            # Insufficient comments but error type doesnt exist
            unique_major_errors.insert(0, insufficient_comment_error)

        # Add the line numbers for where errors occur
        all_errors = merge_lists(unique_major_errors, unique_minor_errors)
        if all_errors is not None:
            for code_error in all_errors:
                line_numbers = []
                if code_error.code_error_lines is not None:
                    for code_line in code_error.code_error_lines:
                        line_numbers_found = jc.get_line_number(code_line)
                        if line_numbers_found is not None:
                            line_numbers.extend(line_numbers_found)
                    # Return the unique list of line numbers
                    line_numbers = sorted(set(line_numbers))
                    code_error.set_line_numbers_of_error(line_numbers)

        # Append the errors back to the instance variables
        self.major_errors = unique_major_errors
        self.minor_errors = unique_minor_errors

    def add_errors_to_doc_document(self, d: Document, errors: List[CodeError]):
        for error in errors:
            # Add the error type
            type_paragraph = d.add_paragraph("", style='List Bullet 2')
            type_paragraph.add_run(str(error.error_type) + ":").italic = True

            # Add the error details
            for details in error.error_details.split("\n"):
                details_paragraph = d.add_paragraph("", style='List Bullet 3')
                details_paragraph.add_run(details)

    def set_document_style(self, document: Document):
        style = document.styles['Heading 3']
        font = style.font
        font.name = 'Lato'
        font.size = Pt(23)
        style2 = document.styles['List Bullet 2']
        font = style2.font
        font.name = 'Lato'
        font.size = Pt(19)
        style3 = document.styles['List Bullet 3']
        font = style3.font
        font.name = 'Lato'
        font.size = Pt(15)

    def save_feedback_to_docx(self, file_path: str):
        document = Document()

        # Set the styles for the document
        self.set_document_style(document)

        # Add the Major Errors, Deductions, and Details to the document
        if self.major_deduction_total > 0:
            document.add_heading(self.major_code_deduction_points_text, 3)
            self.add_errors_to_doc_document(document, self.major_errors)

        # Add the Minor Errors, Deductions, and Details to the document
        if self.minor_deduction_total > 0:
            document.add_heading(self.minor_code_deduction_points_text, 3)
            self.add_errors_to_doc_document(document, self.minor_errors)

        # Add The Final Score to the Document
        document.add_heading(self.final_score_text, 3)

        # Save the feedback to file
        document.save(file_path)
        #print("Feedback Saved to : %s" % file_path)

    def save_feedback_template(self, file_path: str):
        error_details = "This is an example detail for feedback."
        # Create all example errors
        self.major_errors = [MajorError(error_type=error_type, error_details=error_details) for error_type in
                             MajorErrorType.list()]
        self.minor_errors = [MinorError(error_type=error_type, error_details=error_details) for error_type in
                             MinorErrorType.list()]

        # print to file
        self.save_feedback_to_docx(file_path)


def start_grading():
    # Navigate to Bright Space

    # Look for Exams

    # For each Exam , get the instructions, the solution

    # Fore each student submission grade it

    # TODO: Put student name infront of output
    # points, grade_feedback = grade_submission(EXAM_INSTRUCTIONS, EXAM_SOLUTION, STUDENT_SUBMISSION)
    # print("Feedback:\n%s" % grade_feedback)

    # TODO: Add  grade output to submission as Draft

    don_nothing_for_now = None
