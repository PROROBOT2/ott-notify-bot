"""SonyLIV + ZEE5 + Dangal Play serials -> Telegram notifier (multi-show)."""
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse

import requests

try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

# Dangal Play ka response AES se encrypted aata hai. 'cryptography' install na ho toh
# sirf Dangal wala hissa skip hoga, baaki bot (SonyLIV/ZEE5) bilkul normal chalega.
try:
    from cryptography.hazmat.primitives import padding as _padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

# ---------------------------------------------------------------
# SONYLIV SHOWS: (naam, show_id, link wala slug)
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
    {"name": "Family Full House with Rohit Sharma", "id": 1790007851,
     "slug": "family-full-house-with-rohit-sharma"},
    {"name": "Indian Game Show", "id": 1790007836,
     "slug": "indian-game-show"},
    {"name": "Kaun Banega Crorepati", "id": 1700000195,
     "slug": "kaun-banega-crorepati"},
    {"name": "Pushpa Impossible", "id": 1700000921,
     "slug": "pushpa-impossible"},
]

# ---------------------------------------------------------------
# ZEE5 SHOWS: show page ka link hota hai
# https://www.zee5.com/tv-shows/details/<slug>/<id>
# ---------------------------------------------------------------
ZEE5_SHOWS = [
    {"name": "Tumm Se Tumm Tak", "id": "0-6-4z5727104", "slug": "tumm-se-tumm-tak"},
    {"name": "Humari Radha", "id": "0-6-4z5990458", "slug": "humari-radha"},
    {"name": "Tu Hi Re Dil Mein", "id": "0-6-4z5906051", "slug": "tu-hi-re-dil-mein"},
    {"name": "Lakshmi Nivas", "id": "0-6-4z5891598", "slug": "lakshmi-nivas"},
    {"name": "Ganga Mai Ki Betiyan", "id": "0-6-4z5793364", "slug": "ganga-mai-ki-betiyan"},
    {"name": "Vasudha", "id": "0-6-4z5612471", "slug": "vasudha"},
    {"name": "Sach Subhash Chandra Show", "id": "0-6-4z5865856", "slug": "sach-subhash-chandra-show"},
    {"name": "Dilo Ki Ram Leela", "id": "0-6-4z51028474", "slug": "dilo-ki-ram-leela"},
    {"name": "Jagadhatri", "id": "0-6-4z5853175", "slug": "jagadhatri"},
]
ZEE5_PAGE = "https://www.zee5.com/tv-shows/details/{slug}/{id}"

# ---------------------------------------------------------------
# DANGAL PLAY SHOWS: neeche list me link ya series_id do
# ---------------------------------------------------------------
DANGAL_SHOWS = [
    {"name": "Kaisi Teri Dillagi", "series_id": 1739},
    {"name": "Ishq Junooni", "link": "https://www.dangalplay.com/shows/ishq-junooni/ep-31-60/ishq-junooni-ep-number-31"},
    {"name": "Sohni", "link": "https://www.dangalplay.com/home/sohni-ep-01-digital"},
    {"name": "Rimjhim", "link": "https://www.dangalplay.com/shows/rimjhim/ep-1-30/rimjhim-ep-number-01"},
    {"name": "Mann Atisundar", "link": "https://www.dangalplay.com/shows/mann-atisundar/ep-1-30/mann-atisundar-ep-number-01-1"},
    {"name": "Pati Bhramachari", "link": "https://www.dangalplay.com/shows/pati-bhramachari/ep-1-30-1/pati-bhramachari-ep-number-01"},
    {"name": "Hamari Bhabhi Number 1", "link": "https://www.dangalplay.com/shows/hamari-bhabhi-number-1/ep-1-30/hamari-bhabhi-number-1-ep-number-01"},
    {"name": "Mann Sundar", "link": "https://www.dangalplay.com/shows/mann-sundar-ep-number-01"},
]
# Naya show: ya toh {"name": "...", "link": "<us show ke kisi bhi episode ka link>"}
# (bot link se series_id khud nikaal leta hai), ya {"name": "...", "series_id": NUMBER}.

