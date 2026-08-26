import json

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class FieldMapping(models.Model):
    _name = 'field.mapping'
    _description = 'Vendor API Field Mapping'

    vendor_api_id = fields.Many2one('vendor.api', required=True, ondelete='cascade')
    res_model_id = fields.Many2one(related='vendor_api_id.res_model_id', store=True, readonly=True)
    response_key = fields.Char(required=True, string='JSON Key', help='Dotted paths are supported, e.g. attributes.product_title.en_GB.')
    odoo_field_id = fields.Many2one('ir.model.fields', string='Odoo Field', domain="[('model_id', '=', res_model_id), ('ttype', 'not in', ('one2many', 'many2many'))]")
    field_name = fields.Char(related='odoo_field_id.name')
    default_value = fields.Char(help='Used only when the JSON key is absent.')
    selection_values = fields.Text(string='Selection Value Map', help='Optional JSON map, e.g. {"yes": "available"}.')

    @api.constrains('selection_values')
    def _check_selection_values(self):
        for mapping in self.filtered('selection_values'):
            try:
                if not isinstance(json.loads(mapping.selection_values), dict):
                    raise ValueError()
            except (TypeError, ValueError):
                raise ValidationError(self.env._('Selection Value Map must be a JSON object.'))

    def get_value(self, source):
        self.ensure_one()
        missing = object()
        value = self.vendor_api_id._json_value(source, self.response_key, missing)
        if value is missing:
            value = self.default_value if self.default_value is not False else None
        if self.selection_values and value is not None:
            value = json.loads(self.selection_values).get(str(value), value)
        return value
