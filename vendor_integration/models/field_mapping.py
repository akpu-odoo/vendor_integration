import json

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class FieldMapping(models.Model):
    _name = 'field.mapping'
    _description = "Allow user to map field with Response Key i.e, Json Key -> Odoo field"

    res_model_id = fields.Many2one('ir.model', related='vendor_api_id.res_model_id')
    vendor_api_id = fields.Many2one('vendor.api', required=True)

    response_key = fields.Char(
        help="Key in one response object. A dotted path such as 'details.code' is supported.",
    )
    default_value = fields.Char(
        help="Value used when the response does not contain this key. Use this for fixed values, including many2one record IDs.",
    )
    selection_values = fields.Text(
        string='Selection Value Map',
        help='Optional JSON object translating vendor values to Odoo selection values, for example {"active": "enabled"}.',
    )
    odoo_field_id = fields.Many2one(
        'ir.model.fields',
        domain="[('model_id', '=', res_model_id), ('ttype', 'not in', ('one2many', 'many2many'))]",
    )
    field_name = fields.Char(related='odoo_field_id.name')

    @api.constrains('response_key', 'default_value')
    def _check_source_or_default(self):
        for mapping in self:
            if not mapping.response_key and mapping.default_value is False:
                raise ValidationError(self.env._('Set a response key or a default value.'))

    @api.constrains('selection_values', 'odoo_field_id')
    def _check_selection_values(self):
        for mapping in self:
            if not mapping.selection_values:
                continue
            try:
                value_map = json.loads(mapping.selection_values)
            except (TypeError, ValueError) as error:
                raise ValidationError(self.env._('Selection Value Map must be valid JSON: %(error)s', error=error))
            if not isinstance(value_map, dict):
                raise ValidationError(self.env._('Selection Value Map must be a JSON object.'))
            if mapping.odoo_field_id.ttype != 'selection':
                raise ValidationError(self.env._('A Selection Value Map can only be used with a selection field.'))
            valid_values = mapping._selection_values()
            invalid_values = [value for value in value_map.values() if value not in valid_values]
            if invalid_values:
                raise ValidationError(self.env._('Unknown Odoo selection value(s): %(values)s', values=', '.join(map(str, invalid_values))))

    def _selection_values(self):
        self.ensure_one()
        model = self.env[self.vendor_api_id.res_model_id.model]
        field = model._fields[self.odoo_field_id.name]
        return {value for value, _label in field._description_selection(self.env)}

    def get_value(self, response_object):
        """Return this mapping's value for one resolved response object."""
        self.ensure_one()
        missing = object()
        value = self.vendor_api_id._resolve_path(response_object, self.response_key, missing)
        if value is missing:
            if self.default_value is False:
                return None
            value = self.default_value
        if self.selection_values and value is not None:
            value = json.loads(self.selection_values).get(str(value), value)
        return value
