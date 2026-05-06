from __future__ import annotations

from ._base import BenchAdapter, GradeResult, grade_tool_evidence
from ..runners._base import Task


class AutoCodeBenchAdapter(BenchAdapter):
    name = "autocodebench"
    mode = "prompt-only"
    hub_dataset = "tencent/autocodebench"

    def _grade_impl(self, task: Task, events: list[dict]) -> GradeResult:
        # two_sum self-test must print [0, 1] (or 0 1)
        return grade_tool_evidence(task, events, expects=["[0, 1]"])

    def load_tasks(self, n: int = 1) -> list[Task]:
        sample = Task(
            id="two-sum-py",
            bench=self.name,
            prompt=(
                "Write a Python function `two_sum(nums: list[int], target: int) -> list[int]` "
                "that returns indices of the two numbers in `nums` that add up to `target`. "
                "Save to /work/solution.py. Then run a self-test with nums=[2,7,11,15], "
                "target=9 and confirm the output is [0, 1]."
            ),
            mode=self.mode,
        )
        return [sample][:n]
