#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Deterministic builders for the prompt-suite datasets (``evals/datasets/<suite>/v1``).

All data is synthetic: programs come from the v2 dataset's clean templates and seeded
mutations (``dataset_builder``), everything else is written here. Labels come from
construction (a seeded defect is the expected finding), so they are drafts until a human
reviews them (``labels_reviewed_by``). Do not hand-edit the generated files: change this
module and run ``python -m cqc_cpcc.model_eval build-suite-datasets``; a unit test fails
when the committed files differ from what this module produces.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Optional

from cqc_cpcc.model_eval import dataset_builder as db

ROOT = Path(__file__).resolve().parents[3] / "evals" / "datasets"
VERSION = "v1"


def _submission_text(filename: str, source: str, language: str) -> str:
    """Same shape as the app's submission text builder: a named, fenced code block."""
    fence = {"java": "java", "cpp": "cpp"}.get(language, "")
    return f"File: {filename}\n```{fence}\n{source}\n```"


def _reference(a: db.Assignment) -> str:
    return a.clean.replace("{author}", "Ada Example")


# --------------------------------------------------------------------------------------
# exam-grading: the v2 programs, labels mapped to the exam enums (major/minor)
# --------------------------------------------------------------------------------------

#: v2 error-id suffix -> exam enum member name, per assignment.
EXAM_ENUMS = {
    "csc151_exam1_java": {
        s: f"CSC_151_EXAM_1_{s}" for s in (
            "INSUFFICIENT_DOCUMENTATION", "SEQUENCE_AND_SELECTION_ERROR", "OUTPUT_IMPACT_ERROR",
            "NAMING_CONVENTION", "CONSTANTS_ERROR", "INEFFICIENT_CODE", "OUTPUT_FORMATTING",
            "PROGRAMMING_STYLE", "SCANNER_CLASS", "SYNTAX_ERROR")
    },
    "csc134_project_cpp": {
        "INSUFFICIENT_DOCUMENTATION": "CSC_134_PROJECT_2_INSUFFICIENT_DOCUMENTATION",
        "SEQUENCE_SELECTION_ERROR": "CSC_134_PROJECT_1_SEQUENCE_SELECTION_LOOPING_ERRORS",
        "CALCULATION_ERROR": "CSC_134_PROJECT_1_CALCULATION_ERRORS",
        "INPUT_VALIDATION": "CSC_134_PROJECT_1_INPUT_VALIDATION_ERRORS",
        "FUNCTION_PROTOTYPE_ERROR": "CSC_134_PROJECT_1_FUNCTION_PROTOTYPE_ERRORS",
        "MAJOR_FORMATTING": "CSC_134_PROJECT_1_MAJOR_FORMATTING",
        "MINOR_FORMATTING": "CSC_134_PROJECT_1_DECIMAL_SCALE",
        "INCORRECT_DATA_TYPE": "CSC_134_PROJECT_1_INCORRECT_DATA_TYPE_DECLARATION",
        "MISSPELLING": "CSC_134_PROJECT_1_MISSPELLINGS",
        "NAMING_CONVENTION": "CSC_134_PROJECT_1_NAMING_CONVENTION",
        "CONSTANTS_ERROR": "CSC_134_PROJECT_1_CONSTANTS_ERROR",
        "PROGRAMMING_STYLE": "CSC_134_PROJECT_1_PROGRAMMING_STYLE",
        "CURLY_BRACES_OMITTED": "CSC_134_PROJECT_2_CURLY_BRACES_OMITTED",
        "DOES_NOT_COMPILE": "CSC_134_PROJECT_1_DOES_NOT_COMPILE",
        "OUTPUT_IMPACT_ERROR": "CSC_134_PROJECT_1_OUTPUT_IMPACT_ERRORS",
        "INEFFICIENT_CODE": "CSC_134_PROJECT_1_INEFFICIENT_CODE",
    },
}
#: Error-type families offered to the model for each assignment (enum name prefixes).
EXAM_TYPE_PREFIXES = {
    "csc151_exam1_java": ("CSC_151_EXAM_1_",),
    "csc134_project_cpp": ("CSC_134_PROJECT_1_", "CSC_134_PROJECT_2_"),
}
#: v2 case kinds the exam prompt is evaluated on (validity-gate and incomplete-program
#: cases belong to the rubric path's own checks).
EXAM_TAGS = {"clean", "single", "multi", "decoy", "does_not_compile", "unicode", "injection"}


def _exam_type_lists(assignment_key: str) -> tuple[list, list]:
    from cqc_cpcc.exam_review import MajorErrorType, MinorErrorType

    prefixes = EXAM_TYPE_PREFIXES[assignment_key]
    # __members__ includes aliases (courses that share a description), like the app's list.
    major = [m.value for name, m in MajorErrorType.__members__.items() if name.startswith(prefixes)]
    minor = [m.value for name, m in MinorErrorType.__members__.items() if name.startswith(prefixes)]
    return list(dict.fromkeys(major)), list(dict.fromkeys(minor))


def _exam_values(assignment_key: str, error_ids: list, prefix: str) -> list:
    from cqc_cpcc.exam_review import MajorErrorType, MinorErrorType

    members = {**MinorErrorType.__members__, **MajorErrorType.__members__}
    out = []
    for error_id in error_ids:
        name = EXAM_ENUMS[assignment_key][error_id[len(prefix):]]
        out.append(members[name].value)
    return sorted(set(out))


