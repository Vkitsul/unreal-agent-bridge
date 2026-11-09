# Unreal Agent Bridge

Let an AI agent read, edit and compile your Unreal Engine Blueprints as text.

Agent Bridge is an MCP server that runs inside the Unreal Editor. It is pure Python and uses the interpreter Unreal already ships with, so there is nothing to compile and no dependency to install. It works with Claude Code and other MCP clients that support Streamable HTTP.

[Leggi in italiano](AgentBridge/README.md)

> [!WARNING]
> **Early version.** So far this has only been tested against a simulation of Epic's documented API, not inside a real editor. It relies on a Python API that Epic marks as experimental. Run the [self-test](#check-that-it-works) first, and keep your project under source control.

## Requirements

- Unreal Engine 5.8 or later on Windows. Earlier versions do not expose Blueprint graphs to Python.
- The Python Editor Script Plugin, which Agent Bridge enables for you.

## Install

1. Commit your project or make a backup.
2. Copy the [`AgentBridge`](AgentBridge) folder into `YourProject/Plugins/`.
3. Open the project. The Output Log should show:

   ```text
   AgentBridge: MCP server listening on http://127.0.0.1:19840/mcp
   ```

To use another port, set the `AGENT_BRIDGE_PORT` environment variable before starting the editor.

## Check that it works

In the Output Log console, run:

```text
py import agent_bridge.selftest as t; t.run()
```

This builds a temporary Blueprint, exercises each feature on it, and deletes it. The results appear in the Output Log and in `Saved/AgentBridge_selftest.txt`.

## Connect your agent

With the editor open:

```bash
claude mcp add --transport http unreal http://127.0.0.1:19840/mcp
```

## Tools

| Tool | What it does |
|---|---|
| `blueprint_read` | Reads a Blueprint or one of its graphs as compact text |
| `blueprint_edit` | Replaces a piece of that text, like editing a source file |
| `blueprint_write` | Rewrites whole graphs and adds nodes, variables and functions |
| `blueprint_compile` | Compiles and returns errors and warnings for each node |
| `blueprint_search` | Searches every Blueprint in the project, including unloaded ones |
| `blueprint_list_node_types` | Lists the nodes that can be created by name |
| `run_python` | Runs Python in the editor for anything else: assets, actors, levels, Play In Editor |
| `console_command` | Runs an Unreal console command |

Every write is all-or-nothing. If a change fails or the Blueprint does not compile, the edit is rolled back and the agent gets the errors.

## The text format

```text
blueprint /Game/BP_Hero.BP_Hero parent=Character status=UpToDate
var Health: {"type": "number"}
=== graph EventGraph
Event_0: Event BeginPlay @0,0
  then -> CallFunction_3.execute
CallFunction_3: Print String @320,0
  InString = Hello
  then -> wait.execute
+wait call KismetSystemLibrary.Delay @640,0
  Duration = 2.0
```

- Existing nodes are written `id: title @x,y`. New nodes are written `+label kind args`.
- Under a node, `Pin -> otherId.Pin` is a link, written on the output pin, and `Pin = value` is a default value.
- Existing nodes left out of a graph section are deleted.
- `=== graph Name (new function)` creates a function, and `param in Name: float` adds a parameter to it.
- `var Name: float` before the first graph adds a variable.

| New node | Example |
|---|---|
| Function call | `+a call KismetSystemLibrary.PrintString` |
| Function on this Blueprint | `+a call self.MyFunction` |
| Variable | `+a get Health`, `+a set Health` |
| Local variable | `+a getlocal Count`, `+a setlocal Count` |
| Macro | `+a macro ForEachLoop` |
| Event | `+a event ReceiveBeginPlay`, `+a customevent OnHit` |
| Flow | `+a branch`, `+a result` |
| Function entry | `+a entry` |
| Comment box | `+a comment Movement logic` |
| Anything in the node menu | `+a node Utilities\|FlowControl\|Branch` |

## Limitations

- Existing nodes are identified by title and pins, because the API does not say which function or variable a node refers to. Search works on titles, pin values, variable names and graph names.
- Comment bubbles on individual nodes cannot be read, and comment boxes cannot be moved.
- Deleting a `Pin = value` line does not reset the pin to its default.
- Search covers Blueprint assets, not Level Blueprints.
- Widgets, materials and other asset types have no dedicated tools and go through `run_python`.

## Security

The server listens only on `127.0.0.1`, has no authentication, and rejects requests from web pages. An agent connected to it can change, save and delete project data and run arbitrary Python.

## Development

The code is in [`AgentBridge/Content/Python/agent_bridge`](AgentBridge/Content/Python/agent_bridge). The tests run outside Unreal, against a stand-in for the engine API:

```bash
python tests/test_agent_bridge.py
```

## License

[MIT](LICENSE)
