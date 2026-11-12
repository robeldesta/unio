# CSI0000108 was edited to fiscal receipt CSI0000109 before the fix: post it again so it takes the number
v = env['sb.voucher'].search([('name', '=', 'CSI0000108'), ('sb_pad_number', '=', 'CSI0000109'), ('state', '=', 'posted')])
if v:
    v = v.with_user(env['res.users'].search([('login', '=', 'robeldesta@gmail.com')], limit=1))
    v.action_edit()
    v.action_post()
    env.cr.commit()
print('> renamed:', v.name if v else 'nothing to rename')
