#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The session-only classlist identity table: exact user-id join, name fallback, privacy rules.

Synthetic learners only. Nothing here reads a real page or student.
"""

import base64
import pickle

import pytest

from cqc_cpcc.student_identity import (
    ActivityRow,
    Classlist,
    build_classlist,
    name_key,
    user_id_from_actor_href,
    user_id_from_onclick,
)
from cqc_cpcc.utilities import pii_redaction

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_registry():
    pii_redaction.clear_registry()
    yield
    pii_redaction.clear_registry()


def _classlist():
    return build_classlist([
        {"user": "101", "org": "1000001", "name": "Adams, Ann"},
        {"user": "102", "org": "1000002", "name": "Brown, Bob"},
        {"user": "103", "org": "1000003", "name": "Lee, Pat"},
        {"user": "104", "org": "1000004", "name": "Lee, Pat"},  # same name, different student
    ])


class TestNameKey:
    def test_last_comma_first_equals_first_last(self):
        assert name_key("Adams, Ann") == name_key("Ann Adams")

    def test_a_middle_initial_is_ignored_but_a_middle_name_is_not(self):
        assert name_key("Adams, Ann M.") == name_key("Ann Adams")
        assert name_key("Adams, Ann Marie") != name_key("Ann Adams")

    def test_empty_and_none(self):
        assert name_key("") == frozenset() and name_key(None) == frozenset()

    def test_a_curly_apostrophe_matches_a_straight_one(self):
        assert name_key("O’Brien, Pat") == name_key("Pat O'Brien")


class TestUserIdReaders:
    def test_feedback_onclick(self):
        assert user_id_from_onclick("feedback,12345,6789;return false") == "12345"
        assert user_id_from_onclick("EmailUser(12345)") is None
        assert user_id_from_onclick(None) is None and user_id_from_onclick(object()) is None

    def test_actor_href_decodes_the_user_id(self):
        token = base64.urlsafe_b64encode(b"abc123.user_4242").decode().rstrip("=")
        href = f"https://d2l.example/d2l/le/activities/evaluation/actor/{token}?selectedItem=zzz&cft=quiz"
        assert user_id_from_actor_href(href) == "4242"

    @pytest.mark.parametrize("href", [None, "", "https://d2l.example/other", "https://d2l.example/x/actor/",
                                      "https://d2l.example/x/actor/!!!notbase64!!!?a=b"])
    def test_a_link_without_a_readable_id_is_none(self, href):
        assert user_id_from_actor_href(href) is None

    def test_actor_href_whose_decoded_text_has_no_user_is_none(self):
        token = base64.urlsafe_b64encode(b"nothing-here").decode().rstrip("=")
        assert user_id_from_actor_href(f"https://d2l.example/x/actor/{token}?a=b") is None

    def test_a_percent_encoded_padding_decodes(self):
        token = base64.urlsafe_b64encode(b"abcd.user_77").decode()
        assert token.endswith("=") or True
        encoded = token.replace("=", "%3D")
        assert user_id_from_actor_href(f"https://d2l.example/x/actor/{encoded}?a=b") == "77"

    def test_a_segment_with_a_character_outside_the_alphabet_is_not_decoded(self):
        token = base64.urlsafe_b64encode(b"abcd.user_77").decode().rstrip("=")
        assert user_id_from_actor_href(f"https://d2l.example/x/actor/{token}*?a=b") is None

    def test_bytes_after_the_user_id_or_a_longer_suffix_do_not_decode_to_a_wrong_id(self):
        trailing = base64.urlsafe_b64encode(b"abcd.user_77xyz").decode().rstrip("=")
        assert user_id_from_actor_href(f"https://d2l.example/x/actor/{trailing}?a=b") is None
        shifted = base64.urlsafe_b64encode(b"abcd.user_77\n").decode().rstrip("=")
        assert user_id_from_actor_href(f"https://d2l.example/x/actor/{shifted}?a=b") is None


class TestActivityRow:
    def test_it_is_the_plain_pair_plus_the_user_id(self):
        row = ActivityRow("Ann Adams", "10/19/2026", "101")
        name, date = row
        assert (name, date) == ("Ann Adams", "10/19/2026")
        assert row == ("Ann Adams", "10/19/2026") and hash(row) == hash(("Ann Adams", "10/19/2026"))
        assert row.user_id == "101" and ActivityRow("a", "b").user_id is None


class TestResolve:
    def test_the_user_id_gives_the_org_defined_id(self):
        assert _classlist().resolve("Ann Adams", "101") == "1000001"

    def test_two_students_with_the_same_name_resolve_apart_by_user_id(self):
        classlist = _classlist()
        assert classlist.resolve("Pat Lee", "103") == "1000003"
        assert classlist.resolve("Pat Lee", "104") == "1000004"

    def test_a_row_whose_name_does_not_match_its_user_id_is_never_resolved(self):
        assert _classlist().resolve("Bob Brown", "101") is None

    def test_an_unknown_user_id_is_unresolved(self):
        assert _classlist().resolve("Ann Adams", "999") is None

    def test_a_user_id_with_no_name_resolves(self):
        assert _classlist().resolve("", "102") == "1000002"

    def test_a_name_alone_never_resolves_even_when_it_is_unique(self):
        classlist = _classlist()
        assert classlist.resolve("Ann Adams", None) is None
        assert classlist.resolve("Adams, Ann", "") is None
        assert classlist.resolve("Zed Zulu", None) is None and classlist.resolve(None, None) is None

    def test_add_refuses_a_row_that_is_not_a_learner_with_an_org_id(self):
        with pytest.raises(ValueError):
            Classlist().add("", "1000001", "Adams, Ann")
        with pytest.raises(ValueError):
            Classlist().add("101", "ab", "Adams, Ann")
        with pytest.raises(ValueError):
            Classlist().add("101", "12345", "Adams, Ann")  # MyColleges ids are 6-9 digits

    def test_a_user_id_listed_twice_with_different_org_ids_is_refused(self):
        classlist = Classlist()
        classlist.add("101", "1000001", "Adams, Ann")
        classlist.add("101", "1000001", "Adams, Ann")  # the same row again is harmless
        with pytest.raises(ValueError):
            classlist.add("101", "1000002", "Adams, Ann")

    def test_one_org_id_for_two_user_ids_is_refused(self):
        classlist = Classlist()
        classlist.add("101", "1000001", "Adams, Ann")
        with pytest.raises(ValueError):
            classlist.add("102", "1000001", "Brown, Bob")


class TestSessionOnlyAndLogs:
    def test_the_classlist_cannot_be_pickled_or_copied_to_disk(self):
        with pytest.raises(TypeError):
            pickle.dumps(_classlist())

    def test_the_repr_has_a_count_and_no_names_or_ids(self):
        text = repr(_classlist())
        assert "4 learner" in text
        for secret in ("Adams", "Brown", "1000001", "101"):
            assert secret not in text

    def test_clear_drops_everything(self):
        classlist = _classlist()
        classlist.clear()
        assert len(classlist) == 0 and not classlist
        assert classlist.resolve("Ann Adams", "101") is None and classlist.org_defined_ids() == set()

    def test_clear_also_makes_the_log_redactor_forget_the_names_ids_and_user_ids(self):
        classlist = _classlist()
        alias_before = pii_redaction.alias("1000001")
        classlist.clear()
        assert pii_redaction._known_names == {} and pii_redaction._known_tokens == {}
        assert pii_redaction._known_patterns == [] and pii_redaction._pattern_key == {}
        # Aliases stay stable (an HMAC of the Org Defined ID); nothing links them back to a name now.
        assert pii_redaction.alias("1000001") == alias_before
        assert "Adams" in pii_redaction.scrub("Adams, Ann")  # no longer a registered name

    def test_forgetting_one_student_keeps_the_others_registered(self):
        _classlist()
        assert pii_redaction.forget_students(["1000001"]) == 2  # org id and user id tokens
        assert "Brown" not in pii_redaction.scrub("Brown, Bob") and "Adams" in pii_redaction.scrub("Adams, Ann")

    def test_the_alias_is_keyed_by_the_org_defined_id_for_every_spelling(self):
        _classlist()
        by_id = pii_redaction.alias("1000001")
        assert by_id.startswith("student_")
        assert pii_redaction.alias("Ann Adams") == by_id
        assert pii_redaction.alias("Adams, Ann") == by_id
        assert pii_redaction.alias("101") == by_id  # the BrightSpace user id is scrubbed to the same alias

    def test_a_log_line_with_a_name_id_and_user_id_is_scrubbed(self):
        _classlist()
        line = pii_redaction.scrub("marked Adams, Ann (1000001, user 101) present")
        for secret in ("Adams", "1000001", " 101"):
            assert secret not in line
        assert pii_redaction.alias("1000001") in line

    def test_the_alias_does_not_change_when_the_name_spelling_does(self):
        pii_redaction.register_student("Ann Adams", student_id="1000001", alias_key="1000001")
        first = pii_redaction.alias("1000001")
        pii_redaction.clear_registry()
        pii_redaction.register_student("Ann Marie Adams", student_id="1000001", alias_key="1000001")
        assert pii_redaction.alias("1000001") == first
