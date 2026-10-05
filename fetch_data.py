"""Download results + closing odds from football-data.co.uk into data/<season>/<league>.csv.

    python fetch_data.py                         # default leagues, last 5 seasons
    python fetch_data.py --leagues E0 D1 --seasons 2223 2324 2425
"""

import argparse
import urllib.request

from tools import DATA_DIR, LEAGUES

DEFAULT_SEASONS = ["2021", "2122", "2223", "2324", "2425"]
DEFAULT_LEAGUES = ["E0", "D1", "SP1", "I1", "F1"]
URL = "https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--leagues", nargs="+", default=DEFAULT_LEAGUES, choices=list(LEAGUES))
    parser.add_argument("--seasons", nargs="+", default=DEFAULT_SEASONS)
    args = parser.parse_args()

    for season in args.seasons:
        (DATA_DIR / season).mkdir(parents=True, exist_ok=True)
        for league in args.leagues:
            dest = DATA_DIR / season / f"{league}.csv"
            url = URL.format(season=season, league=league)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "football-research-agent"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    dest.write_bytes(r.read())
                print(f"ok    {season} {league}")
            except Exception as e:  # keep going; one missing file shouldn't stop the rest
                print(f"fail  {season} {league}: {e}")


if __name__ == "__main__":
    main()
