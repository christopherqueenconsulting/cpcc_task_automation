#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Utilities for ZIP-based student batch grading.

This module provides utilities for:
1. Extracting student submissions from ZIP files (folder-based)
2. Token estimation for preprocessing detection
3. File prioritization
4. Building submission text for grading

IMPORTANT: This module does NOT truncate student code. Large submissions
are handled via preprocessing (see openai_client.py).

Used by both legacy and rubric-based exam grading tabs.
"""

import os
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from random import randint

from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.utils import read_file


# Token estimation constants
# GPT-5 family models have 128K context window
GPT5_CONTEXT_WINDOW = 128_000
# Target 60-70% for input to leave room for rubric/system text and output
DEFAULT_INPUT_TOKEN_BUDGET_RATIO = 0.65
DEFAULT_MAX_INPUT_TOKENS = int(GPT5_CONTEXT_WINDOW * DEFAULT_INPUT_TOKEN_BUDGET_RATIO)  # ~83K tokens

# Rough token estimation: 1 token ≈ 4 characters for English text
# This is conservative (OpenAI estimates ~1.3 tokens per word, ~4 chars per word)
CHARS_PER_TOKEN = 4

# Noise directories and files to ignore
IGNORE_DIRECTORIES = {
    '__MACOSX',
    '.git',
    '.svn',
    '.hg',
    'node_modules',
    '.venv',
    'venv',
    '__pycache__',
    '.pytest_cache',
    '.idea',
    '.vscode',
    'target',
    'build',
    'dist',
}

IGNORE_FILE_PREFIXES = {
    '._',  # macOS metadata files
    '.DS_Store',
    'Thumbs.db',
}

# File extensions by priority (higher priority = more relevant for grading)
FILE_PRIORITY = {
    # Highest priority: source code
    '.java': 100,
    '.py': 100,
    '.cpp': 100,
    '.c': 100,
    '.js': 90,
    '.ts': 90,
    '.cs': 90,
    '.sas': 90,
    # Medium priority: markup/config
    '.txt': 50,
    '.md': 50,
    '.xml': 40,
    '.json': 40,
    '.yaml': 40,
    '.yml': 40,
    # Lower priority: documents
    '.docx': 30,
    '.pdf': 30,
    '.html': 20,
    # Data files
    '.csv': 10,
    '.xlsx': 10,
}

BINARY_EXTENSIONS = {
    '.exe', '.dll', '.so', '.dylib',
    '.class', '.jar', '.war',
    '.zip', '.tar', '.gz', '.7z',
    '.jpg', '.jpeg', '.png', '.gif', '.bmp',
    '.mp3', '.mp4', '.avi', '.mov',
    '.bin', '.dat',
}


@dataclass
class StudentSubmission:
    """Represents a single student's submission extracted from a ZIP.
    
    Attributes:
        student_id: Unique identifier (folder name or derived)
        student_name: Display name
        files: Dict mapping filename to temp file path (created with delete=False)
        total_chars: Total character count across all files
        estimated_tokens: Estimated token count
    
    Note:
        Temporary files in the `files` dict are created with `delete=False` and must
        be manually cleaned up by the caller after use. This is by design to allow
        the files to be read multiple times during grading. Callers should use
        `os.unlink(filepath)` to remove temp files when done.
        
        IMPORTANT: This class no longer tracks truncation. Large submissions are
        handled via preprocessing (see openai_client.py), not truncation.
    """
    student_id: str
    student_name: str
    files: dict[str, str]  # filename -> temp_file_path
    total_chars: int = 0
    estimated_tokens: int = 0
    is_truncated: bool = False
    omitted_files: list[str] = field(default_factory=list)


def estimate_tokens(text: str) -> int:
    """Estimate token count for a text string.
    
    Uses a conservative approximation of 1 token per 4 characters.
    This is slightly pessimistic to provide safety margin.
    
    Args:
        text: Input text to estimate
        
    Returns:
        Estimated token count
    """
    return len(text) // CHARS_PER_TOKEN


def should_ignore_file(filepath: str) -> bool:
    """Check if a file should be ignored based on path or name.
    
    Args:
        filepath: File path to check
        
    Returns:
        True if file should be ignored
    """
    path = Path(filepath)
    
    # Check directory names
    for part in path.parts:
        if part in IGNORE_DIRECTORIES:
            return True
    
    # Check filename prefixes
    filename = path.name
    for prefix in IGNORE_FILE_PREFIXES:
        if filename.startswith(prefix):
            return True
    
    # Check binary extensions
    ext = path.suffix.lower()
    if ext in BINARY_EXTENSIONS:
        return True
    
    return False


def get_file_priority(filepath: str) -> int:
    """Get priority score for a file based on extension.
    
    Higher scores = more relevant for grading.
    
    Args:
        filepath: File path
        
    Returns:
        Priority score (0-100)
    """
    ext = Path(filepath).suffix.lower()
    return FILE_PRIORITY.get(ext, 0)


def extract_student_submissions_from_zip(
    zip_path: str,
    accepted_file_types: list[str],
    max_tokens_per_student: int = DEFAULT_MAX_INPUT_TOKENS,
) -> dict[str, StudentSubmission]:
    """Extract student submissions from a ZIP file with token estimation.
    
    Parses ZIP into per-student submission units based on folder structure.
    Provides token estimates but does NOT truncate files. Large submissions
    are handled via preprocessing (see openai_client.py).
    
    Expected ZIP structure:
        submission.zip/
            Student_Name_1/
                file1.java
                file2.java
            Student_Name_2/
                file1.py
                file2.py
    
    Or with delimiter pattern (BrightSpace format):
        submission.zip/
            Assignment - Student Name/
                file.java
    
    Args:
        zip_path: Path to ZIP file
        accepted_file_types: List of acceptable file extensions (e.g., ['.java', '.txt'])
        max_tokens_per_student: Token limit (used for logging only, not enforced)
        
    Returns:
        Dict mapping student_id to StudentSubmission
        
    Raises:
        ValueError: If ZIP is empty or malformed
        
    Note:
        This function no longer truncates. The max_tokens_per_student parameter
        is kept for backward compatibility but only used for warnings.
    """
    if not zip_path.endswith('.zip'):
        raise ValueError(f"Not a ZIP file: {zip_path}")
    
    students_data: dict[str, StudentSubmission] = {}
    
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        # First pass: analyze ZIP structure and detect wrapper folders
        all_paths = [f.filename for f in zip_ref.infolist() if not f.is_dir()]
        
        # Check if there's a common wrapper folder
        # e.g., "Programming Exam 1/Student1/file.java" -> wrapper is "Programming Exam 1"
        wrapper_folder = None
        if all_paths:
            # Find common prefix path
            common_parts = []
            first_path_parts = all_paths[0].split('/')
            
            for i, part in enumerate(first_path_parts[:-1]):  # Exclude filename
                if all(p.split('/')[i] == part if len(p.split('/')) > i else False for p in all_paths):
                    common_parts.append(part)
                else:
                    break
            
            # If all files share a common top-level folder, treat it as wrapper
            if common_parts and len(common_parts) >= 1:
                # Check if this is likely a wrapper folder (not a student folder)
                # Heuristic: if there are multiple second-level folders, first level is wrapper
                # BUT: ignore noise folders like __MACOSX, node_modules, etc.
                second_level_folders = set()
                for path in all_paths:
                    # Skip paths that should be ignored (noise files)
                    if should_ignore_file(path):
                        continue
                    
                    parts = path.split('/')
                    if len(parts) > len(common_parts) + 1:
                        folder_name = parts[len(common_parts)]
                        # Don't count noise directories
                        if folder_name not in IGNORE_DIRECTORIES:
                            second_level_folders.add(folder_name)
                
                if len(second_level_folders) > 1:
                    wrapper_folder = '/'.join(common_parts)
                    logger.info(f"Detected wrapper folder in ZIP: '{wrapper_folder}'")
        
        # Second pass: group files by student folder
        student_files: dict[str, list[tuple[str, str]]] = {}  # student_id -> [(filename, zip_path)]
        
        for file_info in zip_ref.infolist():
            # Skip directories
            if file_info.is_dir():
                continue
            
            file_name = os.path.basename(file_info.filename)
            directory_name = os.path.dirname(file_info.filename)
            
            # Skip files in root (no student folder)
            if not directory_name:
                logger.debug(f"Skipping file in root: {file_name}")
                continue
            
            # Check if should ignore
            if should_ignore_file(file_info.filename):
                logger.debug(f"Ignoring file: {file_info.filename}")
                continue
            
            # Remove wrapper folder from directory path if present
            if wrapper_folder and directory_name.startswith(wrapper_folder):
                directory_name = directory_name[len(wrapper_folder):].lstrip('/')
                # If directory is now empty (file was directly in wrapper), skip
                if not directory_name:
                    logger.debug(f"Skipping file directly in wrapper folder: {file_name}")
                    continue
            
            # Parse student identifier from directory
            # Handle "Assignment - Student Name" format (BrightSpace)
            # BrightSpace format is typically: "ID - Student Name - Timestamp"
            # or "Assignment - Student Name"
            # We want the second part (index 1) which is the student name
            folder_name_delimiter = ' - '
            if folder_name_delimiter in directory_name:
                parts = directory_name.split(folder_name_delimiter)
                if len(parts) >= 2:
                    # Take the second part (index 1) as student name
                    student_id = parts[1]
                else:
                    # Fallback if split doesn't work as expected
                    student_id = directory_name.split('/')[0].split('\\')[0]
            else:
                # Use top-level folder name as student ID
                # Handle nested paths: "Student1/subfolder/file.java" -> "Student1"
                student_id = directory_name.split('/')[0].split('\\')[0]
            
            # Check file extension
            # accepted_file_types can contain extensions with or without dots
            # e.g., ['java', 'txt'] or ['.java', '.txt']
            # Normalize by removing dots for comparison
            file_ext = Path(file_name).suffix.lower().lstrip('.')  # e.g., 'java' from 'Main.java'
            
            # Normalize accepted types (remove dots if present)
            normalized_accepted = [ext.lstrip('.') for ext in accepted_file_types]
            
            if file_ext not in normalized_accepted:
                logger.debug(f"Skipping file with unaccepted type: {file_name} (extension: .{file_ext}, accepted: {normalized_accepted})")
                continue
            
            # Skip files with ignored prefixes
            if any(file_name.startswith(prefix) for prefix in IGNORE_FILE_PREFIXES):
                logger.debug(f"Skipping ignored file: {file_name}")
                continue
            
            # Add to student's file list
            if student_id not in student_files:
                student_files[student_id] = []
            student_files[student_id].append((file_name, file_info.filename))
        
        logger.info(f"Found {len(student_files)} potential student folders after parsing")
        
        # Second pass: extract and budget tokens per student
        for student_id, files_list in student_files.items():
            # Sort files by priority (highest first)
            files_list.sort(key=lambda f: get_file_priority(f[0]), reverse=True)
            
            submission = StudentSubmission(
                student_id=student_id,
                student_name=student_id,  # Use as display name
                files={},
            )
            
            total_tokens = 0
            
            for file_name, zip_file_path in files_list:
                # Extract to temp file
                with zip_ref.open(zip_file_path) as file:
                    prefix = f'from_zip_{randint(1000, 100000000)}_'
                    suffix = Path(file_name).suffix
                    temp_file = tempfile.NamedTemporaryFile(
                        delete=False,
                        prefix=prefix,
                        suffix=suffix
                    )
                    temp_file.write(file.read())
                    temp_file.flush()
                    temp_file_path = temp_file.name
                
                # Read file content and estimate tokens
                try:
                    file_content = read_file(temp_file_path, convert_to_markdown=False)
                    file_tokens = estimate_tokens(file_content)
                    
                    # NO TRUNCATION: Just warn if submission is large
                    if total_tokens + file_tokens > max_tokens_per_student:
                        logger.warning(
                            f"Student {student_id}: Large submission detected "
                            f"(~{total_tokens + file_tokens} tokens). "
                            f"Preprocessing will be used automatically during grading."
                        )
                    
                    # Add file to submission (no budget enforcement)
                    submission.files[file_name] = temp_file_path
                    submission.total_chars += len(file_content)
                    total_tokens += file_tokens
                    
                except Exception as e:
                    logger.error(f"Error reading {file_name} for student {student_id}: {e}")
                    # Clean up temp file
                    os.unlink(temp_file_path)
                    continue
            
            submission.estimated_tokens = total_tokens
            
            if submission.files:
                students_data[student_id] = submission
                logger.info(
                    f"Extracted student '{student_id}': {len(submission.files)} files, "
                    f"~{submission.estimated_tokens} tokens"
                )
            else:
                logger.warning(f"No valid files found for student: {student_id}")
    
    if not students_data:
        # Provide helpful error message
        error_msg = f"No student submissions found in ZIP: {zip_path}\n"
        if wrapper_folder:
            error_msg += f"Detected wrapper folder: '{wrapper_folder}'\n"
        
        # List what was found (use all_paths already collected in with block)
        if all_paths:
            error_msg += f"Found {len(all_paths)} file(s) in ZIP but none matched expected structure.\n"
            error_msg += "Expected structure: Student_Name/file.ext or Assignment - Student Name/file.ext\n"
            error_msg += f"Accepted file types: {', '.join(accepted_file_types)}\n"
            error_msg += f"First few files found:\n"
            for f in all_paths[:5]:
                error_msg += f"  - {f}\n"
            if len(all_paths) > 5:
                error_msg += f"  ... and {len(all_paths) - 5} more\n"
        else:
            error_msg += "ZIP appears to be empty or contains only directories.\n"
        
        raise ValueError(error_msg.strip())
    
    logger.info(f"Extracted {len(students_data)} student submissions from ZIP")
    return students_data


def build_submission_text_with_token_limit(
    files: dict[str, str],
    max_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
    is_truncated: bool = False,
    omitted_files: Optional[list[str]] = None,
) -> str:
    """Build combined submission text from multiple files.
    
    Reads files and combines them with filename headers. No longer enforces
    token limits - large submissions are handled via preprocessing in the
    grading layer.
    
    Args:
        files: Dict mapping filename to temp file path
        max_tokens: Token limit (used for logging only, not enforced)
        is_truncated: Whether files were omitted (adds notice)
        omitted_files: List of omitted filenames to include in notice
        
    Returns:
        Combined submission text
        
    Note:
        This function no longer truncates. The max_tokens parameter is kept
        for backward compatibility but only used for warnings.
    """
    parts = []
    
    if is_truncated:
        omitted_list = omitted_files or []
        omitted_text = "\n".join(f"- {name}" for name in omitted_list)
        notice = (
            "NOTE: Some files were omitted due to size limits.\n"
            f"{omitted_text}\n"
        )
        parts.append(notice)
    total_tokens = 0
    
    # Sort files by priority for consistent ordering
    sorted_files = sorted(files.items(), key=lambda f: get_file_priority(f[0]), reverse=True)
    
    for filename, filepath in sorted_files:
        try:
            content = read_file(filepath, convert_to_markdown=False)
            file_tokens = estimate_tokens(content)
            
            # Warn if large, but don't truncate
            if total_tokens + file_tokens > max_tokens:
                logger.warning(
                    f"Large submission detected (~{total_tokens + file_tokens} tokens). "
                    f"Preprocessing will be used automatically."
                )
            
            # Add file with header (no budget check)
            file_section = f"\n\n{'='*60}\n"
            file_section += f"FILE: {filename}\n"
            file_section += f"{'='*60}\n\n"
            file_section += content
            
            parts.append(file_section)
            total_tokens += file_tokens
            
        except Exception as e:
            logger.error(f"Error reading {filename}: {e}")
            continue
    
    return "\n".join(parts)
