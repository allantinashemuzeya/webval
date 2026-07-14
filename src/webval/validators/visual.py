"""Phase 16 — visual comparison between specification images and live pages.

Each VISUAL requirement carries the path of an image extracted from the spec
(in ``source.raw_text``). The validator compares it against every full-page
screenshot captured during discovery using a perceptual hash (dHash,
implemented with Pillow only), picks the best match, and renders a
side-by-side + pixel-difference composite as evidence.

Perceptual hashing tolerates resolution/compression differences while still
catching missing sections, swapped imagery, and layout changes. Thresholds
are configurable (validation.visual.hash_distance_warn / _fail).
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from PIL import Image, ImageChops, ImageDraw

from webval.models import Evidence, EvidenceKind, Requirement, RequirementCategory, Status, ValidationResult
from webval.validators.base import BaseValidator

_HASH_SIZE = 16  # 16x16 dHash => 256-bit fingerprint


def dhash(image: Image.Image, hash_size: int = _HASH_SIZE) -> int:
    """Difference hash: robust to scaling/compression, sensitive to layout."""
    gray = image.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    pixels = gray.tobytes()  # L-mode: one byte per pixel, row-major
    bits = 0
    for row in range(hash_size):
        for col in range(hash_size):
            left = pixels[row * (hash_size + 1) + col]
            right = pixels[row * (hash_size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


class VisualValidator(BaseValidator):
    name: ClassVar[str] = "visual"
    categories: ClassVar[frozenset[RequirementCategory]] = frozenset({RequirementCategory.VISUAL})

    async def validate(self, requirement: Requirement) -> ValidationResult:
        if not self.ctx.settings.validation.visual.enabled:
            return self.result(requirement, Status.NOT_TESTED, "Visual comparison disabled by configuration")

        spec_path = Path(requirement.source.raw_text)
        if not spec_path.is_file():
            return self.result(
                requirement, Status.ERROR,
                f"Specification image missing: {spec_path}",
            )
        spec_img = Image.open(spec_path).convert("RGB")
        spec_hash = dhash(spec_img)

        candidates: list[tuple[int, Path, str]] = []
        for page in self.ctx.site_map.pages:
            if not page.screenshot_path:
                continue
            shot_path = self.ctx.store.run_dir / page.screenshot_path
            if not shot_path.is_file():
                continue
            try:
                shot_img = Image.open(shot_path).convert("RGB")
            except OSError:
                continue
            distance = hamming(spec_hash, dhash(shot_img))
            candidates.append((distance, shot_path, page.url))

        if not candidates:
            return self.result(requirement, Status.ERROR, "No page screenshots available for comparison")

        distance, best_path, best_url = min(candidates, key=lambda c: c[0])
        diff_evidence = self._render_diff(requirement, spec_img, best_path)

        cfg = self.ctx.settings.validation.visual
        detail = (
            f"dHash distance {distance}/256 vs {best_url} "
            f"(warn>{cfg.hash_distance_warn}, fail>{cfg.hash_distance_fail})"
        )
        if distance <= cfg.hash_distance_warn:
            return self.result(
                requirement, Status.PASS,
                f"Live page {best_url} visually consistent with the specification image (distance {distance})",
                details=detail, page_url=best_url, evidence=diff_evidence,
            )
        if distance <= cfg.hash_distance_fail:
            return self.result(
                requirement, Status.WARNING,
                f"Visual differences detected against {best_url} (distance {distance}) — review diff",
                details=detail, page_url=best_url, evidence=diff_evidence,
            )
        if "photo-proof" in requirement.keywords:
            # The spec image is a photo of an annotated proof (monitor bezel,
            # moiré, review chrome) — pixel comparison cannot be a hard fail.
            return self.result(
                requirement, Status.WARNING,
                f"Spec image is a photo of an annotated proof; closest page is {best_url} "
                f"(distance {distance}). Review the diff composite manually.",
                details=detail, page_url=best_url, evidence=diff_evidence,
            )
        return self.result(
            requirement, Status.FAIL,
            f"No page visually matches the specification image (best distance {distance} on {best_url})",
            details=detail, page_url=best_url, evidence=diff_evidence,
        )

    def _render_diff(self, requirement: Requirement, spec_img: Image.Image, shot_path: Path) -> list[Evidence]:
        """Side-by-side composite with a difference heat panel."""
        shot_img = Image.open(shot_path).convert("RGB")
        height = 700
        def scaled(img: Image.Image) -> Image.Image:
            w = int(img.width * height / img.height)
            return img.resize((w, height), Image.Resampling.LANCZOS)
        left, right = scaled(spec_img), scaled(shot_img)
        # difference panel computed on a common size
        common = (min(left.width, right.width), height)
        diff = ImageChops.difference(left.resize(common), right.resize(common))
        diff = diff.point(lambda p: min(255, p * 3))  # amplify subtle differences

        gap = 12
        canvas = Image.new("RGB", (left.width + right.width + common[0] + gap * 2, height + 28), "white")
        canvas.paste(left, (0, 28))
        canvas.paste(right, (left.width + gap, 28))
        canvas.paste(diff, (left.width + right.width + gap * 2, 28))
        draw = ImageDraw.Draw(canvas)
        draw.text((4, 6), "SPEC", fill="black")
        draw.text((left.width + gap + 4, 6), "LIVE", fill="black")
        draw.text((left.width + right.width + gap * 2 + 4, 6), "DIFF (amplified)", fill="black")

        out_path = self.ctx.store.new_path(EvidenceKind.VISUAL_DIFF, f"{requirement.id}-diff", ".png")
        canvas.save(out_path)
        return [
            self.ctx.store.add_file(
                EvidenceKind.VISUAL_DIFF, out_path,
                f"{requirement.id}: spec vs live vs diff composite", "",
            )
        ]
