"""Browser test: the apps sidebar collapses and expands, remembers it, and the replaced apps are hidden.
Run: python3 -I tests_manual/ui_sidebar.py <screenshot dir>
"""
import re, sys, pathlib
from playwright.sync_api import sync_playwright
out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path('.')
pw = re.search(r'^Odoo web login: admin / (\S+)', pathlib.Path.home().joinpath('sonador-odoo/CREDENTIALS.txt').read_text(), re.M).group(1)
B = 'http://127.0.0.1:8269'
results = []


def check(cond, msg):
    print(('PASS ' if cond else 'FAIL ') + msg, flush=True)
    results.append(bool(cond))


with sync_playwright() as p:
    br = p.chromium.launch(); pg = br.new_context(viewport={'width': 1500, 'height': 950}).new_page()
    pg.set_default_timeout(60000)
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)[:300]))
    pg.goto(B + '/web/login'); pg.fill('input[name=login]', 'admin'); pg.fill('input[name=password]', pw)
    pg.click('button[type=submit]'); pg.wait_for_url('**/odoo**', timeout=180000)
    pg.goto(B + '/odoo/action-sonador_books.action_sb_report_center'); pg.wait_for_selector('.o_sb_sidebar_toggle', timeout=120000)
    names = [n.strip() for n in pg.locator('.mk_apps_sidebar_menu .mk_apps_sidebar_name').all_inner_texts()]
    check(not {'Invoicing', 'Inventory', 'Dashboards'} & set(names), 'replaced apps hidden from the sidebar: %s' % names)
    width = lambda: pg.locator('.mk_apps_sidebar_panel').bounding_box()['width']  # noqa: E731
    if 'mk_sidebar_type_small' in pg.evaluate('document.body.className'):
        pg.click('.o_sb_sidebar_toggle'); pg.wait_for_timeout(500)
    wide = width()
    pg.screenshot(path=str(out / 'sidebar_expanded.png'))
    pg.click('.o_sb_sidebar_toggle'); pg.wait_for_timeout(500)
    narrow = width()
    check(narrow < wide / 2, 'collapse shrinks the sidebar (%d -> %d px)' % (wide, narrow))
    pg.screenshot(path=str(out / 'sidebar_collapsed.png'))
    pg.reload(); pg.wait_for_selector('.o_sb_sidebar_toggle'); pg.wait_for_timeout(500)
    check(abs(width() - narrow) < 2, 'collapsed state survives a reload')
    pg.keyboard.press('Control+b' if sys.platform == 'darwin' else 'Alt+b'); pg.wait_for_timeout(500)
    check(abs(width() - wide) < 2, 'keyboard shortcut expands it again')
    check(not errs, 'no JavaScript errors %s' % errs)
    br.close()
print('%d/%d passed' % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
