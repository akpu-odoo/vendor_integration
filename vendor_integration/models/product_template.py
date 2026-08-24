from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    source_api_id = fields.Many2one('vendor.api')
    external_record_id = fields.Char('')