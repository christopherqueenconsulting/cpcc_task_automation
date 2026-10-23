#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The instructor-only "very similar to the solution" flag (PR 7)."""

import pytest

from cqc_cpcc import solution_similarity as ss
from cqc_cpcc.model_eval import dataset as ds
from cqc_cpcc.model_eval.suites.base import DATASETS_ROOT

pytestmark = pytest.mark.unit

JAVA = """
// Program: Order Total
public class OrderTotal {
    public static final double TAX_RATE = 0.07;
    public static void main(String[] args) {
        double itemPrice = 12.5;   // the price
        double total = itemPrice * (1 + TAX_RATE);
        System.out.printf("Total: $%.2f%n", total);
    }
}
"""


def test_normalisation_ignores_comments_names_and_literals():
    renamed = JAVA.replace("itemPrice", "unitCost").replace("0.07", "0.0825").replace("// the price", "")
    assert ss.normalize(JAVA) == ss.normalize(renamed)
    tokens = ss.normalize(JAVA)
    assert "V" in tokens and "N" in tokens and "S" in tokens and "OrderTotal" not in tokens
    assert "printf" in ss.normalize(JAVA) and "double" in ss.normalize(JAVA)


def test_identical_shape_flags_and_a_restructured_program_does_not():
    same = ss.solution_similarity(JAVA.replace("itemPrice", "cost"), JAVA)
    assert same.ratio == 1.0 and same.flagged and same.threshold == ss.DEFAULT_THRESHOLD
    helper = JAVA.replace("        double total = itemPrice * (1 + TAX_RATE);\n",
                          "        double total = withTax(itemPrice);\n").replace(
        "    }\n}\n", "    }\n    static double withTax(double p) {\n        return p * (1 + TAX_RATE);\n    }\n}\n")
    other = ss.solution_similarity(helper, JAVA)
    assert 0.5 < other.ratio < ss.DEFAULT_THRESHOLD and not other.flagged
    assert ss.solution_similarity("", JAVA) == ss.Similarity(0.0, False, ss.DEFAULT_THRESHOLD)
    assert ss.similarity_label(same) == "very similar (1.00)" and ss.similarity_label(other) == ""
    assert ss.similarity_label(None) == ""


def test_huge_or_hostile_input_is_bounded():
    import time

    start = time.monotonic()
    huge = ss.solution_similarity("int x;\n" * 70_000, JAVA)
    hostile = ss.solution_similarity("/*" * 500_000, JAVA)
    assert huge == ss.Similarity(0.0, False, ss.DEFAULT_THRESHOLD)
    assert not hostile.flagged
    # Unterminated starts: every "/*" or quote would otherwise rescan to the end (quadratic).
    for text in ("/* " * 66_000, "/*a" * 66_000, '"' + "x" * 199_000, "'" * 100_000, "'a" * 66_000):
        assert not ss.solution_similarity(text, JAVA).flagged
    assert time.monotonic() - start < 5.0
    assert ss.normalize('x = "http://a//b"; // c') .count("S") == 1 and "c" not in ss.normalize('x = "http://a//b"; // c')


def test_the_threshold_is_calibrated_on_the_grading_solution_dataset():
    """Rename-only rewrites and the solution itself flag; the structural rewrites do not."""
    structural = ("helper_method", "string_format", "ternary_pay", "do_while_rate")
    seen = set()
    for c in ds.load_cases(DATASETS_ROOT / "grading-solution" / "v1"):
        sim = ss.solution_similarity("\n".join(c.files.values()), c.reference_solution)
        if "follows_solution_format" in c.tags:
            assert sim.flagged and sim.ratio == 1.0, c.case_id
            seen.add("solution")
        elif "equivalent" in c.tags and any(c.case_id.endswith(f"__equivalent_{k}") for k in structural):
            assert not sim.flagged, (c.case_id, sim.ratio)
            seen.add("structural")
        elif "equivalent" in c.tags:
            assert sim.flagged, (c.case_id, sim.ratio)  # other names, reordered constants: same shape
            seen.add("rename")
    assert seen == {"solution", "structural", "rename"}
