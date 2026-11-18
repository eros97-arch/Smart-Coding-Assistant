"""Run the WHOLE QA suite in the right order and save every report in one folder.

Run from the project folder:
    venv\\bin\\python tests\\run_all_qa.py              (everything; live steps need the server)
    venv\\bin\\python tests\\run_all_qa.py --skip-live  (offline steps only, no server/API key)
    venv\\bin\\python tests\\run_all_qa.py --runs 1     (quicker benchmark)

Order: 1 pytest -> 2 Bandit -> 3 pip-audit -> 4 benchmark (live) -> 5 DoS PoC (live) -> 6 charts
Reports: qa_reports\\<date_time>\\  with QA_REPORT.txt as the overview, charts in charts\\.
First time: venv\\bin\\python -m pip install flask pytest bandit pip-audit matplotlib
The benchmark sends 52 samples x --runs to Gemini (~5 min per run). If many reviews
fail with 502 (free-tier quota), rerun with a bigger --delay.
"""
import argparse
import glob
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def run(cmd, out_txt, env=None):
    """Run a command, show its output live, and save it to out_txt."""
    print(f'\n$ {" ".join(cmd)}')
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True,
                          encoding='utf-8', errors='replace')
    output = (proc.stdout or '') + (proc.stderr or '')
    print(output.rstrip()[-3000:])
    with open(out_txt, 'w', encoding='utf-8') as f:
        f.write(output)
    return proc.returncode, output


def has_module(name):
    return subprocess.run([PY, '-c', f'import {name}'], capture_output=True).returncode == 0


def server_up():
    try:
        urllib.request.urlopen('http://127.0.0.1:5000/', timeout=5).close()
        return True
    except (urllib.error.URLError, OSError):
        return False


