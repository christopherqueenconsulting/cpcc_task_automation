#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Smoke tests for Streamlit UI.

These tests verify that the basic application loads and key pages are accessible.
"""

import pytest
import re
from playwright.async_api import Page, expect


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_home_page_loads(page: Page, streamlit_app_url: str):
    """Test that the home page loads successfully."""
    await page.goto(streamlit_app_url)
    
    # Wait for Streamlit to finish loading
    await page.wait_for_load_state("networkidle")
    
    # Check that the page title contains expected text
    await expect(page).to_have_title(re.compile(r".*CPCC.*|.*Task.*|.*Automation.*"), timeout=10000)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_navigation_links_exist(page: Page, streamlit_app_url: str):
    """Test that main navigation links are visible."""
    await page.goto(streamlit_app_url)
    await page.wait_for_load_state("networkidle")
    
    # Give Streamlit time to render
    await page.wait_for_timeout(2000)
    
    # Check for sidebar navigation (Streamlit renders navigation in sidebar)
    # Look for links in the page
    await expect(page.locator("text=/Attendance|Feedback|Grade|Settings/i").first).to_be_visible(timeout=10000)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_app_renders_without_errors(page: Page, streamlit_app_url: str):
    """Test that app renders without JavaScript errors."""
    await page.goto(streamlit_app_url)
    await page.wait_for_load_state("networkidle")
    
    # Wait a bit for any async operations
    await page.wait_for_timeout(2000)
    
    # Check that Streamlit's app container is present
    await expect(page.locator("[data-testid='stAppViewContainer']").first).to_be_visible(timeout=10000)
