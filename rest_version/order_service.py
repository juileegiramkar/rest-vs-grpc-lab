"""Order Service - REST version.

    POST /orders     body: {"item_id": "laptop", "quantity": 2}
    GET  /health

Each order is checked with POST {inventory-url}/inventory/check, with a
timeout (default 2s). The result is mapped to an HTTP status:

    201 Created              Inventory confirmed the stock
    400 / 404 / 409          passed through from Inventory (bad input, unknown item, not enough stock)
    502 Bad Gateway          Inventory answered with something unexpected
    503 Service Unavailable  Inventory could not be reached
    504 Gateway Timeout      Inventory did not answer within the timeout
"""
import argparse
import logging
import os
import threading
import time

import requests
from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

log = logging.getLogger("order-rest")
app = Flask(__name__)
app.json.sort_keys = False  # keep response fields in the order they are written

inventory_url = "http://127.0.0.1:8001"
timeout_seconds = 2.0
started_at = time.monotonic()
lock = threading.Lock()
next_order_number = 1
stats = {"orders_confirmed": 0, "orders_rejected": 0, "inventory_errors": 0}


def error(status, code, message):
    return jsonify({"error": code, "message": message}), status


def count(stat):
    with lock:
        stats[stat] += 1


def new_order_id():
    global next_order_number
    with lock:
        order_id = f"ORD-{next_order_number:04d}"
        next_order_number += 1
    return order_id


@app.errorhandler(HTTPException)
def http_error(exc):
    return error(exc.code, exc.name.upper().replace(" ", "_"), exc.description)


@app.post("/orders")
def place_order():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error(400, "INVALID_JSON", "Request body must be a JSON object")
    item_id = body.get("item_id")
    quantity = body.get("quantity")
    if not isinstance(item_id, str) or not item_id.strip():
        return error(400, "INVALID_ARGUMENT", "item_id must be a non-empty string")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
        return error(400, "INVALID_ARGUMENT", "quantity must be an integer >= 1")

    url = f"{inventory_url}/inventory/check"
    log.info("POST /orders item_id=%s quantity=%d -> POST %s (timeout %.1fs)",
             item_id, quantity, url, timeout_seconds)
    start = time.monotonic()
    try:
        resp = requests.post(url, json={"item_id": item_id, "quantity": quantity},
                             timeout=timeout_seconds)
    except requests.Timeout:
        count("inventory_errors")
        log.error("Inventory timed out after %.2fs -> 504 Gateway Timeout", time.monotonic() - start)
        return error(504, "INVENTORY_TIMEOUT",
                     f"Inventory service did not respond within {timeout_seconds}s")
    except requests.ConnectionError as exc:
        count("inventory_errors")
        log.error("Inventory unreachable (%s) -> 503 Service Unavailable", type(exc).__name__)
        return error(503, "INVENTORY_UNAVAILABLE", "Inventory service could not be reached")

    elapsed_ms = (time.monotonic() - start) * 1000
    try:
        data = resp.json()
    except ValueError:
        data = None

    if resp.status_code == 200 and isinstance(data, dict) and data.get("available"):
        count("orders_confirmed")
        order_id = new_order_id()
        log.info("Inventory 200 OK in %.0f ms -> 201 Created %s", elapsed_ms, order_id)
        return jsonify({
            "order_id": order_id,
            "status": "CONFIRMED",
            "item_id": item_id,
            "quantity": quantity,
        }), 201

    if resp.status_code in (400, 404, 409) and isinstance(data, dict):
        count("orders_rejected")
        log.info("Inventory %d in %.0f ms -> %d %s",
                 resp.status_code, elapsed_ms, resp.status_code, data.get("error"))
        return jsonify(data), resp.status_code

    count("inventory_errors")
    log.error("Unexpected Inventory response HTTP %d -> 502 Bad Gateway", resp.status_code)
    return error(502, "BAD_INVENTORY_RESPONSE",
                 f"Unexpected response from Inventory service: HTTP {resp.status_code}")


@app.get("/health")
def health():
    with lock:
        snapshot = dict(stats)
    return jsonify({
        "status": "UP",
        "pid": os.getpid(),
        "uptime_seconds": round(time.monotonic() - started_at, 1),
        **snapshot,
    }), 200


def main():
    global inventory_url, timeout_seconds
    parser = argparse.ArgumentParser(description="Order Service (REST)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--inventory-url", default=inventory_url)
    parser.add_argument("--timeout", type=float, default=timeout_seconds,
                        help="timeout in seconds for the Order -> Inventory request")
    args = parser.parse_args()
    inventory_url = args.inventory_url.rstrip("/")
    timeout_seconds = args.timeout

    logging.basicConfig(level=logging.INFO, datefmt="%H:%M:%S",
                        format="%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s | %(message)s")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    log.info("Order Service (REST) on http://%s:%d  inventory=%s  timeout=%.1fs  pid=%d",
             args.host, args.port, inventory_url, timeout_seconds, os.getpid())
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
