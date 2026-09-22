from ..models import Category
from .base import BaseAgent


class BugDetectionAgent(BaseAgent):
    name = "bug_agent"
    category = Category.BUG
    role_prompt = (
        "You look for logical bugs that static analysis tools miss: "
        "possible None/null dereferences, off-by-one errors, incorrect "
        "conditionals, unhandled exceptions, resource leaks, and mismatches "
        "between a function's apparent contract and its implementation. "
        "Use the static analysis findings as supporting evidence where "
        "relevant, but focus on reasoning about program logic, not style."
    )