def build_exam() -> list[dict]:
    rows = []
    for case in db.build_cases():
        if not set(case.tags) & EXAM_TAGS:
            continue
        a = case.assignment
        major, minor = _exam_type_lists(a.key)
        rows.append({
            "case_id": case.case_id,
            "stratum": a.language,
            "tags": sorted(case.tags),
            "twin_of": case.twin_of,
            "inputs": {
                "exam_instructions": a.instructions,
                "exam_solution": _reference(a),
                "student_submission": _submission_text(case.filename or a.filename, case.source, a.language),
                "major_error_types": major,
                "minor_error_types": minor,
            },
            "labels": {
                "expected": _exam_values(a.key, case.required, a.error_prefix),
                "acceptable": _exam_values(a.key, case.acceptable, a.error_prefix),
            },
        })
    return rows


# --------------------------------------------------------------------------------------
# project-feedback: the v2 programs, labels mapped to FeedbackType
# --------------------------------------------------------------------------------------

#: (assignment key, mutation key) -> (expected FeedbackType names, acceptable names)
FEEDBACK_LABELS = {
    ("csc151_exam1_java", "no_comments"): (("COMMENTS_MISSING",), ()),
    ("csc151_exam1_java", "boundary"): (("LOGIC_ERROR",), ("JAVA_OUTPUT_FORMATTING",)),
    ("csc151_exam1_java", "tax_dropped"): (("LOGIC_ERROR",), ("MISSING_FUNCTIONALITY",)),
    ("csc151_exam1_java", "snake_case"): (("JAVA_NAMING_CONVENTION",), ("VARIABLE_NAMING",)),
    ("csc151_exam1_java", "magic_tax"): (("JAVA_CONSTANTS_ERROR",), ()),
    ("csc151_exam1_java", "loop_multiply"): (("JAVA_INEFFICIENT_CODE",), ()),
    ("csc151_exam1_java", "println_total"): (("JAVA_OUTPUT_FORMATTING",), ("OUTPUT_ALIGNMENT_ERROR",)),
    ("csc151_exam1_java", "no_indent"): (("CODE_INDENTATION",), ("PROGRAMMING_STYLE",)),
    ("csc151_exam1_java", "second_scanner"): (("JAVA_SCANNER_CLASS_ERROR",), ("JAVA_INEFFICIENT_CODE",)),
    ("csc151_exam1_java", "missing_semicolon"): (("SYNTAX_ERROR",), ("DOES_NOT_COMPILE",)),
    ("csc134_project_cpp", "no_comments"): (("COMMENTS_MISSING",), ()),
    ("csc134_project_cpp", "flipped_overtime"): (("LOGIC_ERROR",), ()),
    ("csc134_project_cpp", "missing_multiplier"): (("LOGIC_ERROR",), ("CPP_CONSTANTS_ERROR",)),
    ("csc134_project_cpp", "no_validation"): (("MISSING_FUNCTIONALITY",), ("LOGIC_ERROR",)),
    ("csc134_project_cpp", "function_not_called"): (("MISSING_FUNCTIONALITY",), ("LOGIC_ERROR",)),
    ("csc134_project_cpp", "label_changed"): (("OUTPUT_ALIGNMENT_ERROR",), ()),
    ("csc134_project_cpp", "no_precision"): (("OUTPUT_ALIGNMENT_ERROR",), ()),
    ("csc134_project_cpp", "int_rate"): (("INCORRECT_DATA_TYPE",), ("LOGIC_ERROR",)),
    ("csc134_project_cpp", "misspelled_prompt"): (("SPELLING_ERROR",), ()),
    ("csc134_project_cpp", "cryptic_names"): (("CPP_NAMING_CONVENTION",), ("VARIABLE_NAMING",)),
    ("csc134_project_cpp", "magic_threshold"): (("CPP_CONSTANTS_ERROR",), ()),
    ("csc134_project_cpp", "no_indent"): (("CODE_INDENTATION",), ("PROGRAMMING_STYLE",)),
    ("csc134_project_cpp", "braces_omitted"): (("PROGRAMMING_STYLE",), ("CODE_INDENTATION",)),
    ("csc134_project_cpp", "missing_semicolon"): (("CPP_SYNTAX_ERROR",), ("SYNTAX_ERROR", "DOES_NOT_COMPILE")),
}
#: Always fair: the default feedback types ask for extra tips.
FEEDBACK_ALWAYS_ACCEPTABLE = ("ADDITIONAL_TIPS_PROVIDED",)
#: The page offers only the default types and the prompt says "use only provided Feedback
#: Types", so each specific label is expected as the offered type that covers it. The
#: specific type stays acceptable.
FEEDBACK_OFFERED_TYPE = {
    "COMMENTS_MISSING": "COMMENTS_MISSING",
    "SYNTAX_ERROR": "SYNTAX_ERROR", "CPP_SYNTAX_ERROR": "SYNTAX_ERROR", "DOES_NOT_COMPILE": "SYNTAX_ERROR",
    "SPELLING_ERROR": "SPELLING_ERROR",
    "OUTPUT_ALIGNMENT_ERROR": "OUTPUT_ALIGNMENT_ERROR", "JAVA_OUTPUT_FORMATTING": "OUTPUT_ALIGNMENT_ERROR",
    "PROGRAMMING_STYLE": "PROGRAMMING_STYLE", "CODE_INDENTATION": "PROGRAMMING_STYLE",
    "JAVA_NAMING_CONVENTION": "PROGRAMMING_STYLE", "CPP_NAMING_CONVENTION": "PROGRAMMING_STYLE",
    "VARIABLE_NAMING": "PROGRAMMING_STYLE", "JAVA_CONSTANTS_ERROR": "PROGRAMMING_STYLE",
    "CPP_CONSTANTS_ERROR": "PROGRAMMING_STYLE",
    "LOGIC_ERROR": "LOGIC_ERROR", "MISSING_FUNCTIONALITY": "LOGIC_ERROR", "INCORRECT_DATA_TYPE": "LOGIC_ERROR",
    "JAVA_INEFFICIENT_CODE": "ADDITIONAL_TIPS_PROVIDED",
    "JAVA_SCANNER_CLASS_ERROR": "ADDITIONAL_TIPS_PROVIDED",
}
COURSE_NAMES = {"csc151_exam1_java": "CSC 151 Java Programming", "csc134_project_cpp": "CSC 134 C++ Programming"}
FEEDBACK_TAGS = {"clean", "single", "multi", "decoy", "does_not_compile", "injection"}


