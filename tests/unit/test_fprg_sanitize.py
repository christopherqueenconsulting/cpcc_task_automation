#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Author/identity stripping for Flowgorithm bodies sent to the LLM provider (synthetic data)."""

import base64
import re
from unittest.mock import AsyncMock

import pytest

from cqc_cpcc import flowgorithm_grading as fg
from cqc_cpcc.utilities.fprg_sanitize import sanitize_fprg

pytestmark = pytest.mark.unit

NAME = "Ada Example"
STAMP = base64.b64encode(f"{NAME};LAB01;2026-01-01;10:00:00 AM;2532".encode()).decode()

BODY = '''    <function name="Main" type="None" variable="">
        <parameters/>
        <body>
            <comment text="Read the inputs"/>
            <declare name="length" type="Real" array="False" size=""/>
            <input variable="length"/>
            <output expression="&quot;Enter length:&quot;" newline="True"/>
        </body>
    </function>
</flowgorithm>
'''


def _fprg(extra_comment: str = "") -> str:
    return (
        '<?xml version="1.0"?>\n'
        '<flowgorithm fileversion="4.2">\n'
        '    <attributes>\n'
        '        <attribute name="name" value="Project 1 by Ada Example"/>\n'
        f'        <attribute name="authors" value="{NAME}"/>\n'
        '        <attribute name="about" value="Ada Example, CSC 134"/>\n'
        '        <attribute name="saved" value="2026-01-01 10:05:00 AM"/>\n'
        f'        <attribute name="created" value="{STAMP}"/>\n'
        f'        <attribute name="edited" value="{STAMP}"/>\n'
        '    </attributes>\n'
        + BODY.replace('<comment text="Read the inputs"/>', f'<comment text="Read the inputs{extra_comment}"/>')
    )


def _blank(text: str) -> str:
    return re.sub(r'(<attribute name="(?:name|authors|about|created|edited)" value=")[^"]*"', r'\1"', text)


def test_identity_attributes_blanked_and_rest_identical():
    src = _fprg()
    out = sanitize_fprg(src)
    assert NAME not in out and STAMP not in out and "LAB01" not in out
    assert 'name="authors" value=""' in out
    assert 'name="saved" value="2026-01-01 10:05:00 AM"' in out  # timestamp is not a name
    assert out == _blank(src)  # equals the input except for the stripped values


def test_email_removed_from_comment_and_attribute():
    src = _fprg(" email ada.example@cpcc.edu")
    src = src.replace('<attribute name="saved"', '<attribute name="note" value="ada.example@cpcc.edu"/>\n'
                                                   '        <attribute name="saved"')
    out = sanitize_fprg(src)
    assert "@cpcc.edu" not in out
    assert 'name="note" value=""' in out
    assert "Read the inputs email [email removed]" in out


def test_program_structure_unchanged():
    src = _fprg()
    out = sanitize_fprg(src)
    tags = lambda s: re.findall(r"<\s*/?\s*([A-Za-z]+)", s)  # noqa: E731
    assert tags(out) == tags(src)
    for kept in ('<declare name="length" type="Real"', '<input variable="length"/>',
                 '<comment text="Read the inputs"/>', "&quot;Enter length:&quot;"):
        assert kept in out


@pytest.mark.parametrize("src", [
    f'<flowgorithm><attributes><attribute name="authors" value="{NAME}"/>\n<body><comment text="x ada.example@cpcc.edu"',
    f"<flowgorithm><attributes><attribute value='{NAME}' name='Authors'/><<>>",
    f'<attribute name="authors"\n value="{NAME}" />  garbage & < unclosed',
])
def test_malformed_xml_still_stripped_without_crash(src):
    out = sanitize_fprg(src)
    assert NAME not in out and "@cpcc.edu" not in out


def test_hostile_doctype_and_entities_not_expanded():
    bomb = ('<?xml version="1.0"?>\n<!DOCTYPE lolz [\n<!ENTITY lol "lol">\n'
            '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">\n'
            f'<!ENTITY who "{NAME}">\n<!ENTITY xxe SYSTEM "file:///etc/passwd">\n]>\n'
            '<flowgorithm fileversion="4.2"><attributes>'
            '<attribute name="authors" value="&who;"/></attributes>'
            '<function name="Main"><body><comment text="&lol2; &xxe;"/></body></function></flowgorithm>')
    out = sanitize_fprg(bomb)
    assert "DOCTYPE" not in out and "ENTITY" not in out
    assert NAME not in out and "/etc/passwd" not in out
    assert len(out) <= len(bomb)  # never grows: nothing is expanded
    assert 'name="authors" value=""' in out


def test_empty_and_plain_text_pass_through():
    assert sanitize_fprg("") == ""
    assert sanitize_fprg("no xml here") == "no xml here"


async def test_gateway_call_never_contains_author(mocker):
    grade = fg.FlowgorithmGrade(requirements_summary="s", deductions=[], final_grade=50, overall_feedback="ok")
    structured = mocker.patch("cqc_cpcc.utilities.AI.llm_gateway.structured",
                              new_callable=AsyncMock, return_value=grade)
    await fg.grade_flowgorithm("Area", "| Criterion | Points |", _fprg(" ada.example@cpcc.edu"),
                               "Student A_1", "50")
    sent = repr(structured.call_args)
    assert NAME not in sent and STAMP not in sent and "@cpcc.edu" not in sent
    assert '<declare name=\\"length\\"' in sent or '<declare name="length"' in sent
