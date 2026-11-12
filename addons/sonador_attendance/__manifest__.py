{
    'name': 'Attendance',
    'version': '19.0.0.1.0',
    'summary': 'Clock in and out from the browser or phone (with location and work sites), ZKTeco fingerprint/face devices, '
               'timesheets, overtime approval feeding payroll, lateness and absence under the Labour Proclamation 1156/2019',
    'author': 'Sonador Systems',
    'category': 'Human Resources',
    'license': 'LGPL-3',
    'depends': ['sonador_people', 'sonador_timeoff', 'sonador_payroll'],
    'data': [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'data/cron.xml',
        'views/menus.xml',
    ],
    'assets': {'web.assets_backend': ['sonador_attendance/static/src/att/*']},
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
}
