#!/usr/bin/env python3
"""B1P dummy TCP loopback-only admission session test in a private Docker bridge."""
import argparse
import socket
import socketserver
import threading
import time
import sys
HOST="172.31.252.3"
PORTS=(17420,17421)
TOKEN=b"fakeping"
class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(6)
        while True:
            try:
                payload=self.request.recv(len(TOKEN))
                if not payload or payload!=TOKEN: return
                self.request.sendall(b"ok")
            except (OSError,TimeoutError):
                return
class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True
def serve():
    servers=[]
    for port in PORTS:
        s=Server(("0.0.0.0",port),Handler)
        servers.append(s)
        threading.Thread(target=s.serve_forever,daemon=True).start()
    while True: time.sleep(30)
def connect(addr,port):
    if addr!=HOST or port not in PORTS: raise ValueError("INVALID_FAKE_DEST")
    return socket.create_connection((addr,port),timeout=0.7)
def probe(addr,port):
    try:
        with connect(addr,port) as c:
            c.settimeout(0.7)
            c.sendall(TOKEN)
            return c.recv(2)==b"ok"
    except (OSError,TimeoutError):return False
def session(addr,port):
    try:
        with connect(addr,port) as c:
            c.settimeout(0.75)
            c.sendall(TOKEN)
            if c.recv(2)!=b"ok":raise OSError("missing first reply")
            print("R23D2B1P_EXISTING_TCP_SESSION_ESTABLISHED",flush=True)
            # TCP stays ESTABLISHED while the kernel lease expires after dual
            # SIGKILL. If it keeps getting responses this test MUST FAIL.
            for _ in range(48):
                time.sleep(0.22)
                c.sendall(TOKEN)
                if c.recv(2)!=b"ok":
                    print("R23D2B1P_ESTABLISHED_TCP_NO_LONGER_REACHABLE",flush=True)
                    return 0
            print("R23D2B1P_UNSAFE_SESSION_STAYED_OPEN",file=sys.stderr)
            return 5
    except (OSError,TimeoutError):
        # A failed session is acceptable ONLY if it was established first;
        # the shell checks this marker before it triggers the dual SIGKILL.
        print("R23D2B1P_ESTABLISHED_TCP_NO_LONGER_REACHABLE",flush=True)
        return 0
def main():
    p=argparse.ArgumentParser()
    modes=p.add_mutually_exclusive_group(required=True)
    modes.add_argument("--serve",action="store_true")
    modes.add_argument("--probe",action="store_true")
    modes.add_argument("--session",action="store_true")
    p.add_argument("--addr",default=HOST)
    p.add_argument("--port",type=int,default=17420)
    a=p.parse_args()
    if a.serve:
        if (a.addr,a.port)!=(HOST,17420):raise SystemExit(4)
        serve()
    if (a.addr not in (HOST,) or a.port not in PORTS):raise SystemExit(4)
    if a.probe:
        if not probe(a.addr,a.port):raise SystemExit(1)
        print("R23D2B1P_NEW_TCP_CONNECTED")
    if a.session:raise SystemExit(session(a.addr,a.port))
if __name__=="__main__":main()