def build_feedback() -> list[dict]:
    from cqc_cpcc.project_feedback import DefaultFeedbackType

    feedback_types = DefaultFeedbackType.list()
    rows = []
    for case in db.build_cases():
        if not set(case.tags) & FEEDBACK_TAGS:
            continue
        a = case.assignment
        expected, acceptable = set(), set(FEEDBACK_ALWAYS_ACCEPTABLE)
        for key in case.mutations:
            exp, acc = FEEDBACK_LABELS[(a.key, key)]
            expected |= {FEEDBACK_OFFERED_TYPE[t] for t in exp}
            acceptable |= set(exp) | set(acc) | {FEEDBACK_OFFERED_TYPE.get(t, t) for t in acc}
        rows.append({
            "case_id": case.case_id,
            "stratum": a.language,
            "tags": sorted(case.tags),
            "twin_of": case.twin_of,
            "inputs": {
                "course_name": COURSE_NAMES[a.key],
                "assignment_instructions": a.instructions,
                "assignment_solution": _reference(a),
                "feedback_type_list": feedback_types,
                "student_submission": case.source,
            },
            "labels": {"expected": sorted(expected), "acceptable": sorted(acceptable - expected)},
        })
    return rows


# --------------------------------------------------------------------------------------
# requirement-extraction: instruction documents with gold functional requirements
# --------------------------------------------------------------------------------------

