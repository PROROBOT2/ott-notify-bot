"""TMKOC -> Telegram notifier (SonyLIV API, no TMDB)."""
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

API = (
    "https://apiv3.sonyliv.com/AGL/4.8/A/ENG/WEB/IN/RJ/TRAY/SEARCH/VOD"
    "?objectSubType=ORIGINALS,TV_SHOWS,SPORTS,MOVIES&filter_contentSubtype=EPISODE"
    "&title=Taarak%20Mehta%20Ka%20Ooltah%20Chashmah&orderBy=originalAirDate&sortOrder=desc"
    "&isUser=true&ab_segment=AB_DetailPage_Disable&kids_safe=false&kids_age_group=noage"
    "&segment_id=AB_DetailPage_Disable&utm_medium=organic&isPageV2=true"
    "&layout=landscape_layout&id=1239_1654&utm_source=google&from=0&to=14"
)
SHOW_ID = 1700000084
SHOW_NAME = "Taarak Mehta Ka Ooltah Chashmah"
SHOW_SLUG = "taarak-mehta-ka-ooltah-chashmah"
STATE_FILE = "state.json"
IST = timezone(timedelta(hours=5, minutes=30))

# "switch" = jab episode sabke liye free hota hai (entitlementSwitchTime, 8:30 PM IST)
# "air"    = originalAirDate (ek din pehle, premium)
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


def fetch_episodes():
    r = requests.get(API, headers=HEADERS, timeout=30)
    print("SonyLIV API status:", r.status_code)
    if r.status_code != 200:
        print("Body (first 300):", r.text[:300])
        sys.exit(1)
    data = r.json()
    if data.get("resultCode") != "OK":
        print("Unexpected response:", json.dumps(data)[:300])
        sys.exit(1)
    eps = []
    for c in data["resultObj"]["containers"]:
        m = c.get("metadata", {})
        if m.get("contentSubtype") != "EPISODE" or not m.get("episodeNumber"):
            continue
        if not any(p.get("parentId") == SHOW_ID for p in c.get("parents", [])):
            continue
        eps.append(m)
    eps.sort(key=lambda m: int(m["episodeNumber"]), reverse=True)
    return eps


def avail_time(m):
    """Episode kab 'live' maana jaye (UTC datetime)."""
    if NOTIFY_MODE == "switch":
        iso = (m.get("emfAttributes", {}).get("entitlementSwitchTime") or {}).get("_in")
        if iso:
            return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return datetime.fromtimestamp(m["originalAirDate"] / 1000, tz=timezone.utc)


def episode_link(m):
    slug = re.sub(r"[^a-z0-9]+", "-", m.get("episodeTitle", "").lower()).strip("-")
    return (
        f"https://www.sonyliv.com/shows/{SHOW_SLUG}-{SHOW_ID}/"
        f"{slug}-{m['contentId']}?watch=true"
    )


def build_caption(m, link):
    when = avail_time(m).astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")
    desc = html.escape((m.get("longDescription") or "").strip())
    if len(desc) > 450:
        desc = desc[:447] + "..."
    return (
        f"📺 <b>{html.escape(SHOW_NAME)}</b>\n"
        f"🎬 Episode {m['episodeNumber']}: <b>{html.escape(m.get('episodeTitle', ''))}</b>\n"
        f"🗣 Language: Hindi\n"
        f"🕗 {when}\n\n"
        f"{desc}\n\n"
        f"🔗 {link}"
    )


def get_poster(m):
    emf = m.get("emfAttributes", {})
    url = emf.get("landscape_thumb") or emf.get("thumbnail")
    if not url:
        return None
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code == 200 and r.content:
            return r.content
        print("Poster download failed:", r.status_code)
    except requests.RequestException as e:
        print("Poster error:", e)
    return None


def send_telegram(m):
    link = episode_link(m)
    caption = build_caption(m, link)
    markup = json.dumps({"inline_keyboard": [[{"text": "▶️ Watch on SonyLIV", "url": link}]]})
    base = f"https://api.telegram.org/bot{TOKEN}"
    poster = get_poster(m)
    if poster:
        r = requests.post(
            f"{base}/sendPhoto",
            data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML", "reply_markup": markup},
            files={"photo": ("poster.jpg", poster)},
            timeout=60,
        )
    else:
        r = requests.post(
            f"{base}/sendMessage",
            data={"chat_id": CHAT_ID, "text": caption, "parse_mode": "HTML", "reply_markup": markup},
            timeout=60,
        )
    print("Telegram status:", r.status_code, r.text[:200])
    return r.status_code == 200


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def main():
    if not TOKEN or not CHAT_ID:
        print("TG_BOT_TOKEN / TG_CHAT_ID set nahi hain")
        sys.exit(1)

    eps = fetch_episodes()
    if not eps:
        print("Koi episode nahi mila")
        sys.exit(1)

    now = datetime.now(timezone.utc)
    live = [m for m in eps if avail_time(m) <= now]
    print("Latest on API:", eps[0]["episodeNumber"], "| Live abhi:", [m["episodeNumber"] for m in live][:3])

    state = load_state()
    last = state.get("last_episode")
    if last is None:
        pending = live[:1]  # pehli run: sirf latest live episode (test)
    else:
        pending = sorted((m for m in live if int(m["episodeNumber"]) > last),
                         key=lambda m: int(m["episodeNumber"]))

    if not pending:
        print("Naya kuch nahi")
        return

    for m in pending:
        if send_telegram(m):
            state["last_episode"] = int(m["episodeNumber"])
            state["last_content_id"] = m["contentId"]
            save_state(state)
        else:
            sys.exit(1)


if __name__ == "__main__":
    main()
