"""The MCP tools this server offers."""

import contextlib
import io
import json
import traceback

import unreal

from . import blueprints

_FORMAT_NOTE = (
    "Format: each graph starts '=== graph Name'. Existing nodes are 'id: title @x,y'; new nodes "
    "are '+label kind args'. Indented lines under a node are 'Pin -> otherId.Pin' for links "
    "(written on the output pin) and 'Pin = value' for defaults."
)

_PATH = {"type": "string", "description": "Blueprint asset path, e.g. /Game/Heroes/BP_Hero"}
_GRAPH = {"type": "string", "description": "Only this graph. Omit for the whole Blueprint."}
_ALLOW_ERRORS = {
    "type": "boolean",
    "description": "Keep the change even if it does not compile. It will not be saved.",
}
_SAVE = {"type": "boolean", "description": "Save after a clean compile. Default true."}


def _schema(properties, required):
    return {"type": "object", "properties": properties, "required": required}


_python_globals = {"unreal": unreal}


def _run_python(code):
    output = io.StringIO()
    _python_globals.pop("result", None)
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            exec(compile(code, "<agent>", "exec"), _python_globals)  # pylint: disable=exec-used
    except Exception:  # pylint: disable=broad-except
        return {"success": False, "output": output.getvalue(), "error": traceback.format_exc()}
    response = {"success": True, "output": output.getvalue()}
    if "result" in _python_globals:
        response["result"] = repr(_python_globals["result"])
    return response


def _console_command(command):
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    unreal.SystemLibrary.execute_console_command(world, command)
    return {"success": True}


_TOOLS = [
    (
        {
            "name": "blueprint_read",
            "description": "Read a Blueprint as compact text with stable node ids. " + _FORMAT_NOTE +
                           " Set pin_detail to also list every pin and its type.",
            "inputSchema": _schema(
                {"path": _PATH, "graph": _GRAPH, "pin_detail": {"type": "boolean"}}, ["path"]
            ),
        },
        lambda args: blueprints.read_blueprint(
            args["path"], args.get("graph", ""), args.get("pin_detail", False)
        ),
    ),
    (
        {
            "name": "blueprint_edit",
            "description": "Edit a Blueprint like a source file: replace old_string with "
                           "new_string in its text. Atomic: it compiles and saves, or rolls back "
                           "and returns the compiler errors. Prefer this tool for changes. "
                           + _FORMAT_NOTE,
            "inputSchema": _schema(
                {
                    "path": _PATH, "old_string": {"type": "string"},
                    "new_string": {"type": "string"}, "graph": _GRAPH,
                    "replace_all": {"type": "boolean"}, "allow_errors": _ALLOW_ERRORS,
                    "save": _SAVE,
                },
                ["path", "old_string", "new_string"],
            ),
        },
        lambda args: blueprints.edit_blueprint(
            args["path"], args["old_string"], args["new_string"], args.get("graph", ""),
            args.get("replace_all", False), args.get("allow_errors", False), args.get("save", True),
        ),
    ),
    (
        {
            "name": "blueprint_write",
            "description": "Replace whole graphs of a Blueprint with text, or add variables. Nodes "
                           "left out of a graph section are deleted. Atomic, like blueprint_edit. "
                           "New node kinds: call KismetSystemLibrary.PrintString (or self.Func, or "
                           "a full path), get Name, set Name, getlocal, setlocal, macro ForEachLoop, "
                           "event ReceiveBeginPlay, customevent Name, branch, result, comment Text, "
                           "entry, node Category|Name. '=== graph Name (new function)' creates a "
                           "function; 'param in Name: float' adds a parameter. 'var Name: float' "
                           "before any graph adds a variable. " + _FORMAT_NOTE,
            "inputSchema": _schema(
                {
                    "path": _PATH, "text": {"type": "string"}, "allow_errors": _ALLOW_ERRORS,
                    "save": _SAVE,
                },
                ["path", "text"],
            ),
        },
        lambda args: blueprints.write_blueprint(
            args["path"], args["text"], args.get("allow_errors", False), args.get("save", True)
        ),
    ),
    (
        {
            "name": "blueprint_compile",
            "description": "Compile a Blueprint and return its errors and warnings, per node.",
            "inputSchema": _schema({"path": _PATH}, ["path"]),
        },
        lambda args: blueprints.compile_blueprint(args["path"]),
    ),
    (
        {
            "name": "blueprint_search",
            "description": "Search every Blueprint in the project, unloaded ones included, for "
                           "text in node titles, pin values, variables and graph names. Each hit "
                           "names the Blueprint, graph and node.",
            "inputSchema": _schema(
                {
                    "query": {"type": "string"},
                    "path_prefix": {"type": "string", "description": "Default /Game"},
                    "limit": {"type": "integer", "description": "Default 100"},
                },
                ["query"],
            ),
        },
        lambda args: blueprints.search_blueprints(
            args["query"], args.get("path_prefix", "/Game"), args.get("limit", 100)
        ),
    ),
    (
        {
            "name": "blueprint_list_node_types",
            "description": "List node names usable with '+label node <name>', such as "
                           "'Utilities|FlowControl|Branch'. Use filter to narrow the list.",
            "inputSchema": _schema(
                {"path": _PATH, "graph": _GRAPH, "filter": {"type": "string"}}, ["path"]
            ),
        },
        lambda args: blueprints.list_available_nodes(
            args["path"], args.get("graph", ""), args.get("filter", "")
        ),
    ),
    (
        {
            "name": "run_python",
            "description": "Run Python in the editor with the full unreal module, for anything the "
                           "other tools do not cover: assets, actors, levels, Play In Editor, "
                           "screenshots. Printed output is returned, and so is a variable named "
                           "'result'. This can change or delete project data.",
            "inputSchema": _schema({"code": {"type": "string"}}, ["code"]),
        },
        lambda args: _run_python(args["code"]),
    ),
    (
        {
            "name": "console_command",
            "description": "Run an Unreal console command in the editor.",
            "inputSchema": _schema({"command": {"type": "string"}}, ["command"]),
        },
        lambda args: _console_command(args["command"]),
    ),
]

_HANDLERS = {definition["name"]: handler for definition, handler in _TOOLS}


def list_tools():
    return [definition for definition, _ in _TOOLS]


def call_tool(name, arguments):
    """Run a tool. Returns (text, is_error)."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return "Unknown tool '{}'".format(name), True
    try:
        result = handler(arguments)
    except blueprints.BridgeError as error:
        return str(error), True
    except Exception:  # pylint: disable=broad-except
        return traceback.format_exc(), True
    if isinstance(result, str):
        return result, False
    is_error = result.get("success") is False
    return json.dumps(result, indent=1), is_error
