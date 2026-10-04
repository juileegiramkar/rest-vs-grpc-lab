# REST vs gRPC: Order and Inventory Services

For this lab I built the same two services twice in Python, once using REST (Flask + JSON) and once using gRPC (Protocol Buffers).

- The **Order Service** takes an order with an `item_id` and a `quantity`.
- The **Inventory Service** checks if that item exists and if there's enough of it in stock.

Before confirming an order, the Order Service asks the Inventory Service if the item is available. That call is the part I implemented both ways:

```
REST:  curl             -> Order Service :8000   -- POST /inventory/check -->    Inventory Service :8001
gRPC:  order_client.py  -> Order Service :50052  -- CheckAvailability (gRPC) --> Inventory Service :50051
```

Both inventory services load the same stock from [`data/inventory.json`](data/inventory.json):

| item_id | laptop | keyboard | monitor | headphones |
|---|---|---|---|---|
| in stock | 10 | 25 | 3 | 0 |

## What's in the repo

```
proto/            inventory.proto and order.proto (the gRPC contracts)
rest_version/     inventory_service.py and order_service.py (Flask)
grpc_version/     inventory_server.py, order_server.py, order_client.py
                  plus the *_pb2.py / *_pb2_grpc.py files generated from proto/
data/             inventory.json
scripts/demo.sh   runs the whole demo for one version and saves the output to evidence/
evidence/         client output and service logs used in this README
```

## Status codes

I tried to keep the two versions matching, so every case has a REST status and the gRPC equivalent:

| Case | REST | gRPC |
|---|---|---|
| Order placed | 201 Created | OK |
| Bad input (like quantity 0) | 400 Bad Request | INVALID_ARGUMENT |
| Item doesn't exist | 404 Not Found | NOT_FOUND |
| Not enough stock | 409 Conflict | FAILED_PRECONDITION |
| Inventory too slow | 504 Gateway Timeout | DEADLINE_EXCEEDED |
| Inventory down | 503 Service Unavailable | UNAVAILABLE |

The inventory service returns the 400/404/409 (or INVALID_ARGUMENT/NOT_FOUND/FAILED_PRECONDITION) errors and the order service passes them on to the client. For "not enough stock" I picked 409 and FAILED_PRECONDITION, because the request itself is fine, the inventory just can't cover it right now.

The Order -> Inventory call has a 2 second limit in both versions (`--timeout` for REST, `--deadline` for gRPC).

## Setup

I used Python 3.13 on Windows 11, but any Python 3.10+ should work.

```bash
python -m venv .venv
source .venv/bin/activate        # Git Bash on Windows: source .venv/Scripts/activate
pip install -r requirements.txt
```

In Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` instead (if that's blocked, run `Set-ExecutionPolicy -Scope Process Bypass` first).

Run all the commands below from the repo root with the venv active.

The generated gRPC files are already in the repo. If you change a `.proto` file, regenerate them with:

```bash
python -m grpc_tools.protoc -I proto --python_out=grpc_version --grpc_python_out=grpc_version proto/inventory.proto proto/order.proto
```

## Running the REST version

You need three terminals:

```bash
# terminal 1: inventory service on port 8001
python rest_version/inventory_service.py

# terminal 2: order service on port 8000, with a 2 second timeout on the inventory call
python rest_version/order_service.py --timeout 2

# terminal 3: place an order, then check the order service's health
curl -i -X POST http://127.0.0.1:8000/orders -H "Content-Type: application/json" -d '{"item_id": "laptop", "quantity": 2}'
curl -i http://127.0.0.1:8000/health
```

In PowerShell the quotes inside `-d '{...}'` get stripped, so pipe the JSON in instead:

```powershell
'{"item_id": "laptop", "quantity": 2}' | curl.exe -i -X POST http://127.0.0.1:8000/orders -H "Content-Type: application/json" --data-binary '@-'
```

To test the timeout:

1. Stop the inventory service (Ctrl+C) and start it again with a 5 second delay: `python rest_version/inventory_service.py --delay 5`
2. Send the same order again. After about 2 seconds it comes back with `504 Gateway Timeout`.
3. Run the health check. The order service still answers, with the same pid as before.
4. Restart the inventory service without `--delay` and send another order. It goes through (201), handled by the same order service process.

## Running the gRPC version

```bash
# terminal 1: inventory service on port 50051
python grpc_version/inventory_server.py

# terminal 2: order service on port 50052, with a 2 second deadline on the inventory call
python grpc_version/order_server.py --deadline 2

