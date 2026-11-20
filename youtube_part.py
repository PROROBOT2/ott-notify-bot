"""
youtube_part.py - YouTube Channel New Video Notifications  (v3)
================================================================
Fixes in v3:
  1. Crazy XYZ correct channel ID (was wrong in v2)
  2. Migration fix: date-string -> list; old videos marked seen WITHOUT re-sending
  3. All 10 channels added with hardcoded UC IDs
  4. Duration + Description via YouTube page scrape
  5. Per-video tracking (not date-based)
"""

import os, re, datetime, requests, logging
import xml.etree.ElementTree as ET

log = logging.getLogger(__name__)

BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or os.environ.get("BOT_TOKEN",  "")
CHANNEL_ID = os.environ.get("TG_CHAT_ID")   or os.environ.get("CHANNEL_ID", "")

YT_RSS_BASE = "https://www.youtube.com/feeds/videos.xml?channel_id="
YT_VIDEO    = "https://youtu.be/{vid}"
YT_THUMB    = "https://i.ytimg.com/vi/{vid}/maxresdefault.jpg"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# YouTube Channels to Track  (all UC IDs hardcoded)
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
        "channel_id": "UCebC4x5l2-PQxg46Ucv9CsA",   # ✅ corrected
        "handle":     "CrazyXYZ",
        "hashtag":    "#CrazyXYZ",
    },
    {
        "name":       "The Experiment TV",
        "channel_id": "UC2CBHC5hi5gwlL9gUUYakDw",
        "handle":     "TheExperimentTv",
        "hashtag":    "#TheExperimentTV",
    },
    {
        "name":       "Experiment King",
        "channel_id": "UCmDC4D2f3Ux1vEwCdmPCplA",
        "handle":     "Experiment__King",
        "hashtag":    "#ExperimentKing",
    },
    {
        "name":       "Amit XYZ",
        "channel_id": "UCfIpZ2GsJc8nf94udbaSOpg",
        "handle":     "AmitXYZ",
        "hashtag":    "#AmitXYZ",
    },
    {
        "name":       "MR. INDIAN HACKER Vlogs",
        "channel_id": "UCOcD_2N04_XS6XgImIWcR1Q",
        "handle":     "mrindianhackervlogs",
        "hashtag":    "#MrIndianHackerVlogs",
    },
    {
        "name":       "Mr Sonu Bhai 2.0",
        "channel_id": "",                              # auto-detect from handle
        "handle":     "OfficialMrSonuBhaii2.0",
        "hashtag":    "#MrSonuBhai",
    },
    {
        "name":       "Nishu Tiwari",
        "channel_id": "UC5aKead6Q8YQYjceizxJIuw",
        "handle":     "inishutiwari",
        "hashtag":    "#NishuTiwari",
    },
    {
        "name":       "Techno Gamerz",
        "channel_id": "UCX8pnu3DYUnx8qy8V_c6oHg",
        "handle":     "TechnoGamerzOfficial",
        "hashtag":    "#TechnoGamerz",
    },
    {
        "name":       "Sourav Joshi Vlogs",
        "channel_id": "UCjvgGbPP-_lZ4x8Wv85n89Q",
        "handle":     "souravjvlogs",
        "hashtag":    "#SouravJoshiVlogs",
    },
]

# Max videos to send per channel per run (avoid spam)
MAX_PER_RUN = 2
# Max seen IDs to remember per channel
MAX_SEEN    = 40


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
        ok = _tg("sendPhoto", photo=poster_url, caption=caption)
        if ok:
            return True
        # Try hqdefault if maxresdefault fails
        hq = poster_url.replace("maxresdefault", "hqdefault")
        if hq != poster_url:
            ok = _tg("sendPhoto", photo=hq, caption=caption)
            if ok:
                return True
    return _tg("sendMessage", text=caption)


# ---------------------------------------------------------------------------
# Channel ID auto-detection (for channels without hardcoded ID)
# ---------------------------------------------------------------------------

