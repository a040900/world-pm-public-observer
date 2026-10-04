"""Prospective public quote journal for the frozen subsecond R6 study.

No orders, wallet, signing or transaction APIs. Without --capture the CLI only
validates configuration, with zero network calls. Endpoint/identity/time binding
must be supplied by the operator after authority approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

SCHEMA = "WORLD_PM_SUBSECOND_R6_CAPTURE_R1"
SECONDS = 12 * 3600


class ClockReliability:
    """Observe wall/monotonic pairs; never infer reliability from a fixed offset."""

    def __init__(self, clock=time):
        self.clock = clock
        self.wall_resolution = getattr(getattr(clock, "get_clock_info", lambda _: None)("time"),
                                       "resolution", 1e-9)
        self.monotonic_resolution = getattr(
            getattr(clock, "get_clock_info", lambda _: None)("monotonic"),
            "resolution", 1e-9)
        self.samples = 0
        self.errors = []
        self.first = None
        self.last = None

    def observe(self, wall=None, monotonic=None):
        wall = float(self.clock.time() if wall is None else wall)
        monotonic = float(self.clock.monotonic() if monotonic is None else monotonic)
        current = {"wall": wall, "monotonic": monotonic}
        if self.first is None:
            self.first = current
        elif wall < self.last["wall"]:
            self.errors.append("WALL_CLOCK_BACKWARD")
        elif (wall == self.last["wall"] and monotonic > self.last["monotonic"]
              and monotonic - self.last["monotonic"] > max(1.0, self.wall_resolution * 4)):
            self.errors.append("WALL_CLOCK_FROZEN_INTERVAL")
        self.last = current
        self.samples += 1
        return current

    def report(self):
        return {
            "reliable": bool(self.samples and not self.errors),
            "errors": list(dict.fromkeys(self.errors)),
            "wallResolution": self.wall_resolution,
            "monotonicResolution": self.monotonic_resolution,
            "samples": self.samples,
            "first": self.first,
            "last": self.last,
        }


def valid_price(bid, ask):
    try:
        bid, ask = float(bid), float(ask)
        if math.isfinite(bid) and math.isfinite(ask) and 0 <= bid <= ask <= 1:
            return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2, "spread": ask - bid}
    except (TypeError, ValueError):
        pass
    return {"bid": None, "ask": None, "mid": None, "spread": None}


def source_seconds(value):
    if value is None:
        return None
    value = float(value)
    return value / 1000 if value > 1e11 else value


def at_path(value, path):
    for key in path:
        value = value[int(key)] if isinstance(value, list) else value[key]
    if isinstance(value, str):
        value = json.loads(value)
    return value


def format_values(value, values):
    if isinstance(value, str):
        if value in {"{start_ts}", "{end_ts}", "{query_start}", "{query_end}"}:
            return int(values[value[1:-1]])
        return value.format_map(values)
    if isinstance(value, dict):
        return {k: format_values(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [format_values(v, values) for v in value]
    return value


def http_json(url, *, method="GET", body=None, timeout=5):
    # Only market-data reads. JSON-RPC write methods are not supported here.
    headers = {"Accept": "application/json", "User-Agent": "world-pm-subsecond-r6/1"}
    data = None if body is None else json.dumps(body).encode()
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode())


def validate_config(config):
    if config.get("schemaVersion") != SCHEMA:
        raise ValueError("CONFIG_SCHEMA_INVALID")
    start, end = float(config["startTs"]), float(config["endTs"])
    if not math.isfinite(start) or start % 300 or end != start + SECONDS:
        raise ValueError("EXACT_12H_UTC_MARKET_ALIGNED_PERIOD_REQUIRED")
    markets = config["markets"]
    if len(markets) != SECONDS // 300:
        raise ValueError("EXACT_144_FROZEN_MARKET_IDENTITIES_REQUIRED")
    for i, market in enumerate(markets):
        if market["startTs"] != start + i * 300 or market["endTs"] != start + (i + 1) * 300:
            raise ValueError("MARKET_SCHEDULE_NOT_CONTIGUOUS")
        if not market.get("worldTicker"):
            raise ValueError("EXACT_WORLD_TICKER_REQUIRED")
        if market.get("pmUpToken") and market.get("pmDownToken"):
            if market["pmUpToken"] == market["pmDownToken"]:
                raise ValueError("PM_OUTCOMES_NOT_DISTINCT")
        elif not config["pm"].get("discoveryUrl"):
            raise ValueError("PM_IDENTITY_OR_EXACT_SLUG_DISCOVERY_REQUIRED")
    pm = config["pm"]
    if pm["mode"] not in ("websocket", "rest"):
        raise ValueError("PM_MODE_INVALID")
    key = "websocketUrl" if pm["mode"] == "websocket" else "booksUrl"
    if not pm.get(key):
        raise ValueError("PM_ENDPOINT_REQUIRED")
    world = config["world"]
    if world.get("method", "GET") != "GET":
        raise ValueError("WORLD_HTTP_ADAPTER_ONLY_SUPPORTS_PUBLIC_GET_PRICE_READS")
    if not world.get("url") or not isinstance(world.get("query"), dict):
        raise ValueError("WORLD_ENDPOINT_AND_REQUEST_SHAPE_REQUIRED")
    if set(world["sidePaths"]) != {"yes", "no"}:
        raise ValueError("WORLD_YES_NO_RESPONSE_PATHS_REQUIRED")
    if not all(k in world["fields"] for k in ("source_time", "bid", "ask")):
        raise ValueError("WORLD_RESPONSE_FIELD_BINDING_REQUIRED")
    if not isinstance(world.get("identityFields"), list) or not world["identityFields"]:
        raise ValueError("WORLD_HISTORY_ROW_IDENTITY_FIELDS_REQUIRED")
    if int(world["lookbackSeconds"]) <= 0:
        raise ValueError("WORLD_REQUEST_LOOKBACK_REQUIRED")
    for url in [pm[key], world["url"], pm.get("discoveryUrl")]:
        if url and urllib.parse.urlparse(url).scheme not in ("https", "wss"):
            raise ValueError("PUBLIC_TLS_ENDPOINT_REQUIRED")
    return config


class Journal:
    def __init__(self, path, clock=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Never overwrite a prior prospective sample.
        self.file = self.path.open("x", encoding="utf-8")
        self.lock = threading.RLock()
        self.sequence = 0
        self.quotes = {}
        self.clock = clock or ClockReliability()

    def emit(self, kind, **fields):
        with self.lock:
            stamps = self.clock.observe()
            row = {"type": kind, "sequence": self.sequence,
                   "wall_time": stamps["wall"],
                   "monotonic_time": stamps["monotonic"], **fields}
            self.sequence += 1
            self.file.write(json.dumps(row, allow_nan=False, separators=(",", ":")) + "\n")
            self.file.flush()
            if kind == "quote":
                self.quotes.setdefault((fields["market_start"], fields["venue"], fields["side"]), []).append(row)
            return row

    def latest(self, market_start, venue, side, t):
        with self.lock:
            rows = self.quotes.get((market_start, venue, side), [])
            eligible = [row for row in rows if row["receive_time"] < t]
            if eligible:
                # Late history must not roll the current World quote backwards.
                # Receipt still determines availability; source time determines
                # which of the already available history rows is latest.
                if venue == "world":
                    return max(eligible, key=lambda row: (row["source_time"], row["sequence"])).copy()
                return eligible[-1].copy()
        return None

    def close(self):
        self.file.close()


class FeedHealth:
    """Gap lifecycle shared by the PM WS and REST data readers."""
    def __init__(self, journal, market_start, venue, began):
        self.journal, self.market_start, self.venue = journal, market_start, venue
        self.last_healthy = began
        self.gap_start = began
        self.ping_sent = None
        self.complete = False
        journal.emit("gap_open", market_start=market_start, venue=venue,
                     gap_start=began, reason="INITIAL_SNAPSHOT_PENDING")

    def healthy(self, now):
        self.last_healthy = now
        if self.gap_start is not None:
            self.journal.emit("gap_close", market_start=self.market_start, venue=self.venue,
                              gap_start=self.gap_start, gap_end=now)
            self.gap_start = None
        self.complete = True

    def fail(self, reason):
        self.complete = False
        self.ping_sent = None
        if self.gap_start is None:
            self.gap_start = self.last_healthy
            self.journal.emit("gap_open", market_start=self.market_start, venue=self.venue,
                              gap_start=self.gap_start, reason=reason)

    def ping(self, now):
        self.ping_sent = now
        self.journal.emit("heartbeat", market_start=self.market_start, venue=self.venue,
                          receive_time=now, event="PING")

    def pong(self, now):
        self.ping_sent = None
        self.journal.emit("heartbeat", market_start=self.market_start, venue=self.venue,
                          receive_time=now, event="PONG")
        # A PONG cannot restore a book lost by disconnection/resubscription.
        if self.complete:
            self.last_healthy = now

    def expired(self, now):
        return self.ping_sent is not None and now - self.ping_sent >= 10

    def finish(self, end):
        if self.gap_start is not None:
            self.journal.emit("gap_close", market_start=self.market_start, venue=self.venue,
                              gap_start=self.gap_start, gap_end=end, unresolved=True)


class PMBooks:
    def __init__(self, market, journal):
        self.market, self.journal = market, journal
        self.tokens = {str(market["pmUpToken"]): "up", str(market["pmDownToken"]): "down"}
        self.books = {}

    @property
    def complete(self):
        return set(self.books) == set(self.tokens)

    def ingest(self, event, now):
        kind = event.get("event_type")
        changed = set()
        if kind == "book":
            token = str(event.get("asset_id"))
            if token not in self.tokens or "bids" not in event or "asks" not in event:
                raise ValueError("WRONG_OR_INCOMPLETE_PM_SNAPSHOT")
            self.books[token] = {
                side: {float(r["price"]): float(r["size"]) for r in event[side] if float(r["size"]) > 0}
                for side in ("bids", "asks")}
            changed.add(token)
        elif kind == "price_change":
            for change in event["price_changes"]:
                token = str(change["asset_id"])
                if token not in self.tokens:
                    continue
                if token not in self.books:
                    raise ValueError("DELTA_BEFORE_FULL_SNAPSHOT")
                side = {"BUY": "bids", "SELL": "asks"}[change["side"]]
                price, size = float(change["price"]), float(change["size"])
                if size > 0:
                    self.books[token][side][price] = size
                else:
                    self.books[token][side].pop(price, None)
                changed.add(token)
        for token in changed:
            book = self.books[token]
            value = valid_price(max(book["bids"], default=None), min(book["asks"], default=None))
            self.journal.emit("quote", market_start=self.market["startTs"], venue="pm",
                              side=self.tokens[token], asset_id=token,
                              source_time=source_seconds(event.get("timestamp")),
                              receive_time=now, response_latency=None, **value)


class WorldHistory:
    def __init__(self, journal, market, binding):
        self.journal, self.market, self.binding = journal, market, binding
        self.seen = set()

    def normalize(self, payload):
        items = []
        for side, path in self.binding["sidePaths"].items():
            rows = at_path(payload, path)
            if not isinstance(rows, list):
                raise ValueError("WORLD_HISTORY_NOT_ARRAY")
            for row in rows:
                fields = self.binding["fields"]
                source = float(row[fields["source_time"]])
                if not math.isfinite(source):
                    raise ValueError("WORLD_SOURCE_TIME_INVALID")
                # The request identity is frozen; other-market history is rejected.
                if not self.market["startTs"] <= source < self.market["endTs"]:
                    continue
                identity = (side, *(row[k] for k in self.binding["identityFields"]))
                identity = json.dumps(identity, sort_keys=True, separators=(",", ":"))
                items.append((source, side, identity,
                              valid_price(row.get(fields["bid"]), row.get(fields["ask"]))))
        return sorted(items, key=lambda x: (x[0], x[1], x[2]))

    def ingest(self, items, now, latency):
        for source, side, identity, price in items:
            if identity in self.seen:
                continue
            self.seen.add(identity)
            self.journal.emit("quote", market_start=self.market["startTs"], venue="world",
                              side=side, ticker=self.market["worldTicker"], source_time=source,
                              receive_time=now, response_latency=latency,
                              rounding_semantics="UNKNOWN", row_identity=identity, **price)


def next_anchor(start, cadence, previous_index, now):
    """Skip missed anchors rather than issuing catch-up requests."""
    return max(previous_index + 1, int(math.ceil((now - start) / cadence)))


def resolve_pm(config, market, journal, stop):
    if market.get("pmUpToken") and market.get("pmDownToken"):
        return market
    slug = f"btc-updown-5m-{int(market['startTs'])}"
    while not stop.is_set() and time.time() < market["endTs"]:
        try:
            url = config["pm"]["discoveryUrl"] + "?" + urllib.parse.urlencode({"slug": slug})
            rows = http_json(url, timeout=min(5, market["endTs"] - time.time()))
            if len(rows) != 1 or len(rows[0]["markets"]) != 1:
                raise ValueError("PM_EXACT_SLUG_NOT_UNIQUE")
            pm = rows[0]["markets"][0]
            outcomes, tokens = pm["outcomes"], pm["clobTokenIds"]
            outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
            tokens = json.loads(tokens) if isinstance(tokens, str) else tokens
            if outcomes != ["Up", "Down"] or len(tokens) != 2:
                raise ValueError("PM_EXACT_OUTCOME_IDENTITY_INVALID")
            result = {**market, "pmUpToken": str(tokens[0]), "pmDownToken": str(tokens[1]),
                      "conditionId": pm["conditionId"], "pmSlug": slug}
            journal.emit("identity", market_start=market["startTs"], received_at=time.time(), market=result)
            return result
        except Exception as exc:
            journal.emit("error", market_start=market["startTs"], venue="pm",
                         receive_time=time.time(), error=type(exc).__name__, operation="identity")
            stop.wait(min(2, max(0, market["endTs"] - time.time())))
    raise ValueError("PM_IDENTITY_UNAVAILABLE")


def pm_worker(config, market, journal, stop):
    health = FeedHealth(journal, market["startTs"], "pm", market["startTs"])
    try:
        market = resolve_pm(config, market, journal, stop)
        pm = config["pm"]
        if pm["mode"] == "rest":
            index = 0
            while not stop.is_set() and time.time() < market["endTs"]:
                target = market["startTs"] + index * 0.5
                if stop.wait(max(0, target - time.time())):
                    break
                try:
                    payload = http_json(pm["booksUrl"], method="POST",
                                        body=[{"token_id": market["pmUpToken"]}, {"token_id": market["pmDownToken"]}],
                                        timeout=min(5, max(0.001, market["endTs"] - time.time())))
                    books = PMBooks(market, journal)
                    parsed = [{**row, "event_type": "book", "asset_id": row["asset_id"]} for row in payload]
                    now = time.time()
                    journal.emit("raw", market_start=market["startTs"], venue="pm", receive_time=now, payload=payload)
                    if now >= market["endTs"]:
                        raise TimeoutError("PM_RESPONSE_AFTER_WINDOW")
                    for event in parsed:
                        books.ingest(event, now)
                    if not books.complete:
                        raise ValueError("PM_BATCH_SNAPSHOT_INCOMPLETE")
                    health.healthy(now)
                except Exception as exc:
                    health.fail("REST_REQUEST_FAILED")
                    journal.emit("error", market_start=market["startTs"], venue="pm",
                                 receive_time=time.time(), error=type(exc).__name__)
                index = next_anchor(market["startTs"], 0.5, index, time.time())
            return
        import websocket
        while not stop.is_set() and time.time() < market["endTs"]:
            conn = None
            try:
                books = PMBooks(market, journal)
                conn = websocket.create_connection(pm["websocketUrl"], timeout=min(5, market["endTs"] - time.time()))
                conn.send(json.dumps({"assets_ids": [market["pmUpToken"], market["pmDownToken"]], "type": "market"}))
                conn.settimeout(0.25)
                next_ping = time.monotonic() + 10
                while not stop.is_set() and time.time() < market["endTs"]:
                    now = time.time()
                    if health.expired(now):
                        raise TimeoutError("APPLICATION_PONG_TIMEOUT")
                    if time.monotonic() >= next_ping:
                        conn.send("PING")
                        health.ping(now)
                        next_ping += 10
                    try:
                        message = conn.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    if time.time() >= market["endTs"]:
                        break
                    if message == "PONG":
                        health.pong(time.time())
                        continue
                    if not message:
                        raise ConnectionError("WEBSOCKET_CLOSED")
                    payload = json.loads(message)
                    now = time.time()
                    journal.emit("raw", market_start=market["startTs"], venue="pm", receive_time=now, payload=payload)
                    if now >= market["endTs"]:
                        raise TimeoutError("PM_RESPONSE_AFTER_WINDOW")
                    for event in payload if isinstance(payload, list) else [payload]:
                        books.ingest(event, time.time())
                    if books.complete and not health.complete:
                        health.healthy(time.time())
            except Exception as exc:
                health.fail("WEBSOCKET_INTEGRITY_FAILURE")
                journal.emit("error", market_start=market["startTs"], venue="pm",
                             receive_time=time.time(), error=type(exc).__name__)
                stop.wait(min(1, max(0, market["endTs"] - time.time())))
            finally:
                if conn is not None:
                    conn.close(timeout=0.1)
    except Exception as exc:
        health.fail("PM_WORKER_FAILED")
        journal.emit("error", market_start=market["startTs"], venue="pm", receive_time=time.time(), error=type(exc).__name__)
    finally:
        health.finish(market["endTs"])


def world_worker(config, market, journal, stop):
    binding = config["world"]
    seen = WorldHistory(journal, market, binding)
    index = 0
    while not stop.is_set() and time.time() < market["endTs"]:
        target = market["startTs"] + index
        if stop.wait(max(0, target - time.time())):
            break
        for attempt in range(2):
            if stop.is_set() or time.time() >= market["endTs"]:
                break
            began = time.time()
            values = {"ticker": market["worldTicker"], "start_ts": market["startTs"],
                      "end_ts": market["endTs"], "query_start": max(market["startTs"], int(began) - binding["lookbackSeconds"]),
                      "query_end": min(market["endTs"], int(began) + 1)}
            try:
                query = format_values(binding["query"], values)
                url = binding["url"] + "?" + urllib.parse.urlencode(query)
                payload = http_json(url, timeout=min(5, market["endTs"] - began))
                response_at = time.time()
                # Preserve the raw response receipt before parsing/usable rows.
                journal.emit("raw", market_start=market["startTs"], venue="world", receive_time=response_at,
                             response_received_time=response_at, response_latency=response_at - began,
                             request_started_at=began, attempt=attempt, payload=payload)
                if response_at >= market["endTs"]:
                    break
                items = seen.normalize(payload)
                usable_at = time.time()
                if usable_at >= market["endTs"]:
                    break
                seen.ingest(items, usable_at, response_at - began)
                break
            except Exception as exc:
                journal.emit("error", market_start=market["startTs"], venue="world", receive_time=time.time(),
                             error=type(exc).__name__, attempt=attempt)
                if attempt == 0:
                    stop.wait(min(2, max(0, market["endTs"] - time.time())))
        index = next_anchor(market["startTs"], 1.0, index, time.time())


def capture(config, output):
    validate_config(config)
    if config.get("bindingRatified") is not True:
        raise ValueError("SECTION_2_5_BINDING_NOT_RATIFIED")
    if time.time() >= config["startTs"]:
        raise ValueError("FROZEN_START_ALREADY_ELAPSED_NO_RESELECTION")
    clock = ClockReliability()
    clock.observe()
    journal = Journal(output, clock=clock)
    journal.emit("header", schemaVersion=SCHEMA, startTs=config["startTs"], endTs=config["endTs"],
                 mode="PUBLIC_READ_ONLY_NO_TRADE", timestampReliability="UNVERIFIED",
                 configuration=config, recorded_at=time.time())
    completed = False
    try:
        for market in config["markets"]:
            while time.time() < market["startTs"]:
                time.sleep(min(0.5, market["startTs"] - time.time()))
            stop = threading.Event()
            threads = [threading.Thread(target=worker, args=(config, market, journal, stop))
                       for worker in (pm_worker, world_worker)]
            for thread in threads:
                thread.start()
            try:
                for i in range(600):
                    target = market["startTs"] + i * 0.5
                    if time.time() < target:
                        time.sleep(target - time.time())
                    journal.emit("grid", market_start=market["startTs"], target_time=target,
                                 captured_at=time.time(), quotes={
                                     f"{venue}_{side}": journal.latest(market["startTs"], venue, side, target)
                                     for venue, sides in (("pm", ("up", "down")), ("world", ("yes", "no")))
                                     for side in sides})
                while time.time() < market["endTs"]:
                    time.sleep(min(0.25, market["endTs"] - time.time()))
            finally:
                stop.set()
                for thread in threads:
                    thread.join()
                # Raw evidence stays on disk; earlier markets cannot be used
                # by the next market's grid and need no live in-memory cache.
                with journal.lock:
                    journal.quotes = {key: rows for key, rows in journal.quotes.items()
                                      if key[0] != market["startTs"]}
        completed = True
    finally:
        journal.emit("footer", completed=completed, recorded_at=time.time(),
                     clockReliability=clock.report())
        journal.close()
        path = Path(output)
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        path.with_suffix(path.suffix + ".sha256").write_text(digest + "  " + path.name + "\n", encoding="utf-8")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True)
    p.add_argument("--output", help="Exclusive-create JSONL quote journal")
    p.add_argument("--capture", action="store_true", help="Explicitly start a future bound cohort; never used by offline tests")
    args = p.parse_args()
    config = validate_config(json.loads(Path(args.config).read_text(encoding="utf-8")))
    if not args.capture:
        print(json.dumps({"valid": True, "networkRequests": 0, "bindingRatified": config.get("bindingRatified") is True}))
        return
    if not args.output:
        p.error("--output required with --capture")
    capture(config, args.output)


if __name__ == "__main__":
    main()
