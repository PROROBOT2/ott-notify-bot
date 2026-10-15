
"""SonyLIV + ZEE5 serials -> Telegram notifier."""
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urljoin

import requests

# ---------------- SONYLIV SHOWS ----------------
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
    {"name": "Kaun Banega Crorepati", "id": 1700000195,
     "slug": "kaun-banega-crorepati"},
    {"name": "Family Full House With Rohit Sharma", "id": 1790007851,
     "slug": "family-full-house-with-rohit-sharma"},
    {"name": "Mahotsav Aadi Shakti Ka", "id": 1790007885,
     "slug": "mahotsav-aadi-shakti-ka"},
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
NOTIFY_MODE = os.environ.get("NOTIFY_MODE", "air")
TOKEN = os.environ.get("TG_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TG_CHAT_ID", "")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.sonyliv.com",
    "Referer": "https://www.sonyliv.com/",
}

# ---------------- ZEE5 SHOW ----------------
ZEE5_SHOW_NAME = "Tumm Se Tumm Tak"
ZEE5_SHOW_URL = (
    "https://www.zee5.com/tv-shows/details/"
    "tumm-se-tumm-tak/0-6-4z5727104"
)
ZEE5_EPISODES_URL = ZEE5_SHOW_URL + "/episodes"
ZEE5_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.zee5.com/",
}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# ---------------- TELEGRAM SENDER ----------------
def telegram_send(caption, poster_url=None):
    api = f"https://api.telegram.org/bot{TOKEN}"
    photo = None

    if poster_url:
        try:
            r = requests.get(poster_url, headers=ZEE5_HEADERS, timeout=25)
            if r.ok and r.content:
                photo = r.content
        except requests.RequestException as exc:
            print("Poster download error:", exc)

    try:
        if photo:
            r = requests.post(
                f"{api}/sendPhoto",
                data={
                    "chat_id": CHAT_ID,
                    "caption": caption,
                    "parse_mode": "HTML",
                },
                files={"photo": ("episode-poster.jpg", photo)},
                timeout=60,
            )
            if r.ok:
                print("Telegram poster sent")
                return True
            print("Photo send failed:", r.text[:200])

        r = requests.post(
            f"{api}/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": caption,
                "parse_mode": "HTML",
            },
            timeout=30,
        )
        print("Telegram message:", r.status_code, r.text[:150])
        return r.ok
    except requests.RequestException as exc:
        print("Telegram error:", exc)
        return False


# ---------------- SONYLIV ----------------
def fetch_episodes(show):
    url = API_BASE.format(title=quote(show["name"], safe=""))
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()

    if data.get("resultCode") != "OK":
        raise RuntimeError(f"SonyLIV response error: {json.dumps(data)[:200]}")

    metas = []
    for container in data["resultObj"]["containers"]:
        meta = container.get("metadata", {})
        if meta.get("contentSubtype") != "EPISODE" or not meta.get("episodeNumber"):
            continue
        meta["_parents"] = [
            parent.get("parentId") for parent in container.get("parents", [])
        ]
        metas.append(meta)

    episodes = [m for m in metas if show["id"] in m["_parents"]]
    if not episodes:
        episodes = [
            m for m in metas
            if norm(show["name"]) in norm(m.get("title"))
        ]

    episodes.sort(
        key=lambda m: int(m["episodeNumber"]),
        reverse=True,
    )
    return episodes


def avail_time(meta):
    if NOTIFY_MODE == "switch":
        iso = (
            meta.get("emfAttributes", {})
            .get("entitlementSwitchTime", {})
            .get("_in")
        )
        if iso:
            return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return datetime.fromtimestamp(
        meta["originalAirDate"] / 1000,
        tz=timezone.utc,
    )


def sonyliv_link(show, meta):
    title = re.sub(
        r"['\u2019`]", "",
        meta.get("episodeTitle", "").lower(),
    )
    slug = re.sub(r"[^a-z0-9]+", "-", title).strip("-")
    return (
        f"https://www.sonyliv.com/shows/{show['slug']}-{show['id']}/"
        f"{slug}-{meta['contentId']}?watch=true"
    )


