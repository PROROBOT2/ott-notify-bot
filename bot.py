"""SonyLIV + ZEE5 serials -> Telegram notifier (multi-show)."""
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

# ---------------------------------------------------------------
# SONYLIV SHOWS: (naam, show_id, link wala slug)
# link: https://www.sonyliv.com/shows/<slug>-<show_id>
# ---------------------------------------------------------------
SHOWS = [
    {"name": "Taarak Mehta Ka Ooltah Chashmah", "id": 1700000084,
     "slug": "taarak-mehta-ka-ooltah-chashmah"},
    {"name": "Hastinapur Ke Veer", "id": 1790007734,
     "slug": "hastinapur-ke-veer"},
    {"name": "Kyunki Papa Kehte Hain", "id": 1790007859,
     "slug": "kyunki-papa-kehte-hain"},
    {"name": "Hui Gumm Yaadein Ek Doctor Do Zindagiyaan", "id": 1790006770,
     "slug": "hui-gumm-yaadein-ek-doctor-do-zindagiyaan"},
    {"name": "Family Full House with Rohit Sharma", "id": 1790007851,
     "slug": "family-full-house-with-rohit-sharma"},
    {"name": "Indian Game Show", "id": 1790007836,
     "slug": "indian-game-show"},
    {"name": "Kaun Banega Crorepati", "id": 1700000195,
     "slug": "kaun-banega-crorepati"},
    {"name": "Pushpa Impossible", "id": 1700000921,
     "slug": "pushpa-impossible"},
]

# ---------------------------------------------------------------
# ZEE5 SHOWS: show page ka link hota hai
# https://www.zee5.com/tv-shows/details/<slug>/<id>
# ---------------------------------------------------------------
ZEE5_SHOWS = [
    {"name": "Tumm Se Tumm Tak", "id": "0-6-4z5727104",
     "slug": "tumm-se-tumm-tak"},
]
ZEE5_PAGE = "https://www.zee5.com/tv-shows/details/{slug}/{id}"

API_BASE = (
    "https://apiv3.sonyliv.com/AGL/4.8/A/ENG/WEB/IN/RJ/TRAY/SEARCH/VOD"
    "?objectSubType=ORIGINALS,TV_SHOWS,SPORTS,MOVIES&filter_contentSubtype=EPISODE"
    "&title={title}&orderBy=originalAirDate&sortOrder=desc"
    "&isUser=true&ab_segment=AB_DetailPage_Disable&kids_safe=false&kids_age_group=noage"
    "&segment_id=AB_DetailPage_Disable&utm_medium=organic&isPageV2=true"
    "&layout=landscape_layout&id=1239_1654&utm_source=google&from=0&to=14"
)
STATE_FILE = "state.json"
IST = timezone(timedelta(hours=5, minutes=30))

# "air" = originalAirDate | "switch" = jab sabke liye free ho
NOTIFY_MODE = os.environ.get("NOTIFY_MODE", "air")

TOKEN = os.environ.get("TG_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TG_CHAT_ID", "")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.sonyliv.com",
    "Referer": "https://www.sonyliv.com/",
}
ZEE5_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
    "Referer": "https://www.zee5.com/",
}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# =================================================================
# SONYLIV
# =================================================================
def fetch_episodes(show):
    """Is show ke episodes (naye pehle). Error par Exception."""
    url = API_BASE.format(title=quote(show["name"], safe=""))
    r = requests.get(url, headers=HEADERS, timeout=30)
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
    if not eps:  # fallback: title match
        eps = [m for m in metas if norm(show["name"]) in norm(m.get("title"))]
        if eps:
            print(f"  (fallback title match use hua: {show['name']})")
    eps.sort(key=lambda m: int(m["episodeNumber"]), reverse=True)
    return eps


def avail_time(m):
    if NOTIFY_MODE == "switch":
        iso = (m.get("emfAttributes", {}).get("entitlementSwitchTime") or {}).get("_in")
        if iso:
            return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return datetime.fromtimestamp(m["originalAirDate"] / 1000, tz=timezone.utc)


