"""
API Security Middleware (Step 24.1)

Comprehensive API security measures.

Features:
- Request validation
- SQL injection prevention
- XSS prevention
- CSRF protection
- Content-Type validation
- Request size limiting
- Security headers
"""

import re
from typing import Callable, Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse


# SQL injection patterns
SQL_INJECTION_PATTERNS = [
    r"(\b(SELECT|INSERT|UPDATE|DELETE|DROP|UNION|ALTER|CREATE|EXEC)\b)",
    r"(--|;|/\*|\*/|@@|@)",
    r"(\b(OR|AND)\b\s+\d+\s*=\s*\d+)",
    r"(\b(OR|AND)\b\s+['\"].*['\"])",
    r"(CHAR\(|CONCAT\(|INSERT\(|SELECT\()",
]

# XSS patterns
XSS_PATTERNS = [
    r"(<script[^>]*>.*?</script>)",
    r"(javascript:)",
    r"(on\w+\s*=)",
    r"(<iframe[^>]*>)",
    r"(<object[^>]*>)",
    r"(<embed[^>]*>)",
    r"(<form[^>]*>)",
]

# Path traversal patterns
PATH_TRAVERSAL_PATTERNS = [
    r"(\.\./)",
    r"(\.\.\\)",
    r"(%2e%2e%2f)",
    r"(%2e%2e/)",
    r"(\.\.%2f)",
    r"(%2e%2e%5c)",
]


class SecurityMiddleware(BaseHTTPMiddleware):
    """Comprehensive security middleware."""

    def __init__(
        self,
        app,
        max_request_size: int = 10 * 1024 * 1024,  # 10MB
        block_sql_injection: bool = True,
        block_xss: bool = True,
        block_path_traversal: bool = True,
        require_content_type: bool = True,
        large_upload_paths: tuple = (
            "/compliance/deals/recording",
            "/ingestion/campaigns/upload",
        ),
    ):
        super().__init__(app)
        self.max_request_size = max_request_size
        self.block_sql_injection = block_sql_injection
        self.block_xss = block_xss
        self.block_path_traversal = block_path_traversal
        self.require_content_type = require_content_type
        # Paths that legitimately accept large file uploads, exempt from
        # max_request_size. Both are admin-authenticated and the handler
        # validates the payload:
        #   - call recordings: a long WAV call is uncompressed (~10 MB/min).
        #   - campaign CSV upload: a big lead list (e.g. 60k leads) is ~16 MB,
        #     which otherwise hit the 10 MB cap and failed with 413.
        self.large_upload_paths = large_upload_paths

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Check request size — but skip the cap for audio-recording uploads,
        # which can legitimately exceed it (a 50-min WAV call is ~500 MB).
        path = request.url.path or ""
        is_large_upload = any(seg in path for seg in self.large_upload_paths)
        content_length = request.headers.get("content-length")
        if content_length and not is_large_upload and int(content_length) > self.max_request_size:
            return JSONResponse(
                status_code=413,
                content={"detail": "Request entity too large"},
            )

        # Check Content-Type for requests that actually carry a body. Some
        # command-style POST endpoints intentionally have no request payload.
        if self.require_content_type and request.method in ["POST", "PUT", "PATCH"]:
            has_body = bool(content_length and int(content_length) > 0) or bool(
                request.headers.get("transfer-encoding")
            )
            content_type = request.headers.get("content-type", "")
            if has_body and not content_type:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Content-Type header required"},
                )

        # Validate URL path
        if self.block_path_traversal:
            if self._contains_path_traversal(str(request.url)):
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid request path"},
                )

        # Validate query parameters
        if self.block_sql_injection:
            for key, value in request.query_params.items():
                if self._contains_sql_injection(value):
                    return JSONResponse(
                        status_code=400,
                        content={"detail": "Invalid query parameter"},
                    )
                if self.block_xss and self._contains_xss(value):
                    return JSONResponse(
                        status_code=400,
                        content={"detail": "Invalid query parameter"},
                    )

        # Process request
        response = await call_next(request)

        # Add security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        return response

    def _contains_sql_injection(self, value: str) -> bool:
        """Check if value contains SQL injection patterns."""
        value_upper = value.upper()
        for pattern in SQL_INJECTION_PATTERNS:
            if re.search(pattern, value_upper, re.IGNORECASE):
                return True
        return False

    def _contains_xss(self, value: str) -> bool:
        """Check if value contains XSS patterns."""
        for pattern in XSS_PATTERNS:
            if re.search(pattern, value, re.IGNORECASE):
                return True
        return False

    def _contains_path_traversal(self, url: str) -> bool:
        """Check if URL contains path traversal patterns."""
        for pattern in PATH_TRAVERSAL_PATTERNS:
            if re.search(pattern, url, re.IGNORECASE):
                return True
        return False


