try:
    from mcp.server.fastmcp import FastMCP as _Server  # mcp 1.x
except ModuleNotFoundError:
    # mcp 2.0 renamed FastMCP to MCPServer (same tool()/run() surface we use)
    from mcp.server.mcpserver import MCPServer as _Server

mcp = _Server("microsoft-ads-write")
