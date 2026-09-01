from odoo import fields, models


class MidoceanPrintPricelist(models.Model):
    _name = 'midocean.print.pricelist'
    _description = 'MiDocean Print Pricelist'
    _rec_name = 'vendor_id'

    vendor_id = fields.Many2one('external.vendor', required=True, index=True)
    currency_id = fields.Many2one('res.currency')
    valid_from = fields.Date()
    valid_until = fields.Date()
    manipulation_ids = fields.One2many('midocean.print.manipulation', 'pricelist_id')
    technique_ids = fields.One2many('midocean.print.technique', 'pricelist_id')

    _vendor_pricelist_unique = models.Constraint(
        'unique(vendor_id)', 'There can only be one current print pricelist per vendor.',
    )


class MidoceanPrintManipulation(models.Model):
    _name = 'midocean.print.manipulation'
    _description = 'MiDocean Print Manipulation'
    _rec_name = 'code'

    pricelist_id = fields.Many2one('midocean.print.pricelist', required=True, ondelete='cascade', index=True)
    active = fields.Boolean(default=True)
    code = fields.Char(required=True)
    description = fields.Char()
    price = fields.Float()

    _pricelist_manipulation_unique = models.Constraint(
        'unique(pricelist_id, code)', 'A manipulation code must be unique per pricelist.',
    )


class MidoceanPrintVariableCost(models.Model):
    _name = 'midocean.print.variable.cost'
    _description = 'MiDocean Print Variable Cost'

    technique_id = fields.Many2one('midocean.print.technique', required=True, ondelete='cascade', index=True)
    active = fields.Boolean(default=True)
    source_key = fields.Char(required=True, index=True)
    range_id = fields.Char()
    area_from = fields.Float()
    area_to = fields.Float()
    scale_ids = fields.One2many('midocean.print.price.scale', 'variable_cost_id')

    _technique_source_key_unique = models.Constraint(
        'unique(technique_id, source_key)',
        'A variable cost can only occur once per technique.',
    )


class MidoceanPrintPriceScale(models.Model):
    _name = 'midocean.print.price.scale'
    _description = 'MiDocean Print Price Scale'
    _order = 'minimum_quantity, id'

    variable_cost_id = fields.Many2one('midocean.print.variable.cost', required=True, ondelete='cascade', index=True)
    active = fields.Boolean(default=True)
    source_key = fields.Char(required=True, index=True)
    minimum_quantity = fields.Float(required=True)
    price = fields.Float()
    next_price = fields.Float()

    _variable_cost_source_key_unique = models.Constraint(
        'unique(variable_cost_id, source_key)',
        'A price scale can only occur once per variable cost.',
    )
