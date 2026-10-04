"""Inventory Service - REST version.

    POST /inventory/check     body: {"item_id": "laptop", "quantity": 2}

    200 OK           item exists and enough units are in stock
    400 Bad Request  body is not a JSON object, or item_id / quantity is missing or invalid
    404 Not Found    item_id is not in the catalog
    409 Conflict     item exists but there are not enough units in stock

Start with --delay N to make every check sleep N seconds before answering.
That is the temporary delay used to trigger the Order Service timeout.
"""
import argparse
import json
import logging
import time
from pathlib import Path

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

STOCK_FILE = Path(__file__).resolve().parent.parent / "data" / "inventory.json"

log = logging.getLogger("inventory-rest")
app = Flask(__name__)
app.json.sort_keys = False  # keep response fields in the order they are written
stock = json.loads(STOCK_FILE.read_text())
delay_seconds = 0.0


def error(status, code, message):
    return jsonify({"error": code, "message": message}), status


@app.errorhandler(HTTPException)
def http_error(exc):
    # Unknown routes / wrong methods get a JSON body too, instead of Flask's HTML page.
    return error(exc.code, exc.name.upper().replace(" ", "_"), exc.description)


@app.post("/inventory/check")
def check_inventory():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error(400, "INVALID_JSON", "Request body must be a JSON object")
    item_id = body.get("item_id")
    quantity = body.get("quantity")
    if not isinstance(item_id, str) or not item_id.strip():
        return error(400, "INVALID_ARGUMENT", "item_id must be a non-empty string")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
        return error(400, "INVALID_ARGUMENT", "quantity must be an integer >= 1")

    log.info("POST /inventory/check item_id=%s quantity=%d", item_id, quantity)
    start = time.monotonic()
    if delay_seconds:
        # A REST server has no idea how long the caller is willing to wait,
        # so it sleeps the full delay even if the caller has already given up.
        log.warning("simulated slow response: sleeping %.1fs", delay_seconds)
        time.sleep(delay_seconds)

    if item_id not in stock:
        log.info("-> 404 Not Found (after %.2fs)", time.monotonic() - start)
        return error(404, "ITEM_NOT_FOUND", f"Item '{item_id}' does not exist")
    in_stock = stock[item_id]
    if in_stock < quantity:
        log.info("-> 409 Conflict (after %.2fs)", time.monotonic() - start)
        return error(409, "INSUFFICIENT_STOCK",
                     f"Only {in_stock} unit(s) of '{item_id}' in stock, {quantity} requested")

    log.info("-> 200 OK available_quantity=%d (after %.2fs)", in_stock, time.monotonic() - start)
    return jsonify({
        "item_id": item_id,
        "requested_quantity": quantity,
        "available_quantity": in_stock,
        "available": True,
    }), 200


def main():
    global delay_seconds
    parser = argparse.ArgumentParser(description="Inventory Service (REST)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--delay", type=float, default=0.0,
                        help="seconds to sleep before answering each check (failure demo)")
    args = parser.parse_args()
    delay_seconds = args.delay

    logging.basicConfig(level=logging.INFO, datefmt="%H:%M:%S",
                        format="%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s | %(message)s")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    log.info("Inventory Service (REST) on http://%s:%d  delay=%.1fs  items=%s",
             args.host, args.port, delay_seconds, stock)
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
