"""Real loopback TLS fixture; production DNS guard is tested separately."""
import http.server
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from web_growth_guard.http import Collector, PinnedHTTPS, FetchError, UA


@unittest.skipUnless(shutil.which('openssl'), 'OpenSSL CLI required for temporary fixture certificate')
class WireTests(unittest.TestCase):
    def test_tls_get_and_same_origin_redirect(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            cert, key = base/'cert.pem', base/'key.pem'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes', '-days','1',
                            '-subj','/CN=example.invalid','-addext','subjectAltName=DNS:example.invalid',
                            '-keyout',str(key),'-out',str(cert)], check=True, capture_output=True, timeout=15)
            seen, names = [], []
            class Handler(http.server.BaseHTTPRequestHandler):
                def log_message(self,*args): pass
                def do_GET(self):
                    seen.append((self.path,self.headers.get('User-Agent'),self.headers.get('Cookie'),self.headers.get('Authorization')))
                    if self.path == '/start':
                        self.send_response(302); self.send_header('Location','/end'); self.end_headers()
                    else:
                        raw=b'synthetic TLS response'
                        self.send_response(200); self.send_header('Content-Type','text/plain'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
            server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert,key)
            context.set_servername_callback(lambda sock,name,ctx: names.append(name))
            server.socket=context.wrap_socket(server.socket,server_side=True)
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            try:
                def connection(host,address,timeout):
                    # Test-only injected loopback transport and trust anchor. Production
                    # connection always uses the validated public address, standard CAs and 443.
                    conn=PinnedHTTPS(host,'127.0.0.1',timeout)
                    conn.port=server.server_port
                    conn._context=ssl.create_default_context(cafile=str(cert))
                    return conn
                def resolver(*args): return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))]
                origin='https://example.invalid'
                c=Collector(origin,resolver=resolver,connection=connection,pause=lambda _:None)
                r=c.get(origin+'/start')
                self.assertEqual(r.body,b'synthetic TLS response')
                self.assertEqual(r.redirects,({'status':302,'to':origin+'/end'},))
                self.assertEqual(seen,[('/start',UA,None,None),('/end',UA,None,None)])
                self.assertEqual(names,['example.invalid','example.invalid'])
                with self.assertRaisesRegex(FetchError,'ROBOTS_TOOL_DISALLOWED'):
                    c.get(origin+'/start',allowed=lambda path:path!='/end')
                other=Collector('https://different.invalid',resolver=resolver,connection=connection,pause=lambda _:None)
                with self.assertRaisesRegex(FetchError,'HTTP_UNAVAILABLE_OR_INVALID'):
                    other.get('https://different.invalid/end')
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=5)
