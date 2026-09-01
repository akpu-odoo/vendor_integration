{
    'name': 'MiDocean Integration',
    'version': '19.0.1.0.1',
    'author': 'Odoo PS',
    'summary': 'Synchronize MiDocean products, stock, prices, and purchase orders',
    'description': """
        Extends the generic vendor integration with MiDocean catalogue variants,
        assets, stock, supplier prices, and purchase order exchange.
    """,
    'license': 'LGPL-3',
    'depends': ['vendor_integration'],
    'data': [
        'security/ir.model.access.csv',
        'data/midocean_demo.xml',
        'data/midocean_print_api_data.xml',
        'views/product_views.xml',
        'views/purchase_order_views.xml',
        'views/midocean_menu.xml',
        'views/midocean_print_data_views.xml',
        'views/midocean_print_pricelist_views.xml',
    ],
    'demo': ['data/midocean_demo.xml'],
}
