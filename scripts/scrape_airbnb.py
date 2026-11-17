#!/usr/bin/env python3
"""Scrape Airbnb listings for a city via the Apify actor tri_angle/airbnb-scraper.

Standard library only. Reads the Apify token from --token or the APIFY_TOKEN env var.
Usage:
  python scrape_airbnb.py --city "Jaipur, Rajasthan" --price-min 7000 --max 200 --out data/raw_jaipur.json
"""
import argparse, json, os, sys, pathlib, urllib.request, urllib.error

ACTOR = "tri_angle~airbnb-scraper"


def main():
    ap = argparse.ArgumentParser(description="Scrape Airbnb for a city via Apify.")
    ap.add_argument("--city", required=True, help='e.g. "Jaipur, Rajasthan"')
    ap.add_argument("--price-min", type=int, default=7000, help="per-night floor in --currency")
    ap.add_argument("--max", type=int, default=200, help="max listings (Airbnb caps ~240/query)")
    ap.add_argument("--currency", default="INR")
    ap.add_argument("--locale", default="en-IN")
    ap.add_argument("--token", default=os.environ.get("APIFY_TOKEN"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    if not a.token:
        sys.exit("ERROR: no Apify token. Set APIFY_TOKEN or pass --token.")

    body = json.dumps({
        "locationQueries": [a.city],
        "currency": a.currency,
        "priceMin": a.price_min,
        "maxResults": a.max,
        "locale": a.locale,
    }).encode()

    url = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items?token={a.token}"
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:400]
        if e.code in (402, 403):
            sys.exit(f"ERROR {e.code}: Apify account likely out of credit or blocked.\n{detail}")
        sys.exit(f"ERROR {e.code}: {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"ERROR: network/connection problem: {e}")

    if isinstance(data, dict) and "error" in data:
        sys.exit(f"ERROR: Apify returned: {data['error']}")

    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    json.dump(data, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"Saved {len(data)} listings to {out}")


if __name__ == "__main__":
    main()
