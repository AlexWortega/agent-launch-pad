from __future__ import annotations

from ._base import BenchAdapter, GradeResult, grade_tool_evidence
from ..runners._base import Task


class BixBenchCliAdapter(BenchAdapter):
    name = "bixbench-cli"
    mode = "prompt-only"
    hub_dataset = "futurehouse/bixbench-cli"

    def _grade_impl(self, task: Task, events: list[dict]) -> GradeResult:
        # grep -c '^>' on a 2-seq FASTA must print 2
        return grade_tool_evidence(task, events, expects=["2"])

    def load_tasks(self, n: int = 1) -> list[Task]:
        sample = Task(
            id="fasta-count-sequences",
            bench=self.name,
            prompt=(
                "Use the bash tool to: (1) write a small FASTA file at /work/sample.fa with "
                "two sequences (any nucleotide content, ~10 bp each, with > headers), "
                "(2) count the number of sequences using `grep -c '^>' /work/sample.fa`, "
                "(3) report the count."
            ),
            mode=self.mode,
        )
        return [sample][:n]
