{
    'name': 'Me',
    'version': '19.0.0.3.0',
    'summary': 'Self service in one place: my profile, leave, attendance, pay, goals and tasks, and one inbox for '
               'everything waiting for my approval, whichever app it comes from',
    'author': 'Sonador Systems',
    'category': 'Human Resources',
    'license': 'LGPL-3',
    'depends': ['sonador_people', 'mail', 'base_setup'],
    'data': ['security/groups.xml', 'security/ir.model.access.csv', 'views/views.xml', 'views/config.xml', 'views/confirm.xml', 'views/report.xml', 'data/cron.xml'],
    'assets': {'web.assets_backend': ['sonador_me/static/src/*']},
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': True,
}
