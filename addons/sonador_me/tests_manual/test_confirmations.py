"""Approval confirmations engine, as normal users (rolls back). Run: ~/ecogreen-odoo19/sync/local_shell.sh < this file"""
import base64, io, random
from PIL import Image, ImageDraw
from odoo.exceptions import UserError
from odoo.addons.sonador_me.models.confirm import ConfirmationRequired

ok = []
def check(cond, msg):
    assert cond, msg
    ok.append(msg)

def drawn():
    im = Image.new('RGBA', (600, 200), (255, 255, 255, 0))
    d = ImageDraw.Draw(im)
    pts = [(random.randint(10, 590), random.randint(10, 190)) for _ in range(40)]
    d.line(pts, fill=(0, 0, 139, 255), width=3)
    b = io.BytesIO(); im.save(b, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(b.getvalue()).decode()

try:
    company = env.company
    grp = env.ref('base.group_user')
    boss = env['res.users'].with_context(no_reset_password=True).create({
        'login': 't_cboss@x', 'name': 'Confirm Boss', 'password': 'Test-pass-4711', 'company_id': company.id,
        'company_ids': [(6, 0, [company.id])],
        'group_ids': [(6, 0, [grp.id, env.ref('purchase.group_purchase_manager').id, env.ref('sonador_purchase.group_sb_po_gm').id])]})
    check(not boss._is_admin(), 'tester is not an administrator')
    text = env['sb.confirm.text'].create({'name': 'Purchase: Department and Finance', 'code': 't_po', 'step_no': 7,
                                          'body': 'By ticking this box and continuing, I confirm that:\n- the need is right.', 'enabled': True})
    point = env['sb.confirm.point'].create({'name': 'Approve purchase order', 'text_id': text.id, 'model': 'purchase.order',
                                           'method': 'button_approve', 'amount_field': 'amount_total'})
    company.sb_confirm_on = False
    partner = env['res.partner'].create({'name': 'Conf Supplier'})
    product = env['product.product'].search([('purchase_ok', '=', True)], limit=1)
    def mkpo(price):
        po = env['purchase.order'].create({'partner_id': partner.id, 'order_line': [(0, 0, {'product_id': product.id, 'product_qty': 1, 'price_unit': price})]})
        po.write({'state': 'to approve'})
        return po
    B = lambda: env['purchase.order'].with_user(boss).with_context(sb_confirm_force=True)
    po = mkpo(100)
    B().browse(po.id).button_approve()
    check(po.state == 'purchase', 'company switch off: approval goes through with nothing asked')

    company.sb_confirm_on = True
    po = mkpo(100)
    try:
        B().browse(po.id).button_approve()
        check(False, 'asked')
    except ConfirmationRequired as e:
        payload = e.args[1]
    check(po.state == 'to approve', 'company switch on: the approval waits for the confirmation')
    check(payload['title'] == text.name and payload['version'] == 1 and payload['text'] == text.body, 'the step text and version are shown')
    check(payload['replay']['method'] == 'button_approve' and payload['replay']['args'][0] == [po.id], 'the call can be run again after confirming')
    check(not payload['needs_password'] and not payload['needs_phrase'], 'tick-only step')
    C = env['sb.confirmation'].with_user(boss)
    for bad, why in (((payload['token'], False, '', '', drawn()), 'no tick'), ((payload['token'], True, '', '', False), 'no signature')):
        try:
            C.accept(*bad); check(False, why)
        except UserError:
            check(True, 'refused: %s' % why)
    # a forged token
    try:
        C.accept(payload['token'][:-3] + 'abc', True, '', '', drawn()); check(False, 'forged')
    except UserError:
        check(True, 'refused: forged token')
    C.accept(payload['token'], True, '', '', drawn())
    B().browse(po.id).button_approve()
    check(po.state == 'purchase', 'after ticking and signing, the approval goes through')
    conf = env['sb.confirmation'].search([('res_model', '=', 'purchase.order'), ('res_id', '=', po.id)])
    check(len(conf) == 1 and conf.user_id == boss and conf.user_name == 'Confirm Boss' and conf.doc_number and conf.doc_type, 'confirmation recorded with user, name and document')
    check(conf.text_copy == text.body and conf.text_version == 1 and conf.action == 'Approve purchase order', 'exact text, version and action stored')
    check(conf.signature and conf.seal and conf.seal_ok, 'signature stored with a SHA-256 seal that matches')
    for fn, why in ((lambda: conf.sudo().write({'user_name': 'x'}), 'write'), (lambda: conf.sudo().unlink(), 'unlink')):
        try:
            fn(); check(False, why)
        except UserError:
            check(True, 'ORM refuses %s, even with sudo' % why)
    try:
        with env.cr.savepoint():
            env.cr.execute("UPDATE sb_confirmation SET user_name = 'x' WHERE id = %s", [conf.id])
        check(False, 'sql update')
    except Exception:
        check(True, 'database refuses a direct UPDATE')
    try:
        with env.cr.savepoint():
            env.cr.execute("DELETE FROM sb_confirmation WHERE id = %s", [conf.id])
        check(False, 'sql delete')
    except Exception:
        check(True, 'database refuses a direct DELETE')

    # a new version must be confirmed again
    text.body = text.body + '\n- the budget allows it.'
    check(text.version == 2 and len(text.version_ids) == 2, 'changing the text makes version 2 and keeps version 1')
    po2 = mkpo(100)
    try:
        B().browse(po2.id).button_approve(); check(False, 'v2')
    except ConfirmationRequired as e:
        check(e.args[1]['version'] == 2, 'the new version is asked')
        # signature reuse within the session: none in a shell (no session), so it is not offered
        check(e.args[1]['reuse_signature'] is False, 'no earlier signature offered without a login session')

    # phrase above the amount, password step
    text.write({'control': 'phrase'})
    company.sb_confirm_amount = 1000
    po3 = mkpo(5000)
    try:
        B().browse(po3.id).button_approve(); check(False, 'phrase')
    except ConfirmationRequired as e:
        p3 = e.args[1]
    check(p3['needs_phrase'], 'typed phrase asked above the company amount')
    try:
        C.accept(p3['token'], True, 'yes', '', drawn()); check(False, 'phrase wrong')
    except UserError:
        check(True, 'wrong phrase refused')
    C.accept(p3['token'], True, 'i confirm', '', drawn())
    B().browse(po3.id).button_approve()
    check(po3.state == 'purchase', 'phrase typed: goes through')
    check(env['sb.confirmation'].search([('res_id', '=', po3.id), ('res_model', '=', 'purchase.order')]).phrase_typed == 'i confirm', 'phrase stored')
    text.write({'control': 'password'})
    po4 = mkpo(100)
    try:
        B().browse(po4.id).button_approve(); check(False, 'pw')
    except ConfirmationRequired as e:
        p4 = e.args[1]
    check(p4['needs_password'], 'password asked for the general manager step')
    try:
        C.accept(p4['token'], True, '', 'wrong', drawn()); check(False, 'pw wrong')
    except UserError:
        check(True, 'wrong password refused')
    try:
        C.accept(p4['token'], True, '', 'Test-pass-4711', drawn(), channel='telegram'); check(False, 'pw telegram')
    except UserError:
        check(True, 'password steps are refused in Telegram (approve on the web)')
    C.accept(p4['token'], True, '', 'Test-pass-4711', drawn())
    check(env['sb.confirmation'].search([('res_id', '=', po4.id), ('res_model', '=', 'purchase.order')]).password_checked, 'password re-entry recorded')
    # the text switched off: nothing asked
    text.enabled = False
    po5 = mkpo(100)
    B().browse(po5.id).button_approve()
    check(po5.state == 'purchase', 'text switched off: nothing asked')
    text.enabled = True
    text.control = 'tick'
    # through Me: the same text comes back to the approval center
    po6 = mkpo(100)
    try:
        env['sb.me'].with_user(boss).with_context(sb_confirm_force=True).decide('po:%d' % po6.id, True, '')
        check(False, 'me')
    except ConfirmationRequired as e:
        check(e.args[1]['title'] == text.name, 'Me shows the same text before approving')
    print('\n'.join('OK  ' + m for m in ok))
    print('ALL PASSED', len(ok))
finally:
    env.cr.rollback()
