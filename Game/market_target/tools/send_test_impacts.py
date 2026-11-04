"""Test sender for the game's UDP JSON input (TEST TOOL, not a sensor driver).

Sends the same messages a real sensor bridge or the registration platform
would send, so the installation can be checked end to end before the sensor is
connected. It is also the reference for whoever writes the sensor bridge.

    python tools/send_test_impacts.py start
    python tools/send_test_impacts.py start --player-id 1042 --player-name "سارا"
    python tools/send_test_impacts.py impact 0.5 0.4          # normalised x y (0..1, top-left origin)
    python tools/send_test_impacts.py impact 150 160 --units cm
    python tools/send_test_impacts.py random 10 --interval 2.5
    python tools/send_test_impacts.py demo                     # start + 10 random throws
    python tools/send_test_impacts.py missed | reset | heartbeat
"""
from __future__ import annotations

import argparse
import json
import random
import socket
import sys
import time


def send(sock: socket.socket, addr: tuple[str, int], msg: dict) -> None:
    sock.sendto(json.dumps(msg, ensure_ascii=False).encode("utf-8"), addr)
    print("sent", json.dumps(msg, ensure_ascii=False).encode("ascii", "backslashreplace").decode())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9100)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("start", help="start a round (like the operator start key)")
    p.add_argument("--player-id", default="")
    p.add_argument("--player-name", default="")
    p = sub.add_parser("player", help="announce the next player (registration platform)")
    p.add_argument("id")
    p.add_argument("name", nargs="?", default="")
    p = sub.add_parser("impact", help="one ball impact")
    p.add_argument("x", type=float)
    p.add_argument("y", type=float)
    p.add_argument("--units", choices=["normalized", "cm", "m"], default="normalized",
                   help="only for readability: the game's input.udp_json.units decides how x/y are read")
    p = sub.add_parser("random", help="random impacts across the wall")
    p.add_argument("count", type=int)
    p.add_argument("--interval", type=float, default=2.0)
    p = sub.add_parser("demo", help="start a round, then throw 10 random balls")
    p.add_argument("--interval", type=float, default=3.0)
    sub.add_parser("missed", help="a ball that never reached the wall")
    sub.add_parser("reset", help="abort the round, back to the attract screen")
    sub.add_parser("heartbeat", help="sensor keep-alive")
    args = ap.parse_args(argv)

    addr = (args.host, args.port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if args.cmd == "start":
        msg: dict = {"type": "start"}
        if args.player_id or args.player_name:
            msg["player"] = {"id": args.player_id, "name": args.player_name}
        send(sock, addr, msg)
    elif args.cmd == "player":
        send(sock, addr, {"type": "player", "id": args.id, "name": args.name})
    elif args.cmd == "impact":
        send(sock, addr, {"type": "impact", "x": args.x, "y": args.y})
    elif args.cmd in ("random", "demo"):
        count = args.count if args.cmd == "random" else 10
        if args.cmd == "demo":
            send(sock, addr, {"type": "start"})
            time.sleep(1.0)
            send(sock, addr, {"type": "start"})  # skip the instructions
            time.sleep(3.5)  # countdown
        for _ in range(count):
            send(sock, addr, {"type": "impact", "x": round(random.uniform(0.05, 0.95), 4),
                              "y": round(random.uniform(0.18, 0.9), 4)})
            time.sleep(args.interval)
    elif args.cmd == "missed":
        send(sock, addr, {"type": "throw_missed"})
    else:
        send(sock, addr, {"type": args.cmd})
    return 0


if __name__ == "__main__":
    sys.exit(main())
