"""SonyLIV serials -> Telegram notifier (multi-show)."""
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

# ---------------------------------------------------------------
# YAHAN SHOWS ADD KARO: (naam, show_id, link wala slug)
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
    {"name": "Pushpa Impossible", "id": 1700000921,
     "slug": "pushpa-impossible"},
    {"name": "Indian Game Show", "id": 1790007836,
     "slug": "indian-game-show"},
]

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


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


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


def get_poster(m):
    emf = m.get("emfAttributes", {})
    url = emf.get("landscape_thumb") or emf.get("thumbnail")
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


def send_telegram(show, m):
    link = episode_link(show, m)
    caption = build_caption(show, m, link)
    base = f"https://api.telegram.org/bot{TOKEN}"
    poster = get_poster(m)
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
    return state


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


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
    save_state(state)
    if ok == 0:
        print("Koi bhi show kaam nahi kiya")
        sys.exit(1)


if __name__ == "__main__":
    main()
