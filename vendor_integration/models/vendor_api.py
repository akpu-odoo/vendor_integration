import json
import logging
import time
from datetime import timedelta
from urllib.parse import urljoin

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)
SYNC_TIME_LIMIT_SECONDS = 110


class VendorApi(models.Model):
    _name = 'vendor.api'
    _description = "Vendor's all api list"

    name = fields.Char(required=True)
    external_vendor_id = fields.Many2one('external.vendor', required=True)
    api_url = fields.Char(required=True)
    api_method = fields.Selection(selection='_selection_method_type', required=True, default='get')

    resolved_path = fields.Char()
    res_model_id = fields.Many2one('ir.model',)
    model_name = fields.Char(related='res_model_id.model')

    field_mapping_ids = fields.One2many('field.mapping', 'vendor_api_id')
    is_integration_compatible = fields.Boolean(compute="_compute_is_integration_compatible", store=True)
    is_api_ready = fields.Boolean()
    active = fields.Boolean(default=True)
    integration_type = fields.Selection(
        selection=[
            ('generic', 'Generic'), ('midocean', 'MiDocean'), ('araco', 'Araco'),
            ('impliva', 'Impliva'), ('pf_concept', 'PF Concept'), ('toppoint', 'Toppoint'),
            ('promocorp', 'PromoCorp'), ('texet', 'Texet'), ('toptex', 'TopTex'),
            ('xd_connects', 'XD Connects'), ('sols', "Sol's"),
            ('falk_ross', 'Falk&Ross'), ('l_shop', 'L-Shop'),
        ],
        required=True,
        default='generic',
        help='Generic uses UI mappings. A custom module can override the sync hook for a specific type.',
    )
    external_key_path = fields.Char(help='Stable vendor ID path in one response object, for example id or product.code.')
    sync_enabled = fields.Boolean(default=False)
    sync_interval_hours = fields.Integer(default=24)
    sync_batch_size = fields.Integer(default=500)
    sync_cursor = fields.Integer(default=0, readonly=True)
    next_sync_on = fields.Datetime(readonly=True)
    last_sync_on = fields.Datetime(readonly=True)
    last_sync_message = fields.Char(readonly=True)
    last_test_message = fields.Char(readonly=True)
    last_test_record_count = fields.Integer(readonly=True)
    last_tested_on = fields.Datetime(readonly=True)


    @api.model
    def _selection_method_type(self):
        return [
            ("get", "GET"), 
            ("post", "POST"),
            ("delete", "DELETE"),
            ("patch", "PATCH"),
        ]

    @api.constrains('sync_interval_hours', 'sync_batch_size')
    def _check_sync_settings(self):
        for record in self:
            if record.sync_interval_hours <= 0:
                raise ValidationError(self.env._('Sync interval must be greater than zero.'))
            if record.sync_batch_size <= 0:
                raise ValidationError(self.env._('Sync batch size must be greater than zero.'))

    @api.depends(
        'field_mapping_ids.odoo_field_id',
        'field_mapping_ids.response_key',
        'field_mapping_ids.default_value',
        'res_model_id',
    )
    def _compute_is_integration_compatible(self):
        for record in self:
            if not record.res_model_id:
                record.is_integration_compatible = False
                continue
            required_fields = record.res_model_id.field_id.filtered(
                lambda field: field.required
                and field.name != 'id'
                and field.ttype not in ('one2many', 'many2many')
                and not field.readonly
            )
            configured_fields = record.field_mapping_ids.filtered(
                lambda mapping: mapping.response_key or mapping.default_value is not False
            ).mapped('field_name')
            record.is_integration_compatible = all(field.name in configured_fields for field in required_fields)

    @api.model
    def _resolve_path(self, value, path, default=None):
        """Resolve a simple dotted path in a JSON object or list."""
        if not path:
            return value
        current = value
        for part in path.split('.'):
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                current = current[int(part)]
            else:
                return default
        return current

    def _response_records(self, response_data):
        self.ensure_one()
        records = self._resolve_path(response_data, self.resolved_path, None)
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, list):
            return []
        return records

    def _validate_response_object(self, response_object):
        """Validate mappings against one JSON object without creating Odoo records."""
        for mapping in self.field_mapping_ids:
            missing = object()
            source_value = self._resolve_path(response_object, mapping.response_key, missing)
            if mapping.response_key and source_value is missing and mapping.default_value is False:
                raise ValueError(self.env._('Response key %(key)s was not found.', key=mapping.response_key))
            value = mapping.get_value(response_object)
            if mapping.odoo_field_id.ttype == 'selection' and value is not None:
                valid_values = mapping._selection_values()
                if value not in valid_values:
                    raise ValueError(
                        self.env._('%(field)s has unsupported selection value %(value)s.',
                                   field=mapping.odoo_field_id.field_description, value=value)
                    )

    def action_test_api(self):
        self.ensure_one()
        if not self.res_model_id:
            return self._test_result(False, self.env._('Choose the Odoo model that each response object represents.'))
        try:
            records = self._response_records(self._request_response_data())
        except UserError as error:
            return self._test_result(False, str(error))
        if not records:
            path = self.resolved_path or self.env._('(root)')
            return self._test_result(False, self.env._('No record list was found at resolved path %(path)s.', path=path))
        if not all(isinstance(item, dict) for item in records):
            return self._test_result(False, self.env._('The resolved path must return a JSON object or a list of JSON objects.'))
        try:
            self._validate_response_object(records[0])
        except ValueError as error:
            return self._test_result(False, str(error))
        if not self.is_integration_compatible:
            return self._test_result(
                False,
                self.env._('%(count)s record(s) found, but required field mappings are still missing.', count=len(records)),
                len(records),
            )
        return self._test_result(True, self.env._('%(count)s record(s) found and mappings validated.', count=len(records)), len(records))

    def _endpoint_url(self):
        self.ensure_one()
        if not self.external_vendor_id.base_url or not self.api_url:
            raise UserError(self.env._('Set the vendor base URL and API URL before synchronizing.'))
        return urljoin(self.external_vendor_id.base_url.rstrip('/') + '/', self.api_url.lstrip('/'))

    def _request_response_data(self):
        self.ensure_one()
        response = self.external_vendor_id.authentcation_method_id.request(
            self.api_method, self._endpoint_url(),
        )
        if not response.success:
            message = '; '.join(error.message for error in response.errors) or self.env._('The API request failed.')
            raise UserError(message)
        return response.data

    def _external_id(self, response_object):
        self.ensure_one()
        if not self.external_key_path:
            raise UserError(self.env._('Set External Key Path before synchronizing.'))
        value = self._resolve_path(response_object, self.external_key_path, None)
        if value is None or value is False or value == '':
            raise UserError(self.env._('External key %(path)s is missing from a response object.', path=self.external_key_path))
        return json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else str(value)

    def _coerce_value(self, field, value):
        """Convert JSON scalar values to common Odoo field values."""
        if value is None:
            return value
        if field.type in ('integer', 'many2one') and value != '':
            return int(value)
        if field.type in ('float', 'monetary') and value != '':
            return float(value)
        if field.type == 'boolean' and isinstance(value, str):
            return value.strip().lower() in ('1', 'true', 'yes', 'y')
        return value

    def _prepare_sync_values(self, response_object):
        """Build the create/write values for one response object."""
        self.ensure_one()
        model = self.env[self.res_model_id.model]
        values = {}
        for mapping in self.field_mapping_ids:
            value = mapping.get_value(response_object)
            field = model._fields.get(mapping.field_name)
            if value is None or not field or field.readonly or field.type in ('one2many', 'many2many'):
                continue
            values[mapping.field_name] = self._coerce_value(field, value)
        return values

    def _sync_generic_response(self, response_data, deadline=None):
        """Upsert each resolved object in batches, using its external key."""
        self.ensure_one()
        records = self._response_records(response_data)
        if not records or not all(isinstance(item, dict) for item in records):
            raise UserError(self.env._('Resolved Path must return a list of JSON objects for synchronization.'))
        model = self.env[self.res_model_id.model]
        link_model = self.env['vendor.integration.record']
        created = updated = 0
        cursor = min(self.sync_cursor, len(records))
        while cursor < len(records):
            if deadline and time.monotonic() >= deadline:
                break
            start = cursor
            response_batch = records[start:start + self.sync_batch_size]
            objects_by_external_id = {}
            for response_object in response_batch:
                objects_by_external_id[self._external_id(response_object)] = response_object
            external_ids = list(objects_by_external_id)
            links = link_model.search([
                ('vendor_api_id', '=', self.id), ('external_id', 'in', external_ids),
            ])
            links_by_external_id = {link.external_id: link for link in links}
            existing_ids = set(model.browse([link.res_id for link in links]).exists().ids)
            create_values = []
            create_external_ids = []
            updates = []
            for external_id, response_object in objects_by_external_id.items():
                values = self._prepare_sync_values(response_object)
                link = links_by_external_id.get(external_id)
                if link and link.res_id in existing_ids:
                    updates.append((link.res_id, values))
                else:
                    create_values.append(values)
                    create_external_ids.append(external_id)
            if create_values:
                new_records = model.create(create_values)
                link_model.create([
                    {'vendor_api_id': self.id, 'external_id': external_id, 'res_id': record.id}
                    for external_id, record in zip(create_external_ids, new_records)
                ])
                created += len(new_records)
            for record_id, values in updates:
                model.browse(record_id).write(values)
            updated += len(updates)
            cursor += len(response_batch)
        return {
            'created': created,
            'updated': updated,
            'total': len(records),
            'cursor': cursor,
            'complete': cursor >= len(records),
        }

    def _sync_response(self, response_data, deadline=None):
        """Override this hook in a custom module for a specific integration type."""
        self.ensure_one()
        return self._sync_generic_response(response_data, deadline=deadline)

    def _run_sync(self, time_limit=SYNC_TIME_LIMIT_SECONDS):
        self.ensure_one()
        if not self.res_model_id:
            raise UserError(self.env._('Choose the Odoo model before synchronizing.'))
        if not self.is_integration_compatible:
            raise UserError(self.env._('Complete the required field mappings before synchronizing.'))
        deadline = time.monotonic() + time_limit if time_limit else None
        result = self._sync_response(self._request_response_data(), deadline=deadline)
        now = fields.Datetime.now()
        self.write({
            'last_sync_on': now,
            'sync_cursor': 0 if result['complete'] else result['cursor'],
            'next_sync_on': now + timedelta(hours=self.sync_interval_hours) if result['complete'] else False,
            'last_sync_message': self.env._('%(created)s created, %(updated)s updated (%(cursor)s/%(total)s).', **result),
        })
        return result

    def action_sync_now(self):
        self.ensure_one()
        try:
            result = self._run_sync()
        except UserError as error:
            self.write({'last_sync_message': str(error)})
            return self._sync_notification(False, str(error))
        if not result['complete']:
            self.env.ref('vendor_integration.ir_cron_vendor_api_sync')._trigger()
        return self._sync_notification(True, self.last_sync_message)

    @api.model
    def _cron_sync_due_apis(self):
        now = fields.Datetime.now()
        domain = [
            ('active', '=', True), ('sync_enabled', '=', True), '|',
            ('next_sync_on', '=', False), ('next_sync_on', '<=', now),
        ]
        api_record = self.search(domain, limit=1)
        if not api_record:
            return
        try:
            with self.env.cr.savepoint():
                result = api_record._run_sync()
        except Exception as error:
            _logger.exception('Vendor API sync failed for %s', api_record.display_name)
            api_record.write({
                'last_sync_message': str(error),
                'next_sync_on': now + timedelta(hours=api_record.sync_interval_hours),
            })
            result = None
        if (result and not result['complete']) or self.search_count(domain):
            self.env.ref('vendor_integration.ir_cron_vendor_api_sync')._trigger()

    def _sync_notification(self, success, message):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': self.env._('Synchronization complete') if success else self.env._('Synchronization failed'),
                'message': message,
                'type': 'success' if success else 'danger',
                'sticky': not success,
            },
        }

    def _test_result(self, success, message, record_count=0):
        self.write({
            'is_api_ready': success,
            'last_test_message': message,
            'last_test_record_count': record_count,
            'last_tested_on': fields.Datetime.now(),
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': self.env._('API test successful') if success else self.env._('API test failed'),
                'message': message,
                'type': 'success' if success else 'danger',
                'sticky': not success,
            },
        }
