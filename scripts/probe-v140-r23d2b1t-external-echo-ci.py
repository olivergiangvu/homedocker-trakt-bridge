#!/usr/bin/env python3
"""B1T synthetic external echo endpoint (isolated Linux network namespace ONLY)."""
import argparse
import ipaddress
import socket
import sys

EXT="172.31.249.2"
PORT=17440
TARGET="172.31.249.1"
def serve():
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        s.bind((EXT,PORT))
        s.listen(8)
        s.settimeout(1)
        print("R23D2B1T_EXTERNAL_NAMESPACE_ECHO_LISTENING",flush=True)
        while True:
            try:
                c,peer=s.accept()
            except socket.timeout:
                continue
            with c:
                c.settimeout(1)
                try:
                    if c.recv(32)==b"r23d2b1t-synthetic":
                        c.sendall(peer[0].encode("ascii"))
                except OSError:
                    pass
def probe():
    with socket.create_connection((EXT,PORT),timeout=2) as s:
        s.sendall(b"r23d2b1t-synthetic")
        seen=s.recv(64).decode("ascii")
    if seen!=TARGET:
        print("R23D2B1T_NAT_SOURCE_NOT_HOST_VETH",file=sys.stderr)
        raise SystemExit(5)
    print("R23D2B1T_REAL_DOCKER_NONINTERNAL_EGRESS_SNAT_PASS",flush=True)
if __name__=="__main__":
    p=argparse.ArgumentParser()
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument("--serve",action="store_true")
    g.add_argument("--probe",action="store_true")
    a=p.parse_args()
    if a.serve:serve()
    else:probe()
