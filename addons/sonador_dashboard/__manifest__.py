{
    'name': 'Dashboard',
    'version': '19.0.0.1.0',
    'summary': 'A window into the business for each person, within what their role may see: a short briefing, what '
               'waits for them (act on it there), what looks wrong and why, a question box, then the numbers',
    'author': 'Sonador Systems',
    'category': 'Productivity',
    'license': 'LGPL-3',
    'depends': ['sonador_me', 'sonador_books'],
    'data': ['security/ir.model.access.csv', 'security/rules.xml', 'views/views.xml'],
    'assets': {'web.assets_backend': ['sonador_dashboard/static/src/*']},
    'installable': True,
    'application': True,
}
