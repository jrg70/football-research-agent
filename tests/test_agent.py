import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent  # noqa: E402
import tools  # noqa: E402

HEADER = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,AvgCH,AvgCD,AvgCA,AvgC>2.5,AvgC<2.5,MaxCH,MaxCD,MaxCA,MaxC>2.5,MaxC<2.5"
ROWS = [
    "E0,12/08/2023,A,B,2,1,2.00,3.50,4.00,1.90,1.90,2.10,3.60,4.20,2.00,2.00",  # home win, over
    "E0,13/08/2023,C,D,0,0,1.50,4.00,6.00,1.80,2.00,1.55,4.20,6.50,1.85,2.10",  # draw, under
    "E0,14/08/2023,E,F,1,3,3.00,3.20,2.40,1.70,2.10,3.10,3.30,2.50,1.75,2.20",  # away win, over
    "E0,15/08/2023,G,H,1,0,2.50,3.10,2.90,2.20,1.65,2.60,3.20,3.00,2.30,1.70",  # home win, under
    "E0,,,,,,,,,,,,,,,",                                                      # blank trailing row
]


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "2324").mkdir()
        (root / "2324" / "E0.csv").write_text("\n".join([HEADER, *ROWS]) + "\n")
        self.patches = [mock.patch.object(tools, "DATA_DIR", root),
                        mock.patch.object(tools, "REPORTS_DIR", root / "reports")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_list_available_data(self):
        self.assertEqual(tools.list_available_data()["leagues"]["E0"]["seasons"], ["2324"])

    def test_summary_skips_blank_rows_and_counts_rates(self):
        s = tools.market_summary("E0", ["2324"])["per_season"]["2324"]
        self.assertEqual(s["matches"], 4)
        self.assertEqual(s["home_rate"], 0.5)
        self.assertEqual(s["over25_rate"], 0.5)

    def test_backtest_home_all(self):
        r = tools.backtest("E0", ["2324"], "home")
        self.assertEqual(r["bets"], 4)
        # wins at 2.00 and 2.50 -> +1.0 +1.5, losses -2 => +0.5 profit
        self.assertEqual(r["profit_units"], 0.5)
        self.assertEqual(r["roi"], 0.125)
        self.assertIn("warning", r)  # tiny sample is flagged

    def test_backtest_probability_filter(self):
        # Only the 1.50 favourite has a fair home prob above 0.6.
        r = tools.backtest("E0", ["2324"], "home", min_prob=0.6)
        self.assertEqual(r["bets"], 1)
        self.assertEqual(r["profit_units"], -1.0)

    def test_backtest_max_odds_warns(self):
        self.assertIn("warning_max", tools.backtest("E0", ["2324"], "over25", odds_source="max"))

    def test_bad_input_returns_error_for_claude(self):
        text, is_error = tools.run_tool("backtest", {"league": "XX", "seasons": ["2324"], "selection": "home"})
        self.assertTrue(is_error)
        self.assertIn("Unknown league", text)
        _, is_error = tools.run_tool("nope", {})
        self.assertTrue(is_error)

    def test_write_report(self):
        out = json.loads(tools.run_tool("write_report", {"title": "Test", "markdown": "hi"})[0])
        self.assertTrue(out["saved_to"].endswith("-test.md"))


def _resp(stop_reason, *blocks):
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks))


def _tool_use(id_, name, input_):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def _text(t):
    return SimpleNamespace(type="text", text=t)


class FakeClient:
    """Plays back scripted model responses and records each request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


class LoopTests(unittest.TestCase):
    def test_loop_runs_tools_and_finishes(self):
        client = FakeClient([
            _resp("tool_use", _text("Checking data."), _tool_use("t1", "list_available_data", {})),
            _resp("tool_use",
                  _tool_use("t2", "backtest", {"league": "XX", "seasons": ["2324"], "selection": "home"}),
                  _tool_use("t3", "list_available_data", {})),
            _resp("end_turn", _text("No edge found.")),
        ])
        with mock.patch.object(tools, "DATA_DIR", Path(tempfile.mkdtemp())):
            answer = agent.run("question", client=client, verbose=False)

        self.assertEqual(answer, "No edge found.")
        self.assertEqual(len(client.requests), 3)
        # Both tool results from turn 2 come back in ONE user message, with the error flagged.
        results = client.requests[2]["messages"][-1]["content"]
        self.assertEqual([r["tool_use_id"] for r in results], ["t2", "t3"])
        self.assertTrue(results[0]["is_error"])
        self.assertEqual(client.requests[0]["model"], "claude-opus-5-5")

    def test_loop_gives_up_after_max_turns(self):
        looping = [_resp("tool_use", _tool_use(f"t{i}", "list_available_data", {})) for i in range(3)]
        with self.assertRaises(RuntimeError):
            agent.run("q", client=FakeClient(looping), max_turns=3, verbose=False)

    def test_refusal_stops(self):
        with self.assertRaises(RuntimeError):
            agent.run("q", client=FakeClient([_resp("refusal")]), verbose=False)


if __name__ == "__main__":
    unittest.main()