#: (key, title, [(functional step, keyword groups)], [style rules that must NOT become items])
REQUIREMENT_SPECS = (
    ("order_total", "OrderTotal.java", [
        ("Uses a Scanner to read an item price (double) and a quantity (int).", [["read", "input", "scanner"], ["price"], ["quantity"]]),
        ("Computes the subtotal as price times quantity.", [["subtotal"]]),
        ("Applies a 10% bulk discount to the subtotal when the quantity is 10 or more.", [["discount"]]),
        ("Adds 7% sales tax to the discounted subtotal.", [["tax"], ["add", "7", "sales"]]),
        ("Prints the subtotal, tax and total with two decimal places.", [["print", "display", "output"], ["total"]]),
    ], ["Use named constants for the tax rate.", "Follow Java naming conventions.", "Comment your code."]),
    ("payroll", "payroll.cpp", [
        ("Prompts for hours worked and hourly pay rate.", [["hour"], ["rate"]]),
        ("Validates that hours are between 0 and 80 and the rate is greater than 0, re-prompting until valid.", [["valid", "re-prompt", "reprompt", "range"]]),
        ("Declares, defines and calls a function calculatePay(hours, rate).", [["calculatepay", "function"]]),
        ("Pays overtime at 1.5 times the rate for hours over 40.", [["overtime"]]),
        ("Prints the gross pay with two decimal places.", [["gross", "pay"], ["print", "display", "output"]]),
    ], ["Use curly braces on every control structure.", "Indent consistently.", "Comment your code."]),
    ("grade_average", "GradeAverage.java", [
        ("Reads five exam scores from the user.", [["read", "input", "enter"], ["score"]]),
        ("Calculates the average of the five scores.", [["average", "mean"]]),
        ("Determines the letter grade (A, B, C, D or F) from the average.", [["letter"]]),
        ("Displays the average with one decimal place and the letter grade.", [["display", "print", "output"], ["average", "grade"]]),
    ], ["Use descriptive variable names.", "Include a header comment with your name."]),
    ("temperature", "temperature.cpp", [
        ("Asks the user for a temperature and a unit, C or F.", [["temperature"], ["unit", "c", "f"]]),
        ("Converts Celsius to Fahrenheit or Fahrenheit to Celsius depending on the unit.", [["convert", "conversion"], ["celsius", "fahrenheit"]]),
        ("Prints an error message when the unit is not C or F.", [["error", "invalid"]]),
        ("Prints the converted temperature with one decimal place.", [["print", "display", "output"], ["converted", "result", "temperature"]]),
    ], ["Use const for the conversion factors.", "Keep lines under 80 characters."]),
    ("loan", "LoanCalculator.java", [
        ("Reads the loan amount, annual interest rate and number of years.", [["loan", "amount", "principal"], ["rate", "interest"], ["year"]]),
        ("Computes the monthly payment with the standard amortization formula.", [["monthly"], ["payment"]]),
        ("Computes the total amount paid over the life of the loan.", [["total"], ["paid", "payment", "amount"]]),
        ("Rejects negative or zero inputs with an error message.", [["negative", "zero", "invalid", "reject"]]),
        ("Prints the monthly payment and total paid as currency.", [["print", "display", "output", "currency"]]),
    ], ["Use Math.pow for exponents.", "Follow Java naming conventions."]),
    ("inventory", "inventory.cpp", [
        ("Stores ten product names and quantities in parallel arrays.", [["array"], ["product", "quantity"]]),
        ("Lets the user search for a product by name.", [["search", "find", "look"], ["product", "name"]]),
        ("Prints the quantity of the product, or \"Not found\" if it does not exist.", [["not found", "missing", "exist"]]),
        ("Lists every product whose quantity is below 5 as low stock.", [["low", "below"], ["stock", "quantity"]]),
    ], ["Use a named constant for the array size.", "Use functions for searching and listing."]),
    ("rock_paper_scissors", "RockPaperScissors.java", [
        ("Lets the player choose rock, paper or scissors.", [["choose", "choice", "pick", "select", "enter"]]),
        ("Generates a random choice for the computer.", [["random", "computer"]]),
        ("Determines and announces the winner of the round.", [["winner", "win", "result"]]),
        ("Repeats rounds until the player chooses to quit.", [["repeat", "loop", "again", "quit"]]),
        ("Prints the final count of wins, losses and ties.", [["count", "tally", "score", "total"]]),
    ], ["Use a switch statement where it fits.", "Comment each method."]),
    ("bank_account", "bank.cpp", [
        ("Starts the account with a balance of $100.", [["balance", "start", "initial"]]),
        ("Shows a menu with deposit, withdraw, balance and quit options.", [["menu", "option"], ["deposit", "withdraw"]]),
        ("Deposits a positive amount into the balance.", [["deposit"]]),
        ("Withdraws an amount only when the balance is large enough.", [["withdraw"]]),
        ("Prints the balance with two decimal places when asked.", [["balance"], ["print", "display", "show", "output"]]),
    ], ["Use a do-while loop for the menu.", "Indent consistently."]),
    ("word_stats", "WordStats.java", [
        ("Reads a line of text from the user.", [["read", "input", "enter"], ["text", "line", "sentence"]]),
        ("Counts the number of words in the line.", [["word"], ["count", "number"]]),
        ("Counts the number of vowels in the line.", [["vowel"]]),
        ("Prints the longest word.", [["longest"]]),
    ], ["Use String methods rather than regular expressions.", "Comment your code."]),
    ("shipping", "shipping.cpp", [
        ("Reads the package weight in pounds and the shipping zone (1-3).", [["weight"], ["zone"]]),
        ("Looks up the rate per pound for the zone.", [["rate"], ["zone", "pound"]]),
        ("Adds a $5 surcharge for packages over 20 pounds.", [["surcharge", "extra", "fee"]]),
        ("Prints the shipping cost with two decimal places.", [["cost", "price", "charge"], ["print", "display", "output"]]),
    ], ["Use named constants for the rates.", "Validate nothing else."]),
    ("number_guess", "NumberGuess.java", [
        ("Picks a random number from 1 to 100.", [["random"]]),
        ("Asks the user to guess until they get it right.", [["guess"]]),
        ("Tells the user whether each guess is too high or too low.", [["high"], ["low"]]),
        ("Prints the number of guesses it took.", [["number", "count", "how many"], ["guess"]]),
    ], ["Use a while loop.", "Use meaningful names."]),
    ("seating_chart", "seating.cpp", [
        ("Stores a 5 by 4 seating chart in a two-dimensional array.", [["array", "chart"]]),
        ("Lets the user reserve a seat by row and column.", [["reserve", "book"], ["row", "column", "seat"]]),
        ("Refuses to reserve a seat that is already taken.", [["taken", "occupied", "already", "refuse"]]),
        ("Prints the chart with X for taken seats and O for open seats.", [["print", "display", "show"], ["chart", "seat"]]),
    ], ["Use functions for each menu choice.", "Comment every function."]),
)


def _format_instructions(title: str, steps: list, style: list, variant: str) -> str:
    texts = [s for s, _ in steps]
    if variant == "numbered":
        body = "\n".join(f"{i}. {t}" for i, t in enumerate(texts, 1))
        return f"Write a program in a file named {title} that:\n{body}\n" + " ".join(style) + "\n"
    if variant == "bulleted":
        body = "\n".join(f"- {t}" for t in texts)
        return (f"Assignment: {title}\n\nYour program must:\n{body}\n\nStyle requirements:\n"
                + "\n".join(f"- {s}" for s in style) + "\n")
    lowered = [t[0].lower() + t[1:] for t in texts]
    ordinals = ("First, it", "Next, it", "Then it", "After that, it", "Finally, it")
    sentences = [f"{ordinals[min(i, 3) if i < len(lowered) - 1 else 4]} {t}" for i, t in enumerate(lowered)]
    return f"Create {title}. " + " ".join(sentences) + " " + " ".join(style) + "\n"


