"""Sun NXT -> Telegram alerts (bot.py se alag file).

Is file me kuch bigad jaye ya Sun NXT block kare, tab bhi SonyLIV / ZEE5 / Dangal / Hotstar chalte rahenge,
kyunki bot.py is file ko try/except me import karta hai.
Naya show jodna ho toh sirf neeche SUNNXT_SHOWS me ek line add karo.
"""
import html
import re
from datetime import datetime, timedelta, timezone

import requests

# bot.py se aane wale helpers (bot.py init() chalakar inhe set karta hai)
send_tg = None
save_state = None
IST = timezone(timedelta(hours=5, minutes=30))


def init(**helpers):
    globals().update(helpers)


# ---------------------------------------------------------------
# SUN NXT SHOWS
# gid  = show ki ID (sunnxt.com ke show/episode link me .../detail/<NUMBER>/... wala number, aage GID_ lagao)
# lang = Hindi / Tamil / Telugu / Kannada / Malayalam ...
# slug_prefix (optional) = link ka shuru ka hissa, jaise "suntv-tamil-serial"
# ---------------------------------------------------------------
SUNNXT_SHOWS = [
    {"name": "Roopmati", "gid": "GID_248627", "lang": "Hindi"},
    {"name": "Thodi Si Umeed Thoda Sa Aasman", "gid": "GID_255611", "lang": "Hindi"},
    {"name": "Divya Prem", "gid": "GID_225437", "lang": "Hindi"},
]
SUNNXT_MAX_AGE_DAYS = 3        # isse purane episode ka alert nahi jata

API = "https://pwaapi.sunnxt.com"
SITE = "https://www.sunnxt.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")
FIELDS = ("contents,user/currentdata,images,generalInfo,relatedCast,"
          "globalServiceName,globalServiceId,publishingHouse")

_feed_cache = {}


def _headers(lang):
    return {
        "accept": "*/*",
        "contentlanguage": (lang or "hindi").lower(),
        "origin": SITE,
        "referer": SITE + "/",
        "user-agent": UA,
        "x-myplex-maturity-level": "",
        "x-myplex-platform": "browser",
        "x-ucv": "7",
    }


def _between(now):
    """Site ki tarah is mahine ka range: YYYYDDMMTHHMMSS (din pehle, mahina baad me)."""
    first = now.replace(day=1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)

    def f(d, t):
        return f"{d.year}{d.day:02d}{d.month:02d}T{t}"
    return f"{f(first, '000000')}-{f(last, '235900')}"


def _get_json(url, lang):
    r = requests.get(url, headers=_headers(lang), timeout=40)
    if r.status_code != 200:
        raise RuntimeError(f"Sun NXT API status {r.status_code}: {r.text[:120]}")
    return r.json()


def fetch_show_items(show, now):
    """Show ke naye episodes (naye pehle). Pehle show-wali API, na chale toh home 'Fresh Episodes'."""
    url = (f"{API}/content/v3/vods/{show['gid']}/?&orderBy=releaseDate&orderMode=-1"
           f"&fields={FIELDS}&startIndex=1&count=5&betweenDates={_between(now)}")
    try:
        items = (_get_json(url, show.get("lang")) or {}).get("results") or []
        if items:
            return items
        print("  show API se episode nahi mile, home Fresh Episodes try")
    except Exception as e:
        print("  show API error:", e, "| home Fresh Episodes try")
    lang = (show.get("lang") or "hindi").lower()
    if lang not in _feed_cache:
        feed_url = (f"{API}/content/v2/carousel/portalHomeTvshowsLatestEpisodes"
                    f"?&level=devicemax&startIndex=1&count=30&contentlang={lang}")
        try:
            _feed_cache[lang] = (_get_json(feed_url, lang) or {}).get("results") or []
        except Exception as e:
            print("  Fresh Episodes error:", e)
            _feed_cache[lang] = []
    return [i for i in _feed_cache[lang] if i.get("globalServiceId") == show["gid"]]


def parse_item(item):
    gi = item.get("generalInfo") or {}
    disp = gi.get("displayTitle") or ""
    title = item.get("title") or gi.get("title") or ""
    m = re.search(r"\bE(\d+)\b", disp)
    ep_no = int(m.group(1)) if m else None
    date = None
    d = re.search(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4})", disp or title)
    if d:
        try:
            date = datetime.strptime(f"{d.group(1)} {d.group(2)} {d.group(3)}", "%b %d %Y").date()
        except ValueError:
            date = None
    rel = None
    try:
        rel = datetime.fromisoformat((item.get("releaseDate") or "").replace("Z", "+00:00"))
    except ValueError:
        rel = None
    if date is None and rel is not None:
        date = rel.astimezone(IST).date()
    return {
        "id": int(item["_id"]),
        "title": title,
        "ep_no": ep_no,
        "date": date,
        "release": rel,
        "gname": item.get("globalServiceName") or "",
        "images": (item.get("images") or {}).get("values") or [],
    }


