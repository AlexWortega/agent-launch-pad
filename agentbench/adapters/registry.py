from __future__ import annotations

from ._base import BenchAdapter
from .terminal_bench import TerminalBenchAdapter
from .theagentcompany import TheAgentCompanyAdapter
from .gaia import GaiaAdapter
from .seta_env import SetaEnvAdapter
from .aider_polyglot import AiderPolyglotAdapter
from .autocodebench import AutoCodeBenchAdapter
from .medagentbench import MedAgentBenchAdapter
from .bixbench_cli import BixBenchCliAdapter
from .scienceagentbench import ScienceAgentBenchAdapter
from .financeagent import FinanceAgentAdapter


ADAPTERS: dict[str, type[BenchAdapter]] = {
    "terminal-bench-2": TerminalBenchAdapter,
    "theagentcompany": TheAgentCompanyAdapter,
    "gaia": GaiaAdapter,
    "seta-env": SetaEnvAdapter,
    "aider-polyglot": AiderPolyglotAdapter,
    "autocodebench": AutoCodeBenchAdapter,
    "medagentbench": MedAgentBenchAdapter,
    "bixbench-cli": BixBenchCliAdapter,
    "scienceagentbench": ScienceAgentBenchAdapter,
    "financeagent": FinanceAgentAdapter,
}


def all_benches() -> list[str]:
    return list(ADAPTERS.keys())
