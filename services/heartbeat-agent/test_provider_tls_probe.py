from __future__ import annotations

import io
import unittest
from unittest.mock import Mock, patch

import provider_tls_probe as probe


class ProviderTlsProbeTests(unittest.TestCase):
    def test_probe_sends_only_fixed_connect_before_tls_handshake(self) -> None:
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        connection.recv.side_effect = [b"HTTP/1.1 200 Connection established\r\n\r\n"]
        tls = Mock()
        tls.__enter__ = Mock(return_value=tls)
        tls.__exit__ = Mock(return_value=False)
        context = Mock()
        context.wrap_socket.return_value = tls

        probe.probe_provider_tls(
            create_connection=Mock(return_value=connection),
            create_tls_context=Mock(return_value=context),
        )

        sent = connection.sendall.call_args.args[0]
        self.assertEqual(
            sent,
            b"CONNECT hc-ping.com:443 HTTP/1.1\r\n"
            b"Host: hc-ping.com:443\r\n"
            b"Proxy-Connection: keep-alive\r\n\r\n",
        )
        tls.do_handshake.assert_called_once_with()
        tls.sendall.assert_not_called()
        context.wrap_socket.assert_called_once_with(connection, server_hostname="hc-ping.com")

    def test_probe_refuses_without_explicit_authorization_flag(self) -> None:
        with patch.object(probe.sys, "argv", ["provider_tls_probe.py"]), patch.object(
            probe, "probe_provider_tls"
        ) as contact:
            output = io.StringIO()
            with patch("sys.stderr", output):
                self.assertEqual(probe.main(), 2)
        contact.assert_not_called()
        self.assertIn("Refusing provider contact", output.getvalue())


if __name__ == "__main__":
    unittest.main()