def episode_link(show, m):
    title = re.sub(r"['\u2019`]", "", m.get("episodeTitle", "").lower())  # apostrophe hata do
    slug = re.sub(r"[^a-z0-9]+", "-", title).strip("-")
    return (
        f"https://www.sonyliv.com/shows/{show['slug']}-{show['id']}/"
        f"{slug}-{m['contentId']}?watch=true"
    )


def build_caption(show, m, link):
    when = avail_time(m).astimezone(IST).strftime("%d %B %Y")
    lines = [
        "#NewEpisode #DMT #SONYLIV #Notification",
        "",
        f"<b>Show Name: {html.escape(show['name'], quote=False)}</b>",
    ]
    season = m.get("season")
    if season not in (None, ""):
        try:
            lines.append(f"<b>Season Number: {int(season):02d}</b>")
        except (TypeError, ValueError):
            lines.append(f"<b>Season Number: {html.escape(str(season), quote=False)}</b>")
    lines += [
        f"<b>Episode Number: {m['episodeNumber']}</b>",
        f"<b>Episode Title: {html.escape(m.get('episodeTitle', ''), quote=False)}</b>",
        f"<b>Release Date: {when}</b>",
        "<b>Language: Hindi</b>",
        "",
        "<b>Show Link:</b>",
        f"<code>{html.escape(link, quote=False)}</code>",
    ]
    return "\n".join(lines)


def get_image(url):
    if not url:
        return None
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code == 200 and r.content:
            return r.content
        print("  Poster download failed:", r.status_code)
    except requests.RequestException as e:
        print("  Poster error:", e)
    return None


def get_poster(m):
    emf = m.get("emfAttributes", {})
    return get_image(emf.get("landscape_thumb") or emf.get("thumbnail"))


def send_tg(caption, poster):
    base = f"https://api.telegram.org/bot{TOKEN}"
    if poster:
        r = requests.post(
            f"{base}/sendPhoto",
            data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"},
            files={"photo": ("poster.jpg", poster)},
            timeout=60,
        )
    else:
        r = requests.post(
            f"{base}/sendMessage",
            data={"chat_id": CHAT_ID, "text": caption, "parse_mode": "HTML"},
            timeout=60,
        )
    print("  Telegram status:", r.status_code, r.text[:150])
    return r.status_code == 200


def send_telegram(show, m):
    link = episode_link(show, m)
    caption = build_caption(show, m, link)
    return send_tg(caption, get_poster(m))


# =================================================================
# ZEE5 (show page ke andar JSON-LD se episodes, token ki zaroorat nahi)
# =================================================================
def fetch_zee5_episodes(show):
    """Is show ke episodes (naye pehle). Error par Exception."""
    url = ZEE5_PAGE.format(**show)
    r = requests.get(url, headers=ZEE5_HEADERS, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"ZEE5 page status {r.status_code}")

    blocks = re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        r.text, re.S,
    )
    eps, seen_ids = [], set()
    for b in blocks:
        try:
            data = json.loads(b)
        except json.JSONDecodeError:
            continue
        for d in (data if isinstance(data, list) else [data]):
            if not isinstance(d, dict):
                continue
            for it in d.get("itemListElement") or []:
                if isinstance(it, dict) and isinstance(it.get("item"), dict):
                    it = it["item"]
                link = (it or {}).get("url") or ""
                m = re.search(r"/(0-1-[0-9a-z]+)/?$", link)
                if not m or show["id"] not in link or m.group(1) in seen_ids:
                    continue
                seen_ids.add(m.group(1))
                eps.append({
                    "id": m.group(1),
                    "title": it.get("name") or "",
                    "url": link,
                    "image": it.get("image") or "",
                    "desc": it.get("description") or "",
                    "number": it.get("episodeNumber"),
                })
    return eps  # page pe jo order hai (naya pehle)


