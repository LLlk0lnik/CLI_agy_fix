"""Process-local SmartDNS resolver and SNI proxy routing for Google Cloud Code / Gemini."""
import base64
import http.client
import logging
import socket
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger('smart_dns')

GOOGLE_HOSTS = ('cloudcode-pa.googleapis.com', 'daily-cloudcode-pa.googleapis.com')

# Verified fallback SNI proxy IPs (dns-ai.ru and comss.one egress servers)
DEFAULT_SNI_IPS = [
    '109.120.131.200',
    '206.223.251.18',
    '89.169.55.148',
    '206.223.246.75',
    '157.22.181.209',
    '199.189.255.234',
]

DOH_ENDPOINTS = [
    'https://dns.comss.one/dns-query',
    'https://dns.dns-ai.ru/dns-query',
]


def build_dns_query(domain):
    """Build a minimal RFC 1035 A-record DNS query packet."""
    header = struct.pack('!HHHHHH', 0x1234, 0x0100, 1, 0, 0, 0)
    qname = b''.join(bytes([len(p)]) + p.encode('ascii') for p in domain.split('.') if p) + b'\x00'
    qtype_qclass = struct.pack('!HH', 1, 1)  # A, IN
    return header + qname + qtype_qclass


def parse_dns_a_records(data):
    """Parse IPv4 addresses from RFC 1035 DNS response bytes."""
    ips = []
    if not isinstance(data, (bytes, bytearray)) or len(data) < 12:
        return ips
    try:
        offset = 12
        while offset < len(data) and data[offset] != 0:
            if (data[offset] & 0xC0) == 0xC0:
                offset += 2
                break
            offset += data[offset] + 1
        else:
            offset += 1
        offset += 4  # skip QTYPE and QCLASS
        ancount = struct.unpack('!H', data[6:8])[0]
        for _ in range(ancount):
            if offset >= len(data):
                break
            if (data[offset] & 0xC0) == 0xC0:
                offset += 2
            else:
                while offset < len(data) and data[offset] != 0:
                    offset += data[offset] + 1
                offset += 1
            if offset + 10 > len(data):
                break
            atype, aclass, ttl, rdlen = struct.unpack('!HHIH', data[offset:offset + 10])
            offset += 10
            if atype == 1 and rdlen == 4 and offset + 4 <= len(data):
                ip = '.'.join(str(b) for b in data[offset:offset + 4])
                # Filter out obvious invalid or loopback addresses
                if not ip.startswith('127.') and not ip.startswith('0.'):
                    ips.append(ip)
            offset += rdlen
    except Exception:
        pass
    return ips


