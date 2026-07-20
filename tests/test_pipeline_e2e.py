"""End-to-end pipeline tests: happy path, auto-seeding, resumability."""

from __future__ import annotations

from aetherseed.core.storage.audit import AuditLog
from aetherseed.pipelines import InvestigationPipeline
from aetherseed.schemas import Constraints, RunStatus, SubjectSeed, SubjectType

from tests.fakes import FakeFetcher

PAGES = {
    "http://example.com/": "<h1>Root Co Pty Ltd</h1><a href='/about'>about</a>",
    "http://example.com/about": (
        "<p>Owned by Parent Holdings Ltd. Contact ceo@example.com. "
        "Payment of R2 000 000 to Beta Logistics CC.</p>"
    ),
}


def _subject() -> SubjectSeed:
    return SubjectSeed(
        subject_type=SubjectType.COMPANY,
        primary_identifiers=["http://example.com/"],
        context="ownership and money flows",
        constraints=Constraints(max_depth=1, max_pages=10, require_approval=False),
    )


async def test_full_run_produces_structured_result(env) -> None:
    pipe = InvestigationPipeline(env, fetcher_factory=lambda respect_robots: FakeFetcher(PAGES))
    result = await pipe.run(_subject(), auto_seed=True)

    assert result.status in (RunStatus.SUCCEEDED, RunStatus.PARTIAL)
    assert result.metrics.pages_fetched >= 1
    assert result.graph_delta.nodes, "expected discovered entities"
    assert result.new_leads, "expected leads"
    assert result.metrics.seeds_generated >= 1
    assert 0.0 <= result.gap_report.coverage_score <= 1.0
    # Audit log is intact and hash-chained.
    assert AuditLog(result.run_id, env).verify_chain() is True
    assert result.audit_log_ref


async def test_run_is_resumable(env) -> None:
    fake = FakeFetcher(PAGES)
    pipe = InvestigationPipeline(env, fetcher_factory=lambda respect_robots: fake)
    first = await pipe.run(_subject(), auto_seed=False)
    assert first.metrics.pages_fetched >= 1

    # Resume with the same run id: previously-fetched pages are skipped.
    second = await pipe.run(_subject(), run_id=first.run_id, auto_seed=False)
    assert second.metrics.pages_fetched == 0


async def test_no_crawlable_subject_still_completes(env) -> None:
    pipe = InvestigationPipeline(env, fetcher_factory=lambda respect_robots: FakeFetcher({}))
    subject = SubjectSeed(
        subject_type=SubjectType.COMPANY,
        primary_identifiers=["Some Private Company"],  # not a URL/domain
        constraints=Constraints(max_depth=1, max_pages=5, require_approval=False),
    )
    result = await pipe.run(subject, auto_seed=True)
    assert result.status is RunStatus.SUCCEEDED  # graceful: no crawl, still a result
    assert result.gap_report is not None
