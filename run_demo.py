#!/usr/bin/env python3
"""Start the text2shacl demo UI and the four local backend services together.

  parse-ontology        :9100
  find-relevant-terms   :9101
  build-shacl-shape     :9102   (+ /validate-shape)
  generate-from-guide   :9103   (SSE)
  web app (static)      :8768   → demo/

Databricks credentials are read from the environment (a .env file in this folder
is loaded automatically if python-dotenv is installed). The API key can also be
pasted in the UI, which overrides DATABRICKS_TOKEN per request.
"""

from pathlib import Path
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
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


def load_env_file(path: Path):
    """Load KEY=VALUE pairs from a .env into os.environ.

    Uses python-dotenv if available, otherwise a minimal built-in parser, so the
    demo works even without the package installed. Existing environment variables
    are not overridden.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(path)
        return
    except Exception:
        pass
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_env_file(ROOT / ".env")

PROCESSES = [
    ("parse-ontology",      [PYTHON, "services/parse_ontology.py"]),
    ("find-relevant-terms", [PYTHON, "services/find_relevant_terms.py"]),
    ("build-shacl-shape",   [PYTHON, "services/build_shacl_shapes.py"]),
    ("generate-from-guide", [PYTHON, "services/generate_from_guide.py"]),
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

    tok = os.environ.get("DATABRICKS_TOKEN")
    url = os.environ.get("DATABRICKS_BASE_URL")
    hf = os.environ.get("HF_TOKEN")
    print("Credentials detected:")
    print(f"  DATABRICKS_TOKEN    : {'set' if tok else 'MISSING'}")
    print(f"  DATABRICKS_BASE_URL : {url if url else 'MISSING'}")
    print(f"  HF_TOKEN            : {'set' if hf else 'not set (only needed for HuggingFace backend)'}")
    if not tok or not url:
        print("  ! Databricks generation will fail until both are set in "
              f"{ROOT / '.env'} (or exported in this shell).")

    env = os.environ.copy()
    for name, command in PROCESSES:
        child = subprocess.Popen(command, cwd=ROOT, start_new_session=True, env=env)
        children.append(child)
        print(f"started {name}: {' '.join(command)}")
        time.sleep(0.2)

    web_handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT / "demo"))
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
