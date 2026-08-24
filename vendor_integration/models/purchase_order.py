from odoo import fields, models


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    source_api_id = fields.Many2one('vendor.api')
    external_record_id = fields.Char('')
