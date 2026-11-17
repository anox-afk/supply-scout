---
name: supply-scout
description: Build a cold-call lead list of property hosts/operators for a booking platform, end to end. Scrapes a city's listings from Airbnb via Apify, filters to the ideal-customer profile, cross-checks against your own platform's listings so hosts already on it are removed, then runs a scripted contact enrichment (opens each host's Instagram page and reads the bio, searches directories, reads the brand's website) to add phone, WhatsApp link, email, website, Instagram link, followers, Airbnb host details and a decision-maker hint, and outputs a scored call sheet. Use for host acquisition, supply-side lead generation, or building a cold-call list of villas, homestays, farm stays or boutique hotels. Triggers: "find hosts", "lead list", "scrape Airbnb", "who is not on our platform", "supply leads", "call sheet", "enrich contacts".
---

# Supply Scout

Turns a city into a ranked, contact-enriched call sheet of property operators who are on Airbnb but **not yet on your platform**. Built for zero-commission / subscription booking platforms recruiting supply (hosts). Defaults target Unpaqd; change the flags to use it for any platform.

## What it does (pipeline, every step is a script)
1. **Scrape** premium listings for a city from Airbnb (`scrape_airbnb.py`, Apify actor `tri_angle/airbnb-scraper`).
2. **Capture your platform's own listings** (`fetch_platform_listings.py`, Firecrawl) so the next step can tell who is already on it.
3. **Build leads** (`build_leads.py`): dedupe by host, flag multi-property operators, compute per-night price, apply the ICP filter, score, and **remove hosts already on your platform** by matching property titles.
4. **Enrich contacts** (`enrich_contacts.py`): for every target it
   - finds the Instagram profile,
   - **opens the Instagram page and reads the bio** (followers, posts, display name, phone, email, website, WhatsApp),
   - searches directories (Justdial, Google listings) and the brand's own website for phones and emails,
   - adds Airbnb host details (about text, co-hosts, years hosting, response rate) and a decision-maker hint,
   - picks the best phone, labels its **source and confidence**, and builds a `wa.me` WhatsApp link.
5. **Output**: one scored CSV call sheet with empty `contacted` / `outcome` columns.

## Prerequisites
- **Python 3** (standard library only, no installs).
- **Apify token** (`APIFY_TOKEN`) with credit: apify.com, free tier is about $5/month, about 1,000 listings at $0.004 each.
- **Firecrawl key** (`FIRECRAWL_API_KEY`, firecrawl.dev, free tier 1,000 credits): used to read your platform's JavaScript pages and to run web searches. Each search costs 2 credits, each page scrape 1. Without it, steps 2 and the search parts of step 4 are skipped, but the Instagram bio reading still works for any host whose Instagram handle you already know.
- Set keys in the shell: `export APIFY_TOKEN=...` / PowerShell `$env:APIFY_TOKEN="..."`.

## How to run

### Step 1: Scrape a city
```
python scripts/scrape_airbnb.py --city "Jaipur, Rajasthan" --price-min 7000 --max 200 --out data/raw_jaipur.json
```
`--price-min` is the per-night floor (default INR), `--max` caps listings (Airbnb returns up to about 240 per query).

### Step 2: Capture your platform's own listings (the leak guard)
**Do not trust the sitemap alone.** In the real run Unpaqd's sitemap listed 726 properties in the morning and zero by the afternoon, and 10 of 12 live Jaipur listings were never in it.
1. Save a sitemap snapshot whenever it has listings: `curl -A "Mozilla/5.0" https://YOURPLATFORM.com/sitemap.xml -o data/platform_sitemap_YYYY-MM-DD.xml`
2. Scrape the live city search page on 2 or 3 date ranges (search pages hide dates that are booked):
```
python scripts/fetch_platform_listings.py --url "https://unpaqd.com/listing?cityName=Jaipur&lat=26.9124&lng=75.7873&guest=2&date_range=2026-12-01%2C2026-12-03" --out data/platform_jaipur.txt
```
3. Note hosts already on the platform (listing-team or aggregator accounts) for `--exclude-hosts`.

