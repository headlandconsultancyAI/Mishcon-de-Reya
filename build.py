"""Build the Mishcon LinkedIn engagement dashboard.

What it does, in order:
  1. Pulls the latest LinkedIn posts from Apify (if APIFY_TOKEN is set),
     or from a local JSON export passed with --import.
  2. Merges them into data/posts.json, keyed on post ID. New posts are added,
     posts seen again get their latest engagement numbers, and nothing is
     ever deleted. That keeps history even if a weekly scrape only goes back
     a few weeks.
  3. Works out the rankings over a rolling 12 months and writes site/index.html.

Run it locally with:  python build.py                 (rebuild from saved data)
                      python build.py --import x.json (add an Apify export first)
Only uses the Python standard library.
"""

import argparse
import csv
import json
import os
import re
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
POSTS_FILE = DATA / "posts.json"
META_FILE = DATA / "meta.json"
PARTNERS_FILE = DATA / "partners.csv"
FOCUS_FILE = DATA / "focus.json"
TEMPLATE = ROOT / "template.html"
OUT_DIR = ROOT / "site"

WINDOW_DAYS = 365          # rolling period the rankings cover
MIN_POSTS = 5              # original posts needed to be ranked
THIN_DEPT_POSTS = 20       # departments with fewer original posts are greyed out
TEXT_KEEP = 300            # characters of post text to store


# ---------- helpers ----------

def slug(url):
    """Turn any LinkedIn profile URL into its lower-case handle."""
    if not url:
        return None
    m = re.search(r"/in/([^/?#\s]+)", url)
    return urllib.parse.unquote(m.group(1)).lower().strip() if m else None


def log(msg):
    print(msg, flush=True)


def warn(msg):
    # Shows up as a yellow annotation on the GitHub Actions run
    print(f"::warning::{msg}", flush=True)


# ---------- 1. fetch ----------

def fetch_from_apify(token, task_id, dataset_id):
    base = "https://api.apify.com/v2"
    if dataset_id:
        url = f"{base}/datasets/{dataset_id}/items?clean=true&format=json"
        log(f"Fetching dataset {dataset_id} sent by the Apify webhook")
    elif task_id:
        task = urllib.parse.quote(task_id, safe="~")
        url = f"{base}/actor-tasks/{task}/runs/last/dataset/items?status=SUCCEEDED&clean=true&format=json"
        log(f"Fetching the last successful run of task {task_id}")
    else:
        sys.exit("APIFY_TASK_ID isn't set, and no dataset ID came with the webhook.")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            items = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            sys.exit("Apify couldn't find a successful run for this task. Run the task once in Apify "
                     "(click Start), wait for it to finish, then run this workflow again. "
                     "If it has run, check APIFY_TASK_ID.")
        if e.code in (401, 403):
            sys.exit("Apify rejected the token. Check the APIFY_TOKEN secret.")
        raise
    if not isinstance(items, list):
        sys.exit(f"Apify returned something unexpected: {str(items)[:200]}")
    return items


def normalise(item):
    """Keep just the fields the dashboard needs from one Apify item."""
    meta = item.get("_metadata") or {}
    pid = meta.get("post_id") or item.get("post_url")
    profile = slug(meta.get("source_url"))
    if not pid or not profile or not item.get("posted_at"):
        return None
    eng = item.get("engagement") or {}
    author = item.get("author") or {}
    return {
        "id": str(pid),
        "profile": profile,
        "author": item.get("author_name") or author.get("name") or "",
        "headline": (author.get("headline") or "")[:200],
        "text": (item.get("content") or "").strip()[:TEXT_KEEP],
        "url": item.get("post_url") or "",
        "date": item["posted_at"],
        "repost": bool(item.get("is_repost")),
        "reactions": int(eng.get("total_reactions") or 0),
        "comments": int(eng.get("comments") or 0),
        "shares": int(eng.get("shares") or 0),
        "seen": meta.get("extracted_at") or "",
    }


# ---------- 2. merge ----------

def merge(items):
    posts = {}
    if POSTS_FILE.exists():
        posts = {p["id"]: p for p in json.loads(POSTS_FILE.read_text(encoding="utf-8"))}
    before = len(posts)
    added = updated = skipped = 0
    for raw in items:
        p = normalise(raw)
        if not p:
            skipped += 1
            continue
        if p["id"] in posts:
            updated += 1
        else:
            added += 1
        posts[p["id"]] = p
    log(f"Merged {len(items)} scraped items: {added} new, {updated} refreshed, {skipped} skipped. "
        f"History now {len(posts)} posts (was {before}).")
    ordered = sorted(posts.values(), key=lambda p: (p["date"], p["id"]))
    POSTS_FILE.write_text(json.dumps(ordered, ensure_ascii=False, indent=0), encoding="utf-8")
    return len(items)


