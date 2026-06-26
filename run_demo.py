#!/usr/bin/env python3
"""Start the text2shacl demo UI and the four local backend services together.

  parse-ontology        :9100
  find-relevant-terms   :9101
  build-shacl-shape     :9102   (+ /validate-shape)
  generate-from-guide   :9103   (SSE)
  web app (static)      :8768   → demo/

Inference credentials, model ids and temperature are configured from the UI.
"""

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
    ("parse-ontology",      [PYTHON, "services/parse_ontology.py"]),
    ("find-relevant-terms", [PYTHON, "services/find_relevant_terms.py"]),
    ("build-shacl-shape",   [PYTHON, "services/build_shacl_shapes.py"]),
    ("generate-from-guide", [PYTHON, "services/generate_from_guide.py"]),
]


class NoCacheHTTPRequestHandler(SimpleHTTPRequestHandler):
    """Static-file handler for development: always ask browsers to revalidate.

    The demo UI changes often while iterating. Chrome can otherwise keep using
    cached HTML/CSS/JS across restarts, which makes it look like run_demo.py is
    serving an older version.
    """

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


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

    web_handler = partial(NoCacheHTTPRequestHandler, directory=str(ROOT / "demo"))
    web_server = ThreadingHTTPServer((WEB_HOST, WEB_PORT), web_handler)
    threading.Thread(target=web_server.serve_forever, daemon=True).start()

    print(f"\nDemo:    http://{WEB_HOST}:{WEB_PORT}/index.html")
    print(f"  Rule → Shape:   http://{WEB_HOST}:{WEB_PORT}/rule.html")
    print(f"  Guide → Shapes: http://{WEB_HOST}:{WEB_PORT}/guide.html")
    print("\nServices: :9100 parse · :9101 terms · :9102 build/validate · :9103 guide (SSE)")
    print("\nPress Ctrl+C to stop everything.")

    while True:
        for (name, _), child in zip(PROCESSES, children):
            if child.poll() is not None:
                print(f"{name} stopped with exit code {child.returncode}")
                stop_all()
        time.sleep(1)


if __name__ == "__main__":
    main()
