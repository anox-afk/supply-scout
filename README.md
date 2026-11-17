# Supply Scout

A skill for Claude Code / Codex that builds a cold-call lead list of property operators for a booking platform, in one command sequence.

**What it does:** scrape a city's premium listings from Airbnb → keep the multi-property operators that fit your ideal profile → remove anyone already on your platform → open each host's Instagram page and read the bio, search directories and their website → output a scored call sheet CSV with phone, WhatsApp link, email, website, Instagram link and followers, Airbnb host details and a decision-maker hint.

**Proven run:** Jaipur, for Unpaqd. 200 Airbnb listings → 67 qualified operators not on Unpaqd → a 10-operator call sheet with phone numbers, in an afternoon, for under $2. See `METHOD.md`.

## Setup (2 minutes)
1. Get a free Apify token at apify.com, and a free Firecrawl key at firecrawl.dev (`FIRECRAWL_API_KEY`), then set them:
   - macOS/Linux: `export APIFY_TOKEN=apify_api_xxx`
   - Windows PowerShell: `$env:APIFY_TOKEN="apify_api_xxx"`
2. Have Python 3 and a web-search-capable AI coding tool (Claude Code or Codex).

## Run
```
# 1. scrape a city
python scripts/scrape_airbnb.py --city "Jaipur, Rajasthan" --price-min 7000 --max 200 --out data/raw.json

# 2. capture your platform's own listings (needs FIRECRAWL_API_KEY); run on 2-3 date ranges
python scripts/fetch_platform_listings.py --url "https://YOURPLATFORM.com/<city search page>" --out data/platform_city.txt

# 3. build the scored lead list, cross-checked so existing hosts don't leak in
python scripts/build_leads.py --raw data/raw.json --out-dir data/leads \
  --sitemap-url "https://YOURPLATFORM.com/sitemap.xml,data/platform_sitemap_snapshot.xml" \
  --platform-titles data/platform_city.txt --exclude-hosts "KnownHost1,KnownHost2" --platform-name YourPlatform

# 4. enrich every target: Instagram link + bio, phone, WhatsApp link, email, website, Airbnb host details
python scripts/enrich_contacts.py --targets data/leads/targets.csv --raw "City=data/raw.json" \
  --cache data/enrich_cache.json --out data/leads/targets_enriched.csv
```
Review any host marked `CHECK` before calling, and any Instagram match marked `low`. Don't rely on the sitemap alone: save a snapshot whenever it lists properties (see SKILL.md, Step 2). The enrichment is resumable and cached, so it is safe to stop and restart.
Open `data/leads/targets.csv` — that's your call sheet.

Full instructions and configuration are in `SKILL.md`.
