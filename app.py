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
You are an expert senior code reviewer. Analyze code according to the programming language specified by the user. For every distinct issue you report, begin the finding with exactly one severity label: [Critical], [High], [Medium], or [Low]. Do not put these labels in code blocks or repeat them in the severity summary.

Provide your review structured under these exact Markdown headers:
1. 🐞 **Bugs & Logic Errors** (Are there syntax mistakes, memory leaks, or runtime bugs?)
2. ⚡ **Performance & Optimization** (Can it run faster or use fewer resources?)
3. 🔒 **Security Risks** (Check for memory safety, SQL injection, XSS, or missing input sanitization)
4. 💡 **Refactored Code** (Provide clean, secure, modern code blocks if applicable)
"""

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
                severity_counts TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS messages_conversation_idx
                ON messages(conversation_id, id);
            CREATE INDEX IF NOT EXISTS conversations_updated_idx
                ON conversations(updated_at DESC);
        ''')


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


def call_gemini(model, language, contents):
    """Returns (analysis_text, None) on success or (None, error_description) on failure."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    payload = {
        "system_instruction": {"parts": [{"text": f"{SYSTEM_PROMPT}\nThe selected programming language for this conversation is {language}."}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.2}
    }

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


@app.route('/review', methods=['POST'])
def review_code():
    try:
        data = request.get_json(silent=True) or {}
        user_code = data.get('code', '')
        language = data.get('language', 'Python')
        if not user_code.strip():
            return jsonify({"error": "No code provided"}), 400
        if language not in LANGUAGES:
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
            'parts': [{'text': f'Programming language: {language}\nReview this code carefully:\n```{language.lower()}\n{user_code}\n```'}]
        }]

        errors = []
        for model in MODELS:
            analysis, error = call_gemini(model, language, contents)
            if analysis:
                counts = severity_counts(analysis)
                with db_connection() as db:
                    db.execute(
                        '''INSERT INTO messages
                           (conversation_id, role, kind, content, severity_counts, created_at)
                           VALUES (?, 'model', 'review', ?, ?, ?)''',
                        (conversation_id, analysis, json.dumps(counts), now_utc())
                    )
                    db.execute('UPDATE conversations SET updated_at = ? WHERE id = ?',
                               (now_utc(), conversation_id))
                return jsonify({
                    'analysis': analysis,
                    'conversation_id': conversation_id,
                    'severity_counts': counts
                })
            errors.append(error)
            print(f"Switching to next model after: {error}")

        return jsonify({
            "error": "Could not generate a review. " + "; ".join(errors),
            "conversation_id": conversation_id
        }), 503

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


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
            '''SELECT id, role, kind, content, code, severity_counts, created_at
               FROM messages WHERE conversation_id = ? ORDER BY id''',
            (conversation_id,)
        ).fetchall()
    result_messages = []
    for row in messages:
        message = row_to_dict(row)
        message['severity_counts'] = json.loads(message['severity_counts'] or '{}')
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
    message_text = data.get('message', '').strip()
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