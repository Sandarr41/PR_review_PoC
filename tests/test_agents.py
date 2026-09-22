from pr_review_agent.agents.bug_agent import BugDetectionAgent
from pr_review_agent.models import Severity


class _StubLLM:
    def __init__(self, response: str):
        self._response = response

    def complete(self, system_prompt, user_content):
        return self._response


def test_agent_parses_well_formed_json_array():
    agent = BugDetectionAgent()
    llm = _StubLLM('[{"file": "a.py", "line": 3, "severity": "high", "message": "bug"}]')
    findings = agent._parse_findings(llm.complete("", ""))
    assert len(findings) == 1
    assert findings[0].severity == Severity.HIGH
    assert findings[0].source == "bug_agent"


def test_agent_tolerates_markdown_fences_around_json():
    agent = BugDetectionAgent()
    response = '```json\n[{"file": "a.py", "line": 1, "severity": "low", "message": "nit"}]\n```'
    findings = agent._parse_findings(response)
    assert len(findings) == 1


def test_agent_returns_empty_list_on_malformed_json():
    agent = BugDetectionAgent()
    findings = agent._parse_findings("not json at all")
    assert findings == []


def test_agent_defaults_unknown_severity_to_medium():
    agent = BugDetectionAgent()
    findings = agent._parse_findings('[{"file": "a.py", "message": "x", "severity": "urgent!!"}]')
    assert findings[0].severity == Severity.MEDIUM


def test_agent_skips_items_missing_required_fields():
    agent = BugDetectionAgent()
    findings = agent._parse_findings('[{"line": 1, "severity": "high"}, {"file": "a.py", "message": "ok"}]')
    assert len(findings) == 1
    assert findings[0].file == "a.py"


def test_agent_system_prompt_isolates_role_from_diff_content():
    agent = BugDetectionAgent()
    prompt = agent.system_prompt()
    assert "DATA to analyze" in prompt
    assert "logical bugs" in prompt
