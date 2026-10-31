"""JioHotstar alerts - bot.py se alag file."""
import html
from datetime import datetime, timedelta, timezone
import requests

send_tg = None
get_image = None
save_state = None
IST = timezone(timedelta(hours=5, minutes=30))
HAS_CURL_CFFI = False
cffi_requests = None

def init(**helpers):
    globals().update(helpers)

TIMED_SHOWS = [
    {"name": "Yeh Rishta Kya Kehlata Hai", "platform": "JIOHOTSTAR", "tvmaze_id": 55393,
     "air_time": "21:30", "delay_min": 45, "air_days": [0,1,2,3,4,5,6],
     "link": "https://www.hotstar.com/in/shows/yeh-rishta/586", "poster": ""},
    {"name": "Anupama", "platform": "JIOHOTSTAR",
     "air_time": "22:00", "delay_min": 30, "air_days": [0,1,2,3,4,5],
     "link": "https://www.hotstar.com/in/shows/anupama/1260022017", "poster": ""},
    {"name": "Udne Ki Aasha", "platform": "JIOHOTSTAR",
     "air_time": "20:30", "delay_min": 45, "air_days": [0,1,2,3,4,5],
     "link": "https://www.hotstar.com/in/shows/udne-ki-aasha/1271270408", "poster": ""},
    {"name": "Yeh Fitoor Tera", "platform": "JIOHOTSTAR",
     "air_time": "20:00", "delay_min": 45, "air_days": [0,1,2,3,4,5],
     "link": "https://www.hotstar.com/in/shows/yeh-fitoor-tera/1271650854", "poster": ""},
    {"name": "Oh Humnava Tum Dena Saath Mera", "platform": "JIOHOTSTAR",
     "air_time": "21:00", "delay_min": 45, "air_days": [0,1,2,3,4,5],
     "link": "https://www.hotstar.com/in/shows/oh-humnava-tum-dena-saath-mera/1271616684", "poster": ""},
    {"name": "Sayoneee", "platform": "JIOHOTSTAR",
     "air_time": "21:30", "delay_min": 45, "air_days": [0,1,2,3,4,5],
     "link": "https://www.hotstar.com/in/shows/sayoneee/1271666144", "poster": ""},
    {"name": "Bigg Boss", "platform": "JIOHOTSTAR",
     "air_time": "21:00", "delay_min": 45, "air_days": [0,1,2,3,4,5,6],
     "link": "https://www.hotstar.com/in/shows/bigg-boss/1971002586", "poster": ""},
    {"name": "Mannat Har Khushi Paane Ki", "platform": "JIOHOTSTAR",
     "air_time": "21:00", "delay_min": 45, "air_days": [0,1,2,3,4,5],
     "link": "https://www.hotstar.com/in/shows/mannat-har-khushi-paane-ki/1971311993", "poster": ""},
    {"name": "Tuu Juliet Jatt Di", "platform": "JIOHOTSTAR",
     "air_time": "20:30", "delay_min": 45, "air_days": [0,1,2,3,4,5],
     "link": "https://www.hotstar.com/in/shows/tuu-juliet-jatt-di/1271463159", "poster": ""},
    {"name": "Dhamaal with Pati Patni aur Panga", "platform": "JIOHOTSTAR",
     "air_time": "20:00", "delay_min": 45, "air_days": [0,1,2,3,4],
     "link": "https://www.hotstar.com/in/shows/dhamaal-with-pati-patni-aur-panga/1271427882", "poster": ""},
    {"name": "Roadies Rebirth", "platform": "JIOHOTSTAR",
     "air_time": "19:00", "delay_min": 60, "air_days": [5,6],
     "link": "https://www.hotstar.com/in/shows/roadies-rebirth/1971002274", "poster": ""},
    {"name": "Dr. Aarambhi", "platform": "JIOHOTSTAR",
     "air_time": "21:00", "delay_min": 45, "air_days": [0,1,2,3,4,5],
     "link": "https://www.hotstar.com/in/shows/dr-aarambhi/1271522330", "poster": ""},
]

def tvmaze_latest(tv_id):
    r = requests.get(f"https://api.tvmaze.com/shows/{tv_id}",
                     params={"embed": "previousepisode"},
                     headers={"User-Agent": "ott-notify-bot"}, timeout=30)
    r.raise_for_status()
    d = r.json()
    return (d.get("_embedded") or {}).get("previousepisode"), (d.get("image") or {}).get("original")

def timed_caption(show, now, ep=None):
    when = now.strftime("%d %B %Y")
    if ep:
        try: when = datetime.strptime(ep["airdate"], "%Y-%m-%d").strftime("%d %B %Y")
        except: pass
    lines = [f"#NewEpisode #DMT #{show.get('platform','OTT')} #Notification", "",
             f"<b>Show Name: {html.escape(show['name'], quote=False)}</b>"]
    if ep and ep.get("season"): lines.append(f"<b>Season Number: {ep['season']:02d}</b>")
    if ep and ep.get("number"): lines.append(f"<b>Episode Number: {ep['number']}</b>")
    if ep and ep.get("name"): lines.append(f"<b>Episode Title: {html.escape(ep['name'], quote=False)}</b>")
    lines += [f"<b>Release Date: {when}</b>", "<b>Language: Hindi</b>", "",
              "<b>Show Link:</b>", f"<code>{html.escape(show['link'], quote=False)}</code>"]
    return "\n".join(lines)

def process_timed_show(show, state, now=None):
    now = now or datetime.now(IST)
    if now.weekday() not in show.get("air_days", range(7)):
        return True
    h, m = map(int, show["air_time"].split(":"))
    alert_at = now.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(minutes=show.get("delay_min", 0))
    if now < alert_at:
        print(f"  Time nahi hua (alert {alert_at.strftime('%H:%M')} IST)")
        return True
    today = now.date().isoformat()
    if state["timed"].get(show["name"]) == today:
        print("  Aaj ka alert ja chuka hai")
        return True
    ep, tv_poster = None, None
    if show.get("tvmaze_id"):
        try: ep, tv_poster = tvmaze_latest(show["tvmaze_id"])
        except Exception as e: print(f"  TVmaze error: {e}")
        last_ep_id = state.get("timed_ep", {}).get(show["name"])
        fresh = bool(ep) and ep.get("airdate") == today and ep.get("id") != last_ep_id
        if not fresh:
            ep = None
            if now < alert_at + timedelta(minutes=90):
                print("  TVmaze pe aaj ka ep nahi, wait")
                return True
    else:
        if now > alert_at + timedelta(hours=3):
            print("  Window nikal gaya, skip")
            return True
    poster_url = show.get("poster") or (tv_poster if ep else None)
    poster = get_image(poster_url) if poster_url else None
    if send_tg(timed_caption(show, now, ep), poster):
        state.setdefault("timed", {})[show["name"]] = today
        if ep: state.setdefault("timed_ep", {})[show["name"]] = ep.get("id")
        save_state(state)
        print(f"  Sent: {show['name']}")
        return True
    print(f"  Send fail: {show['name']}")
    return False

def run(state):
    ok = 0
    for show in TIMED_SHOWS:
        print(f"== [JIOHOTSTAR] {show['name']}")
        try:
            if process_timed_show(show, state): ok += 1
        except Exception as e: print(f"  ERROR: {e}")
    return ok, True
