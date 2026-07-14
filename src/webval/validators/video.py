"""Phase 11 — video/media validation (render, playback start, player load).

Handles native <video> elements (playback driven via the media API) and
embedded players (YouTube/Vimeo/Brightcove iframes — verified by iframe load
and player DOM presence, since cross-origin playback state is not readable).
"""

from __future__ import annotations

from typing import Any, ClassVar

from webval.models import (
    EvidenceKind,
    PageSnapshot,
    Requirement,
    RequirementCategory,
    Status,
    ValidationResult,
)
from webval.validators.base import BaseValidator

_NATIVE_PLAY_JS = """
async () => {
  const video = document.querySelector('video');
  if (!video) return { found: false };
  const initial = video.currentTime;
  video.muted = true;                     // autoplay policies require muted
  try { await video.play(); } catch (e) {
    return { found: true, playError: String(e), error: video.error ? video.error.message : null };
  }
  await new Promise((r) => setTimeout(r, 2500));
  return {
    found: true,
    playError: null,
    error: video.error ? video.error.message : null,
    initialTime: initial,
    currentTime: video.currentTime,
    readyState: video.readyState,
    paused: video.paused,
    duration: video.duration || 0,
  };
}
"""


class VideoValidator(BaseValidator):
    name: ClassVar[str] = "video"
    categories: ClassVar[frozenset[RequirementCategory]] = frozenset({RequirementCategory.VIDEO})

    async def validate(self, requirement: Requirement) -> ValidationResult:
        snapshot = self._page_with_video(requirement)
        if snapshot is None:
            return self.result(
                requirement, Status.FAIL,
                "No video element or embedded player found on any crawled page",
            )

        page = await self.ctx.open_page(snapshot.url)
        try:
            has_native = await page.locator("video").count() > 0
            if has_native:
                result = await self._validate_native(page, requirement, snapshot)
            else:
                result = await self._validate_embed(page, requirement, snapshot)
            return result
        finally:
            await page.close()

    def _page_with_video(self, requirement: Requirement) -> PageSnapshot | None:
        target = self.ctx.resolve_target_page(requirement)
        ordered = ([target] if target else []) + [
            p for p in self.ctx.site_map.pages if p is not target
        ]
        for page in ordered:
            if any(a.kind == "video" for a in page.assets):
                return page
        return None

    async def _validate_native(self, page: Any, requirement: Requirement, snapshot: PageSnapshot) -> ValidationResult:
        await page.locator("video").first.scroll_into_view_if_needed()
        state: dict[str, Any] = await page.evaluate(_NATIVE_PLAY_JS)
        shot = self.ctx.store.new_path(EvidenceKind.SCREENSHOT, f"{requirement.id}-video", ".png")
        await page.screenshot(path=str(shot))
        evidence = [
            self.ctx.store.add_file(
                EvidenceKind.SCREENSHOT, shot, f"{requirement.id}: video during playback", snapshot.url
            ),
            self.ctx.store.add_json(EvidenceKind.METRIC, f"{requirement.id}-video-state", state,
                                    f"Video playback state for {requirement.id}", snapshot.url),
        ]
        if state.get("error"):
            return self.result(
                requirement, Status.FAIL,
                f"Video element reports media error: {state['error']}",
                page_url=snapshot.url, evidence=evidence,
            )
        if state.get("playError"):
            return self.result(
                requirement, Status.FAIL,
                f"play() rejected: {state['playError']}",
                page_url=snapshot.url, evidence=evidence,
            )
        advanced = state.get("currentTime", 0) > state.get("initialTime", 0)
        if advanced and state.get("readyState", 0) >= 2:
            return self.result(
                requirement, Status.PASS,
                f"Video renders and playback started (currentTime advanced to "
                f"{state['currentTime']:.1f}s, readyState={state['readyState']})",
                page_url=snapshot.url, evidence=evidence,
            )
        return self.result(
            requirement, Status.WARNING,
            f"Video present but playback did not clearly advance "
            f"(currentTime={state.get('currentTime')}, readyState={state.get('readyState')})",
            page_url=snapshot.url, evidence=evidence,
        )

    async def _validate_embed(self, page: Any, requirement: Requirement, snapshot: PageSnapshot) -> ValidationResult:
        iframe = page.locator(
            'iframe[src*="youtube"], iframe[src*="vimeo"], iframe[src*="brightcove"], iframe[src*="player"]'
        ).first
        if await iframe.count() == 0:
            return self.result(
                requirement, Status.FAIL,
                f"Video expected on {snapshot.url} but no player iframe found in live DOM",
                page_url=snapshot.url,
            )
        await iframe.scroll_into_view_if_needed()
        await page.wait_for_timeout(1500)
        box = await iframe.bounding_box()
        shot = self.ctx.store.new_path(EvidenceKind.SCREENSHOT, f"{requirement.id}-player", ".png")
        await page.screenshot(path=str(shot))
        evidence = [
            self.ctx.store.add_file(EvidenceKind.SCREENSHOT, shot, f"{requirement.id}: embedded player", snapshot.url)
        ]
        if box and box["width"] > 50 and box["height"] > 50:
            src = await iframe.get_attribute("src")
            return self.result(
                requirement, Status.PASS,
                f"Embedded player loaded and rendered ({box['width']:.0f}x{box['height']:.0f})",
                details=f"player src: {src}",
                page_url=snapshot.url, evidence=evidence,
            )
        return self.result(
            requirement, Status.FAIL,
            "Player iframe exists but has no rendered size (player failed to load)",
            page_url=snapshot.url, evidence=evidence,
        )
