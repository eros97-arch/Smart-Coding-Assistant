import os
import json
import re
import sqlite3
import time
import traceback
import urllib.request
import urllib.error
from contextlib import contextmanager
from datetime import datetime, timezone
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
app.config['DATABASE'] = os.environ.get(
    'SMART_CODE_CHECKER_DB',
    os.path.join(app.instance_path, 'reviews.sqlite3')
)

LANGUAGES = [
    'Python', 'JavaScript', 'Java', 'PHP', 'C', 'C++', 'C#', 'SQL', 'HTML', 'CSS'
]
SEVERITIES = ('Critical', 'High', 'Medium', 'Low')
SEVERITY_PATTERN = re.compile(r'\[(Critical|High|Medium|Low)\]', re.IGNORECASE)

SYSTEM_PROMPT = """
You are an expert code reviewer helping student and beginner programmers. Review the
entire submitted program in one pass, including interactions between functions and
how data moves through the program. Inspect syntax and runtime errors, logic and
calculation errors, input validation, exception handling, security, persistence and
data handling, boundary cases, performance, and maintainability.

Find all distinct, genuine issues reasonably supported by the code. Do not stop after
the first few findings, duplicate the same underlying problem, or invent issues.
Separate bugs from optional suggestions. Preserve intended behavior: do not silently
change requirements that are not evident from the code. Explain issues clearly,
including their practical impact and a specific fix.

Line numbers are 1-based and must refer to the exact original submitted source. Map
each issue to the smallest accurate range containing its original_code. If you cannot
verify an exact source excerpt and location, set both line numbers to null and explain
the uncertainty in remaining_concerns. Do not estimate line numbers.

Return the complete corrected program in corrected_code, not isolated snippets.
Review your proposed corrected program for internal consistency and regressions.
If an issue cannot be confidently fixed without more context, preserve behavior,
describe the uncertainty, and add it to remaining_concerns. Return only JSON matching
the supplied response schema; do not wrap it in Markdown fences.
"""

FOLLOW_UP_PROMPT = """
You are a helpful code review assistant. Answer the user's follow-up using the review
conversation context, be clear and accurate, and do not claim changes were executed.
"""

ISSUE_CATEGORIES = (
    'Syntax', 'Runtime', 'Logic', 'Security', 'Validation', 'Exception Handling',
    'Data Handling', 'Persistence', 'Boundary Condition', 'Performance',
    'Code Quality', 'Maintainability', 'Other'
)
SUGGESTION_CATEGORIES = (
    'Performance', 'Readability', 'Maintainability', 'Best Practice', 'Other'
)

REVIEW_RESPONSE_SCHEMA = {
    'type': 'object',
    'properties': {
        'overall_assessment': {
            'type': 'object',
            'properties': {
                'summary': {'type': 'string'},
                'quality': {
                    'type': 'string',
                    'enum': ['Good', 'Fair', 'Poor']
                },
                'total_issues': {'type': 'integer'},
                'total_suggestions': {'type': 'integer'}
            },
            'required': ['summary', 'quality', 'total_issues', 'total_suggestions']
        },
        'issues': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'id': {'type': 'string'},
                    'title': {'type': 'string'},
                    'severity': {'type': 'string', 'enum': list(SEVERITIES)},
                    'category': {'type': 'string', 'enum': list(ISSUE_CATEGORIES)},
                    'start_line': {'anyOf': [{'type': 'integer'}, {'type': 'null'}]},
                    'end_line': {'anyOf': [{'type': 'integer'}, {'type': 'null'}]},
                    'original_code': {'type': 'string'},
                    'explanation': {'type': 'string'},
                    'impact': {'type': 'string'},
                    'suggested_fix': {'type': 'string'},
                    'confidence': {'type': 'number'}
                },
                'required': [
                    'id', 'title', 'severity', 'category', 'start_line',
                    'end_line', 'original_code', 'explanation', 'impact',
                    'suggested_fix', 'confidence'
                ]
            }
        },
        'suggestions': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'id': {'type': 'string'},
                    'title': {'type': 'string'},
                    'category': {
                        'type': 'string',
                        'enum': list(SUGGESTION_CATEGORIES)
                    },
                    'start_line': {'anyOf': [{'type': 'integer'}, {'type': 'null'}]},
                    'end_line': {'anyOf': [{'type': 'integer'}, {'type': 'null'}]},
                    'original_code': {'type': 'string'},
                    'explanation': {'type': 'string'},
                    'recommendation': {'type': 'string'}
                },
                'required': [
                    'id', 'title', 'category', 'start_line', 'end_line', 'original_code',
                    'explanation', 'recommendation'
                ]
            }
        },
        'corrected_code': {'type': 'string'},
        'changes_summary': {'type': 'array', 'items': {'type': 'string'}},
        'remaining_concerns': {'type': 'array', 'items': {'type': 'string'}}
    },
    'required': [
        'overall_assessment', 'issues', 'suggestions', 'corrected_code',
        'changes_summary', 'remaining_concerns'
    ]
}

