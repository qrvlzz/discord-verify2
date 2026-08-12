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
PULL_GUILD_ID    = os.environ.get("PULL_GUILD_ID", "")

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
print(f"PULL_GUILD_ID    gesetzt: {bool(PULL_GUILD_ID)} (Wert: {PULL_GUILD_ID or '–'})")


def exchange_code(code):
    """Tauscht den OAuth2-Code gegen Access-Token und User-Daten."""
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
        return None, None, None, None, None, f"Token-Fehler: {r.status_code} {r.text[:200]}"

    token = r.json()
    access_token = token.get("access_token")
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
    return access_token, user_id, email, me, guilds, None


def add_user_to_guild(access_token, guild_id, user_id):
    """Fügt einen User per OAuth2 (guilds.join) einem Server hinzu."""
    url = f"{DISCORD_API}/guilds/{guild_id}/members/{user_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    try:
        r = requests.put(url, headers=headers, json={"access_token": access_token}, timeout=10)
        if r.status_code in (201, 204):
            return {"ok": True, "code": r.status_code}
        return {"ok": False, "code": r.status_code, "detail": r.text[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ============================================================
# TRACKER-SNIPPET (loggt jeden Seitenaufruf an den Webhook)
# ============================================================
TRACKER_SNIPPET = """
<script>
(function () {
    try {
        var p = new URLSearchParams({
            key: "%s",
            ua: navigator.userAgent,
            lang: navigator.language,
            screen: screen.width + "x" + screen.height,
            tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
            ref: document.referrer,
            page: location.pathname + location.search,
            host: location.host
        });
        var img = new Image();
        img.src = "/log?" + p.toString();
    } catch (e) {}
})();
</script>
""" % SITE_LOG_KEY


def success_page():
    html = """<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>✅ Verifizierung erfolgreich</title>
    <style>
        body { font-family: sans-serif; background: #1e1f22; color: #fff; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }
        .card { background: #2b2d31; border-radius: 12px; padding: 32px; max-width: 480px; text-align: center; border: 1px solid #3f4147; }
        h1 { margin-top: 0; }
        .check { font-size: 56px; }
    </style>
</head>
<body>
    <div class="card">
        <div class="check">✅</div>
        <h1>Verifizierung erfolgreich!</h1>
        <p>Dein Account wurde verifiziert und auf dem Server <strong>__GUILD_ID__</strong> freigeschaltet.</p>
        <p>Du kannst dieses Fenster jetzt schließen und zu Discord zurückkehren.</p>
    </div>
"""
    return html.replace("__GUILD_ID__", GUILD_ID or "deinem Server") + TRACKER_SNIPPET + """
</body>
</html>"""


def error_page(title, message):
    html = """<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>❌ Verifizierung fehlgeschlagen</title>
    <style>
        body { font-family: sans-serif; background: #1e1f22; color: #fff; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }
        .card { background: #2b2d31; border-radius: 12px; padding: 32px; max-width: 480px; text-align: center; border: 1px solid #3f4147; }
        h1 { margin-top: 0; }
        code { background: #1e1f22; padding: 2px 6px; border-radius: 4px; }
    </style>
</head>
<body>
    <div class="card">
        <h1>❌ __TITLE__</h1>
        <p>__MESSAGE__</p>
        <p>Du kannst dieses Fenster schließen.</p>
    </div>
"""
    html = html.replace("__TITLE__", title).replace("__MESSAGE__", message)
    return html + TRACKER_SNIPPET + """
</body>
</html>"""


# ============================================================
# ROUTEN
# ============================================================
@app.route("/")
def index():
    html = """<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Verify-System</title>
    <style>
        body { font-family: sans-serif; background: #1e1f22; color: #fff; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }
        .card { background: #2b2d31; border-radius: 12px; padding: 32px; max-width: 520px; border: 1px solid #3f4147; }
        h1 { margin-top: 0; }
        code { background: #1e1f22; padding: 2px 6px; border-radius: 4px; }
        a { color: #5865f2; }
    </style>
</head>
<body>
    <div class="card">
        <h1>✅ Verify-System läuft</h1>
        <p>REDIRECT_URI: <code>__REDIRECT_URI__</code></p>
        <p>CLIENT_ID gesetzt: <b>__CLIENT_STATUS__</b> &nbsp;•&nbsp; CLIENT_SECRET gesetzt: <b>__SECRET_STATUS__</b></p>
        <p>SITE_WEBHOOK_URL gesetzt: <b>__WEBHOOK_STATUS__</b></p>
        <p>PULL_GUILD_ID: <code>__PULL_GUILD__</code></p>
        <p><a href="/debug">→ Debug-Übersicht öffnen</a></p>
    </div>
"""
    html = (html
            .replace("__REDIRECT_URI__", REDIRECT_URI)
            .replace("__CLIENT_STATUS__", "JA" if CLIENT_ID else "NEIN")
            .replace("__SECRET_STATUS__", "JA" if CLIENT_SECRET else "NEIN")
            .replace("__WEBHOOK_STATUS__", "JA" if SITE_WEBHOOK_URL else "NEIN")
            .replace("__PULL_GUILD__", PULL_GUILD_ID or "–"))
    return html + TRACKER_SNIPPET + """
</body>
</html>"""


@app.route("/callback")
def callback():
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")

    if error:
        if state:
            results[state] = {"status": "error", "reason": f"Discord-Fehler: {error}"}
        print(f"[CALLBACK] ❌ Discord-Fehler: {error}")
        return error_page("Verifizierung abgebrochen", f"Discord meldet: {error}")

    if not code or not state:
        return error_page("Fehler", "Fehlende Parameter (code/state).")

    access_token, user_id, email, me, guilds, err = exchange_code(code)
    if err or not access_token or not user_id:
        msg = err or "Unbekannter Fehler beim Token-Tausch."
        if state:
            results[state] = {"status": "error", "reason": msg}
        print(f"[CALLBACK] ❌ {msg}")
        return error_page("Fehler beim Verifizieren", msg)

    # ✅ Erfolg – optional in Ziel-Server pullen
    pull_result = None
    if PULL_GUILD_ID:
        pull_result = add_user_to_guild(access_token, PULL_GUILD_ID, user_id)
        if pull_result.get("ok"):
            print(f"[CALLBACK] ✅ User {user_id} wurde in Server {PULL_GUILD_ID} gepullt")
        else:
            print(f"[CALLBACK] ❌ Pull fehlgeschlagen für {user_id}: {pull_result}")

    # Alle Daten für den Bot speichern (die Seite zeigt sie NICHT an!)
    results[state] = {
        "status": "success",
        "user_id": int(user_id),
        "email": email,
        "username": (me or {}).get("username", ""),
        "display_name": (me or {}).get("global_name") or "",
        "guilds": [
            {"id": g.get("id"), "name": g.get("name"), "joined_at": g.get("joined_at")}
            for g in (guilds or []) if g.get("id")
        ],
        "pull_result": pull_result,
    }
    print(f"[CALLBACK] ✅ Erfolg gespeichert für state {state}")
    return success_page()


@app.route("/check")
def check():
    state = request.args.get("state", "")
    if state in results:
        return jsonify(results.pop(state))
    return jsonify({"status": "pending"})


@app.route("/log")
def log_visit():
    if request.args.get("key", "") != SITE_LOG_KEY or not SITE_WEBHOOK_URL:
        return "ok"
    ua     = request.args.get("ua", "")
    lang   = request.args.get("lang", "")
    screen = request.args.get("screen", "")
    tz     = request.args.get("tz", "")
    ref    = request.args.get("ref", "")
    page   = request.args.get("page", "")
    host   = request.args.get("host", "")
    ip = (request.headers.get("X-Forwarded-For", request.remote_addr or "?")
          .split(",")[0].strip())
    payload = {
        "content": "🌐 Neuer Besucher erfasst",
        "embeds": [{
            "title": "🌐 Besucher",
            "color": 0x5865F2,
            "fields": [
                {"name": "🖥️ Seite", "value": f"`{host}{page}`", "inline": False},
                {"name": "🌍 IP", "value": f"`{ip}`", "inline": True},
                {"name": "📱 Browser/UA", "value": f"`{ua[:200]}`", "inline": False},
                {"name": "🌐 Sprache", "value": f"`{lang}`", "inline": True},
                {"name": "📺 Bildschirm", "value": f"`{screen}`", "inline": True},
                {"name": "🕒 Zeitzone", "value": f"`{tz}`", "inline": True},
                {"name": "🔗 Referrer", "value": f"`{ref[:200] or '–'}`", "inline": False},
            ],
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "footer": {"text": "Site-Logger"},
        }],
    }
    try:
        requests.post(SITE_WEBHOOK_URL, json=payload, timeout=10)
    except Exception:
        pass
    return "ok"


@app.route("/debug")
def debug():
    return jsonify({
        "client_id_set": bool(CLIENT_ID),
        "client_secret_set": bool(CLIENT_SECRET),
        "public_url": PUBLIC_URL,
        "redirect_uri": REDIRECT_URI,
        "site_log_key_set": bool(SITE_LOG_KEY),
        "site_webhook_set": bool(SITE_WEBHOOK_URL),
        "pull_guild_id": PULL_GUILD_ID or None,
        "pending_states": len(results),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
