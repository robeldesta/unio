{
    'name': 'App order per person',
    'version': '19.0.0.1.0',
    'summary': 'Each person puts their apps in their own order: drag an app up or down in the left bar; the apps grid '
               'follows. Kept per person, on every device.',
    'author': 'Sonador Systems',
    'category': 'Hidden',
    'license': 'LGPL-3',
    'depends': ['mail', 'muk_web_appsbar'],
    'assets': {
        'web.assets_backend': ['sonador_app_order/static/src/*'],
    },
    'installable': True,
    'auto_install': True,
}
