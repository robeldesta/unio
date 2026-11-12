{
    'name': 'School',
    'version': '19.0.0.1.0',
    'summary': 'Run a school on its own books: students and admissions, classes and sections, fees billed and collected '
               '(telebirr, bank, Chapa), daily attendance, marks with total, average and rank, Amharic and English report cards',
    'author': 'Sonador Systems',
    'category': 'Education',
    'license': 'LGPL-3',
    'depends': ['sonador_books', 'sonador_people'],
    'data': [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'data/data.xml',
        'views/views.xml',
        'report/report_card.xml',
    ],
    'assets': {'web.assets_backend': ['sonador_school/static/src/*']},
    'installable': True,
    'application': True,
}