# ---------------------------------------------------------------
# TIMED SHOWS (JioHotstar jaise OTT jinka data GitHub se nahi khulta):
# Alert time pe jata hai. tvmaze_id ho toh episode ka number, title aur poster TVmaze se aata hai.
# air_time = TV ka time (24h, IST), delay_min = alert TV time ke itni der baad bhejna
# (JioHotstar pe episode thodi der baad aata hai), air_days: 0=Mon ... 6=Sun
# poster = apni image ka raw GitHub link (khali chhodo toh bina poster ke jayega)
# ---------------------------------------------------------------
TIMED_SHOWS = [
    {"name": "Yeh Rishta Kya Kehlata Hai", "platform": "JIOHOTSTAR", "tvmaze_id": 55393,
     "backup_for_hotstar": True,   # sirf tab chalta hai jab neeche wala HOTSTAR sitemap scan fail ho
     "air_time": "21:30", "delay_min": 45, "air_days": [0, 1, 2, 3, 4, 5, 6],
     "link": "https://www.hotstar.com/in/shows/yeh-rishta/586", "poster": ""},
]
# tvmaze_id: TVmaze (free public API) se episode ka number, title aur poster aata hai.
# Naya show: tvmaze.com pe show kholo, URL me tvmaze.com/shows/<NUMBER>/... wahi number daalo.

# ---------------------------------------------------------------
# JIOHOTSTAR SHOWS: id = show ke link me ka number
# https://www.hotstar.com/in/shows/<naam>/<ID>
# Naye episode Hotstar ke public sitemap se pakde jate hain (episode ka asli link milta hai).
# tvmaze_id (optional): poster / episode number ka backup source
# first_alert: True ho toh pehli run me bhi latest episode ka alert jata hai (test ke liye)
# ---------------------------------------------------------------
HOTSTAR_SHOWS = [
    {"name": "Yeh Rishta Kya Kehlata Hai", "id": "586", "tvmaze_id": 55393, "first_alert": True},
    {"name": "Bigg Boss", "id": "1971002586"},
    {"name": "Roadies Rebirth", "id": "1971002274"},
    {"name": "Yeh Fitoor Tera", "id": "1271650854"},
    {"name": "Anupama", "id": "1260022017"},
    {"name": "Oh Humnava Tum Dena Saath Mera", "id": "1271616684"},
    {"name": "Tuu Juliet Jatt Di", "id": "1271463159"},
    {"name": "Dhamaal with Pati Patni aur Panga", "id": "1271427882"},
    {"name": "Udne Ki Aasha", "id": "1271270408"},
    {"name": "Dr. Aarambhi", "id": "1271522330"},
    {"name": "Mannat Har Khushi Paane Ki", "id": "1971311993"},
    {"name": "Sayoneee", "id": "1271666144"},
]
HOTSTAR_SCAN_EVERY_MIN = 15       # sitemap bade hote hain, isliye 15 min me ek baar scan
HOTSTAR_SILENT_FIRST_RUN = True   # True: pehli run me purane episodes ka alert nahi (sirf first_alert wale ka)

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
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.sonyliv.com",
    "Referer": "https://www.sonyliv.com/",
}
ZEE5_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
    "Referer": "https://www.google.com/",
    "Upgrade-Insecure-Requests": "1",
}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# =================================================================
# SONYLIV
# =================================================================
def fetch_episodes(show):
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
    if not eps:
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
    title = re.sub(r"['\u2019`]", "", m.get("episodeTitle", "").lower())
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


def get_image(url):
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


def get_poster(m):
    emf = m.get("emfAttributes", {})
    return get_image(emf.get("landscape_thumb") or emf.get("thumbnail"))


def send_tg(caption, poster):
    base = f"https://api.telegram.org/bot{TOKEN}"
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


def send_telegram(show, m):
    link = episode_link(show, m)
    caption = build_caption(show, m, link)
    return send_tg(caption, get_poster(m))


# =================================================================
# ZEE5 (Bypass Akamai WAF using Chrome Impersonation)
# =================================================================
def fetch_zee5_page_html(url):
    """Akamai WAF ko bypass karke ZEE5 page lata hai."""
    if HAS_CURL_CFFI:
        try:
            r = cffi_requests.get(
                url,
                headers=ZEE5_HEADERS,
                impersonate="chrome120",
                timeout=30,
            )
            if r.status_code == 200:
                return r.text
        except Exception as e:
            print(f"  [curl_cffi error]: {e}")

    r = requests.get(url, headers=ZEE5_HEADERS, timeout=30)
    if r.status_code == 200:
        return r.text
    return ""


