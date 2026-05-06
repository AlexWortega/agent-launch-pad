from __future__ import annotations

from ._base import BenchAdapter, GradeResult, grade_final_answer
from ..runners._base import Task


class GaiaAdapter(BenchAdapter):
    name = "gaia"
    mode = "prompt-only"
    hub_dataset = "gaia-benchmark/GAIA"

    def _grade_impl(self, task: Task, events: list[dict]) -> GradeResult:
        return grade_final_answer(task, events)

    def load_tasks(self, n: int = 1) -> list[Task]:
        # Representative GAIA-style multi-step reasoning question. Real GAIA L1 sample.
        sample = Task(
            id="gaia-sample-howmany-studio-albums",
            bench=self.name,
            prompt=(
                "How many studio albums were published by Mercedes Sosa between 2000 and 2009 "
                "(included)? You can use the latest 2022 version of the English Wikipedia. "
                "Provide your final answer as a single integer."
            ),
            mode=self.mode,
            grader_payload={"final_answer": "3", "match": "exact"},
        )
        return [sample][:n]
