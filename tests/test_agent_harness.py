import asyncio

from commerce_copilot.agent_harness import AgentHarnessPolicy, AgentRuntimeHarness
from commerce_copilot.contracts import AgentRole, Evidence, Mission


def _permissions():
    return {AgentRole.CONVERSATION_GUIDE: set()}


def test_harness_normalizes_successful_worker_output():
    async def handler(mission: Mission):
        return "ok", [Evidence(source="test", reference="1")], []

    harness = AgentRuntimeHarness(
        handlers={AgentRole.CONVERSATION_GUIDE: handler},
        role_permissions=_permissions(),
        confirmation_resolver=lambda mission: set(),
    )

    report = asyncio.run(
        harness.execute(Mission(role=AgentRole.CONVERSATION_GUIDE, instruction="hi"))
    )

    assert report.status == "completed"
    assert report.message == "ok"
    assert report.evidence[0].reference == "1"


def test_harness_isolates_worker_failure():
    async def handler(mission: Mission):
        raise RuntimeError("boom")

    harness = AgentRuntimeHarness(
        handlers={AgentRole.CONVERSATION_GUIDE: handler},
        role_permissions=_permissions(),
        confirmation_resolver=lambda mission: set(),
    )

    report = asyncio.run(
        harness.execute(Mission(role=AgentRole.CONVERSATION_GUIDE, instruction="hi"))
    )

    assert report.status == "failed"
    assert "RuntimeError" in report.message
    assert report.evidence == []


def test_harness_enforces_agent_timeout():
    async def handler(mission: Mission):
        await asyncio.sleep(0.05)
        return "late", [], []

    harness = AgentRuntimeHarness(
        handlers={AgentRole.CONVERSATION_GUIDE: handler},
        role_permissions=_permissions(),
        confirmation_resolver=lambda mission: set(),
        policy=AgentHarnessPolicy(timeout_seconds=0.001),
    )

    report = asyncio.run(
        harness.execute(Mission(role=AgentRole.CONVERSATION_GUIDE, instruction="hi"))
    )

    assert report.status == "failed"
    assert "TimeoutError" in report.message