def query_doh(endpoint, domain, timeout=3.0):
    """Query a DoH server for domain A-records."""
    try:
        query_bytes = build_dns_query(domain)
        b64 = base64.urlsafe_b64encode(query_bytes).decode('ascii').rstrip('=')
        url = endpoint + '?dns=' + b64
        req = urllib.request.Request(url, headers={
            'Accept': 'application/dns-message',
            'User-Agent': 'Mozilla/5.0 (compatible; SmartDNS/1.0)'
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return parse_dns_a_records(resp.read())
    except Exception:
        pass
    return []


class SmartDNSResolver:
    """Thread-safe dynamic resolver with in-memory caching and verified static fallback."""

    def __init__(self, fallback_ips=None, endpoints=None, ttl_seconds=300):
        self.fallback_ips = list(DEFAULT_SNI_IPS if fallback_ips is None else fallback_ips)
        self.endpoints = list(DOH_ENDPOINTS if endpoints is None else endpoints)
        self.ttl = ttl_seconds
        self.lock = threading.Lock()
        self.cache = {}
        self.failures = {}

    def resolve(self, domain='cloudcode-pa.googleapis.com'):
        """Resolve domain using DoH or fallback to known healthy SNI proxy IPs."""
        now = time.monotonic()
        with self.lock:
            cached = self.cache.get(domain)
            if cached and cached['expires_at'] > now and cached['ips']:
                return self._filter_healthy(cached['ips'])

        # Query DoH endpoints
        resolved_ips = []
        for ep in self.endpoints:
            ips = query_doh(ep, domain)
            if ips:
                resolved_ips.extend(ips)
                break

        # Deduplicate while preserving order
        unique_ips = list(dict.fromkeys(resolved_ips + self.fallback_ips))

        with self.lock:
            self.cache[domain] = {'ips': unique_ips, 'expires_at': now + self.ttl}
            return self._filter_healthy(unique_ips)

    def _filter_healthy(self, ips):
        now = time.monotonic()
        # Sort so healthy IPs come first; failed IPs sit out for 60 seconds
        healthy = [ip for ip in ips if self.failures.get(ip, 0) <= now]
        return healthy or ips

    def get_sni_ip(self, host='cloudcode-pa.googleapis.com'):
        """Return the best currently available SNI proxy IP."""
        candidates = self.resolve(host)
        return candidates[0] if candidates else self.fallback_ips[0]

    def mark_failed(self, ip):
        """Temporarily deprioritize an IP that failed to connect or reset."""
        with self.lock:
            self.failures[ip] = time.monotonic() + 60


# Global resolver instance
GLOBAL_RESOLVER = SmartDNSResolver()


class SmartDNSHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that routes through the SNI proxy while keeping TLS verification intact."""

    def connect(self):
        target_ip = GLOBAL_RESOLVER.get_sni_ip(self.host) if self.host in GOOGLE_HOSTS else self.host
        try:
            self.sock = self._create_connection((target_ip, self.port), self.timeout, self.source_address)
        except Exception:
            if target_ip != self.host:
                GLOBAL_RESOLVER.mark_failed(target_ip)
            raise
        if self._tunnel_host:
            self._tunnel()
        server_hostname = self.host
        context = getattr(self, '_context', None) or getattr(self, 'context', None)
        self.sock = context.wrap_socket(self.sock, server_hostname=server_hostname)


class SmartDNSHandler(urllib.request.HTTPSHandler):
    """urllib.request handler routing Google Cloud Code requests through SmartDNS."""

    def https_open(self, req):
        return self.do_open(SmartDNSHTTPSConnection, req)


class SmartDnsForwarder:
    """Lightweight process-local HTTP CONNECT forwarder for child processes (e.g. ACP client)."""

    def __init__(self, resolver=None, bind_host='127.0.0.1'):
        self.resolver = resolver or GLOBAL_RESOLVER
        self.bind_host = bind_host
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((self.bind_host, 0))
        self.server_sock.listen(128)
        self.port = self.server_sock.getsockname()[1]
        self.running = False
        self.thread = None

    @property
    def proxy_url(self):
        return f'http://{self.bind_host}:{self.port}'

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        try:
            self.server_sock.close()
        except Exception:
            pass
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)

    def _serve(self):
        while self.running:
            try:
                client_sock, _ = self.server_sock.accept()
                threading.Thread(target=self._handle_client, args=(client_sock,), daemon=True).start()
            except Exception:
                break

    def _handle_client(self, client):
        try:
            client.settimeout(15.0)
            req = b''
            while b'\r\n\r\n' not in req and len(req) < 16384:
                chunk = client.recv(4096)
                if not chunk:
                    break
                req += chunk
            if not req:
                client.close()
                return
            first_line = req.split(b'\r\n')[0].decode('latin1', errors='replace')
            parts = first_line.split()
            if len(parts) >= 2 and parts[0].upper() == 'CONNECT':
                target = parts[1]
                host, port_str = target.split(':') if ':' in target else (target, '443')
                port = int(port_str)
                connect_host = self.resolver.get_sni_ip(host) if host in GOOGLE_HOSTS else host
                remote = socket.create_connection((connect_host, port), timeout=15.0)
                client.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
                self._pipe_duplex(client, remote)
            else:
                client.sendall(b'HTTP/1.1 501 Not Implemented\r\n\r\n')
                client.close()
        except Exception:
            try:
                client.close()
            except Exception:
                pass

    def _pipe_duplex(self, sock1, sock2):
        def forward(src, dst):
            try:
                while self.running:
                    data = src.recv(8192)
                    if not data:
                        break
                    dst.sendall(data)
            except Exception:
                pass
            finally:
                try:
                    src.close()
                except Exception:
                    pass
                try:
                    dst.close()
                except Exception:
                    pass

        t1 = threading.Thread(target=forward, args=(sock1, sock2), daemon=True)
        t2 = threading.Thread(target=forward, args=(sock2, sock1), daemon=True)
        t1.start()
        t2.start()
