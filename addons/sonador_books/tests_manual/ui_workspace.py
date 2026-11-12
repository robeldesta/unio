"""Browser test: the report list never disappears, drill-downs open tabs, records open in a drawer.
Run: python3 -I tests_manual/ui_workspace.py <screenshot dir>   (needs playwright and the local Odoo on :8269)
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
    br = p.chromium.launch(); ctx = br.new_context(viewport={'width': 1500, 'height': 950}); pg = ctx.new_page()
    pg.set_default_timeout(30000)
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)[:300]))
    pg.goto(B + '/web/login'); pg.fill('input[name=login]', 'admin'); pg.fill('input[name=password]', pw)
    pg.click('button[type=submit]'); pg.wait_for_url('**/odoo**', timeout=180000)
    pg.goto(B + '/odoo/action-sonador_books.action_sb_report_center'); pg.wait_for_selector('.o_sb_ws_row', timeout=120000)
    pg.evaluate("Object.keys(localStorage).filter(k => k.startsWith('sonador_books.workspace')).forEach(k => localStorage.removeItem(k))")
    pg.reload(); pg.wait_for_selector('.o_sb_ws_row')
    side = lambda: pg.locator('.o_sb_ws_side .o_sb_ws_row').count()  # noqa: E731
    tabs = lambda: pg.locator('.o_sb_ws_tab:not(.o_sb_ws_tab_home)').count()  # noqa: E731

    def open_report(cat, name):
        pg.locator('.o_sb_ws_cat', has_text=cat).click(); pg.wait_for_timeout(250)
        pg.locator('.o_sb_ws_row', has_text=name).first.dblclick()
        pg.wait_for_selector('.o_sb_ws_pane:not(.d-none) .o_sb_rep_table'); pg.wait_for_timeout(700)

    open_report('Financial Statements', 'Statement of Financial Position')
    open_report('General Ledger', 'General Ledger Trial Balance')
    open_report('Accounts Receivable', 'Customer Ledgers')
    check(tabs() == 3 and side() > 0, 'three reports open as tabs, list still visible (%d tabs, %d list rows)' % (tabs(), side()))
    pg.locator('.o_sb_ws_tab', has_text='General Ledger Trial Balance').click(); pg.wait_for_timeout(500)
    pane = '.o_sb_ws_pane:not(.d-none) '
    pg.click(pane + '.o_sb_pp_btn'); pg.locator(pane + '.o_sb_chip', has_text='This fiscal year').click(); pg.wait_for_timeout(1500)
    label = pg.locator(pane + '.o_sb_pp_btn').inner_text().strip()
    check('FY 2019' in label, 'TB set to this fiscal year (%s)' % label)
    pg.screenshot(path=str(out / 'workspace_tabs.png'))
    links = pg.locator(pane + '.o_sb_rep_numlink')
    n_links = links.count()
    check(n_links > 0, 'TB has numbers to drill into (%d)' % n_links)
    links.first.click(); pg.wait_for_timeout(1500)
    check(tabs() == 4 and side() > 0, 'first drill-down opened a new tab, list still visible')
    crumbs = pg.locator('.o_sb_ws_crumbs').inner_text()
    check('Report Center' in crumbs and 'Trial Balance' in crumbs, 'breadcrumb shows the path: %s' % crumbs.replace('\n', ' '))
    detail = pg.locator(pane + '.o_sb_rep_detail.o_sb_rep_link')
    if not detail.count():  # drilled ledgers open already expanded
        pg.locator(pane + '.o_sb_rep_group').first.click(); pg.wait_for_timeout(1200)
    check(detail.count() > 0, 'GL account expanded in the tab (%d lines)' % detail.count())
    if detail.count():
        detail.first.dblclick(); pg.wait_for_selector('.o_sb_drawer', timeout=20000); pg.wait_for_timeout(800)
        pg.screenshot(path=str(out / 'workspace_drawer.png'))
        check(pg.locator('.o_sb_drawer').count() == 1, 'voucher opened in the slide-over drawer')
        pg.keyboard.press('Escape'); pg.wait_for_timeout(800)
        check(pg.locator('.o_sb_drawer').count() == 0 and detail.count() > 0 and tabs() == 4,
              'Esc closed the drawer, the ledger is exactly as it was')
    # second drill-down from the TB tab
    pg.locator('.o_sb_ws_tab', has_text='General Ledger Trial Balance').click(); pg.wait_for_timeout(600)
    label2 = pg.locator(pane + '.o_sb_pp_btn').inner_text().strip()
    check(label2 == label, 'TB tab kept its filters after switching (%s)' % label2)
    links = pg.locator(pane + '.o_sb_rep_numlink')
    (links.nth(1) if links.count() > 1 else links.first).click(); pg.wait_for_timeout(1500)
    check(tabs() == 5, 'second drill-down opened another tab (%d)' % tabs())
    pg.click('.o_sb_ws_back'); pg.wait_for_timeout(700)
    active = pg.locator('.o_sb_ws_tab.active').inner_text()
    check('Trial Balance' in active, 'in-app Back returned to the trial balance tab')
    pg.locator('.o_sb_ws_tab.active .o_sb_ws_tabx').click(); pg.wait_for_timeout(500)
    check(tabs() == 4 and side() > 0, 'closed a tab with x, list still visible')
    pg.reload(); pg.wait_for_selector('.o_sb_ws_tab'); pg.wait_for_timeout(1500)
    check(tabs() == 4, 'tabs restored after reload (%d)' % tabs())
    # collapse the list and bring it back
    pg.click('.o_sb_ws_sidehead button[title="Hide the list"]'); pg.wait_for_timeout(300)
    check(pg.locator('.o_sb_ws_side.collapsed').count() == 1, 'list collapses to a slim rail')
    pg.click('.o_sb_ws_side.collapsed .o_sb_ws_railbtn'); pg.wait_for_timeout(300)
    check(side() > 0, 'list expands again')
    # nav center -> report opens in the workspace with a way back
    pg.goto(B + '/odoo/action-sonador_books.action_sb_nav_center'); pg.wait_for_selector('.o_sb_rail_item')
    pg.locator('.o_sb_rail_item', has_text='Customers & Sales').click(); pg.wait_for_timeout(1500)
    pg.locator('.o_sb_card a', has_text='View').first.click(); pg.wait_for_selector('.o_sb_ws_tab'); pg.wait_for_timeout(1000)
    back_crumb = pg.locator('.o_sb_ws_crumbs a').first.inner_text()
    check(side() > 0 and back_crumb, 'report from a center opens in the workspace with "%s" to go back' % back_crumb)
    pg.locator('.o_sb_ws_crumbs a').first.click(); pg.wait_for_selector('.o_sb_rail_item'); pg.wait_for_timeout(800)
    check(pg.locator('.o_sb_rail_item.active', has_text='Customers').count() == 1 or pg.locator('.o_sb_nav').count() == 1,
          'breadcrumb returned to the navigation center')
    check(not errs, 'no browser errors %s' % errs[:2])
    br.close()
print('RESULT', 'ALL PASS (%d checks)' % len(results) if all(results) else '%d FAILED of %d' % (results.count(False), len(results)))
