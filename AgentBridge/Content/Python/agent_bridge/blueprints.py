"""Reads and edits Blueprints through Unreal 5.8's Python graph API."""

import os
import traceback

import unreal

from . import text_format
from .text_format import GRAPH_PREFIX, escape

_STANDARD_MACROS = "/Engine/EditorBlueprintResources/StandardMacros.StandardMacros"
_NEW_NODE_SPACING = 400


class BridgeError(Exception):
    """A failure to report to the agent as a plain message."""


def _lib():
    return unreal.BlueprintEditorLibrary


def _graph_editor(graph):
    return unreal.BlueprintGraphEditor.get_graph_editor(graph)


def _load(path):
    asset = unreal.EditorAssetLibrary.load_asset(path)
    blueprint = _lib().get_blueprint_asset(asset) if asset else None
    if not blueprint:
        raise BridgeError("Blueprint not found at path '{}'".format(path))
    return blueprint


def _is_output(pin):
    return pin.get_pin_direction() == unreal.EdGraphPinDirection.EGPD_OUTPUT


def _is_comment(node):
    return isinstance(node, unreal.EdGraphNode_Comment)


def node_id(node):
    """Object names are unique within a graph and are saved with it, so they stay stable."""
    name = node.get_name()
    return name[len("K2Node_") :] if name.startswith("K2Node_") else name


def _node_title(node):
    return _lib().get_comment_text(node) if _is_comment(node) else _lib().get_node_title(node)


def _node_position(node):
    if _is_comment(node):
        return None  # The API has no position getter for comment boxes.
    position = _lib().get_node_pos(node)
    return position.x, position.y


def _live_nodes(editor):
    nodes = {}
    for node in list(editor.list_all_nodes()) + list(editor.list_comment_nodes()):
        nodes[node_id(node)] = node
    return nodes


def _find_graph(blueprint, name):
    for graph in _lib().list_graphs(blueprint):
        if graph.get_name() == name:
            return graph
    return None


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _append_node(lines, node, pin_detail):
    header = "{}: {}".format(node_id(node), escape(_node_title(node)))
    position = _node_position(node)
    if position:
        header += " @{},{}".format(position[0], position[1])
    lines.append(header)

    if _is_comment(node):
        return
    for pin in _lib().list_all_pins(node):
        name = str(pin.get_pin_name())
        if pin_detail:
            lines.append(
                "  # {} {}: {}".format(
                    "out" if _is_output(pin) else "in", name, pin.get_pin_type_display_string()
                )
            )
        if _is_output(pin):
            for other in pin.list_connected_pins():
                lines.append(
                    "  {} -> {}.{}".format(
                        name, node_id(other.get_owning_node()), other.get_pin_name()
                    )
                )
        elif not pin.list_connected_pins():
            value = pin.get_pin_value()
            if value:
                lines.append("  {} = {}".format(name, escape(value)))


def _graph_text(graph, pin_detail):
    lines = [GRAPH_PREFIX + graph.get_name()]
    for node in _live_nodes(_graph_editor(graph)).values():
        _append_node(lines, node, pin_detail)
    return "\n".join(lines)


def _variable_type_label(blueprint, name):
    pin_type = _lib().get_member_variable_type(blueprint, name)
    if pin_type is None:
        return "unknown"
    label = _lib().pin_type_to_json_schema(pin_type, _lib().generated_class(blueprint))
    return " ".join(label.split())


def _body_text(blueprint, pin_detail):
    """Variables and graphs: the part of the text that describes the Blueprint's content."""
    lines = []
    for name in _lib().list_member_variable_names(blueprint, False):
        lines.append("var {}: {}".format(name, _variable_type_label(blueprint, name)))
    for graph in _lib().list_graphs(blueprint):
        lines.append(_graph_text(graph, pin_detail))
    return "\n".join(lines)


def _full_text(blueprint, pin_detail):
    status = blueprint.get_editor_property("status")
    header = "blueprint {} parent={} status={}".format(
        blueprint.get_path_name(),
        _lib().get_blueprint_parent_class(blueprint).get_name(),
        getattr(status, "name", status),
    )
    return header + "\n" + _body_text(blueprint, pin_detail)


