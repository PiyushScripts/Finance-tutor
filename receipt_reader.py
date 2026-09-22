# receipt_reader.py
#
# Reads a photo of a bill, a receipt or a handwritten list of expenses, and
# turns it into a list of expenses for the user to check.
#
# Two Azure services work together here, each doing what it's best at:
#
#   1. Azure Document Intelligence, "Read" model (the "eyes")
#      Microsoft's OCR model. It reads every line of text on the page,
#      printed or handwritten, and gives a confidence score for each word.
#      We use those scores to highlight rows the user should double-check.
#
#   2. Our gpt-4.1-mini model in Microsoft Foundry (the "brain")
#      OCR gives us plain lines of text. The model works out which lines are
#      purchases, with their amount, date, category, and whether each is a
#      need or a want, and skips totals, tax and payment lines.
#
# We started with Document Intelligence's prebuilt receipt model, but it is
# built for printed shop receipts: on a handwritten list it skipped a row
# and couldn't give each row its own date. The Read model plus the chat
# model handles both kinds of page.

import io
import os
import json
from dotenv import load_dotenv
from openai import OpenAI
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential

load_dotenv()

# Connection to Azure Document Intelligence (runs on our Azure resource).
# It's created the first time a bill is scanned, so the rest of the website
# still works if the key isn't in .env yet.
_docintel_client = None


def get_docintel_client():
    global _docintel_client
    if _docintel_client is None:
        _docintel_client = DocumentIntelligenceClient(
            endpoint=os.getenv("DOCINTEL_ENDPOINT"),
            credential=AzureKeyCredential(os.getenv("DOCINTEL_KEY")),
        )
    return _docintel_client

# Connection to our chat model in Foundry, used only for categories.
client = OpenAI(base_url=os.getenv("AZURE_ENDPOINT"), api_key=os.getenv("AZURE_API_KEY"))
deployment_name = os.getenv("DEPLOYMENT_NAME")

# The only categories the model is allowed to choose from. Keeping a fixed
# list means the tracker's totals always group neatly, instead of the model
# inventing "Food", "Foods" and "Groceries" for the same thing.
CATEGORIES = [
    "Groceries",
    "Eating out",
    "Snacks and drinks",
    "Transport",
    "Rent and bills",
    "Education",
    "Health",
    "Shopping",
    "Entertainment",
    "Loans and EMI",
    "Savings",
    "Other",
]


def read_lines(image_bytes):
    # Step 1: Document Intelligence reads all the text on the page.
    poller = get_docintel_client().begin_analyze_document(
        "prebuilt-read",
        body=io.BytesIO(image_bytes),
    )
    result = poller.result(timeout=60)

    pieces = []
    for page in result.pages or []:
        words = page.words or []
        for line in page.lines or []:
            # A piece's confidence is its least certain word. If one digit of
            # an amount was hard to read, the whole row should be checked.
            scores = [
                word.confidence
                for word in words
                if any(span.offset <= word.span.offset < span.offset + span.length for span in line.spans)
            ]
            box = line.polygon or [0] * 8
            ys = box[1::2]
            pieces.append({
                "text": line.content,
                "confidence": min(scores) if scores else 0,
                "page": page.page_number,
                "left": min(box[0::2]),
                "middle": (min(ys) + max(ys)) / 2,
                "height": max(ys) - min(ys),
            })

    return group_into_rows(pieces)


def group_into_rows(pieces):
    # On a page with columns far apart (like "CAR   12/7/2026   7,00,000"),
    # the OCR returns each column as a separate piece, and not always in row
    # order. So we rebuild the rows ourselves using where each piece sits on
    # the page: pieces at the same height belong to the same row, and within a
    # row they're read left to right. This way the model never has to guess
    # which amount goes with which item.
    pieces = sorted(pieces, key=lambda p: (p["page"], p["middle"]))
    rows = []

    for piece in pieces:
        last = rows[-1] if rows else None
        # Same row if it's on the same page and its middle is within half a
        # line's height of the row's middle.
        tolerance = max(piece["height"], last["height"] if last else 0) * 0.5
        if last and last["page"] == piece["page"] and abs(piece["middle"] - last["middle"]) <= tolerance:
            last["pieces"].append(piece)
        else:
            rows.append({"page": piece["page"], "middle": piece["middle"],
                         "height": piece["height"], "pieces": [piece]})

    lines = []
    for row in rows:
        row["pieces"].sort(key=lambda p: p["left"])
        lines.append({
            "text": " | ".join(p["text"] for p in row["pieces"]),
            "confidence": min(p["confidence"] for p in row["pieces"]),
        })
    return lines


