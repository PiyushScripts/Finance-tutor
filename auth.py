# auth.py
#
# Two ways to sign in:
#
#   1. Email and password. Accounts are stored in Azure Cosmos DB (see
#      database.py). We never store the password itself, only a secure
#      hash of it, made by Werkzeug (the library Flask is built on). Even
#      if someone saw the database, they couldn't read anyone's password.
#
#   2. "Continue with Microsoft", using Microsoft Entra ID through
#      Microsoft's MSAL library. The user signs in on Microsoft's own page
#      and Microsoft tells us who they are. This button only appears once
#      the app registration details are in .env.

import os
import re
from functools import wraps
from urllib.parse import quote
from dotenv import load_dotenv
from flask import session, redirect, url_for, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import msal

import database

load_dotenv()

CLIENT_ID = os.getenv("ENTRA_CLIENT_ID")
CLIENT_SECRET = os.getenv("ENTRA_CLIENT_SECRET")
REDIRECT_URI = os.getenv("ENTRA_REDIRECT_URI", "http://localhost:5000/auth/callback")

# The Microsoft button is only shown when it has been set up.
MICROSOFT_ENABLED = bool(CLIENT_ID and CLIENT_SECRET)

# "common" lets both work/school accounts and personal Microsoft accounts
# (Outlook, Hotmail and so on) sign in.
AUTHORITY = "https://login.microsoftonline.com/common"
SCOPES = ["User.Read"]

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8


def sign_in_user(user_id, name, email, provider):
    # Clearing first means no leftover data from before sign-in is reused.
    session.clear()
    session["user"] = {"id": user_id, "name": name, "email": email, "provider": provider}


# ---------------------------------------------------------------------
# Email and password
# ---------------------------------------------------------------------

def check_email(email):
    # First step of the sign-in form. Returns (clean_email, error, exists):
    # exists tells the page whether to ask for a password or show sign-up.
    email = email.strip().lower()
    if not EMAIL_PATTERN.match(email):
        return email, "Please enter a valid email address.", False
    return email, None, database.get_user_by_email(email) is not None


def sign_up(name, email, password, confirm):
    # Returns an error message, or None if the account was created.
    name = name.strip()
    email = email.strip().lower()

    if not name:
        return "Please enter your name."
    if not EMAIL_PATTERN.match(email):
        return "Please enter a valid email address."
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Your password needs at least {MIN_PASSWORD_LENGTH} characters."
    if password != confirm:
        return "The two passwords don't match."
    if database.get_user_by_email(email):
        return "An account with this email already exists. Try signing in."

    user = database.create_user(email, name[:60], generate_password_hash(password))
    sign_in_user(user["userId"], user["name"], email, "password")
    return None


def sign_in(email, password):
    # Returns an error message, or None if the sign-in worked.
    email = email.strip().lower()
    user = database.get_user_by_email(email)

    # The same message for "no such email" and "wrong password", so the page
    # never reveals which emails have accounts.
    if not user or not check_password_hash(user["passwordHash"], password):
        return "Email or password is incorrect."

    sign_in_user(user["userId"], user["name"], email, "password")
    return None


# ---------------------------------------------------------------------
# Continue with Microsoft (Microsoft Entra ID)
# ---------------------------------------------------------------------

def get_msal_app():
    return msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=AUTHORITY,
        client_credential=CLIENT_SECRET,
    )


def start_microsoft_login():
    # Builds the Microsoft sign-in link. The "flow" is saved in the session
    # so we can check that the reply really belongs to this sign-in.
    flow = get_msal_app().initiate_auth_code_flow(SCOPES, redirect_uri=REDIRECT_URI)
    session["auth_flow"] = flow
    return redirect(flow["auth_uri"])


def finish_microsoft_login():
    # Microsoft sends the user back here after they sign in.
    flow = session.pop("auth_flow", None)
    if not flow:
        return False

    try:
        result = get_msal_app().acquire_token_by_auth_code_flow(flow, request.args)
    except ValueError:
        # The reply didn't match the sign-in we started, so we reject it.
        return False

    if "error" in result:
        print("Microsoft sign-in failed:", result.get("error_description"))
        return False

    claims = result.get("id_token_claims", {})
    # "oid" is the user's permanent id from Microsoft. The "ms-" prefix keeps
    # it clearly separate from email accounts' ids.
    sign_in_user(
        "ms-" + (claims.get("oid") or claims.get("sub")),
        claims.get("name") or "there",
        claims.get("preferred_username") or "",
        "microsoft",
    )
    return True


# ---------------------------------------------------------------------
# Signing out and protecting routes
# ---------------------------------------------------------------------

def sign_out(home_url):
    # Returns where to send the user after signing out. Microsoft users are
    # also signed out of Microsoft for this app.
    provider = (session.get("user") or {}).get("provider")
    session.clear()
    if provider == "microsoft":
        return (
            "https://login.microsoftonline.com/common/oauth2/v2.0/logout"
            f"?post_logout_redirect_uri={quote(home_url, safe='')}"
        )
    return home_url


def current_user():
    return session.get("user")


def login_required(view):
    # Put @login_required above any route that needs a signed-in user.
    # Pages send the visitor to the sign-in page; API routes return an error.
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user():
            return view(*args, **kwargs)
        if request.path.startswith("/api/"):
            return jsonify({"error": "Please sign in again."}), 401
        return redirect(url_for("login_page"))

    return wrapper
