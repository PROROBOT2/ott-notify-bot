"""
hotstar_part.py - JioHotstar Episode Notifications
Strategy:
  1. Show API -> check latestEpisodeBroadcastDate (is today's episode available?)
  2. Hotstar Sitemap -> collect all episode content IDs (cached daily, all 12 shows in ONE scan)
  3. Episode API -> find today's episode from top-30 IDs, get poster + details
  4. Send Telegram notification with episode-specific poster
"""

import os, re, datetime, requests, logging

log = logging.getLogger(__name__)

BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")

HOTSTAR_HDR = {
    'accept': 'application/json',
    'origin': 'https://www.hotstar.com',
    'referer': 'https://www.hotstar.com/',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'x-country-code': 'IN',
    'x-platform-code': 'WEB',
}

# weekday: 0=Mon ... 6=Sun
TIMED_SHOWS = [
    {"name": "Yeh Rishta Kya Kehlata Hai",       "show_id": "1500000715", "slug": "yeh-rishta-kya-kehlata-hai",        "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Bigg Boss",                          "show_id": "1971002586", "slug": "bigg-boss",                         "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Roadies Rebirth",                    "show_id": "1971002274", "slug": "roadies-rebirth",                   "lang": "Hindi", "days": [4,5]},
    {"name": "Yeh Fitoor Tera",                    "show_id": "1271650854", "slug": "yeh-fitoor-tera",                  "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Anupama",                            "show_id": "1260022017", "slug": "anupama",                           "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Oh Humnava Tum Dena Saath Mera",     "show_id": "1271616684", "slug": "oh-humanava-tum-dena-sath-mera",   "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Tuu Juliet Jatt Di",                 "show_id": "1271463159", "slug": "tuu-juliet-jatt-di",               "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Dhamaal with Pati Patni aur Panga",  "show_id": "1271427882", "slug": "dhamaal-with-pati-patni-aur-panga","lang": "Hindi", "days": [5,6]},
    {"name": "Udne Ki Aasha",                      "show_id": "1260161865", "slug": "udne-ki-aasha",                    "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Dr. Aarambhi",                       "show_id": "1271522330", "slug": "dr-aarambhi",                      "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Mannat Har Khushi Paane Ki",         "show_id": "1971311993", "slug": "mannat-har-khushi-paane-ki",       "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Sayoneee",                           "show_id": "1271666144", "slug": "sayoneee",                         "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
]

IMG_BASE  = "https://img10.hotstar.com/image/upload/f_auto,t_web_hs_3x/"
SHOW_BASE = "https://www.hotstar.com/in/shows"


# --- Helpers ---

def _ist_now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))

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


# --- Hotstar API helpers ---

def _get_show_info(show_id):
    """Returns show item dict or None."""
    try:
        r = requests.get(
            f"https://api.hotstar.com/o/v2/content/{show_id}?type=SHOW&tas=5",
            headers=HOTSTAR_HDR, timeout=12)
        if r.status_code == 200:
            return r.json()['body']['results']['item']
    except Exception as e:
        log.warning(f"ShowAPI {show_id}: {e}")
    return None

def _get_episode_info(ep_id):
    """Returns episode item dict or None."""
    try:
        r = requests.get(
            f"https://api.hotstar.com/o/v2/content/{ep_id}?type=EPISODE&tas=5",
            headers=HOTSTAR_HDR, timeout=8)
        if r.status_code == 200:
            return r.json()['body']['results']['item']
    except Exception as e:
        log.warning(f"EpAPI {ep_id}: {e}")
    return None


# --- Sitemap scan ---

def _scan_sitemaps(show_ids):
    """
    Download all episode sitemaps ONCE, extract content IDs for all shows.
    Returns dict: show_id -> sorted list of episode content IDs (desc = newest first).
    """
    results = {sid: set() for sid in show_ids}
    for i in range(1, 10):
        url = f"https://www.hotstar.com/in/new-sitemap-EPISODE-{i}.xml"
        log.info(f"[JIOHOTSTAR] Sitemap {i}: fetching...")
        try:
            r = requests.get(url, headers={"User-Agent": "Googlebot"}, timeout=40)
            if r.status_code == 404:
                log.info(f"[JIOHOTSTAR] Sitemap {i}: 404, done")
                break
            if r.status_code != 200:
                continue
            content = r.text
            for sid in show_ids:
                pat = (r'<loc>https://www\.hotstar\.com/in/shows/[^/]+/'
                       + re.escape(sid) + r'/[^/]+/(\d+)/watch</loc>')
                found = re.findall(pat, content)
                results[sid].update(int(x) for x in found)
            total = sum(len(v) for v in results.values())
            log.info(f"[JIOHOTSTAR] Sitemap {i}: done (total ep IDs so far: {total})")
        except Exception as e:
            log.warning(f"[JIOHOTSTAR] Sitemap {i} error: {e}")
    return {sid: sorted(ids, reverse=True) for sid, ids in results.items()}


# --- Find today's episode ---

