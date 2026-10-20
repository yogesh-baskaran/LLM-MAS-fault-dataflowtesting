"""Small connectivity checks; no test case or inference execution."""
import argparse
import os
import socket
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ports', type=int, nargs='+')
    parser.add_argument('--wait-port', type=int)
    parser.add_argument('--wait-url')
    parser.add_argument('--attempts', type=int, default=60)
    parser.add_argument('--pid', type=int)
    args = parser.parse_args()
    sockets = []
    try:
        for port in args.ports or []:
            sock = socket.socket()
            sockets.append(sock)
            sock.bind(('127.0.0.1', port))
    finally:
        for sock in sockets:
            sock.close()
    if not (args.wait_port or args.wait_url):
        return
    last = None
    for _ in range(args.attempts):
        if args.pid:
            try:
                os.kill(args.pid, 0)
            except ProcessLookupError as error:
                raise RuntimeError('Owned service exited before readiness; inspect its job log') from error
        try:
            if args.wait_url:
                with urllib.request.urlopen(args.wait_url, timeout=2) as response:
                    if response.status != 200:
                        raise RuntimeError('Service not ready')
            else:
                with socket.create_connection(('127.0.0.1', args.wait_port), timeout=2):
                    pass
            return
        except (OSError, RuntimeError) as error:
            last = error
            time.sleep(1)
    raise RuntimeError('Service readiness timed out') from last


if __name__ == '__main__':
    main()
