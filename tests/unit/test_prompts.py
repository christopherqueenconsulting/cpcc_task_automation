#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for the shared console prompt helpers.

These run before any browser work, so a mistyped answer has to re-prompt rather
than crash or -- worse -- be read as a valid selection. Every test drives real
keystrokes through ``builtins.input``.
"""

from unittest.mock import patch

import pytest

from cqc_cpcc.utilities.prompts import (
    EXPAND,
    parse_index_selection,
    prompt_index_selection,
    prompt_menu,
    prompt_yes_no,
)

OPTIONS = ["Take attendance", "Process withdrawals", "Grade"]


def typed(*answers):
    """Patch input() with a fixed sequence of keystrokes."""
    return patch("builtins.input", side_effect=list(answers))


@pytest.mark.unit
class TestPromptMenu:
    def test_a_number_selects_that_option(self):
        with typed("2"):
            assert prompt_menu("Pick", OPTIONS) == 1

    def test_pressing_enter_takes_the_default(self):
        with typed(""):
            assert prompt_menu("Pick", OPTIONS, default_index=2) == 2

    def test_surrounding_whitespace_is_ignored(self):
        with typed("  3  "):
            assert prompt_menu("Pick", OPTIONS) == 2

    @pytest.mark.parametrize("bad", ["x", "2.5", "--", "one"])
    def test_a_non_numeric_answer_re_prompts_instead_of_raising(self, bad):
        with typed(bad, "1"):
            assert prompt_menu("Pick", OPTIONS) == 0

    @pytest.mark.parametrize("bad", ["0", "4", "-1", "99"])
    def test_a_number_outside_the_menu_re_prompts(self, bad):
        """Off-by-one is the likeliest mistake, so 0 and len+1 must both fail."""
        with typed(bad, "2"):
            assert prompt_menu("Pick", OPTIONS) == 1

    def test_a_menu_with_no_options_is_a_programming_error(self):
        with pytest.raises(ValueError, match="at least one option"):
            prompt_menu("Pick", [])


@pytest.mark.unit
class TestPromptIndexSelection:
    LABELS = ["CSC-151", "CSC-134", "CSC-113", "CSC-121"]

    def test_a_range_selects_every_item_in_it(self):
        with typed("2-4"):
            assert prompt_index_selection("Pick", self.LABELS) == [1, 2, 3]

    def test_all_selects_everything(self):
        with typed("all"):
            assert prompt_index_selection("Pick", self.LABELS) == [0, 1, 2, 3]

    def test_pressing_enter_takes_the_marked_defaults(self):
        with typed(""):
            assert prompt_index_selection("Pick", self.LABELS, [0, 2]) == [0, 2]

    def test_pressing_enter_with_no_defaults_selects_nothing(self):
        with typed(""):
            assert prompt_index_selection("Pick", self.LABELS) == []

    def test_an_unparseable_answer_re_prompts_and_says_the_valid_range(self, caplog):
        with typed("banana", "1"), caplog.at_level("WARNING"):
            assert prompt_index_selection("Pick", self.LABELS) == [0]

        assert "1 through 4" in caplog.text

    def test_an_out_of_range_number_re_prompts(self):
        with typed("9", "1,2"):
            assert prompt_index_selection("Pick", self.LABELS) == [0, 1]

    def test_defaults_outside_the_list_are_dropped_not_returned(self):
        """A stale default index must never select the wrong course."""
        with typed(""):
            assert prompt_index_selection("Pick", self.LABELS, [1, 99, -1]) == [1]

    def test_an_empty_list_returns_nothing_without_prompting(self):
        with patch("builtins.input", side_effect=AssertionError("must not prompt")):
            assert prompt_index_selection("Pick", []) == []

    def test_the_expand_keyword_asks_the_caller_for_a_longer_list(self):
        with typed("more"):
            result = prompt_index_selection(
                "Pick", self.LABELS,
                expand_keyword="more", expand_hint="Type 'more' to see every course.",
            )

        assert result is EXPAND

    def test_the_expand_keyword_is_matched_case_insensitively(self):
        with typed("MORE"):
            assert prompt_index_selection(
                "Pick", self.LABELS, expand_keyword="more", expand_hint="hint"
            ) is EXPAND

    def test_without_an_expand_keyword_that_word_is_just_an_invalid_answer(self):
        with typed("more", "1"):
            assert prompt_index_selection("Pick", self.LABELS) == [0]


@pytest.mark.unit
class TestParseIndexSelectionEdges:
    """Covers the shapes the run_plan tests do not already exercise."""

    def test_trailing_and_repeated_commas_are_tolerated(self):
        assert parse_index_selection("1,,3,", 4) == [0, 2]

    def test_duplicates_collapse_to_one_index(self):
        assert parse_index_selection("2,2,2-2", 4) == [1]

    def test_a_backwards_range_is_rejected_rather_than_silently_empty(self):
        """4-2 is a typo; returning [] would quietly select nothing."""
        assert parse_index_selection("4-2", 4) is None

    def test_none_is_an_explicit_empty_selection_not_an_error(self):
        assert parse_index_selection("none", 4) == []

    def test_a_blank_answer_is_not_a_selection(self):
        """Blank means "use the default", which only the caller knows."""
        assert parse_index_selection("   ", 4) is None

    @pytest.mark.parametrize("answer", ["1-", "-3", "a-b", "1-x"])
    def test_a_malformed_range_is_rejected(self, answer):
        assert parse_index_selection(answer, 4) is None


@pytest.mark.unit
class TestPromptYesNoEdges:
    @pytest.mark.parametrize("answer", ["maybe", "yy", "nope!", "2"])
    def test_anything_that_is_not_yes_or_no_re_prompts(self, answer, caplog):
        with typed(answer, "y"), caplog.at_level("WARNING"):
            assert prompt_yes_no("Continue?") is True

        assert "yes or no" in caplog.text

    @pytest.mark.parametrize("answer", ["n", "no", "false", "0", "  NO  "])
    def test_every_spelling_of_no_is_understood(self, answer):
        """A dry-run confirmation hangs on this: "no" must never read as yes."""
        with typed(answer):
            assert prompt_yes_no("Continue?", default=True) is False

    @pytest.mark.parametrize("answer", ["y", "yes", "true", "1", " YES "])
    def test_every_spelling_of_yes_is_understood(self, answer):
        with typed(answer):
            assert prompt_yes_no("Continue?", default=False) is True

    @pytest.mark.parametrize("default", [True, False])
    def test_an_empty_answer_takes_the_stated_default(self, default):
        with typed(""):
            assert prompt_yes_no("Continue?", default=default) is default
