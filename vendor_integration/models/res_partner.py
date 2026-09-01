from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    external_vendor_id = fields.Many2one('external.vendor', ondelete='set null')
