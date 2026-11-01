import os
import tempfile
import unittest
from unittest.mock import patch

_test_database = tempfile.TemporaryDirectory()
os.environ['SMART_CODE_CHECKER_DB'] = os.path.join(_test_database.name, 'bootstrap.sqlite3')

import app as checker


class SmartCodeCheckerTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        checker.app.config['TESTING'] = True
        checker.app.config['DATABASE'] = os.path.join(self.temp_directory.name, 'reviews.sqlite3')
        checker.init_db()
        self.client = checker.app.test_client()
        self.original_api_key = checker.API_KEY
        checker.API_KEY = 'test-key'

    def tearDown(self):
        checker.API_KEY = self.original_api_key
        self.temp_directory.cleanup()

    def create_review(self, language='Python'):
        with patch.object(
            checker,
            'call_gemini',
            return_value=('[High] Unsafe input handling.\n\n[Low] Missing validation.', None)
        ):
            return self.client.post('/review', json={
                'code': 'run_query(user_input)',
                'language': language
            })

    def test_review_persists_language_and_dashboard_severity(self):
        response = self.create_review('SQL')
        self.assertEqual(response.status_code, 200)
        review = response.get_json()
        self.assertEqual(review['severity_counts'], {
            'critical': 0, 'high': 1, 'medium': 0, 'low': 1
        })

        conversation = self.client.get(
            f"/api/conversations/{review['conversation_id']}"
        ).get_json()
        self.assertEqual(conversation['conversation']['language'], 'SQL')
        self.assertEqual(conversation['messages'][0]['code'], 'run_query(user_input)')

        dashboard = self.client.get('/api/dashboard').get_json()
        self.assertEqual(dashboard['total_reviews'], 1)
        self.assertEqual(dashboard['total_issues'], 2)
        self.assertEqual(dashboard['severity_totals']['high'], 1)
        self.assertEqual(dashboard['language_usage'], [{'language': 'SQL', 'reviews': 1}])

    def test_follow_up_sends_saved_conversation_context(self):
        with patch.object(checker, 'call_gemini', side_effect=[
            ('[Medium] Check the query result.', None),
            ('Use a parameterized query to prevent injection.', None)
        ]) as gemini:
            review = self.client.post('/review', json={
                'code': 'query(user_input)', 'language': 'JavaScript'
            }).get_json()
            response = self.client.post(
                f"/api/conversations/{review['conversation_id']}/messages",
                json={'message': 'How should I fix this?'}
            )

        self.assertEqual(response.status_code, 200)
        _, language, contents = gemini.call_args.args
        self.assertEqual(language, 'JavaScript')
        self.assertEqual([item['role'] for item in contents], ['user', 'model', 'user'])
        self.assertEqual(contents[-1]['parts'][0]['text'], 'How should I fix this?')

    def test_search_rename_and_delete_conversation(self):
        review = self.create_review('Python').get_json()
        conversation_id = review['conversation_id']

        results = self.client.get('/api/conversations?q=user_input').get_json()
        self.assertEqual([item['id'] for item in results], [conversation_id])
        renamed = self.client.patch(
            f'/api/conversations/{conversation_id}', json={'title': 'Input validation'}
        )
        self.assertEqual(renamed.get_json()['title'], 'Input validation')
        deleted = self.client.delete(f'/api/conversations/{conversation_id}')
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get('/api/conversations').get_json(), [])
        self.assertEqual(self.client.get('/api/dashboard').get_json()['total_reviews'], 0)

    def test_rejects_unsupported_language_and_empty_code(self):
        unsupported = self.client.post('/review', json={'code': 'x', 'language': 'Ruby'})
        empty = self.client.post('/review', json={'code': '  ', 'language': 'Python'})
        self.assertEqual(unsupported.status_code, 400)
        self.assertEqual(empty.status_code, 400)


if __name__ == '__main__':
    unittest.main()