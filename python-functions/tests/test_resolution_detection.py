import unittest
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.llm_email_intelligence import (  # noqa: E402
    _apply_latest_message_resolution_override,
    _detect_latest_message_resolution,
    _normalize_priority_score,
)
from shared.email_intelligence import EmailIntelligence, apply_thread_summary_quality_rules  # noqa: E402


def make_messages(body: str) -> list[dict]:
    return [{"id": 1, "body_text": body, "body_preview": body[:255]}]


class TestResolutionDetection(unittest.TestCase):
    def test_successful_access_with_active_missing_items_does_not_resolve(self):
        result = _detect_latest_message_resolution(
            make_messages(
                "The employee was able to access the platform successfully. "
                "However, review objectives and competency assignments are missing. "
                "Please investigate and provide next steps."
            )
        )

        self.assertFalse(result.detected)
        self.assertEqual(result.trigger_phrase, "")

    def test_actions_performed_and_requested_support_actions_do_not_resolve(self):
        result = _detect_latest_message_resolution(
            make_messages(
                "Actions already performed: verified employee login functionality, "
                "reviewed template assignments, checked department mappings. "
                "Requested support actions: validate Oracle integration jobs and "
                "provide estimated resolution timeline."
            )
        )

        self.assertFalse(result.detected)
        self.assertEqual(result.trigger_phrase, "")

    def test_assignment_completed_issue_resolved_no_action_resolves(self):
        messages = make_messages(
            "NS Operations competency assignment has been completed successfully. "
            "Issue resolved. No further action is required."
        )
        result = _detect_latest_message_resolution(messages)

        self.assertTrue(result.detected)
        self.assertIn(
            result.trigger_phrase,
            {
                "competency assignment has been completed successfully",
                "issue resolved",
                "no further action is required",
            },
        )

        override = _apply_latest_message_resolution_override(
            _normalize_priority_score(
                {
                    "copilot_thread_summary": {},
                    "priority": "High",
                    "priority_score": 8,
                }
            )
        )
        self.assertEqual(override["current_status"], "resolved")
        self.assertEqual(override["request_type"], "Competency Assignment Completed")
        self.assertEqual(override["priority"], "Low")
        self.assertEqual(override["priority_score"], 2)

    def test_all_reported_issues_resolved_can_close_resolves(self):
        result = _detect_latest_message_resolution(
            make_messages("All reported issues have been resolved. This ticket can be closed.")
        )

        self.assertTrue(result.detected)
        self.assertIn(
            result.trigger_phrase,
            {
                "all reported issues have been resolved",
                "this ticket can be closed",
            },
        )

    def test_long_active_escalation_summary_is_not_completed_or_resolved(self):
        thread_summary = """Email Summary

Overall context:
Multiple performance review workflow issues are affecting employees and managers.

1. Initial Issue
The employee was marked Ineligible and Oracle and Laserbeam status may be out of sync.

2. Current/Main Issue
Objectives and competency questionnaires are missing for performance review workflows.

3. Root Cause / Findings
User was eventually able to access the platform successfully. Investigation revealed
configuration validation is still required.

4. Actions & Plan
• Validate Oracle integration jobs
• Review template assignments
• Provide next steps and estimated resolution timeline

5. Current Status
waiting_internal

Quick Takeaway
✅ Resolved: Employee login functionality was verified successfully.
⚠️ Blocker: Objectives and competency questionnaires are missing.
⏳ Waiting on: Support to investigate Oracle synchronization and configuration validation.
"""
        intelligence = EmailIntelligence(
            client_name="Laserbeam",
            product_name="LaserBeam",
            priority="High",
            priority_score=8,
            priority_reason="Business impact exists.",
            priority_confidence=0.8,
            module="Business Requirement",
            domain="Product / BA",
            intent="Incident",
            module_confidence=0.8,
            assigned_team="Business Analyst",
            assigned_team_confidence=0.8,
            client_confidence=0.8,
            product_confidence=0.8,
            issue_summary_confidence=0.8,
            review_required=True,
            routing="Business Analyst",
            summary="Performance review workflow issues require investigation.",
            issue_summary="Performance review workflow issues require investigation.",
            action_required="Investigate and provide next steps.",
            thread_summary=thread_summary,
            current_status="resolved",
        )

        result = apply_thread_summary_quality_rules(intelligence)

        self.assertNotEqual(result.current_status, "resolved")
        self.assertNotEqual(result.active_request_type, "Competency Assignment Completed")
        self.assertNotEqual(result.priority, "Low")


if __name__ == "__main__":
    unittest.main()
