"""CLI smoke tests via Typer's CliRunner."""

from __future__ import annotations

from aetherseed import __version__
from aetherseed.cli import app
from typer.testing import CliRunner

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_prompts_lists_library() -> None:
    result = runner.invoke(app, ["prompts"])
    assert result.exit_code == 0
    assert "seed_expansion" in result.stdout


def test_doctor_runs(env) -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "database" in result.stdout


def test_runs_empty(env) -> None:
    result = runner.invoke(app, ["runs"])
    assert result.exit_code == 0