def _detect_channel_id(handle, state):
    cache = state.setdefault("yt_channel_ids", {})
    if cache.get(handle):
        return cache[handle]

    log.info(f"[YOUTUBE] Auto-detecting channel ID for @{handle}...")
    try:
        r = requests.get(
            f"https://www.youtube.com/@{handle}",
            headers={"User-Agent": BROWSER_UA},
            timeout=15)
        m = re.search(r'"channelId"\s*:\s*"(UC[A-Za-z0-9_-]{20,})"', r.text)
        if m:
            ch_id = m.group(1)
            cache[handle] = ch_id
            log.info(f"[YOUTUBE] @{handle} → {ch_id}")
            return ch_id
    except Exception as e:
        log.warning(f"[YOUTUBE] Channel ID detect error @{handle}: {e}")
    return None


# ---------------------------------------------------------------------------
# Video metadata: duration + description
# ---------------------------------------------------------------------------

def _get_video_meta(vid):
    """
    Scrape YouTube watch page for duration and description.
    Returns (duration_str, desc_snippet)  e.g. ("18:42", "Aaj maine...")
    """
    duration_str = ""
    desc_snippet = ""
    try:
        r = requests.get(
            f"https://www.youtube.com/watch?v={vid}",
            headers={"User-Agent": BROWSER_UA},
            timeout=15)
        if not r.ok:
            return "", ""
        text = r.text

        # Duration
        m = re.search(r'"lengthSeconds"\s*:\s*"(\d+)"', text)
        if m:
            secs = int(m.group(1))
        else:
            m2 = re.search(r'"approxDurationMs"\s*:\s*"(\d+)"', text)
            secs = int(m2.group(1)) // 1000 if m2 else 0

        if secs > 0:
            h, rem = divmod(secs, 3600)
            mn, s  = divmod(rem, 60)
            duration_str = f"{h}:{mn:02d}:{s:02d}" if h else f"{mn}:{s:02d}"

        # Description
        m3 = re.search(r'"shortDescription"\s*:\s*"(.*?)"(?=\s*,\s*"[a-z])', text, re.S)
        if m3:
            raw = m3.group(1)
            try:
                raw = raw.encode('utf-8').decode('unicode_escape', errors='replace')
            except Exception:
                pass
            raw = raw.replace('\\n', ' ').replace('\\t', ' ').replace('\\"', '"')
            raw = re.sub(r'\s+', ' ', raw).strip()
            desc_snippet = raw[:220] + ("…" if len(raw) > 220 else "")

    except Exception as e:
        log.debug(f"Video meta {vid}: {e}")

    return duration_str, desc_snippet


# ---------------------------------------------------------------------------
# YouTube RSS feed
# ---------------------------------------------------------------------------

def _fetch_videos(channel_id):
    """
    Returns list of dicts: {title, vid, pub_ist, pub_ts}
    Sorted newest first.
    """
    try:
        r = requests.get(
            YT_RSS_BASE + channel_id,
            headers={"User-Agent": BROWSER_UA},
            timeout=20)
        if r.status_code != 200:
            log.warning(f"[YOUTUBE] RSS HTTP {r.status_code} for {channel_id}")
            return []

        ns = {
            'atom': 'http://www.w3.org/2005/Atom',
            'yt':   'http://www.youtube.com/xml/schemas/2015',
        }
        IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        root   = ET.fromstring(r.text)
        videos = []

        for entry in root.findall('atom:entry', ns):
            vid   = entry.findtext('yt:videoId',    namespaces=ns) or ''
            title = entry.findtext('atom:title',    namespaces=ns) or ''
            pub   = entry.findtext('atom:published', namespaces=ns) or ''
            if not vid:
                continue
            try:
                pub_dt  = datetime.datetime.fromisoformat(pub.replace('Z', '+00:00'))
                pub_ist = pub_dt.astimezone(IST).date()
                pub_ts  = pub_dt.timestamp()
            except Exception:
                pub_ist = None
                pub_ts  = 0
            videos.append({'title': title, 'vid': vid,
                           'pub_ist': pub_ist, 'pub_ts': pub_ts})

        videos.sort(key=lambda x: x['pub_ts'], reverse=True)
        return videos

    except Exception as e:
        log.warning(f"[YOUTUBE] RSS fetch error: {e}")
        return []


