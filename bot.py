#!/usr/bin/env python3
"""
OTT Telegram Notification Bot
Platforms: SonyLIV + ZEE5 + JioHotstar
"""

import os
import json
import time
import gzip
import datetime
import re
import urllib.request
from curl_cffi import requests as cf_requests

BOT_TOKEN  = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]
STATE_FILE = "state.json"

# ══ SONYLIV ══════════════════════════════════════════════════════════════════
SHOWS = [
    {"name": "Shrimad Ramayan",            "id": "1700000537"},
    {"name": "Pukaar",                     "id": "1700000741"},
    {"name": "Crime Patrol Dial 100",      "id": "1700000033"},
    {"name": "Pushpa Impossible",          "id": "1700000512"},
    {"name": "Aur Bhai Kya Chal Raha Hai", "id": "1700000840"},
    {"name": "Vanshaj",                    "id": "1700000596"},
    {"name": "Wagle Ki Duniya",            "id": "1700000353"},
    {"name": "May I Come In Madam",        "id": "1700000086"},
]

def fetch_episodes(show):
    url = (
        f"https://apiv2.sonyliv.com/AGL/1.4/A/ENG/WEB/IN/DL/CONTENT/VIDEOURL/VOD/"
        f"{show['id']}?sortOrder=desc&size=1"
    )
    r = cf_requests.get(url, impersonate="chrome110", timeout=15, headers={
        "security_token": "1234567890",
        "app_version":    "3.4.9",
        "content-type":   "application/json",
    })
    return r.json()

# ══ ZEE5 ═════════════════════════════════════════════════════════════════════
ZEE5_SHOWS = [
    {"name": "Bhagya Lakshmi",                      "id": "0-6-3z5v58289-ep"},
    {"name": "Kumkum Bhagya",                       "id": "0-6-3z5v58292-ep"},
    {"name": "Kundali Bhagya",                      "id": "0-6-3z5v58293-ep"},
    {"name": "Meet",                                "id": "0-6-3z5v58264-ep"},
    {"name": "Pyaar Ka Pehla Naam Radha Mohan",     "id": "0-6-3z5v58373-ep"},
    {"name": "Bhabiji Ghar Par Hain",               "id": "0-6-3z5v00316-ep"},
    {"name": "Trinayani",                           "id": "0-6-3z5v58252-ep"},
    {"name": "Jamai Raja",                          "id": "0-6-3z5v00101-ep"},
    {"name": "Brahmarakshas",                       "id": "0-6-3z5v00149-ep"},
]

def fetch_zee5_episodes(show):
    url = f"https://www.zee5.com/shows/details/{show['id']}"
    r = cf_requests.get(url, impersonate="chrome110", timeout=20, headers={
        "Accept-Language": "en-IN,en;q=0.9",
        "Referer":         "https://www.zee5.com/",
    })
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', r.text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except Exception:
        return []
    episodes = []
    if isinstance(data, list):
        for item in data:
            if item.get("@type") == "TVEpisode":
                episodes.append(item)
    elif data.get("@type") == "TVEpisode":
        episodes.append(data)
    return episodes

# ══ JIOHOTSTAR ════════════════════════════════════════════════════════════════
HOTSTAR_SHOWS = [
    {"name": "Bigg Boss",                         "id": "1971002586"},
    {"name": "Roadies Rebirth",                   "id": "1971002274"},
    {"name": "Yeh Rishta Kya Kehlata Hai",        "id": "586"},
    {"name": "Yeh Fitoor Tera",                   "id": "1271650854"},
    {"name": "Anupama",                           "id": "1260022017"},
    {"name": "Oh Humnava Tum Dena Saath Mera",    "id": "1271616684"},
    {"name": "Tuu Juliet Jatt Di",                "id": "1271463159"},
    {"name": "Dhamaal with Pati Patni aur Panga", "id": "1271427882"},
    {"name": "Udne Ki Aasha",                     "id": "1271270408"},
    {"name": "Dr. Aarambhi",                      "id": "1271522330"},
    {"name": "Mannat Har Khushi Paane Ki",        "id": "1971311993"},
    {"name": "Sayoneee",                          "id": "1271666144"},
]

