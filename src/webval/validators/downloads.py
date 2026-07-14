"""Phase 10 — download validation (brochures and other downloadable assets).

Strategy: click the download link with Playwright's download interception;
if the asset is served inline (many preprod servers send PDFs without
Content-Disposition), fall back to fetching it through the authenticated
request context. Either way the artifact is stored as evidence, hashed, and
its size verified > 0.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import ClassVar

from webval.models import (
    AssetRef,
    EvidenceKind,
    PageSnapshot,
    Requirement,
    RequirementCategory,
    Status,
    ValidationResult,
)
from webval.utils.text import contains_normalized, normalize_text
from webval.validators.base import BaseValidator


class DownloadValidator(BaseValidator):
    name: ClassVar[str] = "downloads"
    categories: ClassVar[frozenset[RequirementCategory]] = frozenset({RequirementCategory.DOWNLOAD})

    async def validate(self, requirement: Requirement) -> ValidationResult:
        located = self._locate(requirement)
        if located is None:
            return self.result(
                requirement, Status.FAIL,
                "Required downloadable asset not found on any crawled page",
                details=f"target_text={requirement.target_text!r} keywords={requirement.keywords}",
            )
        snapshot, asset = located
        file_name = asset.url.split("/")[-1].split("?")[0] or "download"

        # Attempt a real click-driven download first (closest to user behaviour).
        saved_path, method, error = await self._download_via_click(snapshot, asset, requirement)
        if saved_path is None:
            saved_path, method, error = await self._download_via_request(asset, requirement, error)

        if saved_path is None:
            return self.result(
                requirement, Status.FAIL,
                f"Download of {asset.url} failed: {error}",
                page_url=snapshot.url,
            )

        size = saved_path.stat().st_size
        evidence = [
            self.ctx.store.add_file(
                EvidenceKind.DOWNLOAD, saved_path,
                f"{requirement.id}: downloaded artifact {file_name} ({method})",
                snapshot.url,
            )
        ]
        if size == 0:
            return self.result(
                requirement, Status.FAIL,
                f"{file_name} downloaded but file is 0 bytes",
                details=f"method={method}", page_url=snapshot.url, evidence=evidence,
            )
        return self.result(
            requirement, Status.PASS,
            f"{file_name} downloaded successfully ({size:,} bytes, sha256 recorded)",
            details=f"source={asset.url}; method={method}",
            page_url=snapshot.url, evidence=evidence,
        )

    # ------------------------------------------------------------------ locate

    def _locate(self, requirement: Requirement) -> tuple[PageSnapshot, AssetRef] | None:
        candidates: list[tuple[PageSnapshot, AssetRef, int]] = []
        for page in self.ctx.site_map.pages:
            for asset in page.assets:
                if asset.kind != "download":
                    continue
                score = 1
                blob = normalize_text(asset.url)
                if requirement.target_text and contains_normalized(asset.url, requirement.target_text):
                    score += 10
                score += sum(2 for kw in requirement.keywords if kw in blob)
                # match link text on the page pointing at this asset
                for link in page.links:
                    if link.href == asset.url and requirement.target_text and contains_normalized(
                        link.text, requirement.target_text
                    ):
                        score += 10
                candidates.append((page, asset, score))
        if not candidates:
            return None
        page, asset, score = max(candidates, key=lambda c: c[2])
        if len(candidates) == 1 or score >= 3:
            return page, asset
        return None

    # ---------------------------------------------------------------- download

    async def _download_via_click(
        self, snapshot: PageSnapshot, asset: AssetRef, requirement: Requirement
    ) -> tuple[Path | None, str, str]:
        page = await self.ctx.open_page(snapshot.url)
        try:
            tail = asset.url.split("/")[-1].split("?")[0]
            locator = page.locator(f'a[href*="{tail}"]').first
            if await locator.count() == 0:
                return None, "click", "download link not present in live DOM"
            async with page.expect_download(timeout=20_000) as download_info:
                await locator.click()
            download = await download_info.value
            suffix = PurePosixPath(tail).suffix or ".bin"
            target = self.ctx.store.new_path(EvidenceKind.DOWNLOAD, f"{requirement.id}-{tail}", suffix)
            await download.save_as(target)
            return target, "click", ""
        except Exception as exc:
            return None, "click", str(exc)
        finally:
            await page.close()

    async def _download_via_request(
        self, asset: AssetRef, requirement: Requirement, prior_error: str
    ) -> tuple[Path | None, str, str]:
        try:
            ctx = await self.ctx.session.context()
            response = await ctx.request.get(asset.url, timeout=30_000)
            if response.status >= 400:
                return None, "request", f"HTTP {response.status} (click attempt: {prior_error})"
            body = await response.body()
            tail = asset.url.split("/")[-1].split("?")[0] or "download.bin"
            suffix = PurePosixPath(tail).suffix or ".bin"
            target = self.ctx.store.new_path(EvidenceKind.DOWNLOAD, f"{requirement.id}-{tail}", suffix)
            target.write_bytes(body)
            return target, "request", ""
        except Exception as exc:
            return None, "request", f"{exc} (click attempt: {prior_error})"
