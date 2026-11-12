{
    'name': 'Report designer',
    'version': '19.0.0.2.3',
    'summary': 'Weekly, monthly, quarterly, half-yearly and yearly reports built by each manager, each open at its time: a template per role, the person\'s own words under each '
               'section, the plan check-ins, and report blocks dragged in and filled from the books, sales, store, '
               'purchase and people records. Sent to the manager frozen; exported to PDF, Excel and PowerPoint.',
    'author': 'Sonador Systems',
    'category': 'Productivity',
    'license': 'LGPL-3',
    'depends': ['sonador_planning', 'sonador_books', 'sonador_inventory', 'sonador_sales'],
    'data': [
        'security/ir.model.access.csv',
        'data/templates.xml',
        'report/report.xml',
        'views/menus.xml',
    ],
    'assets': {'web.assets_backend': ['sonador_report_designer/static/src/*']},
    'installable': True,
    'application': False,
}
