try:
    from mcp.server.fastmcp import FastMCP as _Server  # mcp 1.x
    from mcp.server.fastmcp.exceptions import ToolError as ToolError
except ModuleNotFoundError:
    # mcp 2.0 renamed FastMCP to MCPServer (same tool()/run() surface we use)
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError as ToolError

mcp = _Server("microsoft-ads-write")
