"""Shared utilities: logging, text normalization, retries, time."""

from webval.utils.logging import get_logger, setup_logging
from webval.utils.proxy import detect_proxy
from webval.utils.retry import retry_async
from webval.utils.text import fuzzy_ratio, normalize_text, sha256_file, utc_now_iso

__all__ = [
    "detect_proxy",
    "fuzzy_ratio",
    "get_logger",
    "normalize_text",
    "retry_async",
    "setup_logging",
    "sha256_file",
    "utc_now_iso",
]
