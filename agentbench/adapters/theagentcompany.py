from __future__ import annotations

from ._base import BenchAdapter, GradeResult, last_assistant_text, all_tool_results_text
from ..runners._base import Task


class TheAgentCompanyAdapter(BenchAdapter):
    name = "theagentcompany"
    mode = "prompt-only"  # smoke: skip full GitLab/Plane/OwnCloud/RocketChat compose stack
    hub_dataset = "theagentcompany/theagentcompany"

    def _grade_impl(self, task: Task, events: list[dict]) -> GradeResult:
        # Heuristic: ONBOARDING.md is mentioned and 4+ of the 5 sections are present.
        blob = (last_assistant_text(events) + "\n" + all_tool_results_text(events)).lower()
        keywords = ["clone", "install", "rocketchat", "plane", "access"]
        hits = [k for k in keywords if k in blob]
        if "onboarding" in blob and len(hits) >= 4:
            return GradeResult(pass_=True, score=len(hits) / 5, reason=f"onboarding+{len(hits)}/5 keywords")
        return GradeResult(pass_=False, score=len(hits) / 5, reason=f"{len(hits)}/5 keywords; onboarding={'onboarding' in blob}")

    def load_tasks(self, n: int = 1) -> list[Task]:
        sample = Task(
            id="onboarding-readme",
            bench=self.name,
            prompt=(
                "You are a new engineer at TheAgentCompany. Draft a 5-line onboarding README "
                "covering: (1) which repo to clone, (2) how to install deps, (3) which channel "
                "to join on RocketChat, (4) where to find tickets in Plane, (5) who to ask for "
                "access. Use the file-write tool to save it as ONBOARDING.md."
            ),
            mode=self.mode,
        )
        return [sample][:n]
