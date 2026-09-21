from .base import AgentContext, BaseAgent
from .bug_agent import BugDetectionAgent
from .quality_agent import CodeQualityAgent
from .security_agent import SecurityAgent
from .test_coverage_agent import TestCoverageAgent

ALL_AGENTS: list[type[BaseAgent]] = [
    BugDetectionAgent,
    SecurityAgent,
    CodeQualityAgent,
    TestCoverageAgent,
]

__all__ = [
    "AgentContext",
    "BaseAgent",
    "BugDetectionAgent",
    "SecurityAgent",
    "CodeQualityAgent",
    "TestCoverageAgent",
    "ALL_AGENTS",
]
