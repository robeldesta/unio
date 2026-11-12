{
    'name': 'Guided Tours',
    'version': '19.0.0.1.0',
    'summary': 'Step-by-step tours that walk people through doing the real thing in each app (first invoice, receipt, '
               'quotation to delivery, purchase to bill, stock count, payroll, CRM, projects, printing, Studio)',
    'author': 'Sonador Systems',
    'category': 'Hidden',
    'license': 'LGPL-3',
    'depends': ['web'],
    'data': ['views/menu.xml'],
    'assets': {'web.assets_backend': ['sonador_tours/static/src/*']},
    'installable': True,
}
