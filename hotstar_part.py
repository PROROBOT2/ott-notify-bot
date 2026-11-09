"""
hotstar_part.py - JioHotstar Episode Notifications
Strategy:
  1. Download episode sitemaps once per day (Googlebot UA - not blocked)
  2. Filter URL blocks by <lastmod> == today -> these are today's new episodes
  3. Call Episode API only for confirmed today episodes (minimal API calls)
  4. Send Telegram notification with episode-specific poster
  NO Show API needed - avoids rate limiting / blocking
"""

import os, re, datetime, time, requests, logging

log = logging.getLogger(__name__)

BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")

# Headers for Hotstar Episode API
HOTSTAR_HDR = {
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'en-IN,en;q=0.9,hi-IN;q=0.8,hi;q=0.7',
    'origin': 'https://www.hotstar.com',
    'referer': 'https://www.hotstar.com/',
    'user-agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'x-country-code': 'IN',
    'x-platform-code': 'WEB',
    'x-app-version': '6.0.0',
}

# weekday: 0=Mon ... 6=Sun
TIMED_SHOWS = [
    {"name": "Yeh Rishta Kya Kehlata Hai",       "show_id": "1500000715", "slug": "yeh-rishta-kya-kehlata-hai",         "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Bigg Boss",                          "show_id": "1971002586", "slug": "bigg-boss",                          "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Roadies Rebirth",                    "show_id": "1971002274", "slug": "roadies-rebirth",                    "lang": "Hindi", "days": [4,5]},
    {"name": "Yeh Fitoor Tera",                    "show_id": "1271650854", "slug": "yeh-fitoor-tera",                   "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Anupama",                            "show_id": "1260022017", "slug": "anupama",                            "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Oh Humnava Tum Dena Saath Mera",     "show_id": "1271616684", "slug": "oh-humanava-tum-dena-sath-mera",    "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Tuu Juliet Jatt Di",                 "show_id": "1271463159", "slug": "tuu-juliet-jatt-di",                "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Dhamaal with Pati Patni aur Panga",  "show_id": "1271427882", "slug": "dhamaal-with-pati-patni-aur-panga", "lang": "Hindi", "days": [5,6]},
    {"name": "Udne Ki Aasha",                      "show_id": "1260161865", "slug": "udne-ki-aasha",                     "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Dr. Aarambhi",                       "show_id": "1271522330", "slug": "dr-aarambhi",                       "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Mannat Har Khushi Paane Ki",         "show_id": "1971311993", "slug": "mannat-har-khushi-paane-ki",        "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Sayoneee",                           "show_id": "1271666144", "slug": "sayoneee",                          "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Rizz Master",                        "show_id": "1271719301", "slug": "rizz-master",                        "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
]

IMG_BASE  = "https://img10.hotstar.com/image/upload/f_auto,t_web_hs_3x/"
SHOW_BASE = "https://www.hotstar.com/in/shows"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ist_now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))

def _today_ist_str():
    return _ist_now().strftime("%Y-%m-%d")

def _utc_date_of(ts):
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).date()

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
# Sitemap scan — filter by lastmod date (no Show API needed)
# ---------------------------------------------------------------------------

