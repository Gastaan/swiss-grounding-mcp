FROM python:3.13-slim
LABEL io.modelcontextprotocol.server.name="io.github.soheil1lotfi/swiss-grounding-mcp" \
      org.opencontainers.image.source="https://github.com/Gastaan/swiss-grounding-mcp" \
      org.opencontainers.image.description="MCP server: cited answers about Switzerland from official sources"
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/
# HOST=0.0.0.0: inside a container the server must listen on all interfaces (the default is localhost).
# Set SGM_AUTH_TOKEN when the port is reachable from outside a trusted network.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1 PYTHONUNBUFFERED=1 \
    SGM_TRANSPORT=http SGM_CACHE_DIR=/tmp/sgm-cache SGM_MODEL_DIR=/app/models HOST=0.0.0.0 PORT=8000
WORKDIR /app
# the unprivileged user that runs the server (created first, so files can be handed over in the layer
# that creates them; a later chown would copy them into a second layer)
RUN useradd --system --uid 10001 --no-create-home sgm
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --extra semantic --no-install-project
COPY src ./src
# install the project (with hybrid search), unpack the shipped search index and download the embedding
# model at build time, so the container starts fast and never downloads anything at runtime
RUN uv sync --locked --no-dev --extra semantic \
 && .venv/bin/python -c "from swiss_grounding_mcp.sources.search import index_meta; print(index_meta())" \
 && .venv/bin/python -c "from swiss_grounding_mcp import semantic; assert semantic.backend(), semantic.status(); print(semantic.status())" \
 && chown -R sgm /app/models
ENV HF_HUB_OFFLINE=1
# it only needs to read /app and write its cache in /tmp (and the model library's small index files
# next to the model, hence the ownership of /app/models above)
USER sgm
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ['PORT'])"
# HTTP by default (Docker, Cloud Run); MCP clients that start the image themselves pass "--transport stdio"
ENTRYPOINT ["/app/.venv/bin/swiss-grounding-mcp"]
CMD ["--transport", "http"]
