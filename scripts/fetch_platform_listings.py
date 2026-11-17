#!/usr/bin/env python3
"""Scrape your platform's own city search page (JavaScript-rendered) and save every
listing it shows, so build_leads.py can cross-check against listings that are MISSING
from the sitemap. Uses the Firecrawl API (key in FIRECRAWL_API_KEY or --key).

Run it a few times with different dates: search pages usually hide listings that are
unavailable on the chosen dates.

Usage:
  python fetch_platform_listings.py --url "https://unpaqd.com/listing?cityName=Jaipur&lat=26.9124&lng=75.7873&guest=2&date_range=2026-12-01%2C2026-12-03" \
     --out data/platform_jaipur.txt
Output: one line per listing, "title<TAB>url". Appends and de-duplicates if the file exists.
"""
import argparse, json, os, sys, pathlib, urllib.request, urllib.error, urllib.parse


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="platform city/search page URL")
    ap.add_argument("--out", required=True)
    ap.add_argument("--link-pattern", default="/property/")
    ap.add_argument("--scrolls", type=int, default=10)
    ap.add_argument("--key", default=os.environ.get("FIRECRAWL_API_KEY"))
    a = ap.parse_args()
    if not a.key:
        sys.exit("ERROR: no Firecrawl key. Set FIRECRAWL_API_KEY or pass --key.")

    actions = []
    for _ in range(a.scrolls):
        actions += [{"type": "scroll", "direction": "down"}, {"type": "wait", "milliseconds": 1500}]
    body = {"url": a.url, "formats": ["links"], "waitFor": 5000, "actions": actions}
    req = urllib.request.Request("https://api.firecrawl.dev/v2/scrape", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + a.key, "Content-Type": "application/json"})
    try:
        d = json.load(urllib.request.urlopen(req, timeout=300))
    except urllib.error.HTTPError as e:
        sys.exit(f"ERROR {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}")
    links = ((d.get("data") or {}).get("links")) or []

    found = {}
    for u in links:
        if a.link_pattern not in u:
            continue
        clean = u.split("?")[0]
        title = urllib.parse.unquote(clean.rstrip("/").split("/")[-1]).replace("-", " ").strip()
        found[clean] = title

    out = pathlib.Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if out.exists():
        for ln in out.read_text(encoding="utf-8").splitlines():
            p = ln.split("\t")
            if len(p) == 2:
                existing[p[1]] = p[0]
    existing.update({u: t for u, t in found.items()})
    out.write_text("\n".join(f"{t}\t{u}" for u, t in existing.items()) + "\n", encoding="utf-8")
    print(f"Found {len(found)} listings on this page; {len(existing)} total in {out}")


if __name__ == "__main__":
    main()
