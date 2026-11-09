"""Runs the bridge against fake_unreal, and talks to its MCP server over real HTTP."""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "AgentBridge", "Content", "Python"))

import fake_unreal as unreal  # noqa: E402

sys.modules["unreal"] = unreal

from agent_bridge import blueprints, mcp_server, selftest, text_format, tools  # noqa: E402

PATH = "/Game/BP_Hero.BP_Hero"


def build_blueprint():
    """Event BeginPlay -> Print String "Hi", plus an unconnected Delay and a comment."""
    event = unreal.make_node("K2Node_Event", "Event BeginPlay", unreal.THEN)
    event.name_for_test = "event"
    printer = unreal.make_node(
        "K2Node_CallFunction", "Print String", unreal.EXEC_IN, unreal.THEN,
        ("InString", unreal.EdGraphPinDirection.EGPD_INPUT, "Hi"),
        ("Incompatible", unreal.EdGraphPinDirection.EGPD_INPUT, ""),
    )
    delay = unreal.make_node(
        "K2Node_CallFunction", "Delay", unreal.EXEC_IN, unreal.THEN,
        ("Duration", unreal.EdGraphPinDirection.EGPD_INPUT, "1.0"),
    )
    comment = unreal.EdGraphNode_Comment("EdGraphNode_Comment_0", "My note", [])
    printer.x, delay.x = 320, 640
    unreal.connect(event, "then", printer, "execute")
    return unreal.add_blueprint(PATH, {"EventGraph": [event, printer, delay, comment]})


def ids(blueprint):
    return {node.title: blueprints.node_id(node) for node in blueprint.graphs[0].nodes}


class BlueprintCase(unittest.TestCase):
    def setUp(self):
        unreal.state().reset()
        self.blueprint = build_blueprint()
        self.ids = ids(self.blueprint)

    def text(self):
        return blueprints.read_blueprint(PATH)

    def current(self):
        """The Blueprint as it is now: rollbacks replace its graph objects."""
        return unreal.state().assets[PATH]

    def nodes(self):
        return {node.title: node for node in self.current().graphs[0].nodes}

    def pin(self, title, name):
        return next(pin for pin in self.nodes()[title].pins if pin.name == name)


class ReadTests(BlueprintCase):
    def test_lists_nodes_links_and_defaults(self):
        text = self.text()
        self.assertIn("{}: Event BeginPlay @0,0".format(self.ids["Event BeginPlay"]), text)
        self.assertIn("then -> {}.execute".format(self.ids["Print String"]), text)
        self.assertIn("InString = Hi", text)
        self.assertIn("EdGraphNode_Comment_0: My note", text)

    def test_ids_drop_the_common_prefix(self):
        self.assertTrue(self.ids["Delay"].startswith("CallFunction_"))

    def test_one_graph_and_unknown_graph(self):
        self.assertTrue(
            blueprints.read_blueprint(PATH, "EventGraph").startswith("=== graph EventGraph"))
        with self.assertRaisesRegex(blueprints.BridgeError, "Graphs: EventGraph"):
            blueprints.read_blueprint(PATH, "Nope")

    def test_pin_detail_is_ignored_when_parsed_back(self):
        text = blueprints.read_blueprint(PATH, "", True)
        self.assertIn("# in InString: Fake Type", text)
        self.assertEqual(text_format.parse(text)[1], [])


