# METHOD — the exact steps this skill was built from

This is the real run (Jaipur, 2026-11-17) that `supply-scout` automates. Every number is from that run.

## Goal
Find premium property operators on Airbnb who are NOT on Unpaqd, get their phone numbers, and produce a cold-call sheet.

## Step 0 — Define the target (ICP)
Checked Unpaqd's own site first: host page (plans, commission) and the full sitemap (`unpaqd.com/sitemap.xml`, 726 properties). Found ~1 in 6 listings carry operator codes (Elivaas, Lohono, StayVista) and zero PG/co-living. Conclusion: the ideal host is a **multi-property premium operator**, not a single cheap apartment. Set the price floor at Rs 7,000/night (the platform's own "mid-range" tier).

## Step 1 — Validate the scraper cheaply
Ran the Apify actor `tri_angle/airbnb-scraper` on 15 Jaipur listings (~$0.06) to confirm it returns what we need: per-night price, rating + review count, Superhost flag, host name, host "about" text (often names the founders), coordinates, URL. It did.

## Step 2 — Recon across candidate markets
Scraped 70 listings each for Jaipur, Mussoorie and Kasauli (~$0.90 total). Result: **44–53% of premium listings were run by multi-property operators** in every market, and none of the named brands appeared in Unpaqd's sitemap. The approach was validated.

Command shape:
```
python scripts/scrape_airbnb.py --city "Jaipur, Rajasthan" --price-min 7000 --max 70 --out data/recon_jaipur.json
```

## Step 3 — Prove phones are findable (the risky unknown)
Picked 7 named operators and searched for each one's contact. **6 of 7 had a usable phone**, almost always in the **Instagram bio**, which showed up directly in search-result snippets (no need to open Instagram). Directories (Justdial, Google Business) were the backup. Projected hit-rate at scale: ~75–85%.

## Step 4 — Build the full list for the chosen market
Scraped 200 Jaipur listings (~$0.80) and processed them:
```
python scripts/build_leads.py --raw data/raw_jaipur.json --out-dir data/leads_jaipur \
  --price-min 7000 --sitemap-url https://unpaqd.com/sitemap.xml --platform-name Unpaqd
```
This deduped by host, flagged operators (2+ listings), computed per-night price, applied the ICP filter, scored each host, and cross-checked names against Unpaqd's sitemap.
Result: **200 listings → 123 hosts → 35 operators → 67 ICP-qualified targets not on Unpaqd.**

## Step 5 — Enrich the top targets
For the top ~15 targets, searched the **brand/property name + city** (not the host's first name — the brand is what's findable; brands hide inside the Airbnb listing titles, e.g. "Shree Nikunj", "Saiba", "Bardias"). Pulled phone, Instagram and decision-maker from search snippets. Got confident phones for 10 operators.
**Trap caught:** a same-name business ("Amara Jaipur" the wedding venue) had the wrong number for "Amara" the apartments — always confirm the entity matches before trusting a number.

## Step 5b — Leak check (are any targets already on the platform?)
Before delivering, every target was re-checked against Unpaqd's own listings. What that found:
- **The sitemap is not a reliable source.** It listed 726 properties in the morning and zero by the afternoon. Also, 10 of the 12 live Jaipur listings on Unpaqd's search page were never in it. A sitemap-only check would have let everyone through.
- **Host names don't work for matching.** Platform titles don't contain them. Matching **property titles** does, because platforms copy titles from Airbnb during onboarding.
- **Listing-team accounts exist.** One Unpaqd account ("Ritwik") holds properties in several cities. His Jaipur villa ("Cabana") was also on our Airbnb list. That one real leak was removed, and the title matcher now catches it on its own.
- **Platform coordinates can be placeholders.** Six Unpaqd listings sat on the exact city-centre point, so location proximity proved nothing.
- Result: 67 → **66 targets**. All 10 call-ready operators were confirmed not on Unpaqd by brand, title, host and (where usable) location. One borderline flag (Seven Mirror vs a resort room called "Gold Palm Ivory") was opened by hand and confirmed as a different property.

## Step 5c — Scripted enrichment at scale (`enrich_contacts.py`)
Steps 3 and 5 above were done by hand with web search for 17 operators. To cover a full list, the same method was turned into a script:
- **Instagram pages can be opened for free.** Fetching `instagram.com/<handle>/` with a link-preview user agent (`facebookexternalhit/1.1`) returns the page with a `<meta name="description">` tag that contains the follower count, post count and the **full bio text**, including the phone number. No login, no API key. It returned the same numbers we had found by hand for the operators tested.
- **The bio is the main source**, the directories and the brand's own website are the backup, and Airbnb itself supplies host details (about text, co-hosts, years hosting, response rate).
- **Confidence labels** (verified / high / medium / low) say how far to trust each number, because wrong-entity matches are the main risk. In testing, one Instagram profile that looked like a match (Palette by D1) turned out to be an art gallery; reading the bio exposed it.
- **Web search is the bottleneck**: Firecrawl's free plan allows only a few searches a minute, so finding Instagram handles for about 200 hosts takes 30 to 60 minutes. DuckDuckGo's HTML search was tried as a free alternative and blocks automated requests.

## Step 6 — Deliver the call sheet
Wrote the phones into `targets.csv` (columns: host, listings, price band, phone, source, instagram, decision_maker, note, contacted, outcome). The result was a 10-operator Jaipur call sheet ready to dial, inside one afternoon, for under $2 of scraping.

## Tools used
- Apify (`tri_angle/airbnb-scraper`) for the Airbnb scrape — the only paid part (~$0.004/listing).
- Firecrawl for the platform's JavaScript-rendered pages (live listings, property pages).
- A web search tool for enrichment — free.
- Python standard library for processing.
