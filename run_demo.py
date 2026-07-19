#!/usr/bin/env python3
"""Start the text2shacl demo UI and the local backend services together.

  parse-ontology        :9100
  find-relevant-terms   :9101
  build-shacl-shape     :9102   (+ /validate-shape)
  generate-from-guide   :9103   (SSE)
  rule-target-resolver  :9104   (read-only inspection)
  web app (static)      :8768   → demo/

Inference credentials, model ids and temperature are configured from the UI.
"""

from pathlib import Path
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "text2shacl_core"))

from deployment_policy import (  # noqa: E402
    DEPLOYMENT_PROFILE_ENV,
    SUPPORTED_PROFILES,
    capabilities,
    normalize_deployment_profile,
)

PYTHON = sys.executable or shutil.which("python3")
WEB_HOST = "127.0.0.1"
WEB_PORT = 8768

PROCESSES = [
    ("parse-ontology",      [PYTHON, "services/parse_ontology.py"]),
    ("find-relevant-terms", [PYTHON, "services/find_relevant_terms.py"]),
    ("build-shacl-shape",   [PYTHON, "services/build_shacl_shapes.py"]),
    ("generate-from-guide", [PYTHON, "services/generate_from_guide.py"]),
    ("rule-target-resolver", [PYTHON, "services/rule_target_resolver.py"]),
]


class NoCacheHTTPRequestHandler(SimpleHTTPRequestHandler):
    """Static-file handler for development: always ask browsers to revalidate.

    The demo UI changes often while iterating. Chrome can otherwise keep using
    cached HTML/CSS/JS across restarts, which makes it look like run_demo.py is
    serving an older version.
    """

    deployment_profile = "local"

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/api/capabilities":
            body = json.dumps(capabilities(self.deployment_profile)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


def parse_args(argv=None):
    """Parse demo deployment options without reading user inference settings."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--deployment-profile",
        choices=SUPPORTED_PROFILES,
        default=normalize_deployment_profile(os.environ.get(DEPLOYMENT_PROFILE_ENV, "local")),
        help="local enables local Hugging Face inference; public disables it (default: local)",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
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

    child_env = os.environ.copy()
    child_env[DEPLOYMENT_PROFILE_ENV] = args.deployment_profile
    for name, command in PROCESSES:
        child = subprocess.Popen(command, cwd=ROOT, env=child_env, start_new_session=True)
        children.append(child)
        print(f"started {name}: {' '.join(command)}")
        time.sleep(0.2)

    NoCacheHTTPRequestHandler.deployment_profile = args.deployment_profile
    web_handler = partial(NoCacheHTTPRequestHandler, directory=str(ROOT / "demo"))
    web_server = ThreadingHTTPServer((WEB_HOST, WEB_PORT), web_handler)
    threading.Thread(target=web_server.serve_forever, daemon=True).start()

    print(f"\nDemo:    http://{WEB_HOST}:{WEB_PORT}/index.html")
    print(f"Profile: {args.deployment_profile}")
    print(f"  Rule → Shape:   http://{WEB_HOST}:{WEB_PORT}/rule.html")
    print(f"  Guide → Shapes: http://{WEB_HOST}:{WEB_PORT}/guide.html")
    print("\nServices: :9100 parse · :9101 terms · :9102 build/validate · :9103 guide (SSE) · :9104 rule targets")
    print("\nPress Ctrl+C to stop everything.")

    while True:
        for (name, _), child in zip(PROCESSES, children):
            if child.poll() is not None:
                print(f"{name} stopped with exit code {child.returncode}")
                stop_all()
        time.sleep(1)


if __name__ == "__main__":
    main()
