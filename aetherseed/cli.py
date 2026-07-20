"""AetherSeed command-line interface.

Examples
--------
Run an investigation::

    aetherseed investigate --subject "Example Mining Pty Ltd" --type company \\
        --context "Ownership and connections" --max-depth 2 --auto-seed \\
        --require-approval --output ./runs/example

Check backends::

    aetherseed doctor

Serve the API::

    aetherseed serve --port 8000
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from aetherseed import __version__
from aetherseed.config import get_settings
from aetherseed.logging import configure_logging
from aetherseed.schemas import Constraints, InvestigationRun, SubjectSeed, SubjectType

app = typer.Typer(
    name="aetherseed",
    help="Veralogix AetherSeed OSINT — local-first investigative research platform.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"aetherseed {__version__}")
        raise typer.Exit


@app.callback()
def main(
    _version: Annotated[
        bool, typer.Option("--version", callback=_version_callback, is_eager=True)
    ] = False,
) -> None:
    """AetherSeed OSINT CLI."""
    import contextlib
    import sys

    # Ensure UTF-8 output on legacy Windows consoles (cp1252) so rich rendering
    # and any non-ASCII content never crash the CLI.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8")

    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)


@app.command()
def investigate(
    subject: Annotated[
        list[str] | None,
        typer.Option("--subject", "-s", help="Primary identifier(s); repeatable."),
    ] = None,
    subject_type: Annotated[
        SubjectType, typer.Option("--type", "-t", help="Subject type.")
    ] = SubjectType.CUSTOM,
    context: Annotated[str, typer.Option("--context", "-c", help="Investigation brief.")] = "",
    from_json: Annotated[
        Path | None, typer.Option("--from-json", help="Load a SubjectSeed from a JSON file.")
    ] = None,
    max_depth: Annotated[int, typer.Option("--max-depth", help="Max crawl depth.")] = 2,
    max_pages: Annotated[int, typer.Option("--max-pages", help="Max pages to fetch.")] = 200,
    max_seeds: Annotated[int, typer.Option("--max-seeds", help="Max new seeds.")] = 50,
    budget_usd: Annotated[float, typer.Option("--budget-usd", help="Paid-call budget.")] = 0.0,
    auto_seed: Annotated[bool, typer.Option("--auto-seed", help="Enable auto-seeding.")] = False,
    require_approval: Annotated[
        bool, typer.Option("--require-approval/--no-require-approval")
    ] = True,
    ignore_robots: Annotated[
        bool, typer.Option("--ignore-robots", help="Override robots.txt (audited).")
    ] = False,
    screenshots: Annotated[bool, typer.Option("--screenshots", help="Capture screenshots.")] = False,
    enrich: Annotated[bool, typer.Option("--enrich", help="Run enrichment pass.")] = False,
    render: Annotated[bool, typer.Option("--render", help="Use a JS-rendering browser.")] = False,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Directory for run artifacts.")
    ] = None,
) -> None:
    """Run a full investigation for a subject."""
    if from_json is not None:
        seed = SubjectSeed.model_validate_json(from_json.read_text(encoding="utf-8"))
    else:
        if not subject:
            console.print("[red]Provide --subject or --from-json[/red]")
            raise typer.Exit(code=2)
        seed = SubjectSeed(
            subject_type=subject_type,
            primary_identifiers=subject,
            context=context,
            constraints=Constraints(
                max_depth=max_depth,
                max_pages=max_pages,
                max_seeds=max_seeds,
                budget_usd=budget_usd,
                require_approval=require_approval,
                respect_robots=not ignore_robots,
            ),
        )

    from aetherseed.pipelines import InvestigationPipeline

    def _progress(event: str, data: dict[str, object]) -> None:
        if event == "page":
            console.print(f"  [dim]- {data.get('url')}[/dim]", highlight=False)
        elif event in ("run.started", "ai.expansion", "seeding.done", "run.finished"):
            console.print(f"[cyan]>>[/cyan] {event} {data}", highlight=False)

    pipeline = InvestigationPipeline(progress=_progress)
    console.print(
        Panel.fit(
            f"[bold]{', '.join(seed.primary_identifiers)}[/bold]\n"
            f"type={seed.subject_type.value}  backend={pipeline.ai.backend_name}  "
            f"model={pipeline.ai.uses_model()}",
            title="AetherSeed investigation",
        )
    )

    result = asyncio.run(
        pipeline.run(
            seed,
            auto_seed=auto_seed,
            take_screenshots=screenshots,
            enrich=enrich,
            render=render,
        )
    )

    _print_summary(result)
    if output is not None:
        _write_artifacts(result, output)
        console.print(f"[green]Artifacts written to {output}[/green]")


@app.command("runs")
def list_runs(limit: Annotated[int, typer.Option("--limit")] = 20) -> None:
    """List recent investigation runs."""
    from aetherseed.core.storage.db import init_db, session_scope
    from aetherseed.core.storage.repositories import RunRepository

    init_db()
    with session_scope() as session:
        rows = RunRepository(session).list_recent(limit)
        table = Table(title="Recent runs")
        table.add_column("run_id")
        table.add_column("status")
        table.add_column("created_at")
        for r in rows:
            table.add_row(r.run_id, r.status, str(r.created_at))
        console.print(table)


@app.command()
def seeds(
    run_id: Annotated[str, typer.Argument(help="Run id.")],
    approve: Annotated[str | None, typer.Option("--approve", help="Approve a seed id.")] = None,
    reject: Annotated[str | None, typer.Option("--reject", help="Reject a seed id.")] = None,
) -> None:
    """List or approve/reject seeds for a run (human-in-the-loop)."""
    from aetherseed.core.seeding.engine import SeedingEngine
    from aetherseed.core.storage.audit import AuditLog
    from aetherseed.core.storage.db import init_db, session_scope
    from aetherseed.core.storage.repositories import SeedRepository

    init_db()
    audit = AuditLog(run_id)
    with session_scope() as session:
        engine = SeedingEngine()
        if approve:
            ok = engine.approve(session, audit, run_id, approve)
            console.print(f"approve {approve}: {'ok' if ok else 'failed'}")
            return
        if reject:
            ok = engine.reject(session, audit, run_id, reject)
            console.print(f"reject {reject}: {'ok' if ok else 'failed'}")
            return
        rows = SeedRepository(session).list_by_run(run_id)
        table = Table(title=f"Seeds for {run_id}")
        for col in ("id", "type", "identifiers", "status", "origin", "score"):
            table.add_column(col)
        for s in rows:
            table.add_row(
                s.id, s.subject_type, ", ".join(s.identifiers), s.status, s.origin, f"{s.score:.2f}"
            )
        console.print(table)


@app.command()
def doctor() -> None:
    """Check the health of local backends (DB, Ollama, Playwright)."""
    from aetherseed.core.acquisition.browser import playwright_available
    from aetherseed.core.ai.backend import OllamaBackend
    from aetherseed.core.storage.db import init_db

    settings = get_settings()
    table = Table(title="AetherSeed doctor")
    table.add_column("component")
    table.add_column("status")

    try:
        init_db()
        table.add_row("database", "[green]ok[/green]")
    except Exception as exc:
        table.add_row("database", f"[red]{exc}[/red]")

    ollama_ok = OllamaBackend(settings).available()
    ollama_status = (
        f"[green]{settings.ai_ollama_host}[/green]"
        if ollama_ok
        else "[yellow]unreachable (heuristics used)[/yellow]"
    )
    table.add_row("ollama", ollama_status)
    table.add_row(
        "playwright",
        "[green]installed[/green]" if playwright_available() else "[yellow]not installed[/yellow]",
    )
    table.add_row("pii_redaction", "on" if settings.pii_redaction else "off")
    console.print(table)


@app.command()
def prompts() -> None:
    """List the versioned AI prompts in use."""
    from aetherseed.core.ai.prompts import all_prompts

    table = Table(title="Prompt library")
    table.add_column("name")
    table.add_column("version")
    for name, prompt in all_prompts().items():
        table.add_row(name, prompt.version)
    console.print(table)


@app.command()
def serve(
    host: Annotated[str | None, typer.Option("--host")] = None,
    port: Annotated[int | None, typer.Option("--port")] = None,
    reload: Annotated[bool, typer.Option("--reload")] = False,
) -> None:
    """Run the FastAPI service with Uvicorn."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "aetherseed.apps.api.main:app",
        host=host or settings.api_host,
        port=port or settings.api_port,
        reload=reload,
    )