def _scan_sitemaps_today(show_ids, today_str):
    """
    Download episode sitemaps. For each <url> block with <lastmod>==today_str,
    extract content IDs for shows in show_ids.

    Returns dict: show_id -> list of int content IDs (newest first).
    """
    results = {sid: [] for sid in show_ids}
    found_any = False

    sitemap_hdr = {"User-Agent": "Googlebot/2.1 (+http://www.google.com/bot.html)"}

    for i in range(1, 10):
        url = f"https://www.hotstar.com/in/new-sitemap-EPISODE-{i}.xml"
        log.info(f"[JIOHOTSTAR] Sitemap {i}: fetching...")
        try:
            r = requests.get(url, headers=sitemap_hdr, timeout=60)
            if r.status_code == 404:
                log.info(f"[JIOHOTSTAR] Sitemap {i}: 404, done scanning")
                break
            if r.status_code != 200:
                log.warning(f"[JIOHOTSTAR] Sitemap {i}: HTTP {r.status_code}, skipping")
                continue

            today_count = 0
            # Split into individual <url>...</url> blocks
            blocks = r.text.split('<url>')
            for block in blocks:
                # Only process blocks with today's lastmod
                if f'<lastmod>{today_str}</lastmod>' not in block:
                    continue
                # Check which show this URL belongs to
                for sid in show_ids:
                    pat = r'/in/shows/[^/]+/' + re.escape(sid) + r'/[^/]+/(\d+)/watch'
                    m = re.search(pat, block)
                    if m:
                        ep_id = int(m.group(1))
                        results[sid].append(ep_id)
                        today_count += 1
                        found_any = True

            log.info(f"[JIOHOTSTAR] Sitemap {i}: {today_count} today-episodes found")

        except Exception as e:
            log.warning(f"[JIOHOTSTAR] Sitemap {i} error: {e}")

    # Sort descending (newest IDs first)
    for sid in results:
        results[sid] = sorted(set(results[sid]), reverse=True)

    if not found_any:
        log.info(f"[JIOHOTSTAR] No today-episodes in sitemaps yet (lastmod={today_str})")

    return results, found_any


# ---------------------------------------------------------------------------
# Episode API — get details for a single episode
# ---------------------------------------------------------------------------

def _get_episode_info(ep_id):
    """Fetch episode details from Hotstar API. Returns item dict or None."""
    for attempt in range(2):
        try:
            r = requests.get(
                f"https://api.hotstar.com/o/v2/content/{ep_id}?type=EPISODE&tas=5",
                headers=HOTSTAR_HDR, timeout=10)
            if r.status_code == 200:
                return r.json()['body']['results']['item']
            elif r.status_code in (429, 503):
                log.warning(f"[JIOHOTSTAR] EpAPI {ep_id}: HTTP {r.status_code}, retrying...")
                time.sleep(3)
            else:
                log.warning(f"[JIOHOTSTAR] EpAPI {ep_id}: HTTP {r.status_code}")
                break
        except Exception as e:
            log.warning(f"[JIOHOTSTAR] EpAPI {ep_id}: {e}")
            break
    return None


def _pick_poster(item):
    """Pick episode-specific poster from imageSets (not show-level thumbnail)."""
    img_sets = item.get('imageSets') or []
    # Prefer imageType containing EPISODE or THUMBNAIL
    for img_set in img_sets:
        img_type = str(img_set.get('imageType', '')).upper()
        uri = img_set.get('publicUri', '')
        if uri and ('EPISODE' in img_type or 'THUMBNAIL' in img_type):
            return IMG_BASE + uri
    # Fallback: first available
    for img_set in img_sets:
        uri = img_set.get('publicUri', '')
        if uri:
            return IMG_BASE + uri
    return None


# ---------------------------------------------------------------------------
# Caption builder
# ---------------------------------------------------------------------------

