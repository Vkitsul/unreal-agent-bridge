"""The Blueprint text format: parsing and section handling. Does not use the unreal module.

    blueprint /Game/BP_Hero.BP_Hero parent=Character status=UpToDate
    var Health: {"type":"number"}
    === graph EventGraph
    Event_0: Event BeginPlay @0,0
      then -> CallFunction_3.execute
    CallFunction_3: Print String @320,0
      InString = Hello
    +delay call KismetSystemLibrary.Delay @640,0
      Duration = 2.0

A write replaces each graph it names with what the text describes. Existing nodes are
"id: title". New nodes are "+label kind args". Existing nodes left out are deleted.
Links are written once, on the output pin.
"""

import re
from dataclasses import dataclass, field

GRAPH_PREFIX = "=== graph "
NEW_FUNCTION_SUFFIX = " (new function)"

_POSITION = re.compile(r"\s@(-?\d+),(-?\d+)\s*$")


def escape(value):
    """Encode a value so that it stays on one line."""
    return value.replace("\\", "\\\\").replace("\r", "").replace("\n", "\\n")


def unescape(value):
    out = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value) and value[index + 1] in "n\\":
            out.append("\n" if value[index + 1] == "n" else "\\")
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


@dataclass
class Link:
    out_pin: str
    target_id: str
    target_pin: str
    line: int


@dataclass
class Default:
    pin: str
    value: str
    line: int


@dataclass
class Node:
    id: str
    is_new: bool
    line: int
    title: str = ""
    kind: str = ""
    args: str = ""
    x: int = 0
    y: int = 0
    has_position: bool = False
    links: list = field(default_factory=list)
    defaults: list = field(default_factory=list)


@dataclass
class Param:
    direction: str
    name: str
    type: str
    value: str
    line: int


@dataclass
class Variable:
    name: str
    type: str
    line: int


@dataclass
class Graph:
    name: str
    is_new_function: bool
    line: int
    nodes: list = field(default_factory=list)
    params: list = field(default_factory=list)


@dataclass
class Document:
    variables: list = field(default_factory=list)
    graphs: list = field(default_factory=list)


def _parse_header(line, number):
    """Parse a node header. Returns (node, error)."""
    x = y = 0
    has_position = False
    match = _POSITION.search(line)
    if match:
        x, y = int(match.group(1)), int(match.group(2))
        has_position = True
        line = line[: match.start()]
    line = line.rstrip()

    if line.startswith("+"):
        parts = line[1:].split(None, 2)
        if len(parts) < 2:
            return None, "Expected '+label kind [args]'"
        args = parts[2] if len(parts) > 2 else ""
        return (
            Node(parts[0], True, number, kind=parts[1], args=args, x=x, y=y,
                 has_position=has_position),
            None,
        )

    if ":" not in line:
        return None, "Expected 'id: title' or '+label kind [args]'"
    node_id, title = line.split(":", 1)
    if " " in node_id:
        return None, "Node ids contain no spaces. New nodes are written '+label kind [args]'"
    return (
        Node(node_id, False, number, title=unescape(title.strip()), x=x, y=y,
             has_position=has_position),
        None,
    )


def _parse_body(text, number, node):
    """Parse an indented line. Returns an error message or None."""
    arrow = text.find(" -> ")
    equals = text.find(" = ")
    # Pin names contain neither separator, so whichever comes first decides the line type.
    if arrow != -1 and (equals == -1 or arrow < equals):
        target = text[arrow + 4 :].strip()
        if "." not in target:
            return "Link target must be 'nodeId.PinName'"
        target_id, target_pin = target.split(".", 1)
        node.links.append(Link(text[:arrow].strip(), target_id, target_pin, number))
        return None
    if equals != -1:
        node.defaults.append(Default(text[:equals].strip(), unescape(text[equals + 3 :]), number))
        return None
    if text.rstrip().endswith(" ="):
        node.defaults.append(Default(text.rstrip()[:-2].strip(), "", number))
        return None
    return "Expected 'Pin -> id.Pin', 'Pin = value' or '# comment'"