def _zee5_season(it):
    ps = it.get("partOfSeason")
    if isinstance(ps, dict) and ps.get("seasonNumber") not in (None, ""):
        return ps["seasonNumber"]
    return it.get("seasonNumber")


def fetch_zee5_episodes(show):
    """Show ke episodes extract karta hai."""
    url = ZEE5_PAGE.format(**show)
    html_text = fetch_zee5_page_html(url)
    if not html_text:
        print("  ZEE5 HTML nahi mil saka (Akamai block ya timeout)")
        return []

    blocks = re.findall(
        r'<script[^>]*type=[\'"]application/ld\+json[\'"][^>]*>(.*?)</script>',
        html_text, re.S,
    )
    eps, seen_ids = [], set()
    for b in blocks:
        try:
            data = json.loads(b)
        except json.JSONDecodeError:
            continue
        items_to_check = data if isinstance(data, list) else [data]
        for d in items_to_check:
            if not isinstance(d, dict):
                continue
            for it in d.get("itemListElement") or []:
                if isinstance(it, dict) and isinstance(it.get("item"), dict):
                    it = it["item"]
                link = (it or {}).get("url") or ""
                m = re.search(r"(0-1-[0-9a-zA-Z_\-]+)", link)
                if not m or m.group(1) in seen_ids:
                    continue
                seen_ids.add(m.group(1))
                eps.append({
                    "id": m.group(1),
                    "title": it.get("name") or "",
                    "url": link,
                    "image": it.get("image") or "",
                    "desc": it.get("description") or "",
                    "number": it.get("episodeNumber"),
                    "season": _zee5_season(it),
                    "date": it.get("uploadDate") or it.get("datePublished") or it.get("dateCreated") or "",
                    "keys": sorted(it.keys()),
                })

    if not eps:
        matches = re.findall(
            r'href=[\'"](/tv-shows/details/[^\'"]*?(0-1-[0-9a-zA-Z_\-]+)[^\'"]*?)[\'"]',
            html_text,
        )
        for link, ep_id in matches:
            if ep_id in seen_ids:
                continue
            seen_ids.add(ep_id)
            full_url = f"https://www.zee5.com{link}"
            eps.append({
                "id": ep_id,
                "title": f"Episode {ep_id}",
                "url": full_url,
                "image": "",
                "desc": "",
                "number": None,
            })

    return eps


def zee5_release_date(e):
    m = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", e.get("title") or "")
    if m:
        try:
            return datetime.strptime(m.group(1), "%B %d, %Y").strftime("%d %B %Y")
        except ValueError:
            pass
    try:
        return datetime.strptime((e.get("date") or "")[:10], "%Y-%m-%d").strftime("%d %B %Y")
    except ValueError:
        return datetime.now(IST).strftime("%d %B %Y")


def zee5_caption(show, e):
    lines = [
        "#NewEpisode #ZEE5 #Notification",
        "",
        f"Show Name: {html.escape(show['name'], quote=False)}",
    ]
    season = e.get("season")
    if season not in (None, ""):
        try:
            lines.append(f"Season Number: {int(season):02d}")
        except (TypeError, ValueError):
            lines.append(f"Season Number: {html.escape(str(season), quote=False)}")
    if e.get("number"):
        lines.append(f"Episode Number: {html.escape(str(e['number']), quote=False)}")
    lines += [
        f"Episode Title: {html.escape(e['title'], quote=False)}",
        f"Release Date: {zee5_release_date(e)}",
        "Language: Hindi",
        "",
        "Show Link:",
        f"<code>{html.escape(e['url'], quote=False)}</code>",
    ]
    return "\n".join(lines)


def zee5_poster(e):
    url = e.get("image") or ""
    if not url:
        return None
    url = url.replace("f_avif", "f_jpg").replace("w_438,h_254", "w_1000,h_562")
    return get_image(url)


