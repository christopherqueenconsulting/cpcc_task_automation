#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Runtime redaction of student PII for every log line and telemetry event.

This application handles student names, ids, e-mail addresses, submissions and
grades (FERPA-covered education records). Nothing that identifies a student may
reach a log file, the Streamlit log panel, the AI debug dumps, or PostHog.

Two layers:

1. **Structural patterns** that need no prior knowledge: BrightSpace submission
   folder names (``<userid>-<subid> - First Last``), ``ou=``/``qi=``/``db=`` style
   ids, D2L ``mark,<attempt>,<user>`` tokens, e-mail addresses, URL query strings
   and long digit runs (student and BrightSpace user ids). These mirror the
   classes ``scripts/pii_guard.py`` enforces on the repository.
2. **A per-process registry of known student names.** Code that reads a roster,
   a withdrawal record, a submission folder or a BrightSpace learner list calls
   :func:`register_student`; from then on every mention of that name is replaced
   by a stable alias such as ``student_3f9a2c1d``.

Aliases are an HMAC of the normalised name under a secret key that lives only on
this machine (``CQC_PII_ALIAS_KEY``, or a generated ``~/.cqc_cpcc/pii_alias.key``),
so the same student gets the same alias across runs -- which keeps logs useful for
debugging -- but an alias cannot be reversed by hashing a class roster.

Redaction is best-effort defence in depth, not a licence to log student data: call
sites should still prefer :func:`alias` over the raw name.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Any, Iterable

ALIAS_PREFIX = "student_"
ALIAS_KEY_ENV = "CQC_PII_ALIAS_KEY"
ALIAS_KEY_FILE_ENV = "CQC_PII_ALIAS_KEY_FILE"
_DEFAULT_ALIAS_KEY_FILE = Path.home() / ".cqc_cpcc" / "pii_alias.key"

ID_PLACEHOLDER = "<id>"
EMAIL_PLACEHOLDER = "<email>"
REDACTED = "<redacted>"

# A capitalised word as it appears in a name: "Mary", "O'Brien", "Anne-Marie", "J."
_NAME_WORD = r"[A-Z][A-Za-z'.\-]*"

# BrightSpace download folder: "<userid>-<subid> - First Last" (optionally followed by
# " - <submitted date>"). The whole folder label identifies the student.
FOLDER_RE = re.compile(r"\b(\d{4,})-(\d{4,}) - (%s(?: %s){1,4})" % (_NAME_WORD, _NAME_WORD))
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
URL_RE = re.compile(r"\bhttps?://[^\s'\"<>()\]\[]+")
# Query/assignment ids in free text: ou=12345, qi=..., userId=..., "ou": 12345
PARAM_RE = re.compile(
    r"\b(ou|qi|db|ouId|orgUnitId|userId|user_id|userid|uid|ui|ai|sid|studentId|student_id)"
    r"(\s*[=:]\s*['\"]?)(\d{3,})",
    re.IGNORECASE,
)
# D2L onclick handlers carry attempt and user ids: mark,<attempt>,<user> / feedback,<user>
D2L_TOKEN_RE = re.compile(r"\b(markoverall|mark|feedback),\d+(?:,\d+)*")
# Student ids, BrightSpace user ids. Not inside a word or a hex hash.
LONG_NUMBER_RE = re.compile(r"(?<![0-9A-Za-z_])\d{6,}(?![0-9A-Za-z_])")
# Numeric path segments in URLs (/d2l/le/activities/iterator/123456).
_URL_NUMERIC_SEGMENT_RE = re.compile(r"/\d{3,}(?=/|$)")

_lock = threading.RLock()
_known_names: dict[str, str] = {}  # compact variant -> alias
_known_patterns: list[str] = []  # regex for each variant, whitespace-tolerant
_known_tokens: dict[str, str] = {}  # exact id/email tokens -> alias
_names_re: re.Pattern | None = None
_alias_key: bytes | None = None


# --------------------------------------------------------------------------- aliases


