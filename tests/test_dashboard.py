from __future__ import annotations

import unittest

from commodity_agent.dashboard import DASHBOARD_HTML


class TestHostedDashboard(unittest.TestCase):
    def test_dashboard_is_self_contained_and_uses_protected_preview_api(self):
        self.assertIn("Commodity Signal Desk", DASHBOARD_HTML)
        self.assertIn("score-chart", DASHBOARD_HTML)
        self.assertIn('/api/run?preview=1', DASHBOARD_HTML)
        self.assertIn('Authorization', DASHBOARD_HTML)
        self.assertNotIn("localStorage", DASHBOARD_HTML)
        self.assertNotIn("https://cdn.", DASHBOARD_HTML)

    def test_dashboard_contains_risk_disclaimer(self):
        self.assertIn("not investment advice", DASHBOARD_HTML)
        self.assertIn("does not send alerts or execute trades", DASHBOARD_HTML)


if __name__ == "__main__":
    unittest.main()