# terminal 3: place an order, then check the order service's health
python grpc_version/order_client.py laptop 2
python grpc_version/order_client.py --health
```

You can't just curl a gRPC service, so I wrote `order_client.py` for testing. It prints the request, the status code, and the response.

To test the deadline:

1. Restart the inventory service with a delay: `python grpc_version/inventory_server.py --delay 5`
2. Run `python grpc_version/order_client.py laptop 1`. After about 2 seconds it fails with `DEADLINE_EXCEEDED`.
3. Run `python grpc_version/order_client.py --health`. It still returns OK with the same pid.
4. Restart the inventory service without the delay. The next order is confirmed by the same order service.

### Running everything with one script

`scripts/demo.sh` does all of the steps above on its own and saves the output to `evidence/`. It runs in Git Bash on Windows, or in bash on Mac/Linux:

```bash
bash scripts/demo.sh rest
bash scripts/demo.sh grpc
```

## Results

Everything below is copied from the files in [`evidence/`](evidence), which I got by running `scripts/demo.sh` for each version. The service logs use my local time (PDT) and the HTTP `Date` header uses GMT, so they're 7 hours apart.

### Successful REST request

From [`evidence/rest/client_1_normal.txt`](evidence/rest/client_1_normal.txt):

```
$ curl -s -i -X POST http://127.0.0.1:8000/orders -H "Content-Type: application/json" -d '{"item_id": "laptop", "quantity": 2}'
HTTP/1.1 201 CREATED
Server: Werkzeug/3.1.9 Python/3.13.15
Date: Sun, 04 Oct 2026 23:24:45 GMT
Content-Type: application/json
Content-Length: 77
Connection: close

{"order_id":"ORD-0001","status":"CONFIRMED","item_id":"laptop","quantity":2}
```

I also called the inventory endpoint directly, to show the Order -> Inventory request by itself:

```
$ curl -s -i -X POST http://127.0.0.1:8001/inventory/check -H "Content-Type: application/json" -d '{"item_id": "laptop", "quantity": 2}'
HTTP/1.1 200 OK
Server: Werkzeug/3.1.9 Python/3.13.15
Date: Sun, 04 Oct 2026 23:24:45 GMT
Content-Type: application/json
Content-Length: 85
Connection: close

{"item_id":"laptop","requested_quantity":2,"available_quantity":10,"available":true}
```

The order service log for that request ([`evidence/rest/order_service.log`](evidence/rest/order_service.log)):

```
16:24:45.044 INFO    order-rest | POST /orders item_id=laptop quantity=2 -> POST http://127.0.0.1:8001/inventory/check (timeout 2.0s)
16:24:45.048 INFO    order-rest | Inventory 200 OK in 4 ms -> 201 Created ORD-0001
```

And the error cases: 409 for not enough stock, 404 for an item that doesn't exist, and 400 for a bad quantity.

```
$ curl -s -i -X POST http://127.0.0.1:8000/orders -H "Content-Type: application/json" -d '{"item_id": "monitor", "quantity": 5}'
HTTP/1.1 409 CONFLICT
Server: Werkzeug/3.1.9 Python/3.13.15
Date: Sun, 04 Oct 2026 23:24:45 GMT
Content-Type: application/json
Content-Length: 93
Connection: close

{"error":"INSUFFICIENT_STOCK","message":"Only 3 unit(s) of 'monitor' in stock, 5 requested"}

$ curl -s -i -X POST http://127.0.0.1:8000/orders -H "Content-Type: application/json" -d '{"item_id": "phone", "quantity": 1}'
HTTP/1.1 404 NOT FOUND
Server: Werkzeug/3.1.9 Python/3.13.15
Date: Sun, 04 Oct 2026 23:24:45 GMT
Content-Type: application/json
Content-Length: 67
Connection: close

{"error":"ITEM_NOT_FOUND","message":"Item 'phone' does not exist"}

$ curl -s -i -X POST http://127.0.0.1:8000/orders -H "Content-Type: application/json" -d '{"item_id": "laptop", "quantity": 0}'
HTTP/1.1 400 BAD REQUEST
Server: Werkzeug/3.1.9 Python/3.13.15
Date: Sun, 04 Oct 2026 23:24:45 GMT
Content-Type: application/json
Content-Length: 74
Connection: close

{"error":"INVALID_ARGUMENT","message":"quantity must be an integer >= 1"}
```

### Successful gRPC request

From [`evidence/grpc/client_1_normal.txt`](evidence/grpc/client_1_normal.txt) (the client prints messages in protobuf text format):

```
$ python grpc_version/order_client.py laptop 2
> order.v1.OrderService/PlaceOrder  target=127.0.0.1:50052  deadline=10s
  item_id: "laptop"
  quantity: 2
< OK  (4 ms)
  order_id: "ORD-0001"
  status: "CONFIRMED"
  item_id: "laptop"
  quantity: 2
