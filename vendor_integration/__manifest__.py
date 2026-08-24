{
    "name": "Multiple Vendor Integration",
    "version": "19.0.1.0.0",
    "summary": "Module provides feasibility to integrate multiple vendors",
    "description": """
        Vendor Integration - 6451862
        =========================================
        This module allows users to create records for integrating multiple vendors.
    """,
    "category": "Custom",
    # Author
    "author": "Odoo PS",
    "website": "https://www.odoo.com",
    "license": "LGPL-3",
    # Dependency
    "depends": ["base_auth", "purchase"],
    "data": [
        "security/ir.model.access.csv",
        "views/external_vendor_views.xml",
        "views/vendor_api_views.xml",
        "views/field_mapping_views.xml",
        "views/res_partner_views.xml",
        "views/product_template_views.xml",
        "views/product_supplierinfo_views.xml",
        "views/purchase_order_views.xml",
        "views/vendor_integration_menus.xml",
        "data/vendor_integration_cron.xml",
    ],
}
