const input = document.getElementById('code-input');
const reviewBtn = document.getElementById('review-btn');
const loading = document.getElementById('loading');
const loadingText = document.getElementById('loading-text');
const results = document.getElementById('results');
const content = document.getElementById('review-content');
const countEl = document.getElementById('count');
const copyReviewBtn = document.getElementById('copy-review');
const appShell = document.getElementById('app-shell');
const sidebar = document.getElementById('sidebar');
const sidebarToggle = document.getElementById('sidebar-toggle');
const sidebarBackdrop = document.getElementById('sidebar-backdrop');
const dashboardNav = document.getElementById('dashboard-nav');
const workspaceNav = document.getElementById('workspace-nav');
const conversationList = document.getElementById('conversation-list');
const historySearch = document.getElementById('history-search');
const dashboardView = document.getElementById('dashboard-view');
const editorView = document.getElementById('editor-view');
const conversationView = document.getElementById('conversation-view');
const languageSelect = document.getElementById('language-select');
const transcript = document.getElementById('transcript');
const followupInput = document.getElementById('followup-input');
const dialogLayer = document.getElementById('dialog-layer');
const dialogForm = document.getElementById('dialog-form');
const dialogTitle = document.getElementById('dialog-title');
const dialogMessage = document.getElementById('dialog-message');
const dialogLabel = document.getElementById('dialog-label');
const dialogInput = document.getElementById('dialog-input');
const dialogConfirm = document.getElementById('dialog-confirm');

let lastMarkdown = '';
let statusTimer = null;
let currentConversationId = null;
let searchTimer = null;
let dialogResolver = null;
let dialogMode = 'rename';

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

function renderReview(markdown, target = content) {
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

    target.replaceChildren(foundSection ? out : temp);
    if (!target.textContent.trim()) {
        const fallback = document.createElement('pre');
        fallback.className = 'review-raw';
        fallback.textContent = markdown;
        target.replaceChildren(fallback);
    }
    addCopyButtons(target);
}

function appendReviewText(root, label, value) {
    if (!value) return;
    const block = document.createElement('div');
    block.className = 'structured-review-text';
    const heading = document.createElement('strong');
    heading.textContent = label;
    const text = document.createElement('p');
    text.textContent = value;
    block.append(heading, text);
    root.appendChild(block);
}

function appendReviewCode(root, label, value) {
    if (!value) return;
    const block = document.createElement('div');
    block.className = 'structured-review-code';
    const heading = document.createElement('strong');
    heading.textContent = label;
    const pre = document.createElement('pre');
    const code = document.createElement('code');
    code.textContent = value;
    pre.appendChild(code);
    block.append(heading, pre);
    root.appendChild(block);
}