def process_sonyliv(show, state, now):
    print("== SONYLIV:", show["name"])
    episodes = fetch_episodes(show)
    if not episodes:
        print("No episodes found")
        return True

    live = [m for m in episodes if avail_time(m) <= now]
    record = state.setdefault("shows", {}).get(str(show["id"]), {})
    last = record.get("last_episode")

    if last is None:
        pending = live[:1]
    else:
        pending = sorted(
            [m for m in live if int(m["episodeNumber"]) > int(last)],
            key=lambda m: int(m["episodeNumber"]),
        )

    for meta in pending:
        link = sonyliv_link(show, meta)
        date = avail_time(meta).astimezone(IST).strftime("%d %B %Y")
        lines = [
            "#NewEpisode #DMT #SONYLIV #Notification",
            "",
            f"<b>Show Name: {html.escape(show['name'])}</b>",
        ]

        season = meta.get("season")
        if season not in (None, ""):
            try:
                lines.append(f"<b>Season Number: {int(season):02d}</b>")
            except (TypeError, ValueError):
                lines.append(
                    f"<b>Season Number: {html.escape(str(season))}</b>"
                )

        lines.extend([
            f"<b>Episode Number: {meta['episodeNumber']}</b>",
            f"<b>Episode Title: {html.escape(meta.get('episodeTitle', ''))}</b>",
            f"<b>Release Date: {date}</b>",
            "<b>Language: Hindi</b>",
            "",
            "<b>Show Link:</b>",
            f"<code>{html.escape(link)}</code>",
        ])

        emf = meta.get("emfAttributes", {})
        poster = emf.get("landscape_thumb") or emf.get("thumbnail")
        if not telegram_send("\n".join(lines), poster):
            return False

        state.setdefault("shows", {})[str(show["id"])] = {
            "last_episode": int(meta["episodeNumber"]),
            "last_content_id": meta["contentId"],
        }
        save_state(state)

    if not pending:
        print("No new SonyLIV episode")
    return True


# ---------------- ZEE5 EPISODE PAGE READER ----------------
def extract_meta(page_html):
    result = {}
    for tag in re.findall(r"<meta\b[^>]*>", page_html, re.I):
        key = re.search(
            r"""(?:property|name)\s*=\s*["']([^"']+)["']""",
            tag, re.I,
        )
        value = re.search(
            r"""content\s*=\s*["']([^"']*)["']""",
            tag, re.I,
        )
        if key and value:
            result[key.group(1).lower()] = html.unescape(value.group(1))

    title = re.search(
        r"<title[^>]*>(.*?)</title>",
        page_html,
        re.I | re.S,
    )
    if title:
        result["page_title"] = html.unescape(
            re.sub(r"\s+", " ", title.group(1)).strip()
        )
    return result


def get_zee5_episodes():
    response = requests.get(
        ZEE5_EPISODES_URL,
        headers=ZEE5_HEADERS,
        timeout=35,
    )
    response.raise_for_status()
    page = response.text.replace("\\u002F", "/").replace("\\/", "/")
    page = page.replace("&amp;", "&")

    # Read links from rendered HTML or JSON embedded in the public page.
    pattern = re.compile(
        r"""(?:https?:)?//www\.zee5\.com/(?:global/)?tv-shows/details/"""
        r"""tumm-se-tumm-tak/0-6-4z5727104/[^"'\\\s<>]+?/0-1-[A-Za-z0-9]+"""
        r"""|/tv-shows/details/tumm-se-tumm-tak/0-6-4z5727104/"""
        r"""[^"'\\\s<>]+?/0-1-[A-Za-z0-9]+""",
        re.I,
    )
    links = set()
    for match in pattern.findall(page):
        links.add(urljoin("https://www.zee5.com", match))

    if not links:
        raise RuntimeError(
            "ZEE5 episode links page se nahi mile. "
            "Website ka source format badla ho sakta hai."
        )

    episodes = []
    for link in links:
        try:
            r = requests.get(link, headers=ZEE5_HEADERS, timeout=25)
            if not r.ok:
                continue
            metadata = extract_meta(r.text)
            title = (
                metadata.get("og:title")
                or metadata.get("twitter:title")
                or metadata.get("page_title")
                or ""
            )
            description = (
                metadata.get("og:description")
                or metadata.get("description")
                or ""
            )
            poster = (
                metadata.get("og:image")
                or metadata.get("twitter:image")
                or ""
            )

            # Only trust a number when page metadata explicitly includes it.
            number_match = re.search(
                r"\bEpisode\s*(\d+)\b|\bE\s*(\d+)\b",
                title,
                re.I,
            )
            if not number_match:
                number_match = re.search(
                    r"\bEpisode\s*(\d+)\b|\bE\s*(\d+)\b",
                    description,
                    re.I,
                )
            if not number_match:
                continue

            number = int(number_match.group(1) or number_match.group(2))
            title = re.sub(r"\s*[-|]\s*Watch.*$", "", title, flags=re.I)
            title = re.sub(r"\s*[|]\s*ZEE5.*$", "", title, flags=re.I)
            title = re.sub(
                r"^\s*Episode\s*\d+\s*[:|•-]\s*",
                "",
                title,
                flags=re.I,
            ).strip(" -|•:")
            if not title:
                title = f"Episode {number}"

            date_match = re.search(
                r"\b(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\b",
                title + " " + description,
            )
            release_date = date_match.group(1) if date_match else "See official page"

            id_match = re.search(r"(0-1-[A-Za-z0-9]+)", link)
            if not id_match:
                continue

            episodes.append({
                "number": number,
                "title": title,
                "url": link,
                "poster": poster,
                "release_date": release_date,
                "content_id": id_match.group(1),
            })
        except requests.RequestException as exc:
            print("ZEE5 page error:", exc)

    # De-duplicate by episode number.
    unique = {}
    for episode in episodes:
        unique[episode["number"]] = episode

    result = sorted(
        unique.values(),
        key=lambda episode: episode["number"],
        reverse=True,
    )
    if not result:
        raise RuntimeError(
            "ZEE5 pages mile, lekin episode number parse nahi hua. "
            "State update nahi ki gayi."
        )
    return result