def _find_today_episode(ep_ids, target_date):
    """
    Check top-30 episode IDs (largest = most recent) via episode API.
    Returns episode dict if broadCastDate matches target_date (UTC), else None.
    """
    for ep_id in ep_ids[:30]:
        item = _get_episode_info(ep_id)
        if not item:
            continue
        bcast = item.get('broadCastDate', 0)
        if not bcast:
            continue
        ep_date = _utc_date_of(bcast)
        if ep_date == target_date:
            # Pick episode-specific poster (prefer EPISODE_THUMBNAIL over show poster)
            img_sets = item.get('imageSets') or []
            img_uri = ''
            for img_set in img_sets:
                img_type = str(img_set.get('imageType', '')).upper()
                if 'EPISODE' in img_type or 'THUMBNAIL' in img_type:
                    img_uri = img_set.get('publicUri', '')
                    break
            if not img_uri and img_sets:
                img_uri = img_sets[0].get('publicUri', '')
            season  = item.get('season', {})
            return {
                'ep_id':     ep_id,
                'ep_no':     item.get('episodeNo', ''),
                'title':     item.get('title', 'Episode'),
                'bcast':     bcast,
                'poster':    IMG_BASE + img_uri if img_uri else None,
                'season_no': season.get('number', ''),
            }
    return None


# --- Caption ---

def _make_caption(show_name, lang, slug, show_id, ep):
    dt = (datetime.datetime.fromtimestamp(ep['bcast'], tz=datetime.timezone.utc)
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


# --- Main entry point ---

def run(state: dict):
    """Called by bot.py. Returns (ok_count, hotstar_ok_bool)."""
    now       = _ist_now()
    today_str = now.strftime("%Y-%m-%d")
    today_utc = datetime.datetime.now(datetime.timezone.utc).date()
    weekday   = now.weekday()

    # Initialise state buckets
    state.setdefault("hs_sent",  {})
    state.setdefault("hs_cache", {})

    # Count already-sent shows
    ok_count   = sum(1 for s in TIMED_SHOWS
                     if weekday in s["days"]
                     and state["hs_sent"].get(s["name"]) == today_str)
    hotstar_ok = ok_count > 0

    # Which shows still need notification today?
    pending = [s for s in TIMED_SHOWS
               if weekday in s["days"]
               and state["hs_sent"].get(s["name"]) != today_str]

    if not pending:
        log.info("[JIOHOTSTAR] All shows already sent today")
        return ok_count, hotstar_ok

    # Step 1: verify which shows have a new episode today
    shows_today = []
    for show in pending:
        info = _get_show_info(show["show_id"])
        if not info:
            log.warning(f"[JIOHOTSTAR] {show['name']}: show API failed, skipping")
            continue
        leb = info.get('latestEpisodeBroadcastDate', 0)
        if not leb:
            continue
        leb_date = _utc_date_of(leb)
        if leb_date >= today_utc:
            shows_today.append({**show, "target_date": leb_date, "target_bcast": leb})
            log.info(f"[JIOHOTSTAR] {show['name']}: episode today confirmed (bcast={leb})")
        else:
            log.info(f"[JIOHOTSTAR] {show['name']}: no episode today (latest={leb_date})")

    if not shows_today:
        log.info("[JIOHOTSTAR] No new episodes today for pending shows")
        return ok_count, hotstar_ok

    # Step 2: build/refresh sitemap cache (downloaded once per day for ALL shows)
    cache      = state["hs_cache"]
    cache_date = cache.get("_date", "")

    if cache_date != today_str:
        log.info("[JIOHOTSTAR] Scanning all episode sitemaps (takes ~30-60s)...")
        all_ids = _scan_sitemaps([s["show_id"] for s in TIMED_SHOWS])
        state["hs_cache"] = {"_date": today_str, "ep_ids": all_ids}
        cache = state["hs_cache"]
        log.info("[JIOHOTSTAR] Sitemap cache refreshed")

    ep_cache = cache.get("ep_ids", {})

    # Step 3: find & send episode for each show
    retry_needed = False
    for show in shows_today:
        name         = show["name"]
        show_id      = show["show_id"]
        slug         = show["slug"]
        lang         = show["lang"]
        target_date  = show["target_date"]

        ep_ids = ep_cache.get(show_id, [])
        log.info(f"[JIOHOTSTAR] {name}: searching {len(ep_ids)} cached IDs...")

        ep = _find_today_episode(ep_ids, target_date)

        if not ep:
            log.info(f"[JIOHOTSTAR] {name}: today's episode not in sitemap yet, will retry")
            retry_needed = True
            state["hs_cache"]["_date"] = ""  # force rescan next run
            continue

        caption = _make_caption(name, lang, slug, show_id, ep)
        poster  = ep.get('poster')

        log.info(f"[JIOHOTSTAR] {name}: Ep#{ep['ep_no']} '{ep['title']}' | poster={'YES' if poster else 'NO'}")
        sent = _send(poster, caption)

        if sent:
            state["hs_sent"][name] = today_str
            ok_count  += 1
            hotstar_ok = True
            log.info(f"[JIOHOTSTAR] SENT: {name}")
        else:
            log.error(f"[JIOHOTSTAR] FAILED: {name}")

    if retry_needed:
        log.info("[JIOHOTSTAR] Some episodes pending - will retry on next run")

    return ok_count, hotstar_ok
