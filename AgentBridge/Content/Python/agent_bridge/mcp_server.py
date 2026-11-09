"""A minimal MCP Streamable HTTP server, built only on the standard library.

It uses no threads: poll() does a little non-blocking socket work and is called from the
editor's tick, so every tool runs on the game thread as the unreal API requires.
"""

import json
import select
import socket
from urllib.parse import urlsplit

_SUPPORTED_PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26")
_MAX_REQUEST_BYTES = 32 * 1024 * 1024
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")

_REASONS = {200: "OK", 202: "Accepted", 400: "Bad Request", 403: "Forbidden", 404: "Not Found",
            405: "Method Not Allowed", 413: "Payload Too Large"}


class _Connection:
    def __init__(self, sock):
        self.sock = sock
        self.received = bytearray()
        self.to_send = b""
        self.responding = False


def _parse_request(data):
    """Returns (method, path, headers, body) once a whole request has arrived, else None."""
    end = data.find(b"\r\n\r\n")
    if end == -1:
        return None
    lines = bytes(data[:end]).decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) < 2:
        return "", "", {}, b""
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0") or 0)
    if len(data) < end + 4 + length:
        return None
    return parts[0], parts[1], headers, bytes(data[end + 4 : end + 4 + length])


def _http_response(status, body=b"", content_type="application/json"):
    head = (
        "HTTP/1.1 {} {}\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n"
    ).format(status, _REASONS.get(status, ""), content_type, len(body))
    return head.encode("latin-1") + body


def _is_local(headers):
    """Blocks web pages from reaching this server through DNS rebinding."""
    host = headers.get("host", "").rsplit(":", 1)[0]
    if host not in _LOCAL_HOSTS:
        return False
    origin = headers.get("origin")
    return origin is None or urlsplit(origin).hostname in ("127.0.0.1", "localhost", "::1")


def _rpc_result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _rpc_error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


class Server:
    def __init__(self, call_tool, list_tools, port, host="127.0.0.1"):
        self._call_tool = call_tool
        self._list_tools = list_tools
        self._address = (host, port)
        self._listener = None
        self._connections = []
        self._polling = False

    def start(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(self._address)
            listener.listen(16)
            listener.setblocking(False)
        except OSError:
            listener.close()
            raise
        self._listener = listener

    def stop(self):
        for connection in self._connections:
            connection.sock.close()
        self._connections = []
        if self._listener:
            self._listener.close()
            self._listener = None

    def poll(self):
        """Handle whatever socket work is ready. Never blocks waiting for the network."""
        # A compile can tick Slate, which would call back into here mid-request.
        if self._polling or not self._listener:
            return
        self._polling = True
        try:
            self._poll_once()
        finally:
            self._polling = False

    def _poll_once(self):
        readers = [self._listener] + [c.sock for c in self._connections if not c.responding]
        writers = [c.sock for c in self._connections if c.to_send]
        readable, writable, _ = select.select(readers, writers, [], 0)

        if self._listener in readable:
            try:
                sock, _ = self._listener.accept()
                sock.setblocking(False)
                self._connections.append(_Connection(sock))
            except OSError:
                pass

        for connection in list(self._connections):
            if connection.sock in readable:
                self._read(connection)
            if connection in self._connections and connection.sock in writable:
                self._write(connection)

    def _close(self, connection):
        connection.sock.close()
        self._connections.remove(connection)

    def _read(self, connection):
        try:
            data = connection.sock.recv(65536)
        except BlockingIOError:
            return
        except OSError:
            self._close(connection)
            return
        if not data:
            self._close(connection)
            return
        connection.received.extend(data)
        if len(connection.received) > _MAX_REQUEST_BYTES:
            connection.to_send = _http_response(413)
            connection.responding = True
            return
        request = _parse_request(connection.received)
        if request is not None:
            connection.to_send = self._respond(*request)
            connection.responding = True

    def _write(self, connection):
        try:
            sent = connection.sock.send(connection.to_send)
        except BlockingIOError:
            return
        except OSError:
            self._close(connection)
            return
        connection.to_send = connection.to_send[sent:]
        if not connection.to_send:
            self._close(connection)

    def _respond(self, method, path, headers, body):
        if not _is_local(headers):
            return _http_response(403)
        if urlsplit(path).path != "/mcp":
            return _http_response(404)
        if method != "POST":
            return _http_response(405)
        try:
            message = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return _http_response(400)
        if not isinstance(message, dict):
            return _http_response(400)

        response = self._handle_message(message)
        if response is None:
            return _http_response(202)  # Notifications get no JSON-RPC response.
        return _http_response(200, json.dumps(response).encode("utf-8"))

    def _handle_message(self, message):
        request_id = message.get("id")
        method = message.get("method", "")
        if request_id is None:
            return None
        params = message.get("params") or {}

        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in _SUPPORTED_PROTOCOLS else _SUPPORTED_PROTOCOLS[0]
            return _rpc_result(
                request_id,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "agent-bridge", "version": "0.1.0"},
                },
            )
        if method == "ping":
            return _rpc_result(request_id, {})
        if method == "tools/list":
            return _rpc_result(request_id, {"tools": self._list_tools()})
        if method == "tools/call":
            text, is_error = self._call_tool(params.get("name", ""), params.get("arguments") or {})
            return _rpc_result(
                request_id, {"content": [{"type": "text", "text": text}], "isError": is_error}
            )
        return _rpc_error(request_id, -32601, "Method not found: {}".format(method))