class WriteTests(BlueprintCase):
    def test_writing_back_unchanged_changes_nothing(self):
        before = self.text()
        result = blueprints.write_blueprint(PATH, before)
        self.assertTrue(result["success"], result)
        self.assertEqual(self.text(), before)

    def test_edit_changes_a_default_and_saves(self):
        result = blueprints.edit_blueprint(PATH, "InString = Hi", "InString = Bye")
        self.assertTrue(result["saved"], result)
        self.assertEqual(self.pin("Print String", "InString").value, "Bye")
        self.assertEqual(unreal.state().save_calls, 1)

    def test_multiline_value_round_trips(self):
        blueprints.edit_blueprint(PATH, "InString = Hi", "InString = a\\nb")
        self.assertEqual(self.pin("Print String", "InString").value, "a\nb")
        self.assertIn("InString = a\\nb", self.text())

    def test_adds_a_new_node_and_wires_it(self):
        printer = self.ids["Print String"]
        old = "{}: Print String @320,0\n".format(printer)
        added = (
            "+wait call KismetSystemLibrary.Delay @700,50\n  Duration = 3.0\n" + old +
            "  then -> wait.execute\n"
        )
        result = blueprints.edit_blueprint(PATH, old, added)
        self.assertTrue(result["success"], result)
        real_id = result["id_map"]["wait"]
        self.assertIn("then -> {}.execute".format(real_id), result["text"])
        self.assertIn("Duration = 3.0", result["text"])

    def test_left_out_node_is_deleted_and_its_links_go(self):
        delay = self.ids["Delay"]
        text = blueprints.read_blueprint(PATH, "EventGraph")
        kept = [line for line in text.split("\n") if not line.startswith(delay)
                and "Duration" not in line]
        result = blueprints.write_blueprint(PATH, "\n".join(kept))
        self.assertTrue(result["success"], result)
        self.assertNotIn("Delay", self.nodes())

    def test_removing_a_link_line_breaks_the_link(self):
        link = "  then -> {}.execute\n".format(self.ids["Print String"])
        result = blueprints.edit_blueprint(PATH, link, "")
        self.assertTrue(result["success"], result)
        self.assertEqual(self.pin("Print String", "execute").links, [])

    def test_moves_a_node(self):
        blueprints.edit_blueprint(PATH, "Delay @640,0", "Delay @900,120")
        self.assertEqual((self.nodes()["Delay"].x, self.nodes()["Delay"].y), (900, 120))

    def test_comment_text_can_change(self):
        result = blueprints.edit_blueprint(PATH, "My note", "Better note")
        self.assertTrue(result["success"], result)
        self.assertIn("Better note", self.nodes())

    def test_new_comment_node(self):
        text = blueprints.read_blueprint(PATH, "EventGraph") + "\n+c comment Section A @10,10"
        result = blueprints.write_blueprint(PATH, text)
        self.assertTrue(result["success"], result)
        self.assertIn("Section A", self.nodes())

    def test_adds_variable(self):
        result = blueprints.write_blueprint(PATH, "var Health: float\nvar Items: array<int>")
        self.assertTrue(result["success"], result)
        self.assertEqual(self.current().variables,
                         [("Health", "float"), ("Items", "array of int")])

    def test_new_function_with_parameter_and_entry(self):
        text = (
            "=== graph Greet (new function)\n"
            "param in Who: string\n"
            "+start entry\n"
            "  then -> p.execute\n"
            "+p call KismetSystemLibrary.PrintString @300,0\n"
            "  InString = Yo\n"
        )
        result = blueprints.write_blueprint(PATH, text)
        self.assertTrue(result["success"], result)
        graph = next(g for g in self.current().graphs if g.get_name() == "Greet")
        entry = graph.nodes[0]
        self.assertEqual([pin.name for pin in entry.pins], ["then", "Who"])
        self.assertEqual(entry.pins[0].links[0].owner.title, "Print String")

    def test_entry_node_survives_being_left_out(self):
        blueprints.write_blueprint(PATH, "=== graph Greet (new function)\n")
        graph = next(g for g in self.current().graphs if g.get_name() == "Greet")
        self.assertEqual(len(graph.nodes), 1)
        result = blueprints.write_blueprint(PATH, "=== graph Greet\n")
        self.assertTrue(result["success"], result)
        graph = next(g for g in self.current().graphs if g.get_name() == "Greet")
        self.assertEqual(len(graph.nodes), 1)


