# Deployment Profiles

SHARD uses one codebase with two inference policies. Deployment profiles decide
which providers may execute; they do not authenticate API clients.

| Profile | Intended use | Remote inference | Local Hugging Face |
| --- | --- | --- | --- |
| `local` | Development and self-hosting | Enabled | Enabled |
| `public` | Hosted public demo | Enabled | Disabled |

## Configuration

SHARD reads process variables first and then loads missing values from the first
`.env` found in the working directory or project root. Start with the bundled
template:

```bash
cp .env.example .env
```

For local development, the defaults are sufficient:

```bash
SHARD_DEPLOYMENT_PROFILE=local
SHARD_SERVICE_LAYOUT=unified
SHARD_HOST=127.0.0.1
SHARD_PORT=8768
```

For a public listener behind a reverse proxy:

```bash
SHARD_DEPLOYMENT_PROFILE=public
SHARD_SERVICE_LAYOUT=unified
SHARD_HOST=127.0.0.1
SHARD_PORT=8000
DATABRICKS_BASE_URL=https://workspace.example/ai-gateway/mlflow/v1
DATABRICKS_TOKEN=replace-with-a-secret
SHARD_CORS_ALLOWED_ORIGINS=https://apps.citius.gal
SHARD_TRUSTED_PROXY_IPS=127.0.0.1
```

Run either the source launcher or installed command:

```bash
python run_demo.py
# or
shard
```

Never commit `.env`. Provider tokens must remain in the process environment,
secret manager or write-only API request fields. They are not returned by the
API, included in provenance, exported in sessions or logged by SHARD.

## Inference Policy

The local profile exposes remote-provider configuration so each user can supply
their own endpoint and token. It also permits local Hugging Face execution. No
model is selected or downloaded automatically; downloading begins only after an
explicit user confirmation.

The public profile hides provider configuration and local-model controls. The
server supplies remote credentials and independently rejects attempts to invoke
local models, including handcrafted API requests.

`GET /api/v1/capabilities` reports the active profile and non-secret provider
capabilities. It never exposes provider URLs or credentials.

## Unified and Split Layouts

`unified` is the deployment layout. The main listener serves static frontend
assets, JSON endpoints, jobs, Swagger, ReDoc and SSE under one origin.

`split` exists for API v1 compatibility and comparative local operation. It
starts internal adapters on ports `9100`-`9104`; the adapters call the same
application functions as the unified API. Do not expose those ports publicly.
The former `BR2SHACL_DEPLOYMENT_PROFILE` and `BR2SHACL_SERVICE_LAYOUT`
environment names remain accepted only as compatibility aliases.

## Reverse Proxy Under `/shard/`

Frontend assets are relative and `frontend/js/core.js` defaults to the relative
API base `api/v1/`. A page served as `https://apps.citius.gal/shard/rule.html`
therefore calls `https://apps.citius.gal/shard/api/v1/...` automatically.
`window.SHARD_API_BASE` can override this base before `core.js` loads, but a
same-origin relative path is preferred.

The reverse proxy must strip `/shard` before forwarding to SHARD. A representative
nginx configuration is:

```nginx
location = /shard/api/v1/batches/generate {
    proxy_pass http://127.0.0.1:8000/api/v1/batches/generate;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 7200s;
    proxy_send_timeout 7200s;
}

location /shard/ {
    proxy_pass http://127.0.0.1:8000/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 7200s;
    proxy_send_timeout 7200s;
}
```

The exact SSE location is placed first so nginx does not buffer generation
events. The trailing slash in the general `proxy_pass` removes the public
`/shard/` prefix.

## Operational Safeguards

SHARD applies configurable limits for request size, request rates, external
timeouts, concurrent workflows, model downloads and queued jobs. Public reverse
proxies must allow request bodies at least as large as the SHARD resources they
are expected to accept. See [REST API](api.md#operational-safeguards) for every
environment variable and default.

`SHARD_TRUSTED_PROXY_IPS` controls whether `X-Forwarded-For` is trusted for rate
limiting. Configure only the actual reverse-proxy addresses. Use an explicit
`SHARD_CORS_ALLOWED_ORIGINS` allowlist whenever the API and UI are not strictly
same-origin.

The application policy is not a complete security perimeter. Public deployments
still require HTTPS, network restrictions, process supervision and any desired
client authentication at the reverse proxy or platform boundary.
