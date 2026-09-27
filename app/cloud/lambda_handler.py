"""AWS Lambda entry point: API Gateway (HTTP API, payload v2) -> Mangum -> the read-only cloud app."""
from mangum import Mangum

from app.cloud.app import app

handler = Mangum(app, lifespan="off")
