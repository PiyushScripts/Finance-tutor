# database.py
#
# Stores each user's expenses and monthly budget in Azure Cosmos DB.
#
# How the data is organised:
#   database "spendwise"
#     container "users"    - one document per email sign-up (never a plain password)
#     container "expenses" - one document per expense
#     container "budgets"  - one document per user per month, e.g. "2026-09"
#
# Both containers are partitioned by userId. Every read and write in this
# file includes the user's id, so one user can never see or change
# another user's data. Cosmos DB also encrypts everything at rest.

import os
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv
from azure.cosmos import CosmosClient, PartitionKey
from azure.cosmos.exceptions import CosmosResourceNotFoundError

load_dotenv()

# The connection is made the first time it's needed, so the website can
# still start even if the database settings aren't in .env yet.
_containers = None


def get_containers():
    global _containers
    if _containers is None:
        client = CosmosClient(os.getenv("COSMOS_ENDPOINT"), credential=os.getenv("COSMOS_KEY"))

        # Creates the database and containers the first time the app runs.
        # 400 RU/s shared across all three containers keeps us inside the free tier.
        database = client.create_database_if_not_exists("spendwise", offer_throughput=400)
        expenses = database.create_container_if_not_exists(
            id="expenses", partition_key=PartitionKey(path="/userId")
        )
        budgets = database.create_container_if_not_exists(
            id="budgets", partition_key=PartitionKey(path="/userId")
        )
        # Users are looked up by email when signing in, so the email is the
        # document id and the partition key.
        users = database.create_container_if_not_exists(
            id="users", partition_key=PartitionKey(path="/id")
        )
        _containers = {"expenses": expenses, "budgets": budgets, "users": users}
    return _containers


def clean_for_browser(doc):
    # Cosmos DB adds its own fields (starting with "_"). The website
    # doesn't need them, and it doesn't need the userId either.
    return {key: value for key, value in doc.items() if not key.startswith("_") and key != "userId"}


def list_expenses(user_id):
    # Only this user's partition is searched, which is fast and private.
    items = get_containers()["expenses"].query_items(
        query="SELECT * FROM c WHERE c.userId = @userId",
        parameters=[{"name": "@userId", "value": user_id}],
        partition_key=user_id,
    )
    return [clean_for_browser(item) for item in items]


def add_expense(user_id, expense):
    # expense has already been checked by app.py before it gets here.
    doc = {
        "id": str(uuid.uuid4()),
        "userId": user_id,
        "date": expense["date"],
        "merchant": expense.get("merchant"),
        "description": expense["description"],
        "amount": expense["amount"],
        "category": expense["category"],
        "type": expense["type"],
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    get_containers()["expenses"].create_item(doc)
    return clean_for_browser(doc)


def update_expense(user_id, expense_id, changes):
    # Reading with the user's own partition key means we can only ever find,
    # and so only ever change, this user's expenses.
    container = get_containers()["expenses"]
    try:
        doc = container.read_item(item=expense_id, partition_key=user_id)
    except CosmosResourceNotFoundError:
        return None

    for field in ("description", "amount", "category", "type", "date"):
        doc[field] = changes[field]
    container.replace_item(item=expense_id, body=doc)
    return clean_for_browser(doc)


def delete_expense(user_id, expense_id):
    try:
        get_containers()["expenses"].delete_item(item=expense_id, partition_key=user_id)
        return True
    except CosmosResourceNotFoundError:
        return False


def clear_expenses(user_id):
    container = get_containers()["expenses"]
    for expense in list_expenses(user_id):
        container.delete_item(item=expense["id"], partition_key=user_id)


def get_budgets(user_id):
    # Every month's budget for this user, as {"2026-09": 5000, ...}.
    items = get_containers()["budgets"].query_items(
        query="SELECT c.month, c.amount FROM c WHERE c.userId = @userId AND IS_DEFINED(c.month)",
        parameters=[{"name": "@userId", "value": user_id}],
        partition_key=user_id,
    )
    return {item["month"]: item["amount"] for item in items}


def set_budget(user_id, month, amount):
    # Each month's budget is its own document, with the month as its id.
    container = get_containers()["budgets"]
    if amount > 0:
        # upsert means "create it if it doesn't exist, otherwise replace it".
        container.upsert_item({"id": month, "userId": user_id, "month": month, "amount": amount})
    else:
        # Setting a budget of 0 removes that month's budget.
        try:
            container.delete_item(item=month, partition_key=user_id)
        except CosmosResourceNotFoundError:
            pass


# ---------------------------------------------------------------------
# Users who sign up with email and password
# ---------------------------------------------------------------------


def get_user_by_email(email):
    try:
        return get_containers()["users"].read_item(item=email, partition_key=email)
    except CosmosResourceNotFoundError:
        return None


def create_user(email, name, password_hash):
    # Only the password *hash* is stored, never the password itself.
    user = {
        "id": email,
        "userId": str(uuid.uuid4()),
        "name": name,
        "passwordHash": password_hash,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    get_containers()["users"].create_item(user)
    return user
