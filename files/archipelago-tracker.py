#!/usr/bin/env python3
"""Read-only web tracker for an Archipelago server.

Connects to the server as a "Tracker" client for every slot, keeps progress,
received items, hints and recent item sends in memory, and serves them as
JSON (/api/state) plus a single page (/). /api/state?spoilers also lists
each player's remaining locations and the items in them; /spoilers is a page
for those plus item search and the checks needed to reach an item.

Runs on the Archipelago release's own Python version and libraries, found via
$ARCHIPELAGO_DIR. Config is a JSON file in $CREDENTIALS_DIRECTORY/config:
{"server": "ws://127.0.0.1:38281", "password": "...", "slots": ["name", ...],
 "listen_host": "127.0.0.1", "listen_port": 38282,
 "seed_zip": "/path/AP_x.zip", "players_dir": "/path/players"}
"""
import asyncio
import collections
import concurrent.futures
import json
import os
import time
import urllib.parse
import uuid

import archipelago_spoilers

archipelago_spoilers.bootstrap(os.environ["ARCHIPELAGO_DIR"])

from websockets.asyncio.client import connect  # noqa: E402 - bundled with the release

FEED_LENGTH = 100
POLL_SECONDS = 30
CLIENT_GOAL = 30
HERE = os.path.dirname(os.path.abspath(__file__))
PAGES = {"/": "tracker.html", "/spoilers": "spoilers.html"}


