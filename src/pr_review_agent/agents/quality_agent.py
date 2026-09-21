from ..models import Category
from .base import BaseAgent


class CodeQualityAgent(BaseAgent):
    name = "quality_agent"
    category = Category.QUALITY
    role_prompt = (
        "You assess readability and structure: overly complex functions, "
        "duplicated logic, unclear naming, missing docstrings on public "
        "APIs, and violations of the project's own conventions (see the "
        "related symbols / conventions context, if provided). Do not repeat "
        "findings pylint/flake8 already reported verbatim — add judgment "
        "they can't: is this the *right* abstraction, not just a lint rule."
    )
