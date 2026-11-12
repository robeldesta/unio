{
    'name': 'Unio digest',
    'version': '19.0.0.1.0',
    'summary': 'The periodic digest email as Unio: no Odoo name, tips or app adverts; the figures a business reads '
               '(sales, money in and out, approvals, customers billed) from Unio\'s own vouchers',
    'author': 'Sonador Systems',
    'category': 'Hidden',
    'license': 'LGPL-3',
    'depends': ['digest', 'sonador_books'],
    'data': ['data/digest.xml'],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'auto_install': True,
}