# =================================================================
# DANGAL PLAY (guest token + encrypted episode list)
# =================================================================
DANGAL_SITE = "https://www.dangalplay.com"
DANGAL_API = "https://dangal-api.dangalplay.com/microplay/v1"
DANGAL_IMG = "https://img-enterr10.videostech.cloud/thumb"
DANGAL_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)
# Dangal Play ki website ke apne JS wale constants. Site inhe badle toh decrypt fail hoga.
DANGAL_KEY = b"89H49I12T20E542N17D4E5A47R184LKL"
DANGAL_IV = b"0543737198408118"
# Guest token lene ke liye site ke JS me hardcoded app-authorization (HAR sanitize me ye header hat jata hai)
DANGAL_APP_AUTH = "abd07061a3dd9851e3c9dd551e68e2688c5500a5139a561299a4ffbe40584f233f41d2b974b99122998fad15277a03408953d6e36ee6bbe9186b5cfb2a291f230a926bfef04810c774e7de5e94c5bf5f"


def dangal_headers():
    return {
        "Origin": DANGAL_SITE,
        "Referer": DANGAL_SITE + "/",
        "User-Agent": DANGAL_UA,
        "Accept": "application/json, text/plain, */*",
    }


def dangal_decrypt(hex_text):
    dec = Cipher(algorithms.AES(DANGAL_KEY), modes.CBC(DANGAL_IV)).decryptor()
    raw = dec.update(bytes.fromhex(hex_text)) + dec.finalize()
    unpad = _padding.PKCS7(128).unpadder()
    return json.loads(unpad.update(raw) + unpad.finalize())


