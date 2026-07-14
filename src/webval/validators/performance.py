"""Phase 14 — performance metrics (LCP, CLS, INP, TTFB, load time).

LCP/CLS/INP are observed via PerformanceObserver injected before navigation;
TTFB and load time come from the Navigation Timing API. INP needs real user
interactions, so a synthetic click is issued and the resulting event timing
is reported best-effort (marked as such in the evidence).
"""

from __future__ import annotations

from typing import Any, ClassVar

from webval.models import EvidenceKind, Requirement, RequirementCategory, Status, ValidationResult
from webval.validators.base import BaseValidator

_OBSERVER_INIT_JS = """
() => {
  window.__webval = { lcp: null, cls: 0, inp: null };
  new PerformanceObserver((list) => {
    const entries = list.getEntries();
    const last = entries[entries.length - 1];
    if (last) window.__webval.lcp = last.startTime;
  }).observe({ type: 'largest-contentful-paint', buffered: true });
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      if (!entry.hadRecentInput) window.__webval.cls += entry.value;
    }
  }).observe({ type: 'layout-shift', buffered: true });
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      const dur = entry.duration;
      if (window.__webval.inp === null || dur > window.__webval.inp) window.__webval.inp = dur;
    }
  }).observe({ type: 'event', buffered: true, durationThreshold: 16 });
}
"""

_COLLECT_JS = """
() => {
  const nav = performance.getEntriesByType('navigation')[0];
  return {
    lcp_ms: window.__webval ? window.__webval.lcp : null,
    cls: window.__webval ? window.__webval.cls : null,
    inp_ms: window.__webval ? window.__webval.inp : null,
    ttfb_ms: nav ? nav.responseStart - nav.requestStart : null,
    load_ms: nav ? nav.loadEventEnd - nav.startTime : null,
    dom_content_loaded_ms: nav ? nav.domContentLoadedEventEnd - nav.startTime : null,
    transfer_size: nav ? nav.transferSize : null,
  };
}
"""


class PerformanceValidator(BaseValidator):
    name: ClassVar[str] = "performance"
    categories: ClassVar[frozenset[RequirementCategory]] = frozenset({RequirementCategory.PERFORMANCE})

    async def validate(self, requirement: Requirement) -> ValidationResult:
        snapshot = self.ctx.resolve_target_page(requirement)
        if snapshot is None:
            return self.result(requirement, Status.ERROR, "No crawled page available")

        page = await self.ctx.session.new_page()
        try:
            await page.add_init_script(f"({_OBSERVER_INIT_JS})()")
            await page.goto(snapshot.url, wait_until="load")
            await page.wait_for_timeout(1500)  # let LCP settle
            try:
                await page.mouse.click(10, 10)  # synthetic interaction for INP
                await page.wait_for_timeout(500)
            except Exception:
                pass
            metrics: dict[str, Any] = await page.evaluate(_COLLECT_JS)
        finally:
            await page.close()

        budgets = self.ctx.settings.validation.performance
        evidence = [
            self.ctx.store.add_json(
                EvidenceKind.METRIC, f"{requirement.id}-perf",
                {"url": snapshot.url, "metrics": metrics,
                 "budgets": budgets.model_dump(),
                 "note": "INP is best-effort from a synthetic interaction"},
                f"Performance metrics for {requirement.id}", snapshot.url,
            )
        ]

        breaches: list[str] = []
        if metrics.get("lcp_ms") is not None and metrics["lcp_ms"] > budgets.lcp_budget_ms:
            breaches.append(f"LCP {metrics['lcp_ms']:.0f}ms > budget {budgets.lcp_budget_ms}ms")
        if metrics.get("cls") is not None and metrics["cls"] > budgets.cls_budget:
            breaches.append(f"CLS {metrics['cls']:.3f} > budget {budgets.cls_budget}")
        if metrics.get("ttfb_ms") is not None and metrics["ttfb_ms"] > budgets.ttfb_budget_ms:
            breaches.append(f"TTFB {metrics['ttfb_ms']:.0f}ms > budget {budgets.ttfb_budget_ms}ms")

        summary = (
            f"LCP={_fmt(metrics.get('lcp_ms'))} CLS={metrics.get('cls'):.3f} "
            f"INP={_fmt(metrics.get('inp_ms'))} TTFB={_fmt(metrics.get('ttfb_ms'))} "
            f"load={_fmt(metrics.get('load_ms'))}"
            if metrics.get("cls") is not None
            else str(metrics)
        )
        if breaches:
            return self.result(
                requirement, Status.WARNING,
                f"Performance budget breached on {snapshot.url}: " + "; ".join(breaches),
                details=summary, page_url=snapshot.url, evidence=evidence,
            )
        return self.result(
            requirement, Status.PASS,
            f"All performance budgets met on {snapshot.url} ({summary})",
            page_url=snapshot.url, evidence=evidence,
        )


def _fmt(value: float | None) -> str:
    return f"{value:.0f}ms" if value is not None else "n/a"