def read_blueprint(path, graph_name="", pin_detail=False):
    """The whole Blueprint as text, or one graph when graph_name is given."""
    blueprint = _load(path)
    if not graph_name:
        return _full_text(blueprint, pin_detail)
    graph = _find_graph(blueprint, graph_name)
    if graph is None:
        names = ", ".join(str(name) for name in _lib().list_graph_names(blueprint))
        raise BridgeError("Graph '{}' not found. Graphs: {}".format(graph_name, names))
    return _graph_text(graph, pin_detail)


# ---------------------------------------------------------------------------
# Applying text
# ---------------------------------------------------------------------------


class _Context:
    def __init__(self, blueprint):
        self.blueprint = blueprint
        self.errors = []
        self.id_map = {}
        self.touched = []

    def error(self, line, message):
        self.errors.append("line {}: {}".format(line, message))


def _parse_type(text):
    """Build an EdGraphPinType from text such as 'float', 'array<object:Actor>'."""
    lib = _lib()
    text = text.strip()
    for prefix, build in (("array<", lib.get_array_type), ("set<", lib.get_set_type)):
        if text.startswith(prefix) and text.endswith(">"):
            return build(_parse_type(text[len(prefix) : -1]))
    if text.startswith("map<") and text.endswith(">"):
        key, value = text[4:-1].split(",", 1)
        return lib.get_map_type(_parse_type(key), _parse_type(value))
    if ":" in text:
        kind, name = text.split(":", 1)
        if kind == "struct":
            return lib.get_struct_type(_resolve_type_object(name, "static_struct"))
        if kind == "object":
            return lib.get_object_reference_type(_resolve_type_object(name, "static_class"))
        if kind == "class":
            return lib.get_class_reference_type(_resolve_type_object(name, "static_class"))
        raise BridgeError("Unknown type prefix '{}'".format(kind))
    return lib.get_basic_type_by_name(text)


def _resolve_type_object(name, accessor):
    """Accepts a Python type name such as Vector, or a path such as /Script/Engine.Actor."""
    name = name.strip()
    resolved = None
    if name.startswith("/"):
        resolved = unreal.load_object(None, name)
    elif hasattr(unreal, name):
        resolved = getattr(getattr(unreal, name), accessor)()
    if resolved is None:
        raise BridgeError("Type '{}' not found".format(name))
    return resolved


def _call_with_location(function, x, y, *before, **after):
    """The docs do not give the location type, so accept either engine vector type."""
    try:
        return function(*before, unreal.Vector2D(x, y), **after)
    except TypeError:
        return function(*before, unreal.IntPoint(x, y), **after)


def _first_result(function, candidates):
    for candidate in candidates:
        try:
            result = function(candidate)
        except Exception:  # pylint: disable=broad-except
            continue
        if result:
            return result
    return None


def _function_paths(blueprint, args):
    if args.startswith("self."):
        class_path = _lib().generated_class(blueprint).get_path_name()
        name = args[len("self.") :]
        return [class_path + "." + name, class_path + ":" + name]
    if args.startswith("/"):
        return [args]
    return ["/Script/Engine." + args]


def _macro_paths(args):
    if args.startswith("/"):
        return [args]
    return [_STANDARD_MACROS + ":" + args, _STANDARD_MACROS + "." + args]


def _split_member(args):
    if "." in args:
        class_path, name = args.rsplit(".", 1)
        return name, class_path
    return args, ""


def _create_node(blueprint, editor, spec, x, y):
    """Create a node from its kind and args. Returns the node, or None if it failed."""
    kind, args = spec.kind, spec.args

    if kind == "call":
        return _first_result(editor.add_call_function_node, _function_paths(blueprint, args))
    if kind == "macro":
        return _first_result(editor.add_macro_node, _macro_paths(args))
    if kind == "get":
        return editor.add_get_member_variable_node(*_split_member(args))
    if kind == "set":
        return editor.add_set_member_variable_node(*_split_member(args))
    if kind == "getlocal":
        return editor.add_get_local_variable_node(args)
    if kind == "setlocal":
        return editor.add_set_local_variable_node(args)
    if kind == "event":
        return _lib().add_event_override(blueprint, args, unreal.IntPoint(x, y))
    if kind == "customevent":
        return editor.add_custom_event_node(args)
    if kind == "branch":
        return editor.add_branch_node()
    if kind == "result":
        return editor.add_return_node()
    if kind == "comment":
        return _call_with_location(editor.add_comment_node, x, y, args)
    if kind == "node":
        return _call_with_location(editor.create_node_from_name, x, y, args, context_pins=[])
    raise BridgeError(
        "Unknown node kind '{}'. Use call, macro, get, set, getlocal, setlocal, event, "
        "customevent, branch, result, comment, node or entry".format(kind)
    )


