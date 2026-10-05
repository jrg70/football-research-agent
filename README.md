# Betting Research Agent

A small, readable example of **agentic AI**: you ask a football betting question in plain English, and Claude researches it by deciding on its own which analysis tools to call, in what order, and when it has enough evidence to stop.

```bash
python agent.py "Is there any edge in backing heavy home favourites in the Bundesliga?"
```

```
[turn 1] -> list_available_data({})
[turn 2] -> market_summary({"league": "D1", "seasons": ["2021", "2122", "2223", "2324", "2425"]})
[turn 3] -> backtest({"league": "D1", "seasons": ["2021", "2122", "2223"], "selection": "home", "min_prob": 0.65})
[turn 4] -> backtest({"league": "D1", "seasons": ["2324", "2425"], "selection": "home", "min_prob": 0.65})
...
============================================================
Verdict: the in-sample ROI did not hold up on held-out seasons. No edge.
```

*(Illustrative trace. Each run is different because the model chooses its own path.)*

## What makes it "agentic"

A chatbot answers once. An agent runs a **loop**:

```
          +--------------------------------------+
          v                                      |
  question --> Claude --(asks for a tool)--> run tool --> result
                 |
                 +--(done)--> report + answer
```

That loop is about 40 lines in [`agent.py`](agent.py) (`run()`), written by hand with no framework, so you can see exactly what happens:

1. Send the conversation and the tool definitions to Claude.
2. If Claude asks for tools, run them and append the results.
3. Repeat until Claude stops asking, with a turn limit as a safety net.

The interesting behaviour comes from the model, not the code. The system prompt tells it to be skeptical: pick a rule on some seasons, then **re-test the same rule on seasons it didn't use**, before claiming an edge. You can watch it plan, notice failures (for example a typo'd league code comes back as a tool error and it corrects itself), and change course.

## Tools

Defined in [`tools.py`](tools.py). Each one is a plain Python function plus a JSON schema:

| Tool | What it does |
|---|---|
| `list_available_data` | Lists which leagues and seasons are downloaded |
| `market_summary` | Base rates (1X2, over/under 2.5, goals) and how well the closing line was calibrated |
| `backtest` | Flat-stake ROI with a 95% CI for one selection, filtered by de-vigged closing probability, at average, Pinnacle or best-price odds |
| `write_report` | Saves the final findings to `reports/` |

The agent **can't run arbitrary code**: it can only ask structured questions. That keeps it safe and makes every step auditable.

## Setup

Requires Python 3.10+ and an [Anthropic API key](https://console.anthropic.com/).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python fetch_data.py          # downloads 5 leagues x 5 seasons from football-data.co.uk
python agent.py "Do over 2.5 goals bets in Serie A beat the closing line?"
```

Run the tests (no API key needed, because a fake client drives the loop):

```bash
python -m unittest discover -s tests -v
```

## Example questions

- "Are draws systematically underpriced in any of the big five leagues?"
- "Is the favourite-longshot bias visible in Premier League closing odds?"
- "Compare the bookmaker margin at average vs Pinnacle odds across leagues."
- "Has the 2023 stoppage-time rule change shown up in over 2.5 goals rates?"

## Notes

- Model: `claude-opus-5-5`, with server-side refusal fallbacks enabled.
- Data: [football-data.co.uk](https://www.football-data.co.uk/). Please respect their terms; the data is not redistributed here.
- This is a demo of agent design, **not betting advice**. Closing lines are very efficient, and the agent will usually tell you so.
