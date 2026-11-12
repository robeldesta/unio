{
    'name': 'Studio',
    'version': '19.0.0.1.0',
    'summary': 'Build apps and change screens without code: new apps and records, fields and formulas, drag-and-drop forms, '
               'lists, boards and calendars, automations, version history with undo, export as a module',
    'author': 'Sonador Systems',
    'category': 'Productivity',
    'license': 'LGPL-3',
    'depends': ['mail', 'base_automation'],
    'data': ['security/ir.model.access.csv', 'views/views.xml'],
    'assets': {'web.assets_backend': ['sonador_studio/static/src/studio/*']},
    'installable': True,
    'application': True,
}
