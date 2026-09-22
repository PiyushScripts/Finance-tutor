# app.py
#
# The web server for SpendWise. It does four jobs:
#
#   1. Signs users in, with email and password or with Microsoft
#   2. Serves the website (sidebar, chat box, calculators, expense tracker)
#   3. Passes chat messages to our SpendWise agent in Microsoft Foundry
#   4. Saves each user's expenses in Azure Cosmos DB, and runs the
#      calculator cards and the receipt scanner
#
# We used Flask because it is basically just Python that can also serve
# web pages, which keeps the whole project in one language.

import os
import re
from datetime import date, timedelta
from flask import Flask, render_template, request, jsonify, redirect, url_for

from calculators import (
    calculate_emi,
    calculate_rd,
    calculate_fd,
    calculate_savings_plan,
    calculate_budget_split,
    calculate_sip,
)
from foundry_agent import start_conversation, ask_agent
from receipt_reader import scan_receipt, CATEGORIES
from auth import (
    check_email,
    sign_up,
    sign_in,
    start_microsoft_login,
    finish_microsoft_login,
    sign_out,
    current_user,
    login_required,
    MICROSOFT_ENABLED,
)
import database

app = Flask(__name__)

# Flask needs a secret key to sign session cookies. In a real product
# this would always come from the environment, never have a fallback.
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-this")

# The sign-in cookie can't be read by JavaScript (HttpOnly is Flask's
# default), and "Lax" stops other websites from sending requests with it.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

@app.before_request
def use_localhost():
    # Microsoft sends users back to "localhost", so the site must also be
    # opened on localhost, otherwise the browser treats them as two different
    # sites and the sign-in cookie gets lost. This quietly fixes 127.0.0.1.
    if request.host.startswith("127.0.0.1"):
        return redirect(request.url.replace("127.0.0.1", "localhost", 1))


# Links each signed-in user to their own Foundry conversation.
# The key is the user's Microsoft id, and the value is the id of their
# conversation in Foundry. The chat history itself is stored by Foundry
# Agent Service, not by us.
#
# This dictionary is kept in memory, so after a server restart users
# simply get a fresh conversation.
conversations = {}

# Everything shown in the left sidebar. Each term has a question attached
# to it, so clicking a term just sends that question to the tutor. The
# categories match the folders inside our knowledge/ folder.
GLOSSARY = [
    {
        "label": "Budgeting",
        "terms": [
            {"label": "Budget basics", "question": "What is a budget?"},
            {"label": "50/30/20 rule", "question": "What is the 50/30/20 budgeting rule?"},
            {"label": "Needs vs wants", "question": "What's the difference between needs and wants?"},
            {"label": "Expense tracking", "question": "How do I track my expenses?"},
            {"label": "Savings rate", "question": "What is a savings rate?"},
        ],
    },
    {
        "label": "Saving",
        "terms": [
            {"label": "Why save money", "question": "Why should I save money?"},
            {"label": "Savings goals", "question": "How do I set a savings goal?"},
            {"label": "Emergency fund", "question": "What is an emergency fund?"},
            {"label": "Simple interest", "question": "What is simple interest?"},
            {"label": "Compound interest", "question": "What is compound interest?"},
            {"label": "Future value", "question": "What is future value?"},
        ],
    },
    {
        "label": "Loans & EMI",
        "terms": [
            {"label": "Loan basics", "question": "What are the basics of a loan?"},
            {"label": "EMI", "question": "What is an EMI?"},
            {"label": "Amortization", "question": "What is amortization?"},
        ],
    },
    {
        "label": "Banking basics",
        "terms": [
            {"label": "Bank accounts", "question": "What types of bank accounts are there?"},
            {"label": "Fixed deposit", "question": "What is a fixed deposit?"},
            {"label": "Recurring deposit", "question": "What is a recurring deposit?"},
        ],
    },
    {
        "label": "Credit cards",
        "terms": [
            {"label": "How credit cards work", "question": "How do credit cards work?"},
            {"label": "Credit score", "question": "What is a credit score?"},
            {"label": "Credit utilization", "question": "What is credit utilization?"},
            {"label": "Debt traps", "question": "How do I avoid a debt trap?"},
        ],
    },
    {
        "label": "Student finance",
        "terms": [
            {"label": "Student budgeting", "question": "How should I budget as a student?"},
            {"label": "Pocket money & stipends", "question": "How do I manage pocket money or a stipend?"},
            {"label": "Tuition & living costs", "question": "How do I plan for tuition and living expenses?"},
            {"label": "Education loans", "question": "What is an education loan?"},
            {"label": "Part-time income", "question": "How do I manage part-time income?"},
            {"label": "Lending to friends", "question": "How should I handle lending or borrowing money with friends?"},
        ],
    },
    {
        "label": "Financial basics",
        "terms": [
            {"label": "Inflation", "question": "What is inflation?"},
            {"label": "Net worth", "question": "What is net worth?"},
            {"label": "Time value of money", "question": "What is the time value of money?"},
            {"label": "Financial goals", "question": "How do I set financial goals?"},
            {"label": "Common mistakes", "question": "What are common financial mistakes to avoid?"},
            {"label": "Scams & safety", "question": "How do I protect myself from financial scams?"},
        ],
    },
    {
        "label": "Investing",
        "terms": [
            {"label": "Stocks", "question": "What are stocks and how do they work?"},
            {"label": "SIP", "question": "What is a SIP and how does it work?"},
            {"label": "IPO", "question": "What is an IPO?"},
            {"label": "Mutual funds", "question": "What are mutual funds?"},
            {"label": "Bonds", "question": "What are bonds?"},
            {"label": "Gold", "question": "How does investing in gold work?"},
            {"label": "PPF & NPS", "question": "What are PPF and NPS?"},
        ],
    },
    {
        "label": "Insurance",
        "terms": [
            {"label": "Insurance basics", "question": "What is insurance and how does it work?"},
        ],
    },
    {
        "label": "Ways to pay",
        "terms": [
            {"label": "Cash", "question": "What are the limits and rules around cash payments?"},
            {"label": "UPI", "question": "What is UPI and what are its limits?"},
            {"label": "Debit card", "question": "How do debit card payments work?"},
            {"label": "Credit card", "question": "How do credit card payments work?"},
            {"label": "NEFT", "question": "What is NEFT and what are its limits?"},
            {"label": "RTGS", "question": "What is RTGS and what are its limits?"},
            {"label": "IMPS", "question": "What is IMPS and what are its limits?"},
        ],
    },
]

