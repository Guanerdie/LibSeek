#!/usr/bin/env python3
"""Count the decisions automation made, from its JSON log lines.

Usage::

    docker compose logs backend | python scripts/analyze_automation_logs.py -
    python scripts/analyze_automation_logs.py backend.log

Answers the question a single ``AutomationJob.decision`` row cannot: across a
week of runs, which rejection reason is actually costing you resources?  A
policy threshold that rejects nine candidates out of ten is worth knowing about
before you conclude the site has nothing.

Non-JSON lines are ignored, so piping raw ``docker compose logs`` output --
which interleaves uvicorn's plain-text lines -- works without pre-filtering.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from typing import Any


def iter_events(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # ``docker compose logs`` prefixes each line with "service  | ".
        if "{" in line:
            line = line[line.index("{") :]
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and "event" in event:
            yield event


def analyze(lines: Iterable[str]) -> dict[str, Any]:
    rejection_reasons: Counter[str] = Counter()
    failure_codes: Counter[str] = Counter()
    rejected_candidates = 0
    accepted = 0
    empty_searches = 0
    failed_jobs = 0
    media_with_no_pick: Counter[str] = Counter()

    for event in iter_events(lines):
        name = event.get("event")
        if name == "candidate_rejected":
            rejected_candidates += 1
            reasons = event.get("reasons") or []
            if isinstance(reasons, list):
                # A candidate usually fails several checks at once; counting
                # every reason shows which thresholds are really biting.
                rejection_reasons.update(str(reason) for reason in reasons)
        elif name == "candidate_accepted":
            accepted += 1
        elif name == "no_candidate_selected":
            empty_searches += 1
            media_id = event.get("media_id")
            if media_id:
                media_with_no_pick[str(media_id)] += 1
        elif name == "job_failed":
            failed_jobs += 1
            failure_codes[str(event.get("error_code") or "UNKNOWN")] += 1

    return {
        "accepted": accepted,
        "rejected_candidates": rejected_candidates,
        "empty_searches": empty_searches,
        "failed_jobs": failed_jobs,
        "rejection_reasons": rejection_reasons,
        "failure_codes": failure_codes,
        "media_with_no_pick": media_with_no_pick,
    }


def _render(report: dict[str, Any]) -> str:
    out: list[str] = ["## 自动化日志分析", ""]
    out.append(f"选中候选: {report['accepted']}")
    out.append(f"拒绝候选: {report['rejected_candidates']}")
    out.append(f"没有可选资源的搜索: {report['empty_searches']}")
    out.append(f"失败任务: {report['failed_jobs']}")

    reasons: Counter[str] = report["rejection_reasons"]
    if reasons:
        out += ["", "拒绝原因分布:"]
        out += [f"  - {reason}: {count}" for reason, count in reasons.most_common()]

    codes: Counter[str] = report["failure_codes"]
    if codes:
        out += ["", "失败错误码分布:"]
        out += [f"  - {code}: {count}" for code, count in codes.most_common()]

    repeats: Counter[str] = report["media_with_no_pick"]
    persistent = [(media, count) for media, count in repeats.most_common(10) if count > 1]
    if persistent:
        out += ["", "反复搜不到的影视（media_id: 次数）:"]
        out += [f"  - {media}: {count}" for media, count in persistent]

    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "log_file",
        nargs="?",
        default="-",
        help="JSON 日志文件路径，'-' 表示从标准输入读取（默认）",
    )
    args = parser.parse_args(argv)

    if args.log_file == "-":
        report = analyze(sys.stdin)
    else:
        with open(args.log_file, encoding="utf-8", errors="replace") as handle:
            report = analyze(handle)

    print(_render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