def _load_alias_key() -> bytes:
    """Return the machine-local HMAC key, creating it on first use."""
    global _alias_key
    if _alias_key is not None:
        return _alias_key

    env_key = os.getenv(ALIAS_KEY_ENV)
    if env_key:
        _alias_key = env_key.encode("utf-8")
        return _alias_key

    key_path = Path(os.getenv(ALIAS_KEY_FILE_ENV) or _DEFAULT_ALIAS_KEY_FILE)
    try:
        if key_path.exists():
            _alias_key = key_path.read_text(encoding="utf-8").strip().encode("utf-8")
        else:
            key_path.parent.mkdir(parents=True, exist_ok=True)
            new_key = secrets.token_hex(32)
            fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(new_key)
            _alias_key = new_key.encode("utf-8")
    except OSError:
        # Unwritable home directory: aliases are stable for this process only.
        _alias_key = secrets.token_hex(32).encode("utf-8")

    if not _alias_key:
        _alias_key = secrets.token_hex(32).encode("utf-8")
    return _alias_key


def _normalise(value: str) -> str:
    return " ".join(str(value).split()).lower()


def _compact(value: str) -> str:
    """Registry key: lowercase with all whitespace removed ("Doe,Jane" == "Doe, Jane")."""
    return re.sub(r"\s+", "", str(value)).lower()


def alias(value: Any) -> str:
    """Stable, non-reversible alias for a student identifier (name, id or e-mail).

    ``alias("Ada Example")`` always returns the same ``student_xxxxxxxx`` on this
    machine. Empty values return ``"student_unknown"``.
    """
    if value is None or not str(value).strip():
        return ALIAS_PREFIX + "unknown"
    text = str(value)
    if text.startswith(ALIAS_PREFIX):
        return text
    # A registered student keeps one alias whichever spelling is used.
    with _lock:
        known = _known_names.get(_compact(text)) or _known_tokens.get(text.strip().lower())
    if known:
        return known
    digest = hmac.new(_load_alias_key(), _normalise(text).encode("utf-8"), hashlib.sha256)
    return ALIAS_PREFIX + digest.hexdigest()[:8]


def _name_variants(name: str) -> set[str]:
    """Spellings of one name that appear in the systems this app scrapes."""
    cleaned = " ".join(str(name).replace(" ", " ").split())
    if not cleaned:
        return set()

    variants = {cleaned}
    if "," in cleaned:
        # "Last, First Middle" -> "First Middle Last" and "First Last"
        last, _, first = (part.strip() for part in cleaned.partition(","))
        if last and first:
            variants.add(f"{first} {last}")
            variants.add(f"{first.split()[0]} {last}")
            variants.add(f"{last} {first}")
    else:
        words = cleaned.split()
        if len(words) >= 2:
            first, last = words[0], words[-1]
            variants.add(f"{first} {last}")
            variants.add(f"{last}, {first}")
            variants.add(f"{last}, {' '.join(words[:-1])}")
            variants.add(f"{last} {first}")

    # Single words are too ambiguous to scrub globally ("Will", "Grace", "Page").
    return {v for v in variants if len(v.split()) >= 2 and len(v) >= 5}


def register_student(
        name: str | None = None,
        *,
        student_id: Any = None,
        email: str | None = None,
) -> str:
    """Teach the redactor a student's identifiers. Returns the student's alias.

    Call this wherever a roster, withdrawal record, submission folder or learner
    list is read, before anything about that student is logged.
    """
    global _names_re
    canonical = name or email or student_id
    student_alias = alias(canonical)

    with _lock:
        if name:
            changed = False
            for variant in _name_variants(name):
                key = _compact(variant)
                if key not in _known_names:
                    _known_names[key] = student_alias
                    _known_patterns.append(
                        r"\s*".join(re.escape(part) for part in variant.split())
                    )
                    changed = True
            if changed:
                _names_re = None
        for token in (student_id, email):
            if token is not None and len(str(token).strip()) >= 3:
                _known_tokens[str(token).strip().lower()] = student_alias
    return student_alias


def register_students(names: Iterable[str | None]) -> None:
    for name in names or ():
        if name:
            register_student(name)


def clear_registry() -> None:
    """Forget every registered student (tests; or between runs)."""
    global _names_re
    with _lock:
        _known_names.clear()
        _known_patterns.clear()
        _known_tokens.clear()
        _names_re = None


def _reset_alias_key_for_tests() -> None:
    global _alias_key
    _alias_key = None


