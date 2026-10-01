"""Security response headers on every HTTP response the API sends.

A pure ASGI middleware rather than Starlette's ``BaseHTTPMiddleware``: it only
adds headers to the ``http.response.start`` message and passes every body
chunk straight through, so SSE streams (``/api/ask``, ``/api/cluster/stream``)
keep their framing, heartbeats and ``X-Accel-Buffering: no``, nothing is
buffered or compressed, and disconnect handling is unchanged. It wraps the
whole app, so the static frontend mount, API JSON and 404s all get the same
headers.

Why the app and not a Cloudflare response-header transform rule: here the
headers are unit-tested, reviewed and shipped with the code (live on the next
release, no Terraform apply), and they also apply in local dev and compose.
A Cloudflare rule would need a new ruleset phase in infra/modules/edge plus
Transform Rules permissions on both Cloudflare tokens, which only the owner
can change. A response that already carries one of these headers keeps its
own value.

Known gap: Starlette's ServerErrorMiddleware sits outside every user
middleware, so the bare 500 page for an unhandled exception goes out without
these headers. It is a plain-text body with no script, so nothing to frame or
sniff.

Deliberately not here: a script/style-restricting Content-Security-Policy.
The CSP below only sets ``frame-ancestors``; restricting scripts and styles
needs an audit of the Vite build first (see docs/DESIGN.md §11).
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# HSTS: 180 days, no preload. No includeSubDomains: only the apex record is
# managed in Terraform (infra/modules/edge). Other names in the zone (www, and
# anything added by hand) aren't, so nothing guarantees they all serve HTTPS,
# and the header must not commit them to it.
HSTS_VALUE = "max-age=15552000"

# Browser features this site never uses. Only well-supported feature names,
# so browsers don't log "unrecognized feature" warnings. Clipboard is left
# alone: the Contact control copies the email address.
PERMISSIONS_POLICY_VALUE = ", ".join(
    f"{feature}=()"
    for feature in (
        "accelerometer",
        "autoplay",
        "camera",
        "display-capture",
        "encrypted-media",
        "fullscreen",
        "geolocation",
        "gyroscope",
        "magnetometer",
        "microphone",
        "midi",
        "payment",
        "picture-in-picture",
        "publickey-credentials-get",
        "screen-wake-lock",
        "usb",
        "xr-spatial-tracking",
    )
)

SECURITY_HEADERS: dict[str, str] = {
    "Strict-Transport-Security": HSTS_VALUE,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Permissions-Policy": PERMISSIONS_POLICY_VALUE,
}


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, headers: dict[str, str] | None = None) -> None:
        self.app = app
        self.headers = SECURITY_HEADERS if headers is None else headers

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                for name, value in self.headers.items():
                    if name not in response_headers:
                        response_headers.append(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)
