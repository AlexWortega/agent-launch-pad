from __future__ import annotations

from ._base import BenchAdapter, GradeResult, grade_tool_evidence
from ..runners._base import Task


class AiderPolyglotAdapter(BenchAdapter):
    name = "aider-polyglot"
    mode = "prompt-only"
    hub_dataset = "aider/aider-polyglot"

    def _grade_impl(self, task: Task, events: list[dict]) -> GradeResult:
        # FizzBuzz n=15 expected outputs:
        return grade_tool_evidence(task, events, expects=["fizzbuzz", "buzz"])

    def load_tasks(self, n: int = 1) -> list[Task]:
        sample = Task(
            id="fizzbuzz-python",
            bench=self.name,
            prompt=(
                "Implement FizzBuzz in Python. Write the code to /work/fizzbuzz.py. Then run it "
                "with `python3 /work/fizzbuzz.py` and confirm the output for n=15 is correct."
            ),
            mode=self.mode,
        )
        return [sample][:n]