class FailureTests(BlueprintCase):
    def assert_unchanged(self, before, result):
        self.assertFalse(result["success"])
        self.assertEqual(self.text(), before)
        self.assertEqual(unreal.state().save_calls, 0)

    def test_compile_error_rolls_back_and_reports_the_node(self):
        before = self.text()
        result = blueprints.edit_blueprint(PATH, "InString = Hi", "InString = FAIL")
        self.assert_unchanged(before, result)
        self.assertTrue(result["rolled_back"])
        self.assertIn("Bad value on node", result["errors"][0])
        self.assertIn(self.ids["Print String"], result["errors"][0])

    def test_allow_errors_keeps_the_change_but_does_not_save(self):
        result = blueprints.edit_blueprint(
            PATH, "InString = Hi", "InString = FAIL", allow_errors=True)
        self.assertFalse(result["compiled"])
        self.assertFalse(result["rolled_back"])
        self.assertEqual(self.pin("Print String", "InString").value, "FAIL")
        self.assertEqual(unreal.state().save_calls, 0)

    def test_parse_error_touches_nothing(self):
        before = self.text()
        result = blueprints.write_blueprint(PATH, "=== graph EventGraph\nnot a node")
        self.assert_unchanged(before, result)
        self.assertIn("line 2", result["errors"][0])
        self.assertEqual(unreal.state().undo_calls, 0)

    def test_wrong_pin_lists_the_real_ones_and_rolls_back(self):
        before = self.text()
        result = blueprints.edit_blueprint(PATH, "InString = Hi", "Wrong = Hi")
        self.assert_unchanged(before, result)
        self.assertIn("Available: execute, InString", result["errors"][0])

    def test_incompatible_link_rolls_back(self):
        before = self.text()
        event_link = "then -> {}.execute".format(self.ids["Print String"])
        result = blueprints.edit_blueprint(
            PATH, event_link, "then -> {}.Incompatible".format(self.ids["Print String"]))
        self.assert_unchanged(before, result)
        self.assertIn("Cannot connect", result["errors"][0])

    def test_rejected_value_rolls_back(self):
        before = self.text()
        result = blueprints.edit_blueprint(PATH, "InString = Hi", "InString = REJECT")
        self.assert_unchanged(before, result)
        self.assertIn("rejected", result["errors"][0])

    def test_changing_a_title_is_refused(self):
        before = self.text()
        result = blueprints.edit_blueprint(PATH, ": Delay @", ": Sleep @")
        self.assert_unchanged(before, result)
        self.assertIn("delete it and add a new one", result["errors"][0])

    def test_unknown_id_is_refused(self):
        before = self.text()
        text = "=== graph EventGraph\nGhost_1: Ghost @0,0\n"
        self.assert_unchanged(before, blueprints.write_blueprint(PATH, text))

    def test_unknown_function_rolls_back_earlier_new_nodes(self):
        before = self.text()
        text = blueprints.read_blueprint(PATH, "EventGraph") + (
            "\n+good call KismetSystemLibrary.Delay"
            "\n+bad call KismetSystemLibrary.DoesNotExist"
        )
        result = blueprints.write_blueprint(PATH, text)
        self.assert_unchanged(before, result)
        self.assertIn("DoesNotExist", result["errors"][0])

    def test_engine_exception_is_reported_and_rolled_back(self):
        before = self.text()
        text = "var Bad: nonsense"
        result = blueprints.write_blueprint(PATH, text)
        self.assert_unchanged(before, result)
        self.assertIn("bad type", result["errors"][0])

    def test_undo_not_working_falls_back_to_reload_when_clean(self):
        before = self.text()
        unreal.state().undo_works = False
        result = blueprints.edit_blueprint(PATH, "InString = Hi", "InString = FAIL")
        self.assertTrue(result["rolled_back"])
        self.assertEqual(self.text(), before)

    def test_unsaved_work_is_never_thrown_away_by_reload(self):
        unreal.state().undo_works = False
        unreal.state().dirty.add(PATH)
        result = blueprints.edit_blueprint(PATH, "InString = Hi", "InString = FAIL")
        self.assertFalse(result["rolled_back"])
        self.assertIn("ROLLBACK INCOMPLETE", result["errors"][-1])

    def test_refuses_during_play(self):
        unreal.state().playing = True
        with self.assertRaisesRegex(blueprints.BridgeError, "Play In Editor"):
            blueprints.write_blueprint(PATH, "var X: int")

    def test_missing_blueprint(self):
        with self.assertRaisesRegex(blueprints.BridgeError, "not found"):
            blueprints.read_blueprint("/Game/Nope")


