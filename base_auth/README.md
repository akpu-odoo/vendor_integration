# Base Auth

Generic outbound HTTP authentication for Odoo 19, inspired by Postman’s **Authorization** tab.

This module is a **base** other integrations inherit. It stores credentials, attaches them to HTTP calls, and returns a normalized `AuthResponse`. It does **not** map API payloads onto products, partners, or other business models — that stays in the child addon.


## UI

Open **Settings > Technical > Parameters > Auth Connections** to get to the config view.

## Model: `base.auth`

One record = one connection (host + credentials). Name is unique.

### Always visible

| Field | Type | Notes |
| --- | --- | --- |
| `name` | Char, required, unique | Connection label |
| `active` | Boolean | Archive instead of delete |
| `company_id` | Many2one `res.company` | Required, defaults to current company |
| `base_url` | Char, required | Resource API host (not the OAuth token URL) |
| `auth_type` | Selection | One protocol per record |
| `timeout` | Integer, default `30` | Seconds to wait before aborting; must be `> 0`. Form help explains this. |
| `verify_ssl` | Boolean, default `True` | Verify the HTTPS certificate. Disable only for local/self-signed tests. |
| `follow_redirects` | Boolean, default `True` | Follow HTTP 3xx. If off, `Location` is stored in `meta`. |
| `success_path` | Char | Dotted JSON path to the payload (e.g. `result.items`); developer mode. Empty = whole body or a single `data`/`result`/`items`/`payload` wrapper. |
| `error_path` | Char | Dotted JSON path to errors (e.g. `error.message`); developer mode. Empty = common error shapes. |

### `auth_type` values (`_selection_auth_type`)

| Value | Label | Extra fields (shown only for this type) |
| --- | --- | --- |
| `none` | No Auth | — |
| `api_key` | API Key | `api_key_name`, `api_key_in` (`header` / `query`), `api_key` |
| `bearer` | Bearer Token | `bearer_token` → `Authorization: Bearer …` |
| `basic` | Basic Auth | `username`, `password` (also email/password) |
| `oauth2` | OAuth 2.0 | `grant_type` (`client_credentials` only), `client_id`, `client_secret`, `token_endpoint`, optional `auth_endpoint`, `scope` |

`auth_endpoint` is stored for children that add authorization-code / SSO. Core only uses `token_endpoint` for client credentials.

Constraints (`_check_auth_config`): required fields for the selected type; timeout `> 0`; `base_url` set. Extend with `super()` if you add a type.

Secrets use the password widget in the UI. Do not log `api_key`, `password`, `bearer_token`, or `client_secret`.


## Calling the API

```python
connection = env["base.auth"].search([("name", "=", "My API")], limit=1)
response = connection.get("/v1/items")
if response.success:
    records = response.data
else:
    for error in response.errors:
        _logger.warning("%s: %s", error.code, error.message)
```

Pass extra HTTP options through to `requests` (`json=`, `data=`, `params=`, `headers=`, `timeout=`, …):

```python
response = connection.post("/v1/orders", json={"sku": "ABC"}, headers={"Accept": "application/json"})
```

Absolute URLs in `path` are used as-is; otherwise they are joined to `base_url`.

Misconfiguration raises `ValidationError`. Timeouts, SSL, HTTP 4xx/5xx, and 200-bodies that look like errors return `success=False` (they do not raise).

## `AuthResponse` / `AuthError`

From `odoo.addons.base_auth.utils.envelope`.

`AuthResponse`: `success`, `status`, `data`, `errors`, `meta`, `raw_body`, `raw_headers`, `url`, `method`.

`AuthError`: `code`, `message`, `details`.

`success` is True only for HTTP 2xx **and** a body that is not an error payload (`success: false`, `error` / `errors`, OAuth `error_description`, RFC 7807).

`meta` may include `content_type`, `elapsed`, `retry_after`, `redirects`, `location`.

HTTP error codes from `_http_error_code`: `unauthorized` (401), `forbidden` (403), `not_found` (404), `conflict` (409), `validation` (422), `rate_limit` (429), else `http_{status}`. Transport: `timeout`, `ssl`, `transport`. Parse failures: `parse`.

## Override map (always `super()`)

Use these on `_inherit = "base.auth"`. Prefer the most specific hook.

| Override this | When |
| --- | --- |
| `_selection_auth_type` | Add a type (`hmac`, …) |
| `_selection_grant_type` | Add OAuth grants (authorization code, …) |
| `_check_auth_config` | Required fields for your type |
| `_prepare_auth_headers` | Extra/static headers (API key header, HMAC, …) |
| `_prepare_auth_params` | Extra query params (API key in query, …) |
| `_prepare_auth` | Whole auth tuple `(headers, params, basic, error_response)` |
| `_prepare_request` | URL join, timeout, verify, merge caller kwargs |
| `_fetch_oauth_token` | Token URL, body, or caching |
| `_oauth_access_token` | Token JSON is not `{access_token: …}` |
| `_parse_response` | Envelope / success rules |
| `_parse_error` | Error list / codes |
| `_http_error_code` | Extra status mappings |
| `_send` | Replace `requests` or add logging (no secrets) |
| `request` | All verbs |
| `get` / `post` / `put` / `patch` / `delete` | One verb only |

Parser helpers in `utils/parser.py` (import; override model methods rather than patching these unless you must): `get_by_path`, `_xml_to_value`, `parse_body`, `unwrap_data`, `is_error_payload`, `_as_error_list`, `extract_errors`.

Default JSON unwrap keys (single-key bodies only): `data`, `result`, `items`, `payload`. Otherwise set `success_path` / `error_path`.

## Full child example

`models/base_auth.py`:

```python
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class BaseAuth(models.Model):
    _inherit = "base.auth"

    hmac_secret = fields.Char()

    def _selection_auth_type(self):
        types = super()._selection_auth_type()
        types.append(("hmac", "HMAC"))
        return types

    @api.constrains("auth_type", "hmac_secret")
    def _check_auth_config(self):
        super()._check_auth_config()
        for auth in self:
            if auth.auth_type == "hmac" and not auth.hmac_secret:
                raise ValidationError(self.env._("HMAC secret is required."))

    def _prepare_auth_headers(self):
        headers = super()._prepare_auth_headers()
        if self.auth_type == "hmac":
            headers["X-Signature"] = self._sign_hmac()
        return headers

    def _sign_hmac(self):
        self.ensure_one()
        return self.hmac_secret
```

Then call the same record:

```python
connection = self.env["base.auth"].search([("auth_type", "=", "hmac")], limit=1)
response = connection.get("/catalog")
# map response.data onto product.template in this child module
```

## What this module does not do

- Map JSON/XML onto Odoo business fields
- Browser SSO / authorization-code (add a grant + controller in a child)
- Store tokens on `res.users` or reuse `auth.oauth.provider` / `auth.api.key`
