"""Language utility helpers used by core and UI modules."""

#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import os

EXTENSION_TO_LANGUAGES = {
    "py": ["python"],
    "pyw": ["python"],
    "c": ["c"],
    "h": ["c", "cpp"],
    "cpp": ["cpp"],
    "cc": ["cpp"],
    "cxx": ["cpp"],
    "hpp": ["cpp"],
    "hh": ["cpp"],
    "java": ["java"],
    "class": ["java"],
    "js": ["javascript"],
    "mjs": ["javascript"],
    "cjs": ["javascript"],
    "jsx": ["javascript", "react"],
    "ts": ["typescript"],
    "tsx": ["typescript", "react"],
    "html": ["html"],
    "htm": ["html"],
    "xml": ["xml"],
    "svg": ["xml"],
    "css": ["css"],
    "scss": ["scss"],
    "sass": ["sass"],
    "less": ["less"],
    "sh": ["bash"],
    "bash": ["bash"],
    "zsh": ["bash"],
    "fish": ["bash"],
    "json": ["json"],
    "json5": ["json"],
    "yaml": ["yaml"],
    "yml": ["yaml"],
    "toml": ["toml"],
    "ini": ["ini"],
    "env": ["dotenv"],
    "conf": ["apacheconf", "nginx"],
    "md": ["markdown"],
    "markdown": ["markdown"],
    "txt": ["text"],
    "sql": ["sql"],
    "psql": ["sql"],
    "go": ["go"],
    "mod": ["go"],
    "rs": ["rust"],
    "rb": ["ruby"],
    "php": ["php"],
    "swift": ["swift"],
    "kt": ["kotlin"],
    "kts": ["kotlin"],
    "cs": ["csharp"],
    "m": ["objective-c", "matlab"],
    "mm": ["objective-c"],
    "dart": ["dart"],
    "r": ["r"],
    "R": ["r"],
    "scala": ["scala"],
    "groovy": ["groovy"],
    "hs": ["haskell"],
    "lua": ["lua"],
    "pl": ["perl", "prolog"],
    "pm": ["perl"],
    "ps1": ["powershell"],
    "bat": ["batch"],
    "cmd": ["batch"],
    "asm": ["assembly"],
    "s": ["assembly"],
    "dockerfile": ["docker"],
    "docker": ["docker"],
    "makefile": ["makefile"],
    "mk": ["makefile"],
    "graphql": ["graphql"],
    "gql": ["graphql"],
    "proto": ["protobuf"],
    "tex": ["latex"],
    "tf": ["hcl"],
    "tfvars": ["hcl"],
    "vim": ["vim"],
    "zig": ["zig"],
    "wasm": ["wasm"],
    "log": ["log"],
}


def get_file_extension_from_filepath(file_path: str, remove_leading_dot: bool = False) -> str:
    """Extract and normalize file extension from a file path."""
    basename = os.path.basename(file_path)
    _, file_extension = os.path.splitext(basename)

    if remove_leading_dot and file_extension.startswith("."):
        file_extension = file_extension[1:]

    return file_extension.lower() if file_extension else file_extension


def get_language_from_file_path(file_path: str) -> str:
    """Return a best-fit language tag from file extension."""
    file_extension = get_file_extension_from_filepath(file_path, True)
    langs = EXTENSION_TO_LANGUAGES.get(file_extension.lower(), [])
    return langs[0] if langs else "text"

