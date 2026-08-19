"""Start the TrailSmith MCP server on stdio: python -m trailsmith_mcp"""
from .server import server

if __name__ == "__main__":
    server.run("stdio")
