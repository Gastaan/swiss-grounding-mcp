FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 \
    SGM_TRANSPORT=http SGM_CACHE_DIR=/tmp/sgm-cache PORT=8000
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
# install the project and unpack the shipped search index at build time (fast container start)
RUN uv sync --locked --no-dev \
 && .venv/bin/python -c "from swiss_grounding_mcp.sources.search import index_meta; print(index_meta())"
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ['PORT'])"
CMD ["/app/.venv/bin/swiss-grounding-mcp", "--transport", "http"]