def build_requirements() -> list[dict]:
    rows = []
    for key, title, steps, style in REQUIREMENT_SPECS:
        for variant in ("numbered", "bulleted", "prose"):
            rows.append({
                "case_id": f"{key}__{variant}",
                "stratum": variant,
                "tags": [variant],
                "twin_of": None,
                "inputs": {"instructions": _format_instructions(title, steps, style, variant)},
                "labels": {
                    "gold": [{"id": f"G{i}", "text": text, "keyword_groups": groups}
                             for i, (text, groups) in enumerate(steps, 1)],
                    "style": style,
                    # Numbered or bulleted lists must yield exactly one item per step.
                    "exact_count": len(steps) if variant != "prose" else None,
                },
            })
    return rows


# --------------------------------------------------------------------------------------
# flowgorithm-grade: synthetic .fprg flowcharts with seeded missing criteria
# --------------------------------------------------------------------------------------

FLOWGORITHM_RUBRIC = (
    ("Inputs declared and read", 10),
    ("Correct calculation", 15),
    ("Output displayed with a label", 10),
    ("Comments explain the steps", 5),
    ("Descriptive variable names", 10),
)
FLOWGORITHM_TOTAL = sum(p for _, p in FLOWGORITHM_RUBRIC)

FLOWGORITHM_PROGRAMS = (
    ("rectangle", "Ask for the length and width of a rectangle, compute the area and display it with a label.",
     [("length", "Real"), ("width", "Real")], ("area", "length * width"), "Area: "),
    ("average", "Ask for three test scores, compute their average and display it with a label.",
     [("score1", "Real"), ("score2", "Real"), ("score3", "Real")], ("average", "(score1 + score2 + score3) / 3"),
     "Average: "),
    ("pay", "Ask for hours worked and hourly rate, compute the pay and display it with a label.",
     [("hours", "Real"), ("rate", "Real")], ("pay", "hours * rate"), "Pay: "),
)

#: variant -> rubric criteria the variant breaks (seeded)
FLOWGORITHM_VARIANTS = (
    ("clean", ()),
    ("no_comments", ("Comments explain the steps",)),
    ("no_label", ("Output displayed with a label",)),
    ("wrong_calc", ("Correct calculation",)),
    ("cryptic_names", ("Descriptive variable names",)),
    ("missing_input", ("Inputs declared and read",)),
    ("no_comments+no_label", ("Comments explain the steps", "Output displayed with a label")),
    ("wrong_calc+cryptic_names", ("Correct calculation", "Descriptive variable names")),
)


def _fprg(variant: str, inputs: list, result: tuple, label: str) -> str:
    names = {name: (f"x{i}" if "cryptic_names" in variant else name) for i, (name, _) in enumerate(inputs + [result])}
    out_name = names[result[0]]
    expression = result[1]
    for original, new in names.items():
        expression = expression.replace(original, new)
    if "wrong_calc" in variant:
        expression = expression.replace("*", "+").replace("/ 3", "/ 2")
    body = []
    comments = "no_comments" not in variant
    if comments:
        body.append('            <comment text="Read the inputs from the user"/>')
    used_inputs = inputs[:-1] if "missing_input" in variant else inputs
    for name, kind in inputs:
        body.append(f'            <declare name="{names[name]}" type="{kind}" array="False" size=""/>')
    body.append(f'            <declare name="{out_name}" type="Real" array="False" size=""/>')
    for name, _ in used_inputs:
        body.append(f'            <output expression="&quot;Enter {name}:&quot;" newline="True"/>')
        body.append(f'            <input variable="{names[name]}"/>')
    if comments:
        body.append('            <comment text="Compute the result"/>')
    body.append(f'            <assign variable="{out_name}" expression="{expression}"/>')
    if comments:
        body.append('            <comment text="Display the result"/>')
    shown = out_name if "no_label" in variant else f"&quot;{label}&quot; &amp; {out_name}"
    body.append(f'            <output expression="{shown}" newline="True"/>')
    return ('<?xml version="1.0"?>\n<flowgorithm fileversion="4.2">\n'
            '    <attributes>\n        <attribute name="name" value=""/>\n    </attributes>\n'
            '    <function name="Main" type="None" variable="">\n        <parameters/>\n        <body>\n'
            + "\n".join(body) + "\n        </body>\n    </function>\n</flowgorithm>\n")


def build_flowgorithm() -> list[dict]:
    table = "| Criterion | Points |\n|---|---|\n" + "\n".join(f"| {c} | {p} |" for c, p in FLOWGORITHM_RUBRIC)
    points = dict(FLOWGORITHM_RUBRIC)
    rows = []
    for key, instructions, inputs, result, label in FLOWGORITHM_PROGRAMS:
        for variant, broken in FLOWGORITHM_VARIANTS:
            lost_max = sum(points[c] for c in broken)
            rows.append({
                "case_id": f"{key}__{variant}",
                "stratum": key,
                "tags": ["clean"] if not broken else (["single"] if len(broken) == 1 else ["multi"]),
                "twin_of": None,
                "inputs": {
                    "assignment": instructions,
                    "rubric_criteria_markdown_table": table,
                    "submission": _fprg(variant, inputs, result, label),
                    "submission_file_name": key.title(),
                    "total_possible_points": str(FLOWGORITHM_TOTAL),
                },
                "labels": {
                    "criteria": [c for c, _ in FLOWGORITHM_RUBRIC],
                    "expected_deductions": list(broken),
                    # Losing at most each broken criterion's points, and at least a share of it.
                    "grade_range": [FLOWGORITHM_TOTAL - lost_max, FLOWGORITHM_TOTAL - (0.2 * lost_max)],
                    "total": FLOWGORITHM_TOTAL,
                },
            })
    return rows


