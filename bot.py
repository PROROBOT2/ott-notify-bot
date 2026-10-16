"""SonyLIV + ZEE5 serials -> Telegram notifier."""
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

# ---------------------------------------------------------------
# YAHAN SHOWS ADD KARO
# sonyliv: link https://www.sonyliv.com/shows/<slug>-<id>
# zee5   : link https://www.zee5.com/tv-shows/details/<slug>/<id>
# ---------------------------------------------------------------
SHOWS = [
    {"platform": "sonyliv", "name": "Taarak Mehta Ka Ooltah Chashmah",
     "id": 1700000084, "slug": "taarak-mehta-ka-ooltah-chashmah"},
    {"platform": "sonyliv", "name": "Hastinapur Ke Veer",
     "id": 1790007734, "slug": "hastinapur-ke-veer"},
    {"platform": "sonyliv", "name": "Kyunki Papa Kehte Hain",
     "id": 1790007859, "slug": "kyunki-papa-kehte-hain"},
    {"platform": "sonyliv", "name": "Hui Gumm Yaadein Ek Doctor Do Zindagiyaan",
     "id": 1790006770, "slug": "hui-gumm-yaadein-ek-doctor-do-zindagiyaan"},
    {"platform": "zee5", "name": "Ganga Mai Ki Betiyan",
     "id": "0-6-4z5793364", "slug": "ganga-mai-ki-betiyan"},
    {"platform": "zee5", "name": "Tu Hi Re Dil Mein",
     "id": "0-6-4z5906051", "slug": "tu-hi-re-dil-mein"},
]

SONY_API = (
    "https://apiv3.sonyliv.com/AGL/4.8/A/ENG/WEB/IN/RJ/TRAY/SEARCH/VOD"
    "?objectSubType=ORIGINALS,TV_SHOWS,SPORTS,MOVIES&filter_contentSubtype=EPISODE"
    "&title={title}&orderBy=originalAirDate&sortOrder=desc"
    "&isUser=true&ab_segment=AB_DetailPage_Disable&kids_safe=false&kids_age_group=noage"
    "&segment_id=AB_DetailPage_Disable&utm_medium=organic&isPageV2=true"
    "&layout=landscape_layout&id=1239_1654&utm_source=google&from=0&to=14"
)
STATE_FILE = "state.json"
IST = timezone(timedelta(hours=5, minutes=30))
NOTIFY_MODE = os.environ.get("NOTIFY_MODE", "air")  # sirf SonyLIV ke liye

TOKEN = os.environ.get("TG_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TG_CHAT_ID", "")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
SONY_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.sonyliv.com",
    "Referer": "https://www.sonyliv.com/",
}
ZEE_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-IN,en;q=0.9",
}
HASHTAG = {"sonyliv": "#SONYLIV", "zee5": "#ZEE5"}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# ======================= SonyLIV =======================
def sony_avail_time(m):
    if NOTIFY_MODE == "switch":
        iso = (m.get("emfAttributes", {}).get("entitlementSwitchTime") or {}).get("_in")
        if iso:
            return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return datetime.fromtimestamp(m["originalAirDate"] / 1000, tz=timezone.utc)


def fetch_sonyliv(show):
    url = SONY_API.format(title=quote(show["name"], safe=""))
    r = requests.get(url, headers=SONY_HEADERS, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"API status {r.status_code}: {r.text[:200]}")
    data = r.json()
    if data.get("resultCode") != "OK":
        raise RuntimeError(f"Unexpected response: {json.dumps(data)[:200]}")

    metas = []
    for c in data["resultObj"]["containers"]:
        m = c.get("metadata", {})
        if m.get("contentSubtype") != "EPISODE" or not m.get("episodeNumber"):
            continue
        m["_parents"] = [p.get("parentId") for p in c.get("parents", [])]
        metas.append(m)
    eps = [m for m in metas if show["id"] in m["_parents"]]
    if not eps:
        eps = [m for m in metas if norm(show["name"]) in norm(m.get("title"))]

    out = []
    for m in eps:
        title = m.get("episodeTitle", "")
        t = re.sub(r"['\u2019`]", "", title.lower())
        eslug = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
        emf = m.get("emfAttributes", {})
        when = sony_avail_time(m)
        out.append({
            "num": int(m["episodeNumber"]),
            "title": title,
            "season": m.get("season"),
            "date": when.astimezone(IST).strftime("%d %B %Y"),
            "live_at": when,
            "link": (f"https://www.sonyliv.com/shows/{show['slug']}-{show['id']}/"
                     f"{eslug}-{m['contentId']}?watch=true"),
            "poster": emf.get("landscape_thumb") or emf.get("thumbnail"),
            "cid": str(m["contentId"]),
        })
    out.sort(key=lambda e: e["num"], reverse=True)
    return out


# ======================= ZEE5 =======================
def _collect_items(node, acc):
    """JSON-LD ke andar ListItem (episode) dhundo."""
    if isinstance(node, dict):
        item = node.get("item") if isinstance(node.get("item"), dict) else node
        url = item.get("url") or node.get("url")
        name = item.get("name") or node.get("name")
        if node.get("@type") == "ListItem" and isinstance(url, str) and name:
            acc.append({"name": name, "url": url, "image": item.get("image") or node.get("image")})
        for v in node.values():
            _collect_items(v, acc)
    elif isinstance(node, list):
        for v in node:
            _collect_items(v, acc)


def _image_url(img):
    if isinstance(img, str):
        return img
    if isinstance(img, list) and img:
        return _image_url(img[0])
    if isinstance(img, dict):
        return img.get("url") or img.get("contentUrl")
    return None