def _compiled_names() -> re.Pattern | None:
    global _names_re
    with _lock:
        if _names_re is None and _known_patterns:
            body = "|".join(sorted(_known_patterns, key=len, reverse=True))
            _names_re = re.compile(r"(?<![A-Za-z])(?:%s)(?![A-Za-z])" % body, re.IGNORECASE)
        return _names_re


# --------------------------------------------------------------------------- scrubbing


def _scrub_url(match: re.Match) -> str:
    url = match.group(0)
    base, sep, _query = url.partition("?")
    base, _hash, _fragment = base.partition("#")
    lowered = base.lower()
    for marker in ("/viewfile.d2lfile/", "/database/"):
        idx = lowered.find(marker)
        if idx != -1:
            base = base[: idx + len(marker)] + REDACTED
            break
    base = _URL_NUMERIC_SEGMENT_RE.sub("/" + ID_PLACEHOLDER, base)
    return base + ("?" + REDACTED if sep else "")


def _replace_folder(match: re.Match) -> str:
    return register_student(match.group(3), student_id=match.group(1))


def scrub(text: Any) -> str:
    """Return ``text`` with every recognisable student identifier replaced."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return text

    text = FOLDER_RE.sub(_replace_folder, text)

    names_re = _compiled_names()
    if names_re is not None:
        text = names_re.sub(
            lambda m: _known_names.get(_compact(m.group(0)), alias(m.group(0))), text
        )

    with _lock:
        tokens = dict(_known_tokens)
    for token, token_alias in tokens.items():
        if token in text.lower():
            text = re.sub(r"(?<![\w@.])%s(?![\w@])" % re.escape(token), token_alias, text,
                          flags=re.IGNORECASE)

    text = EMAIL_RE.sub(EMAIL_PLACEHOLDER, text)
    text = URL_RE.sub(_scrub_url, text)
    text = PARAM_RE.sub(lambda m: m.group(1) + m.group(2) + ID_PLACEHOLDER, text)
    text = D2L_TOKEN_RE.sub(lambda m: m.group(1) + "," + ID_PLACEHOLDER, text)
    text = LONG_NUMBER_RE.sub(ID_PLACEHOLDER, text)
    return text


_SENSITIVE_KEY_PARTS = ("key", "token", "secret", "password", "passwd", "cookie", "authorization")


def scrub_obj(obj: Any, _depth: int = 0) -> Any:
    """Recursively scrub strings inside dicts/lists/tuples; mask secret-named keys."""
    if _depth > 20:
        return REDACTED
    if isinstance(obj, str):
        return scrub(obj)
    if isinstance(obj, dict):
        cleaned = {}
        for key, value in obj.items():
            key_text = str(key)
            if any(part in key_text.lower() for part in _SENSITIVE_KEY_PARTS):
                cleaned[scrub(key_text)] = "***REDACTED***"
            else:
                cleaned[scrub(key_text)] = scrub_obj(value, _depth + 1)
        return cleaned
    if isinstance(obj, tuple):
        return tuple(scrub_obj(item, _depth + 1) for item in obj)
    if isinstance(obj, (list, set, frozenset)):
        return [scrub_obj(item, _depth + 1) for item in obj]
    return obj


# --------------------------------------------------------------------------- logging


class PiiRedactionFilter(logging.Filter):
    """Rewrites each record's message (and traceback) through :func:`scrub`.

    Attach to handlers, not loggers: a handler filter sees records from every logger
    that propagates to it (Selenium, urllib3, ...), a logger filter only its own.
    """

    _plain_formatter = logging.Formatter()

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "_cqc_pii_scrubbed", False):
            return True
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        record.msg = scrub(message)
        record.args = None

        if record.exc_info and not record.exc_text:
            try:
                record.exc_text = self._plain_formatter.formatException(record.exc_info)
            except Exception:
                record.exc_text = None
        if record.exc_text:
            record.exc_text = scrub(record.exc_text)
        if record.stack_info:
            record.stack_info = scrub(record.stack_info)
        record._cqc_pii_scrubbed = True
        return True


_FILTER = PiiRedactionFilter()


def install_log_redaction(*handlers: logging.Handler) -> None:
    """Attach the shared redaction filter to ``handlers`` (idempotent).

    With no arguments, attaches to every handler on the root logger.
    """
    targets = handlers or tuple(logging.getLogger().handlers)
    for handler in targets:
        if _FILTER not in handler.filters:
            handler.addFilter(_FILTER)
