import os
import time
import requests
from flask import Flask, request

app = Flask(__name__)

# Umgebungsvariablen
CLIENT_ID = os.environ.get("CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")
GUILD_ID = os.environ.get("GUILD_ID", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:5000")
ALLOWED_SERVERS_STR = os.environ.get("ALLOWED_SERVERS", GUILD_ID)
ALLOWED_SERVERS = [int(x) for x in ALLOWED_SERVERS_STR.split(",") if x.strip().isdigit()]

REDIRECT_URI = f"{PUBLIC_URL}/callback"

# In-Memory-Speicher für Ergebnisse
# state -> {"user_id": int, "email": str, "status": "pending"|"success"|"error", "reason": str}
results = {}

def build_oauth_url(state):
    from urllib.parse import urlencode
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "identify email guilds",
        "state": state,
        "prompt": "consent",
    }
    return "https://discord.com/oauth2/authorize?" + urlencode(params)


@app.route("/")
def index():
    return "<h1>✅ Verify-System läuft</h1>"


@app.route("/callback")
def callback():
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")

    if error:
        return html_page("Fehler", f"Discord-Fehler: {error}. Starte /verify erneut.", False)
    if not code or not state:
        return html_page("Fehler", "Code oder State fehlt.", False)

    # OAuth2: Code gegen Access-Token tauschen
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    try:
        resp = requests.post(
            "https://discord.com/api/oauth2/token",
            data=data, headers=headers, timeout=15
        )
        if resp.status_code != 200:
            return html_page("Fehler", f"OAuth2-Fehler ({resp.status_code})", False)

        token_data = resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return html_page("Fehler", "Kein Access-Token erhalten.", False)

        # User-Info + Guilds abrufen
        auth_headers = {"Authorization": f"Bearer {access_token}"}
        me = requests.get("https://discord.com/api/users/@me", headers=auth_headers, timeout=10).json()
        guilds = requests.get("https://discord.com/api/users/@me/guilds", headers=auth_headers, timeout=10).json()

        user_id = me.get("id")
        email = me.get("email", "")
        verified = me.get("verified", False)

        if not user_id:
            return html_page("Fehler", "Konnte User-ID nicht ermitteln.", False)

        # 1. E-Mail-Check
        if not email or not verified:
            results[state] = {"user_id": int(user_id), "status": "error", "reason": "email_not_verified"}
            return html_page("E-Mail nicht verifiziert",
                "Bestätige deine E-Mail zuerst in den Discord-Einstellungen (Discord > Benutzereinstellungen > Konto).",
                False)

        # 2. Server-Check
        user_guild_ids = {int(g["id"]) for g in guilds if g.get("id")}
        if not (user_guild_ids & set(ALLOWED_SERVERS)):
            results[state] = {"user_id": int(user_id), "status": "error", "reason": "not_on_server"}
            return html_page("Nicht auf dem Server",
                "Du bist auf keinem der erlaubten Server. Tritt dem Server erst bei und starte dann /verify erneut.",
                False)

        # ✅ Erfolg
        results[state] = {
            "status": "success",
            "user_id": int(user_id),
            "email": email,
        }
        return html_page("Verifiziert ✅",
            f"Deine E-Mail (<b>{email}</b>) wurde bestätigt.<br>"
            "Der Bot wird dir in wenigen Sekunden die Verify-Rolle geben.<br>"
            "<br><small>Du kannst dieses Fenster schließen.</small>",
            True)

    except Exception as e:
        return html_page("Fehler", f"Interner Fehler: {e}", False)


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


def html_page(title, text, success):
    color = "#22c55e" if success else "#ef4444"
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<title>{title}</title></head>
<body style="font-family:sans-serif;text-align:center;padding:60px">
<h1 style="color:{color}">{title}</h1><p>{text}</p>
<p><a href="https://discord.com/channels/{GUILD_ID}">Zurück zu Discord</a></p>
</body></html>""", 200, {"Content-Type": "text/html"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)