def pick_poster(values):
    for typ, res in (("coverposter", "1280x720"), ("coverposter", "960x540"),
                     ("preview", "832x623"), ("thumbnail", "500x750")):
        for v in values:
            if v.get("type") == typ and v.get("resolution") == res and v.get("link"):
                return v["link"]
    for v in values:
        if v.get("link"):
            return v["link"]
    return None


def episode_link(show, ep):
    gnum = show["gid"].replace("GID_", "")
    lang = (show.get("lang") or "Hindi").lower()
    prefix = show.get("slug_prefix") or f"suntv-{lang}-serial"
    base = re.sub(r"[^a-z0-9]+", "-", (ep["gname"] or show["name"]).lower()).strip("-")
    slug = f"{prefix}-{base}"
    if ep["ep_no"]:
        slug += f"-episodeno{ep['ep_no']}"
    if ep["date"]:
        slug += "-" + ep["date"].strftime("%b%d-%Y")
    return f"{SITE}/{slug}/detail/{gnum}/{ep['id']}"


def caption(show, ep):
    lines = [
        "#NewEpisode #DMT #SUNNXT #Notification",
        "",
        f"<b>Show Name: {html.escape(show['name'], quote=False)}</b>",
    ]
    if ep["ep_no"]:
        lines.append(f"<b>Episode Number: {ep['ep_no']}</b>")
    lines += [
        f"<b>Episode Title: {html.escape(ep['title'], quote=False)}</b>",
        f"<b>Release Date: {ep['date'].strftime('%d %B %Y') if ep['date'] else ''}</b>",
        f"<b>Language: {html.escape(show.get('lang') or 'Hindi', quote=False)}</b>",
        "",
        "<b>Show Link:</b>",
        f"<code>{html.escape(episode_link(show, ep), quote=False)}</code>",
    ]
    return "\n".join(lines)


def poster_bytes(values):
    url = pick_poster(values)
    if not url:
        return None
    try:
        r = requests.get(url, headers={"Referer": SITE + "/", "User-Agent": UA}, timeout=30)
        if r.status_code == 200 and r.content:
            return r.content
        print("  poster status:", r.status_code)
    except requests.RequestException as e:
        print("  poster error:", e)
    return None


def process_show(show, state, now):
    items = fetch_show_items(show, now)
    if not items:
        print("  koi episode nahi mila")
        return False
    now_utc = now.astimezone(timezone.utc)
    eps = []
    for it in items:
        try:
            ep = parse_item(it)
        except Exception as e:
            print("  episode parse error:", e)
            continue
        if ep["release"] is not None and ep["release"] > now_utc:
            continue                                   # abhi release nahi hua
        eps.append(ep)
    eps.sort(key=lambda e: e["id"])
    if not eps:
        print("  released episode nahi mila")
        return True
    latest = eps[-1]
    print(f"  latest ep {latest['ep_no']} | {latest['date']} | id {latest['id']}")

    bucket = state.setdefault("sunnxt", {})
    last = bucket.get(show["gid"])
    pending = eps[-1:] if last is None else [e for e in eps if e["id"] > int(last)]
    if not pending:
        print("  Naya kuch nahi")
        return True
    for ep in pending:
        age = (now.date() - ep["date"]).days if ep["date"] else 0
        if age > SUNNXT_MAX_AGE_DAYS:
            bucket[show["gid"]] = ep["id"]
            save_state(state)
            print(f"  purana episode ({age} din), alert nahi")
            continue
        if send_tg(caption(show, ep), poster_bytes(ep["images"])):
            bucket[show["gid"]] = ep["id"]
            save_state(state)
            print(f"  Sent: {show['name']} | {ep['title']}")
        else:
            return False
    return True


def run(state, now=None):
    """Return: (kitne shows theek chale, kul shows)."""
    now = now or datetime.now(IST)
    state.setdefault("sunnxt", {})
    ok = 0
    for show in SUNNXT_SHOWS:
        print(f"== [SUNNXT] {show['name']}")
        try:
            if process_show(show, state, now):
                ok += 1
        except Exception as e:
            print("  ERROR:", e)
    return ok, len(SUNNXT_SHOWS)
