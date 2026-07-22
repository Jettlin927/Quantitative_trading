from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from my_quant.us_holdings.broker_ledger import (
    calculate_holdings,
    merge_executed_trades,
    normalize_trade,
    update_ledger_from_input,
)


class UsBrokerLedgerTest(unittest.TestCase):
    def test_normalizes_hsbc_chinese_fields_and_skips_non_executed(self):
        executed = normalize_trade(
            {
                "email_ts": "2020-01-02T03:04:05+00:00",
                "交易編號": "EXAMPLE-BUY-001",
                "交易狀況": "全部執行",
                "指示類別": "買入",
                "股票名稱/ 股票編號": "EXAMPLE CORP (EXMPL)",
                "已成交數量(股/單位)": "3",
                "成交價": "USD10.00",
                "Gmail message id": "example-message-001",
            }
        )
        pending = normalize_trade({"交易編號": "P1", "交易狀況": "有待執行"})

        self.assertIsNone(pending)
        assert executed is not None
        self.assertEqual(executed["trade_id"], "EXAMPLE-BUY-001")
        self.assertEqual(executed["ticker"], "EXMPL")
        self.assertEqual(executed["security_name"], "EXAMPLE CORP")
        self.assertEqual(executed["quantity"], "3")
        self.assertEqual(executed["price"], "10")
        self.assertEqual(executed["currency"], "USD")
        self.assertEqual(executed["trade_date"], "2020-01-02")

    def test_rejects_unknown_side(self):
        with self.assertRaisesRegex(ValueError, "invalid side"):
            normalize_trade(
                {
                    "status": "全部执行",
                    "side": "其他",
                    "ticker": "EXMPL",
                    "trade_id": "EXAMPLE-UNKNOWN-001",
                    "quantity": "1",
                    "price": "10",
                }
            )

    def test_merges_by_trade_id_and_calculates_fifo_holdings(self):
        existing = [
            {
                "email_ts_utc": "2026-07-21T13:00:00+00:00",
                "trade_date": "2026-07-21",
                "status": "全部執行",
                "side": "買入",
                "ticker": "EXMPL",
                "security_name": "EXAMPLE CORP",
                "trade_id": "EXAMPLE-BUY-BASE",
                "email_id": "m1",
                "quantity": "10",
                "price": "10",
                "currency": "USD",
                "amount": "100.00",
            }
        ]
        incoming = [
            dict(existing[0]),
            {
                "email_ts_utc": "2026-07-21T14:00:00+00:00",
                "trade_date": "2026-07-21",
                "status": "全部執行",
                "side": "買入",
                "ticker": "EXMPL",
                "security_name": "EXAMPLE CORP",
                "trade_id": "EXAMPLE-BUY-NEW",
                "email_id": "m2",
                "quantity": "5",
                "price": "12",
                "currency": "USD",
            },
            {
                "email_ts_utc": "2026-07-21T15:00:00+00:00",
                "trade_date": "2026-07-21",
                "status": "全部执行",
                "side": "沽出",
                "ticker": "EXMPL",
                "security_name": "EXAMPLE CORP",
                "trade_id": "EXAMPLE-SELL-001",
                "email_id": "m3",
                "quantity": "8",
                "price": "13",
                "currency": "USD",
            },
        ]

        merged = merge_executed_trades(existing, incoming)
        holdings = calculate_holdings(merged)

        self.assertEqual(
            ["EXAMPLE-BUY-BASE", "EXAMPLE-BUY-NEW", "EXAMPLE-SELL-001"],
            [row["trade_id"] for row in merged],
        )
        self.assertEqual(1, len(holdings))
        self.assertEqual("EXMPL", holdings[0]["ticker"])
        self.assertEqual("7", holdings[0]["quantity"])
        self.assertEqual("80.00", holdings[0]["cost_basis"])
        self.assertIn("2 @ 10.0000", holdings[0]["open_lots"])
        self.assertIn("5 @ 12.0000", holdings[0]["open_lots"])

    def test_preserves_confirmed_input_order_when_timestamps_are_missing(self):
        base = {
            "trade_date": "2020-01-02",
            "status": "全部执行",
            "ticker": "EXMPL",
            "security_name": "EXAMPLE CORP",
            "quantity": "1",
            "price": "10",
            "currency": "USD",
        }
        merged = merge_executed_trades(
            [],
            [
                {**base, "side": "买入", "trade_id": "Z-BUY"},
                {**base, "side": "沽出", "trade_id": "A-SELL"},
            ],
        )

        self.assertEqual(["Z-BUY", "A-SELL"], [row["trade_id"] for row in merged])
        self.assertEqual([], calculate_holdings(merged))

    def test_rebuilds_fifo_order_when_backfilled_rows_have_complete_timestamps(self):
        base = {
            "trade_date": "2020-01-02",
            "status": "全部执行",
            "ticker": "EXMPL",
            "security_name": "EXAMPLE CORP",
            "quantity": "1",
            "currency": "USD",
        }
        merged = merge_executed_trades(
            [
                {
                    **base,
                    "email_ts_utc": "2020-01-02T15:00:00+00:00",
                    "side": "买入",
                    "trade_id": "EXAMPLE-LATE-BUY",
                    "price": "100",
                }
            ],
            [
                {
                    **base,
                    "email_ts_utc": "2020-01-02T13:00:00+00:00",
                    "side": "买入",
                    "trade_id": "EXAMPLE-EARLY-BUY",
                    "price": "10",
                },
                {
                    **base,
                    "email_ts_utc": "2020-01-02T14:00:00+00:00",
                    "side": "沽出",
                    "trade_id": "EXAMPLE-MIDDLE-SELL",
                    "price": "20",
                },
            ],
        )
        holdings = calculate_holdings(merged)

        self.assertEqual(
            ["EXAMPLE-EARLY-BUY", "EXAMPLE-MIDDLE-SELL", "EXAMPLE-LATE-BUY"],
            [row["trade_id"] for row in merged],
        )
        self.assertEqual("100.00", holdings[0]["cost_basis"])

    def test_removes_non_executed_rows_from_existing_ledger(self):
        merged = merge_executed_trades(
            [
                {
                    "status": "有待执行",
                    "side": "买入",
                    "ticker": "EXMPL",
                    "trade_id": "EXAMPLE-PENDING-001",
                    "quantity": "1",
                    "price": "10",
                }
            ],
            [],
        )

        self.assertEqual([], merged)

    def test_failed_holdings_validation_does_not_write_any_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "fills.jsonl"
            ledger_path = root / "ledger.csv"
            holdings_path = root / "holdings.csv"
            html_path = root / "holdings.html"
            input_path.write_text(
                json.dumps(
                    {
                        "status": "全部执行",
                        "side": "沽出",
                        "ticker": "EXMPL",
                        "trade_id": "EXAMPLE-SELL-ONLY",
                        "quantity": "1",
                        "price": "10",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "exceeds open lots"):
                update_ledger_from_input(input_path, ledger_path, holdings_path, html_path)

            self.assertFalse(ledger_path.exists())
            self.assertFalse(holdings_path.exists())
            self.assertFalse(html_path.exists())

    def test_cli_update_writes_private_csv_and_html_from_csv_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "fills.jsonl"
            ledger_path = root / "ledger.csv"
            holdings_path = root / "holdings.csv"
            html_path = root / "holdings.html"
            input_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "交易编号": "EXAMPLE-BUY-002",
                                "交易状态": "全部执行",
                                "指示类别": "买入",
                                "股票名称/股票编号": "EXAMPLE CORP (EXMPL)",
                                "已成交数量(股/单位)": "2",
                                "成交价": "USD11.00",
                            },
                            ensure_ascii=False,
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            result = update_ledger_from_input(input_path, ledger_path, holdings_path, html_path)

            self.assertEqual(1, result["added_count"])
            self.assertIn("EXAMPLE-BUY-002", ledger_path.read_text(encoding="utf-8"))
            self.assertIn("EXMPL", holdings_path.read_text(encoding="utf-8"))
            self.assertIn("成本合计：$22.00", html_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