@app.route("/")
@login_required
def home():
    # Loads index.html and passes the glossary into it, so the sidebar
    # is built from the Python list above rather than being typed out
    # by hand in the HTML.
    # The category list is passed in too, so the expense tracker's
    # dropdowns always match what the AI is allowed to choose.
    return render_template("index.html", glossary=GLOSSARY, categories=CATEGORIES, user=current_user())


# ---------------------------------------------------------------------
# Sign in, sign up and sign out (see auth.py)
# ---------------------------------------------------------------------


@app.route("/login", methods=["GET", "POST"])
def login_page():
    # One form that works in steps, like Google or Microsoft sign-in:
    #   step "email"    - the user types their email
    #   step "password" - the email has an account, so we ask for the password
    #   step "create"   - no account yet, so we show the sign-up fields instead
    if current_user():
        return redirect(url_for("home"))

    step = "email"
    error = None
    email = name = ""

    if request.method == "POST":
        step = request.form.get("step", "email")
        email = request.form.get("email", "")
        name = request.form.get("name", "")

        try:
            if step == "email":
                email, error, exists = check_email(email)
                if not error:
                    step = "password" if exists else "create"

            elif step == "password":
                error = sign_in(email, request.form.get("password", ""))
                if not error:
                    return redirect(url_for("home"))

            elif step == "create":
                error = sign_up(name, email, request.form.get("password", ""),
                                request.form.get("confirm", ""))
                if not error:
                    return redirect(url_for("home"))
        except Exception as problem:
            print("Sign-in failed:", problem)
            error = "Something went wrong on our side. Please try again."

    elif request.args.get("error") == "microsoft":
        error = "Microsoft sign-in didn't complete. Please try again."
    elif request.args.get("error") == "microsoft-off":
        error = "Microsoft sign-in isn't set up yet. Please use your email for now."

    return render_template("login.html", step=step, error=error, email=email, name=name)


@app.route("/signup")
def signup_page():
    # Kept so old links still work. Sign-up now happens inside /login.
    return redirect(url_for("login_page"))


@app.route("/auth/microsoft")
def auth_microsoft():
    if not MICROSOFT_ENABLED:
        return redirect(url_for("login_page", error="microsoft-off"))
    return start_microsoft_login()


@app.route("/auth/callback")
def auth_callback():
    if finish_microsoft_login():
        return redirect(url_for("home"))
    return redirect(url_for("login_page", error="microsoft"))


@app.route("/logout")
def logout():
    user = current_user()
    if user:
        conversations.pop(user["id"], None)
    return redirect(sign_out(url_for("login_page", _external=True)))


