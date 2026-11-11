import os
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

_test_database = tempfile.TemporaryDirectory()
os.environ['SMART_CODE_CHECKER_DB'] = os.path.join(_test_database.name, 'bootstrap.sqlite3')

import app as checker


def structured_response(source_code, issues=None, suggestions=None, corrected_code=None):
    return json.dumps({
        'overall_assessment': {
            'summary': 'Review complete.',
            'quality': 'Poor' if issues else 'Good',
            'total_issues': len(issues or []),
            'total_suggestions': len(suggestions or [])
        },
        'issues': issues or [],
        'suggestions': suggestions or [],
        'corrected_code': source_code if corrected_code is None else corrected_code,
        'changes_summary': ['Reviewed the complete source.'],
        'remaining_concerns': []
    })


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
        source_code = 'run_query(user_input)'
        findings = [
            {
                'id': 'ISSUE-001', 'title': 'Unsafe input handling',
                'severity': 'High', 'category': 'Security',
                'start_line': 1, 'end_line': 1, 'original_code': source_code,
                'explanation': 'The input is passed to the query without parameterization.',
                'impact': 'An attacker may alter the query.',
                'suggested_fix': 'Use a parameterized query.', 'confidence': 0.96
            },
            {
                'id': 'ISSUE-002', 'title': 'Missing validation',
                'severity': 'Low', 'category': 'Validation',
                'start_line': 1, 'end_line': 1, 'original_code': source_code,
                'explanation': 'The input is not validated.',
                'impact': 'Unexpected values may be processed.',
                'suggested_fix': 'Validate the value before use.', 'confidence': 0.8
            }
        ]
        with patch.object(
            checker,
            'call_gemini',
            return_value=(structured_response(source_code, findings), None)
        ):
            return self.client.post('/review', json={
                'code': source_code,
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
        self.assertEqual(conversation['messages'][1]['review']['issues'][0]['start_line'], 1)
        self.assertEqual(review['review']['corrected_code'], 'run_query(user_input)')

        dashboard = self.client.get('/api/dashboard').get_json()
        self.assertEqual(dashboard['total_reviews'], 1)
        self.assertEqual(dashboard['total_issues'], 2)
        self.assertEqual(dashboard['severity_totals']['high'], 1)
        self.assertEqual(dashboard['language_usage'], [{'language': 'SQL', 'reviews': 1}])

    def test_follow_up_sends_saved_conversation_context(self):
        with patch.object(checker, 'call_gemini', side_effect=[
            (structured_response('query(user_input)'), None),
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
        self.assertNotIn('structured', gemini.call_args.kwargs)

    def test_structured_review_preserves_planted_findings_and_original_locations(self):
        source_lines = [
            'def average(values):',
            '    total = sum(values)',
            '    return total / len(values)',
            '',
            'def discount(price, percent):',
            '    if percent > 0 or percent <= 100:',
            '        return price * (1 - percent / 100)',
            '    return price',
            '',
            'def load_number(path):',
            "    handle = open(path, 'r')",
            '    raw = handle.read()',
            '    handle.close()',
            '    return float(raw)',
            '',
            'def ratio(numerator, denominator):',
            '    return numerator / denominator',
            '',
            'def add_tax(price, rate):',
            '    return price + rate / 100',
            '',
            'def query_user(cursor, name):',
            '''    cursor.execute("SELECT * FROM users WHERE name = '%s'" % name)''',
            '',
            'def parse_count(value):',
            '    return int(value)',
            '',
            'def malformed():',
            '    return ('
        ]
        source_code = '\n'.join(source_lines)

        def line_for(text):
            return source_lines.index(text) + 1

        def issue(issue_id, title, severity, category, start, end=None):
            end = start if end is None else end
            return {
                'id': issue_id,
                'title': title,
                'severity': severity,
                'category': category,
                'start_line': start,
                'end_line': end,
                'original_code': '\n'.join(source_lines[start - 1:end]),
                'explanation': f'{title} is present in the submitted code.',
                'impact': 'The program may fail or produce an unsafe result.',
                'suggested_fix': 'Validate the input and handle this case.',
                'confidence': 0.91
            }

        findings = [
            issue('ISSUE-001', 'Empty average divides by zero', 'High', 'Runtime',
                  line_for('    return total / len(values)')),
            issue('ISSUE-002', 'Discount condition accepts invalid percentages', 'Medium', 'Logic',
                  line_for('    if percent > 0 or percent <= 100:')),
            issue('ISSUE-003', 'File operations are not safely managed', 'Medium', 'Exception Handling',
                  line_for("    handle = open(path, 'r')"),
                  line_for('    handle.close()')),
            issue('ISSUE-004', 'Floating-point input may be invalid or non-finite', 'Medium', 'Validation',
                  line_for('    return float(raw)')),
            issue('ISSUE-005', 'Ratio divides by a zero denominator', 'High', 'Boundary Condition',
                  line_for('    return numerator / denominator')),
            issue('ISSUE-006', 'Tax calculation adds the rate instead of the tax amount', 'Medium', 'Logic',
                  line_for('    return price + rate / 100')),
            issue('ISSUE-007', 'SQL query interpolates untrusted input', 'Critical', 'Security',
                  line_for('''    cursor.execute("SELECT * FROM users WHERE name = '%s'" % name)''')),
            issue('ISSUE-008', 'Integer conversion can raise for invalid input', 'Medium', 'Validation',
                  line_for('    return int(value)')),
            issue('ISSUE-009', 'Unclosed parenthesis is a syntax error', 'High', 'Syntax',
                  line_for('    return ('))
        ]
        suggestions = [{
            'id': 'SUGGESTION-001',
            'title': 'Use a context manager for the file',
            'category': 'Best Practice',
            'start_line': line_for("    handle = open(path, 'r')"),
            'end_line': line_for('    handle.close()'),
            'original_code': '\n'.join(source_lines[
                line_for("    handle = open(path, 'r')") - 1:
                line_for('    handle.close()')
            ]),
            'explanation': 'A context manager closes the file if an error occurs.',
            'recommendation': 'Use a with statement.'
        }]
        corrected_code = '\n'.join([
            'import math',
            '',
            'def average(values):',
            '    return sum(values) / len(values) if values else 0.0',
            '',
            'def discount(price, percent):',
            '    if 0 <= percent <= 100:',
            '        return price * (1 - percent / 100)',
            '    return price',
            '',
            'def load_number(path):',
            "    with open(path, 'r') as handle:",
            '        raw = handle.read()',
            '    value = float(raw)',
            '    if not math.isfinite(value):',
            '        raise ValueError("number must be finite")',
            '    return value',
            '',
            'def ratio(numerator, denominator):',
            '    if denominator == 0:',
            '        raise ValueError("denominator must not be zero")',
            '    return numerator / denominator',
            '',
            'def add_tax(price, rate):',
            '    return price * (1 + rate / 100)',
            '',
            'def query_user(cursor, name):',
            '    cursor.execute("SELECT * FROM users WHERE name = ?", (name,))',
            '',
            'def parse_count(value):',
            '    try:',
            '        return int(value)',
            '    except (TypeError, ValueError):',
            '        return 0',
            '',
            'def malformed():',
            '    return None'
        ])

        with patch.object(
            checker,
            'call_gemini',
            return_value=(
                structured_response(source_code, findings, suggestions, corrected_code),
                None
            )
        ):
            response = self.client.post('/review', json={'code': source_code, 'language': 'Python'})

        self.assertEqual(response.status_code, 200)
        review = response.get_json()['review']
        self.assertEqual(len(review['issues']), 9)
        self.assertEqual(review['issues'][2]['start_line'], 11)
        self.assertEqual(review['issues'][2]['end_line'], 13)
        self.assertEqual(
            review['issues'][2]['original_code'],
            '\n'.join(source_lines[10:13])
        )
        self.assertEqual(review['issues'][6]['category'], 'Security')
        self.assertEqual(review['corrected_code'], corrected_code)
        self.assertIn('def average(values):', review['corrected_code'])
        self.assertIn('def malformed():', review['corrected_code'])
        self.assertEqual(review['overall_assessment']['total_issues'], 9)
        self.assertEqual(review['suggestions'][0]['start_line'], 11)
        self.assertEqual(review['suggestions'][0]['end_line'], 13)

    def test_partial_structured_response_keeps_valid_findings_and_reports_concerns(self):
        source_code = 'value = int(user_input)'
        valid_issue = {
            'id': 'ISSUE-001', 'title': 'Untrusted conversion',
            'severity': 'urgent', 'category': 'Input',
            'start_line': 1, 'end_line': 1, 'original_code': source_code,
            'explanation': 'Conversion may fail.', 'impact': 'The request may fail.',
            'suggested_fix': 'Validate before converting.', 'confidence': 1.4
        }
        duplicate_issue = dict(valid_issue, title='Duplicate source finding')
        response = checker.validate_review({
            'issues': [valid_issue, None, duplicate_issue],
            'corrected_code': source_code
        }, source_code)

        self.assertEqual(len(response['issues']), 2)
        self.assertEqual(
            len({issue['id'] for issue in response['issues']}),
            len(response['issues'])
        )
        self.assertEqual(response['issues'][0]['severity'], 'Medium')
        self.assertEqual(response['issues'][0]['category'], 'Other')
        self.assertEqual(response['issues'][0]['confidence'], 0.5)
        self.assertEqual(response['issues'][0]['start_line'], 1)
        self.assertGreaterEqual(len(response['remaining_concerns']), 4)

    def test_suggestion_location_is_cleared_when_source_excerpt_does_not_match(self):
        source_code = 'value = 1'
        response = checker.validate_review({
            'issues': [],
            'suggestions': [{
                'id': 'SUGGESTION-001', 'title': 'Simplify this statement',
                'category': 'Readability', 'start_line': 1, 'end_line': 1,
                'original_code': 'value = 2',
                'explanation': 'A suggestion.', 'recommendation': 'Simplify it.'
            }],
            'corrected_code': source_code,
            'changes_summary': [],
            'remaining_concerns': []
        }, source_code)
        self.assertIsNone(response['suggestions'][0]['start_line'])
        self.assertEqual(response['suggestions'][0]['original_code'], '')
        self.assertTrue(response['remaining_concerns'])

    def test_bad_locations_are_null_and_malformed_json_is_not_a_success(self):
        source_code = 'print("safe")'
        normalized = checker.validate_review({
            'issues': [{
                'id': 'ISSUE-001', 'title': 'Unmatched excerpt',
                'severity': 'Low', 'category': 'Code Quality',
                'start_line': 12, 'end_line': 12, 'original_code': 'wrong line',
                'explanation': 'A test finding.', 'impact': 'None.',
                'suggested_fix': 'Review manually.', 'confidence': 0.7
            }],
            'corrected_code': source_code,
            'suggestions': [],
            'changes_summary': [],
            'remaining_concerns': []
        }, source_code)
        self.assertIsNone(normalized['issues'][0]['start_line'])
        self.assertTrue(normalized['remaining_concerns'])

        with patch.object(checker, 'call_gemini', return_value=(
            '{"issues": [], "corrected_code":', None
        )):
            response = self.client.post('/review', json={'code': source_code})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('review', response.get_json())

    def test_call_gemini_requests_schema_constrained_json(self):
        api_response = MagicMock()
        api_response.__enter__.return_value.read.return_value = json.dumps({
            'candidates': [{'content': {'parts': [{'text': '{}'}]}}]
        }).encode('utf-8')
        with patch.object(checker.urllib.request, 'urlopen', return_value=api_response) as urlopen:
            result, error = checker.call_gemini('test-model', 'Python', [], structured=True)

        payload = json.loads(urlopen.call_args.args[0].data.decode('utf-8'))
        self.assertEqual(result, '{}')
        self.assertIsNone(error)
        self.assertEqual(payload['generationConfig']['responseMimeType'], 'application/json')
        self.assertIn('responseJsonSchema', payload['generationConfig'])

    def test_existing_database_gains_structured_review_column(self):
        database_path = os.path.join(self.temp_directory.name, 'legacy.sqlite3')
        db = sqlite3.connect(database_path)
        try:
            db.executescript('''
                CREATE TABLE conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    language TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'message',
                    content TEXT NOT NULL,
                    code TEXT,
                    severity_counts TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
            ''')
        finally:
            db.close()
        checker.app.config['DATABASE'] = database_path
        checker.init_db()
        db = sqlite3.connect(database_path)
        try:
            columns = {row[1] for row in db.execute('PRAGMA table_info(messages)')}
        finally:
            db.close()
        self.assertIn('review_data', columns)

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