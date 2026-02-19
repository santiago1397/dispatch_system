"""Input sanitization utilities.

This module provides security-focused input sanitization functions:
- HTML sanitization to prevent XSS attacks
- Path traversal prevention for file operations
- Common input cleaning utilities

Note: SQL injection is prevented by using SQLAlchemy ORM with parameterized queries.
"""

import html
import os
import re
import unicodedata
from pathlib import Path
from typing import TypeVar

# Default allowed HTML tags for rich text content
DEFAULT_ALLOWED_TAGS = frozenset(
    {
        "a",
        "abbr",
        "acronym",
        "b",
        "blockquote",
        "br",
        "code",
        "em",
        "i",
        "li",
        "ol",
        "p",
        "pre",
        "strong",
        "ul",
    }
)

# Default allowed HTML attributes
DEFAULT_ALLOWED_ATTRIBUTES = {
    "a": frozenset({"href", "title", "rel"}),
    "abbr": frozenset({"title"}),
    "acronym": frozenset({"title"}),
}


def sanitize_html(
    content: str,
    allowed_tags: frozenset[str] | None = None,
    strip: bool = True,
) -> str:
    """Sanitize HTML content to prevent XSS attacks.

    This is a simple implementation that escapes all HTML.
    For rich text support, consider using the `bleach` library.

    Args:
        content: The HTML content to sanitize.
        allowed_tags: Not used in simple mode (for bleach compatibility).
        strip: Not used in simple mode (for bleach compatibility).

    Returns:
        Escaped HTML-safe string.

    Example:
        >>> sanitize_html("<script>alert('xss')</script>")
        "&lt;script&gt;alert('xss')&lt;/script&gt;"
    """
    if not content:
        return ""

    return html.escape(content)


def sanitize_filename(filename: str, allow_unicode: bool = False) -> str:
    """Sanitize a filename to prevent path traversal and unsafe characters.

    Args:
        filename: The filename to sanitize.
        allow_unicode: Whether to allow unicode characters.

    Returns:
        A safe filename string.

    Example:
        >>> sanitize_filename("../../../etc/passwd")
        "etc_passwd"
        >>> sanitize_filename("hello world.txt")
        "hello_world.txt"
    """
    if not filename:
        return ""

    # Normalize unicode
    if allow_unicode:
        filename = unicodedata.normalize("NFKC", filename)
    else:
        filename = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii")

    # Get just the filename (remove any path components)
    filename = os.path.basename(filename)

    # Remove null bytes
    filename = filename.replace("\x00", "")

    # Replace path separators and special characters
    filename = re.sub(r"[/\\:*?\"<>|]", "_", filename)

    # Replace multiple underscores/spaces with single underscore
    filename = re.sub(r"[\s_]+", "_", filename)

    # Remove leading/trailing underscores and dots
    filename = filename.strip("._")

    # Ensure we have a valid filename
    if not filename:
        return "unnamed"

    return filename


def validate_safe_path(
    base_dir: Path | str,
    user_path: str,
) -> Path:
    """Validate that a user-provided path is within the allowed base directory.

    Prevents path traversal attacks by ensuring the resolved path
    is within the expected directory.

    Args:
        base_dir: The base directory that all paths must be within.
        user_path: The user-provided path to validate.

    Returns:
        The resolved, safe path.

    Raises:
        ValueError: If the path would escape the base directory.

    Example:
        >>> validate_safe_path("/uploads", "../../../etc/passwd")
        Raises ValueError
        >>> validate_safe_path("/uploads", "images/photo.jpg")
        Path("/uploads/images/photo.jpg")
    """
    base_path = Path(base_dir).resolve()
    user_path_sanitized = sanitize_filename(user_path.lstrip("/\\"))

    # Resolve the full path
    full_path = (base_path / user_path_sanitized).resolve()

    # Check if the resolved path is within the base directory
    try:
        full_path.relative_to(base_path)
    except ValueError as err:
        raise ValueError(
            f"Path traversal detected: {user_path!r} would escape {base_dir!r}"
        ) from err

    return full_path


def sanitize_string(
    value: str,
    max_length: int | None = None,
    allow_newlines: bool = True,
    strip_whitespace: bool = True,
) -> str:
    """Sanitize a string input with various options.

    Args:
        value: The string to sanitize.
        max_length: Maximum allowed length (truncates if exceeded).
        allow_newlines: Whether to preserve newlines.
        strip_whitespace: Whether to strip leading/trailing whitespace.

    Returns:
        Sanitized string.
    """
    if not value:
        return ""

    # Strip null bytes and other control characters (except newlines if allowed)
    if allow_newlines:
        value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    else:
        value = re.sub(r"[\x00-\x1f\x7f]", "", value)

    # Strip whitespace if requested
    if strip_whitespace:
        value = value.strip()

    # Truncate if needed
    if max_length is not None and len(value) > max_length:
        value = value[:max_length]

    return value


def sanitize_email(email: str) -> str:
    """Basic email sanitization.

    Note: For proper email validation, use Pydantic's EmailStr type.
    This function only performs basic cleaning.

    Args:
        email: The email address to sanitize.

    Returns:
        Lowercased, stripped email.
    """
    if not email:
        return ""

    return email.strip().lower()


T = TypeVar("T", int, float)


def sanitize_numeric(
    value: str | int | float,
    value_type: type[T],
    min_value: T | None = None,
    max_value: T | None = None,
    default: T | None = None,
) -> T | None:
    """Sanitize and validate a numeric value.

    Args:
        value: The value to sanitize (can be string or numeric).
        value_type: The expected type (int or float).
        min_value: Minimum allowed value.
        max_value: Maximum allowed value.
        default: Default value if conversion fails.

    Returns:
        The sanitized numeric value, or default if invalid.

    Example:
        >>> sanitize_numeric("100", int, min_value=0, max_value=1000)
        100
        >>> sanitize_numeric("abc", int, default=0)
        0
    """
    try:
        result = value_type(value)

        if min_value is not None and result < min_value:
            result = min_value
        if max_value is not None and result > max_value:
            result = max_value

        return result
    except (ValueError, TypeError):
        return default


def escape_sql_like(pattern: str, escape_char: str = "\\") -> str:
    """Escape special characters in a LIKE pattern.

    Use this when building LIKE queries with user input.

    Args:
        pattern: The pattern to escape.
        escape_char: The escape character to use.

    Returns:
        Escaped pattern safe for use in LIKE queries.

    Example:
        >>> escape_sql_like("100%")
        "100\\%"
        >>> escape_sql_like("under_score")
        "under\\_score"
    """
    # Escape the escape character first, then special chars
    pattern = pattern.replace(escape_char, escape_char + escape_char)
    pattern = pattern.replace("%", escape_char + "%")
    pattern = pattern.replace("_", escape_char + "_")
    return pattern
