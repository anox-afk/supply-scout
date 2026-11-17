#!/usr/bin/env python3
"""Enrich a targets CSV (from build_leads.py) with every contact detail we can find.

For each host it:
  A. adds Airbnb host details from the raw scrape (about text, co-hosts, years hosting, response);
  B. finds the Instagram profile (web search, needs a Firecrawl key) unless instagram_link is already filled;
  C. OPENS the Instagram profile page (free, no login) and reads followers, posts, display name and the BIO,
     then pulls phones, emails, websites and WhatsApp links out of the bio;
  D. for hosts still without a phone: searches "<brand> <city> contact number" (directories such as Justdial,
     Google listings), finds the brand's own website, and reads its phones / emails / WhatsApp links;
  E. picks the best phone, tags it with source + confidence, and builds a wa.me WhatsApp link.

Results are cached in --cache so the script can be stopped and re-run without repeating work or spending credits.
Standard library only. India (+91) mobile numbers.

Usage:
  python enrich_contacts.py --targets data/leads/targets.csv --raw "Jaipur=data/raw_jaipur.json" \
      --cache data/enrich_cache.json --out data/leads/targets_enriched.csv
Needs FIRECRAWL_API_KEY (env or --firecrawl-key) for stages B and D. Without it, only A and C run
(and only for rows that already have instagram_link / instagram).
"""
import argparse, csv, html as htmlmod, json, os, pathlib, random, re, sys, threading, time
import urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

FB_UA = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
BR_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"

PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?0?([6-9]\d{4}[\s-]?\d{5})(?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
EMAIL_BAD = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", "sentry", "example.", "wixpress", "domain.com", "email.com")
DOMAIN_RE = re.compile(r"\b((?:https?://)?(?:www\.)?[a-z0-9][a-z0-9-]{2,}\.(?:com|in|co\.in|net|org|travel|holiday|stays|villas|io|app)(?:/[^\s\"'<>]*)?)", re.I)
SKIP_DOMAINS = ("instagram.", "facebook.", "fb.com", "airbnb.", "booking.com", "makemytrip.", "goibibo.", "agoda.", "tripadvisor.",
                "youtube.", "youtu.be", "linkedin.", "twitter.", "x.com", "pinterest.", "google.", "wikipedia.", "justdial.",
                "gmail.", "yahoo.", "outlook.", "hotmail.", "wa.me", "whatsapp.", "linktr.ee", "bit.ly", "unpaqd.", "tiktok.")
STOP = set("""bhk villa villas apartment apartments stay stays home homes house room rooms suite suites luxury luxurious
premium private pool with the and for near city centre central view views garden boutique retreat heritage cottage
cottages farm farmhouse penthouse entire spacious cozy modern elegant studio flat mountain valley hill hills lake
palace haveli resort hotel hospitality rentals rental living nest instagram official""".split())
BRAND_RE = re.compile(r"stay|home|living|villa|bnb|retreat|hospitality|resort|group|properties|rooms|nest|casa|boutique|leisure|collective", re.I)
PROFILE_RE = re.compile(r"^https?://(?:www\.)?instagram\.com/([A-Za-z0-9._]{2,30})/?$")
IG_BAD = {"p", "reel", "reels", "explore", "stories", "tv", "accounts", "about", "directory"}
META_RE = re.compile(r'<meta content="(.*?)" name="description"', re.S)
DESC_RE = re.compile(r'^([\d.,KMkm]+) Followers, ([\d.,KMkm]+) Following, ([\d.,KMkm]+) Posts - (.*?) \(@([\w.]+)\) on Instagram:? ?(?:"(.*)")?\s*$', re.S)
RANK = {"verified": 4, "high": 3, "medium": 2, "low": 1}

NEW_COLS = ["instagram_link", "instagram_match", "ig_handle", "ig_followers", "ig_posts", "ig_name", "ig_bio",
            "email", "website", "phone", "phone_source", "phone_confidence", "phones_all", "whatsapp_link",
            "decision_maker", "decision_maker_hint", "host_about", "cohosts", "years_hosting", "airbnb_response",
            "enriched_on"]

lock = threading.Lock()


# ---------- small helpers ----------
def norm_phone(digits):
    return "+91" + re.sub(r"\D", "", digits)[-10:]


def phones_in(text):
    return list(dict.fromkeys(norm_phone(m) for m in PHONE_RE.findall(text or "")))


def emails_in(text):
    out = []
    for e in EMAIL_RE.findall(text or ""):
        if not any(b in e.lower() for b in EMAIL_BAD) and e.lower() not in out:
            out.append(e.lower())
    return out


def toks(s):
    return {t for t in re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split() if len(t) >= 4 and t not in STOP}


def domain_of(u):
    return urllib.parse.urlparse(u if "//" in u else "//" + u).netloc.lower().replace("www.", "")


def websites_in(text):
    out = []
    for m in DOMAIN_RE.findall(text or ""):
        d = domain_of(m)
        if d and not any(s in d for s in SKIP_DOMAINS) and d not in out:
            out.append(d)
    return out


def key_of(row):
    return f"{(row.get('city') or '').strip().lower()}|{(row.get('host') or '').strip().lower()}"


def search_name(row):
    host = row.get("host") or ""
    hints = [h.strip() for h in (row.get("brand_guess") or "").split(";") if h.strip()]
    if BRAND_RE.search(host) or len(host.split()) >= 2:
        return host
    return hints[0][:40] if hints else host


def to_int(s):
    s = (s or "").replace(",", "").strip()
    m = re.match(r"^([\d.]+)\s*([KkMm]?)$", s)
    if not m:
        return ""
    n = float(m.group(1)) * {"": 1, "k": 1e3, "m": 1e6}[m.group(2).lower()]
    return int(n)


def http_get(url, ua, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Language": "en"})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, r.read(1500000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


class Firecrawl:
    def __init__(self, key, max_credits):
        self.key, self.max, self.used = key, max_credits, 0

    def _post(self, path, body, cost):
        if not self.key:
            return None
        with lock:
            if self.used + cost > self.max:
                return None
            self.used += cost
        req = urllib.request.Request("https://api.firecrawl.dev/v2/" + path, data=json.dumps(body).encode(),
                                     headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
        for attempt in range(4):
            try:
                return json.load(urllib.request.urlopen(req, timeout=120))
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(15 * (attempt + 1)); continue
                return None
            except Exception:
                time.sleep(4)
        return None

    def search(self, q, limit=6):
        d = self._post("search", {"query": q, "limit": limit}, 2)
        return ((d or {}).get("data") or {}).get("web") or []

    def scrape(self, url):
        d = self._post("scrape", {"url": url, "formats": ["markdown"], "waitFor": 3000}, 1)
        return (((d or {}).get("data") or {}).get("markdown")) or ""


# ---------- stage B: find the Instagram profile ----------
def discover_ig(row, fc):
    name = search_name(row)
    city = row.get("city") or ""
    want = toks(name) | toks(row.get("host"))
    best = None
    for r in fc.search(f"{name} {city} instagram"):
        m = PROFILE_RE.match((r.get("url") or "").split("?")[0])
        if not m or m.group(1).lower() in IG_BAD:
            continue
        handle = m.group(1)
        text = ((r.get("title") or "") + " " + (r.get("description") or "")).lower()
        hit = [t for t in want if t in text or t in handle.lower().replace(".", "").replace("_", "")]
        ctx = (city.lower() in text) or bool(re.search(r"villa|stay|homestay|rental|airbnb|bnb|hospitality|property|boutique|resort|apartment", text))
        score = len(hit) * 2 + (1 if ctx else 0)
        if best is None or score > best[0]:
            best = (score, handle, bool(hit), ctx)
    if not best:
        return {"link": "", "match": "not found"}
    return {"link": f"https://www.instagram.com/{best[1]}/", "match": "high" if (best[2] and best[3]) else "low"}


# ---------- stage C: open the Instagram page, read the bio ----------
def fetch_ig(handle):
    st, page = http_get(f"https://www.instagram.com/{handle}/", FB_UA, 30)
    if st == 429:
        return {"error": "rate-limited"}
    if st != 200:
        return {"error": f"HTTP {st}"}
    m = META_RE.search(page)
    if not m:
        return {"error": "no profile data (private, missing or blocked)"}
    d = DESC_RE.match(htmlmod.unescape(m.group(1)).strip())
    if not d:
        return {"error": "could not parse profile"}
    followers, following, posts, name, h, bio = d.groups()
    bio = (bio or "").strip()
    return {"followers": to_int(followers), "posts": to_int(posts), "name": name, "bio": bio,
            "phones": phones_in(bio), "emails": emails_in(bio), "websites": websites_in(bio),
            "wa": re.findall(r"wa\.me/(\d{10,13})", bio)}


# ---------- stage D: directories and the brand's own website ----------
def scrape_site(domain, fc):
    out = {"phones": [], "emails": [], "wa": []}
    base = "https://" + domain
    for path in ("", "/contact", "/contact-us"):
        st, page = http_get(base + path, BR_UA)
        if st != 200 or not page:
            continue
        tel = re.findall(r'href=["\']tel:([^"\']+)', page, re.I)
        out["emails"] += re.findall(r'href=["\']mailto:([^"\'?]+)', page, re.I)
        out["wa"] += re.findall(r"(?:wa\.me/|api\.whatsapp\.com/send\?phone=)(\d{10,13})", page)
        visible = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", page)
        visible = re.sub(r"<[^>]+>", " ", visible)
        out["phones"] += phones_in(" ".join(tel) + " " + visible)
        out["emails"] += emails_in(visible)
    if not out["phones"] and not out["emails"] and fc and fc.key:
        md = fc.scrape(base + "/contact") or fc.scrape(base)
        out["phones"] += phones_in(md)
        out["emails"] += emails_in(md)
    out["phones"] = list(dict.fromkeys(out["phones"]))
    out["emails"] = list(dict.fromkeys(e.lower() for e in out["emails"] if not any(b in e.lower() for b in EMAIL_BAD)))
    return out


def directory_search(row, fc):
    name = search_name(row)
    city = row.get("city") or ""
    want = toks(name) | toks(row.get("host"))
    phones, site_candidates = [], []
    for r in fc.search(f"{name} {city} contact number phone"):
        url = r.get("url") or ""
        d = domain_of(url)
        text = ((r.get("title") or "") + " " + (r.get("description") or "")).lower()
        about_brand = any(t in text or t in d for t in want)
        if about_brand and (city.lower() in text or re.search(r"villa|stay|homestay|rental|bnb|hospitality|resort", text)):
            for p in phones_in(text):
                src = "directory:" + (d.split(".")[0] if d else "web")
                phones.append((p, src, "medium"))
        if d and not any(s in d for s in SKIP_DOMAINS) and about_brand:
            site_candidates.append(d)
    return {"phones": phones, "sites": list(dict.fromkeys(site_candidates))[:2]}


# ---------- stage A: Airbnb host details ----------
def load_airbnb_extras(raw_specs):
    extras = {}
    for spec in raw_specs:
        city, _, path = spec.partition("=") if "=" in spec else ("", "", spec)
        for it in json.load(open(path, encoding="utf-8")):
            h = it.get("host") or {}
            nm = (h.get("name") or "").strip().lower()
            if not nm:
                continue
            e = extras.setdefault((city.strip().lower(), nm), {"about": "", "cohosts": [], "years": "", "resp": ""})
            extras.setdefault(("", nm), e)   # fallback for single-city sheets that have no city column
            e["about"] = e["about"] or (h.get("about") or "").replace("\n", " ").strip()
            tah = h.get("timeAsHost")
            if isinstance(tah, dict) and not e["years"]:
                e["years"] = f"{tah.get('years', 0)}y {tah.get('months', 0)}m"
            hd = h.get("hostDetails")
            if isinstance(hd, list) and not e["resp"]:
                e["resp"] = "; ".join(str(x) for x in hd[:2])
            for c in it.get("coHosts") or []:
                if c.get("name") and c["name"] not in e["cohosts"]:
                    e["cohosts"].append(c["name"])
    return extras


def dm_hint(about, cohosts, host):
    m = re.search(r"(?:founded by|founder[s]?[:,]? |owner[s]?[:,]? |run by|managed by|hi,? i'?m|i am|we are)\s+([A-Z][^.\n]{2,90})", about or "")
    parts = []
    if m:
        parts.append(m.group(0)[:110])
    if cohosts:
        parts.append("co-hosts: " + ", ".join(cohosts[:4]))
    return "; ".join(parts) if parts else (f"{host} (Airbnb host)" if host else "")


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--raw", default="", help='Airbnb raw json(s): path, or City=path, comma-separated')
    ap.add_argument("--cache", default="enrich_cache.json")
    ap.add_argument("--top", type=int, default=0, help="only the top N rows by score (0 = all)")
    ap.add_argument("--deep", action="store_true", help="run directory/website search for ALL rows, not only those without a phone")
    ap.add_argument("--no-directory", action="store_true")
    ap.add_argument("--max-credits", type=int, default=300, help="Firecrawl credit ceiling for this run")
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between Instagram requests")
    ap.add_argument("--firecrawl-key", default=os.environ.get("FIRECRAWL_API_KEY", ""))
    a = ap.parse_args()

    rows = list(csv.DictReader(open(a.targets, encoding="utf-8-sig")))
    if not rows:
        sys.exit("ERROR: targets file is empty.")
    fields = list(rows[0].keys())
    for c in NEW_COLS:
        if c not in fields:
            fields.append(c)
    for r in rows:
        for c in NEW_COLS:
            r.setdefault(c, "")
    work = sorted(rows, key=lambda r: -int(r.get("score") or 0))
    if a.top:
        work = work[:a.top]

    cache = {"discover": {}, "ig": {}, "dir": {}, "site": {}}
    cp = pathlib.Path(a.cache)
    if cp.exists():
        cache.update(json.load(open(cp, encoding="utf-8")))

    def save():
        cp.parent.mkdir(parents=True, exist_ok=True)
        json.dump(cache, open(cp, "w", encoding="utf-8"))

    fc = Firecrawl(a.firecrawl_key, a.max_credits)
    print(f"{len(rows)} rows, enriching {len(work)}. Firecrawl: {'on' if fc.key else 'OFF (stages B and D skipped)'}", flush=True)

    # A. Airbnb host details
    extras = load_airbnb_extras([s.strip() for s in a.raw.split(",") if s.strip()]) if a.raw else {}
    for r in work:
        e = extras.get(((r.get("city") or "").lower(), (r.get("host") or "").lower())) or extras.get(("", (r.get("host") or "").lower()))
        if e:
            r["host_about"] = r["host_about"] or e["about"][:300]
            r["cohosts"] = r["cohosts"] or ", ".join(e["cohosts"])
            r["years_hosting"] = r["years_hosting"] or e["years"]
            r["airbnb_response"] = r["airbnb_response"] or e["resp"]
        r["decision_maker_hint"] = dm_hint(r["host_about"], [c.strip() for c in r["cohosts"].split(",") if c.strip()], r.get("host"))

    # B. find Instagram profiles
    if fc.key:
        todo = [r for r in work if not r["instagram_link"] and not re.search(r"@[A-Za-z0-9._]{2,}", r.get("instagram") or "")
                and key_of(r) not in cache["discover"]]
        print(f"B. searching Instagram for {len(todo)} hosts", flush=True)
        with ThreadPoolExecutor(max_workers=3) as ex:
            for i, (r, res) in enumerate(zip(todo, ex.map(lambda r: discover_ig(r, fc), todo)), 1):
                cache["discover"][key_of(r)] = res
                if i % 15 == 0:
                    save(); print(f"   {i}/{len(todo)}", flush=True)
        save()
    for r in work:
        m = re.search(r"@([A-Za-z0-9._]{2,30})", r.get("instagram") or "")
        d = cache["discover"].get(key_of(r))
        if not r["instagram_link"]:
            if m:
                r["instagram_link"], r["instagram_match"] = f"https://www.instagram.com/{m.group(1)}/", "verified"
            elif d and d.get("link"):
                r["instagram_link"], r["instagram_match"] = d["link"], d["match"]
            elif d:
                r["instagram_match"] = d["match"]
        pm = PROFILE_RE.match(r["instagram_link"] or "")
        r["ig_handle"] = pm.group(1) if pm else ""

    # C. open each Instagram page and read the bio
    handles = list(dict.fromkeys(r["ig_handle"] for r in work if r["ig_handle"] and r["ig_handle"] not in cache["ig"]))
    print(f"C. opening {len(handles)} Instagram profiles (about {int(len(handles) * (a.delay + 1))}s)", flush=True)
    fails = 0
    for i, h in enumerate(handles, 1):
        res = fetch_ig(h)
        cache["ig"][h] = res
        fails = fails + 1 if res.get("error") in ("rate-limited", "HTTP 403") else 0
        if i % 25 == 0:
            save(); print(f"   {i}/{len(handles)}", flush=True)
        if fails >= 4:
            print("   Instagram is rate-limiting this IP. Stopping stage C; re-run later to resume from the cache.", flush=True)
            break
        time.sleep(a.delay + random.uniform(0, 0.8))
    save()

    # D. directories + the brand's own website (only for hosts still without a phone, unless --deep)
    def has_phone(r):
        ig = cache["ig"].get(r["ig_handle"]) or {}
        return bool(r.get("phone") or ig.get("phones"))
    if fc.key and not a.no_directory:
        todo = [r for r in work if (a.deep or not has_phone(r)) and key_of(r) not in cache["dir"]]
        print(f"D. directory + website search for {len(todo)} hosts (credit cap {fc.max}, used {fc.used})", flush=True)

        def d_work(r):
            res = directory_search(r, fc)
            sites = list(res["sites"])
            ig = cache["ig"].get(r["ig_handle"]) or {}
            sites = list(dict.fromkeys((ig.get("websites") or []) + sites))[:2]
            res["site_data"] = {}
            for s in sites:
                if s not in cache["site"]:
                    cache["site"][s] = scrape_site(s, fc)
                res["site_data"][s] = cache["site"][s]
            return res
        with ThreadPoolExecutor(max_workers=3) as ex:
            for i, (r, res) in enumerate(zip(todo, ex.map(d_work, todo)), 1):
                cache["dir"][key_of(r)] = res
                if i % 15 == 0:
                    save(); print(f"   {i}/{len(todo)} (credits used {fc.used})", flush=True)
        save()
        print(f"   Firecrawl credits used this run: {fc.used}", flush=True)

    # E. combine: best phone, email, website, WhatsApp link
    today = time.strftime("%Y-%m-%d")
    stats = {"ig_ok": 0, "ig_err": 0, "phone": 0, "email": 0, "website": 0}
    for r in work:
        ig = cache["ig"].get(r["ig_handle"]) or {}
        dr = cache["dir"].get(key_of(r)) or {}
        cands = []
        existing = phones_in(r.get("phone"))
        if existing:
            conf = "low" if "UNVERIFIED" in (r.get("note") or "").upper() else "verified"
            cands.append((existing[0], r.get("phone_source") or "manual", conf))
        if ig and not ig.get("error"):
            stats["ig_ok"] += 1
            r["ig_followers"], r["ig_posts"], r["ig_name"], r["ig_bio"] = ig["followers"], ig["posts"], ig["name"], ig["bio"]
            conf = "high" if r["instagram_match"] in ("high", "verified") else "low"
            cands += [(p, "instagram_bio", conf) for p in ig["phones"]]
            cands += [(norm_phone(w), "instagram_bio_whatsapp", conf) for w in ig.get("wa", [])]
        elif ig:
            stats["ig_err"] += 1
        emails = list(ig.get("emails") or []) if ig else []
        websites = list(ig.get("websites") or []) if ig else []
        for p, src, conf in dr.get("phones", []):
            cands.append((p, src, conf))
        for s, sd in (dr.get("site_data") or {}).items():
            websites.append(s)
            brand_site = any(t in s for t in toks(search_name(r)) | toks(r.get("host")))
            cands += [(p, "website:" + s, "high" if brand_site else "medium") for p in sd.get("phones", [])]
            cands += [(norm_phone(w), "website_whatsapp:" + s, "high" if brand_site else "medium") for w in sd.get("wa", [])]
            emails = list(sd.get("emails", [])) + emails
        seen, uniq = set(), []
        for p, src, conf in sorted(cands, key=lambda c: -RANK[c[2]]):
            if p not in seen:
                seen.add(p); uniq.append((p, src, conf))
        if uniq:
            best = uniq[0]
            if not r.get("phone"):
                r["phone"], r["phone_source"] = best[0], best[1]
            r["phone_confidence"] = best[2] if r["phone"] == best[0] else next((c for p, s, c in uniq if p == norm_phone(r["phone"])), best[2])
            r["phones_all"] = "; ".join(f"{p} ({s}, {c})" for p, s, c in uniq[:6])
            r["whatsapp_link"] = "https://wa.me/" + re.sub(r"\D", "", norm_phone(r["phone"]))
            stats["phone"] += 1
        if emails and not r["email"]:
            r["email"] = emails[0]; stats["email"] += 1
        if websites and not r["website"]:
            r["website"] = websites[0]; stats["website"] += 1
        if not r.get("decision_maker") and r["decision_maker_hint"]:
            r["decision_maker"] = r["decision_maker_hint"]
        r["enriched_on"] = today

    out = a.out or a.targets
    with open(out, "w", newline="", encoding="utf-8-sig") as f:   # BOM so Excel shows emoji/accents correctly
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    print(f"\nDONE -> {out}")
    print(f"Instagram pages read: {stats['ig_ok']} ok, {stats['ig_err']} failed")
    print(f"Hosts with a phone: {stats['phone']} / {len(work)} | email: {stats['email']} | website: {stats['website']}")
    if fc.key:
        print(f"Firecrawl credits used: {fc.used}")


if __name__ == "__main__":
    main()