def _make_caption(show_name, lang, slug, show_id, ep):
    bcast     = ep.get('bcast', 0)
    dt        = (datetime.datetime.fromtimestamp(bcast, tz=datetime.timezone.utc)
                 + datetime.timedelta(hours=5, minutes=30))
    date_str  = dt.strftime("%d %B %Y")
    season_no = str(ep.get('season_no') or '01').zfill(2)
    ep_no     = str(ep.get('ep_no') or '').zfill(3)
    url       = f"{SHOW_BASE}/{slug}/{show_id}/watch"
    return (
        "#NewEpisode #DMT #JIOHOTSTAR #Notification\n\n"
        f"Show Name: {show_name}\n"
        f"Season Number: {season_no}\n"
        f"Episode Number: {ep_no}\n"
        f"Episode Title: {ep['title']}\n"
        f"Release Date: {date_str}\n"
        f"Language: {lang}\n\n"
        f"Show Link:\n<code>{url}</code>"
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(state: dict):
    """Called by bot.py. Returns (ok_count, hotstar_ok_bool)."""
    now       = _ist_now()
    today_str = now.strftime("%Y-%m-%d")
    weekday   = now.weekday()

    # Initialise state buckets
    state.setdefault("hs_sent",  {})
    state.setdefault("hs_cache", {})

    # Count already-sent shows today
    ok_count   = sum(1 for s in TIMED_SHOWS
                     if weekday in s["days"]
                     and state["hs_sent"].get(s["name"]) == today_str)
    hotstar_ok = ok_count > 0

    # Which shows still need sending today?
    pending = [s for s in TIMED_SHOWS
               if weekday in s["days"]
               and state["hs_sent"].get(s["name"]) != today_str]

    if not pending:
        log.info("[JIOHOTSTAR] All today's shows already sent")
        return ok_count, hotstar_ok

    log.info(f"[JIOHOTSTAR] {len(pending)} shows pending: {[s['name'] for s in pending]}")

    # --- Sitemap cache check ---
    cache      = state["hs_cache"]
    cache_date = cache.get("_date", "")

    if cache_date != today_str:
        # Download sitemaps fresh for today
        all_show_ids = [s["show_id"] for s in TIMED_SHOWS]
        ep_map, found_any = _scan_sitemaps_today(all_show_ids, today_str)

        if found_any:
            state["hs_cache"] = {"_date": today_str, "ep_ids": ep_map}
            log.info("[JIOHOTSTAR] Sitemap cache updated for today")
        else:
            # Episodes not in sitemap yet — keep cache empty, retry next run
            log.info("[JIOHOTSTAR] Sitemap has no today-episodes yet, will retry next run")
            state["hs_cache"] = {"_date": "", "ep_ids": {}}
            return ok_count, hotstar_ok
    else:
        ep_map = cache.get("ep_ids", {})
        log.info("[JIOHOTSTAR] Using cached sitemap data")

    # --- Process each pending show ---
    retry_needed = False

    for show in pending:
        name    = show["name"]
        show_id = show["show_id"]
        slug    = show["slug"]
        lang    = show["lang"]

        ep_ids = ep_map.get(show_id, [])

        if not ep_ids:
            log.info(f"[JIOHOTSTAR] {name}: no today-episode in sitemap yet, will retry")
            retry_needed = True
            continue

        log.info(f"[JIOHOTSTAR] {name}: {len(ep_ids)} today-episode ID(s) found in sitemap")

        sent_this_show = False
        for ep_id in ep_ids[:5]:  # try up to 5 IDs
            item = _get_episode_info(ep_id)
            if not item:
                continue

            poster  = _pick_poster(item)
            season  = item.get('season', {})
            bcast   = item.get('broadCastDate', 0)

            ep = {
                'ep_id':     ep_id,
                'ep_no':     item.get('episodeNo', ''),
                'title':     item.get('title', 'Episode'),
                'bcast':     bcast,
                'season_no': season.get('number', ''),
            }

            caption = _make_caption(name, lang, slug, show_id, ep)
            log.info(f"[JIOHOTSTAR] {name}: Ep#{ep['ep_no']} '{ep['title']}' | poster={'YES' if poster else 'NO'}")

            sent = _send(poster, caption)
            if sent:
                state["hs_sent"][name] = today_str
                ok_count  += 1
                hotstar_ok = True
                sent_this_show = True
                log.info(f"[JIOHOTSTAR] SENT: {name}")
                break
            else:
                log.error(f"[JIOHOTSTAR] FAILED to send: {name}")

        if not sent_this_show and not ep_ids:
            retry_needed = True

    if retry_needed:
        # Force re-scan on next run
        state["hs_cache"]["_date"] = ""
        log.info("[JIOHOTSTAR] Some shows pending — cache reset for retry next run")

    return ok_count, hotstar_ok
