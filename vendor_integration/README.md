# Vendor Integration

This module is intentionally limited to configuring an API, reading its JSON,
and creating or updating records in one selected Odoo model. It does not create
variants, attach assets, match stock to products, or apply vendor-specific
rules. Put those rules in a small inherited module.

## UI configuration

Create an **External Vendor** with its base URL and authentication connection,
then add one or more **Vendor APIs**. For every API choose:

| Field | What to enter |
| --- | --- |
| Save Records In | The Odoo model that should receive the basic record. |
| Records Path | Where its records live in the JSON response. |
| External Key | A stable unique key used to update the same record later. |
| Field Mappings | Simple JSON key to Odoo field mappings. |

JSON keys may use dotted paths, for example
`attributes.product_title.en_GB`. The optional Selection Value Map is only for
translating a vendor value to an Odoo selection value.

Large responses are processed in batches of 1,000 records by default. Change
**Batch Size** on an API when needed. The importer saves its cursor and triggers
the next cron batch automatically, so one request never needs to process the
complete catalogue.

The base module also provides reusable `vendor.product.stock` and
`vendor.product.asset` models. Vendor modules may link their imported stock,
SKU, image, or document data to standard Odoo products without defining those
technical storage models again.

## MiDocean and Araco payloads

Root lists, one root object, and dictionaries keyed by SKU are detected
automatically. For a MiDocean root list, leave Records Path empty and use
`master_id` / `sku` respectively as the External Key. For the supplied Araco
payload, use Records Path `value` and External Key `identifier`.

## Custom post-processing

Override `_sync()` in your vendor-specific module when an otherwise tabular
endpoint needs post-processing. The generic parent creates or updates basic
records and returns the saved recordset plus the original JSON payload.
`_response_records(payload)` returns the source objects in exactly the same
order as the returned recordset.

```python
from odoo import models


class VendorApi(models.Model):
    _inherit = 'vendor.api'

    def _sync(self):
        records, payload = super()._sync()
        if self.integration_type == 'midocean' and self.api_purpose == 'catalogue':
            for record, source in zip(records, self._response_records(payload)):
                for variant_data in source.get('variants', []):
                    # Your product.variant / image / mapping logic goes here.
                    pass
        return records, payload
```

Use the same pattern for stock: configure the stock API to save its raw/basic
record to the model you choose, then write the SKU-to-product and stock update
logic after `super()`.

For an endpoint with multiple root collections or deeply nested structures,
override `_sync_custom_payload()`. Return `None` to use the normal batch
importer, or return `(records, payload)` after the vendor-specific import is
complete. MiDocean print data uses this hook.

## MiDocean configuration

The MiDocean post-processing is intentionally small and runs only when Vendor
Type is **MiDocean**. Configure these target models and mappings:

| Purpose | Save Records In | External Key | UI mappings |
| --- | --- | --- | --- |
| Catalogue | `product.template` | `master_id` | Map normal template fields such as `product_name` → `name`, descriptions, dimensions, weight and volume. |
| Stock | `vendor.product.stock` | `sku` | `sku` → `sku`, `qty` → `quantity`, `first_arrival_date`, `first_arrival_qty`. |
| Supplier Price | `vendor.product.price` | `sku` | `sku`, `variant_id` → `variant_external_id`, `price`, `valid_until`. |

Catalogue processing sets the template UoM to **Units**, creates/updates the
Odoo **Color** attribute and its variants, assigns variant SKU and GTIN, and
saves digital-asset URLs without downloading files. Stock records are linked to
their variant by SKU. Price records are linked in the same way and upsert the
vendor's `product.supplierinfo` price using the External Vendor contact.
