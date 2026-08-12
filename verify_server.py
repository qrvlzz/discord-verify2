import os
import time
import requests
from flask import Flask, request

app = Flask(__name__)

# ============================================================
# ENV-VARIABLEN LADEN
# ============================================================
CLIENT_ID = os.environ.get("CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")
GUILD_ID = os.environ.get("GUILD_ID", "")

# PUBLIC_URL: Fallback auf RENDER_EXTERNAL_URL (setzt Render automatisch!)
PUBLIC_URL = os.environ.get("PUBLIC_URL", "") or os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:5000")
PUBLIC_URL = PUBLIC_URL.rstrip("/")  # Schrägstrich am Ende entfernen

ALLOWED_SERVERS_STR = os.environ.get("ALLOWED_SERVERS", GUILD_ID)
ALLOWED_SERVERS = [int(x) for x in ALLOWED_SERVERS_STR.split(",") if x.strip().isdigit()]

SITE_WEBHOOK_URL = os.environ.get("SITE_WEBHOOK_URL", "")

# ============================================================
# STARTUP-DIAGNOSE (erscheint im Render-Log)
# ============================================================
print("=" * 60)
print("VERIFY-SERVER STARTUP-DIAGNOSE")
print("=" * 60)
print(f"CLIENT_ID        gesetzt: {bool(CLIENT_ID)} (Länge: {len(CLIENT_ID)})")
print(f"CLIENT_SECRET    gesetzt: {bool(CLIENT_SECRET)} (Länge: {len(CLIENT_SECRET)})")
print(f"GUILD_ID         gesetzt: {bool(GUILD_ID)} (Wert: {GUILD_ID})")
print(f"ALLOWED_SERVERS: {ALLOWED_SERVERS}")
print(f"PUBLIC_URL:      {PUBLIC_URL}")
print(f"REDIRECT_URI:    {REDIRECT_URI}")
if not CLIENT_ID or not CLIENT_SECRET:
    print("!!! WARNUNG: CLIENT_ID/CLIENT_SECRET fehlen -> Token-Tausch wird mit 400 fehlschlagen!")
if not ALLOWED_SERVERS:
    print("!!! WARNUNG: ALLOWED_SERVERS ist leer -> Server-Check schlägt fehl!")
print("=" * 60)

# In-Memory-Speicher: state -> Ergebnis
results = {}


# ============================================================
# ROUTEN
# ============================================================
@app.route("/")
def index():
    return f"""<h1>✅ Verify-System läuft</h1>
<p>REDIRECT_URI: <code>{REDIRECT_URI}</code></p>
<p>CLIENT_ID gesetzt: <b>{'JA' if CLIENT_ID else 'NEIN'}</b> &nbsp;•&nbsp;
CLIENT_SECRET gesetzt: <b>{'JA' if CLIENT_SECRET else 'NEIN'}</b></p>
<p><a href="/debug">→ Debug-Übersicht öffnen</a></p>"""


@app.route("/debug")
def debug():
    """Zeigt, was der Server wirklich sieht (keine Secrets, nur ob gesetzt)."""
    return {
        "env_gesetzt": {
            "CLIENT_ID": bool(CLIENT_ID),
            "CLIENT_SECRET": bool(CLIENT_SECRET),
            "GUILD_ID": bool(GUILD_ID),
            "PUBLIC_URL": PUBLIC_URL,
            "ALLOWED_SERVERS": ALLOWED_SERVERS,
            "RENDER_EXTERNAL_URL": os.environ.get("RENDER_EXTERNAL_URL", "(nicht gesetzt)"),
        },
        "REDIRECT_URI": REDIRECT_URI,
        "aktive_states": len(results),
        "zeitstempel": int(time.time()),
    }


