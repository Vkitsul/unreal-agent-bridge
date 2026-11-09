"""A stand-in for the `unreal` module, following Epic's documented UE 5.8 Python API.

It proves the bridge's own logic. It cannot prove the real engine behaves as documented.
"""

import copy


class EdGraphPinDirection:
    EGPD_INPUT = "in"
    EGPD_OUTPUT = "out"
    EGPD_MAX = "max"


class ReloadPackagesInteractionMode:
    ASSUME_POSITIVE = "yes"


class IntPoint:
    def __init__(self, x, y):
        self.x, self.y = x, y


class Vector2D(IntPoint):
    pass


class Object:
    def __init__(self, name):
        self._name = name

    def get_name(self):
        return self._name

    def get_path_name(self):
        return self._name


class Vector:
    @staticmethod
    def static_struct():
        return "VectorStruct"


class Actor:
    @staticmethod
    def static_class():
        return "ActorClass"


class _Pin:
    def __init__(self, owner, name, direction, value=""):
        self.owner, self.name, self.direction, self.value = owner, name, direction, value
        self.links = []


class BlueprintGraphPin:
    """A proxy over a pin, as documented: compare with is_same_native_pin, not ==."""

    def __init__(self, pin):
        self._pin = pin

    def is_valid(self):
        return self._pin is not None

    def get_pin_name(self):
        return self._pin.name

    def get_pin_direction(self):
        return self._pin.direction

    def get_pin_type_display_string(self):
        return "Fake Type"

    def get_owning_node(self):
        return self._pin.owner

    def get_pin_value(self):
        return "" if self._pin.links else self._pin.value

    def set_pin_value(self, value):
        if value == "REJECT":
            return False
        self._pin.value = value
        return True

    def list_connected_pins(self):
        return [BlueprintGraphPin(other) for other in self._pin.links]

    def is_same_native_pin(self, other):
        return self._pin is other._pin

    def try_create_connection(self, other):
        if self._pin.name == "Incompatible" or other._pin.name == "Incompatible":
            return False
        self._pin.links.append(other._pin)
        other._pin.links.append(self._pin)
        return True

    def break_single_pin_link(self, other):
        self._pin.links.remove(other._pin)
        other._pin.links.remove(self._pin)
        return True


class EdGraphNode(Object):
    def __init__(self, name, title, pins):
        super().__init__(name)
        self.title, self.x, self.y, self.error = title, 0, 0, ""
        self.pins = [_Pin(self, *spec) for spec in pins]

    def get_editor_property(self, name):
        assert name == "error_msg"
        return self.error


class K2Node(EdGraphNode):
    pass


class K2Node_FunctionEntry(K2Node):
    pass


class EdGraphNode_Comment(EdGraphNode):
    pass


class EdGraph(Object):
    def __init__(self, name):
        super().__init__(name)
        self.nodes = []


class Blueprint(Object):
    def __init__(self, path, graphs):
        super().__init__(path)
        self.graphs = graphs
        self.variables = []
        self.status = "UpToDate"
        self.package = Object(path.split(".")[0])

    def get_outermost(self):
        return self.package

    def get_editor_property(self, name):
        assert name == "status"
        return self.status

    def modify(self):
        if _state.transaction is not None and self not in _state.transaction:
            _state.transaction.append(self)
            _state.undo_stack.append((self, _snapshot(self)))


class _State:
    def __init__(self):
        self.reset()

    def reset(self):
        self.assets = {}
        self.saved = {}
        self.save_calls = 0
        self.undo_stack = []
        self.transaction = None
        self.dirty = set()
        self.counter = 100
        self.undo_works = True
        self.playing = False
        self.undo_calls = 0
        self.registry = []


_state = _State()


def _snapshot(blueprint):
    return copy.deepcopy((blueprint.graphs, blueprint.variables))


def _restore(blueprint, snapshot):
    blueprint.graphs, blueprint.variables = copy.deepcopy(snapshot)


def _next_name(prefix):
    _state.counter += 1
    return "{}_{}".format(prefix, _state.counter)


