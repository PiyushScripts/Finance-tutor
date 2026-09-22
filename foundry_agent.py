# foundry_agent.py
#
# Connects the website's chat box to our SpendWise agent in Microsoft Foundry.
#
# All the intelligence lives in Foundry, not in this file:
#   - the agent's instructions (how SpendWise behaves)
#   - the knowledge base (Foundry IQ on Azure AI Search, 47 finance notes)
#   - Code interpreter, which runs our calculators.py for exact numbers
#     and draws charts
#
# This file only sends the user's message to the agent and brings back
# the reply, plus any chart images the agent made.

import os
import re
import json
import base64
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

load_dotenv()

# The connection is created the first time someone chats, not when the
# website starts. That way the calculators and expense tracker still work
# even if the Foundry connection isn't set up on this computer yet.
_openai_client = None


def get_client():
    global _openai_client
    if _openai_client is None:
        # DefaultAzureCredential signs in using "az login" on this computer,
        # so no secret key for the agent is stored in our code or .env file.
        project_client = AIProjectClient(
            endpoint=os.getenv("FOUNDRY_PROJECT_ENDPOINT"),
            credential=DefaultAzureCredential(),
        )
        _openai_client = project_client.get_openai_client()
    return _openai_client

AGENT_NAME = os.getenv("AGENT_NAME")
AGENT_VERSION = os.getenv("AGENT_VERSION")


def agent_reference():
    # Tells Foundry which agent should answer. If a version is set in .env
    # we use that exact version, so a half-finished edit in the portal can't
    # change the live website by accident.
    reference = {"name": AGENT_NAME, "type": "agent_reference"}
    if AGENT_VERSION:
        reference["version"] = AGENT_VERSION
    return {"agent_reference": reference}


def start_conversation():
    # A Foundry conversation stores the chat history on Azure, so the agent
    # remembers earlier messages (like which option the user picked).
    return get_client().conversations.create().id


def clean_text(text):
    # The agent sometimes adds links to files inside its sandbox, like
    # [chart.png](sandbox:/mnt/data/chart.png). Those links don't work in a
    # browser, and we show the chart as an image anyway, so we remove them.
    text = re.sub(r"\[([^\]]*)\]\(sandbox:[^)]*\)", r"\1", text)
    # Knowledge base citations can appear as markers like 【4:0†source】.
    text = re.sub(r"\s*【[^】]*】", "", text)
    return text.strip()


def get_chart_images(response):
    # When Code interpreter draws a chart, the reply contains a reference to
    # the image file. We download each one and turn it into a data URL so
    # the browser can show it directly.
    images = []

    for item in response.output:
        if item.type != "message":
            continue

        for part in item.content:
            for note in getattr(part, "annotations", None) or []:
                if note.type != "container_file_citation":
                    continue
                if not note.filename.lower().endswith((".png", ".jpg", ".jpeg")):
                    continue

                try:
                    file = get_client().containers.files.content.retrieve(
                        file_id=note.file_id,
                        container_id=note.container_id,
                    )
                    encoded = base64.b64encode(file.read()).decode()
                    images.append(f"data:image/png;base64,{encoded}")
                except Exception as error:
                    # A missing chart shouldn't break the whole answer.
                    print("Could not download chart:", error)

    return images


# The agent is instructed to add a line like this whenever the user reports
# money they spent:
#   EXPENSE_JSON: {"description": "Food", "amount": 200, "category": "Eating out", ...}
EXPENSE_LINE = re.compile(r"^\s*EXPENSE_JSON:\s*(\{.*\})\s*$", re.MULTILINE)


def extract_expenses(text):
    # Finds every EXPENSE_JSON line, reads the JSON on it, and removes the
    # line from the reply so the user only sees the friendly message.
    expenses = []
    for match in EXPENSE_LINE.finditer(text):
        try:
            expenses.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            pass  # a broken line is just skipped
    return EXPENSE_LINE.sub("", text).strip(), expenses


def ask_agent(conversation_id, message):
    # Sends one message to the SpendWise agent inside the given conversation.
    response = get_client().responses.create(
        conversation=conversation_id,
        input=[{"role": "user", "content": message}],
        extra_body=agent_reference(),
    )
    reply, expenses = extract_expenses(clean_text(response.output_text))
    return reply, get_chart_images(response), expenses


# Lets us test the agent from the terminal: python foundry_agent.py
if __name__ == "__main__":
    conversation_id = start_conversation()
    print("SpendWise (Foundry agent) - type 'quit' to exit\n")

    while True:
        message = input("You: ")
        if message.lower() in ("quit", "exit"):
            break
        reply, images, expenses = ask_agent(conversation_id, message)
        print("\nSpendWise:", reply)
        if images:
            print(f"(plus {len(images)} chart image)")
        for expense in expenses:
            print("(expense detected:", expense, ")")
        print()