def oauth_error_hint(err):
    hints = {
        "invalid_client": "CLIENT_ID oder CLIENT_SECRET fehlen/falsch auf Render → Environment prüfen und NEU deployen.",
        "redirect_uri_mismatch": "redirect_uri stimmt nicht überein → REDIRECT_URI EXAKT im Developer Portal eintragen.",
        "invalid_grant": "Dieser Code wurde schon verwendet oder ist abgelaufen → Neuen Verify-Button-Klick starten.",
        "invalid_scope": "Scope im OAuth-Link stimmt nicht → Bot-Code prüfen (identify email guilds).",
    }
    return hints.get(err, "Siehe Render-Logs für Details.")


@app.route("/callback")
def callback():
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")

    print(f"[CALLBACK] Aufgerufen. code={'JA' if code else 'NEIN'}, state={'JA' if state else 'NEIN'}, error={error}")

    if error:
        return error_page("Fehler", f"Discord-Fehler: {error}. Klicke erneut auf Verifizieren.")
    if not code or not state:
        return error_page("Fehler", "Code oder State fehlt.")

    # OAuth2: Code gegen Access-Token tauschen
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    print(f"[CALLBACK] Token-Tausch gegen Discord ... redirect_uri={REDIRECT_URI}")
    try:
        resp = requests.post(
            "https://discord.com/api/oauth2/token",
            data=data, headers=headers, timeout=15
        )
        print(f"[CALLBACK] Discord antwortet: Status {resp.status_code} | {resp.text[:300]}")

        if resp.status_code != 200:
            try:
                err = resp.json().get("error", resp.text)
            except Exception:
                err = resp.text
            hint = oauth_error_hint(err)
            return error_page(
                "OAuth2-Fehler",
                f"<b>Status:</b> {resp.status_code}<br>"
                f"<b>Grund:</b> <code>{err}</code><br><br>"
                f"<b>Hinweis:</b> {hint}"
            )

        token_data = resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return error_page("Fehler", "Kein Access-Token erhalten.")

        # User-Info + Guilds abrufen
        auth_headers = {"Authorization": f"Bearer {access_token}"}
        me = requests.get("https://discord.com/api/users/@me", headers=auth_headers, timeout=10).json()
        guilds = requests.get("https://discord.com/api/users/@me/guilds", headers=auth_headers, timeout=10).json()

        user_id = me.get("id")
        email = me.get("email", "")
        verified = me.get("verified", False)

        print(f"[CALLBACK] User gefunden: id={user_id}, email={'ja' if email else 'NEIN'}, verified={verified}")

        if not user_id:
            return error_page("Fehler", "Konnte User-ID nicht ermitteln.")

        # 1. E-Mail-Check
        if not email or not verified:
            results[state] = {"user_id": int(user_id), "status": "error", "reason": "email_not_verified"}
            return error_page("E-Mail nicht verifiziert",
                "Bestätige deine E-Mail zuerst in den Discord-Einstellungen (Discord → Benutzereinstellungen → Konto).")

        # 2. Server-Check
        user_guild_ids = {int(g["id"]) for g in guilds if g.get("id")}
        if not (user_guild_ids & set(ALLOWED_SERVERS)):
            results[state] = {"user_id": int(user_id), "status": "error", "reason": "not_on_server"}
            return error_page("Nicht auf dem Server",
                "Du bist auf keinem der erlaubten Server. Tritt dem Server erst bei und klicke dann erneut auf Verifizieren.")

        # ✅ Erfolg – alle Daten für den Bot speichern (die Seite zeigt sie NICHT an!)
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
        }
        print(f"[CALLBACK] ✅ Erfolg gespeichert für state {state}")
        return success_page()

    except Exception as e:
        print(f"[CALLBACK] Exception: {e}")
        return error_page("Fehler", f"Interner Fehler: {e}")


@app.route("/check")
def check_state():
    """Wird vom Bot alle 5 Sekunden gepollt."""
    state = request.args.get("state")
    if not state:
        return {"status": "no_state"}, 400

    result = results.get(state)
    if result is None:
        return {"status": "pending"}
    return result


