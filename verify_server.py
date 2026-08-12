import os
import time
import requests
from flask import Flask, request

app = Flask(__name__)

# ============================================================
# ENV-VARIABLEN LADEN (mit Debug-Logging)
# ============================================================
CLIENT_ID = os.environ.get("CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")
GUILD_ID = os.environ.get("GUILD_ID", "")

# PUBLIC_URL: Fallback auf RENDER_EXTERNAL_URL (setzt Render automatisch!)
PUBLIC_URL = os.environ.get("PUBLIC_URL", "") or os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:5000")
PUBLIC_URL = PUBLIC_URL.rstrip("/")  # Schrägstrich am Ende entfernen (häufige Fehlerquelle!)

ALLOWED_SERVERS_STR = os.environ.get("ALLOWED_SERVERS", GUILD_ID)
ALLOWED_SERVERS = [int(x) for x in ALLOWED_SERVERS_STR.split(",") if x.strip().isdigit()]

REDIRECT_URI = f"{PUBLIC_URL}/callback"

# ============================================================
# STARTUP-DIAGNOSE (erscheint im Render-Deploy-Log!)
# ============================================================
print("=" * 60)
print("VERIFY-SERVER STARTUP-DIAGNOSE")
print("=" * 60)
print(f"CLIENT_ID        gesetzt: {bool(CLIENT_ID)} (Länge: {len(CLIENT_ID)})")
print(f"CLIENT_SECRET    gesetzt: {bool(CLIENT_SECRET)} (Länge: {len(CLIENT_SECRET)})")
print(f"GUILD_ID         gesetzt: {bool(GUILD_ID)} (Wert: {GUILD_ID})")
print(f"ALLOWED_SERVERS: {ALLOWED_SERVERS}")
print(f"PUBLIC_URL      (Env oder RENDER_EXTERNAL_URL): {PUBLIC_URL}")
print(f"REDIRECT_URI    (muss EXAKT im Dev-Portal stehen): {REDIRECT_URI}")
if not CLIENT_ID or not CLIENT_SECRET:
    print("!!! WARNUNG: CLIENT_ID/CLIENT_SECRET fehlen -> Token-Tausch wird mit 400 fehlschlagen!")
if not ALLOWED_SERVERS:
    print("!!! WARNUNG: ALLOWED_SERVERS ist leer -> Server-Check schlägt fehl!")
print("=" * 60)

# In-Memory-Speicher: state -> {"user_id": int, "email": str, "status": "pending"|"success"|"error", "reason": str}
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
        "redirect_uri_mismatch": "redirect_uri stimmt nicht überein → Oben die REDIRECT_URI ansehen und EXAKT im Developer Portal eintragen.",
        "invalid_grant": "Dieser Code wurde schon verwendet oder ist abgelaufen → Ein NEUES /verify starten (keinen alten Link klicken).",
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
            return html_page(
                "OAuth2-Fehler",
                f"<b>Status:</b> {resp.status_code}<br>"
                f"<b>Grund:</b> <code>{err}</code><br><br>"
                f"<b>Hinweis:</b> {hint}",
                False
            )

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

        print(f"[CALLBACK] User gefunden: id={user_id}, email={'ja' if email else 'NEIN'}, verified={verified}")

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
        print(f"[CALLBACK] ✅ Erfolg gespeichert für state {state}")
        return html_page("Verifiziert ✅",
            f"Deine E-Mail (<b>{email}</b>) wurde bestätigt.<br>"
            "Der Bot wird dir in wenigen Sekunden die Verify-Rolle geben.<br>"
            "<br><small>Du kannst dieses Fenster schließen.</small>",
            True)

    except Exception as e:
        print(f"[CALLBACK] Exception: {e}")
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
    print(f"[START] Läuft auf Port {port}")
    app.run(host="0.0.0.0", port=port)