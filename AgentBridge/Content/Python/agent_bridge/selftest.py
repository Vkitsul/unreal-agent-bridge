"""Checks the bridge against the real engine, using a temporary Blueprint.

Run it from the editor's console:
    py import agent_bridge.selftest as t; t.run()

The report goes to the Output Log and to Saved/AgentBridge_selftest.txt.
"""

import os
import traceback

import unreal

from . import blueprints

_FOLDER = "/Game/_AgentBridgeSelfTest"
_PATH = _FOLDER + "/BP_SelfTest"
_MARKER = "AgentBridgeMarker"

_REQUIRED_API = {
    "BlueprintEditorLibrary": [
        "add_event_override", "add_member_variable", "compile_blueprint",
        "create_blueprint_asset_with_parent", "generated_class", "get_array_type",
        "get_basic_type_by_name", "get_blueprint_asset", "get_blueprint_parent_class",
        "get_comment_text", "get_member_variable_type", "get_node_pos", "get_node_title",
        "list_all_pins", "list_graph_names", "list_graphs", "list_member_variable_names",
        "pin_type_to_json_schema", "set_comment_text", "set_node_pos",
    ],
    "BlueprintGraphEditor": [
        "add_branch_node", "add_call_function_node", "add_comment_node", "add_custom_event_node",
        "add_get_member_variable_node", "add_graph_input_parameter", "add_macro_node",
        "add_return_node", "add_set_member_variable_node", "create_and_edit_function_graph",
        "create_node_from_name", "find_graph_entry_pin", "get_graph_editor", "list_all_nodes",
        "list_available_nodes", "list_comment_nodes", "list_nodes_with_errors",
        "list_nodes_with_warnings", "remove_comment_node", "remove_nodes",
    ],
    "BlueprintGraphPin": [
        "break_single_pin_link", "get_owning_node", "get_pin_direction", "get_pin_name",
        "get_pin_type_display_string", "get_pin_value", "is_same_native_pin", "is_valid",
        "list_connected_pins", "set_pin_value", "try_create_connection",
    ],
    "EditorLoadingAndSavingUtils": ["get_dirty_content_packages", "reload_packages"],
}


def _check_api():
    missing = []
    for class_name, methods in _REQUIRED_API.items():
        owner = getattr(unreal, class_name, None)
        if owner is None:
            missing.append(class_name)
            continue
        missing += [class_name + "." + name for name in methods if not hasattr(owner, name)]
    assert not missing, "missing: " + ", ".join(missing)


def _write_ok(text, save=False):
    result = blueprints.write_blueprint(_PATH, text, save=save)
    assert result["success"], result["errors"]
    return result


def _graph():
    return blueprints.read_blueprint(_PATH, "EventGraph")


def _create_blueprint():
    created = unreal.BlueprintEditorLibrary.create_blueprint_asset_with_parent(_PATH, unreal.Actor)
    assert created, "asset was not created"


def _write_first_graph():
    _write_ok(
        "var SelfTestCounter: int\n"
        "=== graph EventGraph\n"
        "+start customevent SelfTestEvent @0,0\n"
        "  then -> print.execute\n"
        "+print call KismetSystemLibrary.PrintString @300,0\n"
        "  InString = {}\n"
        "  then -> wait.execute\n"
        "+wait call KismetSystemLibrary.Delay @600,0\n"
        "  Duration = 1.5\n".format(_MARKER)
    )


def _read_back():
    text = blueprints.read_blueprint(_PATH)
    assert _MARKER in text, "default value missing:\n" + text
    assert " -> " in text, "links missing:\n" + text
    assert "var SelfTestCounter" in text, "variable missing:\n" + text
    return "{} characters".format(len(text))


def _edit_and_save():
    result = blueprints.edit_blueprint(_PATH, "Duration = 1.5", "Duration = 2.5")
    assert result["success"], result["errors"]
    assert result["saved"], "compiled but did not save"
    assert "Duration = 2.5" in _graph(), "new value not read back"


def _round_trip_is_a_no_op():
    before = _graph()
    _write_ok(before)
    assert _graph() == before, "graph changed:\n" + _graph()


