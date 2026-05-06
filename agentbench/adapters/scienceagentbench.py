from __future__ import annotations

from ._base import BenchAdapter, GradeResult, grade_tool_evidence
from ..runners._base import Task


class ScienceAgentBenchAdapter(BenchAdapter):
    name = "scienceagentbench"
    mode = "prompt-only"
    hub_dataset = "scienceagentbench/scienceagentbench"

    def _grade_impl(self, task: Task, events: list[dict]) -> GradeResult:
        # Linear regression script must print slope and intercept
        return grade_tool_evidence(task, events, expects=["slope", "intercept"])

    def load_tasks(self, n: int = 1) -> list[Task]:
        sample = Task(
            id="linreg-mtcars",
            bench=self.name,
            prompt=(
                "Write a Python script at /work/analysis.py that creates a small toy dataset "
                "of (mpg, weight) pairs (8-10 rows resembling the mtcars dataset) and fits an "
                "ordinary least-squares regression of mpg on weight using only NumPy. "
                "Print the slope and intercept. Then execute it with `python3 /work/analysis.py`."
            ),
            mode=self.mode,
        )
        return [sample][:n]
