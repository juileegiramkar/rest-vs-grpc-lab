"""Order Service - gRPC version (implements order.v1.OrderService in proto/order.proto).

    PlaceOrder(item_id, quantity)
    Health()

Each order is checked with InventoryService.CheckAvailability using a gRPC
deadline (default 2s). Inventory errors are mapped to the Order response:

    OK                                        Inventory confirmed the stock
    INVALID_ARGUMENT / NOT_FOUND /
    FAILED_PRECONDITION                       passed through from Inventory
    DEADLINE_EXCEEDED                         Inventory did not answer before the deadline
    UNAVAILABLE                               Inventory could not be reached
"""
import argparse
import logging
import os
import threading
import time
from concurrent import futures

import grpc

import inventory_pb2
import inventory_pb2_grpc
import order_pb2
import order_pb2_grpc

log = logging.getLogger("order-grpc")

PASS_THROUGH = {
    grpc.StatusCode.INVALID_ARGUMENT,
    grpc.StatusCode.NOT_FOUND,
    grpc.StatusCode.FAILED_PRECONDITION,
}


class OrderService(order_pb2_grpc.OrderServiceServicer):
    def __init__(self, inventory_addr, deadline_seconds):
        self.inventory_addr = inventory_addr
        self.deadline_seconds = deadline_seconds
        # One long-lived channel; gRPC reconnects on its own if Inventory restarts.
        self.inventory = inventory_pb2_grpc.InventoryServiceStub(grpc.insecure_channel(inventory_addr))
        self.started_at = time.monotonic()
        self.lock = threading.Lock()
        self.next_order_number = 1
        self.stats = {"orders_confirmed": 0, "orders_rejected": 0, "inventory_errors": 0}

    def _count(self, stat):
        with self.lock:
            self.stats[stat] += 1

    def _new_order_id(self):
        with self.lock:
            order_id = f"ORD-{self.next_order_number:04d}"
            self.next_order_number += 1
        return order_id

    def PlaceOrder(self, request, context):
        item_id, quantity = request.item_id, request.quantity
        if not item_id.strip():
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "item_id must be a non-empty string")
        if quantity < 1:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "quantity must be an integer >= 1")

        log.info("PlaceOrder item_id=%s quantity=%d -> InventoryService.CheckAvailability @ %s (deadline %.1fs)",
                 item_id, quantity, self.inventory_addr, self.deadline_seconds)
        start = time.monotonic()
        try:
            self.inventory.CheckAvailability(
                inventory_pb2.CheckAvailabilityRequest(item_id=item_id, quantity=quantity),
                timeout=self.deadline_seconds,
            )
        except grpc.RpcError as err:
            elapsed = time.monotonic() - start
            code = err.code()
            if code in PASS_THROUGH:
                self._count("orders_rejected")
                log.info("Inventory %s in %.0f ms -> %s", code.name, elapsed * 1000, code.name)
                context.abort(code, err.details())
            self._count("inventory_errors")
            if code == grpc.StatusCode.DEADLINE_EXCEEDED:
                log.error("Inventory exceeded the %.1fs deadline (gave up after %.2fs) -> DEADLINE_EXCEEDED",
                          self.deadline_seconds, elapsed)
                context.abort(grpc.StatusCode.DEADLINE_EXCEEDED,
                              f"Inventory service did not respond within the {self.deadline_seconds}s deadline")
            if code == grpc.StatusCode.UNAVAILABLE:
                log.error("Inventory unreachable -> UNAVAILABLE")
                context.abort(grpc.StatusCode.UNAVAILABLE, "Inventory service could not be reached")
            log.error("Unexpected Inventory error %s -> INTERNAL", code.name)
            context.abort(grpc.StatusCode.INTERNAL, f"Unexpected Inventory error: {code.name}")

        self._count("orders_confirmed")
        order_id = self._new_order_id()
        log.info("Inventory OK in %.0f ms -> CONFIRMED %s", (time.monotonic() - start) * 1000, order_id)
        return order_pb2.PlaceOrderResponse(
            order_id=order_id,
            status="CONFIRMED",
            item_id=item_id,
            quantity=quantity,
        )

    def Health(self, request, context):
        with self.lock:
            snapshot = dict(self.stats)
        return order_pb2.HealthResponse(
            status="UP",
            pid=os.getpid(),
            uptime_seconds=round(time.monotonic() - self.started_at, 1),
            **snapshot,
        )


def main():
    parser = argparse.ArgumentParser(description="Order Service (gRPC)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=50052)
    parser.add_argument("--inventory-addr", default="127.0.0.1:50051")
    parser.add_argument("--deadline", type=float, default=2.0,
                        help="gRPC deadline in seconds for the Order -> Inventory call")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, datefmt="%H:%M:%S",
                        format="%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s | %(message)s")
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    order_pb2_grpc.add_OrderServiceServicer_to_server(OrderService(args.inventory_addr, args.deadline), server)
    server.add_insecure_port(f"{args.host}:{args.port}")
    server.start()
    log.info("Order Service (gRPC) on %s:%d  inventory=%s  deadline=%.1fs  pid=%d",
             args.host, args.port, args.inventory_addr, args.deadline, os.getpid())
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop(grace=1)


if __name__ == "__main__":
    main()
