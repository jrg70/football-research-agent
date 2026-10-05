"""Tools the agent can call.

Each tool is a plain Python function over football-data.co.uk CSVs, plus a JSON
schema telling Claude what it does. The agent never runs arbitrary code: it can
only ask structured questions, which keeps it safe and its reasoning auditable.
"""

import csv
import json
import math
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
REPORTS_DIR = Path(__file__).parent / "reports"

LEAGUES = {
    "E0": "England Premier League",
    "E1": "England Championship",
    "D1": "Germany Bundesliga",
    "SP1": "Spain La Liga",
    "I1": "Italy Serie A",
    "F1": "France Ligue 1",
    "N1": "Netherlands Eredivisie",
    "P1": "Portugal Primeira Liga",
    "T1": "Turkey Super Lig",
    "SC0": "Scotland Premiership",
}

# Closing-odds columns per bookmaker source, per selection.
ODDS_COLUMNS = {
    "avg": {"home": "AvgCH", "draw": "AvgCD", "away": "AvgCA", "over25": "AvgC>2.5", "under25": "AvgC<2.5"},
    "pinnacle": {"home": "PSCH", "draw": "PSCD", "away": "PSCA", "over25": "PC>2.5", "under25": "PC<2.5"},
    "max": {"home": "MaxCH", "draw": "MaxCD", "away": "MaxCA", "over25": "MaxC>2.5", "under25": "MaxC<2.5"},
}
MARKETS = {"1x2": ("home", "draw", "away"), "ou25": ("over25", "under25")}
SELECTIONS = [s for group in MARKETS.values() for s in group]


# ---------------------------------------------------------------- data loading

def _num(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if x > 1.0 else None  # decimal odds must exceed 1.0


def _parse_date(text):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def load_matches(league, season):
    """Rows for one league-season with full-time goals parsed. Missing file -> []."""
    path = DATA_DIR / season / f"{league}.csv"
    if not path.exists():
        return []
    rows = []
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        for row in csv.DictReader(f):
            try:
                row["_hg"], row["_ag"] = int(row["FTHG"]), int(row["FTAG"])
            except (KeyError, TypeError, ValueError):
                continue  # postponed / blank trailing lines
            row["_date"] = _parse_date(row.get("Date", ""))
            row["_season"] = season
            rows.append(row)
    return rows


def _won(row, selection):
    hg, ag = row["_hg"], row["_ag"]
    return {
        "home": hg > ag,
        "draw": hg == ag,
        "away": hg < ag,
        "over25": hg + ag > 2,
        "under25": hg + ag < 3,
    }[selection]


def _fair_probs(row, market, source):
    """De-vigged (proportional) implied probabilities for every selection in a market."""
    cols = ODDS_COLUMNS[source]
    odds = [_num(row.get(cols[s])) for s in MARKETS[market]]
    if None in odds:
        return None
    inv = [1 / o for o in odds]
    total = sum(inv)
    return {s: p / total for s, p in zip(MARKETS[market], inv)}


def _market_of(selection):
    return next(m for m, sels in MARKETS.items() if selection in sels)


def _check(league, seasons):
    if league not in LEAGUES:
        raise ValueError(f"Unknown league '{league}'. Valid: {', '.join(LEAGUES)}")
    if not seasons:
        raise ValueError("Give at least one season, e.g. ['2324'].")


# ---------------------------------------------------------------- tools

def list_available_data():
    available = {}
    if DATA_DIR.exists():
        for season_dir in sorted(p for p in DATA_DIR.iterdir() if p.is_dir()):
            for csv_path in sorted(season_dir.glob("*.csv")):
                available.setdefault(csv_path.stem, []).append(season_dir.name)
    if not available:
        return {"error": "No data downloaded. Run `python fetch_data.py` first."}
    return {
        "leagues": {lg: {"name": LEAGUES.get(lg, lg), "seasons": s} for lg, s in available.items()},
        "season_format": "'2324' means the 2023-24 season",
    }


def market_summary(league, seasons):
    """Base rates plus how well the closing line was calibrated."""
    _check(league, seasons)
    out = {"league": league, "seasons": seasons, "per_season": {}}
    for season in seasons:
        rows = load_matches(league, season)
        if not rows:
            out["per_season"][season] = {"error": "no data for this season"}
            continue
        n = len(rows)
        stats = {"matches": n, "avg_goals": round(sum(r["_hg"] + r["_ag"] for r in rows) / n, 3)}
        for sel in SELECTIONS:
            stats[f"{sel}_rate"] = round(sum(_won(r, sel) for r in rows) / n, 4)
        # Calibration: mean de-vigged closing probability vs actual hit rate.
        for sel in SELECTIONS:
            probs = [(_fair_probs(r, _market_of(sel), "avg"), r) for r in rows]
            probs = [(p[sel], _won(r, sel)) for p, r in probs if p]
            if probs:
                stats[f"{sel}_implied_vs_actual"] = {
                    "implied": round(sum(p for p, _ in probs) / len(probs), 4),
                    "actual": round(sum(w for _, w in probs) / len(probs), 4),
                }
        out["per_season"][season] = stats
    return out


def backtest(league, seasons, selection, odds_source="avg", min_prob=0.0, max_prob=1.0):
    """Flat 1-unit stakes on `selection` at closing odds, filtered by its fair probability."""
    _check(league, seasons)
    if selection not in SELECTIONS:
        raise ValueError(f"selection must be one of {SELECTIONS}")
    if odds_source not in ODDS_COLUMNS:
        raise ValueError(f"odds_source must be one of {list(ODDS_COLUMNS)}")
    market = _market_of(selection)
    odds_col = ODDS_COLUMNS[odds_source][selection]

    profits, wins, skipped = [], 0, 0
    for season in seasons:
        for row in load_matches(league, season):
            probs = _fair_probs(row, market, "avg")  # filter on consensus, bet at chosen source
            odds = _num(row.get(odds_col))
            if not probs or odds is None:
                skipped += 1
                continue
            if not (min_prob <= probs[selection] <= max_prob):
                continue
            won = _won(row, selection)
            wins += won
            profits.append(odds - 1 if won else -1.0)

    n = len(profits)
    result = {
        "league": league, "seasons": seasons, "selection": selection, "odds_source": odds_source,
        "filter": {"min_prob": min_prob, "max_prob": max_prob},
        "bets": n, "skipped_missing_odds": skipped,
    }
    if n == 0:
        result["note"] = "No bets matched. Widen the probability filter or check the seasons."
        return result
    mean = sum(profits) / n
    sd = math.sqrt(sum((p - mean) ** 2 for p in profits) / (n - 1)) if n > 1 else 0.0
    half = 1.96 * sd / math.sqrt(n)
    result.update({
        "hit_rate": round(wins / n, 4),
        "profit_units": round(sum(profits), 2),
        "roi": round(mean, 4),
        "roi_95ci": [round(mean - half, 4), round(mean + half, 4)],
        "significant": (mean - half) > 0 or (mean + half) < 0,
    })
    if n < 200:
        result["warning"] = f"Only {n} bets: treat any ROI here as noise."
    if odds_source == "max":
        result["warning_max"] = "Max odds are best-price-across-books: optimistic and hard to get in practice."
    return result


def write_report(title, markdown):
    REPORTS_DIR.mkdir(exist_ok=True)
    slug = "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")[:60] or "report"
    path = REPORTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{slug}.md"
    path.write_text(f"# {title}\n\n{markdown}\n", encoding="utf-8")
    return {"saved_to": f"reports/{path.name}"}


