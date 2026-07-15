"""Config-driven settings.

Precedence (highest wins):
    1. Environment variables / .env  (WEBVAL_ prefix, ``__`` for nesting)
    2. Project YAML passed via --config
    3. Bundled config/default.yaml defaults mirrored in the model defaults

Secrets (auth credentials) are only ever read from the environment layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SiteConfig(BaseModel):
    base_url: str = "https://usim.preprod.sbx.us.pluvicto.com/"
    allowed_hosts: list[str] = Field(default_factory=list)
    ignore_https_errors: bool = True
    max_pages: int = 50
    max_depth: int = 3
    exclude_patterns: list[str] = Field(default_factory=lambda: ["logout", "signout"])

    @model_validator(mode="after")
    def _default_allowed_hosts(self) -> SiteConfig:
        if not self.allowed_hosts:
            host = urlparse(self.base_url).netloc
            if host:
                self.allowed_hosts = [host]
        return self


class AuthConfig(BaseModel):
    mode: str = "http_basic"  # http_basic | none
    username: str = ""
    password: SecretStr = SecretStr("")


class DeviceConfig(BaseModel):
    name: str
    playwright_device: str | None = None
    viewport: dict[str, int] | None = None


class BrowserConfig(BaseModel):
    engine: str = "chromium"
    channel: str | None = None  # "chrome" | "msedge" to force the installed browser; None = auto
    # Proxy for corporate networks: "http://host:port". None = auto-detect
    # (HTTPS_PROXY/HTTP_PROXY env vars, then Windows registry proxy) for the
    # bundled Chromium; installed Chrome/Edge channels follow system/PAC
    # settings natively unless this is set explicitly.
    proxy: str | None = None
    headless: bool = True
    timeout_ms: int = 30_000
    navigation_timeout_ms: int = 45_000
    slow_mo_ms: int = 0
    concurrency: int = 4


class PdfConfig(BaseModel):
    ocr_enabled: bool = True  # auto-OCR pages with no text layer; degrades gracefully when no backend exists
    ocr_backend: str = "auto"  # auto | tesseract | rapidocr (pure-pip, no admin rights)
    ocr_dpi: int = 150
    extract_images: bool = True
    min_requirement_length: int = 12
    id_patterns: list[str] = Field(default_factory=lambda: [r"\b(?:REQ|BR|FR|UC|TC)[-_ ]?\d{1,4}\b"])


class PerformanceBudget(BaseModel):
    lcp_budget_ms: int = 4000
    cls_budget: float = 0.25
    ttfb_budget_ms: int = 1800


class VisualConfig(BaseModel):
    enabled: bool = True
    hash_distance_warn: int = 12
    hash_distance_fail: int = 24


class ValidationConfig(BaseModel):
    content_fuzzy_threshold: float = 0.87
    link_timeout_s: int = 15
    retry_attempts: int = 3
    retry_backoff_s: float = 1.5
    external_links: bool = True
    performance: PerformanceBudget = Field(default_factory=PerformanceBudget)
    visual: VisualConfig = Field(default_factory=VisualConfig)


class EvidenceConfig(BaseModel):
    root: str = "runs"
    full_page_screenshots: bool = True
    keep_dom_snapshots: bool = True


class ReportConfig(BaseModel):
    title: str = "Website Validation Report"
    organization: str = "QA / Validation"
    system_under_test: str = ""
    formats: list[str] = Field(default_factory=lambda: ["excel", "html", "json"])
    # Defect-log sheet fields (QA tracker format)
    qa_name: str = "Automated (webval)"
    phase: str = "UAT"
    environment: str = "Preprod"
    round_of_testing: str = "1"


DEFAULT_DEVICES = [
    DeviceConfig(name="Desktop Chrome", viewport={"width": 1440, "height": 900}),
    DeviceConfig(name="iPhone 14", playwright_device="iPhone 14"),
    DeviceConfig(name="iPad", playwright_device="iPad (gen 7)"),
]


class Settings(BaseSettings):
    """Effective run configuration (env > YAML > defaults)."""

    model_config = SettingsConfigDict(
        env_prefix="WEBVAL_",
        env_nested_delimiter="__",
        # .env is parsed exclusively by _env_overrides (verbatim values, BOM-safe,
        # '#'-safe) — not by pydantic's dotenv reader, whose comment rules differ.
        extra="ignore",
    )

    site: SiteConfig = Field(default_factory=SiteConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    devices: list[DeviceConfig] = Field(default_factory=lambda: list(DEFAULT_DEVICES))
    pdf: PdfConfig = Field(default_factory=PdfConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    evidence: EvidenceConfig = Field(default_factory=EvidenceConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)

    def redacted_dump(self) -> dict[str, Any]:
        """Config snapshot safe to embed in the audit manifest."""
        data = self.model_dump(mode="json")
        if "auth" in data:
            data["auth"]["password"] = "***REDACTED***"
            if data["auth"].get("username"):
                data["auth"]["username"] = "***REDACTED***"
        return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_settings(config_path: Path | None = None) -> Settings:
    """Build Settings from bundled defaults + optional project YAML + environment.

    Environment variables (and .env) win over YAML; YAML wins over model defaults.
    """
    yaml_data: dict[str, Any] = {}
    bundled = Path(__file__).resolve().parents[3].parent / "config" / "default.yaml"
    for candidate in (bundled, config_path):
        if candidate and candidate.is_file():
            with open(candidate, encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
            yaml_data = _deep_merge(yaml_data, loaded)
    merged = _deep_merge(yaml_data, _env_overrides())
    return Settings(**merged)


def _env_overrides() -> dict[str, Any]:
    """Collect WEBVAL_* environment variables as a nested dict."""
    import os

    out: dict[str, Any] = {}
    prefix = "WEBVAL_"
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        path = key[len(prefix):].lower().split("__")
        cursor = out
        for part in path[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[path[-1]] = value
    # .env file support for credentials when not exported.
    # utf-8-sig strips the BOM Windows Notepad prepends (a BOM otherwise makes
    # the first line's key unparseable, silently dropping that credential).
    env_file = Path(".env")
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip().lstrip("﻿")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if not key.startswith(prefix) or key in os.environ:
                continue
            # Keep values verbatim (passwords may contain '#', '=', spaces...);
            # only strip a matching pair of surrounding quotes.
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            path = key[len(prefix):].lower().split("__")
            cursor = out
            for part in path[:-1]:
                cursor = cursor.setdefault(part, {})
            cursor.setdefault(path[-1], value)
    return out
