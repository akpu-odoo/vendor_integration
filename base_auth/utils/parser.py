import json
import xml.etree.ElementTree as ET

from .envelope import AuthError

WRAPPER_KEYS = ("data", "result", "items", "payload")


def get_by_path(value, path):
    """Read a nested dict using a dotted path.

    :param value: parsed body (usually a dict)
    :param path: dotted keys, e.g. ``result.items``; empty returns ``value``
    :return: nested value, or ``None`` if a segment is missing
    """
    if not path:
        return value
    current = value
    for part in path.split("."):
        # Stop if we hit a list/scalar before the path is exhausted.
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _xml_to_value(element):
    """Convert an XML element to dict / list / text.

    :param element: ``xml.etree.ElementTree.Element``
    :return: text for leaves, dict of children, list when a tag repeats
    """
    children = list(element)
    if not children:
        return element.text
    result = {}
    for child in children:
        tag = child.tag.rsplit("}", 1)[-1]
        value = _xml_to_value(child)  # recurse so nested nodes become dicts
        if tag in result:
            if not isinstance(result[tag], list):
                result[tag] = [result[tag]]  # first duplicate: promote scalar to list
            result[tag].append(value)
        else:
            result[tag] = value
    return result


def parse_body(content_type, text):
    """Decode a raw HTTP body from Content-Type, then by sniffing.

    :param content_type: ``Content-Type`` header (charset suffix is ignored)
    :param text: response body as text
    :return: ``(parsed, error)`` — parsed JSON/XML/str/None, and ``AuthError`` or None
    """
    if not text:
        return None, None
    content_type = (content_type or "").split(";")[0].strip().lower()
    stripped = text.lstrip()
    # Header or sniff: JSON declared, or body looks like an object/array.
    if "json" in content_type or stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.loads(text), None
        except json.JSONDecodeError as exc:
            # Fail only when the server claimed JSON; sniffed `{` may be HTML.
            if "json" in content_type:
                return None, AuthError(code="parse", message=str(exc), details=text)
    if "xml" in content_type or stripped.startswith("<"):
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            if "xml" in content_type:
                return None, AuthError(code="parse", message=str(exc), details=text)
        else:
            tag = root.tag.rsplit("}", 1)[-1]
            return {tag: _xml_to_value(root)}, None
    return text, None


def unwrap_data(data, success_path=None):
    """Return the inner payload of a successful body.

    :param data: parsed body
    :param success_path: optional dotted path (connection ``success_path``)
    :return: unwrapped value, or ``data`` unchanged
    """
    if success_path:
        return get_by_path(data, success_path)
    if isinstance(data, dict):
        for key in WRAPPER_KEYS:
            # Only unwrap ``{"data": ...}``, not ``{"data": ..., "meta": ...}``.
            if key in data and len(data) == 1:
                return data[key]
    return data


def is_error_payload(data):
    """True if a JSON object is a business error despite HTTP 2xx.

    :param data: parsed body
    :return: bool
    """
    if not isinstance(data, dict):
        return False
    if data.get("success") is False:
        return True
    if data.get("error") or data.get("errors"):
        return True
    if data.get("error_description"):
        return True
    # RFC 7807 problem+json: detail plus title, type, or status.
    if data.get("detail") and (data.get("title") or data.get("type") or data.get("status")):
        return True
    return False


def _as_error_list(value, default_code):
    """Normalize one error shape into a list of ``AuthError``.

    :param value: dict, list, string, ``AuthError``, or None
    :param default_code: fallback ``AuthError.code``
    :return: list of ``AuthError``
    """
    if value is None or value is False:
        return []
    if isinstance(value, AuthError):
        return [value]
    if isinstance(value, list):
        errors = []
        for item in value:
            errors.extend(_as_error_list(item, default_code))  # flatten nested error lists
        return errors
    if isinstance(value, dict):
        message = (
            value.get("message")
            or value.get("Message")
            or value.get("error_description")
            or value.get("detail")
            or value.get("title")
            or value.get("error")
        )
        if isinstance(message, dict):
            return _as_error_list(message, default_code)  # nested ``error: {message: {...}}``
        code = str(value.get("code") or value.get("error") or default_code)
        if message:
            return [AuthError(code=code, message=str(message), details=value)]
        return [AuthError(code=default_code, message=str(value), details=value)]
    return [AuthError(code=default_code, message=str(value), details=value)]


def extract_errors(data, status=None, error_path=None):
    """Build ``AuthError`` list from a parsed error body.

    :param data: parsed body
    :param status: HTTP status, used as default code ``http_{status}``
    :param error_path: optional dotted path (connection ``error_path``)
    :return: list of ``AuthError`` (may be empty)
    """
    default_code = f"http_{status}" if status else "error"
    if error_path:
        return _as_error_list(get_by_path(data, error_path), default_code)
    if not isinstance(data, dict):
        if data:
            return [AuthError(code=default_code, message=str(data), details=data)]
        return []
    if data.get("errors"):
        return _as_error_list(data["errors"], default_code)
    nested = data.get("error")
    if isinstance(nested, dict) and nested.get("message"):
        return _as_error_list(nested, default_code)
    return _as_error_list(data, default_code)