# ---------------------------------------------------------------- schemas for Claude

_SEASONS = {"type": "array", "items": {"type": "string"}, "description": "Season codes like ['2223', '2324']."}
_LEAGUE = {"type": "string", "enum": list(LEAGUES), "description": "football-data.co.uk division code."}

TOOLS = [
    {
        "name": "list_available_data",
        "description": "List which leagues and seasons are downloaded. Call this first.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "market_summary",
        "description": (
            "Per-season base rates (home/draw/away, over/under 2.5, average goals) and calibration: "
            "the mean de-vigged closing probability vs the actual hit rate for each selection. "
            "A large gap between implied and actual is a hint worth backtesting, not proof."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"league": _LEAGUE, "seasons": _SEASONS},
            "required": ["league", "seasons"],
            "additionalProperties": False,
        },
    },
    {
        "name": "backtest",
        "description": (
            "Flat-stake backtest: bet 1 unit on `selection` in every match whose de-vigged consensus "
            "closing probability for that selection is within [min_prob, max_prob], at closing odds "
            "from `odds_source`. Returns bets, hit rate, ROI and a 95% confidence interval. Use it "
            "to find a rule on some seasons, then re-run the same rule on different seasons to check it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "league": _LEAGUE,
                "seasons": _SEASONS,
                "selection": {"type": "string", "enum": SELECTIONS},
                "odds_source": {
                    "type": "string", "enum": list(ODDS_COLUMNS),
                    "description": "avg = market average (default), pinnacle = sharp book, max = best price.",
                },
                "min_prob": {"type": "number", "minimum": 0, "maximum": 1},
                "max_prob": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["league", "seasons", "selection"],
            "additionalProperties": False,
        },
    },
    {
        "name": "write_report",
        "description": "Save the final findings as a Markdown report. Call once, at the end.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "markdown": {"type": "string", "description": "Report body in Markdown."},
            },
            "required": ["title", "markdown"],
            "additionalProperties": False,
        },
    },
]

_FUNCTIONS = {
    "list_available_data": list_available_data,
    "market_summary": market_summary,
    "backtest": backtest,
    "write_report": write_report,
}


def run_tool(name, tool_input):
    """Run a tool and return (json_text, is_error). Errors go back to Claude so it can recover."""
    try:
        if name not in _FUNCTIONS:
            raise ValueError(f"Unknown tool '{name}'")
        if not isinstance(tool_input, dict):
            raise ValueError("Tool input must be a JSON object")
        result = _FUNCTIONS[name](**tool_input)
        return json.dumps(result, default=str), False
    except (TypeError, ValueError) as e:
        return f"Error: {e}", True