# --------------------------------------------------------------------------------------
# digest: multi-file Java submissions with seeded defects and facts to keep
# --------------------------------------------------------------------------------------

DIGEST_PROJECTS = (
    ("library", "Write a library system with Book and Library classes and a LibraryApp main class that "
                "adds books, checks them out and lists available books.", {
        "Book.java": """\
/** A book that can be checked out. */
public class Book {
    private final String title;
    private final String isbn;
    private boolean checkedOut;

    public Book(String title, String isbn) {
        this.title = title;
        this.isbn = isbn;
    }

    public String getTitle() { return title; }
    public String getIsbn() { return isbn; }
    public boolean isCheckedOut() { return checkedOut; }

    /** Marks the book as checked out. */
    public void checkOut() {
        checkedOut = true;
    }

    /** Marks the book as returned. */
    public void giveBack() {
        checkedOut = false;
    }
}
""",
        "Library.java": """\
import java.util.ArrayList;
import java.util.List;

/** Holds the books and answers availability questions. */
public class Library {
    public static final int MAX_BOOKS = 500;
    private final List<Book> books = new ArrayList<>();

    /** Adds a book unless the library is full. */
    public boolean addBook(Book book) {
        if (books.size() >= MAX_BOOKS) {
            return false;
        }
        books.add(book);
        return true;
    }

    /** Returns the books that are not checked out. */
    public List<Book> availableBooks() {
        List<Book> out = new ArrayList<>();
        for (Book b : books) {
            if (!b.isCheckedOut()) {
                out.add(b);
            }
        }
        return out;
    }

    /** Finds a book by ISBN, or null. */
    public Book findByIsbn(String isbn) {
        for (Book b : books) {
            if (b.getIsbn().equals(isbn)) {
                return b;
            }
        }
        return null;
    }
}
""",
        "LibraryApp.java": """\
/** Demonstrates the library. */
public class LibraryApp {
    public static void main(String[] args) {
        Library library = new Library();
        library.addBook(new Book("Dune", "111"));
        library.addBook(new Book("Emma", "222"));
        Book dune = library.findByIsbn("111");
        dune.checkOut();
        for (Book b : library.availableBooks()) {
            System.out.println("Available: " + b.getTitle());
        }
    }
}
""",
    }, {
        "facts": ["Book", "Library", "LibraryApp", "checkOut", "availableBooks", "findByIsbn", "MAX_BOOKS"],
        "defects": {
            "available_inverted": ("Library.java", ("if (!b.isCheckedOut()) {", "if (b.isCheckedOut()) {"),
                                   [["availablebooks", "available"], ["checked", "inverted", "wrong", "condition"]]),
            "isbn_equals": ("Library.java", ("b.getIsbn().equals(isbn)", "b.getIsbn() == isbn"),
                            [["isbn", "findbyisbn"], ["equals", "compare", "comparison", "reference", "operator"]]),
            "null_checkout": ("LibraryApp.java", ('Book dune = library.findByIsbn("111");\n        dune.checkOut();',
                                                  'Book dune = library.findByIsbn("999");\n        dune.checkOut();'),
                              [["null", "999", "not found", "nullpointer"]]),
        },
        "missing": ("giveBack", "Book.java",
                    ("    /** Marks the book as returned. */\n    public void giveBack() {\n"
                     "        checkedOut = false;\n    }\n", "")),
    }),
    ("grades", "Write a gradebook with Student and Gradebook classes and a GradebookApp main class that "
               "records scores, computes each student's average and prints the class average.", {
        "Student.java": """\
import java.util.ArrayList;
import java.util.List;

/** A student and their scores. */
public class Student {
    private final String name;
    private final List<Integer> scores = new ArrayList<>();

    public Student(String name) {
        this.name = name;
    }

    public String getName() { return name; }

    /** Records a score between 0 and 100. */
    public void addScore(int score) {
        if (score < 0 || score > 100) {
            throw new IllegalArgumentException("score out of range");
        }
        scores.add(score);
    }

    /** Average score, 0 when there are none. */
    public double average() {
        if (scores.isEmpty()) {
            return 0;
        }
        int sum = 0;
        for (int s : scores) {
            sum += s;
        }
        return (double) sum / scores.size();
    }
}
""",
        "Gradebook.java": """\
import java.util.ArrayList;
import java.util.List;

/** All students in a section. */
public class Gradebook {
    public static final double PASSING_AVERAGE = 70.0;
    private final List<Student> students = new ArrayList<>();

    public void enroll(Student s) {
        students.add(s);
    }

    /** Mean of the students' averages. */
    public double classAverage() {
        double total = 0;
        for (Student s : students) {
            total += s.average();
        }
        return students.isEmpty() ? 0 : total / students.size();
    }

    /** Number of students at or above the passing average. */
    public int passingCount() {
        int count = 0;
        for (Student s : students) {
            if (s.average() >= PASSING_AVERAGE) {
                count++;
            }
        }
        return count;
    }
}
""",
        "GradebookApp.java": """\
/** Demonstrates the gradebook. */
public class GradebookApp {
    public static void main(String[] args) {
        Gradebook book = new Gradebook();
        Student a = new Student("Student One");
        a.addScore(90);
        a.addScore(80);
        book.enroll(a);
        System.out.printf("Class average: %.1f%n", book.classAverage());
        System.out.println("Passing: " + book.passingCount());
    }
}
""",
    }, {
        "facts": ["Student", "Gradebook", "GradebookApp", "addScore", "average", "classAverage", "passingCount",
                  "PASSING_AVERAGE"],
        "defects": {
            "integer_division": ("Student.java", ("return (double) sum / scores.size();", "return sum / scores.size();"),
                                 [["average", "division", "divide"], ["integer", "int", "truncat", "cast"]]),
            "passing_strict": ("Gradebook.java", ("s.average() >= PASSING_AVERAGE", "s.average() > PASSING_AVERAGE"),
                               [["passing", "passingcount"], ["boundary", "equal", "strict", "70", "greater"]]),
            "no_range_check": ("Student.java", ("        if (score < 0 || score > 100) {\n"
                                                "            throw new IllegalArgumentException(\"score out of range\");\n"
                                                "        }\n", ""),
                               [["score", "addscore"], ["range", "valid", "check", "0", "100"]]),
        },
        "missing": ("passingCount", "Gradebook.java",
                    ("    /** Number of students at or above the passing average. */\n"
                     "    public int passingCount() {\n        int count = 0;\n        for (Student s : students) {\n"
                     "            if (s.average() >= PASSING_AVERAGE) {\n                count++;\n            }\n"
                     "        }\n        return count;\n    }\n", "")),
    }),
)


