#!/usr/bin/env python3
"""R23D-2B1K: CI-hosted private-network-namespace fake TCP helper.

Only loopback/isolated TEST addresses supplied by the associated CI wrapper.
This is not a real HomeDocker gateway/relay and never reads credentials or DBs.
"""
import argparse
import ipaddress
import socket
import sys
import time

ALLOWED_IPS={"172.31.254.1","172.31.253.2","fd42:23d:1::1"}
ALLOWED_PORTS={17420,17421}
class Stop(Exception):
    pass

def validate(addr,port):
    try:
        x=ipaddress.ip_address(addr)
    except ValueError:
        raise Stop("INVALID_SYNTHETIC_ADDRESS") from None
    if str(x)!=addr or addr not in ALLOWED_IPS or port not in ALLOWED_PORTS:
        raise Stop("ADDRESS_OR_PORT_NOT_IN_CI_LAB")
    return socket.AF_INET6 if x.version==6 else socket.AF_INET

def serve(addr,port):
    family=validate(addr,port)
    with socket.socket(family,socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        server.bind((addr,port))
        server.listen(16)
        server.settimeout(1)
        while True:
            try:
                peer,_=server.accept()
            except socket.timeout:
                continue
            with peer:
                peer.settimeout(1)
                try:
                    data=peer.recv(16)
                    if data==b"r23d-ci-probe":
                        peer.sendall(b"ok")
                except (OSError,TimeoutError):
                    pass

def probe(addr,port):
    family=validate(addr,port)
    try:
        with socket.socket(family,socket.SOCK_STREAM) as conn:
            conn.settimeout(0.6)
            conn.connect((addr,port))
            conn.sendall(b"r23d-ci-probe")
            return conn.recv(2)==b"ok"
    except (OSError,TimeoutError):
        return False

def main():
    parser=argparse.ArgumentParser()
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--serve",action="store_true")
    group.add_argument("--probe",action="store_true")
    parser.add_argument("--addr",required=True)
    parser.add_argument("--port",required=True,type=int)
    a=parser.parse_args()
    if a.serve:
        serve(a.addr,a.port)
    elif probe(a.addr,a.port):
        print("SYNTHETIC_TCP_CONNECTED")
    else:
        print("SYNTHETIC_TCP_DENIED")
        raise SystemExit(1)

if __name__=="__main__":
    main()
