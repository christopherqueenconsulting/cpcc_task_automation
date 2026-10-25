#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""App-checked facts about a Flowgorithm file name (lab number, name-like word).

Names are the synthetic ones the PII guard allows ("Ada Example"). "Name-like word present" is
not a roster check: an ordinary program name such as ``OrderTotal`` also reads ``yes``.
"""

import json
from pathlib import Path

import pytest

from cqc_cpcc.utilities.fprg_filename_facts import (
    NO, UNKNOWN, YES, FileNameFacts, file_name_facts, render_facts)

pytestmark = pytest.mark.unit

CASES = Path(__file__).resolve().parents[2] / "evals" / "datasets" / "flowgorithm-grade" / "v1" / "cases.jsonl"


@pytest.mark.parametrize("name,lab,label,word", [
    ("Ada_Example_Lab3.fprg", YES, "Lab 3", YES),
    ("Lab03 - Ada Example", YES, "Lab 3", YES),
    ("AdaExample_L3", YES, "Lab 3", YES),
    ("Ada Example.fprg", NO, None, YES),
    ("Lab3.fprg", YES, "Lab 3", NO),                 # lab only, no name-like word
    ("lab_3_final_v2", YES, "Lab 3", UNKNOWN),       # stopwords only: could be a surname
    ("12345678.fprg", NO, None, NO),                 # an id is not a lab number
    ("OrderTotal.fprg", NO, None, YES),              # cannot tell a program name from a name: yes
    ("Lab 3.1", YES, "Lab 3", NO),
    ("Lab03-1", YES, "Lab 3", NO),
    ("Ch4_Ada", YES, "Chapter 4", YES),
    ("HW2.fprg", YES, "Homework 2", NO),
    ("project_2", YES, "Project 2", NO),
    ("Flowgorithm 5", YES, "Flowgorithm 5", NO),
    ("ada-example-lab-7.fprg", YES, "Lab 7", YES),
    ("Ada.Example.Lab4.fprg", YES, "Lab 4", YES),
    ("adaExampleLab12", YES, "Lab 12", YES),
    ("Ada_Example", NO, None, YES),
    ("Ada3", YES, None, YES),                      # bare number: yes, no digits sent
    ("Lab3_2026", YES, "Lab 3", NO),                 # a 4-digit year is not a lab number
    ("Ada_Example_2026-10-25", NO, None, YES),       # a date is not a lab number
    ("Lab3 (2)", YES, "Lab 3", NO),
    ("Jos\u00e9_Lab2", YES, "Lab 2", YES),
    ("a/b/Ada Example Lab 4.fprg", YES, "Lab 4", YES),
    # a date after the token must not swallow its digits
    ("Smith_Lab3_10-05-2024", YES, "Lab 3", YES),
    ("Lab3_10_2024", YES, "Lab 3", NO),
    ("Lab1_9-8-24", YES, "Lab 1", NO),
    ("Lab 2 3-4-25", YES, "Lab 2", NO),
    # a name made only of stopwords or initials is unknown, never no
    ("Final_Lab3", YES, "Lab 3", UNKNOWN),
    ("Lab3_J", YES, "Lab 3", UNKNOWN),
    ("New Part Test", UNKNOWN, None, UNKNOWN),   # "Part" is a keyword: no deduction
    ("Chapter_Lab2_Fix", YES, "Lab 2", UNKNOWN),
    # realistic names
    ("Smith_Lab3", YES, "Lab 3", YES),
    ("lab 3 - Maria Garcia", YES, "Lab 3", YES),
    ("CSC151_Lab_03_Doe", YES, "Lab 3", YES),
    ("hw3", YES, "Homework 3", NO),
    ("Lab 10", YES, "Lab 10", NO),
    ("flowgorithm_lab2_final", YES, "Lab 2", UNKNOWN),
    ("JohnSmith-Flowgorithm-5", YES, "Flowgorithm 5", YES),
    ("LastName_FirstName_L3", YES, "Lab 3", YES),
    ("C:\\\\work\\\\Ada Example Lab 4.FPRG", YES, "Lab 4", YES),
    ("Lab1234567.fprg", UNKNOWN, None, UNKNOWN),     # keyword but the 7-digit run is an id: no deduction
    # spelled-out numbers and roman numerals after a keyword (fixed keyword + integer label)
    ("Lab_Three", YES, "Lab 3", NO),
    ("Lab_One", YES, "Lab 1", NO),
    ("Lab_Twenty", YES, "Lab 20", NO),
    ("Lab-Four", YES, "Lab 4", NO),
    ("LabTwentyOne", YES, "Lab 21", NO),
    ("Ada_Example_Lab_Ten", YES, "Lab 10", YES),
    ("LabII", YES, "Lab 2", NO),
    ("LabIV", YES, "Lab 4", NO),
    ("Lab_II", YES, "Lab 2", NO),
    ("lab-ix", YES, "Lab 9", NO),
    ("LabXX", YES, "Lab 20", NO),
    ("LabIII_Ada", YES, "Lab 3", YES),
    ("Lab-03", YES, "Lab 3", NO),
    ("lab3", YES, "Lab 3", NO),
    ("L3", YES, "Lab 3", NO),
    ("Program 3", YES, "Program 3", NO),
    ("Sum.fprg", NO, None, YES),
    # keyword but no readable number: unknown, so no deduction
    ("Lab_Foo", UNKNOWN, None, YES),
    ("Lab", UNKNOWN, None, UNKNOWN),
    ("Smith_Lab", UNKNOWN, None, YES),
    ("LabThirty", UNKNOWN, None, YES),
    # a spelled number with no keyword is number-like, so not "no"
    ("Mix_ed_Three", UNKNOWN, None, YES),
    # a bare roman letter without a keyword is just a word
    # two or more roman letters with no keyword: number-like but not a lab number, so unknown
    ("Smith_II", UNKNOWN, None, YES),
    ("Ada_Example_IV.fprg", UNKNOWN, None, YES),
    ("smith_iii", UNKNOWN, None, YES),
    ("SmithVII", UNKNOWN, None, YES),
    ("Lab II", YES, "Lab 2", NO),
    ("lab_iv", YES, "Lab 4", NO),
    ("Smith_Lab_IV", YES, "Lab 4", YES),
    ("Ada_Example", NO, None, YES),
    ("Ada_X", NO, None, YES),
    ("Smith_Three", UNKNOWN, None, YES),
    ("Smith_V", NO, None, YES),
    ("Lab_III", YES, "Lab 3", NO),
    ("lab-iii", YES, "Lab 3", NO),
    ("Ada_I_Smith", NO, None, YES),
    ("Ada_V", NO, None, YES),
    ("I", NO, None, UNKNOWN),
    ("Labi_Ada", YES, "Lab 1", YES),                 # documented limit: errs toward yes
])
def test_facts(name, lab, label, word):
    facts = file_name_facts(name)
    assert (facts.lab_number, facts.lab_label, facts.name_like_word) == (lab, label, word)


@pytest.mark.parametrize("name", ["", None, "   ", ".fprg", "---", "...", "x.docx", "Ada Example.txt",
                                  "\u0663\u0664.fprg", "\u2603.fprg"])
def test_unparseable_or_odd_extension_is_unknown(name):
    assert file_name_facts(name) == FileNameFacts(UNKNOWN, None, UNKNOWN)
    assert "unknown" in render_facts(file_name_facts(name))


def test_the_label_and_the_rendering_never_carry_the_name():
    for name in ("Ada_Example_Lab3.fprg", "AdaExample_L3", "Ada Example Ch4", "Ben Sample hw2"):
        text = render_facts(file_name_facts(name))
        for part in ("Ada", "Example", "Ben", "Sample"):
            assert part not in text


def test_render_has_no_roster_wording_and_no_digits_for_a_bare_number():
    assert "roster" not in render_facts(file_name_facts("Ada Example"))
    assert render_facts(file_name_facts("Ada3")) == (
        "- Lab number in file name: yes\n- Name in file name: yes")


@pytest.mark.parametrize("name", ["Qu\u0131z3.fprg", "Ex\u0130rcise3", "Assignmen\u017f4", "\u0130\u0131\u017f3",
                                  "L\u0130b3", "Pro\u017fect_2.fprg"])
def test_unicode_case_folding_lookalikes_never_raise(name):
    facts = file_name_facts(name)
    assert isinstance(facts, FileNameFacts)
    assert facts.lab_number in (YES, NO, UNKNOWN)


def test_unexpected_errors_read_unknown_and_never_raise(monkeypatch):
    from cqc_cpcc.utilities import fprg_filename_facts as m

    def boom(_stem):
        raise RuntimeError("Ada Example")

    monkeypatch.setattr(m, "_lab", boom)
    assert file_name_facts("Ada_Lab3") == FileNameFacts(UNKNOWN, None, UNKNOWN)


def test_a_huge_name_is_capped_and_fast():
    import time

    start = time.perf_counter()
    facts = file_name_facts("Lab3" + "x" * 1_000_000 + ".fprg")
    assert time.perf_counter() - start < 2
    assert isinstance(facts, FileNameFacts)
    start = time.perf_counter()
    file_name_facts("a" * 1_000_000)
    file_name_facts("1-" * 500_000)
    assert time.perf_counter() - start < 2


def test_the_rubric_caption_constant_and_page_render():
    from streamlit.testing.v1 import AppTest

    from cqc_streamlit_app.grade_assignment import FLOWGORITHM_FILE_NAME_CAPTION

    assert "unknown is never deducted" in FLOWGORITHM_FILE_NAME_CAPTION
    assert "lab word with no number" in FLOWGORITHM_FILE_NAME_CAPTION and "roman numeral" in FLOWGORITHM_FILE_NAME_CAPTION
    assert "bare" not in FLOWGORITHM_FILE_NAME_CAPTION and "Smith_Three" in FLOWGORITHM_FILE_NAME_CAPTION
    assert "not sent to the model" in FLOWGORITHM_FILE_NAME_CAPTION

    def page():
        from cqc_streamlit_app.grade_assignment import define_grading_rubric
        define_grading_rubric()

    at = AppTest.from_function(page, default_timeout=30).run()
    assert not at.exception
    assert any(FLOWGORITHM_FILE_NAME_CAPTION == c.value for c in at.caption)


def test_unicode_digits_do_not_count_as_a_lab_number():
    assert file_name_facts("Ada\u0663").lab_number == NO


def test_every_eval_dataset_case_renders_without_error():
    rows = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows
    for row in rows:
        facts = file_name_facts(row["inputs"].get("submission_file_name"))
        text = render_facts(facts)
        assert text.startswith("- Lab number in file name: ")
        assert facts.lab_number in (YES, NO, UNKNOWN) and facts.name_like_word in (YES, NO, UNKNOWN)


@pytest.mark.parametrize("name", ["Lab_Three", "LabII", "lab-ix", "LabTwentyOne", "Ada_Example_Lab_Seven"])
def test_matched_text_is_never_in_the_rendered_facts(name):
    text = render_facts(file_name_facts(name))
    lowered = text.lower()
    for token in ("three", "ii", "ix", "twenty", "seven", "ada", "example", "lab_", "labii"):
        assert token not in lowered.replace("lab number in file name", "").replace("name in file name", "")
    assert text.count("(") <= 1 and text.startswith("- Lab number in file name: yes (Lab ")


@pytest.mark.parametrize("name", ["Lab\u0131\u0131", "Lab_Th\u0159ee", "L\u0430b_Three", "Lab\u2169", "Lab_\u0661"])
def test_unicode_lookalikes_never_read_as_numbers(name):
    assert file_name_facts(name).lab_number in (UNKNOWN, NO)


def test_a_million_character_name_with_words_is_fast():
    import time

    start = time.perf_counter()
    file_name_facts("Lab_" + "Three_" * 200_000)
    file_name_facts("LabI" * 300_000)
    file_name_facts("Lab" + " " * 1_000_000 + "Three")
    assert time.perf_counter() - start < 2