def _digest_case(key, instructions, files, spec, defects: tuple, missing: bool) -> dict:
    files = dict(files)
    for d in defects:
        filename, (old, new), _ = spec["defects"][d]
        assert old in files[filename], (key, d)
        files[filename] = files[filename].replace(old, new)
    missing_name = None
    if missing:
        missing_name, filename, (old, new) = spec["missing"]
        assert old in files[filename], (key, "missing")
        files[filename] = files[filename].replace(old, new)
        if missing_name == "passingCount":  # keep it compiling: drop the caller too
            files["GradebookApp.java"] = files["GradebookApp.java"].replace(
                '        System.out.println("Passing: " + book.passingCount());\n', "")
    code = "\n\n".join(_submission_text(n, s, "java") for n, s in files.items())
    suffix = "+".join(defects) or "clean"
    instructions_full = instructions + (f" The {missing_name} method is required." if missing_name else
                                        f" The {spec['missing'][0]} method is required.")
    return {
        "case_id": f"{key}__{suffix}" + ("__missing" if missing else ""),
        "stratum": key,
        "tags": ["clean"] if not defects and not missing else ["seeded"],
        "twin_of": None,
        "inputs": {"student_code": code, "assignment_instructions": instructions_full, "rubric_config": ""},
        "labels": {
            "files": sorted(files),
            "facts": [f for f in spec["facts"] if f != missing_name],
            "defects": [{"id": d, "file": spec["defects"][d][0], "keyword_groups": spec["defects"][d][2]}
                        for d in defects],
            "missing_component": missing_name,
            "code_chars": len(code),
        },
    }


def build_digest() -> list[dict]:
    rows = []
    for key, instructions, files, spec in DIGEST_PROJECTS:
        names = list(spec["defects"])
        combos = [()] + [(n,) for n in names] + [tuple(names[:2])] + [tuple(names)]
        for combo in combos:
            rows.append(_digest_case(key, instructions, files, spec, combo, missing=False))
        rows.append(_digest_case(key, instructions, files, spec, (names[0],), missing=True))
        rows.append(_digest_case(key, instructions, files, spec, (), missing=True))
    return rows


# --------------------------------------------------------------------------------------
# grading level-band stratum: CSC-113 reflections with constructed quality per criterion
# --------------------------------------------------------------------------------------

LEVELBAND_RUBRIC = "csc113_week1_reflection_rubric"
LEVELBAND_INSTRUCTIONS = (
    "Week 1 reflection (250-400 words): identify an AI tool you use, explain how you use it and what "
    "the task would be like without it; explain how the system works and its strengths and limitations "
    "with examples; state your learning goals for this course and how they connect to your career.")
STRONG = ("Exemplary", "Proficient")
WEAK = ("Developing", "Beginning")

_TOOLS = ("a navigation app", "a grammar checker", "a music recommendation service", "a language-learning app",
          "a photo search feature")

_TOOL_STRONG = (
    "I use {tool} almost every day. Last week I relied on it to {task}, and it did the work in seconds by "
    "suggesting exactly what I needed. Without it I would have spent a long time doing the same thing by hand, "
    "checking several sources and probably making mistakes along the way, so the comparison is striking.")
_TOOL_WEAK = "I use {tool} sometimes. It is useful."
_INTEL_STRONG = (
    "Behind the scenes, {tool} learns patterns from very large amounts of data and predicts the most likely "
    "useful answer for my situation. Its strength is speed and consistency: it caught a problem I had missed. "
    "Its limitation is that it can be confidently wrong; once it {mistake}, which showed me it does not "
    "really understand context the way a person does.")
_INTEL_WEAK = "It works with AI. It is smart and helpful."
_GOALS_STRONG = (
    "In this course I want to learn how machine learning models are trained and evaluated, and how to "
    "judge when an AI answer can be trusted. I plan to work in healthcare administration, where AI tools "
    "schedule patients and summarize records, so understanding their limits will help me use them safely "
    "and explain their output to my coworkers.")
