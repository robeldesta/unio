{
    'name': 'Unio',
    'version': '19.0.0.1.0',
    'summary': 'The Unio look everywhere Odoo showed itself: tab icon and title, login, loading animation, "Powered by" in '
               'emails and the portal, phone-app icon; the company keeps its own logo wherever it set one',
    'author': 'Sonador Systems',
    'category': 'Hidden',
    'license': 'LGPL-3',
    'depends': ['web', 'mail', 'portal', 'auth_signup', 'sonador_saas_client'],
    'data': ['views/branding.xml', 'data/data.xml'],
    'assets': {
        'web.assets_backend': ['sonador_unio_brand/static/src/*'],
        'web.assets_frontend': ['sonador_unio_brand/static/src/loader.scss'],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'auto_install': False,
}
