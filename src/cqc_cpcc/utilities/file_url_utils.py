"""Core URL/file helper utilities shared by app and non-UI modules."""

#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import os
import re
import tempfile
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlparse

import httpx

from cqc_cpcc.utilities.logger import logger


def parse_google_drive_url(url: Optional[str]) -> Optional[str]:
    """Parse a Google Drive URL and return the file ID when present."""
    if not url or "drive.google.com" not in url:
        return None

    match = re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
    if match:
        return match.group(1)

    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    if "id" in params and params["id"]:
        return params["id"][0]

    return None


def download_file_from_url(url: str, filename_hint: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """Download a file from a URL and save to a temporary path."""
    try:
        file_id = parse_google_drive_url(url)
        if file_id:
            download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
            logger.info(f"Detected Google Drive file ID: {file_id}")
        else:
            download_url = url

        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            response = client.get(download_url)
            response.raise_for_status()

            content_disposition = response.headers.get("content-disposition", "")
            filename = None
            if content_disposition:
                match = re.search(r'filename="?([^"]+)"?', content_disposition)
                if match:
                    filename = match.group(1)

            if not filename:
                parsed_url = urlparse(url)
                filename = os.path.basename(parsed_url.path) or filename_hint or "downloaded_file"

            if "." not in filename:
                content_type = response.headers.get("content-type", "").lower().split(";")[0].strip()
                mime_to_ext = {
                    "application/pdf": ".pdf",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                    "application/msword": ".doc",
                    "text/plain": ".txt",
                    "application/zip": ".zip",
                    "application/x-zip-compressed": ".zip",
                    "text/html": ".html",
                    "application/json": ".json",
                }
                extension = mime_to_ext.get(content_type)
                if extension:
                    filename += extension
                else:
                    logger.warning(f"Could not determine file extension for content-type: {content_type}")

            file_extension = os.path.splitext(filename)[1] or ".tmp"
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=file_extension)
            temp_file.write(response.content)
            temp_file.close()

            logger.info(f"Successfully downloaded file from URL: {url} -> {temp_file.name}")
            return filename, temp_file.name

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error downloading file from URL {url}: {e}")
        return None
    except httpx.TimeoutException:
        logger.error(f"Timeout downloading file from URL: {url}")
        return None
    except Exception as e:
        logger.error(f"Error downloading file from URL {url}: {e}", exc_info=True)
        return None


def sanitize_zip_filename(course_name: str, timestamp: str) -> str:
    """Build a deterministic ZIP filename from course metadata."""

    def _normalize_component(value: str) -> str:
        sanitized = value.replace(" ", "_")
        sanitized = re.sub(r'[<>:"/\\|?*]', "", sanitized)
        sanitized = re.sub(r"_+", "_", sanitized)
        return sanitized.strip("_")

    def _build_flexible_token_pattern(token: str) -> str:
        token_compact = re.sub(r"[\s_-]", "", token)
        return r"[\s_-]*".join(re.escape(ch) for ch in token_compact)

    course_parts = course_name.split("_") if course_name else []
    idx = 0
    base_course_id = "Course"
    section_token = ""

    if course_parts:
        if (
            len(course_parts) > 1
            and re.fullmatch(r"[A-Za-z]{2,}", course_parts[0])
            and re.fullmatch(r"\d{3}", course_parts[1])
        ):
            base_course_id = f"{course_parts[0]}_{course_parts[1]}"
            idx = 2
        else:
            base_course_id = course_parts[0]
            idx = 1

        if idx < len(course_parts) and re.fullmatch(r"[A-Za-z]\d+", course_parts[idx]):
            section_token = course_parts[idx]
            idx += 1

    assignment_name = "_".join(course_parts[idx:]) if idx < len(course_parts) else "Assignment"

    course_prefix_pattern = r"^\s*" + _build_flexible_token_pattern(base_course_id)
    if section_token:
        course_prefix_pattern += r"(?:[\s_-]*" + _build_flexible_token_pattern(section_token) + r")?"
    course_prefix_pattern += r"[\s_:-]*"

    assignment_name = re.sub(
        course_prefix_pattern,
        "",
        assignment_name,
        count=1,
        flags=re.IGNORECASE,
    ).strip(" _-:")

    if not assignment_name:
        assignment_name = "Assignment"

    parts = [_normalize_component(base_course_id)]
    if section_token:
        parts.append(_normalize_component(section_token))
    parts.append(_normalize_component(assignment_name))

    stem = "_".join([part for part in parts if part])
    return f"{stem}_Feedback_{timestamp}.zip"

