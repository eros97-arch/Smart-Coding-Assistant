"""Saves every pytest run as a report (loaded automatically by pytest).

Output (folder = QA_REPORT_DIR env variable, default "qa_reports"):
  test_results.csv   one row per test / subtest, open in Excel
  test_summary.txt   totals, every failure and its message, open in Notepad
"""
import csv
import os
import platform
import sys
import time

_results = []
_start = time.time()


def _category(nodeid):
    name = nodeid.split('::')[-1]
    if name.startswith('test_VULN_'):
        return 'Vulnerability'
    if name.startswith('test_DEFECT_'):
        return 'Defect'
    if 'test_security' in nodeid:
        return 'Security'
    if 'test_qa_edge_cases' in nodeid:
        return 'QA edge case'
    return 'Core feature'


def _verdict(category, outcome):
    expected_fail = category in ('Vulnerability', 'Defect')
    if outcome == 'passed':
        return 'FIXED (now passes)' if expected_fail else 'OK'
    if outcome == 'failed':
        return 'Confirmed (known issue)' if expected_fail else 'UNEXPECTED FAILURE'
    return outcome.upper()


def pytest_runtest_logreport(report):
    if report.when != 'call' and not (report.when == 'setup' and not report.passed):
        return
    ctx = getattr(report, 'context', None)
    subtest = ', '.join(f'{k}={v}' for k, v in getattr(ctx, 'kwargs', {}).items()) if ctx else ''
    message = ''
    if report.failed:
        crash = getattr(report.longrepr, 'reprcrash', None)
        message = crash.message if crash else str(report.longrepr).strip().splitlines()[-1]
        message = ' '.join(message.split())[:300]
    path, _, name = report.nodeid.partition('::')
    name = name.split('::')[-1]
    category = _category(report.nodeid)
    _results.append({
        'file': os.path.basename(path), 'test': name, 'subtest': subtest,
        'category': category, 'outcome': report.outcome,
        'qa_verdict': _verdict(category, report.outcome),
        'seconds': f'{report.duration:.3f}', 'failure_message': message,
    })


def pytest_sessionfinish(session, exitstatus):
    if not _results:
        return
    out = os.environ.get('QA_REPORT_DIR', 'qa_reports')
    os.makedirs(out, exist_ok=True)
    fields = list(_results[0])
    with open(os.path.join(out, 'test_results.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(_results)

    main = [r for r in _results if not r['subtest']]
    subs = [r for r in _results if r['subtest']]
    count = lambda rows, key, val: sum(r[key] == val for r in rows)
    lines = [
        'SMART CODING ASSISTANT - AUTOMATED TEST REPORT',
        '=' * 60,
        f'Date:     {time.strftime("%Y-%m-%d %H:%M:%S")}',
        f'Machine:  {platform.system()} {platform.release()}, Python {sys.version.split()[0]}',
        f'Duration: {time.time() - _start:.2f} s',
        '',
        f'Tests:    {len(main)}   passed {count(main, "outcome", "passed")}   '
        f'failed {count(main, "outcome", "failed")}',
        f'Subtests: {len(subs)}   passed {count(subs, "outcome", "passed")}',
        '',
        'By category:',
    ]
    for cat in ('Core feature', 'QA edge case', 'Security', 'Defect', 'Vulnerability'):
        rows = [r for r in main if r['category'] == cat]
        if rows:
            lines.append(f'  {cat:<15} {len(rows):>3} tests   passed {count(rows, "outcome", "passed"):>3}'
                         f'   failed {count(rows, "outcome", "failed"):>3}')
    unexpected = [r for r in _results if r['qa_verdict'] == 'UNEXPECTED FAILURE']
    fixed = [r for r in _results if r['qa_verdict'].startswith('FIXED')]
    lines += ['', f'QA verdict: {"ALL AS EXPECTED" if not unexpected else str(len(unexpected)) + " UNEXPECTED FAILURE(S) - investigate"}']
    if fixed:
        lines.append(f'Fixed since last time: {len(fixed)} known issue(s) now pass')
    for title, rows in (('UNEXPECTED FAILURES', unexpected),
                        ('KNOWN ISSUES CONFIRMED (Defects and Vulnerabilities)',
                         [r for r in _results if r['qa_verdict'].startswith('Confirmed')]),
                        ('KNOWN ISSUES NOW FIXED', fixed)):
        if rows:
            lines += ['', title, '-' * len(title)]
            for r in rows:
                lines.append(f'[{r["category"]}] {r["file"]} :: {r["test"]}')
                if r['failure_message']:
                    lines.append(f'    -> {r["failure_message"]}')
    with open(os.path.join(out, 'test_summary.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print(f'\nReports saved: {os.path.join(out, "test_results.csv")} and test_summary.txt')
