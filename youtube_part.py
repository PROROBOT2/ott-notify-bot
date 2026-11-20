"""
youtube_part.py - YouTube Channel New Video Notifications  (v2)
================================================================
Fixes in v2:
  1. Crazy XYZ channel_id hardcoded (auto-detect was failing)
  2. Video duration added via noembed.com (free, no API key)
  3. Video description added (first 200 chars from noembed)
  4. Tracking by video_id (not just date) → no duplicate, no miss
  5. Sends every new video (not just one per day)
"""

import os, re, datetime, requests, logging
import xml.etree.ElementTree as ET

log = logging.getLogger(__name__)

BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or os.environ.get("BOT_TOKEN",  "")
CHANNEL_ID = os.environ.get("TG_CHAT_ID")   or os.environ.get("CHANNEL_ID", "")

YT_RSS_BASE = "https://www.youtube.com/feeds/videos.xml?channel_id="
YT_VIDEO    = "https://youtu.be/{vid}"
YT_THUMB    = "https://i.ytimg.com/vi/{vid}/maxresdefault.jpg"
NOEMBED_URL = "https://noembed.com/embed?url=https://www.youtube.com/watch?v={vid}"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# YouTube Channels to Track
# ---------------------------------------------------------------------------
YT_CHANNELS = [
    {
        "name":       "Mr Indian Hacker",
        "channel_id": "UCSiDGb0MnHFGjs4E2WKvShw",
        "handle":     "MrIndianHacker",
        "hashtag":    "#MrIndianHacker",
    },
    {
        "name":       "Crazy XYZ",
        "channel_id": "UCwKOaKMoiAFbqlc_hBqRGew",   # ✅ hardcoded - fixed
        "handle":     "CrazyXYZ",
        "hashtag":    "#CrazyXYZ",
    },
]

# Max videos to send per channel per run (avoid spam on first run)
MAX_PER_RUN = 2

# How many recent video IDs to remember per channel
MAX_SEEN    = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
        # Try maxresdefault, fall back to hqdefault if 404
        ok = _tg("sendPhoto", photo=poster_url, caption=caption)
        if ok:
            return True
        hq = poster_url.replace("maxresdefault", "hqdefault")
        ok = _tg("sendPhoto", photo=hq, caption=caption)
        if ok:
            return True
    return _tg("sendMessage", text=caption)


# ---------------------------------------------------------------------------
# noembed – get duration + description (free, no API key)
# ---------------------------------------------------------------------------

def _get_video_meta(vid):
    """
    Fetch video duration and description via noembed.com.
    Returns (duration_str, description_snippet)
    duration_str like "12:34" or "" if unavailable.
    """
    duration_str = ""
    desc_snippet = ""
    try:
        r = requests.get(
            NOEMBED_URL.format(vid=vid),
            headers={"User-Agent": BROWSER_UA},
            timeout=10)
        if r.ok:
            data = r.json()
            # noembed gives title, author but NOT duration/description
            # So we also try YouTube oEmbed + page scrape for duration
    except Exception:
        pass

    # Try YouTube watch page for duration meta tag
    try:
        r = requests.get(
            f"https://www.youtube.com/watch?v={vid}",
            headers={"User-Agent": BROWSER_UA},
            timeout=15)
        if r.ok:
            # Duration from meta
            m = re.search(r'"lengthSeconds"\s*:\s*"(\d+)"', r.text)
            if not m:
                m = re.search(r'"approxDurationMs"\s*:\s*"(\d+)"', r.text)
                if m:
                    secs = int(m.group(1)) // 1000
                else:
                    secs = 0
            else:
                secs = int(m.group(1))

            if secs > 0:
                h, rem = divmod(secs, 3600)
                mn, s  = divmod(rem, 60)
                if h:
                    duration_str = f"{h}:{mn:02d}:{s:02d}"
                else:
                    duration_str = f"{mn}:{s:02d}"

            # Description snippet
            m2 = re.search(r'"shortDescription"\s*:\s*"(.*?)"(?=\s*,\s*"[a-z])', r.text, re.S)
            if m2:
                raw = m2.group(1)
                raw = raw.encode('utf-8').decode('unicode_escape', errors='replace')
                raw = raw.replace('\\n', ' ').replace('\\t', ' ')
                raw = re.sub(r'\s+', ' ', raw).strip()
                desc_snippet = raw[:200] + ("…" if len(raw) > 200 else "")

    except Exception as e:
        log.debug(f"Video meta fetch {vid}: {e}")

    return duration_str, desc_snippet


