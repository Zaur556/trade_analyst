from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from commodity_agent.alerts import (
    AlertConfig,
    AlertConfigError,
    Change,
    build_message,
    detect_changes,
    load_config,
    load_state,
    process_alerts,
    send_email,
)
from commodity_agent.config import ASSETS
from commodity_agent.macro import MacroContext
from commodity_agent.pipeline import RunResult
from commodity_agent.signals import analyze
from tests.helpers import flat, make_series, trend

CONFIG = AlertConfig(
    user="sender@gmail.com",
    password="abcd efgh ijkl mnop".replace(" ", ""),
    to="recipient@gmail.com",
)


def result_for(signal) -> RunResult:
    return RunResult(
        generated_at=datetime(2026, 10, 23, 19, 30, tzinfo=timezone.utc),
        as_of="2026-10-23",
        signals=[signal],
        macro=MacroContext(quotes=[], series={}, values={}),
        backtests={},
        news_count=0,
        warnings=[],
        account_size=50_000,
        risk_pct=1.0,
        days=4.0,
        articles=[],
    )


class TestChangeDetection(unittest.TestCase):
    def setUp(self):
        self.signal = analyze(ASSETS["gold"], make_series(trend()), None, None)
        self.result = result_for(self.signal)

    def test_baseline_is_saved_without_sending(self):
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            out = process_alerts(
                self.result, Path(tmp), config=CONFIG,
                sender=lambda *args: sent.append(args),
            )
            self.assertEqual(out.status, "baseline")
            self.assertEqual(sent, [])
            self.assertTrue((Path(tmp) / "alert_state.json").exists())
            state = load_state(Path(tmp) / "alert_state.json")
            self.assertIn("gold", state["assets"])

    def test_action_flip_sends_one_email(self):
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            process_alerts(self.result, Path(tmp), config=CONFIG,
                           sender=lambda *args: sent.append(args))
            changed = replace(self.signal, action="HOLD", score=8.0)
            out = process_alerts(
                result_for(changed), Path(tmp), config=CONFIG,
                sender=lambda *args: sent.append(args),
            )
            self.assertEqual(out.status, "sent")
            self.assertEqual(len(sent), 1)
            self.assertIn("-> HOLD", sent[0][1])
            self.assertEqual(out.changes[0].kind, "action")

    def test_no_change_does_not_advance_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            process_alerts(self.result, Path(tmp), config=CONFIG, sender=lambda *a: None)
            baseline = (Path(tmp) / "alert_state.json").read_text(encoding="utf-8")
            out = process_alerts(self.result, Path(tmp), config=CONFIG, sender=lambda *a: None)
            self.assertEqual(out.status, "no-change")
            self.assertEqual(
                json.loads(baseline)["assets"]["gold"]["confidence"],
                json.loads((Path(tmp) / "alert_state.json").read_text(encoding="utf-8"))["assets"]["gold"]["confidence"],
            )
            # A 20-point confidence move must still fire against the original baseline.
            changed = replace(self.signal, confidence=min(95.0, self.signal.confidence + 20))
            sent = []
            out = process_alerts(
                result_for(changed), Path(tmp), config=CONFIG,
                sender=lambda *args: sent.append(args),
            )
            self.assertEqual(out.status, "sent")
            self.assertEqual(out.changes[0].kind, "confidence")
            self.assertEqual(len(sent), 1)


    def test_scheduled_digest_emails_on_baseline_and_no_change_runs(self):
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            first = process_alerts(
                self.result, out_dir, config=CONFIG,
                sender=lambda *args: sent.append(args), always_send=True,
            )
            self.assertEqual(first.status, "sent")
            self.assertEqual(len(sent), 1)
            self.assertIn("2-hour market update", sent[0][1])
            self.assertIn("CURRENT BOOK", sent[0][1])

            before = (out_dir / "alert_state.json").read_text(encoding="utf-8")
            second = process_alerts(
                self.result, out_dir, config=CONFIG,
                sender=lambda *args: sent.append(args), always_send=True,
            )
            self.assertEqual(second.status, "sent")
            self.assertEqual(len(sent), 2)
            self.assertEqual(sent[1][1], "[Commodity Signal] 2-hour market update")
            self.assertIn("No material signal changes", sent[1][2])
            self.assertEqual(
                json.loads(before)["assets"]["gold"]["confidence"],
                json.loads((out_dir / "alert_state.json").read_text(encoding="utf-8"))["assets"]["gold"]["confidence"],
            )

    def test_scheduled_digest_includes_material_changes(self):
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            process_alerts(
                self.result, Path(tmp), config=CONFIG,
                sender=lambda *args: sent.append(args), always_send=True,
            )
            changed = replace(self.signal, action="HOLD", score=8.0)
            result = process_alerts(
                result_for(changed), Path(tmp), config=CONFIG,
                sender=lambda *args: sent.append(args), always_send=True,
            )
            self.assertEqual(result.status, "sent")
            self.assertEqual(len(sent), 2)
            self.assertIn("1 material change", sent[1][1])
            self.assertIn("Gold: BUY -> HOLD", sent[1][1])

    def test_stop_crossing_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            process_alerts(self.result, Path(tmp), config=CONFIG, sender=lambda *a: None)
            self.assertIsNotNone(self.signal.plan.stop)
            changed = replace(self.signal, price=self.signal.plan.stop - 1.0)
            changes = detect_changes(load_state(Path(tmp) / "alert_state.json"),
                                     result_for(changed))
            self.assertTrue(any(c.kind == "stop" for c in changes))

    def test_flat_trigger_detected(self):
        signal = analyze(ASSETS["brent"], make_series(flat()), None, None)
        self.assertEqual(signal.action, "HOLD")
        with tempfile.TemporaryDirectory() as tmp:
            process_alerts(result_for(signal), Path(tmp), config=CONFIG, sender=lambda *a: None)
            changed = replace(signal, price=signal.plan.trigger_buy + 0.01)
            changes = detect_changes(load_state(Path(tmp) / "alert_state.json"),
                                     result_for(changed))
            self.assertTrue(any(c.kind == "trigger" for c in changes))

    def test_message_contains_book_and_disclaimer(self):
        change = Change("gold", "Gold", "action", "Gold: BUY -> HOLD", "test detail")
        with tempfile.TemporaryDirectory() as tmp:
            subject, text, html = build_message(self.result, [change], Path(tmp))
            self.assertIn("Gold", subject)
            self.assertIn("BUY -> HOLD", subject)
            self.assertIn("CURRENT BOOK", text)
            self.assertIn("not investment advice", text)
            self.assertNotIn("Dashboard:", text)
            self.assertIn("Signal change", html)

    def test_new_instrument_adds_baseline_without_email(self):
        wti = analyze(ASSETS["wti"], make_series(trend()), None, None)
        with tempfile.TemporaryDirectory() as tmp:
            process_alerts(self.result, Path(tmp), config=CONFIG, sender=lambda *a: None)
            sent = []
            two = replace(self.result, signals=[self.signal, wti])
            out = process_alerts(
                two, Path(tmp), config=CONFIG, sender=lambda *args: sent.append(args)
            )
            self.assertEqual(out.status, "baseline")
            self.assertEqual(sent, [])
            state = load_state(Path(tmp) / "alert_state.json")
            self.assertIn("wti", state["assets"])