def zee5_caption(show, e):
    when = datetime.now(IST).strftime("%d %B %Y")
    lines = [
        "#NewEpisode #DMT #ZEE5 #Notification",
        "",
        f"<b>Show Name: {html.escape(show['name'], quote=False)}</b>",
    ]
    if e.get("number"):
        lines.append(f"<b>Episode Number: {html.escape(str(e['number']), quote=False)}</b>")
    lines += [
        f"<b>Episode Title: {html.escape(e['title'], quote=False)}</b>",
        f"<b>Release Date: {when}</b>",
        "<b>Language: Hindi</b>",
    ]
    if e.get("desc"):
        lines += ["", html.escape(e["desc"], quote=False)]
    lines += [
        "",
        "<b>Show Link:</b>",
        f"<code>{html.escape(e['url'], quote=False)}</code>",
    ]
    return "\n".join(lines)


def zee5_poster(e):
    url = e.get("image") or ""
    if not url:
        return None
    # Telegram avif support nahi karta -> jpg maango, thoda bada size
    url = url.replace("f_avif", "f_jpg").replace("w_438,h_254", "w_1000,h_562")
    return get_image(url)


# =================================================================
# STATE
# =================================================================
def load_state():
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    # purana (single-show) format ko migrate karo -> TMKOC
    if "last_episode" in state:
        tm = str(SHOWS[0]["id"])
        state.setdefault("shows", {})[tm] = {
            "last_episode": state.pop("last_episode"),
            "last_content_id": state.pop("last_content_id", None),
        }
    state.setdefault("shows", {})
    state.setdefault("zee5", {})
    return state


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# =================================================================
# PROCESS
# =================================================================
def process_show(show, state, now):
    eps = fetch_episodes(show)
    if not eps:
        print("  Koi episode nahi mila (naam/ID check karo)")
        return False
    live = [m for m in eps if avail_time(m) <= now]
    print(f"  Latest on API: {eps[0]['episodeNumber']} | Live abhi: "
          f"{[m['episodeNumber'] for m in live][:3]}")

    rec = state["shows"].get(str(show["id"]), {})
    last = rec.get("last_episode")
    if last is None:
        pending = live[:1]  # pehli baar: sirf latest live (test)
    else:
        pending = sorted((m for m in live if int(m["episodeNumber"]) > last),
                         key=lambda m: int(m["episodeNumber"]))
    if not pending:
        print("  Naya kuch nahi")
        return True

    for m in pending:
        if send_telegram(show, m):
            state["shows"][str(show["id"])] = {
                "last_episode": int(m["episodeNumber"]),
                "last_content_id": m["contentId"],
            }
            save_state(state)
        else:
            return False
    return True


def process_zee5_show(show, state):
    eps = fetch_zee5_episodes(show)
    if not eps:
        print("  Koi episode nahi mila (page ya JSON-LD badla ho sakta hai)")
        return False
    print(f"  Page pe latest: {eps[0]['title']} | total list: {len(eps)}")

    rec = state["zee5"].get(show["id"])
    if rec is None:
        # pehli baar: sirf sabse naya bhejo, purane "dekhe hue" maan lo
        seen = [e["id"] for e in eps[1:]]
        pending = eps[:1]
    else:
        seen = list(rec.get("seen", []))
        pending = [e for e in reversed(eps) if e["id"] not in seen]  # purana pehle

    if not pending:
        print("  Naya kuch nahi")
        return True

    for e in pending:
        if send_tg(zee5_caption(show, e), zee5_poster(e)):
            seen.append(e["id"])
            state["zee5"][show["id"]] = {"seen": seen[-300:]}
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
        print(f"== {show['name']}")
        try:
            if process_show(show, state, now):
                ok += 1
        except Exception as e:  # ek show fail ho to baaki chalte rahein
            print("  ERROR:", e)
    for show in ZEE5_SHOWS:
        print(f"== [ZEE5] {show['name']}")
        try:
            if process_zee5_show(show, state):
                ok += 1
        except Exception as e:
            print("  ERROR:", e)
    save_state(state)
    if ok == 0:
        print("Koi bhi show kaam nahi kiya")
        sys.exit(1)


if __name__ == "__main__":
    main()
