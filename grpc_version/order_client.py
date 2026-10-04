"""Command-line client for the gRPC Order Service (plays the role curl plays for REST).

    python grpc_version/order_client.py laptop 2      # PlaceOrder
    python grpc_version/order_client.py --health      # Health

Prints the request and response messages in protobuf text format, plus the
gRPC status code. Exits with 1 when the call fails.
"""
import argparse
import sys
import time

import grpc
from google.protobuf import text_format

import order_pb2
import order_pb2_grpc


def show(message):
    text = text_format.MessageToString(message).strip()
    return "\n".join(f"  {line}" for line in text.splitlines()) if text else "  (empty message)"


def main():
    parser = argparse.ArgumentParser(description="gRPC Order Service client")
    parser.add_argument("item_id", nargs="?")
    parser.add_argument("quantity", nargs="?", type=int)
    parser.add_argument("--health", action="store_true", help="call OrderService.Health instead of PlaceOrder")
    parser.add_argument("--target", default="127.0.0.1:50052", help="Order Service address")
    parser.add_argument("--timeout", type=float, default=10.0,
                        help="client deadline for the call to the Order Service")
    args = parser.parse_args()
    if not args.health and (args.item_id is None or args.quantity is None):
        parser.error("give ITEM_ID and QUANTITY, or --health")

    with grpc.insecure_channel(args.target) as channel:
        stub = order_pb2_grpc.OrderServiceStub(channel)
        if args.health:
            method, call, request = "Health", stub.Health, order_pb2.HealthRequest()
        else:
            method, call = "PlaceOrder", stub.PlaceOrder
            request = order_pb2.PlaceOrderRequest(item_id=args.item_id, quantity=args.quantity)

        print(f"> order.v1.OrderService/{method}  target={args.target}  deadline={args.timeout:g}s")
        print(show(request))
        start = time.monotonic()
        try:
            response = call(request, timeout=args.timeout)
        except grpc.RpcError as err:
            print(f"< {err.code().name}  ({(time.monotonic() - start) * 1000:.0f} ms)")
            print(f"  details: {err.details()}")
            return 1
        print(f"< OK  ({(time.monotonic() - start) * 1000:.0f} ms)")
        print(show(response))
        return 0


if __name__ == "__main__":
    sys.exit(main())
