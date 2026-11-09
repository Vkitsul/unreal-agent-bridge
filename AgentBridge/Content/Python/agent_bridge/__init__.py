"""Agent Bridge: an MCP server running inside the Unreal Editor."""

import os

DEFAULT_PORT = 19840

_server = None


def start():
    """Start serving and pump the server from the editor's tick."""
    global _server
    import unreal

    from . import mcp_server, tools

    if _server is not None:
        return

    port = int(os.environ.get("AGENT_BRIDGE_PORT", DEFAULT_PORT))
    server = mcp_server.Server(tools.call_tool, tools.list_tools, port=port)
    try:
        server.start()
    except OSError as error:
        unreal.log_error("AgentBridge: cannot listen on port {}: {}".format(port, error))
        return

    if not hasattr(unreal, "BlueprintGraphEditor"):
        unreal.log_warning(
            "AgentBridge: this engine has no Python Blueprint graph API. "
            "Unreal Engine 5.8 or later is required for the blueprint_* tools."
        )

    _server = server
    # Polled from the tick, because the unreal API may only be used on the game thread.
    unreal.register_slate_post_tick_callback(lambda delta_seconds: server.poll())
    if hasattr(unreal, "register_python_shutdown_callback"):
        unreal.register_python_shutdown_callback(server.stop)
    unreal.log("AgentBridge: MCP server listening on http://127.0.0.1:{}/mcp".format(port))
