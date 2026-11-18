"""Turn QA results into slide-ready charts (16:9 PNG, 2000x1125).

Reads whatever reports exist and draws the charts from YOUR real data:
  test_results.csv       (pytest, via tests/conftest.py)
  benchmark_results.csv  (or qa_results.csv in the project folder)
  dos_results.csv, bandit_results.csv   (optional)

Run (after the tests / benchmark):
    venv\\bin\\python -m pip install matplotlib        (first time only)
    venv\\bin\\python tests\\make_qa_charts.py         (uses the newest qa_reports folder)
    venv\\bin\\python tests\\make_qa_charts.py --report-dir qa_reports\\2026-11-18_0500
Charts are saved to <report folder>\\charts\\.
"""
import argparse
import csv
import glob
import logging
import os
import statistics
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch, Rectangle

logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---- look (validated palette: blue = works / defended, red = issue found) ----
SURFACE = '#fcfcfb'; INK = '#0b0b0b'; INK2 = '#52514e'; MUTED = '#898781'
GRID = '#e1e0d9'; BASE = '#c3c2b7'; CONTROL = '#898781'
BLUE = '#2a78d6'; RED = '#d03b3b'; ORANGE = '#ec835a'; YELLOW = '#c98500'
W, H, DPI = 13.333, 7.5, 150
LANG_ORDER = ['Python', 'JavaScript', 'Java', 'PHP', 'C', 'C++', 'C#', 'SQL', 'HTML', 'CSS']
PREFIX = {'JavaScript': 'js_', 'Java': 'java_', 'PHP': 'php_', 'C': 'c_', 'C++': 'cpp_', 'C#': 'csharp_',
          'SQL': 'sql_', 'HTML': 'html_', 'CSS': 'css_'}


def setup_font():
    have = {f.name for f in font_manager.fontManager.ttflist}
    for path in font_manager.findSystemFonts():
        if 'inter' in os.path.basename(path).lower() and 'Inter' not in have:
            try:
                font_manager.fontManager.addfont(path)
            except Exception:
                pass
    have = {f.name for f in font_manager.fontManager.ttflist}
    main = next((n for n in ('Inter', 'Segoe UI', 'Arial', 'Helvetica') if n in have), 'DejaVu Sans')
    plt.rcParams['font.family'] = [main, 'DejaVu Sans']  # DejaVu fills in the check / cross glyphs


