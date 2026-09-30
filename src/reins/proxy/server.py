"""Local transparent reverse proxy server."""

from __future__ import annotations

import json
import logging
from decimal import Decimal

from aiohttp import ClientSession, web

from reins.core.config import ReinsConfig
from reins.core.events import EventBus
from reins.core.models import SpanData
from reins.core.storage import Storage

logger = logging.getLogger("reins.proxy")

ANTHROPIC_UPSTREAM = "https://api.anthropic.com"
OPENAI_UPSTREAM = "https://api.openai.com"

# Headers to not forward
_HOP_HEADERS = frozenset({"host", "content-length", "transfer-encoding", "connection"})


class ReinsProxy:
    """Local transparent proxy with budget enforcement."""

    def __init__(
        self,
        config: ReinsConfig,
        event_bus: EventBus,
        storage: Storage,
        modules: list,
        budget: Decimal | None = None,
        on_exceed: str = "alert",
    ):
        self._config = config
        self._event_bus = event_bus
        self._storage = storage
        self._modules = modules
        self._budget = budget
        self._on_exceed = on_exceed
        self._app = web.Application()
        self._setup_routes()

    def _setup_routes(self) -> None:
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_route("*", "/v1/messages", self._handle_anthropic_messages)
        self._app.router.add_route("*", "/v1/{path:.*}", self._handle_anthropic_passthrough)

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "service": "reins-proxy"})

    async def _handle_anthropic_messages(self, request: web.Request) -> web.Response:
        """Intercept Anthropic Messages API: budget check → degrade → forward → record."""
        body = await request.json()
        is_stream = body.get("stream", False)

        span = SpanData.from_llm_call("anthropic", body)
        span.metadata["agent_name"] = request.headers.get("X-Reins-Agent", "proxy")
        span.metadata["on_exceed"] = self._on_exceed

        # Pre-hooks (budget check, possible degradation)
        for module in self._modules:
            try:
                span = module.on_span_start(span)
            except Exception as e:
                return web.json_response(
                    {"error": {"type": "budget_error", "message": str(e)}},
                    status=429,
                )

        # Apply degradation
        if span.degraded:
            body["model"] = span.model
            logger.info("Degraded: %s → %s", span.model_requested, span.model)

        # Forward headers
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_HEADERS}

        upstream_url = f"{ANTHROPIC_UPSTREAM}/v1/messages"

        if is_stream:
            return await self._forward_stream(upstream_url, body, headers, span)
        else:
            return await self._forward_json(upstream_url, body, headers, span)

    async def _forward_json(
        self, url: str, body: dict, headers: dict, span: SpanData
    ) -> web.Response:
        """Forward non-streaming request."""
        async with ClientSession() as session:
            async with session.post(url, json=body, headers=headers) as resp:
                resp_body = await resp.read()

                try:
                    resp_json = json.loads(resp_body)
                    usage = resp_json.get("usage", {})
                    span.complete_from_usage_dict(usage)
                except Exception:
                    span.mark_error(Exception("Failed to parse response"))

                self._finalize_span(span)

                return web.Response(
                    body=resp_body,
                    status=resp.status,
                    content_type="application/json",
                )

    async def _forward_stream(
        self, url: str, body: dict, headers: dict, span: SpanData
    ) -> web.StreamResponse:
        """Forward SSE streaming request, accumulate usage at the end."""
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
            },
        )
        await response.prepare(request=None)  # type: ignore[arg-type]

        usage: dict[str, int] = {}

        async with ClientSession() as session:
            async with session.post(url, json=body, headers=headers) as resp:
                # For streaming, we need to set up the response first
                response = web.StreamResponse(
                    status=resp.status,
                    headers={
                        "Content-Type": resp.headers.get("Content-Type", "text/event-stream"),
                    },
                )
                # Note: response.prepare needs the actual request object
                # This is handled by aiohttp when returning from the handler

                chunks = []
                async for chunk in resp.content.iter_any():
                    chunks.append(chunk)
                    # Try to extract usage from message_delta events
                    self._extract_usage_from_chunk(chunk, usage)

        span.complete_from_usage_dict(usage)
        self._finalize_span(span)

        # For simplicity in MVP, buffer the full stream and return
        # (true streaming pass-through is Phase 2)
        full_body = b"".join(chunks)
        return web.Response(
            body=full_body,
            status=200,
            content_type="text/event-stream",
        )

    def _extract_usage_from_chunk(self, chunk: bytes, usage: dict[str, int]) -> None:
        """Extract usage info from SSE chunks."""
        try:
            text = chunk.decode("utf-8")
            for line in text.split("\n"):
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    if "usage" in data:
                        u = data["usage"]
                        usage["input_tokens"] = u.get("input_tokens", usage.get("input_tokens", 0))
                        usage["output_tokens"] = u.get(
                            "output_tokens", usage.get("output_tokens", 0)
                        )
        except Exception:
            pass

    async def _handle_anthropic_passthrough(self, request: web.Request) -> web.Response:
        """Pass through non-messages requests."""
        path = request.match_info["path"]
        url = f"{ANTHROPIC_UPSTREAM}/v1/{path}"
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_HEADERS}

        body = await request.read() if request.can_read_body else None

        async with ClientSession() as session:
            async with session.request(request.method, url, headers=headers, data=body) as resp:
                resp_body = await resp.read()
                return web.Response(
                    body=resp_body,
                    status=resp.status,
                    content_type=resp.headers.get("Content-Type", "application/json"),
                )

    def _finalize_span(self, span: SpanData) -> None:
        """Post-hooks + persist."""
        for module in self._modules:
            try:
                module.on_span_end(span)
            except Exception:
                logger.debug("Module on_span_end error", exc_info=True)

        try:
            self._storage.insert_span(span)
        except Exception:
            logger.debug("Failed to persist span", exc_info=True)

    @property
    def app(self) -> web.Application:
        return self._app

    async def start(self, host: str = "localhost", port: int = 8082) -> None:
        runner = web.AppRunner(self._app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        logger.info("Reins proxy running at http://%s:%d", host, port)


def create_proxy_app(
    budget: str | None = None,
    on_exceed: str = "alert",
    config_path: str | None = None,
) -> web.Application:
    """Create the proxy ASGI app (for CLI startup)."""
    from reins.core.loader import load_modules

    config = ReinsConfig.load(config_path)
    event_bus = EventBus()
    storage = Storage(config.storage_path)
    modules = load_modules(event_bus, storage, config)

    budget_decimal = None
    if budget:
        from reins.core.config import _parse_money

        budget_decimal = _parse_money(budget)

    proxy = ReinsProxy(
        config=config,
        event_bus=event_bus,
        storage=storage,
        modules=modules,
        budget=budget_decimal,
        on_exceed=on_exceed,
    )
    return proxy.app