class InputSanitizer:
    """Sanitizes user input."""

    @staticmethod
    def sanitize_string(value: str, max_length: int = 1000) -> str:
        """Sanitize a string input."""
        if not value:
            return ""

        # Truncate
        value = value[:max_length]

        # Remove null bytes
        value = value.replace("\x00", "")

        # Strip whitespace
        value = value.strip()

        return value

    @staticmethod
    def sanitize_email(email: str) -> str:
        """Sanitize an email address."""
        if not email:
            return ""

        email = email.strip().lower()

        # Basic validation
        if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
            return ""

        return email

    @staticmethod
    def sanitize_phone(phone: str) -> str:
        """Sanitize a phone number."""
        if not phone:
            return ""

        # Remove all non-numeric characters except +
        phone = re.sub(r"[^\d+]", "", phone)

        return phone

    @staticmethod
    def sanitize_uuid(value: str) -> Optional[str]:
        """Sanitize a UUID string."""
        if not value:
            return None

        # Validate UUID format
        uuid_pattern = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        if not re.match(uuid_pattern, value, re.IGNORECASE):
            return None

        return value.lower()

    @staticmethod
    def sanitize_integer(value: str, min_val: int = None, max_val: int = None) -> Optional[int]:
        """Sanitize an integer input."""
        try:
            int_val = int(value)
            if min_val is not None and int_val < min_val:
                return None
            if max_val is not None and int_val > max_val:
                return None
            return int_val
        except (ValueError, TypeError):
            return None

    @staticmethod
    def sanitize_html(value: str) -> str:
        """Remove HTML tags from string."""
        if not value:
            return ""

        # Remove HTML tags
        clean = re.sub(r"<[^>]+>", "", value)

        # Decode HTML entities
        clean = clean.replace("&amp;", "&")
        clean = clean.replace("&lt;", "<")
        clean = clean.replace("&gt;", ">")
        clean = clean.replace("&quot;", '"')
        clean = clean.replace("&#x27;", "'")

        return clean


class CSRFProtection:
    """CSRF protection utilities."""

    @staticmethod
    def generate_csrf_token() -> str:
        """Generate a CSRF token."""
        import secrets
        return secrets.token_hex(32)

    @staticmethod
    def validate_csrf_token(token: str, expected: str) -> bool:
        """Validate a CSRF token."""
        import hmac
        return hmac.compare_digest(token, expected)


class RequestValidator:
    """Validates request data."""

    @staticmethod
    def validate_pagination(page: int, size: int) -> tuple:
        """Validate pagination parameters."""
        page = max(1, page)
        size = max(1, min(100, size))
        return page, size

    @staticmethod
    def validate_sort_field(field: str, allowed_fields: list) -> str:
        """Validate sort field."""
        if field.lstrip("-") not in allowed_fields:
            return allowed_fields[0] if allowed_fields else "id"
        return field

    @staticmethod
    def validate_date_range(start_date: str, end_date: str) -> tuple:
        """Validate date range."""
        from datetime import datetime

        try:
            start = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
            end = datetime.fromisoformat(end_date.replace("Z", "+00:00"))

            if start > end:
                start, end = end, start

            return start, end
        except (ValueError, AttributeError):
            return None, None