```

The Order -> Inventory call shows up in both service logs:

```
# evidence/grpc/order_service.log
16:25:03.529 INFO    order-grpc | PlaceOrder item_id=laptop quantity=2 -> InventoryService.CheckAvailability @ 127.0.0.1:50051 (deadline 2.0s)
16:25:03.531 INFO    order-grpc | Inventory OK in 2 ms -> CONFIRMED ORD-0001

# evidence/grpc/inventory_1_normal.log
16:25:03.531 INFO    inventory-grpc | CheckAvailability item_id=laptop quantity=2 (caller deadline in 2.01s)
16:25:03.531 INFO    inventory-grpc | -> OK available_quantity=10 (after 0.00s)
```

When the item isn't available, the call fails with a gRPC status code instead of returning a response:

```
$ python grpc_version/order_client.py monitor 5
> order.v1.OrderService/PlaceOrder  target=127.0.0.1:50052  deadline=10s
  item_id: "monitor"
  quantity: 5
< FAILED_PRECONDITION  (8 ms)
  details: Only 3 unit(s) of 'monitor' in stock, 5 requested

$ python grpc_version/order_client.py phone 1
> order.v1.OrderService/PlaceOrder  target=127.0.0.1:50052  deadline=10s
  item_id: "phone"
  quantity: 1
< NOT_FOUND  (4 ms)
  details: Item 'phone' does not exist

$ python grpc_version/order_client.py laptop 0
> order.v1.OrderService/PlaceOrder  target=127.0.0.1:50052  deadline=10s
  item_id: "laptop"
< INVALID_ARGUMENT  (2 ms)
  details: quantity must be an integer >= 1
```

### REST timeout

For this I restarted the inventory service with `--delay 5` and left the order service running with its 2 second timeout. The request comes back with a 504 after about 2 seconds instead of hanging for 5 ([`evidence/rest/client_2_slow_inventory.txt`](evidence/rest/client_2_slow_inventory.txt)):

```
$ curl -s -i -w '\ntime_total=%{time_total}s\n' -X POST http://127.0.0.1:8000/orders -H "Content-Type: application/json" -d '{"item_id": "laptop", "quantity": 1}'
HTTP/1.1 504 GATEWAY TIMEOUT
Server: Werkzeug/3.1.9 Python/3.13.15
Date: Sun, 04 Oct 2026 23:24:51 GMT
Content-Type: application/json
Content-Length: 88
Connection: close

{"error":"INVENTORY_TIMEOUT","message":"Inventory service did not respond within 2.0s"}

time_total=2.034291s
```

The order service catches the timeout from `requests` and returns the 504:

```
16:24:49.008 INFO    order-rest | POST /orders item_id=laptop quantity=1 -> POST http://127.0.0.1:8001/inventory/check (timeout 2.0s)
16:24:51.040 ERROR   order-rest | Inventory timed out after 2.03s -> 504 Gateway Timeout
```

The inventory service has no idea the order service already gave up, so it sleeps the full 5 seconds and sends back a 200 that nobody receives ([`evidence/rest/inventory_2_delay.log`](evidence/rest/inventory_2_delay.log)):

```
16:24:49.027 INFO    inventory-rest | POST /inventory/check item_id=laptop quantity=1
16:24:49.027 WARNING inventory-rest | simulated slow response: sleeping 5.0s
16:24:54.028 INFO    inventory-rest | -> 200 OK available_quantity=10 (after 5.00s)
```

### gRPC deadline

Same setup: the inventory service restarted with `--delay 5`, and the order service left running with a 2 second deadline ([`evidence/grpc/client_2_slow_inventory.txt`](evidence/grpc/client_2_slow_inventory.txt)):

```
$ python grpc_version/order_client.py laptop 1
> order.v1.OrderService/PlaceOrder  target=127.0.0.1:50052  deadline=10s
  item_id: "laptop"
  quantity: 1
< DEADLINE_EXCEEDED  (2011 ms)
  details: Inventory service did not respond within the 2.0s deadline