EXEC_IN = ("execute", EdGraphPinDirection.EGPD_INPUT)
THEN = ("then", EdGraphPinDirection.EGPD_OUTPUT)


def make_node(prefix, title, *data_pins, base=K2Node):
    return base(_next_name(prefix), title, list(data_pins))


def add_blueprint(path, graph_nodes):
    graphs = []
    for name, nodes in graph_nodes.items():
        graph = EdGraph(name)
        graph.nodes = nodes
        graphs.append(graph)
    blueprint = Blueprint(path, graphs)
    _state.assets[path] = blueprint
    _state.saved[path] = _snapshot(blueprint)
    return blueprint


def connect(from_node, from_pin, to_node, to_pin):
    source = next(pin for pin in from_node.pins if pin.name == from_pin)
    target = next(pin for pin in to_node.pins if pin.name == to_pin)
    source.links.append(target)
    target.links.append(source)


def state():
    return _state


_FUNCTIONS = {
    "/Script/Engine.KismetSystemLibrary.PrintString": (
        "Print String",
        [EXEC_IN, THEN, ("InString", EdGraphPinDirection.EGPD_INPUT, "Hello"),
         ("Incompatible", EdGraphPinDirection.EGPD_INPUT, "")],
    ),
    "/Script/Engine.KismetSystemLibrary.Delay": (
        "Delay", [EXEC_IN, THEN, ("Duration", EdGraphPinDirection.EGPD_INPUT, "0.2")],
    ),
    "/Script/Engine.Pawn.AddMovementInput": ("Add Movement Input", [EXEC_IN, THEN]),
    "/Game/_AgentBridgeSelfTest/BP_SelfTest_C.SelfTestFunction": (
        "Self Test Function", [EXEC_IN, THEN],
    ),
}


def _library_node_pins(node):
    return [BlueprintGraphPin(pin) for pin in node.pins]


class BlueprintEditorLibrary:
    @classmethod
    def get_current_engine_version(cls):
        return "5.8.fake"

    @classmethod
    def create_blueprint_asset_with_parent(cls, path, parent):
        blueprint = add_blueprint(path, {"EventGraph": []})
        _state.registry.append(AssetData(path, blueprint, loaded=True))
        return blueprint

    @classmethod
    def get_blueprint_asset(cls, asset):
        return asset if isinstance(asset, Blueprint) else None

    @classmethod
    def list_graphs(cls, blueprint):
        return list(blueprint.graphs)

    @classmethod
    def list_graph_names(cls, blueprint):
        return [graph.get_name() for graph in blueprint.graphs]

    @classmethod
    def find_event_graph(cls, blueprint):
        return blueprint.graphs[0]

    @classmethod
    def list_member_variable_names(cls, blueprint, include_inherited_members=True):
        return [name for name, _ in blueprint.variables]

    @classmethod
    def get_member_variable_type(cls, blueprint, name):
        return next(kind for var, kind in blueprint.variables if var == name)

    @classmethod
    def pin_type_to_json_schema(cls, pin_type, self_context):
        return '{"type": "%s"}' % pin_type

    @classmethod
    def generated_class(cls, blueprint):
        return Object(blueprint.get_path_name() + "_C")

    @classmethod
    def get_blueprint_parent_class(cls, blueprint):
        return Object("Character")

    @classmethod
    def get_basic_type_by_name(cls, name):
        if name == "nonsense":
            raise RuntimeError("bad type")
        return name

    @classmethod
    def get_array_type(cls, inner):
        return "array of " + inner

    @classmethod
    def get_set_type(cls, inner):
        return "set of " + inner

    @classmethod
    def get_map_type(cls, key, value):
        return "map of {} to {}".format(key, value)

    @classmethod
    def get_struct_type(cls, struct):
        return "struct " + struct

    @classmethod
    def get_object_reference_type(cls, klass):
        return "object " + klass

    @classmethod
    def get_class_reference_type(cls, klass):
        return "class " + klass

    @classmethod
    def add_member_variable(cls, blueprint, name, pin_type):
        blueprint.variables.append((name, pin_type))
        return True

    @classmethod
    def get_node_title(cls, node):
        return node.title

    @classmethod
    def get_comment_text(cls, node):
        return node.title

    @classmethod
    def set_comment_text(cls, node, text):
        node.title = text

    @classmethod
    def get_node_pos(cls, node):
        if not isinstance(node, K2Node):
            raise TypeError("expected K2Node")
        return IntPoint(node.x, node.y)

    @classmethod
    def set_node_pos(cls, node, position):
        if not isinstance(node, K2Node):
            raise TypeError("expected K2Node")
        node.x, node.y = position.x, position.y

    @classmethod
    def list_all_pins(cls, node):
        if not isinstance(node, K2Node):
            raise TypeError("expected K2Node")
        return _library_node_pins(node)

    @classmethod
    def add_event_override(cls, blueprint, event_name, position):
        event = K2Node(_next_name("K2Node_Event"), "Event " + event_name, [THEN])
        event.x, event.y = position.x, position.y
        blueprint.graphs[0].nodes.append(event)
        return event

    @classmethod
    def compile_blueprint(cls, blueprint):
        failed = False
        for graph in blueprint.graphs:
            for node in graph.nodes:
                node.error = ""
                if any(pin.value == "FAIL" and not pin.links for pin in node.pins):
                    node.error = "Bad value on node"
                    failed = True
                if node.title == "Add Movement Input":
                    node.error = "Target must have a connection"
                    failed = True
        blueprint.status = "Error" if failed else "UpToDate"
        return not failed