def _set_position(node, x, y):
    if not _is_comment(node):
        _lib().set_node_pos(node, unreal.IntPoint(x, y))


def _find_pin(node, name, want_output):
    wanted = name.lower()
    candidates = [pin for pin in _lib().list_all_pins(node) if _is_output(pin) == want_output]
    for pin in candidates:
        if str(pin.get_pin_name()).lower() == wanted:
            return pin, None
    available = ", ".join(str(pin.get_pin_name()) for pin in candidates)
    direction = "output" if want_output else "input"
    return None, "Node {} has no {} pin '{}'. Available: {}".format(
        node_id(node), direction, name, available
    )


def _link_key(source_id, source_pin, target_id, target_pin):
    return (source_id, source_pin.lower(), target_id, target_pin.lower())


def _apply_links(context, section, nodes):
    desired = set()
    for spec in section.nodes:
        for link in spec.links:
            if link.target_id not in nodes:
                context.error(link.line, "Link targets unknown node '{}'".format(link.target_id))
                continue
            desired.add(_link_key(spec.id, link.out_pin, link.target_id, link.target_pin))
    if context.errors:
        return

    # Live ids may differ from the labels new nodes were written with.
    label_for_live_id = {node_id(node): label for label, node in nodes.items()}
    for label, node in nodes.items():
        if _is_comment(node):
            continue
        for pin in _lib().list_all_pins(node):
            if not _is_output(pin):
                continue
            for other in pin.list_connected_pins():
                target_label = label_for_live_id.get(node_id(other.get_owning_node()))
                key = _link_key(label, str(pin.get_pin_name()), target_label or "",
                                str(other.get_pin_name()))
                if key not in desired:
                    pin.break_single_pin_link(other)

    for spec in section.nodes:
        for link in spec.links:
            # Looked up here: connecting a wildcard pin can rebuild a node's pins.
            out_pin, error = _find_pin(nodes[spec.id], link.out_pin, True)
            if error:
                context.error(link.line, error)
                continue
            in_pin, error = _find_pin(nodes[link.target_id], link.target_pin, False)
            if error:
                context.error(link.line, error)
                continue
            if any(in_pin.is_same_native_pin(other) for other in out_pin.list_connected_pins()):
                continue
            if not out_pin.try_create_connection(in_pin):
                context.error(
                    link.line,
                    "Cannot connect {}.{} to {}.{}: incompatible pins".format(
                        spec.id, link.out_pin, link.target_id, link.target_pin
                    ),
                )


def _apply_defaults(context, section, nodes):
    for spec in section.nodes:
        for default in spec.defaults:
            pin, error = _find_pin(nodes[spec.id], default.pin, False)
            if error:
                context.error(default.line, error)
            elif pin.get_pin_value() != default.value and not pin.set_pin_value(default.value):
                context.error(
                    default.line,
                    "Pin '{}' rejected value '{}'".format(default.pin, default.value),
                )


def _bind_existing_nodes(context, section, editor, live):
    """Match the text's nodes to live ones. Returns (nodes by text id, specs to create)."""
    nodes = {}
    to_create = []
    for spec in section.nodes:
        if spec.is_new and spec.kind == "entry":
            nodes[spec.id] = editor.find_graph_entry_pin().get_owning_node()
            context.id_map[spec.id] = node_id(nodes[spec.id])
        elif spec.is_new:
            to_create.append(spec)
        elif spec.id not in live:
            context.error(
                spec.line, "No node '{}' in this graph. New nodes are '+label kind'".format(spec.id)
            )
        else:
            node = live[spec.id]
            if not _is_comment(node) and spec.title != _node_title(node):
                context.error(
                    spec.line,
                    "Node {} is '{}'. To change what a node is, delete it and add a new one".format(
                        spec.id, _node_title(node)
                    ),
                )
            nodes[spec.id] = node
    return nodes, to_create


