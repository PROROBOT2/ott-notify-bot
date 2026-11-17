"""
hotstar_part.py  –  JioHotstar Episode Notifications  (v3 – INSTANT)
======================================================================
Strategy:
  • Tracks last-sent EPISODE ID per show (not date)
    → new episode = any ep_id higher than what we last sent
    → notifications come INSTANTLY whenever a new episode is uploaded
  • Show API (fast, 1 call) → check latestEpisodeBroadcastDate
    → if newer than last sent → scan sitemaps → get episode ID → send
  • If Show API blocked → fall back to sitemap scan directly
  • First run (empty state) → sends the LATEST available episode right now
"""

import os, re, datetime, time, logging, requests

log = logging.getLogger(__name__)

# Support both naming conventions for Telegram credentials
BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or os.environ.get("BOT_TOKEN",  "")
CHANNEL_ID = os.environ.get("TG_CHAT_ID")   or os.environ.get("CHANNEL_ID", "")

HOTSTAR_HDR = {
    "accept":          "application/json, text/plain, */*",
    "accept-language": "en-IN,en;q=0.9,hi-IN;q=0.8,hi;q=0.7",
    "origin":          "https://www.hotstar.com",
    "referer":         "https://www.hotstar.com/",
    "user-agent":      ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
    "x-country-code":  "IN",
    "x-platform-code": "WEB",
    "x-app-version":   "6.0.0",
}

IMG_BASE  = "https://img10.hotstar.com/image/upload/f_auto,t_web_hs_3x/"
SHOW_BASE = "https://www.hotstar.com/in/shows"

# weekday: 0=Mon…6=Sun
TIMED_SHOWS = [
    {"name": "Yeh Rishta Kya Kehlata Hai",      "show_id": "1500000715", "slug": "yeh-rishta-kya-kehlata-hai",         "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Bigg Boss",                         "show_id": "1971002586", "slug": "bigg-boss",                          "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Roadies Rebirth",                   "show_id": "1971002274", "slug": "roadies-rebirth",                    "lang": "Hindi", "days": [4,5]},
    {"name": "Yeh Fitoor Tera",                   "show_id": "1271650854", "slug": "yeh-fitoor-tera",                   "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Anupama",                           "show_id": "1260022017", "slug": "anupama",                            "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Oh Humnava Tum Dena Saath Mera",    "show_id": "1271616684", "slug": "oh-humanava-tum-dena-sath-mera",    "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Tuu Juliet Jatt Di",                "show_id": "1271463159", "slug": "tuu-juliet-jatt-di",                "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Dhamaal with Pati Patni aur Panga", "show_id": "1271427882", "slug": "dhamaal-with-pati-patni-aur-panga", "lang": "Hindi", "days": [5,6]},
    {"name": "Udne Ki Aasha",                     "show_id": "1260161865", "slug": "udne-ki-aasha",                     "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Dr. Aarambhi",                      "show_id": "1271522330", "slug": "dr-aarambhi",                       "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Mannat Har Khushi Paane Ki",        "show_id": "1971311993", "slug": "mannat-har-khushi-paane-ki",        "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Sayoneee",                          "show_id": "1271666144", "slug": "sayoneee",                          "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
    {"name": "Rizz Master",                       "show_id": "1271719301", "slug": "rizz-master",                       "lang": "Hindi", "days": [0,1,2,3,4,5,6]},
]

def init(**kwargs):
    """bot.py calls this; we use our own HTTP client so nothing to do."""
    pass

def _ist_now():
    return datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=5, minutes=30)))

def _tg(method, **kw):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            data={"chat_id": CHANNEL_ID, "parse_mode": "HTML", **kw},
            timeout=30)
        if not r.ok:
            log.warning(f"TG {method}: {r.status_code} {r.text[:80]}")
        return r.ok
    except Exception as e:
        log.warning(f"TG {method}: {e}")
        return False

def _send(poster_url, caption):
    if poster_url and _tg("sendPhoto", photo=poster_url, caption=caption):
        return True
    return _tg("sendMessage", text=caption)

def _show_api_latest_bcast(show_id):
    try:
        r = requests.get(
            f"https://api.hotstar.com/o/v2/content/{show_id}?type=SHOW&tas=5",
            headers=HOTSTAR_HDR, timeout=10)
        if r.status_code == 200:
            return r.json()["body"]["results"]["item"].get("latestEpisodeBroadcastDate")
    except Exception as e:
        log.debug(f"ShowAPI {show_id}: {e}")
    return None

