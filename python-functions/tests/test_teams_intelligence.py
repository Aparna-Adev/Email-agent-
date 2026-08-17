import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import sys
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import shared.email_intelligence as email_intelligence_module  # noqa: E402
from shared.email_intelligence import (  # noqa: E402
    _build_email_context_for_llm,
    _build_intelligence_from_llm,
    build_email_intelligence_from_record,
    extract_email_intelligence,
)
from shared.teams_notifier import build_teams_card_payload  # noqa: E402


def make_email(body: str, subject: str = "Client issue", **overrides):
    defaults = {
        "id": 101,
        "subject": subject,
        "cleaned_body": body,
        "body_preview": body,
        "sender_email": "client@example.com",
        "sender_name": "Client Sender",
        "conversation_id": "thread-123",
        "conversation_index": "index-456",
        "destination_organization": "Example Client",
        "destination_product_name": "",
        "received_at": datetime(2026, 6, 3, 10, 0, 0),
        "reply_count": 1,
        "original_sender_name": "",
        "original_sender_email": "",
        "support_mailbox": "support@example.com",
        "routed_to_email": "",
        "teams_from_email": "",
        "source_email": "",
        "watch_mailbox": "watch@example.com",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestTeamsIntelligence(unittest.TestCase):
    def test_urgent_email_sync_routes_to_platform_backend(self):
        intelligence = extract_email_intelligence(
            make_email("This is urgent. Incoming emails are not syncing with dashboard.")
        )

        self.assertEqual(intelligence.priority, "High")
        self.assertEqual(intelligence.module, "Email Integration")
        self.assertEqual(intelligence.domain, "Sync Services")
        self.assertEqual(intelligence.assigned_team, "Platform / Backend")
        self.assertGreaterEqual(intelligence.priority_confidence, 0.80)
        self.assertEqual(intelligence.routing, "Platform / Backend")

    def test_not_urgent_resolved_email_downgrades_priority(self):
        intelligence = extract_email_intelligence(
            make_email("This is not urgent anymore. Email sync is working now.")
        )

        self.assertEqual(intelligence.priority, "Low")
        self.assertEqual(intelligence.intent, "Resolved / Informational")
        self.assertNotEqual(intelligence.priority, "Critical")

    def test_production_all_user_outage_is_critical(self):
        intelligence = extract_email_intelligence(
            make_email("Production portal is down for all users.")
        )

        self.assertEqual(intelligence.priority, "Critical")
        self.assertIn(
            intelligence.assigned_team,
            {"Platform / Backend", "Infrastructure Team"},
        )
        self.assertGreaterEqual(intelligence.priority_confidence, 0.80)

    def test_payroll_report_clarification_routes_to_business_analyst(self):
        intelligence = extract_email_intelligence(
            make_email("Need clarification on payroll report format.")
        )

        self.assertIn(intelligence.priority, {"Medium", "Low"})
        self.assertEqual(intelligence.assigned_team, "Business Analyst")

    def test_dashboard_button_alignment_routes_to_frontend(self):
        intelligence = extract_email_intelligence(
            make_email("Dashboard button alignment issue.")
        )

        self.assertIn(intelligence.priority, {"Low", "Medium"})
        self.assertEqual(intelligence.assigned_team, "Frontend / UI Team")

    def test_teams_card_uses_business_template_without_confidence_fields(self):
        email = make_email("This is urgent. Incoming emails are not syncing with dashboard.")
        intelligence = extract_email_intelligence(email)
        payload = build_teams_card_payload(email, intelligence)
        body_text = payload["attachments"][0]["content"]["body"][1]["text"]

        self.assertIn("🏢 Customer : Example Client", body_text)
        self.assertIn("📧 Client Email : client@example.com", body_text)
        self.assertIn("📂 Product : Email Integration", body_text)
        self.assertIn("🔍 Review Required : No", body_text)
        self.assertIn("📝 Issue Summary", body_text)
        self.assertIn("💼 Business Impact", body_text)
        self.assertIn("🔗 Recommended Actions", body_text)
        self.assertIn("🆔 Email ID : 101", body_text)
        self.assertIn("🧵 Thread ID : thread-123 / index-456", body_text)
        self.assertNotIn("Priority Confidence:", body_text)
        self.assertNotIn("Module             :", body_text)

    def test_low_confidence_routes_to_human_review(self):
        email = make_email(
            "Please review this later.",
            issue_summary="Please review this later.",
            priority=None,
            priority_confidence=None,
            module=None,
            module_confidence=None,
            assigned_team=None,
            assigned_team_confidence=None,
            review_required=None,
            routing=None,
        )
        intelligence = build_email_intelligence_from_record(email)

        self.assertTrue(intelligence.review_required)
        self.assertEqual(intelligence.assigned_team, "General Queue / Human Review")
        self.assertEqual(intelligence.routing, "General Queue / Human Review")

    def test_teams_card_uses_persisted_fields_without_recomputing(self):
        email = make_email(
            "Production portal is down for all users.",
            issue_summary="Persisted audit summary",
            priority="Low",
            priority_score=1.2,
            priority_reason="Persisted priority reason",
            priority_confidence=0.77,
            module="Business Requirement",
            module_confidence=0.71,
            domain="Product / BA",
            intent="Request / Clarification",
            assigned_team="Business Analyst",
            assigned_team_confidence=0.73,
            review_required=False,
            routing="Business Analyst",
        )
        intelligence = build_email_intelligence_from_record(email)
        payload = build_teams_card_payload(email, intelligence)
        body_text = payload["attachments"][0]["content"]["body"][1]["text"]

        self.assertIn("🔥 Priority : Low", body_text)
        self.assertIn("📍 Routing : Business Analyst", body_text)
        self.assertNotIn("Priority Score", body_text)
        self.assertNotIn("Persisted priority reason", body_text)
        self.assertNotIn("Module", body_text)

    def test_laserbeam_compensation_notification_removes_raw_email_text(self):
        email = make_email(
            (
                "Hi Team,\n"
                "I am unable to add compensation for one of my direct reports.\n"
                "Thanks,\n"
                "Sent from iPhone"
            ),
            subject="Issue with LaserBeam",
            destination_organization="",
            destination_product_name="",
            original_sender_name="Dr. Anita Client <anita@contoso.com>",
            original_sender_email="anita@contoso.com",
            sender_email="anita@contoso.com",
        )
        intelligence = extract_email_intelligence(email)
        payload = build_teams_card_payload(email, intelligence)
        body_text = payload["attachments"][0]["content"]["body"][1]["text"]

        self.assertIn("📂 Product : LaserBeam", body_text)
        self.assertIn("👤 Client Contact : Anita Client", body_text)
        self.assertIn(
            "A compensation entry cannot be added for a direct report in LaserBeam",
            body_text,
        )
        self.assertIn("Compensation workflow disruption", body_text)
        self.assertIn("• Review compensation workflow configuration", body_text)
        self.assertNotIn("Hi Team", body_text)
        self.assertNotIn("Thanks", body_text)
        self.assertNotIn("Sent from iPhone", body_text)

    def test_missing_persisted_fields_use_safe_fallbacks(self):
        email = make_email(
            "Incoming mail issue body fallback.",
            issue_summary=None,
            priority=None,
            priority_score=None,
            priority_reason=None,
            priority_confidence=None,
            module=None,
            module_confidence=None,
            domain=None,
            intent=None,
            assigned_team=None,
            assigned_team_confidence=None,
            review_required=None,
            routing=None,
        )
        intelligence = build_email_intelligence_from_record(email)

        self.assertEqual(intelligence.priority, "Medium")
        self.assertEqual(intelligence.priority_confidence, 0.50)
        self.assertEqual(intelligence.assigned_team, "General Queue / Human Review")
        self.assertEqual(intelligence.assigned_team_confidence, 0.50)
        self.assertEqual(intelligence.module, "Unclear")
        self.assertEqual(intelligence.module_confidence, 0.50)
        self.assertTrue(intelligence.review_required)
        self.assertEqual(intelligence.routing, "General Queue / Human Review")

    def test_llm_context_includes_previous_summary_latest_and_recent_messages(self):
        email = make_email(
            "Latest customer update says payroll export is still blocked.",
            subject="RE: Payroll export issue",
        )
        context = _build_email_context_for_llm(
            email=email,
            cleaned_body=email.cleaned_body,
            issue_summary="Payroll export remains blocked",
            previous_thread_summary={
                "thread_summary": "Earlier messages reported a payroll export failure."
            },
            recent_thread_messages=[
                {
                    "sender_name": "Client Sender",
                    "sender_email": "client@example.com",
                    "received_at": "2026-06-03 09:00:00",
                    "cleaned_body": "Payroll export failed for HR users.",
                }
            ],
        )

        self.assertIn("PREVIOUS THREAD SUMMARY", context)
        self.assertIn("Earlier messages reported a payroll export failure.", context)
        self.assertIn("LATEST EMAIL MESSAGE", context)
        self.assertIn("Latest customer update says payroll export is still blocked.", context)
        self.assertIn("RECENT THREAD MESSAGES", context)
        self.assertIn("[Message 1]", context)
        self.assertIn("Payroll export failed for HR users.", context)

    def test_llm_context_missing_thread_summary_does_not_fail(self):
        email = make_email("Latest message only.")
        context = _build_email_context_for_llm(
            email=email,
            cleaned_body=email.cleaned_body,
            issue_summary="Latest message only",
            previous_thread_summary=None,
            recent_thread_messages=[],
        )

        self.assertIn("No previous thread summary available", context)
        self.assertIn("No recent thread messages available", context)

    def test_invalid_llm_routing_sentence_is_rejected(self):
        email = make_email("Please review the new workflow.")
        intelligence = _build_intelligence_from_llm(
            email=email,
            llm_data={
                "priority": "Medium",
                "assigned_team": "Business Analyst",
                "routing": "Review React code implementation against design requirements",
                "issue_summary": "Workflow review requested",
                "thread_summary": "Customer requested workflow review.",
                "review_required": False,
            },
            cleaned_body=email.cleaned_body,
            fallback_issue_summary="Fallback summary",
        )

        self.assertEqual(intelligence.assigned_team, "Business Analyst")
        self.assertEqual(intelligence.routing, "Business Analyst")
        self.assertEqual(intelligence.issue_summary, "Workflow review requested")
        self.assertEqual(intelligence.thread_summary, "Customer requested workflow review.")

    def test_llm_failure_falls_back_to_rule_engine(self):
        email = make_email("This is urgent. Incoming emails are not syncing with dashboard.")

        with patch.dict("os.environ", {"USE_LLM_EMAIL_INTELLIGENCE": "true"}):
            with patch.object(
                email_intelligence_module,
                "_load_thread_context_for_llm",
                return_value=(None, []),
            ):
                with patch.object(
                    email_intelligence_module,
                    "analyze_email_with_llm",
                    side_effect=RuntimeError("LLM failed"),
                ):
                    intelligence = extract_email_intelligence(email)

        self.assertEqual(intelligence.priority, "High")
        self.assertEqual(intelligence.assigned_team, "Platform / Backend")

    def test_email_intelligence_migration_exists(self):
        migration_path = (
            Path(__file__).resolve().parents[2]
            / "backend"
            / "sql"
            / "23-add-email-intelligence-fields.sql"
        )

        self.assertTrue(migration_path.exists())
        migration_sql = migration_path.read_text(encoding="utf-8")
        self.assertIn("issue_summary", migration_sql)
        self.assertIn("priority_score", migration_sql)
        self.assertIn("assigned_team_confidence", migration_sql)
        self.assertIn("routing", migration_sql)


if __name__ == "__main__":
    unittest.main()