def process_zee5(state):
    print("== ZEE5:", ZEE5_SHOW_NAME)
    episodes = get_zee5_episodes()
    latest = episodes[0]
    print("Latest parsed:", latest["number"], latest["title"])

    record = state.setdefault("zee5", {})
    last = record.get("last_episode")

    # First run sends only the latest episode; later runs send new higher numbers.
    if last is None:
        pending = [latest]
    else:
        pending = sorted(
            [ep for ep in episodes if ep["number"] > int(last)],
            key=lambda ep: ep["number"],
        )

    for episode in pending:
        caption = "\n".join([
            "#NewEpisode #ZEE5 #Notification",
            "",
            f"<b>Show Name: {html.escape(ZEE5_SHOW_NAME)}</b>",
            f"<b>Episode Number: {episode['number']}</b>",
            f"<b>Episode Title: {html.escape(episode['title'])}</b>",
            f"<b>Release Date: {html.escape(episode['release_date'])}</b>",
            "<b>Language: Hindi</b>",
            "",
            f'<b>Official Episode Link:</b> <a href="{html.escape(episode["url"], quote=True)}">Watch on ZEE5</a>',
        ])

        if not telegram_send(caption, episode.get("poster")):
            return False

        record["last_episode"] = episode["number"]
        record["last_content_id"] = episode["content_id"]
        save_state(state)

    if not pending:
        print("No new ZEE5 episode")
    return True


# ---------------- STATE ----------------
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as file:
            state = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}

    # Migrate old single-show state to the first SonyLIV show.
    if "last_episode" in state:
        state.setdefault("shows", {})[str(SHOWS[0]["id"])] = {
            "last_episode": state.pop("last_episode"),
            "last_content_id": state.pop("last_content_id", None),
        }

    state.setdefault("shows", {})
    state.setdefault("zee5", {})
    return state


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, indent=2, ensure_ascii=False)


def main():
    if not TOKEN or not CHAT_ID:
        print("TG_BOT_TOKEN / TG_CHAT_ID set nahi hain")
        sys.exit(1)

    state = load_state()
    now = datetime.now(timezone.utc)
    successes = 0

    for show in SHOWS:
        try:
            if process_sonyliv(show, state, now):
                successes += 1
        except Exception as exc:
            print("SONYLIV ERROR:", show["name"], exc)

    try:
        if process_zee5(state):
            successes += 1
    except Exception as exc:
        print("ZEE5 ERROR:", exc)

    save_state(state)
    if successes == 0:
        print("Koi bhi show kaam nahi kiya")
        sys.exit(1)


if __name__ == "__main__":
    main()
