from __future__ import annotations

from ._base import BenchAdapter, GradeResult
from ._base import last_assistant_text
from ..runners._base import Task


class MedAgentBenchAdapter(BenchAdapter):
    name = "medagentbench"
    mode = "prompt-only"
    hub_dataset = "stanford/medagentbench"

    def _grade_impl(self, task: Task, events: list[dict]) -> GradeResult:
        # Aspirin secondary-prevention: 75-100 mg/day per ACC/AHA. Heuristic match on numbers.
        text = last_assistant_text(events).lower()
        if not text:
            return GradeResult(pass_=False, score=0.0, reason="empty answer")
        has_dose = any(s in text for s in ["75", "81", "100"])
        has_unit = "mg" in text
        if has_dose and has_unit:
            return GradeResult(pass_=True, score=1.0, reason=f"dose+unit found in: {text[:120]!r}")
        return GradeResult(pass_=False, score=0.5 if has_dose else 0.0, reason=f"no dose match in: {text[:120]!r}")

    def load_tasks(self, n: int = 1) -> list[Task]:
        sample = Task(
            id="aspirin-dose-check",
            bench=self.name,
            prompt=(
                "A 65-year-old patient with no contraindications is started on aspirin for "
                "secondary prevention of cardiovascular disease. What is the recommended daily "
                "dose range in mg according to current US guidelines (ACC/AHA)? Provide a "
                "concise answer with the dose range and one-line citation of the guideline."
            ),
            mode=self.mode,
        )
        return [sample][:n]