def _parse_param(line, number):
    """Parse 'param in|out Name: type [= value]'. Returns (param, error)."""
    parts = line.split(None, 2)
    if len(parts) < 3 or parts[1] not in ("in", "out") or ":" not in parts[2]:
        return None, "Expected 'param in|out Name: type [= value]'"
    name, rest = parts[2].split(":", 1)
    value = ""
    if " = " in rest:
        rest, value = rest.split(" = ", 1)
    return Param(parts[1], name.strip(), rest.strip(), unescape(value), number), None


def parse(text):
    """Parse a document without touching any Blueprint. Returns (document, errors)."""
    document = Document()
    errors = []
    graph = None
    node = None

    def add_error(number, message):
        errors.append("line {}: {}".format(number, message))

    for number, raw in enumerate(text.split("\n"), 1):
        line = raw.rstrip("\r")
        stripped = line.strip()
        if not stripped:
            continue

        if line.startswith(GRAPH_PREFIX):
            name = line[len(GRAPH_PREFIX) :].strip()
            is_new = name.endswith(NEW_FUNCTION_SUFFIX)
            if is_new:
                name = name[: -len(NEW_FUNCTION_SUFFIX)].strip()
            graph = Graph(name, is_new, number)
            document.graphs.append(graph)
            node = None
            continue

        if line[0] in " \t":
            if stripped.startswith("#"):
                continue
            if node is None:
                add_error(number, "Indented line has no node above it")
                continue
            error = _parse_body(line.lstrip(), number, node)
            if error:
                add_error(number, error)
            continue

        if line.startswith("#"):
            continue

        if graph is None:
            if line.startswith("blueprint "):
                continue
            if line.startswith("var ") and ":" in line:
                name, rest = line[4:].split(":", 1)
                document.variables.append(Variable(name.strip(), rest.strip(), number))
                continue
            add_error(number, "Expected '{}Name' before nodes".format(GRAPH_PREFIX))
            continue

        if line.startswith("param "):
            param, error = _parse_param(line, number)
            if error:
                add_error(number, error)
            else:
                graph.params.append(param)
            continue

        parsed, error = _parse_header(line, number)
        if error:
            add_error(number, error)
            node = None
            continue
        if any(other.id == parsed.id for other in graph.nodes):
            add_error(number, "Duplicate node id '{}'".format(parsed.id))
        graph.nodes.append(parsed)
        node = parsed

    if not document.graphs and not document.variables and not errors:
        errors.append("Text contains no graph sections or variables")
    return document, errors


def split_sections(text):
    """Split a document into (preamble, [(graph header line, whole section text)])."""
    preamble = []
    sections = []
    current = preamble
    for line in text.split("\n"):
        if line.startswith(GRAPH_PREFIX):
            current = [line]
            sections.append(current)
        else:
            current.append(line)
    return "\n".join(preamble), [(lines[0], "\n".join(lines)) for lines in sections]


def changed_sections(before, after):
    """The parts of `after` that differ from `before`, ready to write.

    Sending only what changed keeps an edit from rewriting graphs it never touched.
    Raises ValueError if the edit removed or renamed a whole graph section.
    """
    old_preamble, old_sections = split_sections(before)
    new_preamble, new_sections = split_sections(after)
    old_by_header = dict(old_sections)
    new_headers = {header for header, _ in new_sections}

    for header, _ in old_sections:
        if header not in new_headers:
            raise ValueError(
                "This edit removes or renames the section '{}'. Whole graphs cannot be "
                "removed or renamed this way; use run_python.".format(header)
            )

    parts = []
    if new_preamble != old_preamble:
        parts.append(new_preamble)
    for header, section in new_sections:
        if old_by_header.get(header) != section:
            parts.append(section)
    return "\n".join(parts)
