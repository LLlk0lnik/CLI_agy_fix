"""Unit tests for process-local SmartDNS resolver, DoH parser and SNI forwarder."""
import socket
import struct
import time
import unittest
from unittest.mock import Mock, patch

from smart_dns import (build_dns_query, parse_dns_a_records, SmartDNSResolver,
                       SmartDNSHTTPSConnection, SmartDnsForwarder, DEFAULT_SNI_IPS, GOOGLE_HOSTS)


class SmartDNSTests(unittest.TestCase):
    def test_build_dns_query(self):
        query = build_dns_query('test.example.com')
        # Check header
        tid, flags, qdcount, ancount, nscount, arcount = struct.unpack('!HHHHHH', query[:12])
        self.assertEqual(tid, 0x1234)
        self.assertEqual(flags, 0x0100)
        self.assertEqual(qdcount, 1)
        self.assertEqual(ancount, 0)
        # Check qname and tail
        self.assertIn(b'\x04test\x07example\x03com\x00', query)
        qtype, qclass = struct.unpack('!HH', query[-4:])
        self.assertEqual(qtype, 1)  # A
        self.assertEqual(qclass, 1)  # IN

    def test_parse_dns_a_records(self):
        # Empty or truncated packet
        self.assertEqual(parse_dns_a_records(b''), [])
        self.assertEqual(parse_dns_a_records(b'too short'), [])

        # Minimal packet with one A record for 1.2.3.4
        # Header (12 bytes): ID=1, Flags=0x8180, QDCOUNT=1, ANCOUNT=1, NSCOUNT=0, ARCOUNT=0
        header = struct.pack('!HHHHHH', 1, 0x8180, 1, 1, 0, 0)
        # Question: \x01a\x00 (domain "a"), QTYPE=1, QCLASS=1
        question = b'\x01a\x00' + struct.pack('!HH', 1, 1)
        # Answer: compression pointer \xc0\x0c (to offset 12), TYPE=1, CLASS=1, TTL=300, RDLEN=4, RDATA=1.2.3.4
        answer = b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 300, 4) + bytes([1, 2, 3, 4])
        packet = header + question + answer

        ips = parse_dns_a_records(packet)
        self.assertEqual(ips, ['1.2.3.4'])

    def test_parse_dns_filters_loopback_and_zero(self):
        header = struct.pack('!HHHHHH', 1, 0x8180, 1, 2, 0, 0)
        question = b'\x01a\x00' + struct.pack('!HH', 1, 1)
        ans1 = b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 300, 4) + bytes([127, 0, 0, 1])
        ans2 = b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 300, 4) + bytes([109, 120, 131, 200])
        packet = header + question + ans1 + ans2

        ips = parse_dns_a_records(packet)
        self.assertEqual(ips, ['109.120.131.200'])

    def test_resolver_fallback_and_caching(self):
        resolver = SmartDNSResolver(fallback_ips=['1.1.1.1', '2.2.2.2'], endpoints=[], ttl_seconds=60)
        resolved = resolver.resolve('cloudcode-pa.googleapis.com')
        self.assertEqual(resolved, ['1.1.1.1', '2.2.2.2'])

        # Cached hit
        self.assertIn('cloudcode-pa.googleapis.com', resolver.cache)
        self.assertEqual(resolver.get_sni_ip('cloudcode-pa.googleapis.com'), '1.1.1.1')

    def test_resolver_mark_failed_deprioritizes_ip(self):
        resolver = SmartDNSResolver(fallback_ips=['1.1.1.1', '2.2.2.2'], endpoints=[], ttl_seconds=60)
        self.assertEqual(resolver.get_sni_ip(), '1.1.1.1')

        resolver.mark_failed('1.1.1.1')
        # Now 2.2.2.2 should be preferred because 1.1.1.1 is in failure cooldown
        self.assertEqual(resolver.get_sni_ip(), '2.2.2.2')

    def test_smart_dns_connection_targets_sni_ip(self):
        conn = SmartDNSHTTPSConnection('cloudcode-pa.googleapis.com', 443)
        mock_sock = Mock()
        mock_context = Mock()
        mock_context.wrap_socket.return_value = mock_sock
        conn._context = mock_context
        conn.context = mock_context

        with patch.object(conn, '_create_connection', return_value=mock_sock) as mock_connect:
            with patch('smart_dns.GLOBAL_RESOLVER.get_sni_ip', return_value='109.120.131.200'):
                conn.connect()
                mock_connect.assert_called_once_with(('109.120.131.200', 443), conn.timeout, conn.source_address)
                mock_context.wrap_socket.assert_called_once_with(mock_sock, server_hostname='cloudcode-pa.googleapis.com')

    def test_smart_dns_forwarder_lifecycle_and_non_connect(self):
        forwarder = SmartDnsForwarder()
        forwarder.start()
        try:
            self.assertTrue(forwarder.running)
            self.assertTrue(forwarder.port > 0)
            # Send GET request instead of CONNECT
            with socket.create_connection(('127.0.0.1', forwarder.port), timeout=2.0) as client:
                client.sendall(b'GET / HTTP/1.1\r\nHost: example.com\r\n\r\n')
                resp = client.recv(1024)
                self.assertIn(b'501 Not Implemented', resp)
        finally:
            forwarder.stop()
            self.assertFalse(forwarder.running)


if __name__ == '__main__':
    unittest.main()
