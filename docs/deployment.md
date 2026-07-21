# Deployment Profiles

The same codebase supports two explicit deployment policies. A profile controls
which inference providers may execute. Remote credentials are deployment
secrets, while model choices remain browser settings.

| Profile | Intended use | Databricks | Hugging Face |
| --- | --- | --- | --- |
| `local` | Development and self-hosted research | Remote | Local, enabled |
| `public` | Publicly hosted demo | Remote | Local, disabled |

Run the local profile with:

```bash
python3 run_demo.py --deployment-profile local
```

Run the hosted-demo policy with:

```bash
python3 run_demo.py --deployment-profile public --host 0.0.0.0
```

In the public profile the frontend removes local-model configuration and links
to the project repository. Every inference endpoint independently enforces the
same policy, so a handcrafted request cannot cause the hosted server to load a
local model. The browser presents the hosted backend simply as remote inference
and never receives its base URL or token. It also hides custom-model controls;
the deployment decides which remote model options are offered.

No deployment profile selects a model automatically. In the local profile,
selecting a model first performs an offline cache check. A missing model remains
disabled until the user confirms an explicit download, whose progress is
streamed to the model panel.

For public deployments, configure the remote backend in the server process or
its secret manager:

```bash
export DATABRICKS_BASE_URL="https://workspace.example/ai-gateway/mlflow/v1"
export DATABRICKS_TOKEN="..."
python3 run_demo.py --deployment-profile public --host 0.0.0.0
```

Alternatively, copy `.env.example` to `.env` and replace its placeholder
credentials. Both `python run_demo.py` and the installed `shard` command load
`.env` automatically from the working directory or project root. Variables
already supplied by the shell, systemd or another secret manager are never
overwritten by the file. Only the loaded file path is reported at startup;
secret values are not printed.

API clients may override these values with request-scoped credentials when the
deployment policy permits it. Request values take precedence over server
environment values.

In the local profile, the browser exposes provider configuration so each user
can supply their own remote URL and token. These write-only request values are
not session-exported, logged or returned by the API.

The profile is an application capability policy, not a production network
configuration. SHARD does not authenticate clients against its own API. A public
deployment should place the application behind HTTPS, explicit network policy,
the configured request-size/rate safeguards and a reverse proxy, and expose only
the unified application endpoint. Compatibility ports are intended for loopback
development and comparative experiments.

`GET /api/v1/capabilities` reports the active profile, provider execution modes,
repository URL and API catalog. It contains no credentials or inference base
URLs.

The canonical environment controls are `SHARD_DEPLOYMENT_PROFILE`,
`SHARD_SERVICE_LAYOUT`, `SHARD_HOST`, and `SHARD_PORT`. The two former
`BR2SHACL_*` aliases are accepted during the API v1 migration. Remote inference
uses `DATABRICKS_BASE_URL` and `DATABRICKS_TOKEN` when a request does not supply
an explicit override.

Operational limits are intentionally broad and configurable. See
[`docs/api.md`](api.md#operational-safeguards) for rate, timeout, upload,
concurrency, trusted-proxy and CORS variables. Set
`SHARD_CORS_ALLOWED_ORIGINS` to the public frontend origin; do not rely on a
wildcard. Provider tokens must remain in the server secret manager or in
write-only request fields and must never be placed in reverse-proxy logs.
