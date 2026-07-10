# template-builder — public read-only demo image.
#
# The demo serves the bundled examples/ workspace with TB_READ_ONLY=1:
# browsing, validation, rendering and .docx export work; nothing writes and
# nothing calls an LLM, so the container needs NO API key.
#
# To run the FULL product in a container instead, override:
#   docker run -e TB_READ_ONLY= -e ANTHROPIC_API_KEY=sk-ant-... \
#              -v /your/workspace:/data -e TB_WORKSPACE=/data -p 8000:8000 ...
FROM python:3.14-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY template_builder ./template_builder
RUN pip install --no-cache-dir .

COPY examples ./examples

ENV TB_WORKSPACE=/app/examples \
    TB_READ_ONLY=1

EXPOSE 8000
# $PORT is set by Render/Heroku/Railway; default to 8000 elsewhere.
CMD ["sh", "-c", "uvicorn --factory template_builder.server:app_from_env --host 0.0.0.0 --port ${PORT:-8000}"]
