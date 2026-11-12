{
    'name': 'Requests',
    'version': '19.0.0.1.0',
    'summary': 'Payment, petty cash, driver per diem and purchase requests with department, finance and general manager '
               'approval; paid by a payment voucher or ordered by a purchase order in one click',
    'description': """
Requests
========
Staff ask for money or goods; the department head approves, finance checks (and codes the accounts: the payment
order), and the general manager gives the final approval above the company's limit. Then the cashier makes the payment
voucher (or petty cash voucher) from the request in one click, and the request shows paid or partly paid; a purchase
request becomes a request for quotation. Every step, send back (with the reason) and cancellation is in the chatter,
and everything waiting for someone is in their approvals inbox in Me.
""",
    'author': 'Sonador Systems',
    'category': 'Accounting',
    'license': 'LGPL-3',
    'depends': ['sonador_books', 'sonador_people', 'sonador_me', 'sonador_purchase', 'sonador_fleet'],
    'data': [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'security/rules.xml',
        'data/sequence.xml',
        'views/request_views.xml',
        'views/purchase_request_views.xml',
        'views/config_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': True,
}
