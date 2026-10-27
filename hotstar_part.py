"""JioHotstar (+ time wale alerts) - bot.py se alag file.

Is file me kuch bigad jaye (galti se ya Hotstar badal jaye) toh sirf JioHotstar ruka rahega.
SonyLIV / ZEE5 / Dangal ke alerts chalte rahenge, kyunki bot.py is file ko try/except me import karta hai.
JioHotstar ka saara kaam (shows jodna, badalna) sirf yahin karo, bot.py ko mat chhedo.
"""
import html
import json
import re
import time
from datetime import datetime, timedelta, timezone

import requests

# bot.py se aane wale helpers (bot.py init() chalakar inhe set karta hai)
send_tg = None
get_image = None
save_state = None
IST = timezone(timedelta(hours=5, minutes=30))
HAS_CURL_CFFI = False
cffi_requests = None


def init(**helpers):
    globals().update(helpers)


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
HOTSTAR_MAX_SECONDS = 150          # sitemap scan itne second se zyada nahi chalega
HOTSTAR_MAX_AGE_DAYS = 3          # isse purana episode (lastmod ke hisaab se) ka alert nahi jata
HOTSTAR_SCAN_EVERY_MIN = 5       # sitemap bade hote hain, isliye 15 min me ek baar scan
HOTSTAR_SILENT_FIRST_RUN = False  # False: turant latest episode ka alert bhejta hai

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


HOTSTAR_INDEX = "https://www.hotstar.com/in/new-sitemap.xml"   # robots.txt wala asli index
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


def _hs_get(url):
    if HAS_CURL_CFFI:
        return cffi_requests.get(url, headers={"User-Agent": HOTSTAR_UA},
                                 impersonate="chrome120", timeout=90)
    return requests.get(url, headers={"User-Agent": HOTSTAR_UA}, timeout=90)


def _hs_parse(text, ids, found):
    for block in re.findall(r"<url>(.*?)</url>", text, re.S):
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
        if len(parts) < k + 5:             # sirf show ka page hai, episode ka nahi
            continue
        ep_id = parts[-2] if parts[-1] == "watch" else parts[-1]
        if sid not in ids or not ep_id.isdigit() or ep_id == sid:
            continue
        lm = re.search(r"<lastmod>(.*?)</lastmod>", block)
        found.setdefault(sid, []).append(
            {"url": loc, "ep_id": ep_id, "lastmod": lm.group(1).strip() if lm else ""})


def _hs_scan_urls(urls, ids, found, t0):
    """Diye gaye sitemap links khol ke episodes nikalta hai. Return: kitne sitemap khule."""
    queue = list(urls)
    opened = fails = idx = 0
    while idx < len(queue):
        u = queue[idx]
        idx += 1
        name = u.rsplit("/", 1)[-1]
        if opened == 0 and fails >= 2:
            break                      # shuru ke 2 sitemap hi nahi khule toh aage try nahi karte
        if time.time() - t0 > HOTSTAR_MAX_SECONDS:
            print("  [HOTSTAR] time limit, baaki sitemap agli run me")
            break
        try:
            r = _hs_get(u)
        except Exception as e:
            print(f"  [HOTSTAR] {name} error: {e}")
            fails += 1
            continue
        if r.status_code == 404 and opened:
            break
        if r.status_code != 200:
            snippet = re.sub(r"\s+", " ", (r.text or "")[:120])
            print(f"  [HOTSTAR] {name} status {r.status_code} | {snippet}")
            fails += 1
            continue
        opened += 1
        if "<sitemapindex" in r.text:       # index file: andar ke episode sitemaps bhi kholo
            kids = [k.strip() for k in re.findall(r"<loc>(.*?)</loc>", r.text)
                    if "episode" in k.lower()]
            queue.extend(kids[:15])
            print(f"  [HOTSTAR] {name}: index, {len(kids)} episode sitemap mile")
        else:
            _hs_parse(r.text, ids, found)
            print(f"  [HOTSTAR] {name}: OK")
    return opened


def _hs_discover():
    """robots.txt se episode sitemaps ke links (agar fixed naam kaam na kare)."""
    try:
        r = _hs_get("https://www.hotstar.com/robots.txt")
    except Exception as e:
        print("  [HOTSTAR] robots.txt error:", e)
        return []
    if r.status_code != 200:
        print(f"  [HOTSTAR] robots.txt status {r.status_code}")
        return []
    urls = [u.strip() for u in re.findall(r"(?im)^\s*sitemap:\s*(\S+)", r.text)
            if "episode" in u.lower() or re.search(r"index|/sitemap\.xml$", u.lower())]
    print(f"  [HOTSTAR] robots.txt se {len(urls)} sitemap mile")
    return urls[:15]


def _hs_scan_fallback():
    """Purana tarika: fixed naam / robots.txt (index kaam na kare tab)."""
    ids = {s["id"] for s in HOTSTAR_SHOWS}
    found = {}
    t0 = time.time()
    fixed = [HOTSTAR_SITEMAP.format(i=i) for i in range(1, 13)]
    opened = _hs_scan_urls(fixed, ids, found, t0)
    if opened == 0:
        urls = _hs_discover()
        if urls:
            opened = _hs_scan_urls(urls, ids, found, t0)
    return found, opened


