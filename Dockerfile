# template-builder container image.
#
# Default configuration is the public read-only demo: it serves the bundled
# examples/ workspace with TB_READ_ONLY=1 — browsing, validation, rendering
# and .docx export work; nothing writes and nothing calls an LLM, so the
# container needs NO API key.
#
# To run the FULL product at the firm instead, mount a workspace and set:
#   docker run -e TB_READ_ONLY= -e ANTHROPIC_API_KEY=sk-ant-... \
#              -e TB_AUTH=firm:choose-a-long-password \
#              -v /your/workspace:/data -e TB_WORKSPACE=/data -p 8000:8000 ...
# TB_AUTH turns on HTTP Basic auth for everything except the counterparty
# intake surface. Basic auth needs TLS: terminate it in a reverse proxy
# (Caddy/nginx) in front of this container. Run ONE container per workspace —
# build jobs are in-memory, per-process.
FROM python:3.14-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY template_builder ./template_builder
RUN pip install --no-cache-dir .

COPY examples ./examples

# never run the service as root; the demo never writes, and a mounted
# workspace should be owned by (or writable to) uid 10001
RUN useradd --uid 10001 --create-home tb
USER tb

ENV TB_WORKSPACE=/app/examples \
    TB_READ_ONLY=1

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c 'import os, urllib.request; urllib.request.urlopen("http://127.0.0.1:" + os.environ.get("PORT", "8000") + "/api/config")' || exit 1
# $PORT is set by Render/Heroku/Railway; default to 8000 elsewhere.
# --proxy-headers: trust X-Forwarded-* from the TLS reverse proxy in front.
CMD ["sh", "-c", "uvicorn --factory template_builder.server:app_from_env --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