# Key is read from an environment variable - never hard-code it in this file.
# PowerShell:  $env:GEMINI_API_KEY="your_key_here"
API_KEY = os.environ.get("GEMINI_API_KEY")
# Models are tried in order. If one is busy, slow, or retired, the next one is used.
# If Google renames models, the error in your terminal will tell you the new names.
MODELS = ["gemini-3.5-flash-lite", "gemini-3.8-flash"]

ATTEMPTS_PER_MODEL = 2   # tries per model before moving to the next one
TIMEOUT_SECONDS = 45     # per request; fail fast so we can switch models


@contextmanager
def db_connection():
    os.makedirs(os.path.dirname(app.config['DATABASE']) or '.', exist_ok=True)
    connection = sqlite3.connect(app.config['DATABASE'])
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys = ON')
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db():
    with db_connection() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                language TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user', 'model')),
                kind TEXT NOT NULL DEFAULT 'message',
                content TEXT NOT NULL,
                code TEXT,
                review_data TEXT,
                severity_counts TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS messages_conversation_idx
                ON messages(conversation_id, id);
            CREATE INDEX IF NOT EXISTS conversations_updated_idx
                ON conversations(updated_at DESC);
        ''')
        message_columns = {
            row['name'] for row in db.execute('PRAGMA table_info(messages)')
        }
        if 'review_data' not in message_columns:
            db.execute('ALTER TABLE messages ADD COLUMN review_data TEXT')


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def severity_counts(text):
    counts = {severity.lower(): 0 for severity in SEVERITIES}
    for match in SEVERITY_PATTERN.findall(text):
        counts[match.lower()] += 1
    return counts


def row_to_dict(row):
    return dict(row) if row is not None else None


@app.route('/')
def index():
    return render_template('index.html')


def call_gemini(model, language, contents, structured=False):
    """Returns (analysis_text, None) on success or (None, error_description) on failure."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    payload = {
        "system_instruction": {
            "parts": [{
                "text": (
                    f"{SYSTEM_PROMPT}\nThe selected programming language is {language}."
                    if structured else FOLLOW_UP_PROMPT
                )
            }]
        },
        "contents": contents,
        "generationConfig": {"temperature": 0.2}
    }
    if structured:
        payload['generationConfig'].update({
            'responseMimeType': 'application/json',
            'responseJsonSchema': REVIEW_RESPONSE_SCHEMA
        })

    for attempt in range(ATTEMPTS_PER_MODEL):
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'x-goog-api-key': API_KEY,
            },
            method='POST'
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                candidates = res_data.get('candidates') or []
                if not candidates:
                    feedback = res_data.get('promptFeedback') or {}
                    block_reason = (
                        feedback.get('blockReason')
                        or 'the API returned no candidates'
                    )
                    return None, f"{model}: no review generated ({block_reason})"

                candidate = candidates[0]
                if not isinstance(candidate, dict):
                    return None, f"{model}: invalid candidate in the API response"
                content = candidate.get('content') or {}
                parts = [] if not isinstance(content, dict) else content.get('parts') or []
                text_parts = [
                    part['text'].strip()
                    for part in parts
                    if isinstance(part, dict)
                    and isinstance(part.get('text'), str)
                    and part['text'].strip()
                    and not part.get('thought', False)
                ]
                if not text_parts:
                    finish_reason = candidate.get('finishReason')
                    detail = (
                        f"the response finished with {finish_reason}"
                        if finish_reason
                        else 'the API response contained no review text'
                    )
                    return None, f"{model}: no review generated ({detail})"

                return '\n\n'.join(text_parts), None

        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace')
            print(f"[{model}] HTTP {e.code}: {body}")
            if e.code in (429, 500, 503) and attempt < ATTEMPTS_PER_MODEL - 1:
                time.sleep(2 ** attempt)
                continue
            return None, f"{model}: HTTP {e.code}"

        except (TimeoutError, urllib.error.URLError) as e:
            print(f"[{model}] timed out or failed: {e}")
            return None, f"{model}: timeout"

        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"[{model}] invalid JSON response: {e}")
            return None, f"{model}: invalid response from the API"

    return None, f"{model}: failed"


def parse_review_json(response_text):
    """Parse JSON directly or recover a JSON object surrounded by explanatory text."""
    text = response_text.strip()
    fenced = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        result = None
    else:
        if isinstance(result, dict):
            return result
        raise ValueError('Gemini review must be a JSON object')

    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character == '{':
            try:
                result, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(result, dict):
                return result
    raise ValueError('Gemini did not return a valid JSON review object')


def _review_string(value, fallback=''):
    return value.strip() if isinstance(value, str) else fallback


def _canonical_choice(value, options):
    if not isinstance(value, str):
        return None
    return next((option for option in options if option.casefold() == value.strip().casefold()), None)


def _valid_line(value, line_count):
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 1 <= value <= line_count
    )


def validate_review(review_data, source_code):
    """Normalize partial findings while rejecting responses without a complete fix."""
    if not isinstance(review_data, dict):
        raise ValueError('Gemini review must be a JSON object')
    corrected_code = review_data.get('corrected_code')
    if not isinstance(corrected_code, str) or not corrected_code.strip():
        raise ValueError('Gemini review did not include complete corrected code')

    concerns = []

    def add_concern(message):
        if message not in concerns:
            concerns.append(message)

    assessment_data = review_data.get('overall_assessment')
    if not isinstance(assessment_data, dict):
        assessment_data = {}
        add_concern('The overall assessment was incomplete; a summary was generated from the findings.')
    quality = _canonical_choice(assessment_data.get('quality'), ('Good', 'Fair', 'Poor'))
    if quality is None:
        quality = 'Fair'
        add_concern('The overall quality rating was missing or invalid and is marked Fair pending review.')

    normalized_source = source_code.replace('\r\n', '\n').replace('\r', '\n')
    lines = normalized_source.splitlines()
    line_count = len(lines)

    def excerpt_in_source(snippet):
        normalized_snippet = snippet.replace('\r\n', '\n').replace('\r', '\n').strip()
        return bool(normalized_snippet) and normalized_snippet in normalized_source

    raw_issues = review_data.get('issues')
    if not isinstance(raw_issues, list):
        raw_issues = []
        add_concern('The issues list was missing or malformed.')
    issues = []
    seen_issue_ids = set()
    for index, item in enumerate(raw_issues, 1):
        if not isinstance(item, dict):
            add_concern(f'Finding {index} was malformed and could not be represented.')
            continue
        issue_id = _review_string(item.get('id'), f'ISSUE-{index:03d}')
        if issue_id in seen_issue_ids:
            base_id = f'ISSUE-{index:03d}'
            issue_id = base_id
            suffix = 2
            while issue_id in seen_issue_ids:
                issue_id = f'{base_id}-{suffix}'
                suffix += 1
            add_concern(f'A duplicate issue ID was replaced with {issue_id}.')
        seen_issue_ids.add(issue_id)
        severity = _canonical_choice(item.get('severity'), SEVERITIES)
        if severity is None:
            severity = 'Medium'
            add_concern(f'{issue_id} had an invalid severity and is marked Medium pending review.')
        category = _canonical_choice(item.get('category'), ISSUE_CATEGORIES)
        if category is None:
            category = 'Other'
            add_concern(f'{issue_id} had an unsupported category and is categorized as Other.')

        start = item.get('start_line')
        end = item.get('end_line')
        snippet = _review_string(item.get('original_code'))
        if start is None and end is None:
            pass
        elif (
            _valid_line(start, line_count)
            and _valid_line(end, line_count)
            and end >= start
            and snippet
        ):
            source_excerpt = '\n'.join(lines[start - 1:end])
            if snippet.replace('\r\n', '\n').strip() not in source_excerpt:
                start = end = None
                add_concern(f'{issue_id} could not be matched reliably to its claimed source lines.')
            else:
                snippet = source_excerpt
        else:
            start = end = None
            add_concern(f'{issue_id} had invalid or incomplete line numbers; its location is unknown.')
        if snippet and not excerpt_in_source(snippet):
            snippet = ''
            add_concern(f'{issue_id} included an excerpt that could not be matched to the submitted source.')

        confidence = item.get('confidence')
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            confidence = 0.5
            add_concern(f'{issue_id} had an invalid confidence value and is marked 0.5.')

        issues.append({
            'id': issue_id,
            'title': _review_string(item.get('title'), f'Finding {index}'),
            'severity': severity,
            'category': category,
            'start_line': start,
            'end_line': end,
            'original_code': snippet,
            'explanation': _review_string(item.get('explanation'), 'No explanation was provided.'),
            'impact': _review_string(item.get('impact'), 'The practical impact was not specified.'),
            'suggested_fix': _review_string(item.get('suggested_fix'), 'Review this finding manually.'),
            'confidence': confidence
        })

    raw_suggestions = review_data.get('suggestions')
    if not isinstance(raw_suggestions, list):
        raw_suggestions = []
        add_concern('The suggestions list was missing or malformed.')
    suggestions = []
    seen_suggestion_ids = set()
    for index, item in enumerate(raw_suggestions, 1):
        if not isinstance(item, dict):
            add_concern(f'Suggestion {index} was malformed and could not be represented.')
            continue
        suggestion_id = _review_string(item.get('id'), f'SUGGESTION-{index:03d}')
        if suggestion_id in seen_suggestion_ids:
            base_id = f'SUGGESTION-{index:03d}'
            suggestion_id = base_id
            suffix = 2
            while suggestion_id in seen_suggestion_ids:
                suggestion_id = f'{base_id}-{suffix}'
                suffix += 1
            add_concern(f'A duplicate suggestion ID was replaced with {suggestion_id}.')
        seen_suggestion_ids.add(suggestion_id)
        category = _canonical_choice(item.get('category'), SUGGESTION_CATEGORIES)
        if category is None:
            category = 'Other'
            add_concern(f'{suggestion_id} had an unsupported category and is categorized as Other.')
        start = item.get('start_line')
        end = item.get('end_line')
        snippet = _review_string(item.get('original_code'))
        if start is None and end is None:
            if snippet and not excerpt_in_source(snippet):
                snippet = ''
                add_concern(f'{suggestion_id} could not be matched reliably to the submitted source.')
        elif (
            _valid_line(start, line_count)
            and _valid_line(end, line_count)
            and end >= start
            and snippet
        ):
            source_excerpt = '\n'.join(lines[start - 1:end])
            if snippet.replace('\r\n', '\n').replace('\r', '\n').strip() in source_excerpt:
                snippet = source_excerpt
            else:
                start = end = None
                snippet = ''
                add_concern(f'{suggestion_id} could not be matched reliably to its claimed source lines.')
        else:
            start = end = None
            snippet = ''
            add_concern(f'{suggestion_id} had invalid or incomplete line numbers; its location is unknown.')
        suggestions.append({
            'id': suggestion_id,
            'title': _review_string(item.get('title'), f'Suggestion {index}'),
            'category': category,
            'start_line': start,
            'end_line': end,
            'original_code': snippet,
            'explanation': _review_string(item.get('explanation'), 'No explanation was provided.'),
            'recommendation': _review_string(item.get('recommendation'), 'Review this suggestion manually.')
        })

    raw_concerns = review_data.get('remaining_concerns')
    if isinstance(raw_concerns, list):
        for concern in raw_concerns:
            if isinstance(concern, str) and concern.strip():
                add_concern(concern.strip())
            else:
                add_concern('A remaining concern was malformed and requires manual review.')
    else:
        add_concern('The remaining concerns list was missing or malformed.')

    raw_changes = review_data.get('changes_summary')
    changes = []
    if isinstance(raw_changes, list):
        changes = [change.strip() for change in raw_changes if isinstance(change, str) and change.strip()]
        if len(changes) != len(raw_changes):
            add_concern('One or more change summary entries were malformed.')
    else:
        add_concern('The changes summary was missing or malformed.')

    summary = _review_string(assessment_data.get('summary'))
    if not summary:
        summary = f'The review identified {len(issues)} issue(s) and {len(suggestions)} optional suggestion(s).'
        add_concern('The overall assessment summary was missing; one was generated from the retained findings.')

    return {
        'overall_assessment': {
            'summary': summary,
            'quality': quality,
            'total_issues': len(issues),
            'total_suggestions': len(suggestions)
        },
        'issues': issues,
        'suggestions': suggestions,
        'corrected_code': corrected_code,
        'changes_summary': changes,
        'remaining_concerns': concerns
    }


def review_as_text(review):
    """Create readable legacy message content for search and follow-up context."""
    assessment = review['overall_assessment']
    sections = [
        f"Overall assessment ({assessment['quality']}): {assessment['summary']}"
    ]
    for issue in review['issues']:
        location = (
            f"lines {issue['start_line']}-{issue['end_line']}"
            if issue['start_line'] is not None else 'location unknown'
        )
        sections.append(
            f"[{issue['severity']}] {issue['title']} ({issue['id']}, {location})\n"
            f"{issue['explanation']}\nImpact: {issue['impact']}\n"
            f"Suggested fix:\n{issue['suggested_fix']}"
        )
    for suggestion in review['suggestions']:
        sections.append(
            f"[Low] Suggestion: {suggestion['title']} ({suggestion['id']})\n"
            f"{suggestion['explanation']}\nRecommendation: {suggestion['recommendation']}"
        )
    if review['changes_summary']:
        sections.append('Changes summary:\n' + '\n'.join(f'- {item}' for item in review['changes_summary']))
    if review['remaining_concerns']:
        sections.append('Remaining concerns:\n' + '\n'.join(f'- {item}' for item in review['remaining_concerns']))
    sections.append(f"Complete corrected code:\n```\n{review['corrected_code']}\n```")
    return '\n\n'.join(sections)


@app.route('/review', methods=['POST'])
def review_code():
    try:
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"error": "A JSON object containing code is required"}), 400
        user_code = data.get('code', '')
        language = data.get('language', 'Python')
        if not isinstance(user_code, str) or not user_code.strip():
            return jsonify({"error": "No code provided"}), 400
        if not isinstance(language, str) or language not in LANGUAGES:
            return jsonify({"error": "Unsupported programming language"}), 400
        if not API_KEY:
            return jsonify({"error": "GEMINI_API_KEY is not set. Set it in this terminal and restart the server."}), 500

        timestamp = now_utc()
        with db_connection() as db:
            cursor = db.execute(
                'INSERT INTO conversations (title, language, created_at, updated_at) VALUES (?, ?, ?, ?)',
                (f'{language} review', language, timestamp, timestamp)
            )
            conversation_id = cursor.lastrowid
            db.execute(
                '''INSERT INTO messages (conversation_id, role, kind, content, code, created_at)
                   VALUES (?, 'user', 'review', ?, ?, ?)''',
                (conversation_id, user_code, user_code, timestamp)
            )

        contents = [{
            'role': 'user',
            'parts': [{
                'text': (
                    f'Programming language: {language}\n'
                    'Review the complete original source below. Its first line is line 1; '
                    'count every blank line and never report line numbers from modified code.\n'
                    f'```{language.lower()}\n{user_code}\n```'
                )
            }]
        }]

        errors = []
        for model in MODELS:
            response_text, error = call_gemini(model, language, contents, structured=True)
            if response_text:
                try:
                    structured_review = validate_review(parse_review_json(response_text), user_code)
                except ValueError:
                    errors.append(f'{model}: invalid structured review')
                    continue
                analysis = review_as_text(structured_review)
                counts = {severity.lower(): 0 for severity in SEVERITIES}
                for issue in structured_review['issues']:
                    counts[issue['severity'].lower()] += 1
                with db_connection() as db:
                    db.execute(
                        '''INSERT INTO messages
                           (conversation_id, role, kind, content, review_data, severity_counts, created_at)
                           VALUES (?, 'model', 'review', ?, ?, ?, ?)''',
                        (
                            conversation_id,
                            analysis,
                            json.dumps(structured_review),
                            json.dumps(counts),
                            now_utc()
                        )
                    )
                    db.execute('UPDATE conversations SET updated_at = ? WHERE id = ?',
                               (now_utc(), conversation_id))
                return jsonify({
                    'analysis': analysis,
                    'review': structured_review,
                    'conversation_id': conversation_id,
                    'severity_counts': counts
                })
            errors.append(error or f'{model}: no response')
            print(f"Switching to next model after: {error}")

        return jsonify({
            "error": (
                'Gemini could not return a complete, valid structured review. '
                'Please try again; your submitted code is unchanged.'
            ),
            "conversation_id": conversation_id
        }), 502

    except Exception:
        traceback.print_exc()
        return jsonify({"error": "The review could not be completed due to a server error."}), 500


@app.route('/api/dashboard')
def dashboard_data():
    with db_connection() as db:
        reviews = db.execute(
            "SELECT COUNT(*) FROM messages WHERE role = 'user' AND kind = 'review' AND code IS NOT NULL"
        ).fetchone()[0]
        severity_totals = {severity.lower(): 0 for severity in SEVERITIES}
        severity_rows = db.execute(
            "SELECT severity_counts FROM messages WHERE role = 'model' AND kind = 'review'"
        ).fetchall()
        for row in severity_rows:
            try:
                saved_counts = json.loads(row['severity_counts'])
            except (TypeError, json.JSONDecodeError):
                continue
            for severity in severity_totals:
                severity_totals[severity] += int(saved_counts.get(severity, 0))

        language_rows = db.execute(
            '''SELECT language, COUNT(*) AS reviews FROM conversations
               GROUP BY language ORDER BY reviews DESC, language'''
        ).fetchall()
        recent_rows = db.execute(
            '''SELECT c.id, c.title, c.language, c.updated_at,
                      (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count
               FROM conversations c ORDER BY c.updated_at DESC LIMIT 6'''
        ).fetchall()

    return jsonify({
        'total_reviews': reviews,
        'total_issues': sum(severity_totals.values()),
        'languages_used': len(language_rows),
        'language_usage': [row_to_dict(row) for row in language_rows],
        'supported_languages': LANGUAGES,
        'severity_totals': severity_totals,
        'recent_activity': [row_to_dict(row) for row in recent_rows]
    })


@app.route('/api/conversations')
def list_conversations():
    search = request.args.get('q', '').strip()
    with db_connection() as db:
        if search:
            pattern = f'%{search}%'
            rows = db.execute(
                '''SELECT c.id, c.title, c.language, c.created_at, c.updated_at
                   FROM conversations c
                   WHERE c.title LIKE ? OR EXISTS (
                       SELECT 1 FROM messages m
                       WHERE m.conversation_id = c.id AND m.content LIKE ?
                   )
                   ORDER BY c.updated_at DESC''',
                (pattern, pattern)
            ).fetchall()
        else:
            rows = db.execute(
                '''SELECT id, title, language, created_at, updated_at
                   FROM conversations ORDER BY updated_at DESC'''
            ).fetchall()
    return jsonify([row_to_dict(row) for row in rows])


@app.route('/api/conversations/<int:conversation_id>')
def get_conversation(conversation_id):
    with db_connection() as db:
        conversation = db.execute(
            'SELECT id, title, language, created_at, updated_at FROM conversations WHERE id = ?',
            (conversation_id,)
        ).fetchone()
        if conversation is None:
            return jsonify({'error': 'Conversation not found'}), 404
        messages = db.execute(
            '''SELECT id, role, kind, content, code, review_data, severity_counts, created_at
               FROM messages WHERE conversation_id = ? ORDER BY id''',
            (conversation_id,)
        ).fetchall()
    result_messages = []
    for row in messages:
        message = row_to_dict(row)
        message['severity_counts'] = json.loads(message['severity_counts'] or '{}')
        review_json = message.pop('review_data', None)
        message['review'] = json.loads(review_json) if review_json else None
        result_messages.append(message)
    return jsonify({'conversation': row_to_dict(conversation), 'messages': result_messages})


@app.route('/api/conversations/<int:conversation_id>', methods=['PATCH'])
def rename_conversation(conversation_id):
    data = request.get_json(silent=True) or {}
    title = data.get('title', '').strip()
    if not title:
        return jsonify({'error': 'A conversation title is required'}), 400
    with db_connection() as db:
        cursor = db.execute(
            'UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?',
            (title[:100], now_utc(), conversation_id)
        )
        if cursor.rowcount == 0:
            return jsonify({'error': 'Conversation not found'}), 404
    return jsonify({'id': conversation_id, 'title': title[:100]})


@app.route('/api/conversations/<int:conversation_id>', methods=['DELETE'])
def delete_conversation(conversation_id):
    with db_connection() as db:
        cursor = db.execute('DELETE FROM conversations WHERE id = ?', (conversation_id,))
        if cursor.rowcount == 0:
            return jsonify({'error': 'Conversation not found'}), 404
    return jsonify({'deleted': True})


@app.route('/api/conversations/<int:conversation_id>/messages', methods=['POST'])
def send_follow_up(conversation_id):
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'A JSON object containing a message is required'}), 400
    message = data.get('message', '')
    message_text = message.strip() if isinstance(message, str) else ''
    if not message_text:
        return jsonify({'error': 'A follow-up question is required'}), 400
    if not API_KEY:
        return jsonify({'error': 'GEMINI_API_KEY is not set. Set it in this terminal and restart the server.'}), 500

    with db_connection() as db:
        conversation = db.execute(
            'SELECT id, language FROM conversations WHERE id = ?', (conversation_id,)
        ).fetchone()
        if conversation is None:
            return jsonify({'error': 'Conversation not found'}), 404
        timestamp = now_utc()
        db.execute(
            '''INSERT INTO messages (conversation_id, role, content, created_at)
               VALUES (?, 'user', ?, ?)''',
            (conversation_id, message_text, timestamp)
        )
        rows = db.execute(
            'SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id',
            (conversation_id,)
        ).fetchall()
        db.execute('UPDATE conversations SET updated_at = ? WHERE id = ?',
                   (timestamp, conversation_id))

    contents = [
        {'role': row['role'], 'parts': [{'text': row['content']}]} for row in rows
    ]
    errors = []
    for model in MODELS:
        response_text, error = call_gemini(model, conversation['language'], contents)
        if response_text:
            counts = severity_counts(response_text)
            with db_connection() as db:
                db.execute(
                    '''INSERT INTO messages
                       (conversation_id, role, content, severity_counts, created_at)
                       VALUES (?, 'model', ?, ?, ?)''',
                    (conversation_id, response_text, json.dumps(counts), now_utc())
                )
                db.execute('UPDATE conversations SET updated_at = ? WHERE id = ?',
                           (now_utc(), conversation_id))
            return jsonify({'message': response_text, 'severity_counts': counts})
        errors.append(error)

    return jsonify({'error': 'Could not generate a response. ' + '; '.join(errors)}), 503


init_db()

if __name__ == '__main__':
    app.run(debug=True, port=5000)