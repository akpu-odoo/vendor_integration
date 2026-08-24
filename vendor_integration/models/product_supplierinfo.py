from odoo import fields, models


class ProductSupplierinfo(models.Model):
    _inherit = 'product.supplierinfo'

    source_api_id = fields.Many2one('vendor.api')
    external_record_id = fields.Char('')