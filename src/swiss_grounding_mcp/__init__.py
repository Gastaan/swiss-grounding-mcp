"""Swiss Grounding MCP: authoritative Swiss public information for AI assistants."""


def main() -> None:
    from .server import main as run

    run()