@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    # Called whenever someone sends a message or clicks a sidebar term.
    data = request.get_json()
    message = (data.get("message") or "").strip()

    if not message:
        return jsonify({"error": "Empty message"}), 400

    user_id = current_user()["id"]

    try:
        # First message from this user: start a new Foundry conversation.
        if user_id not in conversations:
            conversations[user_id] = start_conversation()

        reply, images, raw_expenses = ask_agent(conversations[user_id], message)
    except Exception as error:
        print("Agent call failed:", error)
        return jsonify({"error": "The tutor is unavailable right now."}), 500

    # Expenses the agent spotted in the message ("I spent 200 on food").
    # The browser sets allow_expenses to false when it sends a spending
    # summary for advice, so those numbers never get logged a second time.
    saved = []
    if data.get("allow_expenses", True):
        try:
            for raw in raw_expenses:
                expense = clean_expense(raw)
                if expense:
                    saved.append(database.add_expense(user_id, expense))
        except Exception as error:
            print("Saving chat expense failed:", error)

    return jsonify({"reply": reply, "images": images, "expenses": saved})


def clean_expense(raw):
    # Every expense is checked here before it's saved, whether it came from
    # the agent, a scanned bill or the user typing it in. We never trust
    # input blindly: the amount must be a real positive number, the category
    # must be one of our fixed list, and the date must be a real date.
    try:
        amount = round(float(raw.get("amount")), 2)
    except (TypeError, ValueError, AttributeError):
        return None
    if amount <= 0 or amount > 10_000_000:
        return None

    description = str(raw.get("description") or "").strip()[:60] or "Expense"
    category = raw.get("category")
    merchant = raw.get("merchant")

    return {
        "description": description,
        "amount": amount,
        "category": category if category in CATEGORIES else "Other",
        "type": "want" if raw.get("type") == "want" else "need",
        "date": resolve_date(raw.get("date")),
        "merchant": str(merchant)[:60] if merchant else None,
    }


def resolve_date(value):
    # The agent may say "today" or "yesterday". Anything that isn't a real
    # YYYY-MM-DD date becomes today.
    value = str(value or "today").strip().lower()
    if value == "yesterday":
        return (date.today() - timedelta(days=1)).isoformat()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
    return date.today().isoformat()


@app.route("/api/chat/reset", methods=["POST"])
@login_required
def reset_chat():
    # Used by the "New chat" button. Forgetting the conversation id means
    # the next message starts a brand new Foundry conversation.
    conversations.pop(current_user()["id"], None)
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------
# Calculator routes
#
# There is one route per calculator card. They all do the same three
# things: read the numbers the user typed, call the matching function
# from calculators.py, and send the answer back.
#
# We kept them as separate routes rather than combining them into one
# clever generic route, because this way each calculator is easy to
# find, easy to read and easy to change on its own.
# ---------------------------------------------------------------------


@app.route("/api/calculate/emi", methods=["POST"])
@login_required
def api_emi():
    data = request.get_json()
    # Values arrive from the web page as text, so we convert them to
    # numbers before doing any maths with them.
    result = calculate_emi(
        float(data["principal"]),
        float(data["rate"]),
        int(data["tenure"]),
    )
    return jsonify({"result": result})


@app.route("/api/calculate/rd", methods=["POST"])
@login_required
def api_rd():
    data = request.get_json()
    result = calculate_rd(
        float(data["monthly_deposit"]),
        float(data["rate"]),
        int(data["months"]),
    )
    return jsonify({"result": result})


@app.route("/api/calculate/fd", methods=["POST"])
@login_required
def api_fd():
    data = request.get_json()
    result = calculate_fd(
        float(data["principal"]),
        float(data["rate"]),
        float(data["years"]),
    )
    return jsonify({"result": result})


@app.route("/api/calculate/savings-goal", methods=["POST"])
@login_required
def api_savings_goal():
    data = request.get_json()
    result = calculate_savings_plan(
        float(data["goal_amount"]),
        int(data["months"]),
    )
    return jsonify({"result": result})


@app.route("/api/calculate/budget-split", methods=["POST"])
@login_required
def api_budget_split():
    data = request.get_json()
    split = calculate_budget_split(float(data["income"]))

    # This is the one calculator that returns three numbers instead of
    # one, so we format them into a single line here for the page to show.
    result_text = (
        f"Needs: ₹{split['needs']:.2f}  |  "
        f"Wants: ₹{split['wants']:.2f}  |  "
        f"Savings: ₹{split['savings']:.2f}"
    )
    return jsonify({"result": result_text})


