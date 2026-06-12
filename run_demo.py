#!/usr/bin/env python3
"""Start the text2shacl demo UI and local backend services together."""

from pathlib import Path
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import shutil
import signal
import subprocess
import sys
import threading
import time


ROOT = Path(__file__).resolve().parent
PYTHON = shutil.which("python3") or sys.executable
WEB_HOST = "127.0.0.1"
WEB_PORT = 8768
PROCESSES = [
    ("ontology parser", [PYTHON, "services/parse_ontology.py"]),
    ("relevant terms", [PYTHON, "services/find_relevant_terms.py"]),
    ("shacl builder", [PYTHON, "services/build_shacl_shapes.py"]),
]


def main():
    children = []
    web_server = None

    def stop_all(*_):
        if web_server:
            web_server.shutdown()
            web_server.server_close()
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=4)
            except subprocess.TimeoutExpired:
                child.kill()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)

    for name, command in PROCESSES:
        child = subprocess.Popen(command, cwd=ROOT, start_new_session=True)
        children.append(child)
        print(f"started {name}: {' '.join(command)}")
        time.sleep(0.2)

    web_handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT / "demo"))
    web_server = ThreadingHTTPServer((WEB_HOST, WEB_PORT), web_handler)
    web_thread = threading.Thread(target=web_server.serve_forever, daemon=True)
    web_thread.start()
    print(f"started web app: http://{WEB_HOST}:{WEB_PORT}")

    print(f"\nDemo: http://{WEB_HOST}:{WEB_PORT}")
    print("Parser: http://127.0.0.1:9100/parse-ontology")
    print("Relevant terms: http://127.0.0.1:9101/find-relevant-terms")
    print("SHACL builder: http://127.0.0.1:9102/build-shacl-shape")
    print("\nPress Ctrl+C to stop everything.")

    while True:
        for name, child in zip([name for name, _ in PROCESSES], children):
            if child.poll() is not None:
                print(f"{name} stopped with exit code {child.returncode}")
                stop_all()
        time.sleep(1)


if __name__ == "__main__":
    main()
