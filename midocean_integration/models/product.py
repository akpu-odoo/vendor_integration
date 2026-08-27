from odoo import fields, models


class ProductProduct(models.Model):
    _inherit = 'product.product'

    vendor_stock_ids = fields.One2many('vendor.product.stock', 'product_id', string='Vendor Stock')
    vendor_asset_ids = fields.One2many('vendor.product.asset', 'product_id', string='Vendor Assets')


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    vendor_asset_ids = fields.One2many('vendor.product.asset', 'product_tmpl_id', string='Vendor Assets')


class ProductSupplierinfo(models.Model):
    _inherit = 'product.supplierinfo'

    vendor_api_id = fields.Many2one('vendor.api', ondelete='cascade', index=True)
    _vendor_product_unique = models.Constraint('unique(vendor_api_id, product_id)', 'A vendor API can only have one supplier price per product.')
