import logging
from datetime import timedelta
from urllib.parse import urljoin

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


_logger = logging.getLogger(__name__)


class VendorApi(models.Model):
    """Small API importer; vendor-specific processing belongs in an override."""

    _name = 'vendor.api'
    _description = 'Vendor API'

    name = fields.Char(required=True)
    external_vendor_id = fields.Many2one('external.vendor', required=True, ondelete='cascade')
    api_url = fields.Char(required=True, string='Endpoint')
    api_method = fields.Selection([('get', 'GET'), ('post', 'POST')], default='get', required=True)
    integration_type = fields.Selection([
        ('generic', 'Generic'), ('midocean', 'MiDocean'), ('araco', 'Araco'),
        ('impliva', 'Impliva'), ('pf_concept', 'PF Concept'), ('toppoint', 'Toppoint'),
        ('promocorp', 'PromoCorp'), ('texet', 'Texet'), ('toptex', 'TopTex'),
        ('xd_connects', 'XD Connects'), ('sols', "Sol's"), ('falk_ross', 'Falk&Ross'),
        ('l_shop', 'L-Shop'),
    ], default='generic', required=True, string='Vendor Type')
    api_purpose = fields.Selection([
        ('catalogue', 'Catalogue'), ('stock', 'Stock'),
        ('supplier_price', 'Supplier Price'), ('print_data', 'Print Data'),
        ('print_pricelist', 'Print Pricelist'), ('generic', 'Generic'),
    ], default='generic', required=True, string='Purpose')
    res_model_id = fields.Many2one('ir.model', string='Save Records In')
    resolved_path = fields.Char(
        string='Records Path',
        help='Path to the records, e.g. "value" or "data.items". Leave empty for a root list or object.',
    )
    external_key_path = fields.Char(
        required=True, string='External Key',
        help='Unique key in each source record, e.g. sku or identifier. Used to update a later sync.',
    )
    field_mapping_ids = fields.One2many('field.mapping', 'vendor_api_id', string='Field Mappings')

    active = fields.Boolean(default=True)
    sync_enabled = fields.Boolean(default=False)
    sync_interval_hours = fields.Integer(default=24, string='Sync Every (Hours)')
    sync_batch_size = fields.Integer(default=1000, string='Batch Size')
    sync_cursor = fields.Integer(default=0, readonly=True)
    sync_payload = fields.Json(readonly=True, copy=False, string='Pending Sync Payload')
    next_sync_on = fields.Datetime()
    last_sync_on = fields.Datetime(readonly=True)
    last_sync_message = fields.Char(readonly=True)
    last_test_message = fields.Char(readonly=True)
    last_tested_on = fields.Datetime(readonly=True)

    @api.constrains('sync_interval_hours', 'sync_batch_size')
    def _check_interval(self):
        """Keep scheduled runs and batch processing valid."""
        for api in self:
            if api.sync_interval_hours <= 0 or api.sync_batch_size <= 0:
                raise ValidationError(self.env._('Sync interval and batch size must be greater than zero.'))

    def _endpoint_url(self):
        """Return the absolute endpoint URL for this API."""
        self.ensure_one()
        if not self.external_vendor_id.base_url:
            raise UserError(self.env._('Set the vendor base URL.'))
        return urljoin(self.external_vendor_id.base_url.rstrip('/') + '/', self.api_url.lstrip('/'))

    def _get_json(self):
        """Request and return the decoded JSON response.

        :raises UserError: when the configured authentication request fails.
        """
        self.ensure_one()
        response = self.external_vendor_id.authentcation_method_id.request(self.api_method, self._endpoint_url())
        if not response.success:
            message = '; '.join(error.message for error in response.errors)
            raise UserError(message or self.env._('The API request failed.'))
        return response.data

    @api.model
    def _json_value(self, data, path, default=None):
        """Read a dotted path from a JSON dictionary."""
        for key in (path or '').split('.'):
            if key:
                if not isinstance(data, dict) or key not in data:
                    return default
                data = data[key]
        return data

    def _response_records(self, payload):
        """Return source objects from a response list, object, or keyed object."""
        self.ensure_one()
        data = self._json_value(payload, self.resolved_path, payload)
        if isinstance(data, dict):
            values = list(data.values())
            records = values if values and all(isinstance(value, dict) for value in values) else [data]
        else:
            records = data
        if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
            raise UserError(self.env._('Records Path must resolve to JSON objects.'))
        return records

    def _values_from_json(self, data):
        """Map simple scalar values; integration code handles complex values."""
        self.ensure_one()
        model = self.env[self.res_model_id.model]
        values = {}
        for mapping in self.field_mapping_ids:
            field = model._fields.get(mapping.field_name)
            value = mapping.get_value(data)
            if not field or value is None or field.readonly or field.type in ('one2many', 'many2many'):
                continue
            try:
                if field.type in ('integer', 'many2one'):
                    value = int(value) if value != '' else False
                elif field.type in ('float', 'monetary'):
                    value = float(str(value).replace(',', '.')) if value != '' else False
                elif field.type == 'boolean' and isinstance(value, str):
                    value = value.lower() in ('1', 'true', 'yes')
            except (TypeError, ValueError) as error:
                raise UserError(self.env._(
                    'Cannot convert %(key)s to %(field)s: %(error)s',
                    key=mapping.response_key,
                    field=field.string,
                    error=error,
                ))
            values[mapping.field_name] = value
        return values

    def _before_create_records(self, source_records):
        """Return optional batch state used by ``_create_values_from_json``.

        Integration modules can use this to prepare related records once per
        batch, then enrich the values of new records without changing the
        generic create/update flow.
        """
        return None

    def _create_values_from_json(self, data, prepared):
        """Return extra values only for records that do not exist yet."""
        return {}

    def _prepare_record_values(self, data, values, record, prepared):
        """Return final values, or ``False`` to skip an invalid source record."""
        return values

    def _create_records(self, payload, source_records=None):
        """Create or update the batch and return its target records.

        Args:
            payload: Complete decoded API response.
            source_records: Optional source records to process.
        """
        self.ensure_one()
        source_records = source_records if source_records is not None else self._response_records(payload)
        prepared = self._before_create_records(source_records)
        model = self.env[self.res_model_id.model]
        keys = [self._json_value(data, self.external_key_path) for data in source_records]
        if any(key in (None, '') for key in keys):
            raise UserError(self.env._(
                'External Key "%(key)s" is missing from one or more records.',
                key=self.external_key_path,
            ))
        keys = [str(key) for key in keys]
        if len(keys) != len(set(keys)):
            raise UserError(self.env._(
                'External Key "%(key)s" must be unique in each response.',
                key=self.external_key_path,
            ))
        links = self.env['vendor.integration.record'].search([
            ('vendor_api_id', '=', self.id), ('res_model_id', '=', self.res_model_id.id), ('external_id', 'in', keys),
        ])
        links_by_key = {link.external_id: link for link in links}
        linked_records = model.browse(links.mapped('res_id')).exists()
        records_by_id = {record.id: record for record in linked_records}
        items, create_values = [], []
        for source, key in zip(source_records, keys):
            link = links_by_key.get(key)
            record = records_by_id.get(link.res_id, model.browse()) if link else model.browse()
            values = self._values_from_json(source)
            if not record:
                values.update(self._create_values_from_json(source, prepared))
            values = self._prepare_record_values(source, values, record, prepared)
            if values is False:
                continue
            items.append((key, record, values))
            if not record:
                create_values.append(values)
        created = iter(model.create(create_values)) if create_values else iter(())
        record_ids, link_values = [], []
        for key, record, values in items:
            if record:
                record.write(values)
            else:
                record = next(created)
                link_values.append({
                    'vendor_api_id': self.id, 'res_model_id': self.res_model_id.id,
                    'external_id': key, 'res_id': record.id,
                })
            record_ids.append(record.id)
        if link_values:
            self.env['vendor.integration.record'].create(link_values)
        return model.browse(record_ids)

    def _sync(self):
        """Override and call ``super()`` to add vendor-specific post-processing.

        Example::

            records, payload = super()._sync()
            for record, source in zip(records, self._response_records(payload)):
                # create variants, map stock, download assets, ...
                pass
            return records, payload
        """
        self.ensure_one()
        # An API commonly returns its complete catalogue in one response.  Keep
        # that response while its batches are being consumed: every continuation
        # then does database work only and cannot drift to a different response.
        payload = self.sync_payload if self.sync_cursor and self.sync_payload is not False else self._get_json()
        source_records = self._response_records(payload)
        cursor = min(self.sync_cursor, len(source_records))
        batch = source_records[cursor:cursor + self.sync_batch_size]
        records = self._create_records(payload, batch)
        next_cursor = cursor + len(batch)
        has_more = next_cursor < len(source_records)
        now = fields.Datetime.now()
        values = {
            'last_sync_on': now,
            'next_sync_on': now if has_more else now + timedelta(hours=self.sync_interval_hours),
            'sync_cursor': next_cursor if has_more else 0,
            'last_sync_message': self.env._(
                '%(saved)s saved (%(done)s/%(total)s).',
                saved=len(records),
                done=next_cursor,
                total=len(source_records),
            ),
        }
        if has_more:
            # Do not rewrite a potentially large JSON column for every batch.
            if not self.sync_payload:
                values['sync_payload'] = payload
        else:
            values['sync_payload'] = False
        self.write(values)
        return records, payload

    def action_test_api(self):
        """Test the endpoint and store a concise result for the user."""
        self.ensure_one()
        try:
            message, success = self.env._('%s record(s) found.', len(self._response_records(self._get_json()))), True
        except UserError as error:
            message, success = str(error), False
        self.write({'last_tested_on': fields.Datetime.now(), 'last_test_message': message})
        return self._notification(success, message)

    def action_sync_now(self):
        """Synchronize one batch and schedule a continuation when needed."""
        self.ensure_one()
        try:
            self._sync()
            if self.sync_cursor:
                self.env.ref('vendor_integration.ir_cron_vendor_api_sync')._trigger()
            return self._notification(True, self.last_sync_message)
        except UserError as error:
            self.write({'last_sync_message': str(error)})
            return self._notification(False, str(error))

    @api.model
    def _cron_sync_due_apis(self):
        """Run a batch for every API that is due.

        Each endpoint has its own interval, so a cron invocation must not stop
        after the first matching API.  In particular, manually triggering the
        cron should evaluate product, stock and price endpoints independently.
        A large response is still processed one batch at a time; a follow-up
        cron is queued when at least one endpoint has more records to process.
        """
        now = fields.Datetime.now()
        due_apis = self.search([
            ('active', '=', True),
            '|', ('sync_cursor', '>', 0), ('sync_enabled', '=', True),
            '|', ('next_sync_on', '=', False), ('next_sync_on', '<=', now),
        ])
        has_pending_batches = False
        for api in due_apis:
            try:
                api._sync()
                has_pending_batches |= bool(api.sync_cursor)
            except Exception:
                # One unavailable endpoint must not prevent the other due APIs
                # from being synchronized during this cron invocation.
                _logger.exception('Vendor API sync failed for %s', api.display_name)
                api.write({'next_sync_on': now + timedelta(hours=api.sync_interval_hours)})

        if has_pending_batches:
            self.env.ref('vendor_integration.ir_cron_vendor_api_sync')._trigger()

    def _notification(self, success, message):
        """Build a standard Odoo notification action."""
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': self.env._('Done') if success else self.env._('Failed'),
            'message': message, 'type': 'success' if success else 'danger', 'sticky': not success,
        }}
