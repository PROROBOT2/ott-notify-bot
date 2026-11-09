"""
youtube_part.py - YouTube Channel New Video Notifications
Strategy:
  1. YouTube RSS feed (no API key needed, completely free)
  2. For each channel, check if new video posted today (IST)
  3. Send Telegram notification with thumbnail + video title + link
  4. Channel IDs auto-detected from handle if not hardcoded
"""

import os, re, datetime, requests, logging
import xml.etree.ElementTree as ET

log = logging.getLogger(__name__)

BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")

YT_RSS_BASE = "https://www.youtube.com/feeds/videos.xml?channel_id="
YT_HANDLE   = "https://www.youtube.com/@{handle}"
YT_VIDEO    = "https://youtu.be/{vid}"
YT_THUMB    = "https://i.ytimg.com/vi/{vid}/maxresdefault.jpg"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

YT_CHANNELS = [
    {
        "name":       "Mr Indian Hacker",
        "channel_id": "UCSiDGb0MnHFGjs4E2WKvShw",
        "handle":     "MrIndianHacker",
        "hashtag":    "#MrIndianHacker",
    },
    {
        "name":       "Crazy XYZ",
        "channel_id": "",
        "handle":     "CrazyXYZ",
        "hashtag":    "#CrazyXYZ",
    },
]

def _ist_now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))

def _tg(method, **kw):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            data={"chat_id": CHANNEL_ID, "parse_mode": "HTML", **kw},
            timeout=30)
        return r.ok
    except Exception as e:
        log.warning(f"Telegram {method} error: {e}")
        return False

def _send(poster_url, caption):
    if poster_url:
        ok = _tg("sendPhoto", photo=poster_url, caption=caption)
        if ok:
            return True
    return _tg("sendMessage", text=caption)

def _detect_channel_id(handle, state):
    cache = state.setdefault("yt_channel_ids", {})
    if cache.get(handle):
        return cache[handle]
    log.info(f"[YOUTUBE] Auto-detecting channel ID for @{handle}...")
    try:
        r = requests.get(
            YT_HANDLE.format(handle=handle),
            headers={"User-Agent": BROWSER_UA},
            timeout=15)
        m = re.search(r'"channelId"\s*:\s*"(UC[A-Za-z0-9_-]{20,})"', r.text)
        if m:
            ch_id = m.group(1)
            cache[handle] = ch_id
            log.info(f"[YOUTUBE] @{handle} → {ch_id}")
            return ch_id
    except Exception as e:
        log.warning(f"[YOUTUBE] Channel ID detect error for @{handle}: {e}")
    return None

def _fetch_videos(channel_id):
    url = YT_RSS_BASE + channel_id
    try:
        r = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=20)
        if r.status_code != 200:
            return []
        ns = {
            'atom': 'http://www.w3.org/2005/Atom',
            'yt':   'http://www.youtube.com/xml/schemas/2015',
        }
        root   = ET.fromstring(r.text)
        videos = []
        for entry in root.findall('atom:entry', ns):
            vid_id = entry.findtext('yt:videoId', namespaces=ns) or ''
            title  = entry.findtext('atom:title',  namespaces=ns) or ''
            pub    = entry.findtext('atom:published', namespaces=ns) or ''
            if not vid_id:
                continue
            try:
                pub_dt  = datetime.datetime.fromisoformat(pub.replace('Z', '+00:00'))
                pub_ist = (pub_dt + datetime.timedelta(hours=5, minutes=30)).date()
            except Exception:
                pub_ist = None
            videos.append({'title': title, 'vid': vid_id, 'pub_ist': pub_ist})
        return videos
    except Exception as e:
        log.warning(f"[YOUTUBE] RSS fetch error: {e}")
        return []

def _make_caption(ch, video, today_ist):
    date_str  = today_ist.strftime("%d %B %Y")
    video_url = YT_VIDEO.format(vid=video['vid'])
    hashtag   = ch.get("hashtag", "#YouTube")
    return (
        f"#NewVideo #DMT {hashtag} #Notification\n\n"
        f"Channel: {ch['name']}\n"
        f"Title: {video['title']}\n"
        f"Date: {date_str}\n\n"
        f"Watch Now:\n<code>{video_url}</code>"
    )

def run(state: dict):
    """Called by bot.py. Returns (ok_count, yt_ok_bool)."""
    now       = _ist_now()
    today_str = now.strftime("%Y-%m-%d")
    today_ist = now.date()
    state.setdefault("yt_sent",        {})
    state.setdefault("yt_channel_ids", {})
    ok_count = sum(1 for ch in YT_CHANNELS
                   if state["yt_sent"].get(ch["name"]) == today_str)
    yt_ok    = ok_count > 0
    pending  = [ch for ch in YT_CHANNELS
                if state["yt_sent"].get(ch["name"]) != today_str]
    if not pending:
        log.info("[YOUTUBE] All channels already notified today")
        return ok_count, yt_ok
    log.info(f"[YOUTUBE] Checking {len(pending)} channels...")
    for ch in pending:
        name  = ch["name"]
        ch_id = ch.get("channel_id", "")
        if not ch_id:
            ch_id = _detect_channel_id(ch["handle"], state)
        if not ch_id:
            log.warning(f"[YOUTUBE] {name}: no channel ID, skipping")
            continue
        videos     = _fetch_videos(ch_id)
        today_vids = [v for v in videos if v["pub_ist"] == today_ist]
        if not today_vids:
            log.info(f"[YOUTUBE] {name}: no new video today yet, will retry")
            continue
        video   = today_vids[0]
        poster  = YT_THUMB.format(vid=video['vid'])
        caption = _make_caption(ch, video, today_ist)
        sent    = _send(poster, caption)
        if sent:
            state["yt_sent"][name] = today_str
            ok_count += 1
            yt_ok     = True
            log.info(f"[YOUTUBE] SENT: {name}")
        else:
            log.error(f"[YOUTUBE] FAILED: {name}")
    return ok_count, yt_ok
