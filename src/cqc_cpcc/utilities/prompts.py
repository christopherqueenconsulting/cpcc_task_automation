"""Shared console prompt helpers.

The project has several hand-rolled copies of the same "print a menu, read
``input()``, recurse on bad input" idiom. These helpers exist so new code has one
implementation to reuse; the pre-existing copies are intentionally left alone.

Every prompt writes through ``logger`` rather than ``print`` (ruff ``T201``).
"""

from cqc_cpcc.utilities.logger import logger

_YES_VALUES = {"y", "yes", "true", "1"}
_NO_VALUES = {"n", "no", "false", "0"}


def prompt_yes_no(question: str, default: bool = True) -> bool:
    """Ask a yes/no question. An empty answer takes ``default``."""
    suffix = "[Y/n]" if default else "[y/N]"

    while True:
        answer = input(f"{question} {suffix}: ").strip().lower()
        if not answer:
            return default
        if answer in _YES_VALUES:
            return True
        if answer in _NO_VALUES:
            return False
        logger.warning("Please answer yes or no.")


def prompt_menu(question: str, options: list[str], default_index: int = 0) -> int:
    """Show a numbered menu and return the zero-based index of the choice."""
    if not options:
        raise ValueError("prompt_menu requires at least one option")

    while True:
        logger.info(question)
        for position, option in enumerate(options, start=1):
            logger.info("%s: %s", position, option)

        answer = input(f"Enter your selection [{default_index + 1}]: ").strip()
        if not answer:
            return default_index

        try:
            selection = int(answer)
        except ValueError:
            logger.warning("Invalid selection.")
            continue

        if 1 <= selection <= len(options):
            return selection - 1

        logger.warning("Invalid selection.")


def parse_index_selection(answer: str, count: int) -> list[int] | None:
    """Parse ``all`` / ``1,3,5`` / ``2-4`` into sorted zero-based indexes.

    Returns ``None`` when the answer cannot be parsed or references an item
    outside ``1..count`` so the caller can re-prompt. An explicit ``none`` (or an
    empty selection) returns an empty list, which is a valid "select nothing".
    """
    normalized = (answer or "").strip().lower()
    if not normalized:
        return None
    if normalized == "all":
        return list(range(count))
    if normalized == "none":
        return []

    selected: set[int] = set()
    for chunk in normalized.replace(" ", "").split(","):
        if not chunk:
            continue
        try:
            if "-" in chunk:
                start_text, _, end_text = chunk.partition("-")
                start, end = int(start_text), int(end_text)
                if start > end:
                    return None
                candidates = range(start, end + 1)
            else:
                candidates = [int(chunk)]
        except ValueError:
            return None

        for candidate in candidates:
            if not 1 <= candidate <= count:
                return None
            selected.add(candidate - 1)

    return sorted(selected)


# Returned by prompt_index_selection when the caller offered an expand keyword and
# the user typed it, meaning "show me the wider list instead".
EXPAND = object()


def prompt_index_selection(
        question: str,
        labels: list[str],
        default_indexes: list[int] | None = None,
        expand_keyword: str | None = None,
        expand_hint: str | None = None,
):
    """Show a numbered list and return the selected zero-based indexes.

    When ``expand_keyword`` is given and the user types it, :data:`EXPAND` is
    returned so the caller can re-prompt against a longer list.
    """
    if not labels:
        return []

    default_indexes = [
        index for index in (default_indexes or []) if 0 <= index < len(labels)
    ]

    while True:
        logger.info(question)
        for position, label in enumerate(labels, start=1):
            marker = "*" if (position - 1) in default_indexes else " "
            logger.info("%s %s: %s", marker, position, label)
        logger.info("Enter 'all', 'none', or numbers such as 1,3,5 or 2-4.")
        if expand_keyword and expand_hint:
            logger.info(expand_hint)

        default_text = (
            ",".join(str(index + 1) for index in default_indexes)
            if default_indexes
            else "none"
        )
        answer = input(f"Enter your selection [{default_text}]: ").strip()
        if not answer:
            return list(default_indexes)

        if expand_keyword and answer.lower() == expand_keyword.lower():
            return EXPAND

        selection = parse_index_selection(answer, len(labels))
        if selection is None:
            logger.warning(
                "Invalid selection. Valid entries are 1 through %s.", len(labels)
            )
            continue

        return selection
