"""Small MaaS HTTP client used by the analysis and legacy translation paths."""
from __future__ import annotations

from dataclasses import dataclass
import logging

import httpx

import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MaaSCompletion:
    """Completion content together with safe-to-persist upstream diagnostics."""

    content: str
    response_metadata: dict[str, int | str | None]


class MaaSClientError(RuntimeError):
    """A MaaS HTTP/response-envelope failure with diagnostic metadata."""

    def __init__(self, message: str, *, code: str, response_metadata: dict | None = None,
                 raw_response: str | None = None):
        super().__init__(message)
        self.code = code
        self.response_metadata = response_metadata or {}
        self.raw_response = raw_response


def is_maas_configured() -> bool:
    # Read the config module at call time. The admin settings endpoint updates
    # these values in-process; importing scalar copies here would keep using
    # the old URL/key until a backend restart.
    return bool(str(config.MAAS_BASE_URL or '').strip() and str(config.MAAS_APP_KEY or '').strip())


def _endpoint() -> str:
    base = str(config.MAAS_BASE_URL or '').rstrip('/')
    return f"{base}/v1/chat/completions" if base.endswith('/api') else f"{base}/api/v1/chat/completions"


def _response_metadata(response: httpx.Response, payload: object | None = None) -> dict[str, int | str | None]:
    """Return diagnostics useful for an upstream support ticket, never credentials."""
    headers = response.headers
    header_request_id = next((headers.get(name) for name in (
        "x-request-id", "request-id", "x-amzn-requestid", "x-amz-request-id",
    ) if headers.get(name)), None)
    body_request_id = None
    if isinstance(payload, dict):
        body_request_id = payload.get("request_id") or payload.get("requestId") or payload.get("id")
    return {
        "http_status": response.status_code,
        "response_body_bytes": len(response.content),
        "upstream_request_id": str(header_request_id) if header_request_id else None,
        "maas_response_id": str(body_request_id) if body_request_id else None,
        # Prefer the header correlation ID when the provider supplies one.
        "request_id": str(header_request_id or body_request_id) if (header_request_id or body_request_id) else None,
    }


async def chat_completion(messages: list[dict[str, str]], timeout: int = 200) -> MaaSCompletion:
    if not is_maas_configured():
        raise MaaSClientError(
            'MaaS API 未配置，请在 backend/.env 中设置 MAAS_BASE_URL 和 MAAS_APP_KEY',
            code="maas_not_configured",
        )
    endpoint = _endpoint()
    # Do not let an unreachable host hold every claimed slice for the full
    # model timeout. Fail fast on connection/pool failures, but retain the
    # longer read timeout for a valid, slow MaaS response.
    http_timeout = httpx.Timeout(
        connect=min(float(timeout), 15.0),
        read=float(timeout),
        write=min(float(timeout), 15.0),
        pool=15.0,
    )
    logger.info("MaaS request started endpoint=%s messages=%d", endpoint, len(messages))
    async with httpx.AsyncClient(timeout=http_timeout, trust_env=False) as client:
        # Mass chat API is OpenAI-compatible. Do not send model/temperature
        # (they are ignored). Omit chatId so this stays a one-shot call and
        # history is not stored. Explicit stream=false is required: the
        # platform may otherwise stream SSE, and httpx would hang waiting
        # for a single JSON object.
        try:
            response = await client.post(
                endpoint,
                headers={'Authorization': f'Bearer {config.MAAS_APP_KEY}', 'Content-Type': 'application/json'},
                json={'stream': False, 'messages': messages},
            )
        except httpx.TimeoutException as exc:
            logger.warning("MaaS request timeout endpoint=%s error=%s", endpoint, exc)
            raise MaaSClientError(
                f"MaaS 请求超时（连接/读取超过 {timeout} 秒）",
                code="maas_timeout",
                response_metadata={"endpoint": endpoint},
            ) from exc
        except httpx.RequestError as exc:
            logger.warning("MaaS network error endpoint=%s error=%s", endpoint, exc)
            raise MaaSClientError(
                f"MaaS 网络请求失败：{exc}",
                code="maas_network_error",
                response_metadata={"endpoint": endpoint},
            ) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            metadata = _response_metadata(response)
            raise MaaSClientError(
                f"MaaS HTTP {response.status_code} 返回体不是合法 JSON",
                code="invalid_maas_http_response",
                response_metadata=metadata,
                raw_response=response.text,
            ) from exc
        metadata = _response_metadata(response, payload)
        if response.is_error:
            raise MaaSClientError(
                f"MaaS HTTP {response.status_code}",
                code="maas_http_error",
                response_metadata=metadata,
                raw_response=response.text,
            )
        if not isinstance(payload, dict):
            raise MaaSClientError(
                "MaaS 返回 JSON 根节点不是对象",
                code="invalid_maas_http_response",
                response_metadata=metadata,
                raw_response=response.text,
            )
        choices = payload.get('choices') or []
        if not choices or not isinstance(choices[0], dict):
            raise MaaSClientError(
                'MaaS 返回中缺少 choices',
                code="invalid_maas_http_response",
                response_metadata=metadata,
                raw_response=response.text,
            )
        message = choices[0].get('message') or {}
        content = message.get('content') if isinstance(message, dict) else None
        if content is None:
            raise MaaSClientError(
                'MaaS 返回中缺少 message.content',
                code="invalid_maas_http_response",
                response_metadata=metadata,
                raw_response=response.text,
            )
        completion = str(content)
        return MaaSCompletion(
            content=completion,
            response_metadata={**metadata, "completion_content_chars": len(completion)},
        )


async def translate_to_chinese(text: str) -> str:
    """Legacy helper retained for the old KB translation endpoint."""
    result = await chat_completion([
        {'role': 'system', 'content': '请将用户提供的文本翻译为简体中文，只输出译文。'},
        {'role': 'user', 'content': str(text or '')},
    ], timeout=200)
    return result.content.strip()
