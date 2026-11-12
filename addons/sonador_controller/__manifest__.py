{
    'name': 'Controller',
    'version': '19.0.0.1.0',
    'summary': 'Finance controller: checks the books every night (cost of sales against sales, stock against the ledger, '
               'overdue and slower-paying customers, duplicate supplier bills) and lists what needs attention, with the '
               'records behind each finding. Read-only: it never changes the books.',
    'author': 'Sonador Systems',
    'category': 'Accounting',
    'license': 'LGPL-3',
    'depends': ['sonador_books', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'security/rules.xml',
        'data/cron.xml',
        'views/finding_views.xml',
        'views/company_views.xml',
        'views/menus.xml',
    ],
    'assets': {'web.assets_backend': ['sonador_controller/static/src/ctl/*']},
    'installable': True,
    'application': True,
}
