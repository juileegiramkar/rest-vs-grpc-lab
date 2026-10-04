"""Inventory Service - gRPC version (implements inventory.v1.InventoryService in proto/inventory.proto).

    CheckAvailability(item_id, quantity)

    OK                   item exists and enough units are in stock
    INVALID_ARGUMENT     item_id is empty or quantity < 1
    NOT_FOUND            item_id is not in the catalog
    FAILED_PRECONDITION  item exists but there are not enough units in stock

Start with --delay N to make every check sleep N seconds before answering.
That is the temporary delay used to trigger the Order Service deadline.
"""
import argparse
import json
import logging
import time
from concurrent import futures
from pathlib import Path

import grpc

import inventory_pb2
import inventory_pb2_grpc

STOCK_FILE = Path(__file__).resolve().parent.parent / "data" / "inventory.json"

log = logging.getLogger("inventory-grpc")


def describe_deadline(context):
    remaining = context.time_remaining()
    return "no deadline" if remaining is None else f"caller deadline in {remaining:.2f}s"


class InventoryService(inventory_pb2_grpc.InventoryServiceServicer):
    def __init__(self, stock, delay_seconds):
        self.stock = stock
        self.delay_seconds = delay_seconds

    def CheckAvailability(self, request, context):
        item_id, quantity = request.item_id, request.quantity
        if not item_id.strip():
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "item_id must be a non-empty string")
        if quantity < 1:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "quantity must be an integer >= 1")

        # Unlike REST, the caller's deadline travels with the request (grpc-timeout header).
        log.info("CheckAvailability item_id=%s quantity=%d (%s)", item_id, quantity, describe_deadline(context))
        start = time.monotonic()
        if self.delay_seconds and not self._sleep_unless_cancelled(context):
            # The caller is gone, so nobody will receive this reply.
            return inventory_pb2.CheckAvailabilityResponse()

        if item_id not in self.stock:
            log.info("-> NOT_FOUND (after %.2fs)", time.monotonic() - start)
            context.abort(grpc.StatusCode.NOT_FOUND, f"Item '{item_id}' does not exist")
        in_stock = self.stock[item_id]
        if in_stock < quantity:
            log.info("-> FAILED_PRECONDITION (after %.2fs)", time.monotonic() - start)
            context.abort(grpc.StatusCode.FAILED_PRECONDITION,
                          f"Only {in_stock} unit(s) of '{item_id}' in stock, {quantity} requested")

        log.info("-> OK available_quantity=%d (after %.2fs)", in_stock, time.monotonic() - start)
        return inventory_pb2.CheckAvailabilityResponse(
            item_id=item_id,
            requested_quantity=quantity,
            available_quantity=in_stock,
            available=True,
        )

    def _sleep_unless_cancelled(self, context):
        """Sleep for the configured delay; return False early if the caller's deadline expires."""
        log.warning("simulated slow response: sleeping %.1fs", self.delay_seconds)
        start = time.monotonic()
        while time.monotonic() - start < self.delay_seconds:
            if not context.is_active():
                log.warning("caller's deadline expired after %.2fs -> abandoning this request",
                            time.monotonic() - start)
                return False
            time.sleep(0.05)
        return True


def main():
    parser = argparse.ArgumentParser(description="Inventory Service (gRPC)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=50051)
    parser.add_argument("--delay", type=float, default=0.0,
                        help="seconds to sleep before answering each check (failure demo)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, datefmt="%H:%M:%S",
                        format="%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s | %(message)s")
    stock = json.loads(STOCK_FILE.read_text())

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    inventory_pb2_grpc.add_InventoryServiceServicer_to_server(InventoryService(stock, args.delay), server)
    server.add_insecure_port(f"{args.host}:{args.port}")
    server.start()
    log.info("Inventory Service (gRPC) on %s:%d  delay=%.1fs  items=%s", args.host, args.port, args.delay, stock)
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop(grace=1)


if __name__ == "__main__":
    main()