def _remove_absent_nodes(editor, live, nodes):
    kept = {node_id(node) for node in nodes.values()}
    absent = [node for key, node in live.items() if key not in kept]
    for node in absent:
        if _is_comment(node):
            editor.remove_comment_node(node)
    removable = [
        node for node in absent
        if not _is_comment(node) and not isinstance(node, unreal.K2Node_FunctionEntry)
    ]
    if removable:
        editor.remove_nodes(removable)


def _apply_existing_edits(section, nodes):
    for spec in section.nodes:
        node = nodes[spec.id]
        if spec.is_new:
            continue
        if _is_comment(node):
            if spec.title != _node_title(node):
                _lib().set_comment_text(node, spec.title)
        elif spec.has_position and _node_position(node) != (spec.x, spec.y):
            _set_position(node, spec.x, spec.y)


def _apply_params(context, section, editor):
    existing = set()
    entry_pin = editor.find_graph_entry_pin()
    if section.params and entry_pin.is_valid():
        existing = {
            str(pin.get_pin_name()).lower()
            for pin in _lib().list_all_pins(entry_pin.get_owning_node())
        }
    for param in section.params:
        if param.name.lower() in existing:
            continue
        try:
            pin_type = _parse_type(param.type)
        except BridgeError as error:
            context.error(param.line, str(error))
            continue
        if param.direction == "in":
            editor.add_graph_input_parameter(param.name, pin_type, param.value)
        else:
            editor.add_graph_output_parameter(param.name, pin_type)


def _apply_graph(context, section):
    graph = _find_graph(context.blueprint, section.name)
    if graph is None:
        context.error(
            section.line,
            "Graph '{}' does not exist. Write '{}{}{}' to create a function".format(
                section.name, GRAPH_PREFIX, section.name, text_format.NEW_FUNCTION_SUFFIX
            ),
        )
        return
    editor = _graph_editor(graph)
    live = _live_nodes(editor)

    nodes, to_create = _bind_existing_nodes(context, section, editor, live)
    if context.errors:
        return

    _remove_absent_nodes(editor, live, nodes)
    _apply_params(context, section, editor)

    positions = [_node_position(node) for node in _live_nodes(editor).values()]
    positions = [position for position in positions if position]
    next_x = max([x for x, _ in positions], default=-_NEW_NODE_SPACING) + _NEW_NODE_SPACING
    next_y = positions[-1][1] if positions else 0

    for spec in to_create:
        x, y = (spec.x, spec.y) if spec.has_position else (next_x, next_y)
        try:
            created = _create_node(context.blueprint, editor, spec, x, y)
        except BridgeError as error:
            context.error(spec.line, str(error))
            continue
        if not created:
            context.error(spec.line, "Could not create node '{} {}'".format(spec.kind, spec.args))
            continue
        _set_position(created, x, y)
        next_x, next_y = x + _NEW_NODE_SPACING, y
        nodes[spec.id] = created
        context.id_map[spec.id] = node_id(created)
    if context.errors:
        return

    _apply_existing_edits(section, nodes)
    _apply_links(context, section, nodes)
    if not context.errors:
        # Last, because connecting a wildcard pin decides the type its default must have.
        _apply_defaults(context, section, nodes)
    context.touched.append(section.name)


def _apply_document(context, document):
    lib = _lib()
    blueprint = context.blueprint
    existing_variables = {name.lower() for name in lib.list_member_variable_names(blueprint, False)}
    for variable in document.variables:
        if variable.name.lower() in existing_variables:
            continue
        try:
            if not lib.add_member_variable(blueprint, variable.name, _parse_type(variable.type)):
                context.error(variable.line, "Could not add variable '{}'".format(variable.name))
        except BridgeError as error:
            context.error(variable.line, str(error))

    for section in document.graphs:
        if section.is_new_function:
            unreal.BlueprintGraphEditor.create_and_edit_function_graph(blueprint, section.name)
    if context.errors:
        return

    # Function signatures come first, and need a compile before callers can see them.
    first = [section for section in document.graphs if section.is_new_function or section.params]
    rest = [section for section in document.graphs if section not in first]
    for section in first:
        _apply_graph(context, section)
    if context.errors:
        return
    if first and rest:
        lib.compile_blueprint(blueprint)
    for section in rest:
        _apply_graph(context, section)