def fetch_zee5(show):
    page_url = f"https://www.zee5.com/tv-shows/details/{show['slug']}/{show['id']}"
    r = requests.get(page_url, headers=ZEE_HEADERS, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"ZEE5 page status {r.status_code}")
    page = r.text

    items = []
    blocks = re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S)
    for blk in blocks:
        try:
            _collect_items(json.loads(blk), items)
        except json.JSONDecodeError:
            continue
    print(f"  ZEE5: page {len(page)} chars, JSON-LD blocks {len(blocks)}, items {len(items)}")

    text = html.unescape(re.sub(r"<[^>]+>", "", page))
    out, seen = [], set()
    for it in items:
        if "/0-1-" not in it["url"] or it["url"] in seen:
            continue
        seen.add(it["url"])
        name = html.unescape(it["name"])
        pat = re.escape(name) + r"\s*E(\d+)\s*[•·]\s*\d+m\s*[•·]\s*(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})"
        mt = re.search(pat, text)
        if not mt:
            print(f"  (episode number nahi mila: {name})")
            continue
        try:
            d = datetime.strptime(re.sub(r"\s+", " ", mt.group(2)), "%d %b %Y")
            date = d.strftime("%d %B %Y")
        except ValueError:
            date = mt.group(2)
        out.append({
            "num": int(mt.group(1)),
            "title": name,
            "season": None,
            "date": date,
            "live_at": None,  # page par hai => live hai
            "link": it["url"],
            "poster": _image_url(it["image"]),
            "cid": it["url"].rstrip("/").split("/")[-1],
        })
    out.sort(key=lambda e: e["num"], reverse=True)
    if not out and len(page) < 2000:
        print("  Page chhota/khaali aaya:", page[:300])
    return out


def fetch_episodes(show):
    if show.get("platform") == "zee5":
        return fetch_zee5(show)
    return fetch_sonyliv(show)


# ======================= Telegram =======================
def build_caption(show, ep):
    tag = HASHTAG.get(show.get("platform", "sonyliv"), "#OTT")
    esc = lambda s: html.escape(str(s), quote=False)
    lines = [f"#NewEpisode {tag} #Notification", "", f"<b>Show Name: {esc(show['name'])}</b>"]
    if ep.get("season") not in (None, ""):
        try:
            lines.append(f"<b>Season Number: {int(ep['season']):02d}</b>")
        except (TypeError, ValueError):
            lines.append(f"<b>Season Number: {esc(ep['season'])}</b>")
    lines += [
        f"<b>Episode Number: {ep['num']}</b>",
        f"<b>Episode Title: {esc(ep['title'])}</b>",
        f"<b>Release Date: {esc(ep['date'])}</b>",
        "<b>Language: Hindi</b>",
        "",
        "<b>Show Link:</b>",
        f"<code>{esc(ep['link'])}</code>",
    ]
    return "\n".join(lines)


def get_poster(ep):
    url = ep.get("poster")
    if not url:
        return None
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        if r.status_code == 200 and r.content:
            return r.content
        print("  Poster download failed:", r.status_code)
    except requests.RequestException as e:
        print("  Poster error:", e)
    return None


def send_telegram(show, ep):
    caption = build_caption(show, ep)
    base = f"https://api.telegram.org/bot{TOKEN}"
    poster = get_poster(ep)
    if poster:
        r = requests.post(
            f"{base}/sendPhoto",
            data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"},
            files={"photo": ("poster.jpg", poster)}, timeout=60)
    else:
        r = requests.post(
            f"{base}/sendMessage",
            data={"chat_id": CHAT_ID, "text": caption, "parse_mode": "HTML"}, timeout=60)
    print("  Telegram status:", r.status_code, r.text[:150])
    return r.status_code == 200


# ======================= State + main =======================
def load_state():
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    if "last_episode" in state:  # purana format -> TMKOC
        tm = str(SHOWS[0]["id"])
        state.setdefault("shows", {})[tm] = {
            "last_episode": state.pop("last_episode"),
            "last_content_id": state.pop("last_content_id", None),
        }
    state.setdefault("shows", {})
    return state


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def process_show(show, state, now):
    eps = fetch_episodes(show)
    if not eps:
        print("  Koi episode nahi mila (naam/ID/page check karo)")
        return False
    live = [e for e in eps if e["live_at"] is None or e["live_at"] <= now]
    print(f"  Latest: {eps[0]['num']} | Live abhi: {[e['num'] for e in live][:3]}")

    last = state["shows"].get(str(show["id"]), {}).get("last_episode")
    if last is None:
        pending = live[:1]  # pehli baar: sirf latest (test)
    else:
        pending = sorted((e for e in live if e["num"] > last), key=lambda e: e["num"])
    if not pending:
        print("  Naya kuch nahi")
        return True

    for ep in pending:
        if send_telegram(show, ep):
            state["shows"][str(show["id"])] = {"last_episode": ep["num"], "last_content_id": ep["cid"]}
            save_state(state)
        else:
            return False
    return True


def main():
    if not TOKEN or not CHAT_ID:
        print("TG_BOT_TOKEN / TG_CHAT_ID set nahi hain")
        sys.exit(1)
    state = load_state()
    now = datetime.now(timezone.utc)
    ok = 0
    for show in SHOWS:
        print(f"== {show['name']} ({show.get('platform', 'sonyliv')})")
        try:
            if process_show(show, state, now):
                ok += 1
        except Exception as e:  # ek show fail ho to baaki chalte rahein
            print("  ERROR:", e)
    save_state(state)
    if ok == 0:
        print("Koi bhi show kaam nahi kiya")
        sys.exit(1)


if __name__ == "__main__":
    main()