def _scan_sitemaps_for_shows(show_ids):
    results = {sid: [] for sid in show_ids}
    bot_hdr = {"User-Agent": "Googlebot/2.1 (+http://www.google.com/bot.html)"}
    for i in range(1, 10):
        url = f"https://www.hotstar.com/in/new-sitemap-EPISODE-{i}.xml"
        log.info(f"[HOTSTAR] Sitemap {i}: fetching …")
        try:
            r = requests.get(url, headers=bot_hdr, timeout=60)
            if r.status_code == 404:
                log.info(f"[HOTSTAR] Sitemap {i}: done (404)")
                break
            if r.status_code != 200:
                log.warning(f"[HOTSTAR] Sitemap {i}: HTTP {r.status_code}")
                continue
            text = r.text
            for sid in show_ids:
                pat = r"/in/shows/[^/]+/" + re.escape(sid) + r"/([^/]+)/(\d+)/watch"
                for m in re.finditer(pat, text):
                    results[sid].append((int(m.group(2)), m.group(1)))
            log.info(f"[HOTSTAR] Sitemap {i}: scanned")
        except Exception as e:
            log.warning(f"[HOTSTAR] Sitemap {i}: {e}")
    for sid in results:
        seen, uniq = set(), []
        for ep_id, slug in sorted(results[sid], reverse=True):
            if ep_id not in seen:
                seen.add(ep_id)
                uniq.append((ep_id, slug))
        results[sid] = uniq
    return results

def _episode_info(ep_id):
    for _ in range(2):
        try:
            r = requests.get(
                f"https://api.hotstar.com/o/v2/content/{ep_id}?type=EPISODE&tas=5",
                headers=HOTSTAR_HDR, timeout=10)
            if r.status_code == 200:
                return r.json()["body"]["results"]["item"]
            if r.status_code in (429, 503):
                time.sleep(3); continue
            break
        except Exception as e:
            log.debug(f"EpAPI {ep_id}: {e}"); break
    return None

def _poster(item):
    for img in item.get("imageSets") or []:
        t = str(img.get("imageType", "")).upper()
        u = img.get("publicUri", "")
        if u and ("EPISODE" in t or "THUMBNAIL" in t):
            return IMG_BASE + u
    for img in item.get("imageSets") or []:
        u = img.get("publicUri", "")
        if u: return IMG_BASE + u
    return None

def _ep_num_from_slug(slug):
    m = re.search(r"(?:ep(?:isode)?[-_\s]*)(\d{1,4})", slug, re.I)
    return m.group(1).zfill(3) if m else ""

def _caption_rich(show, ep_data):
    bcast = ep_data.get("bcast", 0)
    if bcast:
        dt = (datetime.datetime.fromtimestamp(bcast, tz=datetime.timezone.utc)
              + datetime.timedelta(hours=5, minutes=30))
        date_str = dt.strftime("%d %B %Y")
    else:
        date_str = _ist_now().strftime("%d %B %Y")
    sn  = str(ep_data.get("season_no") or "01").zfill(2)
    en  = str(ep_data.get("ep_no") or "").zfill(3)
    url = f"{SHOW_BASE}/{show['slug']}/{show['show_id']}/watch"
    return (
        "#NewEpisode #DMT #JIOHOTSTAR #Notification\n\n"
        f"Show Name: {show['name']}\n"
        f"Season Number: {sn}\n"
        + (f"Episode Number: {en}\n" if en else "")
        + f"Episode Title: {ep_data['title']}\n"
        f"Release Date: {date_str}\n"
        f"Language: {show['lang']}\n\n"
        f"Show Link:\n<code>{url}</code>"
    )

def _caption_fallback(show, ep_slug):
    date_str = _ist_now().strftime("%d %B %Y")
    en  = _ep_num_from_slug(ep_slug)
    url = f"{SHOW_BASE}/{show['slug']}/{show['show_id']}/watch"
    return (
        "#NewEpisode #DMT #JIOHOTSTAR #Notification\n\n"
        f"Show Name: {show['name']}\n"
        + (f"Episode Number: {en}\n" if en else "")
        + f"Release Date: {date_str}\n"
        f"Language: {show['lang']}\n\n"
        f"Show Link:\n<code>{url}</code>"
    )