function renderStructuredReview(review, target = content) {
    const root = document.createElement('div');
    root.className = 'structured-review';

    const assessment = document.createElement('section');
    assessment.className = 'structured-review-panel';
    const assessmentHeading = document.createElement('h3');
    assessmentHeading.textContent = `Overall assessment · ${review.overall_assessment.quality}`;
    const assessmentSummary = document.createElement('p');
    assessmentSummary.textContent = review.overall_assessment.summary;
    assessment.append(assessmentHeading, assessmentSummary);
    root.appendChild(assessment);

    const issuesHeading = document.createElement('h3');
    issuesHeading.textContent = `Issues (${review.issues.length})`;
    root.appendChild(issuesHeading);
    if (!review.issues.length) {
        const emptyIssues = document.createElement('p');
        emptyIssues.textContent = 'No issues were identified.';
        root.appendChild(emptyIssues);
    }
    review.issues.forEach((issue) => {
        const card = document.createElement('article');
        card.className = `structured-issue severity-${issue.severity.toLowerCase()}`;
        card.dataset.issueId = issue.id;
        const heading = document.createElement('h4');
        heading.textContent = `${issue.id} · ${issue.title}`;
        const meta = document.createElement('p');
        meta.className = 'structured-review-meta';
        meta.textContent = `${issue.severity} · ${issue.category} · ${
            issue.start_line === null
                ? 'Location unknown'
                : `Lines ${issue.start_line}-${issue.end_line}`
        } · Confidence ${Math.round(issue.confidence * 100)}%`;
        card.append(heading, meta);
        appendReviewText(card, 'Explanation', issue.explanation);
        appendReviewText(card, 'Impact', issue.impact);
        appendReviewCode(card, 'Original code', issue.original_code);
        appendReviewCode(card, 'Suggested fix', issue.suggested_fix);
        root.appendChild(card);
    });

    if (review.suggestions.length) {
        const suggestionsHeading = document.createElement('h3');
        suggestionsHeading.textContent = `Suggestions (${review.suggestions.length})`;
        root.appendChild(suggestionsHeading);
        review.suggestions.forEach((suggestion) => {
            const card = document.createElement('article');
            card.className = 'structured-review-panel';
            const heading = document.createElement('h4');
            heading.textContent = `${suggestion.id} · ${suggestion.title}`;
            const meta = document.createElement('p');
            meta.className = 'structured-review-meta';
            meta.textContent = `${suggestion.category} · ${
                suggestion.start_line === null
                    ? 'Location unspecified'
                    : `Lines ${suggestion.start_line}-${suggestion.end_line}`
            }`;
            card.append(heading, meta);
            appendReviewText(card, 'Explanation', suggestion.explanation);
            appendReviewText(card, 'Recommendation', suggestion.recommendation);
            appendReviewCode(card, 'Original code', suggestion.original_code);
            root.appendChild(card);
        });
    }

    if (review.changes_summary.length) {
        const changes = document.createElement('section');
        changes.className = 'structured-review-panel';
        const heading = document.createElement('h3');
        heading.textContent = 'Changes summary';
        const list = document.createElement('ul');
        review.changes_summary.forEach((change) => {
            const item = document.createElement('li');
            item.textContent = change;
            list.appendChild(item);
        });
        changes.append(heading, list);
        root.appendChild(changes);
    }

    if (review.remaining_concerns.length) {
        const concerns = document.createElement('section');
        concerns.className = 'structured-review-panel';
        const heading = document.createElement('h3');
        heading.textContent = 'Remaining concerns';
        const list = document.createElement('ul');
        review.remaining_concerns.forEach((concern) => {
            const item = document.createElement('li');
            item.textContent = concern;
            list.appendChild(item);
        });
        concerns.append(heading, list);
        root.appendChild(concerns);
    }

    appendReviewCode(root, 'Complete corrected code', review.corrected_code);
    target.replaceChildren(root);
    addCopyButtons(target);
}

