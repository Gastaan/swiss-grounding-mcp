import json

import pytest
from fastmcp import Client

from swiss_grounding_mcp.server import mcp


@pytest.fixture
async def client():
    async with Client(mcp) as c:
        yield c


async def call(client, tool: str, **args) -> dict:
    """Call a tool and return its structured result, checking text and structured content agree."""
    r = await client.call_tool(tool, args, raise_on_error=False)
    assert not r.is_error, r.content[0].text
    assert json.loads(r.content[0].text) == r.structured_content
    return r.structured_content