def dangal_get_token():
    """Bina login ka guest token (roz naya banta hai, bot har run me khud leta hai)."""
    r = requests.post(
        f"{DANGAL_API}/generate/authToken/device/desktop/platform/web",
        json={},
        headers={**dangal_headers(), "Content-Type": "application/json",
                 "authorization": DANGAL_APP_AUTH},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Dangal token status {r.status_code}: {r.text[:150]}")
    return r.json()["authToken"]


def dangal_api_get(path, token):
    headers = {
        **dangal_headers(),
        "authtoken": token,
        "country": "IN",
        "language": "EN",
        "state": "Haryana",
        "city": "Gurgaon",
        "profileid": "1",
    }
    r = requests.get(
        f"{DANGAL_API}/{path}/device/desktop/platform/web", headers=headers, timeout=30
    )
    if r.status_code != 200:
        raise RuntimeError(f"Dangal API status {r.status_code}: {r.text[:150]}")
    j = r.json()
    if j.get("code") != 1:
        raise RuntimeError(f"Dangal API code {j.get('code')} for {path}")
    return dangal_decrypt(j["result"])


def dangal_released(ep):
    try:
        t = datetime.strptime(ep["startDate"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        return t <= datetime.now(IST)
    except Exception:
        return True


def dangal_newest_season_id(seasons):
    """Season title 'Ep 61-90' jaisa hota hai: jiska upper number sabse bada, wahi sabse naya."""
    best, best_n = seasons[0]["seasonID"], -1
    for s in seasons:
        nums = [int(x) for x in re.findall(r"\d+", s.get("seasonTitle", ""))]
        if nums and max(nums) > best_n:
            best, best_n = s["seasonID"], max(nums)
    return best


def dangal_latest_episodes(series_id, token):
    """Released episodes, purane se naye order me (last = sabse naya)."""
    seasons = dangal_api_get(
        f"season/list/seriesId/{series_id}/limit/10/offset/0", token
    )["season"]
    if not seasons:
        return []
    season_id = dangal_newest_season_id(seasons)
    base = f"user/content/list/seasonId/{season_id}/limit/10"
    page = dangal_api_get(f"{base}/offset/0", token)
    eps = page.get("content", [])
    total = int(page.get("totalCount") or len(eps))
    if total > len(eps) and len(eps) > 1 and int(eps[0]["id"]) < int(eps[-1]["id"]):
        # list purane->naye order me aayi, toh sabse naye episodes aakhri page pe hote hain
        eps = dangal_api_get(f"{base}/offset/{total - 10}", token).get("content", [])
    eps = [e for e in eps if e.get("contentType") == "video" and dangal_released(e)]
    return sorted(eps, key=lambda e: int(e["id"]))


def dangal_slug_candidates(link):
    parts = [p for p in urlparse(link).path.split("/") if p]
    cands = []
    if parts:
        cands.append(parts[-1])
    if len(parts) >= 2 and parts[0] == "shows":
        cands.append(parts[1])
    return list(dict.fromkeys(cands))


def dangal_series_id(show, state, token):
    """Link se series_id nikalta hai (ek baar nikal gaya toh state me save ho jata hai)."""
    if show.get("series_id"):
        return int(show["series_id"])
    link = show.get("link", "")
    cached = state["dangal_series"].get(link)
    if cached:
        return int(cached)
    errors = []
    for slug in dangal_slug_candidates(link):
        try:
            c = dangal_api_get(
                f"content/detail/slug/{quote(slug, safe='')}", token
            ).get("content") or {}
            sid = c.get("seriesID") or c.get("seriesId")
            if sid:
                state["dangal_series"][link] = int(sid)
                save_state(state)
                return int(sid)
            errors.append(f"{slug}: seriesID nahi mila")
        except Exception as e:
            errors.append(f"{slug}: {e}")
    raise RuntimeError("series_id nahi mila (" + "; ".join(errors) + ")")


def dangal_poster(ep_id):
    for w, h in ((1280, 720), (480, 270)):
        try:
            r = requests.get(
                DANGAL_IMG,
                params={"fn": f"5005_{ep_id}_rectangle_16x9", "w": w, "h": h, "platform": "web"},
                headers={"Referer": DANGAL_SITE + "/", "User-Agent": DANGAL_UA},
                timeout=30,
            )
            if (r.status_code == 200 and r.content
                    and r.headers.get("content-type", "").startswith("image")):
                return r.content
            print("  Dangal poster failed:", r.status_code)
        except requests.RequestException as e:
            print("  Dangal poster error:", e)
    return None


def dangal_caption(show, e):
    try:
        when = datetime.strptime(e["startDate"], "%Y-%m-%d %H:%M:%S").strftime("%d %B %Y")
    except Exception:
        when = datetime.now(IST).strftime("%d %B %Y")
    link = f"{DANGAL_SITE}/{e['permalink']}"
    lines = [
        "#NewEpisode #DMT #DANGALPLAY #Notification",
        "",
        f"<b>Show Name: {html.escape(show['name'], quote=False)}</b>",
    ]
    m = re.search(r"EP\s*#?\s*(\d+)", e.get("title", ""), re.I)
    if m:
        lines.append(f"<b>Episode Number: {m.group(1)}</b>")
    lines += [
        f"<b>Episode Title: {html.escape(e.get('title', ''), quote=False)}</b>",
        f"<b>Release Date: {when}</b>",
        "<b>Language: Hindi</b>",
        "",
        "<b>Show Link:</b>",
        f"<code>{html.escape(link, quote=False)}</code>",
    ]
    return "\n".join(lines)


# =================================================================
# STATE
# =================================================================
def load_state():
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    if "last_episode" in state:
        tm = str(SHOWS[0]["id"])
        state.setdefault("shows", {})[tm] = {
            "last_episode": state.pop("last_episode"),
            "last_content_id": state.pop("last_content_id", None),
        }
    state.setdefault("shows", {})
    state.setdefault("zee5", {})
    state.setdefault("dangal", {})
    state.setdefault("dangal_series", {})
    state.setdefault("timed", {})
    state.setdefault("hotstar", {})
    state.setdefault("timed_ep", {})
    return state


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# =================================================================
# PROCESS
# =================================================================
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
        pending = live[:1]
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


def process_zee5_show(show, state):
    eps = fetch_zee5_episodes(show)
    if not eps:
        print("  Koi episode nahi mila (page ya JSON-LD check karo)")
        return False
    print(f"  Page pe latest: {eps[0]['title']} | total list: {len(eps)}")

    rec = state["zee5"].get(show["id"])
    if rec is None:
        seen = [e["id"] for e in eps[1:]]
        pending = eps[:1]
    else:
        seen = list(rec.get("seen", []))
        pending = [e for e in reversed(eps) if e["id"] not in seen]

    if not pending:
        print("  Naya kuch nahi")
        return True

    for e in pending:
        if not e.get("number") or e.get("season") in (None, ""):
            print("  (ZEE5 JSON-LD me season/episode number nahi mila, keys:", e.get("keys"), ")")
        if send_tg(zee5_caption(show, e), zee5_poster(e)):
            seen.append(e["id"])
            state["zee5"][show["id"]] = {"seen": seen[-300:]}
            save_state(state)
        else:
            return False
    return True


def process_dangal_show(show, state, token):
    series_id = dangal_series_id(show, state, token)
    eps = dangal_latest_episodes(series_id, token)
    if not eps:
        print(f"  Koi episode nahi mila (series_id {series_id})")
        return False
    show = {**show, "name": show.get("name") or eps[-1].get("seriesTitle") or "Dangal Play Show"}
    print(f"  series_id {series_id} | Latest on API: {eps[-1]['title']} | released: {len(eps)}")

    key = str(series_id)
    rec = state["dangal"].get(key)
    if rec is None:
        pending = eps[-1:]          # pehli baar: sirf sabse naya episode
    else:
        last = int(rec.get("last_id", 0))
        pending = [e for e in eps if int(e["id"]) > last]

    if not pending:
        print("  Naya kuch nahi")
        return True

    for e in pending:
        if send_tg(dangal_caption(show, e), dangal_poster(e["id"])):
            state["dangal"][key] = {"last_id": int(e["id"])}
            save_state(state)
        else:
            return False
    return True


def tvmaze_latest(tv_id):
    """TVmaze (free public API) se show ka sabse naya aired episode + show poster."""
    r = requests.get(
        f"https://api.tvmaze.com/shows/{tv_id}",
        params={"embed": "previousepisode"},
        headers={"User-Agent": "ott-notify-bot"},
        timeout=30,
    )
    r.raise_for_status()
    d = r.json()
    ep = (d.get("_embedded") or {}).get("previousepisode")
    poster = (d.get("image") or {}).get("original")
    return ep, poster


def timed_caption(show, now, ep=None):
    if ep:
        try:
            when = datetime.strptime(ep["airdate"], "%Y-%m-%d").strftime("%d %B %Y")
        except Exception:
            when = now.strftime("%d %B %Y")
    else:
        when = now.strftime("%d %B %Y")
    lines = [
        f"#NewEpisode #DMT #{show.get('platform', 'OTT')} #Notification",
        "",
        f"<b>Show Name: {html.escape(show['name'], quote=False)}</b>",
    ]
    if ep and ep.get("number"):
        lines.append(f"<b>Episode Number: {ep['number']}</b>")
    if ep and ep.get("name"):
        lines.append(f"<b>Episode Title: {html.escape(ep['name'], quote=False)}</b>")
    lines += [
        f"<b>Release Date: {when}</b>",
        "<b>Language: Hindi</b>",
        "",
        "<b>Show Link:</b>",
        f"<code>{html.escape(show['link'], quote=False)}</code>",
    ]
    return "\n".join(lines)


def process_timed_show(show, state, now=None):
    now = now or datetime.now(IST)
    if now.weekday() not in show.get("air_days", range(7)):
        return True
    h, m = map(int, show["air_time"].split(":"))
    alert_at = now.replace(hour=h, minute=m, second=0, microsecond=0) \
        + timedelta(minutes=show.get("delay_min", 0))
    if now < alert_at:
        print("  Abhi time nahi hua")
        return True
    today = now.date().isoformat()
    if state["timed"].get(show["name"]) == today:
        print("  Aaj ka alert ja chuka hai")
        return True

    ep, tv_poster = None, None
    if show.get("tvmaze_id"):
        try:
            ep, tv_poster = tvmaze_latest(show["tvmaze_id"])
        except Exception as e:
            print("  TVmaze error:", e)
        last_ep = state["timed_ep"].get(show["name"])
        fresh = bool(ep) and ep.get("airdate") == today and ep.get("id") != last_ep
        if not fresh:
            ep = None
            if now < alert_at + timedelta(minutes=90):
                print("  TVmaze pe aaj ka episode abhi nahi aaya, wait")
                return True
            print("  TVmaze pe nahi aaya, bina episode detail ke alert")
    elif now > alert_at + timedelta(hours=3):
        print("  Aaj ka time nikal gaya, skip")
        return True

    poster_url = show.get("poster") or (tv_poster if ep else None)
    poster = get_image(poster_url) if poster_url else None
    if send_tg(timed_caption(show, now, ep), poster):
        state["timed"][show["name"]] = today
        if ep:
            state["timed_ep"][show["name"]] = ep.get("id")
        save_state(state)
        return True
    return False


HOTSTAR_SITEMAP = "https://www.hotstar.com/in/new-sitemap-EPISODE-{i}.xml"
HOTSTAR_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HOTSTAR_API_H = {
    "User-Agent": HOTSTAR_UA, "Accept": "application/json",
    "x-country-code": "IN", "x-platform-code": "web", "x-tenant-id": "in",
}
HOTSTAR_CDN = "https://img10.hotstar.com/image/upload/f_auto,t_web_hs_3x"


def hotstar_scan():
    """Sitemaps se HOTSTAR_SHOWS ke episode links nikalta hai. Return: (found, kitne sitemap khule)."""
    ids = {s["id"] for s in HOTSTAR_SHOWS}
    found, opened, fails = {}, 0, 0
    for i in range(1, 13):
        if opened == 0 and fails >= 2:
            break                      # shuru ke 2 sitemap hi nahi khule toh aage try nahi karte
        try:
            r = requests.get(HOTSTAR_SITEMAP.format(i=i),
                             headers={"User-Agent": HOTSTAR_UA}, timeout=90)
        except requests.RequestException as e:
            print(f"  [HOTSTAR] sitemap {i} error: {e}")
            fails += 1
            continue
        if r.status_code == 404 and opened:
            break
        if r.status_code != 200:
            print(f"  [HOTSTAR] sitemap {i} status {r.status_code}")
            fails += 1
            continue
        opened += 1
        for block in re.findall(r"<url>(.*?)</url>", r.text, re.S):
            loc = re.search(r"<loc>(.*?)</loc>", block)
            if not loc:
                continue
            loc = loc.group(1).strip()
            parts = loc.rstrip("/").split("/")
            try:
                k = parts.index("shows")
                sid = parts[k + 2]
            except (ValueError, IndexError):
                continue
            ep_id = parts[-2] if parts[-1] == "watch" else parts[-1]
            if sid not in ids or not ep_id.isdigit():
                continue
            lm = re.search(r"<lastmod>(.*?)</lastmod>", block)
            found.setdefault(sid, []).append(
                {"url": loc, "ep_id": ep_id, "lastmod": lm.group(1).strip() if lm else ""})
        print(f"  [HOTSTAR] sitemap {i}: OK")
    return found, opened


def hotstar_details(show, latest):
    """Title, episode number, date, poster. Pehle Hotstar API, nahi mila toh slug aur TVmaze."""
    parts = latest["url"].rstrip("/").split("/")
    slug = parts[-3] if parts[-1] == "watch" else parts[-2]
    title = slug.replace("-", " ").title()
    ep_no, img = "", None
    date = (latest.get("lastmod") or "")[:10] or datetime.now(IST).strftime("%Y-%m-%d")
    try:
        d = requests.get(f"https://api.hotstar.com/o/v2/content/{latest['ep_id']}",
                         headers=HOTSTAR_API_H, timeout=20)
        if d.status_code == 200:
            item = d.json().get("body", {}).get("results", {}).get("item", {})
            title = item.get("title") or title
            ep_no = item.get("episodeNo") or ""
            ts = item.get("broadCastDate") or item.get("startDate")
            if ts:
                date = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(IST).strftime("%Y-%m-%d")
            for s in item.get("imageSets", []):
                if s.get("publicUri"):
                    img = f"{HOTSTAR_CDN}/{s['publicUri']}"
                    break
        else:
            print(f"  (Hotstar API status {d.status_code}, slug/TVmaze se detail)")
    except Exception as e:
        print("  (Hotstar API error:", e, ")")
    if show.get("tvmaze_id") and (not img or not ep_no):
        try:
            tv_ep, tv_poster = tvmaze_latest(show["tvmaze_id"])
            img = img or tv_poster
            if tv_ep and not ep_no and tv_ep.get("airdate"):
                gap = abs((datetime.strptime(date, "%Y-%m-%d")
                           - datetime.strptime(tv_ep["airdate"], "%Y-%m-%d")).days)
                if gap <= 1:
                    ep_no = tv_ep.get("number") or ""
        except Exception as e:
            print("  (TVmaze error:", e, ")")
    return {"title": title, "ep_no": ep_no, "date": date, "img": img}


def hotstar_caption(show, latest, det):
    try:
        when = datetime.strptime(det["date"], "%Y-%m-%d").strftime("%d %B %Y")
    except ValueError:
        when = det["date"]
    lines = [
        "#NewEpisode #DMT #JIOHOTSTAR #Notification",
        "",
        f"<b>Show Name: {html.escape(show['name'], quote=False)}</b>",
    ]
    if det["ep_no"]:
        lines.append(f"<b>Episode Number: {html.escape(str(det['ep_no']), quote=False)}</b>")
    lines += [
        f"<b>Episode Title: {html.escape(det['title'], quote=False)}</b>",
        f"<b>Release Date: {when}</b>",
        f"<b>Language: {show.get('lang', 'Hindi')}</b>",
        "",
        "<b>Show Link:</b>",
        f"<code>{html.escape(latest['url'], quote=False)}</code>",
    ]
    return "\n".join(lines)


def process_hotstar(state):
    """True = sitemap scan theek chala (ya abhi throttle me hai aur pichla scan theek tha)."""
    now_ts = datetime.now(timezone.utc).timestamp()
    last = state.get("hotstar_scan") or {}
    if last.get("ts") and now_ts - last["ts"] < HOTSTAR_SCAN_EVERY_MIN * 60:
        print("  [HOTSTAR] abhi scan nahi (15 min me ek baar)")
        return bool(last.get("ok"))
    found, opened = hotstar_scan()
    if opened == 0:
        state["hotstar_scan"] = {"ts": None, "ok": False}
        print("  [HOTSTAR] koi sitemap nahi khula (block ya error)")
        return False
    state["hotstar_scan"] = {"ts": now_ts, "ok": True}
    save_state(state)
    for show in HOTSTAR_SHOWS:
        eps = found.get(show["id"], [])
        if not eps:
            print(f"  [HOTSTAR] {show['name']}: sitemap me episode nahi mila")
            continue
        latest = max(eps, key=lambda e: int(e["ep_id"]))
        last_id = state["hotstar"].get(show["id"])
        if last_id is not None and int(latest["ep_id"]) <= int(last_id):
            continue
        if last_id is None and HOTSTAR_SILENT_FIRST_RUN and not show.get("first_alert"):
            state["hotstar"][show["id"]] = latest["ep_id"]
            save_state(state)
            print(f"  [HOTSTAR] {show['name']}: pehli baar, latest save kiya (alert nahi)")
            continue
        det = hotstar_details(show, latest)
        poster = get_image(det["img"]) if det["img"] else None
        if send_tg(hotstar_caption(show, latest, det), poster):
            state["hotstar"][show["id"]] = latest["ep_id"]
            save_state(state)
            print(f"  [HOTSTAR] Sent: {show['name']} | {det['title']}")
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
        except Exception as e:
            print("  ERROR:", e)
    for show in ZEE5_SHOWS:
        print(f"== [ZEE5] {show['name']}")
        try:
            if process_zee5_show(show, state):
                ok += 1
        except Exception as e:
            print("  ERROR:", e)
    if DANGAL_SHOWS:
        if not HAS_CRYPTO:
            print("== [DANGAL] 'cryptography' install nahi hai, Dangal skip "
                  "(requirements.txt me 'cryptography' add karo)")
        else:
            dangal_token = None
            try:
                dangal_token = dangal_get_token()
            except Exception as e:
                print("== [DANGAL] token ERROR:", e)
            if dangal_token:
                for show in DANGAL_SHOWS:
                    print(f"== [DANGAL] {show['name']}")
                    try:
                        if process_dangal_show(show, state, dangal_token):
                            ok += 1
                    except Exception as e:
                        print("  ERROR:", e)
    hotstar_ok = False
    if HOTSTAR_SHOWS:
        print("== [HOTSTAR] sitemap scan")
        try:
            hotstar_ok = process_hotstar(state)
            if hotstar_ok:
                ok += 1
        except Exception as e:
            print("  ERROR:", e)
    for show in TIMED_SHOWS:
        if show.get("backup_for_hotstar") and hotstar_ok:
            continue
        print(f"== [TIMED] {show['name']}")
        try:
            if process_timed_show(show, state):
                ok += 1
        except Exception as e:
            print("  ERROR:", e)
    save_state(state)
    if ok == 0:
        print("Koi bhi show kaam nahi kiya")
        sys.exit(1)


if __name__ == "__main__":
    main()
