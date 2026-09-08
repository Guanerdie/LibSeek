"""Decisions must be readable as JSON lines, not just as one DB row each.

Optimisation 3 in ``NEXT_OPTIMIZATION_PLAN.md``.
"""

from __future__ import annotations

import json
import sys
from importlib import util
from pathlib import Path

import pytest
import structlog

from app.core.logging import bind_job_context, clear_job_context, configure_logging, get_logger

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "analyze_automation_logs.py"


def _load_script():
    spec = util.spec_from_file_location("analyze_automation_logs", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def json_logs(capsys):
    """Configure structlog for JSON output and hand back a reader."""

    configure_logging()
    yield capsys
    clear_job_context()
    structlog.reset_defaults()


def _events(capsys) -> list[dict]:
    return [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip().startswith("{")
    ]


def test_an_event_is_rendered_as_one_json_line(json_logs) -> None:
    get_logger("test").info("candidate_accepted", candidate_id="c1", score=0.9)

    events = _events(json_logs)

    assert len(events) == 1
    assert events[0]["event"] == "candidate_accepted"
    assert events[0]["candidate_id"] == "c1"
    assert events[0]["level"] == "info"
    assert "timestamp" in events[0]


def test_job_context_is_merged_into_every_event_below_it(json_logs) -> None:
    bind_job_context(run_id="run-1", job_id="job-1", media_id="media-1")
    logger = get_logger("test")
    logger.info("candidate_rejected", candidate_id="c1", reasons=["评分低于策略门槛"])
    logger.info("no_candidate_selected", total_candidates=3)

    events = _events(json_logs)

    assert [event["event"] for event in events] == [
        "candidate_rejected",
        "no_candidate_selected",
    ]
    for event in events:
        assert event["run_id"] == "run-1"
        assert event["job_id"] == "job-1"
        assert event["media_id"] == "media-1"


def test_clearing_the_context_stops_tagging_later_events(json_logs) -> None:
    """A leaked context would attribute one job's events to the next job."""

    bind_job_context(job_id="job-1")
    clear_job_context()
    get_logger("test").info("candidate_accepted", candidate_id="c1")

    assert "job_id" not in _events(json_logs)[0]


# ---------------------------------------------------------------------------
# scripts/analyze_automation_logs.py
# ---------------------------------------------------------------------------


def test_the_analysis_script_counts_every_reason_on_a_candidate() -> None:
    script = _load_script()
    lines = [
        json.dumps(
            {
                "event": "candidate_rejected",
                "reasons": ["评分低于策略门槛", "做种数不足"],
            }
        ),
        json.dumps({"event": "candidate_rejected", "reasons": ["做种数不足"]}),
        json.dumps({"event": "candidate_accepted", "candidate_id": "c9"}),
    ]

    report = script.analyze(lines)

    assert report["accepted"] == 1
    assert report["rejected_candidates"] == 2
    assert report["rejection_reasons"]["做种数不足"] == 2
    assert report["rejection_reasons"]["评分低于策略门槛"] == 1


def test_the_analysis_script_ignores_uvicorn_plain_text_lines() -> None:
    """``docker compose logs`` interleaves non-JSON output; it must not crash."""

    script = _load_script()
    lines = [
        "INFO:     Application startup complete.",
        "",
        json.dumps({"event": "candidate_accepted"}),
        "not json at all",
    ]

    report = script.analyze(lines)

    assert report["accepted"] == 1


def test_the_analysis_script_strips_the_compose_service_prefix() -> None:
    script = _load_script()
    line = 'backend  | {"event": "job_failed", "error_code": "PT_TIMEOUT"}'

    report = script.analyze([line])

    assert report["failed_jobs"] == 1
    assert report["failure_codes"]["PT_TIMEOUT"] == 1


def test_the_analysis_script_surfaces_media_that_never_find_anything() -> None:
    script = _load_script()
    lines = [
        json.dumps({"event": "no_candidate_selected", "media_id": "m1"}),
        json.dumps({"event": "no_candidate_selected", "media_id": "m1"}),
        json.dumps({"event": "no_candidate_selected", "media_id": "m2"}),
    ]

    report = script.analyze(lines)

    assert report["empty_searches"] == 3
    assert report["media_with_no_pick"]["m1"] == 2
    rendered = script._render(report)
    assert "反复搜不到的影视" in rendered
    assert "m1: 2" in rendered
    # A single miss is normal; only repeats are worth listing.
    assert "m2" not in rendered.split("反复搜不到的影视")[1]
