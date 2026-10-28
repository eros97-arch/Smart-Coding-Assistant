import os
import json
import time
import traceback
import urllib.request
import urllib.error
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

SYSTEM_PROMPT = """
You are an expert Senior Code Reviewer specializing in multiple programming languages, including PHP, Python, JavaScript, C++, and C#. Analyze the provided code snippet carefully.

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


@app.route('/')
def index():
    return render_template('index.html')


def call_gemini(model, user_code):
    """Returns (analysis_text, None) on success or (None, error_description) on failure."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{
            "role": "user",
            "parts": [{"text": f"Here is the code to analyze:\n```\n{user_code}\n```"}]
        }]
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
                return res_data['candidates'][0]['content']['parts'][0]['text'], None

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

        except (KeyError, IndexError):
            print(f"[{model}] unexpected response format")
            return None, f"{model}: unexpected response"

    return None, f"{model}: failed"


@app.route('/review', methods=['POST'])
def review_code():
    try:
        if not API_KEY:
            return jsonify({"error": "GEMINI_API_KEY is not set. Set it in this terminal and restart the server."}), 500

        data = request.get_json()
        user_code = data.get('code', '')
        if not user_code.strip():
            return jsonify({"error": "No code provided"}), 400

        errors = []
        for model in MODELS:
            analysis, error = call_gemini(model, user_code)
            if analysis:
                return jsonify({"analysis": analysis})
            errors.append(error)
            print(f"Switching to next model after: {error}")

        return jsonify({"error": "Gemini is busy right now. Please try again in a minute. (" + "; ".join(errors) + ")"}), 503

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)