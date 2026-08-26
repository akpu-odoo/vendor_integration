from odoo import fields, models
from odoo.exceptions import UserError


class VendorProductAsset(models.Model):
    _name = 'vendor.product.asset'
    _description = 'Vendor Product Asset'
    _rec_name = 'name'

    name = fields.Char(required=True)
    vendor_api_id = fields.Many2one('vendor.api', required=True, ondelete='cascade', index=True)
    asset_key = fields.Char(required=True, index=True)
    product_tmpl_id = fields.Many2one('product.template', ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', ondelete='cascade', index=True)
    url = fields.Char(required=True)
    highres_url = fields.Char(string='High Resolution URL')
    asset_type = fields.Char()
    subtype = fields.Char()

    _asset_key_unique = models.Constraint('unique(vendor_api_id, asset_key)', 'A vendor asset can only be stored once per API.')


class VendorProductStock(models.Model):
    _name = 'vendor.product.stock'
    _description = 'Vendor Product Stock'
    _rec_name = 'sku'

    vendor_api_id = fields.Many2one('vendor.api', ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', ondelete='cascade', index=True)
    sku = fields.Char(required=True, index=True)
    quantity = fields.Integer()
    first_arrival_date = fields.Date()
    first_arrival_qty = fields.Integer()

    _stock_unique = models.Constraint('unique(vendor_api_id, sku)', 'A SKU can only have one stock record per API.')


class VendorProductPrice(models.Model):
    _name = 'vendor.product.price'
    _description = 'Vendor Product Price'
    _rec_name = 'sku'

    vendor_api_id = fields.Many2one('vendor.api', ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', ondelete='cascade', index=True)
    sku = fields.Char(required=True, index=True)
    variant_external_id = fields.Char(index=True)
    price = fields.Float()
    valid_until = fields.Date()

    _price_unique = models.Constraint('unique(vendor_api_id, sku)', 'A SKU can only have one current price per API.')


class ProductProduct(models.Model):
    _inherit = 'product.product'

    vendor_stock_ids = fields.One2many('vendor.product.stock', 'product_id', string='Vendor Stock')
    vendor_price_ids = fields.One2many('vendor.product.price', 'product_id', string='Vendor Prices')
    vendor_asset_ids = fields.One2many('vendor.product.asset', 'product_id', string='Vendor Assets')


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    vendor_asset_ids = fields.One2many('vendor.product.asset', 'product_tmpl_id', string='Vendor Assets')


class ProductSupplierinfo(models.Model):
    _inherit = 'product.supplierinfo'

    vendor_api_id = fields.Many2one('vendor.api', ondelete='cascade', index=True)
    _vendor_product_unique = models.Constraint('unique(vendor_api_id, product_id)', 'A vendor API can only have one supplier price per product.')


class VendorApi(models.Model):
    _inherit = 'vendor.api'

    def _sync(self):
        cursor = self.sync_cursor
        records, payload = super()._sync()
        if self.integration_type == 'midocean':
            sources = self._response_records(payload)
            cursor = min(cursor, len(sources))
            sources = sources[cursor:cursor + len(records)]
            getattr(self, '_sync_midocean_%s' % self.api_purpose, lambda *_: None)(records, sources)
        return records, payload

    def _sync_midocean_catalogue(self, templates, sources):
        if templates._name != 'product.template':
            return
        templates.write({'uom_id': self.env.ref('uom.product_uom_unit').id})
        attributes = self._midocean_attributes(sources)
        assets = []
        for template, source in zip(templates, sources):
            variants = source.get('variants', [])
            self._add_variant_values(template, variants, attributes)
            attribute_ids = {attribute.id for attribute, _values in attributes.values()}
            products = {
                frozenset(ptav.product_attribute_value_id.id for ptav in product.product_template_attribute_value_ids if ptav.attribute_id.id in attribute_ids): product
                for product in template.product_variant_ids
            }
            for variant in variants:
                key = frozenset(attributes[field][1][variant[field]].id for field in attributes if variant.get(field))
                product = products.get(key)
                if product:
                    values = {'default_code': variant.get('sku'), 'barcode': variant.get('gtin') or False}
                    if any(product[field] != value for field, value in values.items()):
                        product.write(values)
                    assets.extend((product, variant.get('sku'), item) for item in variant.get('digital_assets', []))
            assets.extend((template, source.get('master_id') or source.get('master_code'), item) for item in source.get('digital_assets', []))
        self._sync_assets(assets)

    def _midocean_attributes(self, sources):
        """Return native Odoo attributes and values keyed by MiDocean field."""
        definitions = {'color_code': 'Color Code', 'size_textile': 'Size'}
        result = {}
        for field, name in definitions.items():
            names = {variant.get(field) for source in sources for variant in source.get('variants', [])}
            names.discard(False)
            names.discard(None)
            if not names:
                continue
            attribute = self.env['product.attribute'].search([('name', '=', name)], limit=1)
            if not attribute:
                attribute = self.env['product.attribute'].create({'name': name, 'display_type': 'select', 'create_variant': 'always'})
            if attribute.create_variant != 'always':
                raise UserError(self.env._('%s must create variants instantly for MiDocean imports.', name))
            values = self.env['product.attribute.value'].search([('attribute_id', '=', attribute.id), ('name', 'in', list(names))])
            by_name = {value.name: value for value in values}
            missing = names - by_name.keys()
            if missing:
                by_name.update({value.name: value for value in self.env['product.attribute.value'].create([
                    {'attribute_id': attribute.id, 'name': value} for value in missing
                ])})
            result[field] = (attribute, by_name)
        return result

    def _add_variant_values(self, template, variants, attributes):
        """Only link new values: replacing a line makes Odoo delete variants."""
        new_lines, line_updates, remove_lines = [], [], []
        descriptions = {variant.get('color_description') for variant in variants}
        legacy_line = template.attribute_line_ids.filtered(lambda line: line.attribute_id.name == 'Color')
        if legacy_line and set(legacy_line.value_ids.mapped('name')) <= descriptions:
            remove_lines = [(2, line.id) for line in legacy_line]
        for field, (attribute, values) in attributes.items():
            ids = {values[variant[field]].id for variant in variants if variant.get(field)}
            line = template.attribute_line_ids.filtered(lambda item: item.attribute_id == attribute)
            new_ids = ids - set(line.value_ids.ids)
            if not new_ids:
                continue
            if line:
                line_updates.append((line, new_ids))
            else:
                new_lines.append((0, 0, {'attribute_id': attribute.id, 'value_ids': [(6, 0, list(ids))]}))
        if new_lines or remove_lines:
            template.write({'attribute_line_ids': remove_lines + new_lines})
        for line, value_ids in line_updates:
            line.write({'value_ids': [(4, value_id) for value_id in value_ids]})

    def _sync_midocean_stock(self, records, sources):
        if records._name != 'vendor.product.stock':
            return
        products = self.env['product.product'].search([('default_code', 'in', [source.get('sku') for source in sources])])
        by_sku = {product.default_code: product for product in products}
        for record, source in zip(records, sources):
            record.write({'vendor_api_id': self.id, 'product_id': by_sku.get(source.get('sku'), False).id})

    def _sync_midocean_supplier_price(self, records, sources):
        if records._name != 'vendor.product.price':
            return
        if not self.external_vendor_id.partner_id:
            raise UserError(self.env._('The vendor needs a contact before supplier prices can be created.'))
        products = self.env['product.product'].search([('default_code', 'in', [source.get('sku') for source in sources])])
        by_sku = {product.default_code: product for product in products}
        lines = self.env['product.supplierinfo'].search([('vendor_api_id', '=', self.id), ('product_id', 'in', products.ids)])
        by_product = {line.product_id.id: line for line in lines}
        create_values = []
        for record, source in zip(records, sources):
            product = by_sku.get(source.get('sku'))
            record.write({'vendor_api_id': self.id, 'product_id': product.id if product else False})
            if not product:
                continue
            values = {'partner_id': self.external_vendor_id.partner_id.id, 'product_id': product.id,
                      'product_code': source.get('sku'), 'price': float(str(source.get('price') or 0).replace(',', '.')),
                      'date_end': source.get('valid_until') or False}
            if product.id in by_product:
                by_product[product.id].write(values)
            else:
                create_values.append(dict(values, vendor_api_id=self.id))
        if create_values:
            self.env['product.supplierinfo'].create(create_values)

    def _sync_assets(self, assets):
        values = []
        for owner, key, asset in assets:
            if asset.get('url') and key:
                values.append({'vendor_api_id': self.id, 'asset_key': '%s:%s' % (key, asset['url']),
                               'name': asset.get('subtype') or asset.get('type') or asset['url'].rsplit('/', 1)[-1],
                               'url': asset['url'], 'highres_url': asset.get('url_highress'), 'asset_type': asset.get('type'),
                               'subtype': asset.get('subtype'), 'product_tmpl_id': owner.id if owner._name == 'product.template' else False,
                               'product_id': owner.id if owner._name == 'product.product' else False})
        if not values:
            return
        asset_model = self.env['vendor.product.asset']
        existing = asset_model.search([('vendor_api_id', '=', self.id), ('asset_key', 'in', [value['asset_key'] for value in values])])
        by_key = {asset.asset_key: asset for asset in existing}
        for value in values:
            if value['asset_key'] in by_key:
                by_key[value['asset_key']].write(value)
        new_values = [value for value in values if value['asset_key'] not in by_key]
        if new_values:
            asset_model.create(new_values)
