import os, asyncio, datetime
import aiohttp
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

WEBHOOK_URL = os.getenv("SITE_WEBHOOK_URL", "")
PORT = int(os.getenv("PORT", "8123"))

def client_ip(request):
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote or "Unbekannt"

async def get_geo(session, ip):
    try:
        async with session.get(
            f"http://ip-api.com/json/{ip}?fields=status,country,regionName,city,isp,lat,lon",
            timeout=aiohttp.ClientTimeout(total=5)
        ) as r:
            d = await r.json()
            return d if d.get("status") == "success" else {}
    except Exception:
        return {}

async def handle_log(request):
    if not WEBHOOK_URL:
        return web.Response(text="kein Webhook konfiguriert", status=503)
    ip = client_ip(request)
    q = request.query

    # 👤 Name / Discord-Name / ID (werden vom Tracker-Snippet mitgeschickt)
    name = (q.get("name") or "")[:128]
    dn = (q.get("dn") or "")[:128]
    uid = (q.get("id") or "")[:64]

    fields = [
        {"name": "IP",          "value": f"`{ip}`", "inline": True},
        {"name": "Land",        "value": "?", "inline": True},
        {"name": "Stadt",       "value": "?", "inline": True},
        {"name": "ISP",         "value": "?", "inline": True},
        {"name": "User-Agent",  "value": q.get("ua") or request.headers.get("User-Agent", "?")},
        {"name": "Referrer",    "value": q.get("ref") or "Direkt"},
        {"name": "Screen",      "value": q.get("screen") or "?", "inline": True},
        {"name": "Sprache",     "value": q.get("lang") or "?", "inline": True},
        {"name": "Zeitzone",    "value": q.get("tz") or "?", "inline": True},
        {"name": "Seite",       "value": q.get("page") or "?"},
        {"name": "Zeit",        "value": datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S"), "inline": True},
    ]

    # Name DIREKT UNTER der IP einfügen
    if name or dn or uid:
        lines = []
        if name:
            lines.append(f"**{name}**")
        if dn:
            lines.append(f"Discord: **{dn}**")
        if uid:
            lines.append(f"ID: `{uid}`")
        fields.insert(1, {"name": "👤 Name", "value": "\n".join(lines), "inline": False})

    async with aiohttp.ClientSession() as session:
        geo = await get_geo(session, ip)
        # Land/Stadt/ISP nachtragen, sobald Geo da ist
        for field in fields:
            if field["name"] == "Land":
                field["value"] = geo.get("country", "?")
            elif field["name"] == "Stadt":
                field["value"] = geo.get("city", "?")
            elif field["name"] == "ISP":
                field["value"] = geo.get("isp", "?")

        payload = {
            "username": "Site-Logger",
            "embeds": [{"title": "🌐 Neuer Besucher", "color": 0x5865F2,
                        "fields": fields,
                        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()}],
        }
        try:
            async with session.post(WEBHOOK_URL, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status not in (200, 204):
                    print(f"[LOGGER] Webhook-Fehler: {resp.status}")
        except Exception as e:
            print(f"[LOGGER] Fehler: {e}")
    return web.Response(text="OK")

async def main():
    app = web.Application()
    app.router.add_get("/log", handle_log)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print(f"✅ Site-Logger läuft auf Port {PORT}")

if __name__ == "__main__":
    asyncio.run(main())