class EditTests(BlueprintCase):
    def test_old_string_must_exist(self):
        with self.assertRaisesRegex(blueprints.BridgeError, "not found"):
            blueprints.edit_blueprint(PATH, "absent", "x")

    def test_old_string_must_be_unique(self):
        with self.assertRaisesRegex(blueprints.BridgeError, r"occurs \d+ times"):
            blueprints.edit_blueprint(PATH, "CallFunction_", "x")

    def test_removing_a_whole_section_is_refused(self):
        with self.assertRaisesRegex(blueprints.BridgeError, "removes or renames"):
            blueprints.edit_blueprint(PATH, "=== graph EventGraph", "=== graph Other")

    def test_only_changed_sections_are_sent(self):
        before = "blueprint x\n=== graph A\nn: t\n=== graph B\nm: u"
        after = "blueprint x\n=== graph A\nn: t\n=== graph B\nm: v"
        self.assertEqual(text_format.changed_sections(before, after), "=== graph B\nm: v")


class ParserTests(unittest.TestCase):
    def parse_one(self, body):
        document, errors = text_format.parse("=== graph G\n" + body)
        self.assertEqual(errors, [])
        return document.graphs[0].nodes[0]

    def test_title_may_contain_colon_and_at(self):
        node = self.parse_one("Id_1: Cast To: Foo @home @5,-6")
        self.assertEqual((node.title, node.x, node.y), ("Cast To: Foo @home", 5, -6))

    def test_value_containing_arrow_is_a_default(self):
        node = self.parse_one("Id_1: T\n  A = x -> y.z")
        self.assertEqual(node.defaults[0].value, "x -> y.z")
        self.assertEqual(node.links, [])

    def test_pin_names_with_spaces(self):
        node = self.parse_one("Id_1: T\n  Loop Body -> Id_2.Array Element")
        self.assertEqual(node.links[0].out_pin, "Loop Body")
        self.assertEqual(node.links[0].target_pin, "Array Element")

    def test_empty_default(self):
        self.assertEqual(self.parse_one("Id_1: T\n  A =").defaults[0].value, "")

    def test_duplicate_ids(self):
        _, errors = text_format.parse("=== graph G\nA: t\nA: t")
        self.assertIn("Duplicate", errors[0])

    def test_windows_line_endings(self):
        document, errors = text_format.parse("=== graph G\r\nA: t @1,2\r\n  P = v\r\n")
        self.assertEqual(errors, [])
        self.assertEqual(document.graphs[0].nodes[0].defaults[0].value, "v")

    def test_escape_round_trip(self):
        for value in ("a\nb", "back\\slash", "\\n literal", "plain", ""):
            self.assertEqual(text_format.unescape(text_format.escape(value)), value)

    def test_empty_document(self):
        self.assertTrue(text_format.parse("   \n")[1])


