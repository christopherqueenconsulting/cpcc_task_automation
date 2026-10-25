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
    ("lab_3_final_v2", YES, "Lab 3", NO),            # stopwords only
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
    ("Ada3", YES, "Number 3", YES),
    ("Lab3_2026", YES, "Lab 3", NO),                 # a 4-digit year is not a lab number
    ("Ada_Example_2026-10-25", NO, None, YES),       # a date is not a lab number
    ("Lab3 (2)", YES, "Lab 3", NO),
    ("Jos\u00e9_Lab2", YES, "Lab 2", YES),
    ("a/b/Ada Example Lab 4.fprg", YES, "Lab 4", YES),
    ("C:\\\\work\\\\Ada Example Lab 4.FPRG", YES, "Lab 4", YES),
    ("Lab1234567.fprg", NO, None, NO),               # 7-digit run: an id
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


def test_render_says_not_verified_against_a_roster_only_for_yes():
    assert "not verified against a roster" in render_facts(file_name_facts("Ada Example"))
    assert "roster" not in render_facts(file_name_facts("Lab3"))


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
