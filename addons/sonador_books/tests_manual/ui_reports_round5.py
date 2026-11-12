import sys, pathlib
from playwright.sync_api import sync_playwright
out = pathlib.Path(sys.argv[1]); B='http://localhost:8369'
def check(c,m): print(('PASS ' if c else 'FAIL ')+m, flush=True)
with sync_playwright() as p:
    br=p.chromium.launch(); ctx=br.new_context(viewport={'width':1440,'height':900}); pg=ctx.new_page(); pg.set_default_timeout(60000)
    errs=[]; pg.on('pageerror', lambda e: errs.append(str(e)[:300]))
    pg.goto(B+'/web/login?db=ecogreen'); pg.fill('input[name=login]','r5.accountant'); pg.fill('input[name=password]','r5.accountant-R5test')
    pg.click('button[type=submit]'); pg.wait_for_url(lambda u: '/web/login' not in u, timeout=180000)
    pg.goto(B+'/odoo/action-sonador_books.action_sb_rep_tb'); pg.locator('.o_sb_rep_table:visible').first.wait_for(timeout=180000)
    pg.evaluate("Object.keys(localStorage).filter(k => k.startsWith('sonador_books.')).forEach(k => localStorage.removeItem(k))")
    # an old stored GL view with a drill's account in it (as round 4 left it)
    pg.evaluate("localStorage.setItem('sonador_books.report.general_ledger', JSON.stringify({options:{account_ids:[999999], exclude_closing_since:'2026-07-08'}, ui:{mode:'month'}}))")
    pg.goto(B+'/odoo/action-sonador_books.action_sb_rep_tb'); pane='.o_sb_ws_pane:not(.d-none) '
    pg.locator('.o_sb_rep_table:visible').first.wait_for(timeout=180000); pg.wait_for_timeout(1500)
    sel = pane if pg.locator(pane+'.o_sb_rep_table').count() else ''
    # TB fits at 1440: no horizontal scroll in the table wrapper
    w = pg.evaluate("(() => { const w=document.querySelector('%s.o_sb_rep_tablewrap'); return [w.scrollWidth, w.clientWidth]; })()" % sel)
    pg.screenshot(path=str(out/'tb_1440.png'))
    check(w[0] <= w[1] + 1, 'TB fits at 1440 px (table %d px in %d px)' % tuple(w))
    row = pg.locator(sel+'tr', has_text='1104-0002').first
    row.locator('.o_sb_rep_numlink').first.click(); pg.wait_for_timeout(3000)
    chips = pg.locator('.o_sb_filter_chip').all_inner_texts()
    check(any('1104-0002' in c for c in chips), 'drilled GL shows its account as a removable chip: %s' % chips)
    pg.screenshot(path=str(out/'gl_drill_chip.png'))
    # GL fresh from the menu
    pg.goto(B+'/odoo/action-sonador_books.action_sb_rep_gl'); pg.wait_for_timeout(4000)
    pg.locator('.o_sb_rep_table:visible').first.wait_for(timeout=180000)
    groups = pg.locator('.o_sb_ws_pane:not(.d-none) .o_sb_rep_group, .o_sb_rep_group').count()
    chips2 = pg.locator('.o_sb_ws_pane:not(.d-none) .o_sb_filter_chip').all_inner_texts()
    check(groups > 5 and not chips2, 'GL from the menu shows every account (%d accounts), no drill filter left (chips %s)' % (groups, chips2))
    stored = pg.evaluate("localStorage.getItem('sonador_books.report.general_ledger')")
    check('account_ids' not in (stored or ''), 'stored GL options hold no account/partner filter')
    # account picker
    pg.locator('.o_sb_ws_pane:not(.d-none) button:has-text("Filters")').first.click(); pg.wait_for_timeout(500)
    inp = pg.locator('.o_sb_rep_pop input[placeholder="Account number or name"]').first
    inp.fill('1104-0002'); pg.wait_for_timeout(1500)
    pg.screenshot(path=str(out/'gl_picker.png'))
    pg.locator('.o_sb_pick_list a').first.click(); pg.wait_for_timeout(3000)
    g2 = pg.locator('.o_sb_ws_pane:not(.d-none) .o_sb_rep_group').count()
    badge = pg.locator('.o_sb_ws_pane:not(.d-none) .badge:has-text("on")').count()
    check(g2 == 1 and badge >= 1, 'account picker narrows the GL to one account (%d) and Filters says on' % g2)
    pg.locator('.o_sb_ws_pane:not(.d-none) .o_sb_filter_chip .fa-times').first.click(); pg.wait_for_timeout(3000)
    check(pg.locator('.o_sb_ws_pane:not(.d-none) .o_sb_rep_group').count() > 5, 'removing the chip shows all accounts again')
    # search on an entry's partner
    pg.locator('.o_sb_ws_pane:not(.d-none) .o_sb_search input').first.fill('Ethio'); pg.wait_for_timeout(3500)
    lab = pg.locator('.o_sb_ws_pane:not(.d-none) .o_sb_rep_group').all_inner_texts()
    check(len(lab) >= 1, 'search finds accounts through their entries (partner): %s' % [l.split('\n')[0][:40] for l in lab[:4]])
    pg.screenshot(path=str(out/'gl_search.png'))
    check(not errs, 'no JS errors %s' % errs[:2])
    br.close()
