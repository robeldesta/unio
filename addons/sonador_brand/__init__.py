import base64

from odoo.tools import file_open

from . import models


def post_init_hook(env):
    """Rename Odoo's bot to AutoBot: its contact, its avatar, and what it already said."""
    p = env.ref('base.partner_root')
    with file_open('sonador_brand/static/img/autobot.svg', 'rb') as f:
        p.write({'name': 'AutoBot', 'image_1920': base64.b64encode(f.read()),
                 'email': (p.email or '').replace('odoobot', 'autobot') or False})
    env.cr.execute("UPDATE mail_message SET body = replace(body::text, 'OdooBot', 'AutoBot')::text WHERE body::text LIKE '%%OdooBot%%'")
    env.cr.execute("UPDATE mail_message SET email_from = replace(replace(email_from, 'OdooBot', 'AutoBot'), 'odoobot@', 'autobot@') "
                   "WHERE email_from ILIKE '%%odoobot%%'")
