"""
sunnxt_part.py - SunNXT (Sun Neo Hindi) Episode Notifications
Strategy:
  1. Auto-detect Sun Neo YouTube channel ID (cached in state)
  2. Download YouTube RSS feed (no API key needed, free)
  3. Find today's FULL EPISODE video for each show by keyword matching
  4. Use YouTube maxresdefault thumbnail as episode poster
  5. Send Telegram notification
"""

import os, re, datetime, requests, logging
import xml.etree.ElementTree as ET

log = logging.getLogger(__name__)

BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or os.environ.get("BOT_TOKEN",  "")
CHANNEL_ID = os.environ.get("TG_CHAT_ID")   or os.environ.get("CHANNEL_ID", "")

YT_HANDLE   = "https://www.youtube.com/@sunneo"
YT_RSS_BASE = "https://www.youtube.com/feeds/videos.xml?channel_id="
YT_THUMB    = "https://i.ytimg.com/vi/{vid}/maxresdefault.jpg"
SHOW_LINK   = "https://www.sunnxt.com"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# weekday: 0=Mon ... 6=Sun
# keywords: title must contain ALL of at least one keyword group (case-insensitive)
# ep_keywords: at least one must appear to confirm it's a full episode (not promo/teaser)
SHOWS = [
    {
        "name":        "Divya Prem",
        "keywords":    ["divya prem"],
        "ep_words":    ["full episode", "episode", " ep "],
        "lang":        "Hindi",
        "days":        [0,1,2,3,4,5,6],
        "show_url":    "https://www.sunnxt.com/hindi/shows/divya-prem",
    },
    {
        "name":        "Nandini Ka Pratishodh",
        "keywords":    ["nandini ka pratishodh", "nandini pratishodh"],
        "ep_words":    ["full episode", "episode", " ep "],
        "lang":        "Hindi",
        "days":        [0,1,2,3,4,5,6],
        "show_url":    "https://www.sunnxt.com/hindi/shows/nandini-ka-pratishodh",
    },
    {
        "name":        "Saajha Sindoor",
        "keywords":    ["saajha sindoor"],
        "ep_words":    ["full episode", "episode", " ep "],
        "lang":        "Hindi",
        "days":        [0,1,2,3,4,5,6],
        "show_url":    "https://www.sunnxt.com/hindi/shows/saajha-sindoor",
    },
    {
        "name":        "Ishq Jabaria",
        "keywords":    ["ishq jabaria"],
        "ep_words":    ["full episode", "episode", " ep "],
        "lang":        "Hindi",
        "days":        [0,1,2,3,4,5,6],
        "show_url":    "https://www.sunnxt.com/hindi/shows/ishq-jabaria",
    },
    {
        "name":        "Chatthi Maiyya Ki Bitiya",
        "keywords":    ["chatthi maiyya", "chhathi maiyya"],
        "ep_words":    ["full episode", "episode", " ep "],
        "lang":        "Hindi",
        "days":        [0,1,2,3,4,5,6],
        "show_url":    "https://www.sunnxt.com/hindi/shows/chatthi-maiyya-ki-bitiya",
    },
    {
        "name":        "Lakshmi Mere Aangan Ki",
        "keywords":    ["lakshmi mere aangan"],
        "ep_words":    ["full episode", "episode", " ep "],
        "lang":        "Hindi",
        "days":        [0,1,2,3,4,5,6],
        "show_url":    "https://www.sunnxt.com/hindi/shows/lakshmi-mere-aangan-ki",
    },
]


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
    return _tg("sendMessage", text=caption)


# ---------------------------------------------------------------------------
# YouTube channel ID discovery (auto, cached in state)
# ---------------------------------------------------------------------------

def _get_yt_channel_id(state):
    """Fetch Sun Neo YouTube channel ID and cache in state."""
    cached = state.get("sunnxt_yt_channel_id", "")
    if cached:
        return cached

    log.info("[SUNNXT] Fetching Sun Neo YouTube channel ID...")
    try:
        r = requests.get(
            YT_HANDLE,
            headers={"User-Agent": BROWSER_UA},
            timeout=15)
        m = re.search(r'"channelId"\s*:\s*"(UC[A-Za-z0-9_-]{20,})"', r.text)
        if m:
            ch_id = m.group(1)
            state["sunnxt_yt_channel_id"] = ch_id
            log.info(f"[SUNNXT] Channel ID found: {ch_id}")
            return ch_id
    except Exception as e:
        log.warning(f"[SUNNXT] Channel ID fetch error: {e}")
    return None


# ---------------------------------------------------------------------------
# YouTube RSS feed → today's videos
# ---------------------------------------------------------------------------