function showError(message) {
    lastMarkdown = '';
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
    content.replaceChildren();
    copyReviewBtn.hidden = true;
    lastMarkdown = '';
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

function showView(view) {
    [dashboardView, editorView, conversationView].forEach((section) => {
        section.hidden = section !== view;
    });
    const showingDashboard = view === dashboardView;
    dashboardNav.setAttribute('aria-current', showingDashboard ? 'page' : 'false');
    workspaceNav.setAttribute('aria-current', showingDashboard ? 'false' : 'page');
    appShell.classList.remove('sidebar-open');
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

async function requestJson(url, options = {}) {
    const response = await fetch(url, options);
    const text = await response.text();
    let data;
    try {
        data = text ? JSON.parse(text) : {};
    } catch (err) {
        throw new Error(`The server returned an unreadable response (HTTP ${response.status}).`);
    }
    if (!response.ok) throw new Error(data.error || `Request failed (HTTP ${response.status}).`);
    return data;
}

function formatDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(date);
}

function makeHistoryButton(conversation) {
    const item = document.createElement('div');
    item.className = 'history-item';
    const open = document.createElement('button');
    open.type = 'button';
    open.className = 'history-open';
    const title = document.createElement('span');
    title.className = 'history-title';
    title.textContent = conversation.title;
    const meta = document.createElement('span');
    meta.className = 'history-meta';
    meta.textContent = `${conversation.language} | ${formatDate(conversation.updated_at)}`;
    open.append(title, meta);
    open.addEventListener('click', () => openConversation(conversation.id));

    const actions = document.createElement('div');
    actions.className = 'history-actions';
    const rename = document.createElement('button');
    rename.type = 'button';
    rename.textContent = 'Rename';
    rename.setAttribute('aria-label', `Rename ${conversation.title}`);
    rename.addEventListener('click', () => renameConversation(conversation));
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = 'Delete';
    remove.setAttribute('aria-label', `Delete ${conversation.title}`);
    remove.addEventListener('click', () => deleteConversation(conversation));
    actions.append(rename, remove);
    item.append(open, actions);
    return item;
}

async function loadHistory(query = '') {
    const conversations = await requestJson(`/api/conversations?q=${encodeURIComponent(query)}`);
    conversationList.replaceChildren();
    if (!conversations.length) {
        const empty = document.createElement('p');
        empty.className = 'empty-history';
        empty.textContent = query ? 'No matching reviews.' : 'Your saved reviews will appear here.';
        conversationList.appendChild(empty);
        return;
    }
    conversations.forEach((conversation) => conversationList.appendChild(makeHistoryButton(conversation)));
}

async function loadDashboard() {
    try {
        const data = await requestJson('/api/dashboard');
        document.getElementById('stat-reviews').textContent = data.total_reviews;
        document.getElementById('stat-issues').textContent = data.total_issues;
        document.getElementById('stat-languages').textContent = data.languages_used;

        const severityRoot = document.getElementById('severity-stats');
        severityRoot.replaceChildren();
        const maxSeverity = Math.max(1, ...Object.values(data.severity_totals));
        Object.entries(data.severity_totals).forEach(([severity, amount]) => {
            const row = document.createElement('div');
            row.className = `severity-row ${severity}`;
            const label = document.createElement('span');
            label.textContent = severity[0].toUpperCase() + severity.slice(1);
            const track = document.createElement('span');
            track.className = 'severity-track';
            const bar = document.createElement('span');
            bar.className = 'severity-bar';
            bar.style.width = `${(amount / maxSeverity) * 100}%`;
            track.appendChild(bar);
            const count = document.createElement('strong');
            count.textContent = amount;
            row.append(label, track, count);
            severityRoot.appendChild(row);
        });

        const languageRoot = document.getElementById('language-usage');
        languageRoot.replaceChildren();
        if (data.language_usage.length) {
            data.language_usage.forEach((item) => {
                const row = document.createElement('div');
                row.className = 'language-row';
                const name = document.createElement('span');
                name.textContent = item.language;
                const total = document.createElement('strong');
                total.textContent = `${item.reviews} ${item.reviews === 1 ? 'review' : 'reviews'}`;
                row.append(name, total);
                languageRoot.appendChild(row);
            });
        } else {
            const empty = document.createElement('p');
            empty.className = 'empty-state';
            empty.textContent = 'Languages used will appear here.';
            languageRoot.appendChild(empty);
        }
        document.getElementById('supported-languages').textContent = data.supported_languages.join(' · ');

        const recentRoot = document.getElementById('recent-activity');
        recentRoot.replaceChildren();
        if (data.recent_activity.length) {
            data.recent_activity.forEach((activity) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'recent-item';
                const name = document.createElement('span');
                name.className = 'recent-title';
                name.textContent = activity.title;
                const meta = document.createElement('span');
                meta.className = 'recent-meta';
                meta.textContent = `${activity.language} | ${formatDate(activity.updated_at)} | ${activity.message_count} messages`;
                button.append(name, meta);
                button.addEventListener('click', () => openConversation(activity.id));
                recentRoot.appendChild(button);
            });
        } else {
            const empty = document.createElement('p');
            empty.className = 'empty-state';
            empty.textContent = 'No reviews yet. Start your first code review to see activity here.';
            recentRoot.appendChild(empty);
        }
    } catch (err) {
        const recentRoot = document.getElementById('recent-activity');
        recentRoot.textContent = err.message;
    }
}

