{
    'name': 'People Insights',
    'version': '19.0.0.1.0',
    'summary': 'HR analytics (headcount, turnover, tenure, payroll cost, overtime, leave, performance, recruitment) and '
               'anonymous pulse / eNPS surveys',
    'author': 'Sonador Systems',
    'category': 'Human Resources',
    'license': 'LGPL-3',
    'depends': ['sonador_recruit', 'sonador_performance'],
    'data': ['security/ir.model.access.csv', 'views/menus.xml'],
    'assets': {'web.assets_backend': ['sonador_insights/static/src/ins/*']},
    'installable': True,
    'application': False,
}