class SearchTests(BlueprintCase):
    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        unreal.Paths.content_dir = self.directory.name + os.sep
        self.match = self.add_asset("Hit", b"xx KismetSystem PrintString yy", self.blueprint)
        other = unreal.add_blueprint("/Game/Other.Other", {"EventGraph": []})
        self.miss = self.add_asset("Other", b"nothing relevant here", other)

    def add_asset(self, name, content, asset):
        with open(os.path.join(self.directory.name, name + ".uasset"), "wb") as handle:
            handle.write(content)
        data = unreal.AssetData("/Game/" + name, asset)
        unreal.state().registry.append(data)
        return data

    def test_finds_node_by_title_with_or_without_spaces(self):
        for query in ("Print String", "printstring"):
            result = blueprints.search_blueprints(query)
            self.assertEqual(result["results"][0]["title"], "Print String", query)
            self.assertEqual(result["results"][0]["graph"], "EventGraph")

    def test_unmatched_files_are_never_loaded(self):
        blueprints.search_blueprints("PrintString")
        self.assertEqual(self.match.get_asset_calls, 1)
        self.assertEqual(self.miss.get_asset_calls, 0)

    def test_title_words_not_in_the_file_still_match(self):
        result = blueprints.search_blueprints("Print KismetSystem")
        self.assertEqual(result["candidates"], 1)

    def test_loaded_assets_skip_the_file_filter(self):
        self.miss._loaded = True
        result = blueprints.search_blueprints("PrintString")
        self.assertEqual(result["candidates"], 2)

    def test_finds_pin_values(self):
        os.remove(os.path.join(self.directory.name, "Hit.uasset"))
        with open(os.path.join(self.directory.name, "Hit.uasset"), "wb") as handle:
            handle.write("Hi".encode("utf-16-le"))
        result = blueprints.search_blueprints("Hi")
        self.assertTrue(any(hit["matched"] == "pin InString" for hit in result["results"]))

    def test_limit_and_path_prefix(self):
        self.assertTrue(blueprints.search_blueprints("e", limit=1)["truncated"])
        self.assertEqual(blueprints.search_blueprints("Print", path_prefix="/Other")["scanned"], 0)