# --- helpers ----------------------------------------------------------------


def _print_summary(result: InvestigationRun) -> None:
    m = result.metrics
    table = Table(title=f"Run {result.run_id} — {result.status.value}")
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_row("pages fetched", str(m.pages_fetched))
    table.add_row("processed / failed", f"{m.processed} / {m.failed}")
    table.add_row("entities (delta)", str(len(result.graph_delta.nodes)))
    table.add_row("relationships (delta)", str(len(result.graph_delta.edges)))
    table.add_row("leads", str(len(result.new_leads)))
    table.add_row("seeds generated", str(m.seeds_generated))
    table.add_row("pending approvals", str(m.pending))
    table.add_row("coverage score", f"{result.gap_report.coverage_score:.2f}")
    table.add_row("llm calls", str(m.llm_calls))
    table.add_row("duration (s)", f"{m.duration_s:.1f}" if m.duration_s else "-")
    console.print(table)

    if result.new_leads:
        lt = Table(title="Top leads")
        lt.add_column("score", justify="right")
        lt.add_column("type")
        lt.add_column("title")
        for lead in result.top_leads(10):
            lt.add_row(f"{lead.score:.2f}", lead.lead_type, lead.title[:70])
        console.print(lt)


def _write_artifacts(result: InvestigationRun, output: Path) -> None:
    from aetherseed.core.graph.store import NetworkXGraphStore

    output.mkdir(parents=True, exist_ok=True)
    (output / "run.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")

    store = NetworkXGraphStore(graph_id=result.subject.existing_graph_id)
    store.apply_delta(result.graph_delta)
    (output / "graph.graphml").write_text(str(store.export("graphml")), encoding="utf-8")
    (output / "graph.jsonld").write_text(
        json.dumps(store.export("json-ld"), indent=2, default=str), encoding="utf-8"
    )
    (output / "leads.json").write_text(
        json.dumps([lead.model_dump(mode="json") for lead in result.top_leads(50)], indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    app()
