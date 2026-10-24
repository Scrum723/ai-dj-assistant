"""Optional YouTube/chat forwarder into the community Aggregator via POST /chat.

Prefer enabling community.adapters.youtube in settings (in-process pytchat adapter).
This script remains for terminal demo input or an external forwarder process.
"""

import logging
import os
import sys
import time

import requests
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv(os.path.expanduser("~/.env"))
load_dotenv()

BRAIN_URL = os.getenv("AI_DJ_BRAIN_URL", "http://127.0.0.1:8000")


def forward(author, message, platform="youtube"):
    try:
        requests.post(
            f"{BRAIN_URL}/chat",
            json={"author": author, "message": message, "platform": platform},
            timeout=5,
        )
    except Exception as e:
        logger.error("Forward failed: %s", e)


if __name__ == "__main__":
    video_id = sys.argv[1] if len(sys.argv) > 1 else None
    if video_id:
        requests.post(f"{BRAIN_URL}/agent/youtube", json={"video_id": video_id}, timeout=5)
        try:
            import pytchat
        except Exception as e:
            logger.error("pytchat missing: %s — enable community.adapters.youtube instead", e)
            sys.exit(1)
        chat = pytchat.create(video_id=video_id)
        logger.info("Forwarding YouTube chat %s to Aggregator via /chat", video_id)
        while chat.is_alive():
            for c in chat.get().sync_items():
                forward(c.author.name, c.message, "youtube")
            time.sleep(1)
    else:
        print("Type viewer chat as  Name: message")
        try:
            while True:
                line = input("> ").strip()
                if not line:
                    continue
                if ":" in line:
                    author, message = line.split(":", 1)
                else:
                    author, message = "You", line
                forward(author.strip(), message.strip(), "room")
        except (KeyboardInterrupt, EOFError):
            print("\nBye.")