def _hs_index_children(text):
    kids = []
    for block in re.findall(r"<sitemap>(.*?)</sitemap>", text, re.S):
        loc = re.search(r"<loc>(.*?)</loc>", block)
        if not loc:
            continue
        lm = re.search(r"<lastmod>(.*?)</lastmod>", block)
        kids.append((loc.group(1).strip(), lm.group(1).strip() if lm else ""))
    return kids


def _hs_num(url):
    m = re.search(r"(\d+)\.xml$", url)
    return int(m.group(1)) if m else 0


def hotstar_scan(state=None):
    """robots.txt wale asli index (new-sitemap.xml) se saare episode sitemaps dekhta hai.
    Sirf wahi sitemap dobara kholta hai jinka lastmod badla ho, isliye har run halka rehta hai.
    Return: (found, kitne requests theek chale)."""
    ids = {s["id"] for s in HOTSTAR_SHOWS}
    t0 = time.time()
    book = state.setdefault("hotstar_sm", {}) if state is not None else {}
    mods = book.setdefault("mods", {})
    best = book.setdefault("best", {})
    opened = 0
    kids = []
    try:
        r = _hs_get(HOTSTAR_INDEX)
        if r.status_code == 200 and "<sitemapindex" in r.text:
            kids = [(u, lm) for u, lm in _hs_index_children(r.text) if "episode" in u.lower()]
            opened += 1
        else:
            snippet = re.sub(r"\s+", " ", (r.text or "")[:120])
            print(f"  [HOTSTAR] index status {r.status_code} | {snippet}")
    except Exception as e:
        print("  [HOTSTAR] index error:", e)
    if not kids:
        print("  [HOTSTAR] index se episode sitemap nahi mile, purana tarika")
        return _hs_scan_fallback()

    kids.sort(key=lambda k: _hs_num(k[0]), reverse=True)        # sabse naye (bade number) pehle
    lms = [lm for _, lm in kids if lm]
    print(f"  [HOTSTAR] index: {len(kids)} episode sitemap | sabse naya lastmod: {max(lms) if lms else '?'}")
    for u, lm in kids[:3]:
        print(f"    - {u.rsplit('/', 1)[-1]} (lastmod {lm or '?'})")
    always = {u for u, _ in kids[:3]} if len(lms) < len(kids) else set()   # lastmod na ho toh aakhri 3 hamesha
    for u, lm in kids:
        if time.time() - t0 > HOTSTAR_MAX_SECONDS:
            print("  [HOTSTAR] time limit, baaki sitemap agli run me")
            break
        if u in mods and mods[u] == lm and u not in always:
            continue                                            # badla nahi, dobara nahi kholte
        try:
            rr = _hs_get(u)
        except Exception as e:
            print(f"  [HOTSTAR] {u.rsplit('/', 1)[-1]} error: {e}")
            continue
        if rr.status_code != 200:
            snippet = re.sub(r"\s+", " ", (rr.text or "")[:120])
            print(f"  [HOTSTAR] {u.rsplit('/', 1)[-1]} status {rr.status_code} | {snippet}")
            continue
        tmp = {}
        _hs_parse(rr.text, ids, tmp)
        for sid, entries in tmp.items():
            cand = max(entries, key=lambda e: int(e["ep_id"]))
            if sid not in best or int(cand["ep_id"]) > int(best[sid]["ep_id"]):
                best[sid] = cand
        mods[u] = lm
        opened += 1
        print(f"  [HOTSTAR] {u.rsplit('/', 1)[-1]}: OK")
    found = {sid: [best[sid]] for sid in best if sid in ids}
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
        print(f"  [HOTSTAR] abhi scan nahi ({HOTSTAR_SCAN_EVERY_MIN} min me ek baar)")
        return bool(last.get("ok"))
    found, opened = hotstar_scan(state)
    if opened == 0:
        state["hotstar_scan"] = {"ts": None, "ok": False}
        print("  [HOTSTAR] koi sitemap nahi khula (block ya error)")
        return False
    state["hotstar_scan"] = {"ts": now_ts, "ok": True}
    save_state(state)
    newest = max((e["lastmod"][:10] for v in found.values() for e in v if e.get("lastmod")),
                 default="")
    print(f"  [HOTSTAR] sitemap me hamare shows ka sabse naya lastmod: {newest or '?'}")
    for show in HOTSTAR_SHOWS:
        eps = found.get(show["id"], [])
        if not eps:
            print(f"  [HOTSTAR] {show['name']}: sitemap me episode nahi mila")
            continue
        latest = max(eps, key=lambda e: int(e["ep_id"]))
        lm = (latest.get("lastmod") or "")[:10]
        print(f"  [HOTSTAR] {show['name']}: latest ep {latest['ep_id']} | lastmod {lm or '?'}")
        last_id = state["hotstar"].get(show["id"])
        if last_id is not None and int(latest["ep_id"]) <= int(last_id):
            continue
        if lm:
            try:
                age = (datetime.now(IST).date() - datetime.strptime(lm, "%Y-%m-%d").date()).days
            except ValueError:
                age = 0
            if age > HOTSTAR_MAX_AGE_DAYS:
                state["hotstar"][show["id"]] = latest["ep_id"]
                save_state(state)
                print(f"  [HOTSTAR] {show['name']}: purana episode ({age} din), alert nahi")
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


def run(state):
    """JioHotstar + TIMED alerts chalata hai. Return: (ok count, kya Hotstar sitemap scan theek chala)."""
    ok = 0
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
    return ok, hotstar_ok