def _fetch_yt_videos(channel_id):
    """
    Fetch YouTube RSS feed for channel.
    Returns list of dicts: {title, video_id, published_ist (date)}
    """
    url = YT_RSS_BASE + channel_id
    try:
        r = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=20)
        if r.status_code != 200:
            log.warning(f"[SUNNXT] YouTube RSS HTTP {r.status_code}")
            return []

        # Parse XML - handle namespaces
        ns = {
            'atom': 'http://www.w3.org/2005/Atom',
            'yt':   'http://www.youtube.com/xml/schemas/2015',
        }
        root = ET.fromstring(r.text)
        videos = []
        for entry in root.findall('atom:entry', ns):
            vid_id = entry.findtext('yt:videoId', namespaces=ns) or ''
            title  = entry.findtext('atom:title', namespaces=ns) or ''
            pub    = entry.findtext('atom:published', namespaces=ns) or ''
            if not vid_id:
                continue
            # Parse publish date to IST date
            try:
                pub_dt = datetime.datetime.fromisoformat(pub.replace('Z', '+00:00'))
                pub_ist = (pub_dt + datetime.timedelta(hours=5, minutes=30)).date()
            except Exception:
                pub_ist = None
            videos.append({
                'title':    title,
                'video_id': vid_id,
                'pub_ist':  pub_ist,
            })
        log.info(f"[SUNNXT] YouTube RSS: {len(videos)} recent videos fetched")
        return videos
    except Exception as e:
        log.warning(f"[SUNNXT] YouTube RSS error: {e}")
        return []


# ---------------------------------------------------------------------------
# Match show to today's episode video
# ---------------------------------------------------------------------------

def _match_episode(show, videos, today_ist):
    """
    Find a video published today that matches this show's keywords
    and contains an episode-indicator word (not just promo/teaser).
    Returns matched video dict or None.
    """
    kw_groups  = show["keywords"]
    ep_words   = show["ep_words"]

    for video in videos:
        if video["pub_ist"] != today_ist:
            continue
        title_lo = video["title"].lower()

        # Must match at least one keyword group
        kw_match = any(kw.lower() in title_lo for kw in kw_groups)
        if not kw_match:
            continue

        # Must have an episode indicator
        ep_match = any(ew.lower() in title_lo for ew in ep_words)
        if not ep_match:
            continue

        return video
    return None


# ---------------------------------------------------------------------------
# Caption
# ---------------------------------------------------------------------------

def _make_caption(show, video, today_ist):
    date_str = today_ist.strftime("%d %B %Y")
    url      = show.get("show_url", SHOW_LINK)
    title    = video.get("title", "")
    # Try to extract episode number from title e.g. "Ep 45" or "Episode 45"
    ep_no = ""
    m = re.search(r'[Ee]p(?:isode)?\s*[#\-]?\s*(\d+)', title)
    if m:
        ep_no = m.group(1).zfill(3)

    return (
        "#NewEpisode #DMT #SUNNXT #Notification\n\n"
        f"Show Name: {show['name']}\n"
        + (f"Episode Number: {ep_no}\n" if ep_no else "")
        + f"Episode Title: {title}\n"
        f"Release Date: {date_str}\n"
        f"Language: {show['lang']}\n\n"
        f"Show Link:\n<code>{url}</code>"
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(state: dict):
    """Called by bot.py. Returns (ok_count, sunnxt_ok_bool)."""
    now       = _ist_now()
    today_str = now.strftime("%Y-%m-%d")
    today_ist = now.date()
    weekday   = now.weekday()

    state.setdefault("sunnxt_sent", {})

    ok_count  = sum(1 for s in SHOWS
                    if weekday in s["days"]
                    and state["sunnxt_sent"].get(s["name"]) == today_str)
    sunnxt_ok = ok_count > 0

    pending = [s for s in SHOWS
               if weekday in s["days"]
               and state["sunnxt_sent"].get(s["name"]) != today_str]

    if not pending:
        log.info("[SUNNXT] All today's shows already sent")
        return ok_count, sunnxt_ok

    log.info(f"[SUNNXT] {len(pending)} shows pending: {[s['name'] for s in pending]}")

    # Get YouTube channel ID
    ch_id = _get_yt_channel_id(state)
    if not ch_id:
        log.error("[SUNNXT] Could not get YouTube channel ID, skipping")
        return ok_count, sunnxt_ok

    # Fetch today's videos from RSS
    videos = _fetch_yt_videos(ch_id)
    if not videos:
        log.info("[SUNNXT] No videos from RSS, will retry next run")
        return ok_count, sunnxt_ok

    # Match and send each pending show
    for show in pending:
        name = show["name"]
        vid  = _match_episode(show, videos, today_ist)

        if not vid:
            log.info(f"[SUNNXT] {name}: today's episode not on YouTube yet, will retry")
            continue

        poster  = YT_THUMB.format(vid=vid["video_id"])
        caption = _make_caption(show, vid, today_ist)

        log.info(f"[SUNNXT] {name}: found '{vid['title']}'")
        sent = _send(poster, caption)

        if sent:
            state["sunnxt_sent"][name] = today_str
            ok_count  += 1
            sunnxt_ok = True
            log.info(f"[SUNNXT] SENT: {name}")
        else:
            log.error(f"[SUNNXT] FAILED: {name}")

    return ok_count, sunnxt_ok