```

The order service catches the `DEADLINE_EXCEEDED` error and sends it back to the client:

```
16:25:08.012 INFO    order-grpc | PlaceOrder item_id=laptop quantity=1 -> InventoryService.CheckAvailability @ 127.0.0.1:50051 (deadline 2.0s)
16:25:10.020 ERROR   order-grpc | Inventory exceeded the 2.0s deadline (gave up after 2.01s) -> DEADLINE_EXCEEDED
```

This is different from REST: the deadline is sent along with the gRPC request, so the inventory server knows about it, notices when it runs out, and stops at 2 seconds ([`evidence/grpc/inventory_2_delay.log`](evidence/grpc/inventory_2_delay.log)):

```
16:25:08.014 INFO    inventory-grpc | CheckAvailability item_id=laptop quantity=1 (caller deadline in 2.01s)
16:25:08.014 WARNING inventory-grpc | simulated slow response: sleeping 5.0s
16:25:10.032 WARNING inventory-grpc | caller's deadline expired after 2.02s -> abandoning this request
```

### The order service keeps running

I didn't restart either order service at any point during the demo. Right after each failure the health check still answers with the same pid. Once the inventory service is back to normal, the same process confirms the next order (ORD-0002).

REST, pid 22688 the whole time ([`client_2_slow_inventory.txt`](evidence/rest/client_2_slow_inventory.txt), [`client_3_recovered.txt`](evidence/rest/client_3_recovered.txt)):

```
$ curl -s -i http://127.0.0.1:8000/health          # right after the 504
HTTP/1.1 200 OK
...
{"status":"UP","pid":22688,"uptime_seconds":6.7,"orders_confirmed":1,"orders_rejected":2,"inventory_errors":1}

$ curl -s -i -X POST http://127.0.0.1:8000/orders -H "Content-Type: application/json" -d '{"item_id": "laptop", "quantity": 1}'
HTTP/1.1 201 CREATED
...
{"order_id":"ORD-0002","status":"CONFIRMED","item_id":"laptop","quantity":1}

$ curl -s -i http://127.0.0.1:8000/health
HTTP/1.1 200 OK
...
{"status":"UP","pid":22688,"uptime_seconds":13.0,"orders_confirmed":2,"orders_rejected":2,"inventory_errors":1}
```

gRPC, pid 17952 the whole time ([`client_2_slow_inventory.txt`](evidence/grpc/client_2_slow_inventory.txt), [`client_3_recovered.txt`](evidence/grpc/client_3_recovered.txt)):

```
$ python grpc_version/order_client.py --health     # right after DEADLINE_EXCEEDED
> order.v1.OrderService/Health  target=127.0.0.1:50052  deadline=10s
  (empty message)
< OK  (2 ms)
  status: "UP"
  pid: 17952
  uptime_seconds: 7.5
  orders_confirmed: 1
  orders_rejected: 2
  inventory_errors: 1

$ python grpc_version/order_client.py laptop 1
> order.v1.OrderService/PlaceOrder  target=127.0.0.1:50052  deadline=10s
  item_id: "laptop"
  quantity: 1
< OK  (4 ms)
  order_id: "ORD-0002"
  status: "CONFIRMED"
  item_id: "laptop"
  quantity: 1

$ python grpc_version/order_client.py --health
> order.v1.OrderService/Health  target=127.0.0.1:50052  deadline=10s
  (empty message)
< OK  (2 ms)
  status: "UP"
  pid: 17952
  uptime_seconds: 11.2
  orders_confirmed: 2
  orders_rejected: 2
  inventory_errors: 1
```

In both order service logs, the failure is followed straight away by the next successful order:

```
# evidence/rest/order_service.log
16:24:51.040 ERROR   order-rest | Inventory timed out after 2.03s -> 504 Gateway Timeout
16:24:57.356 INFO    order-rest | POST /orders item_id=laptop quantity=1 -> POST http://127.0.0.1:8001/inventory/check (timeout 2.0s)
16:24:57.377 INFO    order-rest | Inventory 200 OK in 21 ms -> 201 Created ORD-0002

# evidence/grpc/order_service.log
16:25:10.020 ERROR   order-grpc | Inventory exceeded the 2.0s deadline (gave up after 2.01s) -> DEADLINE_EXCEEDED
16:25:13.720 INFO    order-grpc | PlaceOrder item_id=laptop quantity=1 -> InventoryService.CheckAvailability @ 127.0.0.1:50051 (deadline 2.0s)
16:25:13.722 INFO    order-grpc | Inventory OK in 2 ms -> CONFIRMED ORD-0002
```

## REST vs gRPC: what I noticed

The biggest difference for me was where the contract lives. With gRPC I defined the messages and the RPC once in a `.proto` file and generated the code from it, but with REST the contract was really just the URL, the JSON field names, and the status codes I chose, so I had to validate every field by hand in both services. The gRPC types didn't catch everything though, because in proto3 a quantity of 0 looks exactly the same as no quantity at all, so I still needed my own check for that. Timeouts are where the two behaved most differently: when the REST call timed out, the inventory service kept working and sent its response 3 seconds after nobody was listening, but with gRPC the deadline travels with the request, so the inventory server saw it expire and stopped at 2 seconds. On the other hand, REST was much easier to test and debug since I could just use curl and read the JSON, while gRPC needed generated code, matching package versions, and a separate client script.
