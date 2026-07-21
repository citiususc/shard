"""Command-line entry point for the SHARD web application."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv

from shard.api.compat import compatibility_server_specs
from shard.api.contract import (
    SERVICE_LAYOUT_ENV,
    SPLIT_LAYOUT,
    SUPPORTED_SERVICE_LAYOUTS,
    UNIFIED_LAYOUT,
    get_service_layout,
)
from shard.api.router import dispatch_api_request
from shard.deployment.policy import (
    DEPLOYMENT_PROFILE_ENV,
    SUPPORTED_PROFILES,
    get_deployment_profile,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable or shutil.which("python3")
DEFAULT_WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 8768


def load_environment(env_file=None):
    """Load the nearest SHARD .env file without overriding process settings."""
    candidates = (
        (Path(env_file).expanduser(),) if env_file else (
            Path.cwd() / ".env",
            PROJECT_ROOT / ".env",
        )
    )
    visited = set()
    for candidate in candidates:
        path = candidate.resolve()
        if path in visited:
            continue
        visited.add(path)
        if path.is_file():
            load_dotenv(dotenv_path=path, override=False)
            return path
    return None


def _frontend_root() -> Path:
    """Locate frontend assets in a source tree or an installed distribution."""
    candidates = (
        PROJECT_ROOT / "frontend",
        Path(__file__).resolve().parents[1] / "share" / "shard" / "frontend",
        Path(sys.prefix) / "share" / "shard" / "frontend",
    )
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    searched = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(f"SHARD frontend assets were not found in: {searched}")


FRONTEND_ROOT = _frontend_root()

SPLIT_PROCESSES = tuple(
    (
        service_id,
        [PYTHON, "-m", "shard.api.compat", service_id],
    )
    for service_id in (
        "ontology",
        "term-ranking",
        "shapes",
        "batch",
        "target-resolution",
    )
)


class ApplicationHTTPRequestHandler(SimpleHTTPRequestHandler):
    """Serve frontend files and, in unified mode, the same-origin API."""

    deployment_profile = "local"
    service_layout = UNIFIED_LAYOUT

    def log_message(self, *args):
        pass

    def _dispatch_api(self):
        path = self.path.split("?", 1)[0]
        system_request = path in {
            "/api/v1",
            "/api/v1/docs",
            "/api/v1/redoc",
            "/api/v1/openapi.json",
            "/api/v1/capabilities",
            "/api/v1/health",
        }
        job_request = (
            path == "/api/v1/ontology/indexes"
            or path.startswith("/api/v1/ontology/indexes/")
            or path == "/api/v1/models/local/downloads"
            or path.startswith("/api/v1/models/local/downloads/")
        )
        if self.service_layout == UNIFIED_LAYOUT or system_request or job_request:
            return dispatch_api_request(self)
        return False

    def do_OPTIONS(self):
        if self._dispatch_api():
            return
        self.send_error(404, "Unknown endpoint")

    def do_GET(self):
        if self._dispatch_api():
            return
        super().do_GET()

    def do_POST(self):
        if self._dispatch_api():
            return
        self.send_error(404, "Unknown endpoint")

    def do_DELETE(self):
        if self._dispatch_api():
            return
        self.send_error(404, "Unknown endpoint")

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


NoCacheHTTPRequestHandler = ApplicationHTTPRequestHandler


def parse_args(argv=None):
    """Parse deployment options without reading inference credentials."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--deployment-profile",
        choices=SUPPORTED_PROFILES,
        default=get_deployment_profile(),
        help="local enables Hugging Face; public permits remote inference only",
    )
    parser.add_argument(
        "--service-layout",
        choices=SUPPORTED_SERVICE_LAYOUTS,
        default=get_service_layout(),
        help="unified serves one API; split starts compatibility processes",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("SHARD_HOST", DEFAULT_WEB_HOST),
        help=f"web listener host (default: {DEFAULT_WEB_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("SHARD_PORT", DEFAULT_WEB_PORT)),
        help=f"web listener port (default: {DEFAULT_WEB_PORT})",
    )
    return parser.parse_args(argv)


def _start_server(host, port, handler):
    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def main(argv=None):
    """Start SHARD and block until interrupted."""
    env_file = load_environment()
    args = parse_args(argv)
    children = []
    servers = []
    stopping = False

    os.environ[DEPLOYMENT_PROFILE_ENV] = args.deployment_profile
    os.environ[SERVICE_LAYOUT_ENV] = args.service_layout

    def stop_all(*_):
        nonlocal stopping
        if stopping:
            return
        stopping = True
        for server in servers:
            server.shutdown()
            server.server_close()
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

    if args.service_layout == SPLIT_LAYOUT:
        child_env = os.environ.copy()
        source_package_root = PROJECT_ROOT / "src"
        if source_package_root.is_dir():
            existing_path = child_env.get("PYTHONPATH", "")
            child_env["PYTHONPATH"] = os.pathsep.join(
                value for value in (str(source_package_root), existing_path) if value
            )
        for name, command in SPLIT_PROCESSES:
            child = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                env=child_env,
                start_new_session=True,
            )
            children.append(child)
            print(f"started {name}: {' '.join(command)}")
            time.sleep(0.2)
    else:
        for name, port, handler in compatibility_server_specs():
            servers.append(_start_server(DEFAULT_WEB_HOST, port, handler))
            print(f"started compatibility listener {name}: http://{DEFAULT_WEB_HOST}:{port}")

    ApplicationHTTPRequestHandler.deployment_profile = args.deployment_profile
    ApplicationHTTPRequestHandler.service_layout = args.service_layout
    web_handler = partial(ApplicationHTTPRequestHandler, directory=str(FRONTEND_ROOT))
    servers.append(_start_server(args.host, args.port, web_handler))

    display_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    if env_file:
        print(f"Environment: {env_file}")
    print(f"\nSHARD:   http://{display_host}:{args.port}/index.html")
    print(f"Profile: {args.deployment_profile}")
    print(f"Layout:  {args.service_layout}")
    print(f"API:     http://{display_host}:{args.port}/api/v1")
    print(f"  Rule -> Shape:   http://{display_host}:{args.port}/rule.html")
    print(f"  Batch -> Shapes: http://{display_host}:{args.port}/batch.html")
    print("\nCompatibility ports: :9100 ontology, :9101 terms, :9102 shapes, "
          ":9103 batch, :9104 resolver")
    print("\nPress Ctrl+C to stop everything.")

    while True:
        for (name, _), child in zip(SPLIT_PROCESSES, children):
            if child.poll() is not None:
                print(f"{name} stopped with exit code {child.returncode}")
                stop_all()
        time.sleep(1)
