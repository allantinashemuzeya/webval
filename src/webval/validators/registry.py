"""Validator registry: instantiate the default validator set for a context.

Extension point: append custom validators here or pass extras to
``default_validators``. Dispatch order matters only when two validators claim
the same category (first wins).
"""

from __future__ import annotations

from webval.validators.accessibility import AccessibilityValidator
from webval.validators.anchors import AnchorValidator
from webval.validators.base import BaseValidator, ValidationContext
from webval.validators.content import ContentValidator
from webval.validators.downloads import DownloadValidator
from webval.validators.images import ImageValidator
from webval.validators.links import LinkValidator
from webval.validators.metadata import MetadataValidator
from webval.validators.performance import PerformanceValidator
from webval.validators.responsive import ResponsiveValidator
from webval.validators.ui_behavior import UiBehaviorValidator
from webval.validators.video import VideoValidator
from webval.validators.visual import VisualValidator

_DEFAULT_CLASSES: list[type[BaseValidator]] = [
    MetadataValidator,
    LinkValidator,
    AnchorValidator,
    AccessibilityValidator,
    ImageValidator,
    DownloadValidator,
    VideoValidator,
    ResponsiveValidator,
    UiBehaviorValidator,
    PerformanceValidator,
    VisualValidator,
    ContentValidator,  # last: CONTENT/GENERAL catch-all
]


def default_validators(
    ctx: ValidationContext,
    extra: list[type[BaseValidator]] | None = None,
) -> list[BaseValidator]:
    classes = list(extra or []) + _DEFAULT_CLASSES
    return [cls(ctx) for cls in classes]