_HS_API_H = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":          "application/json",
    "x-country-code":  "IN",
    "x-platform-code": "web",
    "x-tenant-id":     "in",
    "Accept-Encoding": "gzip",
}
_HS_SMAP_H = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Encoding": "gzip, deflate",
}
_HS_CDN         = "https://img10.hotstar.com/image/upload/f_auto,t_web_hs_3x"
_hs_cache       = {}
_hs_cache_built = False   # ← separate flag (empty dict {} is falsy in Python!)


def _hs_raw(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = resp.read()
        if 'gzip' in resp.info().get('Content-Encoding', ''):
            data = gzip.decompress(data)
        return data.decode('utf-8')


def _hs_json(url):
    return json.loads(_hs_raw(url, _HS_API_H))


def _build_hs_cache():
    global _hs_cache, _hs_cache_built
    if _hs_cache_built:
        return
    _hs_cache_built = True
    show_ids = {s["id"] for s in HOTSTAR_SHOWS}
    for i in range(1, 10):
        url = f"https://www.hotstar.com/in/new-sitemap-EPISODE-{i}.xml"
        try:
            xml = _hs_raw(url, _HS_SMAP_H)
            # Regex instead of XML parser — handles invalid XML tokens
            locs     = re.findall(r'<loc>(.*?)</loc>', xml)
            lastmods = re.findall(r'<lastmod>(.*?)</lastmod>', xml)
            for loc, lastmod in zip(locs, lastmods):
                parts = loc.rstrip("/").split("/")
                try:
                    idx = parts.index("shows")
                    sid = parts[idx + 2]
                    if sid in show_ids:
                        _hs_cache.setdefault(sid, []).append({
                            "url":     loc,
                            "lastmod": lastmod,
                            "ep_id":   parts[-2],
                        })
                except (ValueError, IndexError):
                    pass
            print(f"[HS] Sitemap {i}: OK")
        except Exception as e:
            print(f"[HS] Sitemap {i} error: {e}")


def fetch_hotstar_latest(show):
    _build_hs_cache()
    sid = show["id"]
    eps = _hs_cache.get(sid, [])
    if not eps:
        return None
    latest = sorted(eps, key=lambda x: x["lastmod"], reverse=True)[0]
    ep_id  = latest["ep_id"]
    try:
        data  = _hs_json(f"https://api.hotstar.com/o/v2/content/{ep_id}")
        item  = data.get("body", {}).get("results", {}).get("item", {})
        title = item.get("title", "")
        ep_no = item.get("episodeNo", "")
        bcast = item.get("broadCastDate") or item.get("startDate") or 0
        bd = (
            datetime.datetime.fromtimestamp(bcast, tz=datetime.timezone.utc)
                              .strftime("%d %b %Y")
            if bcast else latest["lastmod"]
        )
        img_url = None
        for img_set in item.get("imageSets", []):
            pub = img_set.get("publicUri", "")
            if pub:
                img_url = f"{_HS_CDN}/{pub}"
                break
        if not img_url:
            try:
                sd = _hs_json(f"https://api.hotstar.com/o/v2/content/{sid}")
                si = sd.get("body", {}).get("results", {}).get("item", {})
                for img_set in si.get("imageSets", []):
                    if img_set.get("imageType") in ("v", "v23", "h"):
                        pub = img_set.get("publicUri", "")
                        if pub:
                            img_url = f"{_HS_CDN}/{pub}"
                            break
            except Exception:
                pass
        return {"title": title, "episodeNo": ep_no, "broadcastDate": bd,
                "imageUrl": img_url, "watchUrl": latest["url"], "ep_id": ep_id}
    except Exception as e:
        print(f"[HS] API error ep {ep_id}: {e}")
        parts = latest["url"].rstrip("/").split("/")
        slug  = parts[-3] if len(parts) >= 3 else ""
        return {"title": slug.replace("-", " ").title(), "episodeNo": "",
                "broadcastDate": latest["lastmod"], "imageUrl": None,
                "watchUrl": latest["url"], "ep_id": ep_id}

# ══ TELEGRAM ══════════════════════════════════════════════════════════════════
def send_telegram(caption: str, photo_url: str | None = None):
    base = f"https://api.telegram.org/bot{BOT_TOKEN}"
    if photo_url:
        payload = {"chat_id": CHANNEL_ID, "photo": photo_url,
                   "caption": caption, "parse_mode": "HTML"}
        endpoint = f"{base}/sendPhoto"
    else:
        payload = {"chat_id": CHANNEL_ID, "text": caption,
                   "parse_mode": "HTML", "disable_web_page_preview": False}
        endpoint = f"{base}/sendMessage"
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(endpoint, data=data,
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

# ══ STATE ═════════════════════════════════════════════════════════════════════
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            s = json.load(f)
    else:
        s = {}
    s.setdefault("shows",   {})
    s.setdefault("zee5",    {})
    s.setdefault("hotstar", {})
    return s

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# ══ MAIN ══════════════════════════════════════════════════════════════════════
def main():
    state = load_state()
    sent  = 0

    # SonyLIV
    for show in SHOWS:
        try:
            data  = fetch_episodes(show)
            items = (data.get("resultObj", {})
                        .get("content", [{}])[0]
                        .get("episodeList", []))
            if not items:
                continue
            ep    = items[0]
            ep_id = str(ep.get("id", ""))
            if not ep_id or state["shows"].get(show["id"]) == ep_id:
                continue
            title    = ep.get("episodeTitle", "")
            ep_no    = ep.get("episodeNumber", "")
            season   = ep.get("season", "")
            lang     = ep.get("language", "")
            air_date = ep.get("premierDate", "")
            img      = ep.get("episodeImage", "")
            link     = f"https://www.sonyliv.com/shows/{show['id']}/episodes/{ep_id}"
            caption  = (
                f"🎬 <b>{show['name']}</b>\n"
                f"📺 Season {season} | Episode {ep_no}\n"
                f"🎭 {title}\n"
                f"📅 {air_date}  🗣️ {lang}\n\n"
                f"▶️ <a href='{link}'>Watch on SonyLIV</a>\n\n"
                f"#NewEpisode #SonyLIV #Notification"
            )
            send_telegram(caption, img or None)
            state["shows"][show["id"]] = ep_id
            save_state(state)
            sent += 1
            time.sleep(2)
        except Exception as e:
            print(f"[SonyLIV] {show['name']}: {e}")

    # ZEE5
    for show in ZEE5_SHOWS:
        try:
            episodes = fetch_zee5_episodes(show)
            if not episodes:
                continue
            ep    = episodes[0]
            ep_id = ep.get("identifier", ep.get("url", ""))
            if not ep_id or state["zee5"].get(show["id"]) == ep_id:
                continue
            title    = ep.get("name", "")
            ep_no    = ep.get("episodeNumber", "")
            season   = ep.get("partOfSeason", {}).get("seasonNumber", "")
            lang     = ep.get("inLanguage", "")
            air_date = ep.get("datePublished", "")
            img      = ep.get("thumbnailUrl", ep.get("image", ""))
            link     = ep.get("url", f"https://www.zee5.com/shows/details/{show['id']}")
            if isinstance(img, list):
                img = img[0] if img else ""
            caption  = (
                f"🎬 <b>{show['name']}</b>\n"
                f"📺 Season {season} | Episode {ep_no}\n"
                f"🎭 {title}\n"
                f"📅 {air_date}  🗣️ {lang}\n\n"
                f"▶️ <a href='{link}'>Watch on ZEE5</a>\n\n"
                f"#NewEpisode #ZEE5 #Notification"
            )
            send_telegram(caption, img or None)
            state["zee5"][show["id"]] = ep_id
            save_state(state)
            sent += 1
            time.sleep(2)
        except Exception as e:
            print(f"[ZEE5] {show['name']}: {e}")

    # JioHotstar
    for show in HOTSTAR_SHOWS:
        try:
            ep = fetch_hotstar_latest(show)
            if not ep:
                continue
            ep_id = ep["ep_id"]
            if state["hotstar"].get(show["id"]) == ep_id:
                continue
            ep_line = f"📺 Episode {ep['episodeNo']}\n" if ep["episodeNo"] else ""
            caption = (
                f"🎬 <b>{show['name']}</b>\n"
                f"{ep_line}"
                f"🎭 {ep['title']}\n"
                f"📅 {ep['broadcastDate']}\n\n"
                f"▶️ <a href='{ep['watchUrl']}'>Watch on JioHotstar</a>\n\n"
                f"#NewEpisode #JioHotstar #Notification"
            )
            send_telegram(caption, ep["imageUrl"] or None)
            state["hotstar"][show["id"]] = ep_id
            save_state(state)
            sent += 1
            time.sleep(2)
        except Exception as e:
            print(f"[Hotstar] {show['name']}: {e}")

    print(f"Done. Sent {sent} new notifications.")


if __name__ == "__main__":
    main()
