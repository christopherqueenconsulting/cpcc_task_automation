#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Pytest fixtures and configuration for E2E tests.

This conftest file provides:
- Streamlit app server fixture that runs in test mode
- Playwright browser fixtures for testing
- Cleanup and teardown logic
"""

import os
import subprocess
import time
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright


@pytest.fixture(scope="session")
def streamlit_app_url():
    """Start Streamlit app in test mode and return URL.
    
    The app runs on port 8502 to avoid conflicts with dev instances.
    CQC_TEST_MODE environment variable is set to enable deterministic responses.
    """
    # Set test mode before starting app
    env = os.environ.copy()
    env["CQC_TEST_MODE"] = "true"
    # Disable API key requirement check for test mode
    env["OPENAI_API_KEY"] = "test-key-not-used"
    
    # Start Streamlit in background
    process = subprocess.Popen(
        [
            "poetry", "run", "streamlit", "run", 
            "src/cqc_streamlit_app/Home.py",
            "--server.port", "8502",
            "--server.headless", "true",
            "--server.fileWatcherType", "none"  # Disable file watcher for stability
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    # Wait for app to start (give it up to 10 seconds)
    app_url = "http://localhost:8502"
    max_wait = 10
    for i in range(max_wait):
        try:
            import urllib.request
            urllib.request.urlopen(app_url)
            print(f"Streamlit app started successfully on {app_url}")
            break
        except Exception:
            if i < max_wait - 1:
                time.sleep(1)
            else:
                process.terminate()
                raise RuntimeError(f"Failed to start Streamlit app after {max_wait} seconds")
    
    yield app_url
    
    # Cleanup: terminate Streamlit process
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    
    print("Streamlit app stopped")


@pytest_asyncio.fixture
async def browser() -> Browser:
    """Create a Playwright browser instance for the session."""
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(
        headless=True,
        args=['--disable-dev-shm-usage']  # Prevent issues in CI
    )
    yield browser
    await browser.close()
    await playwright.stop()


@pytest_asyncio.fixture
async def page(browser: Browser) -> Page:
    """Create a new browser page for each test."""
    context = await browser.new_context(
        viewport={'width': 1280, 'height': 720},
        locale='en-US'
    )
    page = await context.new_page()
    yield page
    await context.close()
