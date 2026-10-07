#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Every Streamlit page script loads without raising.

Catches import-time breakage such as a mis-cased module name, which macOS's
case-insensitive filesystem does not hide from Python's import system.
"""

from pathlib import Path

import pytest

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

PAGES = Path(__file__).resolve().parents[2] / "src" / "cqc_streamlit_app" / "pages"


@pytest.mark.unit
@pytest.mark.parametrize("page", ["2_Give_Feedback.py"])
def test_page_loads_without_exception(page):
    app = AppTest.from_file(str(PAGES / page), default_timeout=60).run()
    assert not app.exception, [e.value for e in app.exception]
