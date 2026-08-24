import logging
from urllib.parse import urljoin

import requests
from requests import RequestException

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..utils.envelope import AuthError, AuthResponse
from ..utils.parser import extract_errors, is_error_payload, parse_body, unwrap_data

_logger = logging.getLogger(__name__)

HTTP_ERROR_CODES = {
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation",
    429: "rate_limit",
}


class BaseAuth(models.Model):
    _name = "base.auth"
    _description = "Outbound Auth Connection"
    _order = "name"
    _check_company_auto = True

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    base_url = fields.Char(required=True)
    auth_type = fields.Selection(
        selection="_selection_auth_type",
        string="Type",
        required=True,
        default="none",
    )
    timeout = fields.Integer(
        default=30,
        help="How many seconds to wait for the remote API before the call is aborted. "
        "Must be greater than zero. Default is 30.",
    )
    verify_ssl = fields.Boolean(
        string="Verify SSL",
        default=True,
        help="Check the remote server's HTTPS certificate. Leave enabled in production. "
        "Disable only for a local or self-signed test endpoint.",
    )
    follow_redirects = fields.Boolean(
        string="Follow Redirects",
        default=True,
        help="Follow HTTP 3xx redirects to the final URL (needed when an API returns "
        "a download link). If disabled, the response stays on the first redirect "
        "and the Location header is stored in meta.",
    )
    success_path = fields.Char(
        help="Dotted path to the useful payload inside a successful JSON body, "
        "e.g. result.items. Leave empty to use the whole body, or a single "
        "wrapper key named data, result, items, or payload.",
    )
    error_path = fields.Char(
        help="Dotted path to the error object or list inside a failed JSON body, "
        "e.g. error.message. Leave empty to detect common shapes "
        "(errors, error, message, RFC 7807).",
    )

    api_key = fields.Char()
    api_key_in = fields.Selection(
        selection=[("header", "Header"), ("query", "Query Params")],
        default="header",
    )
    api_key_name = fields.Char()

    bearer_token = fields.Char()

    username = fields.Char()
    password = fields.Char()

    client_id = fields.Char()
    client_secret = fields.Char()
    token_endpoint = fields.Char()
    auth_endpoint = fields.Char()
    scope = fields.Char()
    grant_type = fields.Selection(
        selection="_selection_grant_type",
        default="client_credentials",
    )

    _name_uniq = models.Constraint("unique(name)", "The connection name must be unique.")

    @api.model
    def _selection_auth_type(self):
        return [
            ("none", "No Auth"),
            ("api_key", "API Key"),
            ("bearer", "Bearer Token"),
            ("basic", "Basic Auth"),
            ("oauth2", "OAuth 2.0"),
        ]

    @api.model
    def _selection_grant_type(self):
        return [("client_credentials", "Client Credentials")]

    @api.constrains(
        "auth_type",
        "api_key",
        "api_key_name",
        "bearer_token",
        "username",
        "password",
        "client_id",
        "client_secret",
        "token_endpoint",
        "timeout",
        "base_url",
    )
    def _check_auth_config(self):
        for auth in self:
            if auth.timeout <= 0:
                raise ValidationError(self.env._("Timeout must be greater than zero."))
            if not auth.base_url:
                raise ValidationError(self.env._("Base URL is required."))
            if auth.auth_type == "api_key" and (not auth.api_key or not auth.api_key_name):
                raise ValidationError(
                    self.env._("API key name and value are required for API Key authentication.")
                )
            if auth.auth_type == "bearer" and not auth.bearer_token:
                raise ValidationError(
                    self.env._("A token is required for Bearer authentication.")
                )
            if auth.auth_type == "basic" and (not auth.username or not auth.password):
                raise ValidationError(
                    self.env._("Username and password are required for Basic authentication.")
                )
            if auth.auth_type == "oauth2" and (
                not auth.client_id or not auth.client_secret or not auth.token_endpoint
            ):
                raise ValidationError(
                    self.env._(
                        "Client ID, client secret and token URL are required for OAuth 2.0."
                    )
                )

    def _prepare_auth_headers(self):
        self.ensure_one()
        if self.auth_type == "api_key" and self.api_key_in == "header":
            return {self.api_key_name: self.api_key}
        if self.auth_type == "bearer":
            return {"Authorization": f"Bearer {self.bearer_token}"}
        return {}

    def _prepare_auth_params(self):
        self.ensure_one()
        if self.auth_type == "api_key" and self.api_key_in == "query":
            return {self.api_key_name: self.api_key}
        return {}

    def _oauth_access_token(self, token_response):
        data = token_response.data
        if isinstance(data, dict):
            return data.get("access_token")
        return data

    def _prepare_auth(self):
        self.ensure_one()
        if self.auth_type == "oauth2":
            token_response = self._fetch_oauth_token()
            if not token_response.success:
                return {}, {}, None, token_response
            token = self._oauth_access_token(token_response)
            if not token:
                return {}, {}, None, AuthResponse(
                    success=False,
                    status=token_response.status,
                    errors=[
                        AuthError(code="oauth", message="Missing access_token in token response.")
                    ],
                    data=token_response.data,
                    meta=token_response.meta,
                    raw_body=token_response.raw_body,
                    raw_headers=token_response.raw_headers,
                    url=token_response.url,
                    method=token_response.method,
                )
            headers = {"Authorization": f"Bearer {token}"}
            headers.update(self._prepare_auth_headers())
            return headers, self._prepare_auth_params(), None, None
        basic = (self.username, self.password) if self.auth_type == "basic" else None
        return self._prepare_auth_headers(), self._prepare_auth_params(), basic, None

    def _prepare_request(self, method, path, params=None, headers=None, **kwargs):
        self.ensure_one()
        if path and path.startswith(("http://", "https://")):
            url = path
        else:
            url = urljoin(self.base_url.rstrip("/") + "/", (path or "").lstrip("/"))
        auth_headers, auth_params, basic, error = self._prepare_auth()
        if error:
            return None, error
        request_headers = {**auth_headers, **(headers or {})}
        request_params = {**(params or {}), **auth_params}
        request_kwargs = {
            "method": method.upper(),
            "url": url,
            "params": request_params or None,
            "headers": request_headers or None,
            "timeout": kwargs.pop("timeout", None) or self.timeout,
            "verify": kwargs.pop("verify", self.verify_ssl),
            "allow_redirects": kwargs.pop("allow_redirects", self.follow_redirects),
            "auth": basic,
        }
        request_kwargs.update(kwargs)
        return request_kwargs, None

    def _fetch_oauth_token(self):
        self.ensure_one()
        payload = {
            "grant_type": self.grant_type or "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        if self.scope:
            payload["scope"] = self.scope
        return self._send(
            method="POST",
            url=self.token_endpoint,
            data=payload,
            timeout=self.timeout,
            verify=self.verify_ssl,
            allow_redirects=self.follow_redirects,
        )

    def _http_error_code(self, status):
        if status in HTTP_ERROR_CODES:
            return HTTP_ERROR_CODES[status]
        if status:
            return f"http_{status}"
        return "http"

    def _parse_error(self, data, status, error_path=None):
        code = self._http_error_code(status)
        errors = extract_errors(data, status=status, error_path=error_path)
        if not errors:
            return [
                AuthError(
                    code=code,
                    message=self.env._("HTTP %(status)s", status=status),
                )
            ]
        for error in errors:
            error.code = code
        return errors

    def _parse_response(self, response=None, exc=None, method="", url=""):
        if exc is not None:
            code = "timeout" if isinstance(exc, requests.Timeout) else "transport"
            if isinstance(exc, requests.exceptions.SSLError):
                code = "ssl"
            return AuthResponse(
                success=False,
                errors=[AuthError(code=code, message=str(exc), details=exc)],
                url=url,
                method=method,
            )
        raw_body = response.text or ""
        raw_headers = dict(response.headers)
        content_type = response.headers.get("Content-Type", "")
        parsed, parse_error = parse_body(content_type, raw_body)
        meta = {
            "content_type": content_type,
            "elapsed": getattr(response.elapsed, "total_seconds", lambda: None)(),
        }
        if response.headers.get("Retry-After"):
            meta["retry_after"] = response.headers["Retry-After"]
        if response.history:
            meta["redirects"] = [hist.headers.get("Location") for hist in response.history]
        if parse_error:
            return AuthResponse(
                success=False,
                status=response.status_code,
                errors=[parse_error],
                meta=meta,
                raw_body=raw_body,
                raw_headers=raw_headers,
                url=response.url,
                method=method or response.request.method,
            )
        status = response.status_code
        success_http = 200 <= status < 300
        payload_error = is_error_payload(parsed)
        if success_http and not payload_error:
            return AuthResponse(
                success=True,
                status=status,
                data=unwrap_data(parsed, self.success_path),
                meta=meta,
                raw_body=raw_body,
                raw_headers=raw_headers,
                url=response.url,
                method=method or response.request.method,
            )
        if not success_http and status in (301, 302, 303, 307, 308) and not self.follow_redirects:
            meta["location"] = response.headers.get("Location")
        return AuthResponse(
            success=False,
            status=status,
            data=parsed,
            errors=self._parse_error(parsed, status, error_path=self.error_path),
            meta=meta,
            raw_body=raw_body,
            raw_headers=raw_headers,
            url=response.url,
            method=method or response.request.method,
        )

    def _send(self, method, url, **kwargs):
        try:
            response = requests.request(method, url, **kwargs)
        except RequestException as exc:
            _logger.warning("base.auth request failed: %s %s", method, url)
            return self._parse_response(exc=exc, method=method, url=url)
        return self._parse_response(response=response, method=method, url=url)

    def request(self, method, path="", **kwargs):
        self.ensure_one()
        request_kwargs, error = self._prepare_request(method, path, **kwargs)
        if error:
            return error
        method = request_kwargs.pop("method")
        url = request_kwargs.pop("url")
        return self._send(method, url, **request_kwargs)

    def get(self, path="", **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path="", **kwargs):
        return self.request("POST", path, **kwargs)

    def put(self, path="", **kwargs):
        return self.request("PUT", path, **kwargs)

    def patch(self, path="", **kwargs):
        return self.request("PATCH", path, **kwargs)

    def delete(self, path="", **kwargs):
        return self.request("DELETE", path, **kwargs)
