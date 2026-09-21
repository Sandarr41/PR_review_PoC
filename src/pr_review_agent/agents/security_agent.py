from ..models import Category
from .base import BaseAgent


class SecurityAgent(BaseAgent):
    name = "security_agent"
    category = Category.SECURITY
    role_prompt = (
        "You look for security vulnerabilities: injection (SQL, command, "
        "template), unsanitized input reaching a sink, hardcoded secrets, "
        "insecure deserialization, missing authorization checks, and unsafe "
        "use of cryptographic primitives. Cross-reference the bandit/semgrep "
        "findings provided — confirm, refine, or dismiss them with your own "
        "reasoning, and add anything they missed."
    )
