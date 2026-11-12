from . import models


def post_init_hook(env):
    """Me opens first for everyone who has no other home screen set."""
    action = env.ref('sonador_me.action_me', raise_if_not_found=False)
    if action:
        env['res.users'].sudo().search([('share', '=', False), ('action_id', '=', False)]).write({'action_id': action.id})
