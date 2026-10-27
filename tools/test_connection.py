"""Bounded BRP transport tests using a real local HTTP server."""
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest

spec = importlib.util.spec_from_file_location('connection', Path(__file__).parents[1] / 'extension/connection.py')
connection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(connection)


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.reply = dict(jsonrpc='2.0', id=1, result={'ok': True})
        self.raw_reply = None
        self.requests = []
        case = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                case.requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(case.raw_reply or json.dumps(case.reply).encode())
            def log_message(self, *args): pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = f'http://127.0.0.1:{self.server.server_port}'
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_method_and_current_values_are_sent_without_game_assumptions(self):
        self.assertEqual(connection.post(self.endpoint, 'demo/validate', {'objects': []}), {'ok': True})
        self.assertEqual(self.requests[0]['method'], 'demo/validate')
        self.assertEqual(self.requests[0]['params'], {'objects': []})

    def test_rpc_failure_is_distinct_from_unavailable_connection(self):
        self.reply = dict(jsonrpc='2.0', id=1, error={'code': -32601, 'message': 'Method missing'})
        with self.assertRaises(connection.RpcError) as raised:
            connection.post(self.endpoint, 'demo/missing', {})
        self.assertEqual(raised.exception.code, -32601)
        with self.assertRaises(connection.Unavailable):
            connection.post('http://127.0.0.1:1', 'demo/validate', {}, timeout=0.1)

    def test_malformed_response_is_not_success(self):
        for body in ({'result': {}}, {'jsonrpc': '2.0', 'id': 99, 'result': {}},
                     {'jsonrpc': '2.0', 'id': 1},
                     {'jsonrpc': '2.0', 'id': 1, 'result': float('inf')}):
            self.reply = body
            with self.subTest(body=body), self.assertRaises(ValueError):
                connection.post(self.endpoint, 'demo/validate', {})

    def test_overflow_number_is_not_accepted_as_finite_json(self):
        self.raw_reply = b'{"jsonrpc":"2.0","id":1,"result":{"value":1e309}}'
        with self.assertRaises(ValueError):
            connection.post(self.endpoint, 'demo/validate', {})

    def test_deeply_nested_response_is_a_reported_failure(self):
        self.raw_reply = b'{"jsonrpc":"2.0","id":1,"result":' + b'[' * 3000 + b'0' + b']' * 3000 + b'}'
        with self.assertRaises(ValueError):
            connection.post(self.endpoint, 'demo/validate', {})

    def test_nonfinite_request_is_rejected_before_network(self):
        with self.assertRaises(ValueError):
            connection.post(self.endpoint, 'demo/validate', {'value': float('nan')})
        self.assertEqual(self.requests, [])


if __name__ == '__main__': unittest.main()
