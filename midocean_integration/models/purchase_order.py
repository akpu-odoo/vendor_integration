from odoo import fields, models
from odoo.exceptions import UserError


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    midocean_vendor_id = fields.Many2one(related='partner_id.external_vendor_id', readonly=True)
    midocean_order_number = fields.Char(copy=False, readonly=True)
    midocean_response = fields.Json(copy=False, readonly=True)
    midocean_last_error = fields.Char(copy=False, readonly=True)
    midocean_proof_line_id = fields.Char(copy=False)
    midocean_rejection_code = fields.Integer(default=3)
    midocean_rejection_comment = fields.Char()

    def _midocean_api_enabled(self):
        self.ensure_one()
        return self.midocean_vendor_id.vendor_api_ids.filtered(lambda api: api.integration_type == 'midocean')[:1]

    def _midocean_request(self, method, path, payload=None, params=None):
        self.ensure_one()
        if not self._midocean_api_enabled():
            raise UserError(self.env._('This purchase order does not use a MiDocean vendor.'))
        response = self.midocean_vendor_id.authentcation_method_id.request(
            method, path, json=payload, params=params,
            headers={'Accept': 'application/json', 'Content-Type': 'application/json'},
        )
        if not response.success:
            raise UserError('; '.join(error.message for error in response.errors) or self.env._('MiDocean request failed.'))
        return response.data

    def _midocean_order_payload(self):
        self.ensure_one()
        address = self.dest_address_id or self.company_id.partner_id
        lines = self.order_line.filtered(lambda line: not line.display_type and line.product_id)
        if not lines or any(not line.product_id.default_code for line in lines):
            raise UserError(self.env._('Every MiDocean order line needs a product with an internal reference (SKU).'))
        return {
            'order_header': {
                'preferred_shipping_date': fields.Date.to_string(self.date_planned.date()) if self.date_planned else False,
                'check_price': 'false', 'currency': self.currency_id.name, 'contact_email': self.user_id.email or '',
                'shipping_address': {'contact_name': address.name or '', 'company_name': address.commercial_company_name or '',
                    'street1': address.street or '', 'street2': address.street2 or '', 'postal_code': address.zip or '',
                    'city': address.city or '', 'region': address.state_id.code or '', 'country': address.country_id.code or '',
                    'email': address.email or self.user_id.email or '', 'phone': address.phone or ''},
                'po_number': self.name, 'timestamp': fields.Datetime.to_string(fields.Datetime.now()),
                'contact_name': self.user_id.name, 'order_type': 'SAMPLE' if self.midocean_order_type == 'sample' else 'NORMAL',
            },
            'order_lines': [{'order_line_id': str(line.id), 'sku': line.product_id.default_code,
                             'quantity': str(line.product_qty), 'expected_price': str(line.price_unit)} for line in lines],
        }

    midocean_order_type = fields.Selection([('normal', 'Normal'), ('sample', 'Sample')], default='normal')

    def _midocean_number(self, data):
        if isinstance(data, dict):
            for key in ('order_number', 'orderNumber'):
                if data.get(key):
                    return str(data[key])
            for value in data.values():
                number = self._midocean_number(value)
                if number:
                    return number
        if isinstance(data, list):
            for value in data:
                number = self._midocean_number(value)
                if number:
                    return number
        return False

    def action_midocean_create_order(self):
        for order in self:
            if order.midocean_order_number:
                continue
            data = order._midocean_request('post', '/gateway/order/2.1/create', order._midocean_order_payload())
            order.write({'midocean_response': data, 'midocean_order_number': order._midocean_number(data), 'midocean_last_error': False})
        return True

    def action_midocean_refresh_order(self):
        for order in self:
            if not order.midocean_order_number:
                raise UserError(self.env._('Send the purchase order to MiDocean first.'))
            order.write({'midocean_response': order._midocean_request(
                'get', '/gateway/order/2.1/detail', params={'order_number': order.midocean_order_number}), 'midocean_last_error': False})
        return True

    def action_midocean_approve_proof(self):
        for order in self:
            order._midocean_proof('approve', {'order_number': order.midocean_order_number, 'order_line_id': order.midocean_proof_line_id})
        return True

    def action_midocean_reject_proof(self):
        for order in self:
            order._midocean_proof('reject', {'order_number': order.midocean_order_number, 'order_line_id': order.midocean_proof_line_id,
                'rejection_code': order.midocean_rejection_code, 'rejection_comment': order.midocean_rejection_comment or '', 'additional_files': [], 'new_artworks': []})
        return True

    def _midocean_proof(self, action, payload):
        self.ensure_one()
        if not payload['order_number'] or not payload['order_line_id']:
            raise UserError(self.env._('MiDocean order number and proof line ID are required.'))
        self._midocean_request('post', '/gateway/proof/1.0/%s' % action, payload)

    def button_confirm(self):
        result = super().button_confirm()
        self.filtered(lambda order: order._midocean_api_enabled() and not order.midocean_order_number).action_midocean_create_order()
        return result