class TestConfig(unittest.TestCase):
    def test_env_file_loads_and_strips_app_password_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "gmail.env"
            env.write_text(
                "\ufeffGMAIL_USER=me@gmail.com\n"
                "GMAIL_APP_PASSWORD=abcd efgh ijkl mnop\n"
                "ALERT_TO=other@gmail.com\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {}, clear=True):
                cfg = load_config(env_file=env)
            self.assertEqual(cfg.user, "me@gmail.com")
            self.assertEqual(cfg.password, "abcdefghijklmnop")
            self.assertEqual(cfg.to, "other@gmail.com")

    def test_missing_config_raises_helpful_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(AlertConfigError) as ctx:
                    load_config(env_file=Path(tmp) / "missing.env")
            self.assertIn("App Password", str(ctx.exception))

    def test_smtp_login_and_send(self):
        with mock.patch("commodity_agent.alerts.smtplib.SMTP_SSL") as smtp_cls:
            smtp = smtp_cls.return_value.__enter__.return_value
            send_email(CONFIG, "subject", "text", "<p>html</p>")
            smtp.login.assert_called_once_with(CONFIG.user, CONFIG.password)
            smtp.send_message.assert_called_once()
            message = smtp.send_message.call_args[0][0]
            self.assertEqual(message["Subject"], "subject")
            self.assertEqual(message["To"], CONFIG.to)

    def test_port_587_uses_starttls(self):
        cfg = AlertConfig(
            user="sender@gmail.com",
            password="abcdefghijklmnop",
            to="recipient@gmail.com",
            port=587,
        )
        with mock.patch("commodity_agent.alerts.smtplib.SMTP") as smtp_cls:
            smtp = smtp_cls.return_value.__enter__.return_value
            send_email(cfg, "subject", "text", "<p>html</p>")
            smtp.starttls.assert_called_once()
            smtp.login.assert_called_once_with(cfg.user, cfg.password)
            smtp.send_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()