EXTRACT_INSTRUCTIONS = (
    "You read the text of a shop bill, a receipt, or a handwritten list of expenses. "
    "The text is given as numbered lines. Reply with only a JSON object, no other text, like:\n"
    '{"merchant": "DMart", "total": 312, "items": [{"lines": [3], "description": "Milk", '
    '"amount": 64, "date": "2026-09-18", "category": "Groceries", "type": "need"}]}\n\n'
    "Rules:\n"
    "- Each numbered line is one row of the page. Pieces of the same row are separated by \" | \".\n"
    "- One item for each thing that was bought. Include every purchase, even short ones. "
    "Take each item's name, date and amount from its own row, never from another row.\n"
    "- Write the description in normal capitalisation, like \"Glasses\", not \"GLASSES\".\n"
    "- Never include totals, subtotals, taxes, GST, discounts, change, payment method or "
    "balance lines as items.\n"
    "- amount is a plain number. Indian digit grouping like 1,00,000 means 100000.\n"
    "- Dates are written day/month/year, the Indian way, so 12/7/2026 means 12 July 2026. "
    "Write dates as YYYY-MM-DD. If an item has its own date, use it. Otherwise use the "
    "bill's date. If there is no date at all, use null.\n"
    f"- category must be exactly one of: {', '.join(CATEGORIES)}. Guide:\n"
    "  Transport: car, bike, scooter, fuel, cab, auto, metro, bus, train, flights.\n"
    "  Health: glasses, medicines, doctor, hospital, gym, pharmacy.\n"
    "  Education: fees, books, courses, stationery, and a laptop or tablet for study.\n"
    "  Shopping: clothes, shoes, furniture like a table or chair, gadgets, home items.\n"
    "  Eating out: meals, restaurants, canteen, food delivery, or just \"food\".\n"
    "  Groceries: vegetables, fruit, milk, rice, bread, household supplies.\n"
    "  Snacks and drinks: soft drinks, juice, chai, coffee, chips, chocolates.\n"
    "  Rent and bills: rent, electricity, water, internet, mobile recharge.\n"
    '- type is "need" or "want".\n'
    '- lines lists the line numbers the item came from.\n'
    "- merchant is the shop name if there is one, otherwise null. total is the bill's total "
    "if one is written, otherwise null."
)


def parse_json(text):
    # Models sometimes wrap JSON in ``` marks, so we strip those first.
    text = text.strip().strip("`")
    if text.startswith("json"):
        text = text[4:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def to_number(value):
    try:
        return round(float(str(value).replace(",", "").replace("₹", "").strip()), 2)
    except (TypeError, ValueError):
        return None


def extract_expenses(lines):
    # Step 2: the chat model turns lines of text into structured expenses.
    if not lines:
        return {"merchant": None, "total": None, "items": []}

    numbered = "\n".join(f"{number}: {line['text']}" for number, line in enumerate(lines, start=1))
    response = client.responses.create(
        model=deployment_name,
        instructions=EXTRACT_INSTRUCTIONS,
        input=numbered,
    )
    data = parse_json(response.output_text)

    items = []
    for raw in data.get("items") or []:
        if not isinstance(raw, dict):
            continue

        # The row's confidence comes from Azure's OCR, not from the model:
        # the least certain of the lines this item was read from.
        used = [n for n in raw.get("lines") or [] if isinstance(n, int) and 1 <= n <= len(lines)]
        confidence = min((lines[n - 1]["confidence"] for n in used), default=0)

        category = raw.get("category")
        date = raw.get("date")
        items.append({
            "description": str(raw.get("description") or "").strip()[:60],
            "amount": to_number(raw.get("amount")),
            "date": date if isinstance(date, str) and len(date) == 10 else None,
            "category": category if category in CATEGORIES else "Other",
            "type": "want" if raw.get("type") == "want" else "need",
            "confidence": round(confidence, 2),
        })

    merchant = data.get("merchant")
    return {
        "merchant": str(merchant)[:60] if merchant else None,
        "total": to_number(data.get("total")) if data.get("total") is not None else None,
        "items": items,
    }


def scan_receipt(image_bytes):
    # The one function app.py calls: read the page, then understand it.
    return extract_expenses(read_lines(image_bytes))