@app.route("/api/calculate/sip", methods=["POST"])
@login_required
def api_sip():
    data = request.get_json()
    result = calculate_sip(
        float(data["monthly_investment"]),
        float(data["rate"]),
        float(data["years"]),
    )
    return jsonify({"result": result})


@app.route("/api/receipt/scan", methods=["POST"])
@login_required
def api_scan_receipt():
    # Called when someone uploads a bill in the Expense Tracker. It only
    # returns what was read. Nothing is saved here, because the user checks
    # and confirms everything first, then the website saves the confirmed
    # rows through /api/expenses.
    uploaded = request.files.get("receipt")
    if not uploaded:
        return jsonify({"error": "No file uploaded"}), 400

    try:
        receipt = scan_receipt(uploaded.read())
    except Exception as error:
        # Printed in the terminal for us, with a friendly message for the user.
        print("Receipt scan failed:", error)
        return jsonify({"error": "Couldn't read this bill. Try a clearer, well-lit photo."}), 500

    return jsonify(receipt)


# ---------------------------------------------------------------------
# Expenses and budget (stored in Azure Cosmos DB, see database.py)
#
# The user id always comes from the signed-in session, never from the
# browser, so nobody can ask for someone else's data.
# ---------------------------------------------------------------------


@app.route("/api/expenses", methods=["GET"])
@login_required
def api_list_expenses():
    user_id = current_user()["id"]
    try:
        return jsonify({
            "expenses": database.list_expenses(user_id),
            "budgets": database.get_budgets(user_id),
        })
    except Exception as error:
        print("Loading expenses failed:", error)
        return jsonify({"error": "Couldn't load your expenses."}), 500


@app.route("/api/expenses", methods=["POST"])
@login_required
def api_add_expenses():
    user_id = current_user()["id"]
    items = (request.get_json() or {}).get("expenses", [])[:100]

    try:
        saved = []
        for raw in items:
            expense = clean_expense(raw)
            if expense:
                saved.append(database.add_expense(user_id, expense))
        return jsonify({"expenses": saved})
    except Exception as error:
        print("Saving expenses failed:", error)
        return jsonify({"error": "Couldn't save your expenses."}), 500


@app.route("/api/expenses/<expense_id>", methods=["PATCH"])
@login_required
def api_update_expense(expense_id):
    # Used by the Edit button. The new values go through the same checks
    # as any new expense before they're saved.
    raw = request.get_json() or {}
    if not str(raw.get("description") or "").strip():
        return jsonify({"error": "Please give the expense a name."}), 400

    expense = clean_expense(raw)
    if not expense:
        return jsonify({"error": "Please enter an amount above zero."}), 400

    try:
        updated = database.update_expense(current_user()["id"], expense_id, expense)
    except Exception as error:
        print("Updating expense failed:", error)
        return jsonify({"error": "Couldn't save your changes."}), 500

    if not updated:
        return jsonify({"error": "That expense no longer exists."}), 404
    return jsonify({"expense": updated})


@app.route("/api/expenses/<expense_id>", methods=["DELETE"])
@login_required
def api_delete_expense(expense_id):
    try:
        database.delete_expense(current_user()["id"], expense_id)
        return jsonify({"status": "ok"})
    except Exception as error:
        print("Deleting expense failed:", error)
        return jsonify({"error": "Couldn't delete that expense."}), 500


@app.route("/api/expenses", methods=["DELETE"])
@login_required
def api_clear_expenses():
    try:
        database.clear_expenses(current_user()["id"])
        return jsonify({"status": "ok"})
    except Exception as error:
        print("Clearing expenses failed:", error)
        return jsonify({"error": "Couldn't clear your expenses."}), 500


@app.route("/api/budget", methods=["PUT"])
@login_required
def api_set_budget():
    # Sets the budget for one month, sent as "2026-09".
    data = request.get_json() or {}
    month = str(data.get("month") or "")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        return jsonify({"error": "Please pick a month first."}), 400

    try:
        amount = round(float(data.get("amount", 0)), 2)
    except (TypeError, ValueError):
        return jsonify({"error": "Please enter a valid amount."}), 400
    amount = max(0, amount)

    try:
        database.set_budget(current_user()["id"], month, amount)
        return jsonify({"month": month, "budget": amount})
    except Exception as error:
        print("Saving budget failed:", error)
        return jsonify({"error": "Couldn't save your budget."}), 500


if __name__ == "__main__":
    # debug=True reloads the server automatically when we save a file,
    # which was useful while building. It should be turned off if this
    # were ever put online.
    app.run(debug=True, port=5000)
