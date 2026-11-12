{
    'name': 'Unio AI',
    'version': '19.0.0.1.0',
    'summary': 'An assistant that knows your company: answers from your own records and reports (with links), drafts '
               'quotations, invoices, tasks and messages for you to approve, explains reports, in English or Amharic',
    'author': 'Sonador Systems',
    'category': 'Productivity',
    'license': 'LGPL-3',
    'depends': ['mail', 'sonador_books', 'sonador_saas_client'],
    'data': ['security/ir.model.access.csv', 'views/views.xml'],
    'assets': {'web.assets_backend': ['sonador_ai/static/src/*']},
    'installable': True,
}
