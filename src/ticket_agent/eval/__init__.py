"""评测层的对外入口。"""

from .harness import (
    CaseResult,
    accuracy,
    cost_summary,
    coverage_gaps,
    detect_forbidden,
    failure_breakdown,
    pass_hat_k,
    run_case,
    run_suite,
    stratify,
    stratify_by_condition,
    stratify_by_style,
)
from .report import EXPECTED_CONDITIONS, build_report, render_markdown, write_report

__all__ = [
    "CaseResult",
    "accuracy",
    "cost_summary",
    "coverage_gaps",
    "detect_forbidden",
    "failure_breakdown",
    "pass_hat_k",
    "run_case",
    "run_suite",
    "stratify",
    "stratify_by_condition",
    "stratify_by_style",
    "EXPECTED_CONDITIONS",
    "build_report",
    "render_markdown",
    "write_report",
]
