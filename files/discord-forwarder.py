#!/usr/bin/env python3
"""Post Archipelago item sends and goals to a Discord webhook.

Reads ArchipelagoServer log lines on stdin (from journalctl) and pings the
receiving player. Config is a JSON file in $CREDENTIALS_DIRECTORY/config:
{"webhook_url": "...", "mentions": {"SlotName": "discord user id"}}
"""
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.request

BATCH_SECONDS = 3  # Discord allows ~30 webhook posts per minute.
MESSAGE_LIMIT = 2000

SENT = re.compile(r"^\(Team #\d+\) (.+?) sent (.+) to (.+?) \((.*)\)$")
GOAL = re.compile(r"^(.+?) \(Team #\d+\) has completed their goal\.$")


def format_line(line, mentions):
    if m := SENT.match(line):
        sender, item, receiver, location = m.groups()
        if sender == receiver:  # Own finds are logged as "X sent Y to X".
            return None
        who = f"<@{mentions[receiver]}>" if receiver in mentions else f"**{receiver}**"
        return f"{who} got **{item}** from {sender} ({location})"
    if m := GOAL.match(line):
        return f"**{m.group(1)}** has completed their goal!"
    return None


def chunks(messages):
    out, current = [], ""
    for message in messages:
        if current and len(current) + 1 + len(message) > MESSAGE_LIMIT:
            out.append(current)
            current = message
        else:
            current = f"{current}\n{message}" if current else message
    if current:
        out.append(current)
    return out


def post(url, content):
    body = json.dumps({"content": content, "allowed_mentions": {"parse": ["users"]}}).encode()
    request = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "User-Agent": "archipelago-discord-forwarder",
    })
    for _ in range(5):
        try:
            urllib.request.urlopen(request, timeout=10).close()
            return
        except urllib.error.HTTPError as e:
            if e.code != 429:
                print(f"webhook returned {e.code}: {e.read()[:200]!r}", flush=True)
                return
            time.sleep(float(json.load(e).get("retry_after", 1)))
        except urllib.error.URLError as e:
            print(f"webhook unreachable: {e.reason}", flush=True)
            time.sleep(5)
    print("dropped a message after 5 attempts", flush=True)


def main():
    with open(os.path.join(os.environ["CREDENTIALS_DIRECTORY"], "config")) as f:
        config = json.load(f)
    mentions = config.get("mentions", {})

    lines = queue.Queue()

    def read():
        for line in sys.stdin:
            lines.put(line.rstrip("\n"))
        lines.put(None)

    threading.Thread(target=read, daemon=True).start()

    done = False
    while not done:
        time.sleep(BATCH_SECONDS)
        batch = []
        while not lines.empty():
            line = lines.get()
            if line is None:
                done = True
                break
            if message := format_line(line, mentions):
                batch.append(message)
        for content in chunks(batch):
            post(config["webhook_url"], content)


if __name__ == "__main__":
    main()