def _compile_error_rolls_back():
    before = _graph()
    # A Pawn function with no Pawn target, wired into execution, cannot compile on an Actor.
    text = before + (
        "\n+start2 customevent SelfTestBroken @0,300"
        "\n  then -> bad.execute"
        "\n+bad call Pawn.AddMovementInput @300,300"
    )
    result = blueprints.write_blueprint(_PATH, text, save=False)
    assert not result["success"], "the broken graph compiled"
    assert result["errors"], "no compiler errors were reported"
    assert result["rolled_back"], "not rolled back: {}".format(result["errors"])
    assert _graph() == before, "graph not restored:\n" + _graph()
    return result["errors"][0][:150]


def _bad_function_rolls_back():
    before = _graph()
    text = before + "\n+ok branch\n+bad call KismetSystemLibrary.NoSuchFunction"
    result = blueprints.write_blueprint(_PATH, text, save=False)
    assert result["rolled_back"], "not rolled back: {}".format(result["errors"])
    assert _graph() == before, "graph not restored:\n" + _graph()


def _node_kind(spec):
    def check():
        before = _graph()
        result = _write_ok(before + "\n+probe " + spec)
        assert result["id_map"].get("probe"), "no id reported"
    return check


def _new_function_and_call():
    result = blueprints.write_blueprint(
        _PATH,
        "=== graph SelfTestFunction (new function)\n"
        "param in Amount: float\n"
        "+begin entry\n"
        "  then -> p.execute\n"
        "+p call KismetSystemLibrary.PrintString @300,0\n",
        save=False,
    )
    assert result["success"], result["errors"]
    assert "Amount" in blueprints.read_blueprint(_PATH, "SelfTestFunction", True), "no parameter"
    _write_ok(_graph() + "\n+callit call self.SelfTestFunction")


def _search():
    result = blueprints.search_blueprints(_MARKER, path_prefix=_FOLDER)
    assert result["results"], "no hits: {}".format(result)
    return "{} hit(s), first: {}".format(len(result["results"]), result["results"][0]["title"])


def _node_types():
    result = blueprints.list_available_nodes(_PATH)
    assert result["total"] > 0, "empty list"
    return "{} node types, e.g. {}".format(result["total"], result["names"][:2])


_STEPS = [
    ("Python graph API present", _check_api),
    ("Create temporary Blueprint", _create_blueprint),
    ("Write nodes, links, defaults, variable", _write_first_graph),
    ("Read back", _read_back),
    ("Edit a value and save", _edit_and_save),
    ("Read then write unchanged is a no-op", _round_trip_is_a_no_op),
    ("Compile error is reported and rolled back", _compile_error_rolls_back),
    ("Unknown function is rolled back", _bad_function_rolls_back),
    ("Node kind: branch", _node_kind("branch")),
    ("Node kind: macro", _node_kind("macro ForEachLoop")),
    ("Node kind: comment", _node_kind("comment SelfTestComment")),
    ("Node kind: get variable", _node_kind("get SelfTestCounter")),
    ("Node kind: set variable", _node_kind("set SelfTestCounter")),
    ("Node kind: event override", _node_kind("event ReceiveTick")),
    ("Node kind: by menu name", _node_kind("node Utilities|FlowControl|Branch")),
    ("New function with parameter, then call it", _new_function_and_call),
    ("Search finds the marker", _search),
    ("List node types", _node_types),
]


def _write_report(lines):
    path = os.path.join(
        unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir()),
        "AgentBridge_selftest.txt",
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return path


def run():
    """Run every step, delete the temporary asset, and return (passed, failed)."""
    lines = ["AgentBridge self-test, engine " +
             unreal.BlueprintEditorLibrary.get_current_engine_version()]
    passed = failed = 0
    try:
        for name, step in _STEPS:
            try:
                detail = step()
            except Exception as error:  # pylint: disable=broad-except
                failed += 1
                lines.append("FAIL  {}: {}".format(name, error))
                if not isinstance(error, AssertionError):
                    lines.append(traceback.format_exc())
            else:
                passed += 1
                lines.append("PASS  {}{}".format(name, ": " + detail if detail else ""))
    finally:
        try:
            unreal.EditorAssetLibrary.delete_directory(_FOLDER)
        except Exception as error:  # pylint: disable=broad-except
            lines.append("Could not delete {}: {}".format(_FOLDER, error))

    lines.append("{} passed, {} failed".format(passed, failed))
    report = _write_report(lines)
    for line in lines:
        unreal.log("AgentBridge: " + line)
    unreal.log("AgentBridge: report saved to " + report)
    return passed, failed
