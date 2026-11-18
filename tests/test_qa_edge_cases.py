"""QA edge-case and negative tests for Smart Coding Assistant.

Owner: Muhd Nurasyraf (QA & Test Engineer)
Run:   python -m pytest tests -v        (or: python -m unittest discover -s tests -v)

Gemini is mocked throughout, so no API key or internet is needed.
Tests marked "DEFECT" currently FAIL on purpose: each one documents a bug
found during QA. When the bug is fixed, the test will pass.
"""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import app as checker

VALID_REVIEW = {
    'overall_assessment': {'summary': 'ok', 'quality': 'Good',
                           'total_issues': 0, 'total_suggestions': 0},
    'issues': [], 'suggestions': [],
    'corrected_code': 'print(1)\n',
    'changes_summary': [], 'remaining_concerns': [],
}


class QAEdgeCaseTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix='.sqlite3')
        os.close(fd)
        checker.app.config['DATABASE'] = self.db_path
        checker.app.config['TESTING'] = True
        checker.init_db()
        self.client = checker.app.test_client()
        self.key = patch.object(checker, 'API_KEY', 'test-key')
        self.key.start()

    def tearDown(self):
        self.key.stop()
        os.remove(self.db_path)

    def make_review(self):
        import json
        with patch.object(checker, 'call_gemini',
                          return_value=(json.dumps(VALID_REVIEW), None)):
            r = self.client.post('/review', json={'code': 'print(1)', 'language': 'Python'})
        self.assertEqual(r.status_code, 200)
        return r.get_json()['conversation_id']

    # ---------- Input validation (passing) ----------
    def test_missing_api_key_returns_clear_500(self):
        with patch.object(checker, 'API_KEY', None):
            r = self.client.post('/review', json={'code': 'x', 'language': 'Python'})
        self.assertEqual(r.status_code, 500)
        self.assertIn('GEMINI_API_KEY', r.get_json()['error'])

    def test_whitespace_only_code_rejected(self):
        r = self.client.post('/review', json={'code': '   \n\t ', 'language': 'Python'})
        self.assertEqual(r.status_code, 400)

    def test_non_json_body_rejected(self):
        r = self.client.post('/review', data='code=print(1)',
                             content_type='application/x-www-form-urlencoded')
        self.assertEqual(r.status_code, 400)

    def test_all_ten_languages_accepted_and_sent_to_gemini(self):
        import json
        expected = ['Python', 'JavaScript', 'Java', 'PHP', 'C', 'C++', 'C#', 'SQL', 'HTML', 'CSS']
        self.assertEqual(checker.LANGUAGES, expected)
        for language in expected:
            with self.subTest(language=language):
                with patch.object(checker, 'call_gemini',
                                  return_value=(json.dumps(VALID_REVIEW), None)) as m:
                    r = self.client.post('/review', json={'code': 'x', 'language': language})
                self.assertEqual(r.status_code, 200)
                sent_language, contents = m.call_args.args[1], m.call_args.args[2]
                self.assertEqual(sent_language, language)               # system prompt language
                self.assertIn(f'Programming language: {language}',     # user prompt language
                              contents[0]['parts'][0]['text'])
                conv = self.client.get(f"/api/conversations/{r.get_json()['conversation_id']}")
                self.assertEqual(conv.get_json()['conversation']['language'], language)  # saved
        usage = {row['language'] for row in self.client.get('/api/dashboard').get_json()['language_usage']}
        self.assertEqual(usage, set(expected))                          # dashboard counts all 10

    def test_language_is_case_sensitive(self):
        # Documents current behaviour: 'python' is rejected, only 'Python' works.
        r = self.client.post('/review', json={'code': 'x', 'language': 'python'})
        self.assertEqual(r.status_code, 400)

    def test_unknown_conversation_returns_404(self):
        self.assertEqual(self.client.get('/api/conversations/9999').status_code, 404)
        self.assertEqual(self.client.delete('/api/conversations/9999').status_code, 404)
        r = self.client.post('/api/conversations/9999/messages', json={'message': 'hi'})
        self.assertEqual(r.status_code, 404)

    def test_empty_follow_up_rejected(self):
        cid = self.make_review()
        r = self.client.post(f'/api/conversations/{cid}/messages', json={'message': '  '})
        self.assertEqual(r.status_code, 400)

    def test_rename_truncates_to_100_chars(self):
        cid = self.make_review()
        r = self.client.patch(f'/api/conversations/{cid}', json={'title': 'A' * 250})
        self.assertEqual(len(r.get_json()['title']), 100)

    def test_delete_cascades_to_messages(self):
        cid = self.make_review()
        self.client.delete(f'/api/conversations/{cid}')
        db = sqlite3.connect(self.db_path)
        left = db.execute('SELECT COUNT(*) FROM messages WHERE conversation_id=?', (cid,)).fetchone()[0]
        db.close()
        self.assertEqual(left, 0)

    # ---------- Security (passing) ----------
    def test_search_is_parameterised_against_sql_injection(self):
        self.make_review()
        r = self.client.get("/api/conversations?q=' OR 1=1; DROP TABLE conversations;--")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), [])
        self.assertEqual(self.client.get('/api/conversations').status_code, 200)  # table still exists

    def test_api_key_not_leaked_in_responses(self):
        with patch.object(checker, 'call_gemini', return_value=(None, 'm: HTTP 503')):
            r = self.client.post('/review', json={'code': 'x', 'language': 'Python'})
        self.assertNotIn('test-key', r.get_data(as_text=True))

    # ---------- Resilience (passing) ----------
    def test_all_models_failing_returns_502_not_crash(self):
        with patch.object(checker, 'call_gemini', return_value=(None, 'm: timeout')) as m:
            r = self.client.post('/review', json={'code': 'x', 'language': 'Python'})
        self.assertEqual(r.status_code, 502)
        self.assertEqual(m.call_count, len(checker.MODELS))  # fallback tried every model

    def test_fallback_to_second_model(self):
        import json
        responses = [(None, 'first: HTTP 429'), (json.dumps(VALID_REVIEW), None)]
        with patch.object(checker, 'call_gemini', side_effect=responses):
            r = self.client.post('/review', json={'code': 'print(1)', 'language': 'Python'})
        self.assertEqual(r.status_code, 200)

    # ---------- DEFECTS (fail until fixed) ----------
    def test_DEFECT_rename_with_non_string_title_should_be_400(self):
        cid = self.make_review()
        r = self.client.patch(f'/api/conversations/{cid}', json={'title': 123})
        self.assertEqual(r.status_code, 400)  # currently 500 (AttributeError)

    def test_DEFECT_rename_with_list_body_should_be_400(self):
        cid = self.make_review()
        r = self.client.patch(f'/api/conversations/{cid}', json=['x'])
        self.assertEqual(r.status_code, 400)  # currently 500 (AttributeError)

    def test_DEFECT_oversized_code_should_be_rejected(self):
        # Slides claim "payload length caps"; nothing enforces one.
        with patch.object(checker, 'call_gemini', return_value=(None, 'x')) as m:
            r = self.client.post('/review', json={'code': 'x=1\n' * 500_000, 'language': 'Python'})
        self.assertEqual(r.status_code, 413,
                         f'~2 MB of code was accepted (HTTP {r.status_code}) instead of rejected with 413')
        m.assert_not_called()

    def test_DEFECT_rapid_requests_should_be_rate_limited(self):
        # Slides claim "Flask rate limiting"; no limiter is installed.
        with patch.object(checker, 'call_gemini', return_value=(None, 'x')):
            codes = {self.client.post('/review', json={'code': 'a', 'language': 'Python'}).status_code
                     for _ in range(30)}
        self.assertIn(429, codes, f'30 rapid requests, none blocked; status codes seen: {sorted(codes)}')

    def test_DEFECT_failed_review_should_not_count_on_dashboard(self):
        with patch.object(checker, 'call_gemini', return_value=(None, 'm: HTTP 503')):
            self.client.post('/review', json={'code': 'x', 'language': 'Python'})
        data = self.client.get('/api/dashboard').get_json()
        self.assertEqual(data['total_reviews'], 0,
                         f'A failed review is counted on the dashboard (total_reviews={data["total_reviews"]})')

    def test_DEFECT_corrupt_stored_row_should_not_crash_history(self):
        cid = self.make_review()
        db = sqlite3.connect(self.db_path)
        db.execute("UPDATE messages SET severity_counts='not json' WHERE conversation_id=?", (cid,))
        db.commit()
        db.close()
        r = self.client.get(f'/api/conversations/{cid}')
        self.assertEqual(r.status_code, 200)  # currently 500 (JSONDecodeError)


if __name__ == '__main__':
    unittest.main()
