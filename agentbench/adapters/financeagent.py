from __future__ import annotations

from ._base import BenchAdapter, GradeResult, grade_final_answer
from ..runners._base import Task


class FinanceAgentAdapter(BenchAdapter):
    name = "financeagent"
    mode = "prompt-only"
    hub_dataset = "vals/financeagent"

    def _grade_impl(self, task: Task, events: list[dict]) -> GradeResult:
        return grade_final_answer(task, events)

    def load_tasks(self, n: int = 1) -> list[Task]:
        sample = Task(
            id="apple-revenue-q4-2023",
            bench=self.name,
            prompt=(
                "From Apple Inc.'s Q4 FY2023 earnings (reported November 2023), what was the "
                "total quarterly net revenue in USD billions, rounded to one decimal place? "
                "Provide just the number followed by 'B' (e.g. '90.0B')."
            ),
            mode=self.mode,
            grader_payload={"final_answer": "89.5B", "match": "contains_any", "alts": ["89.5", "89.50"]},
        )
        return [sample][:n]