class BlueprintGraphEditor:
    def __init__(self, blueprint, graph):
        self._blueprint, self._graph = blueprint, graph

    @classmethod
    def get_graph_editor(cls, graph):
        return cls(None, graph)

    @classmethod
    def create_and_edit_function_graph(cls, blueprint, name):
        graph = EdGraph(name)
        entry = K2Node_FunctionEntry(_next_name("K2Node_FunctionEntry"), name, [THEN])
        graph.nodes.append(entry)
        blueprint.graphs.append(graph)
        return cls(blueprint, graph)

    def _add(self, node):
        self._graph.nodes.append(node)
        return node

    def list_all_nodes(self):
        return [node for node in self._graph.nodes if isinstance(node, K2Node)]

    def list_comment_nodes(self):
        return [node for node in self._graph.nodes if isinstance(node, EdGraphNode_Comment)]

    def list_nodes_with_errors(self):
        return [node for node in self.list_all_nodes() if node.error]

    def list_nodes_with_warnings(self):
        return []

    def list_available_nodes(self, context_pins):
        return ["Development|PrintString", "Utilities|FlowControl|Branch"]

    def find_graph_entry_pin(self):
        for node in self._graph.nodes:
            if isinstance(node, K2Node_FunctionEntry):
                return BlueprintGraphPin(node.pins[0])
        return BlueprintGraphPin(None)

    def add_call_function_node(self, path):
        if path not in _FUNCTIONS:
            return None
        title, pins = _FUNCTIONS[path]
        return self._add(make_node("K2Node_CallFunction", title, *pins))

    def add_branch_node(self):
        return self._add(make_node(
            "K2Node_IfThenElse", "Branch", EXEC_IN, THEN,
            ("else", EdGraphPinDirection.EGPD_OUTPUT),
            ("Condition", EdGraphPinDirection.EGPD_INPUT, "true")))

    def add_get_member_variable_node(self, name, class_path=""):
        return self._add(make_node("K2Node_VariableGet", "Get " + name,
                                   (name, EdGraphPinDirection.EGPD_OUTPUT)))

    def add_set_member_variable_node(self, name, class_path=""):
        return self._add(make_node("K2Node_VariableSet", "Set " + name, EXEC_IN, THEN))

    def add_custom_event_node(self, name):
        return self._add(make_node("K2Node_CustomEvent", name, THEN))

    def add_macro_node(self, path):
        if not path.endswith(":ForEachLoop"):
            return None
        return self._add(make_node("K2Node_MacroInstance", "ForEachLoop", EXEC_IN))

    def add_return_node(self):
        return self._add(make_node("K2Node_FunctionResult", "Return Node", EXEC_IN))

    def create_node_from_name(self, name, location, context_pins, declaring_class=None):
        if not isinstance(location, Vector2D):
            raise TypeError("expected Vector2D")
        return self._add(make_node("K2Node_IfThenElse", name, EXEC_IN, THEN))

    def add_comment_node(self, text, location):
        if not isinstance(location, Vector2D):
            raise TypeError("expected Vector2D")
        node = EdGraphNode_Comment(_next_name("EdGraphNode_Comment"), text, [])
        return self._add(node)

    def add_graph_input_parameter(self, name, pin_type, value):
        entry = self.find_graph_entry_pin().get_owning_node()
        entry.pins.append(_Pin(entry, name, EdGraphPinDirection.EGPD_OUTPUT, value))

    def remove_nodes(self, nodes):
        for node in nodes:
            for pin in node.pins:
                for other in list(pin.links):
                    other.links.remove(pin)
                pin.links = []
            self._graph.nodes.remove(node)

    def remove_comment_node(self, node):
        self._graph.nodes.remove(node)


