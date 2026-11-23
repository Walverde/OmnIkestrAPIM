from fastapi import APIRouter, Response
from starlette.middleware.base import BaseHTTPMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest


router = APIRouter(tags=["observability"])

HTTP_REQUESTS = Counter(
    "omnikestr_http_requests_total",
    "HTTP requests served by the API.",
    ["method", "route", "status"],
)
QUEUE_JOBS = Counter(
    "omnikestr_marketplace_jobs_total",
    "Marketplace jobs completed, retried, or failed.",
    ["provider", "status"],
)


class HTTPMetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        route = request.scope.get("route")
        route_path = getattr(route, "path", None)
        if not route_path:
            route_path = "/<unmatched>"
        HTTP_REQUESTS.labels(
            method=request.method,
            route=route_path,
            status=str(response.status_code),
        ).inc()
        return response


@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
