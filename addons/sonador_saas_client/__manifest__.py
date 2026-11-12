{
    'name': 'Cloud Subscription Status',
    'version': '19.0.0.1.0',
    'summary': 'Inside a cloud customer system: the trial banner, read-only mode when the trial ends or the subscription lapses, '
               'and the Apps screen (the apps the company uses, preset by its kind of business)',
    'author': 'Sonador Systems',
    'category': 'Hidden',
    'license': 'LGPL-3',
    'depends': ['web'],
    'data': ['views/apps.xml'],
    'assets': {'web.assets_backend': ['sonador_saas_client/static/src/*']},
    'installable': True,
    'auto_install': False,
}
