from fastapi import APIRouter, Response
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
@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