class EditorAssetLibrary:
    @classmethod
    def load_asset(cls, path):
        for key, asset in _state.assets.items():
            if key == path or key.split(".")[0] == path:
                return asset
        return None

    @classmethod
    def delete_directory(cls, path):
        for key in [key for key in _state.assets if key.startswith(path)]:
            del _state.assets[key]
        _state.registry = [a for a in _state.registry if not a.package_name.startswith(path)]
        return True

    @classmethod
    def save_loaded_asset(cls, asset, only_if_is_dirty=True):
        _state.saved[asset.get_path_name()] = _snapshot(asset)
        _state.save_calls += 1
        return True


class EditorLoadingAndSavingUtils:
    @classmethod
    def get_dirty_content_packages(cls):
        return [asset.get_outermost() for key, asset in _state.assets.items()
                if key in _state.dirty]

    @classmethod
    def reload_packages(cls, packages, interaction_mode=None):
        for key, asset in _state.assets.items():
            if asset.get_outermost() in packages:
                _restore(asset, _state.saved[key])
        return True, ""


class ScopedEditorTransaction:
    def __init__(self, description):
        pass

    def __enter__(self):
        _state.transaction = []
        return self

    def __exit__(self, kind, value, trace):
        _state.transaction = None
        return False


class SystemLibrary:
    @classmethod
    def execute_console_command(cls, world, command):
        if command == "TRANSACTION UNDO":
            _state.undo_calls += 1
            if _state.undo_works and _state.undo_stack:
                blueprint, snapshot = _state.undo_stack.pop()
                _restore(blueprint, snapshot)


class UnrealEditorSubsystem:
    def get_editor_world(self):
        return Object("World")


class LevelEditorSubsystem:
    def is_in_play_in_editor(self):
        return _state.playing


def get_editor_subsystem(klass):
    return klass()


class Paths:
    content_dir = ""

    saved_dir = ""

    @classmethod
    def project_content_dir(cls):
        return cls.content_dir

    @classmethod
    def project_saved_dir(cls):
        return cls.saved_dir

    @classmethod
    def convert_relative_path_to_full(cls, path):
        return path


class TopLevelAssetPath:
    def __init__(self, package, asset):
        self.package, self.asset = package, asset


class AssetData:
    def __init__(self, package_name, asset, loaded=False):
        self.package_name, self._asset, self._loaded = package_name, asset, loaded
        self.get_asset_calls = 0

    def is_asset_loaded(self):
        return self._loaded

    def get_asset(self):
        self.get_asset_calls += 1
        return self._asset


class _Registry:
    def get_assets_by_class(self, class_path, search_sub_classes=False):
        return list(_state.registry)


class AssetRegistryHelpers:
    @classmethod
    def get_asset_registry(cls):
        return _Registry()


def log(message):
    pass


log_warning = log
log_error = log