# ---------------------------------------------------------------------------
# Caption
# ---------------------------------------------------------------------------

def _make_caption(ch, video, duration_str, desc_snippet):
    date_str = (video['pub_ist'].strftime("%d %B %Y")
                if video['pub_ist'] else _ist_now().strftime("%d %B %Y"))
    url      = YT_VIDEO.format(vid=video['vid'])
    hashtag  = ch.get("hashtag", "#YouTube")

    parts = [
        f"#NewVideo #DMT {hashtag} #Notification\n",
        f"Channel: {ch['name']}",
        f"Title: {video['title']}",
        f"Date: {date_str}",
    ]
    if duration_str:
        parts.append(f"Duration: {duration_str}")
    if desc_snippet:
        parts.append(f"\nDescription:\n{desc_snippet}")
    parts.append(f"\nWatch Now:\n<code>{url}</code>")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(state: dict):
    """Called by bot.py. Returns (ok_count, yt_ok_bool)."""
    state.setdefault("yt_sent",        {})
    state.setdefault("yt_channel_ids", {})

    ok_count = 0
    yt_ok    = False

    log.info(f"[YOUTUBE] Checking {len(YT_CHANNELS)} channels...")

    for ch in YT_CHANNELS:
        name = ch["name"]

        # Resolve channel ID
        ch_id = ch.get("channel_id", "")
        if not ch_id:
            ch_id = state["yt_channel_ids"].get(ch["handle"], "")
        if not ch_id:
            ch_id = _detect_channel_id(ch["handle"], state)
        if not ch_id:
            log.warning(f"[YOUTUBE] {name}: no channel ID, skipping")
            continue

        # --- Migration: old date-string → new list ---
        raw_seen = state["yt_sent"].get(name, [])
        if isinstance(raw_seen, str):
            # Old format was "YYYY-MM-DD" (date of last sent)
            old_date_str = raw_seen          # e.g. "2026-11-20"
            log.info(f"[YOUTUBE] {name}: migrating old tracking (date={old_date_str})")
            # Fetch RSS and silently mark all videos up to old_date as seen
            all_vids = _fetch_videos(ch_id)
            seen = []
            for v in all_vids:
                if v["pub_ist"] and str(v["pub_ist"]) <= old_date_str:
                    seen.append(v["vid"])    # mark as seen WITHOUT sending
            state["yt_sent"][name] = seen
            log.info(f"[YOUTUBE] {name}: migration done, {len(seen)} videos marked seen")
            # After migration, check if there are truly new videos (after old_date)
            new_after_migration = [v for v in all_vids if v["vid"] not in seen]
            if not new_after_migration:
                log.info(f"[YOUTUBE] {name}: no new videos since migration date")
                continue
            # Process new videos below
            videos = all_vids
        else:
            seen   = list(raw_seen)
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

        sent_count = 0
        for video in new_vids:
            if sent_count >= MAX_PER_RUN:
                break

            log.info(f"[YOUTUBE] {name}: → '{video['title']}'")
            duration_str, desc_snippet = _get_video_meta(video["vid"])
            poster  = YT_THUMB.format(vid=video["vid"])
            caption = _make_caption(ch, video, duration_str, desc_snippet)

            if _send(poster, caption):
                seen.append(video["vid"])
                ok_count  += 1
                yt_ok      = True
                sent_count += 1
                log.info(f"[YOUTUBE] ✅ SENT: {name} → {video['vid']}")
            else:
                log.error(f"[YOUTUBE] ❌ FAILED: {name}")

        # Save updated seen list (keep last MAX_SEEN)
        state["yt_sent"][name] = seen[-MAX_SEEN:]

    return ok_count, yt_ok
