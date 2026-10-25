import argparse
import secrets
import socket
import threading
import webbrowser

import uvicorn

from .server import create_app


def local_socket(port):
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", port))
        return sock
    except OSError:
        sock.close()
        raise


def main():
    parser = argparse.ArgumentParser(description="iBackup — lokale iPod-Ansicht")
    parser.add_argument("--backup", help="Backupordner oder Ordner mit mehreren Backups")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        sock = local_socket(args.port)
    except OSError:
        parser.exit(1, "Dieser Port ist belegt. Du kannst mit --port 8766 einen anderen verwenden.\n")
    port = sock.getsockname()[1]
    token = secrets.token_urlsafe(32)
    url = f"http://127.0.0.1:{port}/#token={token}"
    app = create_app([args.backup] if args.backup else None, token, port)
    print(f"iBackup läuft lokal: {url}\nBeenden: Ctrl+C. Das Backup bleibt unverändert.", flush=True)
    if not args.no_browser:
        timer = threading.Timer(1.0, webbrowser.open, args=(url,))
        timer.daemon = True
        timer.start()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False, log_level="warning", proxy_headers=False)
    try:
        uvicorn.Server(config).run(sockets=[sock])
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


if __name__ == "__main__":
    main()
