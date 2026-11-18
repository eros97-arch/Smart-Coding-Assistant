"""Security tests for Smart Coding Assistant (OWASP-style checks).

Owner: Muhd Nurasyraf (QA & Test Engineer)
Run:   venv\\bin\\python -m pytest tests/test_security.py -v --tb=no

Gemini is mocked, so no API key or internet is needed.
Tests named "test_VULN_..." currently FAIL on purpose: each one is a security
weakness found during testing (proof of exploitation). It passes once fixed.
"""
import json
import os
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import app as checker

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE_KEY = 'AQ.test-secret-key-123'
REVIEW = {
    'overall_assessment': {'summary': 'ok', 'quality': 'Good',
                           'total_issues': 0, 'total_suggestions': 0},
    'issues': [], 'suggestions': [], 'corrected_code': 'x = 1\n',
    'changes_summary': [], 'remaining_concerns': [],
}
XSS = '<script>alert(1)</script><img src=x onerror=alert(1)>'


class SecurityTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix='.sqlite3')
        os.close(fd)
        checker.app.config['DATABASE'] = self.db_path
        checker.app.config['TESTING'] = True
        checker.init_db()
        self.client = checker.app.test_client()
        self.key = patch.object(checker, 'API_KEY', FAKE_KEY)
        self.key.start()

    def tearDown(self):
        self.key.stop()
        os.remove(self.db_path)

    def review(self, code='x = 1', reply=None):
        with patch.object(checker, 'call_gemini',
                          return_value=(json.dumps(reply or REVIEW), None)):
            r = self.client.post('/review', json={'code': code, 'language': 'Python'})
        return r

    # ===== A03 Injection: SQL =====
    def test_sql_injection_in_search_is_blocked(self):
        self.review()
        for payload in ["' OR '1'='1", "'; DROP TABLE conversations;--", '" OR 1=1 --']:
            r = self.client.get('/api/conversations', query_string={'q': payload})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.get_json(), [], payload)
        self.assertEqual(len(self.client.get('/api/conversations').get_json()), 1)

    def test_sql_injection_in_title_and_code_is_stored_as_plain_text(self):
        payload = "x'); DELETE FROM messages;--"
        cid = self.review(code=payload).get_json()['conversation_id']
        self.client.patch(f'/api/conversations/{cid}', json={'title': payload})
        conv = self.client.get(f'/api/conversations/{cid}').get_json()
        self.assertEqual(conv['conversation']['title'], payload)
        self.assertEqual(conv['messages'][0]['code'], payload)

    def test_non_numeric_ids_are_rejected_by_routing(self):
        for bad in ['1 OR 1=1', '../etc/passwd', '-1']:
            self.assertEqual(self.client.get(f'/api/conversations/{bad}').status_code, 404)

    # ===== A03 Injection: XSS (server side) =====
    def test_xss_payload_is_returned_as_json_data_not_html(self):
        cid = self.review(code=XSS).get_json()['conversation_id']
        self.client.patch(f'/api/conversations/{cid}', json={'title': XSS})
        r = self.client.get(f'/api/conversations/{cid}')
        # Served as JSON (browser won't run it as HTML); stored exactly, not altered or executed
        self.assertEqual(r.mimetype, 'application/json')
        self.assertEqual(r.headers.get('Content-Type'), 'application/json')
        self.assertEqual(r.get_json()['conversation']['title'], XSS)
        self.assertEqual(r.get_json()['messages'][0]['code'], XSS)

    def test_frontend_sanitises_ai_markdown_with_dompurify(self):
        js = open(os.path.join(ROOT, 'static', 'script.js'), encoding='utf-8').read()
        self.assertIn('DOMPurify.sanitize(marked.parse(', js)
        # every innerHTML assignment must take sanitised html
        for line in re.findall(r'.*\.innerHTML\s*=.*', js):
            self.assertRegex(line, r'innerHTML\s*=\s*(html|\'\'|"")', line.strip())

    # ===== A02 / Secrets =====
    def test_api_key_never_sent_to_browser(self):
        self.assertNotIn(FAKE_KEY, self.client.get('/').get_data(as_text=True))
        self.assertNotIn(FAKE_KEY, self.client.get('/api/dashboard').get_data(as_text=True))
        with patch.object(checker, 'call_gemini', return_value=(None, 'm: HTTP 503')):
            r = self.client.post('/review', json={'code': 'x', 'language': 'Python'})
        self.assertNotIn(FAKE_KEY, r.get_data(as_text=True))

    def test_api_key_never_stored_in_database(self):
        self.review()
        db = sqlite3.connect(self.db_path)
        try:
            dump = '\n'.join(db.iterdump())
        finally:
            db.close()  # Windows cannot delete the file while it is still open
        self.assertNotIn(FAKE_KEY, dump)

    def test_api_key_not_hardcoded_in_source(self):
        src = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
        self.assertIsNone(re.search(r'AIza[0-9A-Za-z_\-]{20,}|AQ\.[0-9A-Za-z_\-]{20,}', src))
        self.assertIn("os.environ.get(\"GEMINI_API_KEY\")", src)

    def test_api_key_sent_in_header_not_url(self):
        captured = {}

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps({'candidates': [{'content': {'parts': [{'text': 'ok'}]}}]}).encode()

        def fake_urlopen(req, timeout):
            captured['url'], captured['headers'] = req.full_url, dict(req.header_items())
            return Resp()
        with patch.object(checker.urllib.request, 'urlopen', fake_urlopen):
            checker.call_gemini('m', 'Python', [])
        self.assertNotIn(FAKE_KEY, captured['url'])
        self.assertTrue(captured['url'].startswith('https://'))
        self.assertIn(FAKE_KEY, captured['headers'].values())

    def test_server_errors_do_not_leak_stack_traces(self):
        with patch.object(checker, 'call_gemini', side_effect=RuntimeError('secret internal detail')):
            r = self.client.post('/review', json={'code': 'x', 'language': 'Python'})
        self.assertEqual(r.status_code, 500)
        self.assertNotIn('secret internal detail', r.get_data(as_text=True))
        self.assertNotIn('Traceback', r.get_data(as_text=True))

    # ===== LLM01 Prompt injection (output side) =====
    def test_ai_output_with_injected_html_is_kept_as_text(self):
        evil = dict(REVIEW, overall_assessment=dict(REVIEW['overall_assessment'], summary=XSS))
        r = self.review(reply=evil)
        self.assertEqual(r.get_json()['review']['overall_assessment']['summary'], XSS)
        js = open(os.path.join(ROOT, 'static', 'script.js'), encoding='utf-8').read()
        self.assertIn('assessmentSummary.textContent = review.overall_assessment.summary', js)

    def test_ai_cannot_invent_fields_outside_the_schema(self):
        evil = dict(REVIEW, run_shell='rm -rf /', issues=[{'id': 'X', 'title': 't', 'severity': 'DROP',
                    'category': 'Hack', 'start_line': 999, 'end_line': 999, 'original_code': 'nope',
                    'explanation': 'e', 'impact': 'i', 'suggested_fix': 'f', 'confidence': 5}])
        review = self.review(reply=evil).get_json()['review']
        self.assertNotIn('run_shell', review)
        issue = review['issues'][0]
        self.assertEqual((issue['severity'], issue['category']), ('Medium', 'Other'))
        self.assertIsNone(issue['start_line'])
        self.assertEqual(issue['confidence'], 0.5)

    # ===== VULNERABILITIES FOUND (fail until fixed) =====
    def test_VULN_debug_mode_enabled(self):
        src = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
        self.assertFalse('debug=True' in src,
                         'app.py runs with debug=True: Werkzeug debugger allows remote code execution')

    def test_VULN_no_security_headers(self):
        h = self.client.get('/').headers
        missing = [n for n in ('Content-Security-Policy', 'X-Content-Type-Options', 'X-Frame-Options')
                   if n not in h]
        self.assertFalse(missing, f'Missing security headers: {", ".join(missing)}')

    def test_VULN_cdn_scripts_have_no_integrity_check(self):
        html = open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8').read()
        cdn = re.findall(r'<script[^>]+src="https://[^"]+"[^>]*>', html)
        self.assertTrue(cdn)
        for tag in cdn:
            self.assertTrue('integrity=' in tag,
                            f'No integrity= check on CDN script: {tag}')  # hacked CDN could run any script

    def test_VULN_no_size_limit_quota_exhaustion(self):
        with patch.object(checker, 'call_gemini', return_value=(None, 'x')) as m:
            self.client.post('/review', json={'code': 'A' * 2_000_000, 'language': 'Python'})
        self.assertEqual(m.call_count, 0,
                         f'2 MB of code was accepted and sent to Gemini {m.call_count} time(s)')

    def test_VULN_no_rate_limit(self):
        with patch.object(checker, 'call_gemini', return_value=(None, 'x')):
            codes = {self.client.post('/review', json={'code': 'a', 'language': 'Python'}).status_code
                     for _ in range(30)}
        self.assertIn(429, codes, f'30 rapid requests, none blocked; status codes seen: {sorted(codes)}')

    def test_VULN_no_access_control_anyone_can_delete_history(self):
        cid = self.review().get_json()['conversation_id']
        other_user = checker.app.test_client()  # no login, no token, no session
        status = other_user.delete(f'/api/conversations/{cid}').status_code
        self.assertNotEqual(status, 200, 'A client with no login deleted another user\'s review (HTTP 200)')


if __name__ == '__main__':
    unittest.main()