async function renameConversation(conversation) {
    const title = await showDialog({
        mode: 'rename',
        title: 'Rename review',
        value: conversation.title,
        confirmLabel: 'Save'
    });
    if (title === null || !title.trim()) return;
    try {
        await requestJson(`/api/conversations/${conversation.id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: title.trim() })
        });
        await loadHistory(historySearch.value);
        await loadDashboard();
        if (currentConversationId === conversation.id) {
            document.getElementById('conversation-title').textContent = title.trim();
        }
    } catch (err) {
        window.alert(err.message);
    }
}

async function deleteConversation(conversation) {
    const confirmed = await showDialog({
        mode: 'confirm',
        title: 'Delete review?',
        message: `Delete "${conversation.title}" and its messages? This cannot be undone.`,
        confirmLabel: 'Delete'
    });
    if (!confirmed) return;
    try {
        const deletingCurrent = currentConversationId === conversation.id;
        await requestJson(`/api/conversations/${conversation.id}`, { method: 'DELETE' });
        if (deletingCurrent) currentConversationId = null;
        await loadHistory(historySearch.value);
        await loadDashboard();
        if (deletingCurrent) showView(dashboardView);
    } catch (err) {
        window.alert(err.message);
    }
}

function showDialog(options) {
    dialogMode = options.mode;
    dialogTitle.textContent = options.title;
    dialogMessage.textContent = options.message || '';
    dialogMessage.hidden = options.mode !== 'confirm';
    dialogLabel.hidden = options.mode !== 'rename';
    dialogInput.hidden = options.mode !== 'rename';
    dialogInput.disabled = options.mode !== 'rename';
    dialogInput.required = options.mode === 'rename';
    dialogInput.value = options.value || '';
    dialogConfirm.textContent = options.confirmLabel;
    dialogConfirm.classList.toggle('danger-action', options.mode === 'confirm');
    dialogLayer.hidden = false;
    return new Promise((resolve) => {
        dialogResolver = resolve;
        (options.mode === 'rename' ? dialogInput : dialogConfirm).focus();
    });
}

function closeDialog(result) {
    dialogLayer.hidden = true;
    if (dialogResolver) dialogResolver(result);
    dialogResolver = null;
}

dialogForm.addEventListener('submit', (event) => {
    event.preventDefault();
    closeDialog(dialogMode === 'rename' ? dialogInput.value.trim() : true);
});
document.getElementById('dialog-cancel').addEventListener('click', () => closeDialog(null));
dialogLayer.addEventListener('click', (event) => {
    if (event.target === dialogLayer) closeDialog(null);
});
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !dialogLayer.hidden) closeDialog(null);
});

function appendConversationMessage(message) {
    const article = document.createElement('article');
    article.className = `chat-message ${message.role === 'model' ? 'assistant-message' : 'user-message'}`;
    const heading = document.createElement('div');
    heading.className = 'message-heading';
    heading.textContent = message.role === 'model' ? 'Smart Code Checker' : 'You';
    const body = document.createElement('div');
    body.className = 'message-body';
    if (message.role === 'model') {
        if (message.review) {
            renderStructuredReview(message.review, body);
        } else {
            renderReview(message.content, body);
        }
    } else if (message.kind === 'review' && message.code) {
        const details = document.createElement('details');
        details.className = 'submitted-code';
        const label = document.createElement('summary');
        label.textContent = `Original submitted code (${message.code.split('\n').length} lines, ${message.code.length} characters)`;
        const code = document.createElement('pre');
        const codeText = document.createElement('code');
        codeText.textContent = message.code;
        code.appendChild(codeText);
        details.append(label, code);
        body.appendChild(details);
    } else {
        body.textContent = message.content;
    }
    article.append(heading, body);
    transcript.appendChild(article);
}

async function openConversation(id) {
    try {
        const data = await requestJson(`/api/conversations/${id}`);
        currentConversationId = id;
        document.getElementById('conversation-title').textContent = data.conversation.title;
        document.getElementById('conversation-language').textContent = `${data.conversation.language} review`;
        transcript.replaceChildren();
        data.messages.forEach(appendConversationMessage);
        showView(conversationView);
        transcript.scrollTop = transcript.scrollHeight;
        followupInput.focus();
    } catch (err) {
        window.alert(err.message);
    }
}

function startNewReview() {
    currentConversationId = null;
    input.value = '';
    languageSelect.value = 'Python';
    updateCount();
    results.hidden = true;
    content.replaceChildren();
    copyReviewBtn.hidden = true;
    showView(editorView);
    input.focus();
}

/* ---------- Submit ---------- */
async function submitCode() {
    const code = input.value;
    if (!code.trim()) {
        input.focus();
        return;
    }

    startLoading();

    let reviewSucceeded = false;
    try {
        const response = await fetch('/review', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code, language: languageSelect.value })
        });
        const text = await response.text();
        let data;
        try {
            data = text ? JSON.parse(text) : {};
        } catch (err) {
            showError(`The server returned an unreadable response (HTTP ${response.status}).`);
            return;
        }

        if (response.ok && typeof data.analysis === 'string' && data.analysis.trim() && data.review) {
            lastMarkdown = data.analysis.trim();
            copyReviewBtn.hidden = false;
            renderStructuredReview(data.review);
            currentConversationId = data.conversation_id;
            reviewSucceeded = true;
        } else {
            showError(data.error || `The server returned status ${response.status} without a review.`);
        }
    } catch (err) {
        showError(`Could not complete the request. ${err.message || 'Check that app.py is still running.'}`);
    } finally {
        stopLoading();
        results.hidden = false;
        if (!reviewSucceeded) results.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    if (reviewSucceeded) {
        await loadHistory(historySearch.value);
        await loadDashboard();
        await openConversation(currentConversationId);
    }
}

document.getElementById('followup-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const message = followupInput.value.trim();
    if (!message || !currentConversationId) return;
    const sendButton = document.getElementById('send-followup');
    sendButton.disabled = true;
    try {
        await requestJson(`/api/conversations/${currentConversationId}/messages`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message })
        });
        followupInput.value = '';
        await openConversation(currentConversationId);
        await loadHistory(historySearch.value);
        await loadDashboard();
    } catch (err) {
        window.alert(err.message);
    } finally {
        sendButton.disabled = false;
    }
});

followupInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        document.getElementById('followup-form').requestSubmit();
    }
});

document.getElementById('new-review-btn').addEventListener('click', startNewReview);
document.getElementById('dashboard-new-review').addEventListener('click', startNewReview);
dashboardNav.addEventListener('click', () => {
    showView(dashboardView);
    loadDashboard();
});
workspaceNav.addEventListener('click', () => {
    if (currentConversationId !== null) {
        openConversation(currentConversationId);
    } else {
        showView(editorView);
    }
});
sidebarToggle.addEventListener('click', () => {
    if (window.matchMedia('(max-width: 760px)').matches) {
        appShell.classList.toggle('sidebar-open');
    } else {
        appShell.classList.toggle('sidebar-collapsed');
    }
});
sidebarBackdrop.addEventListener('click', () => appShell.classList.remove('sidebar-open'));
historySearch.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadHistory(historySearch.value).catch((err) => {
        conversationList.textContent = err.message;
    }), 180);
});

updateCount();
loadHistory().catch((err) => { conversationList.textContent = err.message; });
loadDashboard();