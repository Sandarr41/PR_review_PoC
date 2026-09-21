from ..models import Category
from .base import BaseAgent


class TestCoverageAgent(BaseAgent):
    name = "test_coverage_agent"
    category = Category.TESTING
    role_prompt = (
        "You check whether new or changed functionality has corresponding "
        "tests. Use the 'Related test files' section to see what test files "
        "already exist for the changed modules. Flag new public functions/"
        "classes with no apparent test coverage, and changed behavior whose "
        "existing tests were not updated. Do not flag test files themselves, "
        "or purely internal/private helper changes that are exercised "
        "indirectly by existing tests."
    )
