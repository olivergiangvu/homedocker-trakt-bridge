#!/usr/bin/env python3
"""Disposable R23D-2B1N Docker-bridge fake TCP probe. No real credentials."""
import argparse
import ipaddress
import selectors
import socket
import sys

DEST="172.31.252.3"
PORTS=(17420,17421)
PAYLOAD=b"r23d2b1n-fake"
class Stop(Exception): pass

def fake_server():
    sel=selectors.DefaultSelector()
    with sel:
        for port in PORTS:
            server=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            server.bind(("0.0.0.0",port))
            server.listen(16)
            server.setblocking(False)
            sel.register(server,selectors.EVENT_READ)
        while True:
            for key,_ in sel.select(timeout=1):
                conn,_=key.fileobj.accept()
                with conn:
                    conn.settimeout(0.5)
                    try:
                        if conn.recv(64)==PAYLOAD:
                            conn.sendall(b"ok")
                    except (OSError,TimeoutError):pass

def client_probe(addr,port):
    try:
        if str(ipaddress.ip_address(addr))!=DEST or port not in PORTS:
            raise Stop("OUTSIDE_SYNTHETIC_DOCKER_PRIVATE_LAB")
    except ValueError:
        raise Stop("INVALID_PRIVATE_LAB_DESTINATION") from None
    try:
        with socket.create_connection((addr,port),timeout=0.7) as sock:
            sock.settimeout(0.7)
            sock.sendall(PAYLOAD)
            if sock.recv(2)==b"ok":return
    except (OSError,TimeoutError):pass
    raise Stop("FAKE_TCP_NOT_REACHABLE")

if __name__=="__main__":
    p=argparse.ArgumentParser()
    x=p.add_mutually_exclusive_group(required=True)
    x.add_argument("--serve",action="store_true")
    x.add_argument("--probe",action="store_true")
    p.add_argument("--addr")
    p.add_argument("--port",type=int)
    a=p.parse_args()
    try:
        if a.serve:
            if a.addr is not None or a.port is not None:
                raise Stop("INVALID_SERVER_ARGS")
            fake_server()
        else:
            if a.addr is None or a.port is None:
                raise Stop("MISSING_PROBE_ARGS")
            client_probe(a.addr,a.port)
            print("R23D2B1N_FAKE_TCP_ACCEPTED")
    except Stop as e:
        print("R23D2B1N_"+str(e),file=sys.stderr)
        raise SystemExit(1)
