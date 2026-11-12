"""Sonador, 9 Nov 2026 (Robel): drop the default "Bank" account 1104-0003 (it was used for Chapa); everything on it moves to
1102-0001 Bank Suspense Account, the "Bank" journal and the account are archived. Safe to repeat."""
env = env(context=dict(env.context, tracking_disable=True))
A = env['account.account'].with_context(active_test=False)


def acc(code):
    a = A.search([('code', '=', code), ('company_ids', 'in', env.company.id)], limit=1)
    assert a, code
    return a


old, new, petty = acc('1104-0003'), acc('1102-0001'), acc('1101-0002')
cr = env.cr
before = round(sum(env['account.move.line'].search([('account_id', '=', old.id), ('parent_state', '=', 'posted')]).mapped('balance')), 2)
cr.execute('UPDATE account_move_line SET account_id=%s WHERE account_id=%s', (new.id, old.id))
lines = cr.rowcount
cr.execute('UPDATE sb_voucher_line SET account_id=%s WHERE account_id=%s', (new.id, old.id))
cr.execute("UPDATE sb_voucher SET cash_account_id=%s WHERE cash_account_id=%s AND voucher_type='transfer'", (new.id, old.id))
cr.execute('UPDATE sb_voucher SET cash_account_id=%s WHERE cash_account_id=%s', (petty.id, old.id))
for t, c in (('sb_voucher', 'cash_account_to_id'),):
    cr.execute('UPDATE %s SET %s=%%s WHERE %s=%%s' % (t, c, c), (new.id, old.id))
cr.execute('''SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='public' AND data_type='integer'
              AND column_name LIKE '%%account_id' AND table_name NOT IN ('account_account_res_company_rel', 'account_journal')''')
for t, c in cr.fetchall():
    cr.execute('SELECT count(*) FROM "%s" WHERE "%s"=%%s' % (t, c), (old.id,))
    n = cr.fetchone()[0]
    assert not n, 'still used: %s.%s (%s)' % (t, c, n)
env.invalidate_all()
bank = env['account.journal'].with_context(active_test=False).search([('default_account_id', '=', old.id)])
assert not env['account.move'].search_count([('journal_id', 'in', bank.ids)]), 'the Bank journal has entries'
bank.write({'active': False})
old.write({'active': False})
after = round(sum(env['account.move.line'].search([('account_id', '=', new.id), ('parent_state', '=', 'posted')]).mapped('balance')), 2)
env.cr.commit()
print('> moved %s lines (%s) to %s %s; balance there now %s' % (lines, '{:,.2f}'.format(before), new.code, new.name, '{:,.2f}'.format(after)))
print('> archived: %s %s and journal %s' % (old.code, old.name, ', '.join(bank.mapped('name')) or '-'))
