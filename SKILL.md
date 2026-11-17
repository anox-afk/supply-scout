---
name: supply-scout
description: Build a cold-call lead list of property hosts/operators for a booking platform. Scrapes a city's listings from an OTA (Airbnb) via Apify, filters to the ideal-customer profile, cross-checks against your own platform's sitemap to keep only hosts NOT already listed, enriches each with phone + Instagram + decision-maker using web search, and outputs a scored call sheet. Use for host acquisition, supply-side lead generation, or building a cold-call list of villas, homestays, farm stays or boutique hotels. Triggers: "find hosts", "lead list", "scrape Airbnb", "who is not on our platform", "supply leads", "call sheet".
---

# Supply Scout

Turns a city into a ranked, phone-enriched call sheet of property operators who are on Airbnb but **not yet on your platform**. Built for zero-commission / subscription booking platforms recruiting supply (hosts). Defaults target Unpaqd; change the config to use it for any platform.

## What it does (pipeline)
1. **Scrape** premium listings for a city from Airbnb (Apify actor `tri_angle/airbnb-scraper`).
2. **Build leads**: dedupe by host, flag multi-property operators, compute per-night price, apply the ICP filter, score, and **cross-check every listing's title against your platform's own listings** (sitemap snapshot + a live scrape of the platform's city page) so hosts already on it don't leak into the call list.
3. **Enrich**: for the top targets, find a phone/WhatsApp, Instagram handle and decision-maker using your web search tool.
4. **Output**: a scored `targets.csv` call sheet, columns ready for outcomes.

## Prerequisites
- **Apify token** with credit. Get one free at apify.com, then set it: `export APIFY_TOKEN=apify_api_xxx` (Windows PowerShell: `$env:APIFY_TOKEN="apify_api_xxx"`). The free tier gives ~$5/month ≈ ~1,000 listings at $0.004 each.
- **Python 3** (standard library only — no pip installs).
- **A web search tool** (Claude Code's WebSearch, Codex web search, or similar) for the enrichment step.
- **Firecrawl key** (firecrawl.dev, free tier) for `fetch_platform_listings.py`: `FIRECRAWL_API_KEY`. Needed because platform search pages are JavaScript-rendered.

## How to run

### Step 1 — Scrape a city
```
python scripts/scrape_airbnb.py --city "Jaipur, Rajasthan" --price-min 7000 --max 200 --out data/raw_jaipur.json
```
- `--price-min` is the per-night floor in the chosen currency (default INR). Set it to your ICP floor.
- `--max` caps listings (Airbnb returns up to ~240 per query). Start with 200.
- Reads the token from `APIFY_TOKEN` (or pass `--token`).

### Step 2a — Capture your platform's own listings (the leak guard)
**Do not trust the sitemap alone.** In the real run, Unpaqd's sitemap listed 726 properties in the morning and *zero* by the afternoon, and 10 of 12 live Jaipur listings were never in it. So capture listings from three places:
1. **Save a sitemap snapshot whenever it has listings** (`curl -A "Mozilla/5.0" https://YOURPLATFORM.com/sitemap.xml -o data/platform_sitemap_YYYY-MM-DD.xml`). Keep it; reuse it.
2. **Scrape the live city search page** on 2–3 different date ranges (search pages hide listings booked on the chosen dates):
```
python scripts/fetch_platform_listings.py --url "https://unpaqd.com/listing?cityName=Jaipur&lat=26.9124&lng=75.7873&guest=2&date_range=2026-12-01%2C2026-12-03" --out data/platform_jaipur.txt
```
3. **List hosts you already know are on the platform** (listing-team or aggregator accounts that hold many owners' properties) for `--exclude-hosts`.

### Step 2b — Build the scored lead list
```
python scripts/build_leads.py --raw data/raw_jaipur.json --out-dir data/leads_jaipur \
  --price-min 7000 \
  --sitemap-url "https://unpaqd.com/sitemap.xml,data/platform_sitemap_YYYY-MM-DD.xml" \
  --platform-titles data/platform_jaipur.txt --exclude-hosts "KnownHostName" --platform-name Unpaqd
```
The cross-check compares **property titles** (platforms copy titles from Airbnb when they onboard a host) using distinctive words, ignoring generic ones like "villa", "pool", "luxury" and city names. Each host gets `on_platform`:
- `YES` → strong title match or excluded host → **removed** from targets (printed as REMOVED with the evidence).
- `CHECK` → borderline match → **kept but flagged**; open both listings and decide before calling.
- `no` → no match found.
The script warns loudly if the sitemap has no listings or if fewer than 50 platform listings were loaded.
Produces:
- `data/leads_jaipur/all_hosts.csv` — every host, deduped and scored.
- `data/leads_jaipur/targets.csv` — only ICP-qualified hosts **not on your platform**, ranked. This is the call sheet.

It also prints the **top targets with brand hints** pulled from their listing titles — use those for Step 3.

**Scoring (edit in `references/scoring.md` or via flags):** +3 for 3+ listings, +2 for 2 listings; +2 if top price ≥ premium floor, +1 if ≥ base floor; +1 for rating ≥4.7 with ≥20 reviews; +1 Superhost; +1 brand-style name. The ICP keeps multi-property operators regardless of reviews (the brand is the qualifier) and single owners only if they clear the quality bar.

### Step 3 — Enrich the top targets (you, the model, do this with web search)
For each target (work down the ranked list, ~15–25), **search the brand/property name + city, not the host's first name** — the brand (e.g. "Shree Nikunj Jaipur", "Saiba villa Jaipur") is what's findable. Run queries like:
- `"<brand> <city> contact number"`
- `site:instagram.com <brand> <city>`
- `"<brand> <city>" justdial` / `google maps`

The phone is **almost always in the Instagram bio**, which shows up in search-result snippets (you rarely need to open Instagram). Directories (Justdial, Google Business) are the backup.

For each target record: `phone`, `phone_source`, `instagram`, `decision_maker`, and a `note`.

**Optional, for scale (hundreds of leads):** instead of manual search you can use a dedicated Instagram profile scraper to pull bios/contacts in bulk — an Apify Instagram Profile Scraper actor, or a PhantomBuster Instagram phantom (needs an Instagram session cookie). For B2B decision-makers, a LinkedIn profile scraper (PhantomBuster, Apify) finds the founder/owner behind a brand. These cost money and setup; the free web-search method above already lands ~75–85%, so only reach for them at volume.

**Two rules that prevent wasted calls:**
- **Verify the entity matches** — a same-name business is a trap. Confirm the brand, city and property type line up before trusting a number (e.g. "Amara Jaipur" the apartments ≠ "Amara Jaipur" the wedding venue).
- Mark any number you could not confirm as `UNVERIFIED` and WhatsApp-check it before calling.

Write these back into `targets.csv` (fill the empty `phone` / `phone_source` / `instagram` / `decision_maker` / `note` columns).

### Step 4 — Hand over the call sheet
`targets.csv` is now the call list: host, listings, price band, phone, decision-maker, and empty `contacted` / `outcome` columns to fill as calls happen.

## Configuration for a different platform
- `--sitemap-url` (URL and/or saved snapshot files, comma-separated), `--platform-titles` (files from `fetch_platform_listings.py`, comma-separated), `--link-pattern` (URL fragment that marks a listing page, default `/property/`), `--exclude-hosts`, `--platform-name`: the cross-check that removes hosts already on your platform.
- `--price-min`, `--premium`, `--min-reviews`, `--min-rating`: your ICP thresholds.
- `--currency` / `--locale` in Step 1 for non-India markets.

## Limits & honesty
- Airbnb's terms discourage scraping; this is a contract/blocking risk, run it at sensible volume. Apify handles proxies.
- Phone hit-rate for premium Instagram-marketing operators is ~75–85%; a tail (Airbnb-DM-only hosts) will have no public number.
- Cold-calling rules vary by country (in India, TRAI governs promotional calls). Check local rules and use an appropriate number.
- The cross-check can only see platform listings you captured. A listing that is booked on every date you scraped, missing from the sitemap, and retitled beyond recognition can still slip through. **Last line of defence: ask on the call "are you already listed on <platform>?"**, and check against the platform's internal host list when you have access.
- Platform pages may show placeholder coordinates (e.g. every listing at the city-centre point), so don't treat location proximity as proof of a match.

See `METHOD.md` for the exact real-world run this skill was distilled from.