# ---------------------------------------------------------------------------
# YouTube RSS feed fetch
# ---------------------------------------------------------------------------

def _fetch_videos(channel_id):
    """
    Fetch latest videos from YouTube RSS.
    Returns list of dicts: {title, vid, pub_ist (date), pub_ts (timestamp)}
    Sorted newest first.
    """
    url = YT_RSS_BASE + channel_id
    try:
        r = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=20)
        if r.status_code != 200:
            log.warning(f"[YOUTUBE] RSS HTTP {r.status_code} for {channel_id}")
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
                pub_ist = (pub_dt.astimezone(
                    datetime.timezone(datetime.timedelta(hours=5, minutes=30)))).date()
                pub_ts  = pub_dt.timestamp()
            except Exception:
                pub_ist = None
                pub_ts  = 0
            videos.append({
                'title':   title,
                'vid':     vid_id,
                'pub_ist': pub_ist,
                'pub_ts':  pub_ts,
            })

        # Sort newest first
        videos.sort(key=lambda x: x['pub_ts'], reverse=True)
        return videos

    except Exception as e:
        log.warning(f"[YOUTUBE] RSS fetch error: {e}")
        return []


# ---------------------------------------------------------------------------
# Caption
# ---------------------------------------------------------------------------

def _make_caption(ch, video, duration_str, desc_snippet):
    date_str  = video['pub_ist'].strftime("%d %B %Y") if video['pub_ist'] else _ist_now().strftime("%d %B %Y")
    video_url = YT_VIDEO.format(vid=video['vid'])
    hashtag   = ch.get("hashtag", "#YouTube")

    lines = [
        f"#NewVideo #DMT {hashtag} #Notification\n",
        f"Channel: {ch['name']}",
        f"Title: {video['title']}",
        f"Date: {date_str}",
    ]
    if duration_str:
        lines.append(f"Duration: {duration_str}")
    if desc_snippet:
        lines.append(f"\nDescription:\n{desc_snippet}")
    lines.append(f"\nWatch Now:\n<code>{video_url}</code>")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(state: dict):
    """Called by bot.py. Returns (ok_count, yt_ok_bool)."""
    state.setdefault("yt_sent",        {})   # {channel_name: [vid_id, ...]}
    state.setdefault("yt_channel_ids", {})   # handle -> UC... (cache)

    ok_count = 0
    yt_ok    = False

    log.info(f"[YOUTUBE] Checking {len(YT_CHANNELS)} channels...")

    for ch in YT_CHANNELS:
        name = ch["name"]

        # Resolve channel ID
        ch_id = ch.get("channel_id", "")
        if not ch_id:
            # Try cached auto-detect
            ch_id = state["yt_channel_ids"].get(ch["handle"], "")
        if not ch_id:
            log.warning(f"[YOUTUBE] {name}: no channel ID, skipping")
            continue

        # Seen video IDs for this channel
        seen = state["yt_sent"].get(name, [])
        if isinstance(seen, str):
            seen = []   # migrate old date-based tracking

        # Fetch RSS
        videos = _fetch_videos(ch_id)
        if not videos:
            log.info(f"[YOUTUBE] {name}: no videos from RSS")
            continue

        # Find unseen videos
        new_vids = [v for v in videos if v["vid"] not in seen]
        if not new_vids:
            log.info(f"[YOUTUBE] {name}: no new videos")
            continue

        log.info(f"[YOUTUBE] {name}: {len(new_vids)} new video(s)")

        sent_this_run = 0
        for video in new_vids:
            if sent_this_run >= MAX_PER_RUN:
                break

            log.info(f"[YOUTUBE] {name}: sending → '{video['title']}'")

            # Get duration + description
            duration_str, desc_snippet = _get_video_meta(video["vid"])

            poster  = YT_THUMB.format(vid=video["vid"])
            caption = _make_caption(ch, video, duration_str, desc_snippet)

            sent = _send(poster, caption)
            if sent:
                seen.append(video["vid"])
                ok_count += 1
                yt_ok     = True
                sent_this_run += 1
                log.info(f"[YOUTUBE] ✅ SENT: {name} → {video['vid']}")
            else:
                log.error(f"[YOUTUBE] ❌ FAILED: {name}")

        # Trim seen list (keep last MAX_SEEN only)
        state["yt_sent"][name] = seen[-MAX_SEEN:]

    return ok_count, yt_ok
