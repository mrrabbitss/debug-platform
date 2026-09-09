"""Diagnostic reasoning milestones shared by model and evidence-tool steps."""
from app.services.job_progress import report_progress


def report_planning_progress(ctx, coverage, round_number, message):
    attempted = sum(bool(item.get("attempted")) for item in coverage.values())
    # Round limits are a budget, not the amount of work remaining. Unknown
    # coverage stays at the stage milestone and shows only the actual round.
    percent = 50 + int(30 * attempted / len(coverage)) if coverage else 50
    report_progress(ctx, percent, message,
                    stage="多轮推理与证据核验", stage_index=4, stage_count=6,
                    completed_units=attempted if coverage else None,
                    total_units=len(coverage) or None,
                    unit="项故障树检查" if coverage else None, round_number=round_number)