def read(path):
    try:
        with open(path, encoding='utf-8-sig') as f:
            return f.read().strip()
    except OSError:
        return '(no report produced)'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--runs', type=int, default=2, help='benchmark runs per sample')
    p.add_argument('--delay', type=float, default=1.0, help='seconds between benchmark requests')
    p.add_argument('--skip-live', action='store_true', help='skip benchmark + DoS (no server needed)')
    args = p.parse_args()

    folder = os.path.join(ROOT, 'qa_reports', time.strftime('%Y-%m-%d_%H%M'))
    os.makedirs(folder, exist_ok=True)
    status = []

    # 1. Automated tests
    print('\n' + '=' * 60 + '\nSTEP 1/6  Automated tests (pytest)\n' + '=' * 60)
    env = dict(os.environ, QA_REPORT_DIR=folder)
    code, out = run([PY, '-m', 'pytest', 'tests', '-v', '--tb=short', '-p', 'no:cacheprovider'],
                    os.path.join(folder, 'pytest_output.txt'), env)
    last = [l for l in out.splitlines() if ' passed' in l or ' failed' in l]
    status.append(('1. Automated tests (pytest)', last[-1].strip('= ') if last else f'exit code {code}'))

    # 2. Bandit
    print('\n' + '=' * 60 + '\nSTEP 2/6  Bandit security scan\n' + '=' * 60)
    if has_module('bandit'):
        run([PY, '-m', 'bandit', 'app.py', '-f', 'csv', '-o', os.path.join(folder, 'bandit_results.csv')],
            os.path.join(folder, 'bandit_log.txt'))
        _, out = run([PY, '-m', 'bandit', 'app.py'], os.path.join(folder, 'bandit_report.txt'))
        high = out.count('Severity: High')
        med = out.count('Severity: Medium')
        low = out.count('Severity: Low')
        status.append(('2. Bandit scan', f'{high} High, {med} Medium, {low} Low '
                       '(B310 urlopen = known false positive)'))
    else:
        status.append(('2. Bandit scan', 'SKIPPED - run: venv\\bin\\python -m pip install bandit'))

    # 3. pip-audit
    print('\n' + '=' * 60 + '\nSTEP 3/6  Library vulnerability check (pip-audit)\n' + '=' * 60)
    if has_module('pip_audit'):
        code, out = run([PY, '-m', 'pip_audit', '-r', 'requirements.txt'],
                        os.path.join(folder, 'pip_audit_report.txt'))
        status.append(('3. pip-audit', 'No known vulnerabilities' if 'No known vulnerabilities' in out
                       else 'See pip_audit_report.txt'))
    else:
        status.append(('3. pip-audit', 'SKIPPED - run: venv\\bin\\python -m pip install pip-audit'))

    # 4 + 5. Live steps
    live = not args.skip_live and server_up()
    if not args.skip_live and not live:
        print('\nServer not running at http://127.0.0.1:5000 - skipping live steps 4 and 5.')
        print('Start it in another window ($env:GEMINI_API_KEY="..."; venv\\bin\\python app.py) and rerun.')
    print('\n' + '=' * 60 + '\nSTEP 4/6  Live AI benchmark\n' + '=' * 60)
    if live:
        run([PY, 'tests/qa_benchmark.py', '--runs', str(args.runs), '--delay', str(args.delay), '--fresh',
             '--out', os.path.join(folder, 'benchmark_results.csv'), '--report-dir', folder],
            os.path.join(folder, 'benchmark_log.txt'))
        summary = read(os.path.join(folder, 'benchmark_summary.txt'))
        pick = [l.strip() for l in summary.splitlines()
                if l.startswith(('Languages:', 'Planted bug', 'Response time'))]
        status.append(('4. Live AI benchmark', ' | '.join(pick) or 'See benchmark_summary.txt'))
    else:
        status.append(('4. Live AI benchmark', 'SKIPPED (server not running or --skip-live)'))

    print('\n' + '=' * 60 + '\nSTEP 5/6  Live DoS proof of concept\n' + '=' * 60)
    if live:
        run([PY, 'tests/qa_dos_poc.py', '--report-dir', folder], os.path.join(folder, 'dos_log.txt'))
        dos = read(os.path.join(folder, 'dos_summary.txt'))
        pick = [l.strip() for l in dos.splitlines() if l.startswith(('Payload cap:', 'Rate limiting:'))]
        status.append(('5. DoS proof of concept', ' | '.join(pick) or 'See dos_summary.txt'))
    else:
        status.append(('5. DoS proof of concept', 'SKIPPED (server not running or --skip-live)'))

    # 6. Charts
    print('\n' + '=' * 60 + '\nSTEP 6/6  Charts for the slides\n' + '=' * 60)
    if has_module('matplotlib'):
        bench = os.path.join(folder, 'benchmark_results.csv')
        if not os.path.exists(bench) and os.path.exists(os.path.join(ROOT, 'qa_results.csv')):
            bench = os.path.join(ROOT, 'qa_results.csv')  # reuse the last benchmark when skipped
        cmd = [PY, 'tests/make_qa_charts.py', '--report-dir', folder]
        if os.path.exists(bench):
            cmd += ['--benchmark', bench]
        run(cmd, os.path.join(folder, 'charts_log.txt'))
        made = len(glob.glob(os.path.join(folder, 'charts', '*.png')))
        status.append(('6. Charts', f'{made} PNG charts in charts\\'))
    else:
        status.append(('6. Charts', 'SKIPPED - run: venv\\bin\\python -m pip install matplotlib'))

    # Master report
    lines = ['SMART CODING ASSISTANT - FULL QA REPORT', '=' * 60,
             f'Generated: {time.strftime("%Y-%m-%d %H:%M:%S")}', f'Folder:    {folder}', '',
             'OVERVIEW', '--------']
    lines += [f'{name:<30} {result}' for name, result in status]
    sections = [('AUTOMATED TESTS', 'test_summary.txt'), ('BANDIT', 'bandit_report.txt'),
                ('PIP-AUDIT', 'pip_audit_report.txt'), ('LIVE AI BENCHMARK', 'benchmark_summary.txt'),
                ('DoS PROOF OF CONCEPT', 'dos_summary.txt')]
    for title, fname in sections:
        path = os.path.join(folder, fname)
        if os.path.exists(path):
            lines += ['', '', title, '=' * len(title), read(path)]
    lines += ['', '', 'FILES IN THIS FOLDER', '--------------------']
    lines += sorted(f for f in os.listdir(folder))
    with open(os.path.join(folder, 'QA_REPORT.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    print('\n' + '=' * 60 + '\nDONE\n' + '=' * 60)
    for name, result in status:
        print(f'{name:<30} {result}')
    print(f'\nAll reports saved in: {folder}')
    print('Open QA_REPORT.txt first.')
    if os.name == 'nt':
        try:
            os.startfile(folder)  # opens the folder in File Explorer
        except OSError:
            pass


if __name__ == '__main__':
    main()