# ---------- 3. build ----------

def load_partners():
    by_slug, partners = {}, []
    with open(PARTNERS_FILE, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {k: (v or "").strip() for k, v in row.items()}
            if not row.get("Name"):
                continue
            partners.append(row)
            for u in [row.get("LinkedIn URL")] + row.get("Alt LinkedIn URL", "").split():
                s = slug(u)
                if s:
                    by_slug[s] = row
    return partners, by_slug


def eng(p):
    return p["reactions"] + p["comments"] + p["shares"]


def fmt(n):
    if n is None:
        return ""
    n = round(n, 1)
    return f"{n:,.0f}" if n == int(n) else f"{n:,.1f}"


def build():
    partners, by_slug = load_partners()
    focus = json.loads(FOCUS_FILE.read_text(encoding="utf-8")) if FOCUS_FILE.exists() else {}
    posts = json.loads(POSTS_FILE.read_text(encoding="utf-8")) if POSTS_FILE.exists() else []
    meta = json.loads(META_FILE.read_text(encoding="utf-8")) if META_FILE.exists() else {}
    if not posts:
        sys.exit("No posts in data/posts.json yet, so there's nothing to build.")

    end = datetime.fromisoformat(meta.get("last_scrape", max(p["seen"] or p["date"] for p in posts)).replace("Z", "+00:00"))
    start = end - timedelta(days=WINDOW_DAYS)
    in_window = [p for p in posts if start <= datetime.fromisoformat(p["date"].replace("Z", "+00:00")) <= end]

    groups, unmatched = {}, {}
    for p in in_window:
        row = by_slug.get(p["profile"])
        if not row:
            unmatched[p["profile"]] = unmatched.get(p["profile"], 0) + 1
            continue
        groups.setdefault(row["Name"], (row, []))[1].append(p)
    for s, n in sorted(unmatched.items()):
        warn(f"Scraped profile '{s}' ({n} posts) isn't in data/partners.csv, so it's left out.")

    people = []
    for name, (row, ps) in groups.items():
        orig = [p for p in ps if not p["repost"]]
        es = [eng(p) for p in orig]
        jt = row["Job Title"].replace(" – ", ", ").replace(" - ", ", ")
        pg = row.get("Practice Group", "")
        f = focus.get(name) if len(orig) >= MIN_POSTS else None
        if len(orig) >= MIN_POSTS and not f:
            warn(f"{name} is now ranked but has no focus area. Add one to data/focus.json.")
        top = sorted(orig, key=eng, reverse=True)[:3]
        people.append(dict(
            name=name, dept=row["Department"], role=f"{jt}, {pg}" if pg else jt,
            url=row.get("LinkedIn URL") or f"https://www.linkedin.com/in/{ps[0]['profile']}",
            total=len(ps), orig=len(orig), reposts=len(ps) - len(orig),
            median=statistics.median(es) if es else None,
            mean=round(sum(es) / len(es), 1) if es else None,
            totalEng=sum(es),
            reactions=sum(p["reactions"] for p in orig), comments=sum(p["comments"] for p in orig),
            focus=f[0] if f else "", focusNote=f[1] if f else "",
            top=[dict(text=t["text"].replace("\n", " ")[:170], url=t["url"], eng=eng(t), date=t["date"][:10]) for t in top],
            _es=es,
        ))

    depts = []
    for dn in sorted({r["Department"] for r in partners}):
        g = [p for p in people if p["dept"] == dn]
        es = [e for p in g for e in p["_es"]]
        depts.append(dict(name=dn, people=len(g), staff=sum(r["Department"] == dn for r in partners),
                          posting=sum(p["orig"] > 0 for p in g), orig=len(es),
                          median=statistics.median(es) if es else None))
    depts.sort(key=lambda d: (d["orig"] < THIN_DEPT_POSTS, -(d["median"] or 0)))

    notes = {"Not found": "profile not found", "Check": "profile match unconfirmed"}
    names_with_posts = {p["name"] for p in people}
    silent = sorted(
        [dict(name=r["Name"], dept=r["Department"], note=notes.get(r.get("Match status", ""), ""))
         for r in partners if r["Name"] not in names_with_posts],
        key=lambda x: (x["dept"], x["name"].split()[-1]))

    # ----- headline copy, written from the numbers -----
    ranked = sorted([p for p in people if p["orig"] >= MIN_POSTS], key=lambda p: -p["median"])
    if not ranked:
        sys.exit("Nobody has enough original posts to rank. Check the scrape.")
    lead, second = ranked[0], (ranked[1] if len(ranked) > 1 else None)
    busiest = max(people, key=lambda p: p["orig"])
    poss = lambda n: n + ("'" if n.endswith("s") else "'s")
    if busiest["name"] == lead["name"]:
        headline = f"{poss(lead['name'])} posts land hardest, and nobody posts more."
    else:
        headline = f"{poss(lead['name'])} posts land hardest. {busiest['name']} posts most."
    lead_focus = f" on {lead['focus'][0].lower() + lead['focus'][1:]}" if lead["focus"] else ""
    fact1 = (f"<b>{fmt(lead['median'])} median per post</b>{lead['name']}, with {lead['orig']} original posts{lead_focus}."
             + (f" {second['name']} is next on {fmt(second['median'])}." if second else ""))
    if busiest["name"] != lead["name"]:
        fact2 = (f"<b>{busiest['orig']} posts, {fmt(busiest['median'])} median</b>{busiest['name']} posts more than anyone, "
                 f"but a typical post draws less than the top of the table.")
    else:
        all_posts = sum(p["total"] for p in people)
        fact2 = (f"<b>{sum(p['reposts'] for p in people):,} of {all_posts:,} are reposts</b>"
                 f"Reposts are left out of the ranking, as their engagement belongs to someone else.")
    solid = [d for d in depts if d["orig"] >= THIN_DEPT_POSTS]
    fact3 = ""
    if len(solid) >= 2:
        fact3 = (f"<b>{solid[0]['name']} leads on engagement</b>{solid[0]['name']} gets a median of "
                 f"{fmt(solid[0]['median'])} per post. {solid[-1]['name']} gets {fmt(solid[-1]['median'])}.")
    facts = "".join(f"<p>{x}</p>" for x in (fact1, fact2, fact3) if x)

    for p in people:
        p.pop("_es")

    period = f"{start:%B %Y} to {end:%B %Y}"
    html = TEMPLATE.read_text(encoding="utf-8")
    subs = {
        "__HEADLINE__": headline,
        "__FACTS__": facts,
        "__PERIOD__": period,
        "__START_LONG__": f"{start.day} {start:%B %Y}",
        "__END_LONG__": f"{end.day} {end:%B %Y}",
        "__END_MONTH__": f"{end:%B %Y}",
        "__NPEOPLE__": str(len(people)),
        "__NSTAFF__": str(len(partners)),
        "__NSILENT__": str(len(silent)),
        "__DATA__": json.dumps(people, ensure_ascii=False),
        "__DEPTS__": json.dumps(depts),
        "__SILENT__": json.dumps(silent, ensure_ascii=False),
    }
    for k, v in subs.items():
        html = html.replace(k, v)
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "index.html").write_text(html, encoding="utf-8")
    (OUT_DIR / ".nojekyll").write_text("", encoding="utf-8")
    log(f"Built site/index.html: {len(people)} people, {len(ranked)} ranked, {period}. "
        f"Top: {lead['name']} ({fmt(lead['median'])}).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--import", dest="import_file", help="Apify JSON export to merge in before building")
    args = ap.parse_args()

    items = None
    if args.import_file:
        items = json.loads(Path(args.import_file).read_text(encoding="utf-8"))
        log(f"Importing {len(items)} items from {args.import_file}")
    elif os.environ.get("APIFY_TOKEN"):
        items = fetch_from_apify(os.environ["APIFY_TOKEN"], os.environ.get("APIFY_TASK_ID", "").strip(),
                                 os.environ.get("DATASET_ID", "").strip())
    else:
        log("No APIFY_TOKEN set, so rebuilding from saved data only.")

    if items is not None:
        if not items:
            sys.exit("The scrape came back empty, so nothing was changed. Check the Apify run.")
        merge(items)
        seen = [i.get("_metadata", {}).get("extracted_at") for i in items]
        seen = max([s for s in seen if s] or [datetime.now(timezone.utc).isoformat()])
        META_FILE.write_text(json.dumps({"last_scrape": seen}, indent=1), encoding="utf-8")

    build()


if __name__ == "__main__":
    main()
