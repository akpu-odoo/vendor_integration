from odoo import fields, models


class VendorProductStock(models.Model):
    _name = 'vendor.product.stock'
    _description = 'Vendor Product Stock'
    _rec_name = 'sku'

    vendor_api_id = fields.Many2one('vendor.api', ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', ondelete='cascade', index=True)
    sku = fields.Char(required=True, index=True)
    quantity = fields.Integer()
    first_arrival_date = fields.Date()
    first_arrival_qty = fields.Integer()

    _stock_unique = models.Constraint('unique(vendor_api_id, sku)', 'A SKU can only have one stock record per API.')