# ============================================================
# SEITEN-DESIGN
# ============================================================
def success_page():
    """Schöne Erfolgsseite – OHNE E-Mail, OHNE Username."""
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
    background: #ffffff;
    border-radius: 24px;
    padding: 48px 56px;
    max-width: 480px;
    width: 100%;
    text-align: center;
    box-shadow: 0 25px 60px rgba(0,0,0,.35);
    animation: pop .5s ease;
  }
  @keyframes pop { from { transform: scale(.9); opacity: 0; } to { transform: scale(1); opacity: 1; } }
  .check {
    width: 96px; height: 96px;
    margin: 0 auto 24px;
    background: #22c55e;
    border-radius: 50%;
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
    background: #ffffff;
    border-radius: 24px;
    padding: 40px 48px;
    max-width: 480px;
    width: 100%;
    text-align: center;
    box-shadow: 0 25px 60px rgba(0,0,0,.35);
  }}
  .icon {{
    width: 80px; height: 80px;
    margin: 0 auto 20px;
    background: #ef4444;
    border-radius: 50%;
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


# ============================================================
# BESUCHER-LOGGER (Website -> Discord-Webhook)
# ============================================================
@app.route("/log")
def log_visitor():
    """Wird von der Website aufgerufen – loggt Besucher an den Discord-Webhook."""
    if not SITE_WEBHOOK_URL:
        return "kein Webhook konfiguriert", 503

    # IP ermitteln (hinter Render-Proxy/Cloudflare)
    ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() \
         or request.remote_addr or "Unbekannt"

    # Geo-Infos abrufen (kostenlos, 45 req/min)
    geo = {}
    try:
        r = requests.get(
            f"http://ip-api.com/json/{ip}?fields=status,country,regionName,city,isp,org,lat,lon,timezone",
            timeout=5)
        d = r.json()
        if d.get("status") == "success":
            geo = d
    except Exception:
        pass

    q = request.args
    fields = [
        {"name": "🌐 IP",           "value": f"`{ip}`", "inline": True},
        {"name": "🇩🇪 Land",        "value": geo.get("country", "?"), "inline": True},
        {"name": "🏙️ Stadt",       "value": geo.get("city", "?"), "inline": True},
        {"name": "🏢 ISP",          "value": geo.get("isp", "?"), "inline": True},
        {"name": "📍 Koordinaten",  "value": f"{geo.get('lat', '?')}, {geo.get('lon', '?')}", "inline": True},
        {"name": "🧭 Zeitzone",     "value": geo.get("timezone", "?"), "inline": True},
        {"name": "💻 User-Agent",   "value": (q.get("ua") or request.headers.get("User-Agent") or "?")[:1024]},
        {"name": "🔗 Referrer",     "value": q.get("ref") or "Direkt"},
        {"name": "🖥️ Screen",       "value": q.get("screen") or "?", "inline": True},
        {"name": "🗣️ Sprache",      "value": q.get("lang") or "?", "inline": True},
        {"name": "🕒 Browser-TZ",   "value": q.get("tz") or "?", "inline": True},
        {"name": "📄 Seite",        "value": q.get("page") or "?"},
        {"name": "⏰ Zeit",         "value": time.strftime("%d.%m.%Y %H:%M:%S"), "inline": True},
    ]

    payload = {
        "username": "Site-Logger",
        "embeds": [{
            "title": "🌐 Neuer Besucher erfasst",
            "color": 0x5865F2,
            "fields": fields,
            "footer": {"text": "Site-Logger • /log"},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        }],
    }
    try:
        requests.post(SITE_WEBHOOK_URL, json=payload, timeout=10)
    except Exception as e:
        print(f"[LOG] Webhook-Fehler: {e}")
    return "OK"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"[START] Läuft auf Port {port}")
    app.run(host="0.0.0.0", port=port)
