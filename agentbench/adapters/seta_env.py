from __future__ import annotations

from ._base import BenchAdapter, GradeResult, last_assistant_text, all_tool_results_text
from ..runners._base import Task


class SetaEnvAdapter(BenchAdapter):
    name = "seta-env"
    mode = "prompt-only"
    hub_dataset = "camel-ai/seta-env"

    def _grade_impl(self, task: Task, events: list[dict]) -> GradeResult:
        blob = (last_assistant_text(events) + "\n" + all_tool_results_text(events)).lower()
        sections = ["day 1", "day 2", "day 3"]
        hits = [s for s in sections if s in blob]
        if len(hits) == 3 and ("cost" in blob or "$" in blob):
            return GradeResult(pass_=True, score=1.0, reason="3-day plan + cost mention")
        return GradeResult(pass_=False, score=len(hits) / 4, reason=f"{len(hits)}/3 day sections, cost={'cost' in blob}")

    def load_tasks(self, n: int = 1) -> list[Task]:
        sample = Task(
            id="seta-env-sample-plan-trip",
            bench=self.name,
            prompt=(
                "Plan a 3-day trip to Tokyo for a budget of $1500. Use the bash tool to write "
                "your plan to /work/plan.md, with sections: Day 1, Day 2, Day 3, and Estimated "
                "Costs. Use realistic Tokyo-specific recommendations."
            ),
            mode=self.mode,
        )
        return [sample][:n]