def run(state: dict):
    """Called by bot.py every run. Returns (ok_count, hotstar_ok)."""
    now     = _ist_now()
    weekday = now.weekday()
    state.setdefault("hs_last_ep", {})
    state.setdefault("hs_cache",   {})
    ok_count   = 0
    hotstar_ok = False
    todays_shows = [s for s in TIMED_SHOWS if weekday in s["days"]]
    if not todays_shows:
        log.info("[HOTSTAR] No shows scheduled today")
        return 0, False
    need_sitemap = []
    skip_api     = []
    for show in todays_shows:
        name     = show["name"]
        show_id  = show["show_id"]
        last     = state["hs_last_ep"].get(name, {})
        last_ep  = last.get("ep_id", 0)
        bcast = _show_api_latest_bcast(show_id)
        if bcast is None:
            log.info(f"[HOTSTAR] {name}: Show API unavailable → sitemap fallback")
            skip_api.append(show)
        else:
            last_bcast = last.get("bcast", 0)
            if bcast > last_bcast:
                log.info(f"[HOTSTAR] {name}: new episode detected (bcast {bcast} > {last_bcast})")
                need_sitemap.append(show)
            else:
                log.info(f"[HOTSTAR] {name}: no new episode (bcast unchanged)")
    to_scan = need_sitemap + skip_api
    if not to_scan:
        log.info("[HOTSTAR] All shows up to date")
        return ok_count, hotstar_ok
    scan_ids   = [s["show_id"] for s in to_scan]
    cache      = state["hs_cache"]
    cache_time = cache.get("_ts", 0)
    now_ts     = now.timestamp()
    if (now_ts - cache_time) > 1200 or not all(sid in cache for sid in scan_ids):
        log.info(f"[HOTSTAR] Scanning sitemaps for {len(scan_ids)} shows …")
        ep_map = _scan_sitemaps_for_shows(scan_ids)
        cache.update(ep_map)
        cache["_ts"] = now_ts
        state["hs_cache"] = cache
        log.info("[HOTSTAR] Sitemap scan complete")
    else:
        ep_map = cache
        log.info("[HOTSTAR] Using cached sitemap data")
    for show in to_scan:
        name    = show["name"]
        show_id = show["show_id"]
        last    = state["hs_last_ep"].get(name, {})
        last_ep = last.get("ep_id", 0)
        pairs = ep_map.get(show_id, [])
        if not pairs:
            log.info(f"[HOTSTAR] {name}: not in sitemap yet, will retry next run")
            continue
        to_send = [(eid, eslug) for eid, eslug in pairs if eid > last_ep]
        if not to_send:
            log.info(f"[HOTSTAR] {name}: sitemap episode ID not newer than last sent")
            cache["_ts"] = 0
            continue
        ep_id, ep_slug = to_send[0]
        log.info(f"[HOTSTAR] {name}: ep_id={ep_id} (slug={ep_slug})")
        item = _episode_info(ep_id)
        if item:
            season   = item.get("season") or {}
            ep_bcast = item.get("broadCastDate", 0)
            ep_data  = {
                "ep_no":    item.get("episodeNo", ""),
                "title":    item.get("title", "Episode"),
                "bcast":    ep_bcast,
                "season_no": season.get("number", ""),
            }
            poster  = _poster(item)
            caption = _caption_rich(show, ep_data)
            log.info(f"[HOTSTAR] {name}: Ep#{ep_data['ep_no']} '{ep_data['title']}' | "
                     f"poster={'YES' if poster else 'NO'}")
        else:
            log.warning(f"[HOTSTAR] {name}: Episode API blocked → sending fallback")
            poster  = None
            ep_bcast = 0
            caption = _caption_fallback(show, ep_slug)
        sent = _send(poster, caption)
        if sent:
            state["hs_last_ep"][name] = {"ep_id": ep_id, "bcast": ep_bcast}
            ok_count  += 1
            hotstar_ok = True
            log.info(f"[HOTSTAR] ✅ SENT: {name}")
        else:
            log.error(f"[HOTSTAR] ❌ FAILED to send: {name}")
    return ok_count, hotstar_ok
