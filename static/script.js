const input = document.getElementById('code-input');
const reviewBtn = document.getElementById('review-btn');
const loading = document.getElementById('loading');
const loadingText = document.getElementById('loading-text');
const results = document.getElementById('results');
const content = document.getElementById('review-content');
const countEl = document.getElementById('count');
const copyReviewBtn = document.getElementById('copy-review');

let lastMarkdown = '';
let statusTimer = null;

const EXAMPLE = `<?php
$id = $_GET['id'];
$conn = mysqli_connect("localhost", "root", "", "my_db");
$query = "SELECT * FROM users WHERE id = $id";
$result = mysqli_query($conn, $query);
$row = mysqli_fetch_assoc($result);
echo "Welcome " . $row['name'];
?>`;

const STATUS_MESSAGES = [
    'Reading your code...',
    'Looking for bugs and logic errors...',
    'Checking performance...',
    'Scanning for security risks...',
    'Writing a refactored version...'
];

const SECTION_TYPES = [
    { key: 'bugs',     re: /bugs|logic error/i },
    { key: 'perf',     re: /performance|optimi[sz]ation/i },
    { key: 'security', re: /security/i },
    { key: 'refactor', re: /refactor/i }
];

/* ---------- Input helpers ---------- */
function updateCount() {
    const text = input.value;
    const lines = text.length ? text.split('\n').length : 0;
    countEl.textContent = `${lines} ${lines === 1 ? 'line' : 'lines'}, ${text.length} characters`;
}
input.addEventListener('input', updateCount);

input.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        submitCode();
    }
});

document.getElementById('example-btn').addEventListener('click', () => {
    input.value = EXAMPLE;
    updateCount();
    input.focus();
});

document.getElementById('clear-btn').addEventListener('click', () => {
    input.value = '';
    updateCount();
    input.focus();
});

reviewBtn.addEventListener('click', submitCode);

copyReviewBtn.addEventListener('click', () => copyText(lastMarkdown, copyReviewBtn, 'Copy review'));

async function copyText(text, button, resetLabel) {
    try {
        await navigator.clipboard.writeText(text);
        button.textContent = 'Copied';
    } catch (err) {
        button.textContent = 'Copy failed';
    }
    setTimeout(() => { button.textContent = resetLabel; }, 1600);
}

/* ---------- Rendering ---------- */
function sectionTypeFor(node) {
    if (!/^(H[1-4]|P|OL|UL)$/.test(node.tagName)) return null;
    const text = node.textContent.trim();
    if (!text || text.length > 80) return null;
    const isHeading = /^H[1-4]$/.test(node.tagName);
    if (!isHeading && !node.querySelector('strong')) return null;
    if (node.querySelectorAll('li').length > 1) return null;
    const match = SECTION_TYPES.find((t) => t.re.test(text));
    return match ? { key: match.key, title: text.replace(/^\d+\.\s*/, '') } : null;
}

function makeSection(type) {
    const section = document.createElement('article');
    section.className = `review-section ${type.key}`;
    const title = document.createElement('h3');
    title.className = 'review-title';
    title.textContent = type.title;
    const body = document.createElement('div');
    body.className = 'review-body';
    section.append(title, body);
    return { section, body };
}

function addCopyButtons(root) {
    root.querySelectorAll('pre').forEach((pre) => {
        const wrap = document.createElement('div');
        wrap.className = 'code-wrap';
        pre.parentNode.insertBefore(wrap, pre);
        wrap.appendChild(pre);

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'copy-code';
        btn.textContent = 'Copy';
        btn.addEventListener('click', () => copyText(pre.innerText, btn, 'Copy'));
        wrap.appendChild(btn);
    });
}

function renderReview(markdown) {
    const html = DOMPurify.sanitize(marked.parse(markdown));
    const temp = document.createElement('div');
    temp.innerHTML = html;

    const out = document.createDocumentFragment();
    let current = null;
    let foundSection = false;

    Array.from(temp.children).forEach((node) => {
        const type = sectionTypeFor(node);
        if (type) {
            current = makeSection(type);
            out.appendChild(current.section);
            foundSection = true;
        } else if (current) {
            current.body.appendChild(node);
        } else {
            out.appendChild(node);
        }
    });

    content.replaceChildren(foundSection ? out : temp);
    addCopyButtons(content);
}

function showError(message) {
    const box = document.createElement('div');
    box.className = 'error';
    const label = document.createElement('strong');
    label.textContent = 'Error: ';
    box.append(label, document.createTextNode(message));
    content.replaceChildren(box);
    copyReviewBtn.hidden = true;
}

/* ---------- Loading state ---------- */
function startLoading() {
    reviewBtn.disabled = true;
    reviewBtn.textContent = 'Reviewing...';
    results.hidden = true;
    loading.hidden = false;
    let i = 0;
    loadingText.textContent = STATUS_MESSAGES[0];
    statusTimer = setInterval(() => {
        i = Math.min(i + 1, STATUS_MESSAGES.length - 1);
        loadingText.textContent = STATUS_MESSAGES[i];
    }, 3000);
}

function stopLoading() {
    clearInterval(statusTimer);
    reviewBtn.disabled = false;
    reviewBtn.textContent = 'Review code';
    loading.hidden = true;
}

/* ---------- Submit ---------- */
async function submitCode() {
    const code = input.value;
    if (!code.trim()) {
        input.focus();
        return;
    }

    startLoading();

    try {
        const response = await fetch('/review', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code: code })
        });

        let data = {};
        try { data = await response.json(); } catch (e) { /* non-JSON response */ }

        if (response.ok && data.analysis) {
            lastMarkdown = data.analysis;
            copyReviewBtn.hidden = false;
            renderReview(data.analysis);
        } else {
            showError(data.error || `The server returned status ${response.status}.`);
        }
    } catch (err) {
        showError('Could not reach the server. Check that app.py is still running.');
    } finally {
        stopLoading();
        results.hidden = false;
        results.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

updateCount();