def compile_report(blueprint):
    """Compile and return (success, errors, warnings)."""
    succeeded = _lib().compile_blueprint(blueprint)
    errors = []
    warnings = []
    for graph in _lib().list_graphs(blueprint):
        editor = _graph_editor(graph)
        for source, target in (
            (editor.list_nodes_with_errors(), errors),
            (editor.list_nodes_with_warnings(), warnings),
        ):
            for node in source:
                target.append(
                    "{} / {} / {}: {}".format(
                        graph.get_name(), node_id(node), _node_title(node),
                        node.get_editor_property("error_msg"),
                    )
                )
    if not succeeded and not errors:
        errors.append("Compile failed with no error on a node. See the Output Log.")
    return bool(succeeded) and not errors, errors, warnings


def _package_is_dirty(blueprint):
    package_name = blueprint.get_outermost().get_path_name()
    return any(
        package.get_path_name() == package_name
        for package in unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()
    )


def _undo_last_transaction():
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    unreal.SystemLibrary.execute_console_command(world, "TRANSACTION UNDO")


def _rollback(path, before, was_dirty):
    """Restore the Blueprint. Each step is checked, as the graph API is still experimental."""
    _undo_last_transaction()
    blueprint = _load(path)
    _lib().compile_blueprint(blueprint)
    if _body_text(blueprint, False) == before:
        return True
    if was_dirty:
        return False  # Reloading from disk would throw away the user's unsaved work.
    unreal.EditorLoadingAndSavingUtils.reload_packages(
        [blueprint.get_outermost()], unreal.ReloadPackagesInteractionMode.ASSUME_POSITIVE
    )
    blueprint = _load(path)
    _lib().compile_blueprint(blueprint)
    return _body_text(blueprint, False) == before


def _is_playing():
    return unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor()


def write_blueprint(path, text, allow_errors=False, save=True):
    """Apply text to a Blueprint: every change lands, compiles and saves, or none do."""
    result = {
        "success": False, "compiled": False, "rolled_back": False, "saved": False,
        "errors": [], "warnings": [],
    }
    document, parse_errors = text_format.parse(text)
    if parse_errors:
        result["errors"] = parse_errors
        return result
    if _is_playing():
        raise BridgeError("Stop Play In Editor before writing a Blueprint")

    blueprint = _load(path)
    before = _body_text(blueprint, False)
    was_dirty = _package_is_dirty(blueprint)
    context = _Context(blueprint)

    with unreal.ScopedEditorTransaction("AgentBridge: edit Blueprint"):
        # The transaction must record something, or undoing it would undo the user's last edit.
        blueprint.modify()
        try:
            _apply_document(context, document)
        except Exception:  # pylint: disable=broad-except
            context.errors.append(traceback.format_exc())

    if context.errors:
        result["errors"] = context.errors
    else:
        compiled, errors, warnings = compile_report(blueprint)
        result.update(compiled=compiled, errors=errors, warnings=warnings)

    if result["errors"] and not (allow_errors and not context.errors):
        restored = _rollback(path, before, was_dirty)
        result["rolled_back"] = restored
        if not restored:
            result["errors"].append(
                "ROLLBACK INCOMPLETE: the Blueprint may be partly edited. Check it in the "
                "editor, or revert the asset in source control."
            )
        return result

    result["success"] = result["compiled"]
    result["id_map"] = context.id_map
    if save and result["compiled"]:
        result["saved"] = bool(unreal.EditorAssetLibrary.save_loaded_asset(blueprint, False))
    # Autocast nodes and new ids can differ from what was written, so report what is there now.
    result["text"] = "\n".join(
        _graph_text(_find_graph(blueprint, name), False) for name in context.touched
    )
    return result