class Tracker:
    def __init__(self, config):
        self.config = config
        self.names = {}  # game -> {"items": {id: name}, "locations": {id: name}}
        self.slots = {}  # slot id -> {"name", "game"}
        self.players = {}  # slot name -> progress dict
        self.feed = collections.deque(maxlen=FEED_LENGTH)
        self.seen_sends = set()
        self.online = set()
        self.server_up = False
        self.seed = None
        self.names_ready = asyncio.Event()
        self.spoilers = archipelago_spoilers.Spoilers(config["seed_zip"], config["players_dir"])
        # One thread: engine work is CPU-bound and shares one rebuilt world.
        self.engine = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def item_name(self, game, item_id):
        return self.names.get(game, {}).get("items", {}).get(item_id, f"Item {item_id}")

    def location_name(self, game, location_id):
        return self.names.get(game, {}).get("locations", {}).get(location_id, f"Location {location_id}")

    # Slot 0 is the server itself; it sends starting items.
    def game_of(self, slot):
        return "Archipelago" if slot == 0 else self.slots.get(slot, {}).get("game", "")

    def name_of(self, slot):
        return "Archipelago" if slot == 0 else self.slots.get(slot, {}).get("name", f"Slot {slot}")

    async def watch_slot(self, name, primary):
        while True:
            try:
                await self.session(name, primary)
            except Exception as e:  # server stopped, restarting, or refused us
                if primary:
                    self.server_up = False
                    self.online.clear()
                print(f"{name}: {type(e).__name__}: {e}", flush=True)
            await asyncio.sleep(10)

    async def session(self, name, primary):
        async with connect(self.config["server"], max_size=None) as ws:
            room = json.loads(await ws.recv())[0]  # RoomInfo
            packets = []
            if primary:
                self.server_up = True
                if room["seed_name"] != self.seed:  # new game: drop the old one's state
                    self.seed = room["seed_name"]
                    self.feed.clear()
                    self.seen_sends.clear()
                    self.online.clear()
                # Answered before Connected, so names are known when progress arrives.
                packets.append({"cmd": "GetDataPackage", "games": room["games"] + ["Archipelago"]})
            else:
                await self.names_ready.wait()
            packets.append({
                "cmd": "Connect", "password": self.config.get("password", ""),
                "game": "", "name": name, "uuid": str(uuid.uuid4()),
                "version": {"major": 0, "minor": 6, "build": 7, "class": "Version"},
                "items_handling": 0b111, "tags": ["Tracker"], "slot_data": False,
            })
            await ws.send(json.dumps(packets))
            poller = None
            try:
                async for message in ws:
                    for packet in json.loads(message):
                        await self.handle(ws, name, primary, packet)
                        if primary and packet["cmd"] == "Connected" and poller is None:
                            poller = asyncio.create_task(self.poll(ws))
            finally:
                if poller:
                    poller.cancel()

    async def handle(self, ws, name, primary, packet):
        cmd = packet["cmd"]
        if cmd == "DataPackage":
            for game, data in packet["data"]["games"].items():
                self.names[game] = {
                    "items": {v: k for k, v in data["item_name_to_id"].items()},
                    "locations": {v: k for k, v in data["location_name_to_id"].items()},
                }
            self.names_ready.set()
        elif cmd == "ConnectionRefused":
            raise ConnectionError(f"refused: {packet.get('errors')}")
        elif cmd == "Connected":
            self.slots.update({int(k): v for k, v in packet["slot_info"].items()})
            checked = set(packet["checked_locations"])
            self.players[name] = {
                "name": name, "game": self.game_of(packet["slot"]), "slot": packet["slot"],
                "team": packet["team"], "checked": checked,
                "total": len(checked) + len(packet["missing_locations"]),
                "received": [], "hints": [], "goal": False, "scouted": {},
            }
            # create_as_hint 0: look up contents without creating or announcing hints.
            await ws.send(json.dumps([{"cmd": "LocationScouts",
                                       "locations": packet["missing_locations"], "create_as_hint": 0}]))
        elif cmd == "RoomUpdate" and name in self.players:
            self.players[name]["checked"].update(packet.get("checked_locations", []))
        elif cmd == "ReceivedItems" and name in self.players:
            player = self.players[name]
            if packet["index"] == 0:
                player["received"] = []
            for item in packet["items"]:
                player["received"].append({
                    "item": self.item_name(player["game"], item["item"]),
                    "from": self.name_of(item["player"]),
                    "location": self.location_name(self.game_of(item["player"]), item["location"]),
                    "flags": item["flags"],
                })
        elif cmd == "LocationInfo" and name in self.players:
            player = self.players[name]
            for item in packet["locations"]:  # for scouts, "player" is the receiver
                player["scouted"][item["location"]] = {
                    "location": self.location_name(player["game"], item["location"]),
                    "item": self.item_name(self.game_of(item["player"]), item["item"]),
                    "for": self.name_of(item["player"]),
                    "flags": item["flags"],
                }
        elif cmd == "PrintJSON":
            self.on_print(packet)
        elif cmd == "Retrieved":
            self.on_retrieved(packet["keys"])

    def on_print(self, packet):
        kind = packet.get("type")
        if kind == "ItemSend":
            item = packet["item"]
            key = (item["player"], item["location"])
            if key in self.seen_sends:  # every involved slot's connection gets a copy
                return
            self.seen_sends.add(key)
            receiver = packet["receiving"]
            self.feed.appendleft({
                "time": time.time(),
                "from": self.name_of(item["player"]),
                "to": self.name_of(receiver),
                "item": self.item_name(self.game_of(receiver), item["item"]),
                "location": self.location_name(self.game_of(item["player"]), item["location"]),
                "flags": item["flags"],
            })
        elif kind in ("Join", "Part") and not {"Tracker", "TextOnly"} & set(packet.get("tags", [])):
            name = self.name_of(packet["slot"])
            (self.online.add if kind == "Join" else self.online.discard)(name)
        elif kind == "Goal":
            for player in self.players.values():
                if player["slot"] == packet.get("slot"):
                    player["goal"] = True

    def on_retrieved(self, keys):
        for player in self.players.values():
            suffix = f"{player['team']}_{player['slot']}"
            status = keys.get(f"_read_client_status_{suffix}")
            if status is not None:
                player["goal"] = status == CLIENT_GOAL
            hints = keys.get(f"_read_hints_{suffix}")
            if hints is not None:
                player["hints"] = [{
                    "item": self.item_name(self.game_of(h["receiving_player"]), h["item"]),
                    "for": self.name_of(h["receiving_player"]),
                    "location": self.location_name(self.game_of(h["finding_player"]), h["location"]),
                    "in_world_of": self.name_of(h["finding_player"]),
                    "found": h["found"],
                } for h in hints if h["receiving_player"] == player["slot"]]

    async def poll(self, ws):
        await asyncio.sleep(5)  # let the other slots' connections finish first
        while True:
            keys = []
            for p in self.players.values():
                keys += [f"_read_client_status_{p['team']}_{p['slot']}", f"_read_hints_{p['team']}_{p['slot']}"]
            if keys:
                await ws.send(json.dumps([{"cmd": "Get", "keys": keys}]))
            await asyncio.sleep(POLL_SECONDS)

    def checked(self):
        return {(p["slot"], loc) for p in self.players.values() for loc in p["checked"]}

    async def spoiler_api(self, path, params):
        engine = self.spoilers
        if engine.status != "ready":
            return {"status": engine.status, "error": engine.error}
        loop = asyncio.get_running_loop()
        if path == "/api/spoilers/search":
            results = await loop.run_in_executor(
                self.engine, engine.search, params.get("q", [""])[0], self.checked())
            return {"status": "ready", "results": results}
        if path == "/api/spoilers/path":
            key = (int(params["slot"][0]), int(params["location"][0]))
            if key not in engine.by_id:
                return {"status": "ready", "error": "unknown location"}
            return {"status": "ready", **await loop.run_in_executor(
                self.engine, engine.path, key[0], key[1], self.checked())}
        return {"status": "ready"}

    def state(self, spoilers=False):
        players = []
        for p in sorted(self.players.values(), key=lambda p: p["slot"]):
            player = {
                "name": p["name"], "game": p["game"], "online": p["name"] in self.online,
                "checked": len(p["checked"]), "total": p["total"], "goal": p["goal"],
                "received": p["received"], "hints": p["hints"],
            }
            if spoilers:
                player["remaining"] = sorted(
                    (v for k, v in p["scouted"].items() if k not in p["checked"]),
                    key=lambda v: v["location"])
            players.append(player)
        return {"server_up": self.server_up, "players": players, "feed": list(self.feed)}

    async def serve_http(self, reader, writer):
        try:
            request = (await reader.readline()).decode(errors="replace").split()
            while (await reader.readline()) not in (b"\r\n", b"\n", b""):
                pass
            path, _, query = (request[1] if len(request) > 1 else "/").partition("?")
            params = urllib.parse.parse_qs(query, keep_blank_values=True)
            if path == "/api/state":
                body = json.dumps(self.state("spoilers" in params)).encode()
                kind, status = "application/json", "200 OK"
            elif path.startswith("/api/spoilers/"):
                try:
                    body = json.dumps(await self.spoiler_api(path, params)).encode()
                    kind, status = "application/json", "200 OK"
                except (KeyError, ValueError):
                    body, kind, status = b"Bad request", "text/plain", "400 Bad Request"
            elif path in PAGES:
                with open(os.path.join(HERE, PAGES[path]), "rb") as f:
                    body, kind, status = f.read(), "text/html; charset=utf-8", "200 OK"
            else:
                body, kind, status = b"Not found", "text/plain", "404 Not Found"
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: {kind}\r\nContent-Length: {len(body)}\r\n"
                "Cache-Control: no-store\r\nConnection: close\r\n\r\n".encode() + body)
            await writer.drain()
        finally:
            writer.close()


async def main():
    with open(os.path.join(os.environ["CREDENTIALS_DIRECTORY"], "config")) as f:
        config = json.load(f)
    tracker = Tracker(config)
    asyncio.get_running_loop().run_in_executor(tracker.engine, tracker.spoilers.load)
    server = await asyncio.start_server(tracker.serve_http, config["listen_host"], config["listen_port"])
    tasks = [tracker.watch_slot(name, i == 0) for i, name in enumerate(config["slots"])]
    async with server:
        await asyncio.gather(server.serve_forever(), *tasks)


if __name__ == "__main__":
    asyncio.run(main())