class ServerHarness:
    """Pumps poll() on its own thread, standing in for the editor's tick."""

    def __init__(self):
        self.server = mcp_server.Server(tools.call_tool, tools.list_tools, port=0)
        self.server._address = ("127.0.0.1", 0)
        self.server.start()
        self.port = self.server._listener.getsockname()[1]
        self.running = True
        self.thread = threading.Thread(target=self.pump, daemon=True)
        self.thread.start()

    def pump(self):
        while self.running:
            self.server.poll()
            time.sleep(0.001)

    def close(self):
        self.running = False
        self.thread.join()
        self.server.stop()

    def request(self, method="POST", body=None, path="/mcp", headers=None):
        data = None if body is None else (
            body if isinstance(body, bytes) else json.dumps(body).encode())
        request = urllib.request.Request(
            "http://127.0.0.1:{}{}".format(self.port, path), data=data, method=method,
            headers=dict({"Content-Type": "application/json"}, **(headers or {})))
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.read()

    def rpc(self, method, params=None):
        status, body = self.request(
            body={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
        assert status == 200, status
        return json.loads(body)


class McpTests(BlueprintCase):
    def setUp(self):
        super().setUp()
        self.harness = ServerHarness()
        self.addCleanup(self.harness.close)

    def call(self, name, **arguments):
        result = self.harness.rpc("tools/call", {"name": name, "arguments": arguments})["result"]
        return result["content"][0]["text"], result["isError"]

    def test_initialize_echoes_supported_protocol(self):
        result = self.harness.rpc("initialize", {"protocolVersion": "2025-06-18"})["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertIn("tools", result["capabilities"])

    def test_initialize_unknown_protocol_gets_latest(self):
        result = self.harness.rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]
        self.assertEqual(result["protocolVersion"], "2025-11-25")

    def test_notification_gets_202(self):
        status, _ = self.harness.request(
            body={"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(status, 202)

    def test_tools_list_has_valid_definitions(self):
        listed = self.harness.rpc("tools/list")["result"]["tools"]
        self.assertEqual(len(listed), 8)
        for tool in listed:
            self.assertTrue(tool["description"])
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_read_edit_over_http(self):
        text, is_error = self.call("blueprint_read", path=PATH)
        self.assertFalse(is_error)
        self.assertIn("Print String", text)
        output, is_error = self.call(
            "blueprint_edit", path=PATH, old_string="InString = Hi", new_string="InString = Ciao è")
        self.assertFalse(is_error, output)
        self.assertEqual(self.pin("Print String", "InString").value, "Ciao è")

    def test_failed_write_is_an_error_with_details(self):
        output, is_error = self.call(
            "blueprint_edit", path=PATH, old_string="InString = Hi", new_string="InString = FAIL")
        self.assertTrue(is_error)
        self.assertTrue(json.loads(output)["rolled_back"])

    def test_bridge_error_is_a_plain_message(self):
        output, is_error = self.call("blueprint_read", path="/Game/Nope")
        self.assertTrue(is_error)
        self.assertNotIn("Traceback", output)

    def test_run_python(self):
        output, is_error = self.call("run_python", code="print('hi')\nresult = 6 * 7")
        self.assertFalse(is_error)
        self.assertEqual(json.loads(output)["output"], "hi\n")
        self.assertEqual(json.loads(output)["result"], "42")

    def test_run_python_reports_exceptions(self):
        output, is_error = self.call("run_python", code="1/0")
        self.assertTrue(is_error)
        self.assertIn("ZeroDivisionError", json.loads(output)["error"])

    def test_large_payload_survives(self):
        big = "x" * 3000000
        output, is_error = self.call(
            "blueprint_edit", path=PATH, old_string="InString = Hi", new_string="InString = " + big)
        self.assertFalse(is_error, output[:300])
        self.assertEqual(len(self.pin("Print String", "InString").value), 3000000)

    def test_unknown_method_and_tool(self):
        self.assertEqual(self.harness.rpc("nope")["error"]["code"], -32601)
        self.assertTrue(self.call("nope")[1])

    def test_rejects_other_origins_and_hosts(self):
        body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        status, _ = self.harness.request(body=body, headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, _ = self.harness.request(body=body, headers={"Host": "evil.example"})
        self.assertEqual(status, 403)
        status, _ = self.harness.request(body=body, headers={"Origin": "http://localhost:3000"})
        self.assertEqual(status, 200)

    def test_http_errors(self):
        self.assertEqual(self.harness.request("GET")[0], 405)
        self.assertEqual(self.harness.request(body=b"{not json")[0], 400)
        self.assertEqual(self.harness.request(body=b"[1,2]")[0], 400)
        self.assertEqual(self.harness.request(path="/other", body={})[0], 404)

    def test_poll_does_not_reenter(self):
        server = mcp_server.Server(tools.call_tool, tools.list_tools, port=0)
        server._listener = object()
        server._polling = True
        calls = []
        server._poll_once = lambda: calls.append(1)
        server.poll()
        self.assertEqual(calls, [])


class SelfTestTests(unittest.TestCase):
    def setUp(self):
        unreal.state().reset()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        unreal.Paths.saved_dir = self.directory.name + os.sep
        unreal.Paths.content_dir = self.directory.name + os.sep

    def report(self):
        with open(os.path.join(self.directory.name, "AgentBridge_selftest.txt"),
                  encoding="utf-8") as handle:
            return handle.read()

    def test_all_steps_pass_on_the_documented_api(self):
        passed, failed = selftest.run()
        self.assertEqual(failed, 0, self.report())
        self.assertEqual(passed, len(selftest._STEPS))

    def test_temporary_asset_is_deleted(self):
        selftest.run()
        self.assertEqual(unreal.state().assets, {})

    def test_a_failing_step_is_reported_and_the_rest_still_run(self):
        original = unreal.BlueprintGraphEditor.add_branch_node
        unreal.BlueprintGraphEditor.add_branch_node = lambda self: None
        self.addCleanup(setattr, unreal.BlueprintGraphEditor, "add_branch_node", original)
        passed, failed = selftest.run()
        self.assertGreater(failed, 0)
        self.assertGreater(passed, 10)
        self.assertIn("FAIL  Node kind: branch", self.report())
        self.assertEqual(unreal.state().assets, {})

    def test_missing_api_names_what_is_missing(self):
        original = unreal.BlueprintGraphEditor.add_macro_node
        del unreal.BlueprintGraphEditor.add_macro_node
        self.addCleanup(setattr, unreal.BlueprintGraphEditor, "add_macro_node", original)
        selftest.run()
        self.assertIn("BlueprintGraphEditor.add_macro_node", self.report())


if __name__ == "__main__":
    unittest.main(verbosity=1)