def edit_blueprint(path, old_string, new_string, graph_name="", replace_all=False,
                   allow_errors=False, save=True):
    """Replace exact text in the Blueprint's text form, then write what changed."""
    if old_string == new_string:
        raise BridgeError("old_string and new_string are identical")
    before = read_blueprint(path, graph_name)
    count = before.count(old_string)
    if count == 0:
        raise BridgeError("old_string was not found. Read the Blueprint again and copy it exactly.")
    if count > 1 and not replace_all:
        raise BridgeError(
            "old_string occurs {} times. Include more lines, or set replace_all.".format(count)
        )
    after = before.replace(old_string, new_string)
    try:
        changed = text_format.changed_sections(before, after)
    except ValueError as error:
        raise BridgeError(str(error)) from error
    return write_blueprint(path, changed, allow_errors, save)


def compile_blueprint(path):
    compiled, errors, warnings = compile_report(_load(path))
    return {"compiled": compiled, "errors": errors, "warnings": warnings}


def list_available_nodes(path, graph_name="", name_filter="", limit=200):
    """Names accepted by '+label node <name>', optionally filtered."""
    blueprint = _load(path)
    graph = _find_graph(blueprint, graph_name) if graph_name else _lib().find_event_graph(blueprint)
    if graph is None:
        raise BridgeError("Graph not found")
    wanted = name_filter.lower()
    names = [str(name) for name in _graph_editor(graph).list_available_nodes([])]
    matches = [name for name in names if wanted in name.lower()]
    return {"total": len(matches), "names": matches[:limit]}


# ---------------------------------------------------------------------------
# Searching
# ---------------------------------------------------------------------------


def _package_file(package_name):
    """Disk path of a /Game package, or None for other mount points."""
    if not package_name.startswith("/Game/"):
        return None
    relative = package_name[len("/Game/") :] + ".uasset"
    return os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_content_dir()),
                        relative)


def _file_may_match(file_path, needle):
    """Identifiers and strings are stored uncompressed, so a byte scan rules out most files."""
    try:
        with open(file_path, "rb") as handle:
            data = handle.read().lower()
    except OSError:
        return True
    return needle.encode("ascii", "ignore") in data or needle.encode("utf-16-le") in data


def _matches(value, query, compact):
    value = str(value).lower()
    return query in value or compact in value.replace(" ", "")


def _search_blueprint(blueprint, query, compact, results, limit):
    path = blueprint.get_path_name()

    def add(graph, node, title, matched):
        if len(results) < limit:
            results.append(
                {"blueprint": path, "graph": graph, "node_id": node, "title": title,
                 "matched": matched}
            )

    for name in _lib().list_member_variable_names(blueprint, False):
        if _matches(name, query, compact):
            add("", "", str(name), "variable")
    for graph in _lib().list_graphs(blueprint):
        graph_name = graph.get_name()
        if _matches(graph_name, query, compact):
            add(graph_name, "", graph_name, "graph name")
        for key, node in _live_nodes(_graph_editor(graph)).items():
            title = _node_title(node)
            if _matches(title, query, compact):
                add(graph_name, key, title, "title")
            elif not _is_comment(node):
                for pin in _lib().list_all_pins(node):
                    if _matches(pin.get_pin_value(), query, compact):
                        add(graph_name, key, title, "pin {}".format(pin.get_pin_name()))
                        break


def search_blueprints(query, path_prefix="/Game", limit=100, max_load=500):
    """Find text in nodes, pin values, variables and graph names across the project."""
    query = query.strip().lower()
    if not query:
        raise BridgeError("query is required")
    compact = query.replace(" ", "")
    # Titles add words that are not in the file ("Get Health"), so look only for the longest.
    needle = max(query.split(), key=len)

    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    assets = registry.get_assets_by_class(unreal.TopLevelAssetPath("/Script/Engine", "Blueprint"), True)
    assets = [asset for asset in assets if str(asset.package_name).startswith(path_prefix)]

    candidates = []
    for asset in assets:
        file_path = _package_file(str(asset.package_name))
        # A loaded Blueprint may have unsaved edits, so its file cannot rule it out.
        if asset.is_asset_loaded() or file_path is None or _file_may_match(file_path, needle):
            candidates.append(asset)

    results = []
    for asset in candidates[:max_load]:
        if len(results) >= limit:
            break
        blueprint = _lib().get_blueprint_asset(asset.get_asset())
        if blueprint:
            _search_blueprint(blueprint, query, compact, results, limit)
    return {
        "scanned": len(assets),
        "candidates": len(candidates),
        "truncated": len(candidates) > max_load or len(results) >= limit,
        "results": results,
    }
