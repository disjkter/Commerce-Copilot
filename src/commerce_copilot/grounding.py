from typing import Any

from .contracts import AgentReport, GroundingIssue, GroundingSummary


def validate_reports(reports: list[AgentReport]) -> GroundingSummary:
    issues: list[GroundingIssue] = []
    checked_claims = 0

    for report in reports:
        evidence_by_reference = {item.reference: item for item in report.evidence}
        for claim in report.claims:
            checked_claims += 1
            evidence = evidence_by_reference.get(claim.evidence_reference)
            if evidence is None:
                issues.append(
                    GroundingIssue(
                        role=report.role,
                        statement=claim.statement,
                        reason="missing_evidence",
                    )
                )
                continue
            if claim.attribute not in evidence.attributes:
                issues.append(
                    GroundingIssue(
                        role=report.role,
                        statement=claim.statement,
                        reason="missing_attribute",
                    )
                )
                continue
            actual_value = evidence.attributes[claim.attribute]
            if not _values_match(actual_value, claim.expected_value):
                issues.append(
                    GroundingIssue(
                        role=report.role,
                        statement=claim.statement,
                        reason="value_mismatch",
                    )
                )

    return GroundingSummary(
        passed=not issues,
        checked_claims=checked_claims,
        issues=issues,
    )


def _values_match(actual: Any, expected: Any) -> bool:
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return abs(float(actual) - float(expected)) < 1e-9
    return actual == expected
