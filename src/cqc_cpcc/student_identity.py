#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Who is who: the BrightSpace classlist, read once per run and kept in memory only.

Attendance used to join BrightSpace activity to MyColleges by name text, which can pair the wrong
student or none. The BrightSpace classlist lists every learner with their name, their Org Defined ID
(the id MyColleges uses) and an internal BrightSpace user id. Activity rows carry that same internal
user id, so the join is exact: activity row -> user id -> classlist row -> Org Defined ID -> MyColleges.
Names are only a cross-check: a row without a trustworthy user id is unresolved and never recorded.

Privacy rules (the owner's, 2026-11-11):
* The name/id table lives only in a :class:`Classlist` object for the length of one run. It is never
  written to disk, never pickled and never logged; :meth:`Classlist.clear` drops it when collection ends.
* Every student the classlist names is registered with the log redactor under an app-generated alias keyed
  by the Org Defined ID, so no log line carries a name or id.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Iterable, Optional

from urllib.parse import unquote

from cqc_cpcc.utilities.pii_redaction import forget_students, register_student

_WORD_RE = re.compile(r"[\w'\-]+")
_FEEDBACK_RE = re.compile(r"\bfeedback,(\d+)")
_ACTOR_USER_RE = re.compile(r"\.user_(\d+)\Z")
# MyColleges roster ids are 6-9 digits (the roster script reads them with \b\d{6,9}\b).
ORG_ID_RE = re.compile(r"^\d{6,9}$")


def name_key(text: str | None) -> frozenset[str]:
    """Order-free whole-word key of a name: ``Last, First`` equals ``First Last``; lone initials are ignored."""
    words = _WORD_RE.findall((text or "").lower().replace("’", "'"))
    return frozenset(word for word in words if len(word) >= 2)


def user_id_from_onclick(onclick: object) -> Optional[str]:
    """The BrightSpace user id in a learner link's ``feedback,<userId>,...`` handler, else None."""
    if not isinstance(onclick, str):
        return None
    match = _FEEDBACK_RE.search(onclick)
    return match.group(1) if match else None


def user_id_from_actor_href(href: object) -> Optional[str]:
    """The user id hidden in a quiz attempt link ``.../evaluation/actor/<base64>?...``, else None.

    The last path segment is base64; decoded once it holds ``<token>.user_<id>``. That is D2L's own link
    encoding and can change without notice, so every step is defensive and a miss is simply None (the
    row is then unresolved and never recorded).
    """
    if not isinstance(href, str) or "/actor/" not in href:
        return None
    segment = unquote(href.split("/actor/", 1)[1].split("?", 1)[0].split("/", 1)[0])
    if not segment:
        return None
    try:
        padded = segment + "=" * (-len(segment) % 4)
        decoded = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeError):
        return None
    match = _ACTOR_USER_RE.search(decoded)
    return match.group(1) if match else None


class ActivityRow(tuple):
    """One completion read from a results table: behaves as ``(name, date_text)`` and also carries ``user_id``.

    The BrightSpace user id (None when the row's links hold none) is what joins the row to the classlist
    exactly. Equality and hashing are those of the plain pair, so code that compares or unpacks the pair is
    unaffected.
    """

    user_id: Optional[str]

    def __new__(cls, name: str, date_text: str, user_id: Optional[str] = None):
        row = super().__new__(cls, (name, date_text))
        row.user_id = user_id
        return row


class Classlist:
    """Session-only identity table. Holds names and ids in memory; refuses to be copied to disk."""

    def __init__(self) -> None:
        self._org_by_user: dict[str, str] = {}
        self._user_by_org: dict[str, str] = {}
        self._key_by_user: dict[str, frozenset[str]] = {}

    def add(self, user_id: str, org_defined_id: str, name: str) -> None:
        """Add one learner. Registers them with the log redactor under an alias keyed by their Org Defined ID."""
        user_id, org_defined_id = str(user_id).strip(), str(org_defined_id).strip()
        if not user_id or not ORG_ID_RE.match(org_defined_id):
            raise ValueError("a classlist row needs a user id and a numeric Org Defined ID")
        if user_id in self._org_by_user and self._org_by_user[user_id] != org_defined_id:
            raise ValueError("a BrightSpace user id appears twice with different Org Defined IDs")
        if org_defined_id in self._user_by_org and self._user_by_org[org_defined_id] != user_id:
            raise ValueError("an Org Defined ID belongs to two BrightSpace user ids")
        key = name_key(name)
        self._user_by_org[org_defined_id] = user_id
        self._org_by_user[user_id] = org_defined_id
        self._key_by_user[user_id] = key
        register_student(name, student_id=org_defined_id, tokens=[user_id], alias_key=org_defined_id)

    def __len__(self) -> int:
        return len(self._org_by_user)

    def __bool__(self) -> bool:
        return bool(self._org_by_user)

    def org_defined_ids(self) -> set[str]:
        return set(self._org_by_user.values())

    def resolve(self, name: str | None, user_id: str | None) -> Optional[str]:
        """The Org Defined ID for one activity row, or None when it cannot be tied to exactly one learner.

        The row must carry its BrightSpace user id and the classlist must know it. When the row also shows a
        name, that name must be the classlist's name for that learner: a mismatch means a mis-read link and is
        never resolved. There is no name-only fallback (a missing or unreadable user id leaves the row unresolved),
        so a student who is not on the classlist can never be mistaken for one who is.
        """
        if not user_id:
            return None
        org = self._org_by_user.get(str(user_id))
        if org is None:
            return None
        row_key = name_key(name)
        if row_key and row_key != self._key_by_user.get(str(user_id)):
            return None
        return org

    def clear(self) -> None:
        """Drop the name/id table and the log redactor's copy of it (collection is over; MyColleges only needs the
        Org Defined IDs already resolved, and logs use aliases keyed by them)."""
        forget_students(self._org_by_user.values())
        self._org_by_user.clear()
        self._user_by_org.clear()
        self._key_by_user.clear()

    def __repr__(self) -> str:  # counts only: never a name or an id
        return "<Classlist %d learner(s)>" % len(self)

    def __getstate__(self):
        raise TypeError("the classlist holds student identities and must never be pickled or persisted")

    def __reduce__(self):
        raise TypeError("the classlist holds student identities and must never be pickled or persisted")


def build_classlist(rows: Iterable[dict]) -> Classlist:
    """A :class:`Classlist` from rows ``{"user": ..., "org": ..., "name": ...}``; a bad row raises ValueError."""
    classlist = Classlist()
    try:
        for row in rows:
            classlist.add(row.get("user"), row.get("org"), row.get("name"))
    except BaseException:
        # Rows added before the bad one are already registered with the log redactor: forget them, so a
        # rejected classlist leaves nothing behind.
        classlist.clear()
        raise
    return classlist
