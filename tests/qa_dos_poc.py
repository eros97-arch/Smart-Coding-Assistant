"""Live proof-of-concept: does the running app enforce payload caps and rate limiting?

Needs the server running (venv\\bin\\python app.py). Uses NO Gemini quota:
every request is built so the app rejects it BEFORE calling Gemini.

Run:  venv\\bin\\python tests\\qa_dos_poc.py
      venv\\bin\\python tests\\qa_dos_poc.py --requests 100 --size-mb 10

Test 1 - Payload cap:  sends a huge body with an invalid language.
   PROTECTED  -> 413 Payload Too Large (server refuses to read it)
   VULNERABLE -> 400 "Unsupported language" (server read and parsed it all)
Test 2 - Rate limit:   fires many requests as fast as possible with empty code.
   PROTECTED  -> some 429 Too Many Requests
   VULNERABLE -> every request answered (400), never 429
"""
import argparse
import csv
import os
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = 'http://127.0.0.1:5000'


def post(body: bytes):
    req = urllib.request.Request(f'{BASE}/review', data=body,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:200]
    except (urllib.error.URLError, ConnectionError, OSError) as e:
        return 'conn-error', str(e).encode()


def payload_cap_test(size_mb):
    print(f'\n=== TEST 1: Payload cap ({size_mb} MB of code) ===')
    code = 'x = 1\n' * int(size_mb * 1024 * 1024 / 6)
    body = json.dumps({'code': code, 'language': 'NotALanguage'}).encode()
    start = time.perf_counter()
    status, reply = post(body)
    secs = time.perf_counter() - start
    print(f'Sent {len(body)/1024/1024:.1f} MB -> HTTP {status} in {secs:.2f}s')
    print(f'Server said: {reply.decode(errors="replace")[:120]}')
    detail = f'{len(body)/1024/1024:.1f} MB sent -> HTTP {status} in {secs:.2f}s'
    if status == 413:
        print('RESULT: PROTECTED - oversized payload rejected')
        return True, detail
    if status == 400 and b'Unsupported' in reply:
        print('RESULT: VULNERABLE - server accepted and parsed the whole payload.')
        print('        With a valid language, all of it would be sent to Gemini (quota/cost).')
        return False, detail
    print('RESULT: UNCLEAR - check the server window')
    return None, detail


def rate_limit_test(n, workers):
    print(f'\n=== TEST 2: Rate limit ({n} requests, {workers} at a time) ===')
    body = json.dumps({'code': '', 'language': 'Python'}).encode()
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        statuses = list(pool.map(lambda _: post(body)[0], range(n)))
    secs = time.perf_counter() - start
    counts = {s: statuses.count(s) for s in sorted(set(statuses), key=str)}
    print(f'{n} requests in {secs:.2f}s ({n/secs:.0f} per second)')
    print(f'Status codes: {counts}')
    detail = f'{n} requests in {secs:.2f}s ({n/secs:.0f}/s), status codes {counts}'
    if 429 in counts:
        print(f'RESULT: PROTECTED - {counts[429]} requests blocked with 429')
        return True, detail
    print(f'RESULT: VULNERABLE - all {n} requests were processed; none blocked.')
    print('        Nothing stops one script from sending reviews non-stop, each one a')
    print('        Gemini call, until the free quota (HTTP 429 from Google) runs out.')
    return False, detail


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--size-mb', type=float, default=5)
    p.add_argument('--requests', type=int, default=50)
    p.add_argument('--workers', type=int, default=10)
    p.add_argument('--report-dir', default='qa_reports')
    args = p.parse_args()
    status, _ = post(b'{}')
    if status == 'conn-error':
        raise SystemExit('Cannot reach http://127.0.0.1:5000 - start the server first '
                         '(venv\\bin\\python app.py) in another window.')
    cap, cap_detail = payload_cap_test(args.size_mb)
    rate, rate_detail = rate_limit_test(args.requests, args.workers)
    word = lambda v: 'PROTECTED' if v else 'VULNERABLE' if v is False else 'UNCLEAR'
    print('\n===== DoS PROOF-OF-CONCEPT SUMMARY =====')
    print(f'Payload cap:   {word(cap)}')
    print(f'Rate limiting: {word(rate)}')
    print('No Gemini quota was used (all requests were rejected before the AI call).')

    stamp = time.strftime('%Y-%m-%d %H:%M:%S')
    os.makedirs(args.report_dir, exist_ok=True)
    rows = [
        ['Payload cap', f'{args.size_mb} MB body, invalid language', '413 Payload Too Large',
         cap_detail, word(cap), stamp],
        ['Rate limiting', f'{args.requests} requests, {args.workers} at a time', 'some 429 Too Many Requests',
         rate_detail, word(rate), stamp],
    ]
    csv_path = os.path.join(args.report_dir, 'dos_results.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['test', 'attack', 'protected_if', 'observed', 'result', 'tested_at'])
        w.writerows(rows)
    txt_path = os.path.join(args.report_dir, 'dos_summary.txt')
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write('SMART CODING ASSISTANT - DoS PROOF OF CONCEPT\n' + '=' * 50 + '\n')
        f.write(f'Date: {stamp}\nTarget: {BASE}/review\n\n')
        for r in rows:
            f.write(f'{r[0]}: {r[4]}\n  Attack:    {r[1]}\n  Protected: {r[2]}\n  Observed:  {r[3]}\n\n')
        f.write('No Gemini quota was used: every request was invalid on purpose and rejected\n'
                'before the AI call, which still proves the server accepted it without limits.\n')
    print(f'Saved {csv_path} and {txt_path}')


if __name__ == '__main__':
    main()
