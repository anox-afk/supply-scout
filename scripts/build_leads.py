#!/usr/bin/env python3
"""Turn raw Airbnb listings into a scored, deduped lead list, and cross-check every
listing against the target platform so hosts ALREADY on it don't leak into the call list.

Cross-check (v2) compares PROPERTY TITLES, not host names (platform titles rarely contain
host names). Platform titles come from:
  1. the platform sitemap (--sitemap-url), which is often INCOMPLETE, plus
  2. a live scrape of the platform's city search page (--platform-titles file, made by
     fetch_platform_listings.py), which catches listings missing from the sitemap.
Hosts known to be on the platform (e.g. listing-team accounts) go in --exclude-hosts.

Each host gets on_platform = YES (strong match or excluded) / CHECK (borderline, review it)
/ no. YES hosts are dropped from targets.csv; CHECK hosts stay, flagged.

Standard library only.
Usage:
  python build_leads.py --raw data/raw_jaipur.json --out-dir data/leads_jaipur \
    --sitemap-url https://unpaqd.com/sitemap.xml --platform-titles data/platform_jaipur.txt \
    --exclude-hosts "Ritwik" --platform-name Unpaqd
"""
import argparse, json, re, csv, sys, pathlib, urllib.request, urllib.parse, difflib

BRAND_RE = re.compile(
    r"stay|home|living|villa|bnb|retreat|hospitality|resort|group|"
    r"properties|rooms|nest|casa|boutique|leisure|collective|the ", re.I)

# Generic words that create false title matches. City/state names are added at runtime.
STOP = set("""
bhk 1bhk 2bhk 3bhk 4bhk 5bhk 6bhk br 1br 2br 3br 4br 5br bed beds bedroom bedrooms bath baths ba bd
villa villas apartment apartments apt flat flats studio home homes house stay stays room rooms suite suites
luxury luxurious lux luxe premium private pvt pool pools with and the an in near of by for to at on
city centre center central view views garden gardens balcony terrace cozy cosy modern elegant serene
spacious entire family boutique retreat heritage kitchen cook jacuzzi farm farmhouse farmstay duplex penthouse
new best beautiful lovely stunning designer grand royal peaceful calm space spaces park road area nagar
guest guests floor top amazing great perfect getaway stylish escape fast wifi centrally located quiet
gym lift open air hill hills bathtub shared style resort hotel cottage cottages forest mins minutes from
airport deluxe classic sophisticated comfort comfortable charming chic cute quaint rooftop sea beach lake
""".split())


def norm(s):
    s = urllib.parse.unquote(s or "").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def toks(s, stop):
    return {t for t in norm(s).split() if t not in stop and len(t) > 2 and not t.isdigit()}


def pernight(it):
    """Per-night price from the Airbnb price breakdown ('N nights x <amount>')."""
    pr = it.get("price") or {}
    desc = ((pr.get("breakDown") or {}).get("basePrice") or {}).get("description") or ""
    m = re.search(r"x\s*[^\d]*([\d,]+)", desc)
    return int(m.group(1).replace(",", "")) if m else None


def title_from_url(u):
    path = urllib.parse.urlparse(u).path.rstrip("/")
    return urllib.parse.unquote(path.split("/")[-1])


def load_platform_titles(sitemap_url, titles_files, link_pattern):
    titles, sitemap_text = [], ""
    for src in [s.strip() for s in (sitemap_url or "").split(",") if s.strip()]:
        try:
            if pathlib.Path(src).exists():          # saved snapshot on disk
                text = pathlib.Path(src).read_text(encoding="utf-8", errors="ignore")
            else:                                    # live URL
                req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
                text = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "ignore")
            sitemap_text += text
            found = [u for u in re.findall(r"<loc>([^<]+)</loc>", text) if link_pattern in u]
            if not found:
                print(f"WARNING: sitemap {src} has NO listing URLs matching '{link_pattern}'. "
                      f"Sitemaps change and are often incomplete; rely on --platform-titles "
                      f"and/or a saved sitemap snapshot.", file=sys.stderr)
            titles += [(title_from_url(u), u) for u in found]
        except Exception as e:
            print(f"WARNING: sitemap {src} failed ({e}).", file=sys.stderr)
    for titles_file in [s.strip() for s in (titles_files or "").split(",") if s.strip()]:
        for ln in open(titles_file, encoding="utf-8"):
            ln = ln.strip()
            if not ln:
                continue
            parts = ln.split("\t")
            t = parts[0]
            u = parts[1] if len(parts) > 1 else (t if t.startswith("http") else "")
            if t.startswith("http"):
                t = title_from_url(t)
            titles.append((t, u))
    # dedupe on url/title
    seen, out = set(), []
    for t, u in titles:
        k = (u or t).lower()
        if k not in seen:
            seen.add(k); out.append((t, u))
    return out, sitemap_text.lower()