_GOALS_WEAK = "I want to learn about AI."
_FILLER = (
    "Overall, thinking about this tool made me realize how often I depend on AI without noticing it. I also "
    "talked with a classmate who uses a similar tool differently, and comparing our experiences showed me "
    "that the same system can feel helpful or frustrating depending on what you expect from it. I am "
    "curious to see how my view changes as the course goes on and I learn more about how these systems are "
    "built, tested and improved over time.")
_FILLER_2 = (
    "I also noticed that the tool works best when I give it clear information, and worse when my request is "
    "vague. That reminds me that I still need to check its work instead of trusting every answer, especially "
    "when the result matters for school or work.")
_FILLER_3 = (
    "Looking back over the week, I tried to keep a short log of every time I used an AI feature, and the list "
    "was much longer than I expected, from autocomplete on my phone to the recommendations on my streaming "
    "service and the spam filter in my email inbox.")
_TASKS = ("plan a route around road construction", "fix the grammar in a scholarship essay",
          "find new songs for a study playlist", "practice Spanish verb tenses", "find old photos of my dog")
_MISTAKES = ("sent me down a closed road", "changed the meaning of a sentence", "kept repeating the same artist",
             "accepted a wrong answer as correct", "mixed up two different dogs")

#: (case suffix, quality per criterion: tool, intelligence, goals, presentation)
LEVELBAND_VARIANTS = (
    ("all_strong", ("s", "s", "s", "s")),
    ("all_weak", ("w", "w", "w", "w")),
    ("weak_tool", ("w", "s", "s", "s")),
    ("weak_analysis", ("s", "w", "s", "s")),
    ("weak_goals", ("s", "s", "w", "s")),
    ("too_long", ("s", "s", "s", "w")),
)


def _reflection(quality: tuple, i: int) -> str:
    tool, task, mistake = _TOOLS[i % 5], _TASKS[i % 5], _MISTAKES[i % 5]
    parts = [
        (_TOOL_STRONG if quality[0] == "s" else _TOOL_WEAK).format(tool=tool, task=task),
        (_INTEL_STRONG if quality[1] == "s" else _INTEL_WEAK).format(tool=tool, mistake=mistake),
        _GOALS_STRONG if quality[2] == "s" else _GOALS_WEAK,
    ]
    if quality[3] == "s":
        # Presentation: meets the 250-400 word format.
        extra = [_FILLER]
        if len(" ".join(parts + extra).split()) < 260:
            extra.append(_FILLER_2)
        return "\n\n".join(parts + extra)
    if all(q == "w" for q in quality):
        return "\n\n".join(parts)  # far too short
    # Strong content, but well over the 400-word limit: the format is not met.
    return "\n\n".join(parts + [_FILLER, _FILLER_2, _FILLER_3, _FILLER, _FILLER_2])


def build_levelband() -> list[dict]:
    from cqc_cpcc.model_eval.dataset import rubric as load_rubric

    r = load_rubric(LEVELBAND_RUBRIC)
    criteria = [c for c in r.criteria if c.enabled]
    rows = []
    for i, ((suffix, quality), n) in enumerate(itertools.product(LEVELBAND_VARIANTS, range(3))):
        allowed = {c.criterion_id: list(STRONG if q == "s" else WEAK) for c, q in zip(criteria, quality)}
        low = sum(min(l.score_min for l in c.levels if l.label in allowed[c.criterion_id]) for c in criteria)
        high = sum(max(l.score_min for l in c.levels if l.label in allowed[c.criterion_id]) for c in criteria)
        text = _reflection(quality, i)
        rows.append({
            "case_id": f"reflection__{suffix}__{n}",
            "stratum": "reflection",
            "tags": ["levelband", suffix],
            "twin_of": None,
            "inputs": {"rubric_id": LEVELBAND_RUBRIC, "assignment_instructions": LEVELBAND_INSTRUCTIONS,
                       "student_submission": text},
            "labels": {"allowed_levels": allowed, "score_range": [low, high],
                       "max_points": r.total_points_possible, "word_count": len(text.split())},
        })
    return rows


# --------------------------------------------------------------------------------------

BUILDERS = {
    "exam-grading": build_exam,
    "project-feedback": build_feedback,
    "requirement-extraction": build_requirements,
    "flowgorithm-grade": build_flowgorithm,
    "digest": build_digest,
    "grading-levelband": build_levelband,
}


def dataset_dir(name: str) -> Path:
    return ROOT / name / VERSION


def render(name: str, reviewed_by: Optional[str] = None) -> str:
    rows = BUILDERS[name]()
    ids = [r["case_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{name}: duplicate case ids")
    return "".join(json.dumps({**row, "labels_reviewed_by": reviewed_by}, ensure_ascii=False, sort_keys=True) + "\n"
                   for row in rows)


def write_all(root: Path = ROOT, reviewed_by: Optional[str] = None) -> dict:
    counts = {}
    for name in BUILDERS:
        d = root / name / VERSION
        d.mkdir(parents=True, exist_ok=True)
        text = render(name, reviewed_by)
        (d / "cases.jsonl").write_text(text, encoding="utf-8")
        (d / "VERSION").write_text(VERSION + "\n", encoding="utf-8")
        counts[name] = text.count("\n")
    return counts


def load_rows(name: str, root: Path = ROOT) -> list[dict]:
    path = root / name / VERSION / "cases.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