### Step 3: Build the scored lead list
```
python scripts/build_leads.py --raw data/raw_jaipur.json --out-dir data/leads_jaipur --price-min 7000 \
  --sitemap-url "https://unpaqd.com/sitemap.xml,data/platform_sitemap_YYYY-MM-DD.xml" \
  --platform-titles data/platform_jaipur.txt --exclude-hosts "KnownHostName" --platform-name Unpaqd
```
Property titles are compared using distinctive words only (generic words and city names are ignored). Each host gets `on_platform`: `YES` (removed, evidence printed), `CHECK` (kept, flagged: open both listings and decide), or `no`. Output: `all_hosts.csv` and `targets.csv`.

Scoring: +3 for 3+ listings, +2 for 2; +2 if top price at or above `--premium`, +1 if at or above `--price-min`; +1 rating 4.7+ with 20+ reviews; +1 Superhost; +1 brand-style name.

### Step 4: Enrich contacts
```
python scripts/enrich_contacts.py --targets data/leads_jaipur/targets.csv --raw "Jaipur=data/raw_jaipur.json" \
  --cache data/enrich_cache.json --out data/leads_jaipur/targets_enriched.csv
```
- Several cities in one sheet: add a `city` column and pass `--raw "Jaipur=a.json,Mussoorie=b.json"`.
- **Resumable**: everything is cached in `--cache`. Stop it any time; re-running continues where it stopped and spends no credits twice.
- `--top 40` enriches only the 40 best-scored rows. `--deep` also runs the directory/website search for hosts that already have a phone (to collect emails and websites). `--max-credits` caps Firecrawl spend per run (default 300). `--no-directory` skips step D.
- **Free-tier note:** Firecrawl's free plan allows only a few searches a minute, so about 200 hosts takes 30 to 60 minutes. Run it in the background.
- Instagram is read directly (free, no login) at about 1.5 seconds per profile. If Instagram starts refusing, the script stops cleanly; run it again later.

Columns it adds: `instagram_link`, `instagram_match` (verified / high / low / not found), `ig_handle`, `ig_followers`, `ig_posts`, `ig_name`, `ig_bio`, `email`, `website`, `phone`, `phone_source`, `phone_confidence` (verified / high / medium / low), `phones_all` (every number found with its source), `whatsapp_link`, `decision_maker`, `decision_maker_hint`, `host_about`, `cohosts`, `years_hosting`, `airbnb_response`, `enriched_on`. Existing values in `phone`, `phone_source`, `decision_maker` and `note` are never overwritten. The CSV is written with a BOM so Excel and Google Sheets show emoji and accents correctly.

**How phones are found and trusted**
| Source | Confidence |
|---|---|
| Entered by hand earlier | verified |
| Instagram bio of a profile whose name matches the brand | high |
| Brand's own website (tel: links, visible text, WhatsApp links) | high |
| Directory/search snippet that mentions the brand and city | medium |
| Instagram profile that might be the wrong business | low |

**Two rules that prevent wasted calls**
- **Verify the entity**: a same-name business is a trap ("Amara Jaipur" the apartments is not "Amara Jaipur" the wedding venue). `instagram_match = low` or `check` means open the profile first. Read `ig_bio` to confirm it is a stay and not, say, an art gallery.
- WhatsApp-check any `medium` or `low` number before calling.

### Step 5: Hand over the call sheet
The enriched CSV is the call list. Sort by `score` and `phone_confidence`, call the verified and high ones first, and fill `contacted` / `outcome` as you go.

## Configuration for a different platform
- `--sitemap-url`, `--platform-titles`, `--link-pattern`, `--exclude-hosts`, `--platform-name`: the cross-check that removes hosts already on your platform.
- `--price-min`, `--premium`, `--min-reviews`, `--min-rating`: your ICP thresholds.
- `--currency` / `--locale` in step 1 for non-India markets. Phone detection is India (+91) mobile numbers; change `PHONE_RE` in `enrich_contacts.py` for other countries.

## Limits and honesty
- Airbnb's terms discourage scraping: a contract and blocking risk, so run at sensible volume.
- About 75 to 85% of Instagram-marketing operators publish a phone in their bio. Single-property hosts and Airbnb-message-only hosts often have no public number at all.
- The Instagram page gives the bio but not the "link in bio" website. Websites come from the bio text, directory search and brand-name search, so some are missed.
- Cold-calling rules vary by country (India: TRAI governs promotional calls). Check local rules.
- The platform cross-check only sees listings you captured. **Last line of defence: ask on the call "are you already listed on <platform>?"**
- Platform pages may show placeholder coordinates, so do not treat location proximity as proof of a match.

See `METHOD.md` for the exact real run this skill was built from.