def match_listing(title, platform, stop):
    """Return (level, matched_title, shared, ratio) where level is STRONG/CHECK/None."""
    nt, tt = norm(title), toks(title, stop)
    best = (None, "", [], 0.0)
    for pt, pu in platform:
        tp = toks(pt, stop)
        shared = tt & tp
        ratio = difflib.SequenceMatcher(None, nt, norm(pt)).ratio()
        if (len(shared) >= 3 and ratio >= 0.55) or ratio >= 0.85:
            level = "STRONG"
        elif len(shared) >= 2 or ratio >= 0.80:
            level = "CHECK"
        else:
            continue
        rank = (2 if level == "STRONG" else 1, len(shared), ratio)
        if best[0] is None or rank > ((2 if best[0] == "STRONG" else 1), len(best[2]), best[3]):
            best = (level, pt, sorted(shared), round(ratio, 2))
    return best


def brand_hints(titles):
    out, seen = [], set()
    for t in titles:
        if not t:
            continue
        seg = re.split(r"[-|,:~·]", t)[0].strip()
        if len(seg) < 3:
            seg = " ".join(t.split()[:4])
        if seg.lower() not in seen:
            seen.add(seg.lower()); out.append(seg)
    return out[:3]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--price-min", type=int, default=7000)
    ap.add_argument("--premium", type=int, default=15000)
    ap.add_argument("--min-reviews", type=int, default=20)
    ap.add_argument("--min-rating", type=float, default=4.7)
    ap.add_argument("--sitemap-url", default="https://unpaqd.com/sitemap.xml",
                    help="sitemap URL and/or saved snapshot file(s), comma-separated")
    ap.add_argument("--platform-titles", default="",
                    help="file(s) from fetch_platform_listings.py (title<TAB>url per line), comma-separated")
    ap.add_argument("--link-pattern", default="/property/", help="URL fragment that marks a listing page")
    ap.add_argument("--exclude-hosts", default="", help="comma-separated host names known to be on the platform")
    ap.add_argument("--city-words", default="", help="extra comma-separated place words to ignore when matching")
    ap.add_argument("--platform-name", default="Unpaqd")
    a = ap.parse_args()

    d = json.load(open(a.raw, encoding="utf-8"))
    if not isinstance(d, list):
        sys.exit(f"ERROR: {a.raw} is not a list of listings.")

    platform, sitemap_text = load_platform_titles(a.sitemap_url, a.platform_titles, a.link_pattern)
    if not platform:
        print("WARNING: no platform titles loaded, cross-check is OFF. Every host will show as not listed.", file=sys.stderr)
    elif len(platform) < 50:
        print(f"WARNING: only {len(platform)} platform listings loaded, so the cross-check is thin. "
              f"Add a sitemap snapshot or more --platform-titles files (other dates/pages).", file=sys.stderr)
    exclude = {h.strip().lower() for h in a.exclude_hosts.split(",") if h.strip()}

    # place words from the scraped listings' locations, so city names don't create matches
    stop = set(STOP)
    for it in d:
        for w in norm(" ".join(str(v) for v in [it.get("location"), it.get("locationSubtitle")] if v)).split():
            stop.add(w)
    for w in a.city_words.split(","):
        stop |= set(norm(w).split())

    hosts = {}
    for it in d:
        h = it.get("host") or {}
        key = h.get("name") or h.get("id") or "?"
        rec = hosts.setdefault(key, {"name": h.get("name"), "super": h.get("isSuperHost"), "L": []})
        rt = it.get("rating") or {}
        rec["L"].append({
            "pn": pernight(it),
            "rating": rt.get("guestSatisfaction") if isinstance(rt, dict) else None,
            "reviews": rt.get("reviewsCount") if isinstance(rt, dict) else None,
            "title": it.get("title"), "url": it.get("url"),
        })

    rows = []
    for key, r in hosts.items():
        L = r["L"]; n = len(L)
        prices = [x["pn"] for x in L if x["pn"]]
        minp = min(prices) if prices else 0
        maxp = max(prices) if prices else 0
        maxrev = max((x["reviews"] or 0) for x in L) if L else 0
        bestrt = max((x["rating"] or 0) for x in L) if L else 0
        quality = (maxrev >= a.min_reviews and bestrt >= a.min_rating)
        passes = (maxp >= a.price_min) and (n >= 2 or quality)

        # cross-check against the platform
        nm = (r["name"] or "").lower()
        on_platform, why = "no", ""
        if nm in exclude:
            on_platform, why = "YES", "excluded host (known on platform)"
        else:
            strongest = None
            for x in L:
                lvl, pt, shared, ratio = match_listing(x["title"] or "", platform, stop)
                if lvl and (strongest is None or (lvl == "STRONG" and strongest[0] != "STRONG")):
                    strongest = (lvl, pt, shared, ratio, x["title"])
            if strongest:
                on_platform = "YES" if strongest[0] == "STRONG" else "CHECK"
                why = f"'{(strongest[4] or '')[:45]}' ~ '{strongest[1][:45]}' shared={strongest[2]} ratio={strongest[3]}"
            elif nm and len(nm) > 4 and sitemap_text and re.search(
                    r"(?<![a-z])" + re.escape(nm) + r"(?![a-z])", sitemap_text):
                on_platform, why = "CHECK", "host name appears as a whole word in sitemap"

        sc = 0
        sc += 3 if n >= 3 else (2 if n == 2 else 0)
        sc += 2 if maxp >= a.premium else (1 if maxp >= a.price_min else 0)
        sc += 1 if quality else 0
        sc += 1 if r["super"] else 0
        sc += 1 if BRAND_RE.search(r["name"] or "") else 0
        rows.append({
            "host": r["name"], "listings": n,
            "brand_guess": "; ".join(brand_hints([x["title"] for x in L])),
            "min_pn": minp, "max_pn": maxp, "best_rating": bestrt, "max_reviews": maxrev,
            "superhost": "Y" if r["super"] else "", "on_platform": on_platform,
            "platform_match": why, "score": sc, "passes": passes, "sample_url": L[0]["url"],
        })

    rows.sort(key=lambda x: (-x["score"], -x["listings"], -x["max_pn"]))
    targets = [r for r in rows if r["passes"] and r["on_platform"] != "YES"]

    out = pathlib.Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
    cols = ["host", "listings", "brand_guess", "min_pn", "max_pn", "best_rating", "max_reviews",
            "superhost", "on_platform", "platform_match", "score", "sample_url"]
    _write(out / "all_hosts.csv", cols, rows)
    _write(out / "targets.csv",
           cols + ["phone", "phone_source", "instagram", "decision_maker", "note", "contacted", "outcome"],
           [{**{k: r.get(k, "") for k in cols},
             "phone": "", "phone_source": "", "instagram": "", "decision_maker": "",
             "note": "", "contacted": "", "outcome": ""} for r in targets])

    n_ops = sum(1 for r in rows if r["listings"] >= 2)
    n_yes = sum(1 for r in rows if r["passes"] and r["on_platform"] == "YES")
    n_chk = sum(1 for r in targets if r["on_platform"] == "CHECK")
    print(f"{len(d)} listings | {len(hosts)} hosts | {n_ops} operators | platform titles loaded: {len(platform)}")
    print(f"Removed as already on {a.platform_name}: {n_yes} | Targets: {len(targets)} (of which {n_chk} flagged CHECK)")
    for r in rows:
        if r["passes"] and r["on_platform"] == "YES":
            print(f"  REMOVED  {r['host']}: {r['platform_match']}")
    for r in targets:
        if r["on_platform"] == "CHECK":
            print(f"  CHECK    {r['host']}: {r['platform_match']}")
    print(f"Wrote {out/'all_hosts.csv'} and {out/'targets.csv'}")
    print("\nTop targets to enrich (host | listings | price/night | brand hints for search):")
    for r in targets[:15]:
        print(f"  {r['host']} | {r['listings']} | {r['min_pn']}-{r['max_pn']} | {r['brand_guess']}")


def _write(path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})


if __name__ == "__main__":
    main()