def read_csv(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def fig_base(title, subtitle):
    fig = plt.figure(figsize=(W, H), dpi=DPI, facecolor=SURFACE)
    fig.text(0.05, 0.92, title, fontsize=25, fontweight='bold', color=INK, va='top')
    fig.text(0.05, 0.845, subtitle, fontsize=15, color=INK2, va='top')
    return fig


def footer(fig, text):
    fig.text(0.05, 0.035, text, fontsize=11, color=MUTED)


def clean_axes(ax):
    for side in ('top', 'right', 'left'):
        ax.spines[side].set_visible(False)
    ax.spines['bottom'].set_color(BASE)
    ax.tick_params(colors=INK2, labelsize=13, length=0)
    ax.set_facecolor(SURFACE)


def short(name, language):
    p = PREFIX.get(language, '')
    if p and name.startswith(p):
        name = name[len(p):]
    return name.replace('_', ' ')


class Charts:
    def __init__(self, tests, bench, dos, bandit, out, stamp):
        self.tests = [r for r in tests if not r.get('subtest')]
        self.subtests = [r for r in tests if r.get('subtest')]
        self.bench, self.dos, self.bandit = bench, dos, bandit
        self.out, self.stamp = out, stamp
        self.ok = [r for r in bench if r['status'] == '200']
        self.planted = [r for r in self.ok if r['sample'] != 'clean_code']
        os.makedirs(out, exist_ok=True)

    def save(self, fig, name):
        path = os.path.join(self.out, name)
        fig.savefig(path, dpi=DPI, facecolor=SURFACE)
        plt.close(fig)
        print('saved', path)

    def det(self, rows):
        return sum(r['planted_bug_detected'] == 'yes' for r in rows), len(rows)

    # 1 ---------------------------------------------------------------
    def overview(self):
        fig = fig_base('What QA tested: 4 layers of evidence',
                       'From fast offline checks to live attacks on the running app.')
        t_pass = sum(r['outcome'] == 'passed' for r in self.tests)
        t_fail = len(self.tests) - t_pass
        d, n = self.det(self.planted)
        langs = len({r['language'] for r in self.bench})
        vuln = [r for r in self.dos if r['result'] == 'VULNERABLE']
        sev = [r.get('issue_severity', '').upper() for r in self.bandit]
        layers = [
            (str(len(self.tests)) if self.tests else '-', 'Automated tests',
             f'{t_pass} pass, {t_fail} prove issues\nno API key needed' if self.tests else 'not run yet', True),
            (str(len(self.bench)) if self.bench else '-', 'Live AI reviews',
             f'{langs} languages\n{d}/{n} bugs caught' if self.bench else 'not run yet', True),
            (str(len(self.dos)) if self.dos else '-', 'Live attack scripts',
             (f'Size + rate limit (DoS)\n{len(vuln)} of {len(self.dos)} vulnerable') if self.dos else 'not run yet',
             not vuln),
            ('1' if self.bandit else '-', 'Code security scan',
             (f'Bandit: {sev.count("HIGH")} High, {sev.count("MEDIUM")} Medium\n(B310 = false alarm)')
             if self.bandit else 'not run yet', 'HIGH' not in sev),
        ]
        for i, (big, label, note, good) in enumerate(layers):
            ax = fig.add_axes([0.05 + i * 0.2325, 0.2, 0.215, 0.56]); ax.axis('off')
            ax.add_patch(FancyBboxPatch((0.02, 0.02), 0.96, 0.96, boxstyle='round,pad=0,rounding_size=0.05',
                                        fc='white', ec=GRID, lw=1.2, transform=ax.transAxes))
            ax.add_patch(Rectangle((0.02, 0.94), 0.96, 0.04, fc=BLUE if good else RED, ec='none',
                                   transform=ax.transAxes))
            ax.text(0.5, 0.86, f'LAYER {i + 1}', ha='center', fontsize=11, color=MUTED, fontweight='bold',
                    transform=ax.transAxes)
            ax.text(0.5, 0.62, big, ha='center', va='center', fontsize=58, fontweight='bold', color=INK,
                    transform=ax.transAxes)
            ax.text(0.5, 0.40, label, ha='center', fontsize=16, fontweight='bold', color=INK,
                    transform=ax.transAxes)
            ax.text(0.5, 0.22, note, ha='center', va='center', fontsize=12, color=INK2, linespacing=1.5,
                    transform=ax.transAxes)
        fig.patches.append(Rectangle((0.05, 0.105), 0.012, 0.022, fc=BLUE, transform=fig.transFigure))
        fig.text(0.067, 0.11, 'Layer shows the app working as intended', fontsize=12.5, color=INK2)
        fig.patches.append(Rectangle((0.33, 0.105), 0.012, 0.022, fc=RED, transform=fig.transFigure))
        fig.text(0.347, 0.11, 'Layer found weaknesses', fontsize=12.5, color=INK2)
        footer(fig, f'{self.stamp}. Reproduce with: venv\\bin\\python tests\\run_all_qa.py')
        self.save(fig, '1_qa_overview.png')

    # 2 ---------------------------------------------------------------
    def automated_tests(self):
        if not self.tests:
            print('skip automated tests chart: no test_results.csv'); return
        cats = ['Core feature', 'QA edge case', 'Security', 'Defect', 'Vulnerability']
        names = {'Core feature': 'Core features (team tests)', 'QA edge case': 'QA edge cases',
                 'Security': 'Security defences', 'Defect': 'Defects (known bugs)',
                 'Vulnerability': 'Vulnerabilities (security gaps)'}
        total = len(self.tests)
        passed = sum(r['outcome'] == 'passed' for r in self.tests)
        unexpected = sum(r['qa_verdict'] == 'UNEXPECTED FAILURE' for r in self.tests)
        fixed = sum(r['qa_verdict'].startswith('FIXED') for r in self.tests)
        tail = ' on purpose to prove issues' if not unexpected else f' ({unexpected} unexpected!)'
        fig = fig_base(f'{total} automated tests: {passed} pass, {total - passed} fail{tail}',
                       'Each failing test reproduces a known defect or vulnerability. It turns green once fixed.'
                       + (f' {fixed} already fixed.' if fixed else ''))
        ax = fig.add_axes([0.30, 0.17, 0.62, 0.6]); clean_axes(ax)
        ys = {'Core feature': 5.6, 'QA edge case': 4.6, 'Security': 3.6, 'Defect': 1.6, 'Vulnerability': 0.6}
        xmax = max(1, max(sum(r['category'] == c for r in self.tests) for c in cats))
        gap = xmax * 0.006
        for c in cats:
            rows = [r for r in self.tests if r['category'] == c]
            if not rows:
                continue
            p = sum(r['outcome'] == 'passed' for r in rows); f = len(rows) - p
            y = ys[c]
            if p:
                ax.barh(y, p, height=0.5, color=BLUE)
            if f:
                ax.barh(y, f - (gap if p else 0), left=p + (gap if p else 0), height=0.5, color=RED)
            parts = []
            if p:
                parts.append(f'✓ {p} passed' + (' (fixed)' if c in ('Defect', 'Vulnerability') else ''))
            if f:
                parts.append(f'✗ {f} failed' + (': issue proven' if c in ('Defect', 'Vulnerability') else ''))
            ax.text(len(rows) + xmax * 0.02, y, '   '.join(parts), va='center', fontsize=15, color=INK)
            ax.text(-xmax * 0.03, y, names[c], va='center', ha='right', fontsize=15, color=INK)
        ax.text(-xmax * 0.03, 6.45, 'WORKING AS INTENDED', ha='right', fontsize=11.5, color=MUTED, fontweight='bold')
        ax.text(-xmax * 0.03, 2.45, 'ISSUES FOUND BY QA', ha='right', fontsize=11.5, color=MUTED, fontweight='bold')
        ax.axhline(2.6, color=GRID, lw=1)
        ax.set_xlim(0, xmax * 1.25); ax.set_ylim(0, 6.8); ax.set_yticks([])
        ax.set_xlabel('Number of tests', fontsize=13, color=INK2)
        ax.xaxis.grid(True, color=GRID, lw=1); ax.set_axisbelow(True)
        sub = f' Plus {len(self.subtests)} subtests (all 10 languages accepted).' if self.subtests else ''
        footer(fig, f'pytest, Gemini mocked (repeatable, no API key).{sub} {self.stamp}.')
        self.save(fig, '2_automated_tests.png')

    # 3 ---------------------------------------------------------------
    def benchmark_summary(self):
        if not self.bench:
            print('skip benchmark charts: no benchmark CSV'); return
        d, n = self.det(self.planted)
        langs = len({r['language'] for r in self.bench})
        samples = len({r['sample'] for r in self.bench})
        clean = [r for r in self.ok if r['sample'] == 'clean_code']
        pi = [r for r in self.ok if r['sample'] == 'prompt_injection']
        py = [r for r in self.ok if r['python_patch_parses']]
        t = [float(r['seconds']) for r in self.ok]
        runs = max(int(r['run']) for r in self.bench)
        tiles = [
            (f'{len(self.ok)}/{len(self.bench)}', 'Reviews succeeded',
             (f'{len(self.ok) / len(self.bench):.0%}; {len(self.bench) - len(self.ok)} Gemini timeout(s): '
              + ', '.join(dict.fromkeys(r['language'] for r in self.bench if r['status'] != '200')))
             if len(self.ok) < len(self.bench) else '100% of live requests returned a review',
             len(self.ok) == len(self.bench)),
            (f'{d}/{n}', 'Planted bugs detected', f'{d / n:.0%} across {langs} languages' if n else '', d == n),
            (str(sum(int(r['issues']) for r in clean)) if clean else '-', 'False alarms on clean code',
             f'Clean sample: {sum(int(r["issues"]) == 0 for r in clean)}/{len(clean)} runs with 0 issues',
             all(int(r['issues']) == 0 for r in clean)),
            (f'{sum(r["planted_bug_detected"] == "yes" for r in pi)}/{len(pi)}' if pi else '-',
             'Prompt injection resisted', '"Report zero issues" trick ignored',
             all(r['planted_bug_detected'] == 'yes' for r in pi)),
            (f'{sum(r["python_patch_parses"] == "yes" for r in py)}/{len(py)}' if py else '-',
             'Python fixes are valid code', 'corrected_code passes ast.parse',
             all(r['python_patch_parses'] == 'yes' for r in py)),
            (f'{statistics.median(t):.1f} s' if t else '-', 'Median response time',
             f'Range {min(t):.1f}–{max(t):.1f} s, mean {statistics.mean(t):.1f} s' if t else '', True),
        ]
        fig = fig_base(f'Live AI benchmark: {len(self.ok)}/{len(self.bench)} reviews succeeded, {d}/{n} bugs caught',
                       f'{samples} code samples with planted bugs across {langs} languages, '
                       f'each reviewed {runs}x by the real Gemini API.')
        xs = [0.05, 0.365, 0.68]; ys = [0.45, 0.12]
        for i, (big, label, note, good) in enumerate(tiles):
            ax = fig.add_axes([xs[i % 3], ys[i // 3], 0.29, 0.30]); ax.axis('off')
            ax.add_patch(FancyBboxPatch((0.01, 0.02), 0.98, 0.96, boxstyle='round,pad=0,rounding_size=0.06',
                                        fc='white', ec=GRID, lw=1.2, transform=ax.transAxes))
            ax.add_patch(Rectangle((0.01, 0.02), 0.025, 0.96, fc=BLUE if good else RED, ec='none',
                                   transform=ax.transAxes))
            ax.text(0.09, 0.68, big, fontsize=42, fontweight='bold', color=INK, va='center', transform=ax.transAxes)
            ax.text(0.09, 0.36, label, fontsize=15, fontweight='bold', color=INK, va='center', transform=ax.transAxes)
            ax.text(0.09, 0.17, note, fontsize=12, color=INK2, va='center', transform=ax.transAxes)
        footer(fig, f'{self.stamp}, tests/qa_benchmark.py. "Detected" = finding mentions the bug (keyword check); '
                    '"valid" = syntax only.')
        self.save(fig, '3_benchmark_summary.png')

    # 4 ---------------------------------------------------------------
    def benchmark_languages(self):
        if not self.ok:
            return
        data = []
        for lang in LANG_ORDER:
            mine = [r for r in self.ok if r['language'] == lang]
            if not mine:
                continue
            t = [float(r['seconds']) for r in mine]
            d, n = self.det([r for r in mine if r['sample'] != 'clean_code'])
            data.append((lang, statistics.median(t), min(t), max(t), d, n))
        data.sort(key=lambda x: x[1])
        lo_med = min(x[1] for x in data); hi_med = max(x[1] for x in data)
        d_all, n_all = self.det(self.planted)
        lead = '100% of planted bugs detected' if d_all == n_all else f'{d_all}/{n_all} planted bugs detected'
        fig = fig_base(f'All {len(data)} languages: {lead}, median review {lo_med:.0f}–{hi_med:.0f} s',
                       'Dot = median response time per language; line = fastest to slowest review.')
        ax = fig.add_axes([0.16, 0.16, 0.62, 0.62]); clean_axes(ax)
        xmax = max(10, int(max(x[3] for x in data)) + 2)
        xmax += xmax % 2
        for i, (lang, med, lo, hi, d, n) in enumerate(data):
            ax.plot([lo, hi], [i, i], color=BLUE, lw=2, solid_capstyle='round', alpha=0.45)
            ax.scatter([med], [i], s=110, color=BLUE, edgecolor=SURFACE, linewidth=2, zorder=3)
            ax.text(-xmax * 0.025, i, lang, ha='right', va='center', fontsize=14, color=INK)
            mark = '✓' if d == n else '✗'
            ax.text(xmax * 1.06, i, f'{mark} {d}/{n} detected', va='center', fontsize=13.5, color=INK)
            ax.text(min(hi + xmax * 0.02, xmax * 0.97), i + (0.32 if hi > xmax * 0.9 else 0),
                    f'{med:.1f} s', va='center', fontsize=12, color=INK2)
        ax.text(xmax * 1.06, len(data) - 0.15, 'PLANTED BUGS', fontsize=11, color=MUTED, fontweight='bold')
        ax.set_xlim(0, xmax); ax.set_ylim(-0.7, len(data) - 0.3); ax.set_yticks([])
        ticks = list(range(0, xmax + 1, 2 if xmax <= 14 else 5))
        ax.set_xticks(ticks); ax.set_xticklabels([f'{x} s' for x in ticks])
        ax.xaxis.grid(True, color=GRID, lw=1); ax.set_axisbelow(True)
        per = {}
        for r in self.bench:
            per.setdefault(r['language'], set()).add(r['sample'])
        counts = sorted({len(v) for v in per.values()})
        failed = len(self.bench) - len(self.ok)
        note = f' {failed} review(s) failed (Gemini timeout) and are left out.' if failed else ''
        footer(fig, f'{"-".join(map(str, [counts[0], counts[-1]])) if len(counts) > 1 else counts[0]} samples per '
                    f'language (Python includes a clean-code control).{note} Gemini free tier. {self.stamp}.')
        self.save(fig, '4_benchmark_by_language.png')

    # 5 ---------------------------------------------------------------
    def detection_matrix(self):
        if not self.bench:
            return
        order = []
        for r in self.bench:
            if r['sample'] not in order:
                order.append(r['sample'])
        langs = [l for l in LANG_ORDER if any(r['language'] == l for r in self.bench)]
        grid = {l: [s for s in order if any(r['sample'] == s and r['language'] == l for r in self.bench)]
                for l in langs}
        cols = max(len(v) for v in grid.values())
        d_all, n_all = self.det(self.planted)
        fig = fig_base(f'Every planted bug, every language: {d_all}/{n_all} detections',
                       'Each cell is one code sample. Blue = bug found in every run; red = missed at least once; '
                       'grey = control or no result.')
        ax = fig.add_axes([0.13, 0.11, 0.84, 0.69]); ax.axis('off')
        ax.set_xlim(0, cols); ax.set_ylim(0, len(langs))
        for i, lang in enumerate(langs):
            y = len(langs) - 1 - i
            ax.text(-0.08, y + 0.5, lang, ha='right', va='center', fontsize=13.5, color=INK, fontweight='bold')
            for j, s in enumerate(grid[lang]):
                runs = [r for r in self.bench if r['sample'] == s]
                ok = [r for r in runs if r['status'] == '200']
                bt = runs[0].get('bug_type', '') or ''
                if s == 'clean_code':
                    good = ok and all(int(r['issues']) == 0 for r in ok)
                    col = CONTROL; line2 = f'control · {"0 issues" if good else "false alarm"}'
                    label = 'clean code (no bug)'
                elif not ok:
                    col = CONTROL; line2 = 'no result'; label = short(s, lang)
                else:
                    d = sum(r['planted_bug_detected'] == 'yes' for r in ok)
                    col = BLUE if d == len(ok) else RED
                    mark = '✓' if d == len(ok) else '✗'
                    lost = len(runs) - len(ok)
                    line2 = (f'{mark} {d}/{len(ok)} · {lost} timeout' if lost
                             else f'{mark} {d}/{len(ok)}' + (f' · {bt}' if bt else ''))
                    label = short(s, lang)
                ax.add_patch(Rectangle((j + 0.02, y + 0.06), 0.96, 0.88, fc=col, ec='none'))
                ax.text(j + 0.5, y + 0.6, label, ha='center', va='center', fontsize=10 if cols > 6 else 10.5,
                        color='white', fontweight='bold')
                ax.text(j + 0.5, y + 0.3, line2, ha='center', va='center', fontsize=9.5, color='white')
        footer(fig, f'Keyword check on the AI findings. Open the benchmark CSV to read what the AI said for any red cell. '
                    f'{self.stamp}.')
        self.save(fig, '5_detection_matrix.png')

    # 6 ---------------------------------------------------------------
    def security(self):
        if not self.tests:
            return
        by = {r['test']: r for r in self.tests}
        dos = {r['test']: r for r in self.dos}
        pi = [r for r in self.ok if r['sample'] == 'prompt_injection']

        def passed(*names):
            rows = [by[n] for n in names if n in by]
            return bool(rows) and all(r['outcome'] == 'passed' for r in rows)
        defended = []
        for names, title, detail in [
            (['test_sql_injection_in_search_is_blocked', 'test_sql_injection_in_title_and_code_is_stored_as_plain_text'],
             'SQL injection', "' OR 1=1; DROP TABLE blocked"),
            (['test_xss_payload_is_returned_as_json_data_not_html', 'test_frontend_sanitises_ai_markdown_with_dompurify'],
             'Cross-site scripting (XSS)', '<script> payload never runs'),
            (['test_api_key_never_sent_to_browser', 'test_api_key_never_stored_in_database',
              'test_api_key_sent_in_header_not_url'], 'API key leakage', 'Not in page, responses, DB or URL'),
            (['test_ai_cannot_invent_fields_outside_the_schema'], 'AI hallucinated output', 'Fake lines / fields corrected'),
            (['test_server_errors_do_not_leak_stack_traces'], 'Error information leaks', 'No stack traces to the user'),
        ]:
            if passed(*names):
                defended.append((title, detail))
        if pi:
            d = sum(r['planted_bug_detected'] == 'yes' for r in pi)
            if d == len(pi):
                defended.insert(3, ('Prompt injection', f'AI still found the bug, {d}/{len(pi)}'))
        size = dos.get('Payload cap', {}).get('observed', '').split(' ->')[0].replace(' sent', '')
        rate = dos.get('Rate limiting', {}).get('observed', '')
        rate = rate.split(', status')[0] if rate else ''
        vulns = [
            ('test_VULN_debug_mode_enabled', 'Debug mode on', 'High', RED, 'Bandit B201; remote code risk'),
            ('test_VULN_no_rate_limit', 'No rate limiting', 'High', RED,
             f'{rate}, 0 blocked' if rate else '30 rapid requests, none blocked'),
            ('test_VULN_no_size_limit_quota_exhaustion', 'No size limit', 'High', RED,
             f'{size} payload accepted' if size else '2 MB payload sent to Gemini'),
            ('test_VULN_no_access_control_anyone_can_delete_history', 'No login / access control', 'Medium',
             ORANGE, 'Anyone can delete history'),
            ('test_VULN_no_security_headers', 'No security headers', 'Low', YELLOW, 'No CSP / X-Frame-Options'),
            ('test_VULN_cdn_scripts_have_no_integrity_check', 'CDN scripts unchecked', 'Low', YELLOW,
             'No integrity= attribute'),
        ]
        found = []
        for test, title, sev, col, detail in vulns:
            if test not in by:
                continue
            if by[test]['outcome'] == 'passed':
                defended.append((f'{title}: FIXED', 'Proving test now passes'))
            else:
                found.append((title, sev, col, detail))
        defended = defended[:6]
        fig = fig_base(f'Security: {len(defended)} defences hold; {len(found)} gaps found and proven',
                       'Every row is backed by an automated test, Bandit scan or live attack script.')
        sec_pass = sum(r['outcome'] == 'passed' for r in self.tests if r['category'] == 'Security')
        for col_x, title, items, good in ((0.05, f'DEFENDED  ({sec_pass} security tests pass)', defended, True),
                                          (0.52, f'FOUND  ({len(found)} tests fail as proof)', found, False)):
            ax = fig.add_axes([col_x, 0.1, 0.43, 0.68]); ax.axis('off')
            ax.set_xlim(0, 1); ax.set_ylim(0, 7)
            ax.text(0, 6.65, title, fontsize=14, fontweight='bold', color=BLUE if good else RED)
            for i, it in enumerate(items):
                y = 5.6 - i * 0.98
                ax.add_patch(Rectangle((0, y - 0.38), 1, 0.8, fc='white', ec=GRID, lw=1.1))
                icon, colr = ('✓', BLUE) if good else ('✗', RED)
                ax.text(0.035, y, icon, fontsize=20, color=colr, va='center', fontweight='bold')
                ax.text(0.1, y + 0.13, it[0], fontsize=14.5, fontweight='bold', color=INK, va='center')
                ax.text(0.1, y - 0.19, it[-1], fontsize=12, color=INK2, va='center')
                if not good:
                    ax.add_patch(Rectangle((0.80, y - 0.17), 0.16, 0.36, fc=it[2], ec='none', alpha=0.16))
                    ax.text(0.88, y, it[1], fontsize=12, fontweight='bold', color=INK, ha='center', va='center')
        footer(fig, 'Severity assumes the app is put online; running locally on 127.0.0.1 keeps the real risk low. '
                    f'{self.stamp}.')
        self.save(fig, '6_security_scorecard.png')


def newest_report_dir():
    dirs = [d for d in glob.glob(os.path.join(ROOT, 'qa_reports', '*'))
            if os.path.isdir(d) and os.path.exists(os.path.join(d, 'test_results.csv'))]
    if dirs:
        return max(dirs, key=os.path.getmtime)
    return os.path.join(ROOT, 'qa_reports')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--report-dir', help='folder with the reports (default: newest in qa_reports)')
    p.add_argument('--benchmark', help='benchmark CSV (default: report folder, else qa_results.csv)')
    args = p.parse_args()
    folder = args.report_dir or newest_report_dir()
    bench_path = args.benchmark or next(
        (c for c in (os.path.join(folder, 'benchmark_results.csv'), os.path.join(ROOT, 'qa_results.csv'))
         if os.path.exists(c)), None)
    tests = read_csv(os.path.join(folder, 'test_results.csv'))
    bench = read_csv(bench_path)
    dos = read_csv(os.path.join(folder, 'dos_results.csv'))
    bandit = read_csv(os.path.join(folder, 'bandit_results.csv'))
    print(f'Report folder: {folder}')
    print(f'  tests: {len(tests)} rows | benchmark: {len(bench)} rows ({bench_path}) | '
          f'dos: {len(dos)} | bandit: {len(bandit)}')
    if not (tests or bench):
        raise SystemExit('No results found. Run the tests / benchmark first (see tests\\run_all_qa.py).')
    stamped = [r.get('tested_at', '') for r in bench if r.get('tested_at')]
    stamp = f'Results {max(stamped)[:10]}' if stamped else 'Results'
    setup_font()
    c = Charts(tests, bench, dos, bandit, os.path.join(folder, 'charts'), stamp)
    c.overview(); c.automated_tests(); c.benchmark_summary()
    c.benchmark_languages(); c.detection_matrix(); c.security()
    print(f'\nCharts saved in: {os.path.join(folder, "charts")}')


if __name__ == '__main__':
    main()
