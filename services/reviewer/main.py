import time

import httpx

import jwt
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from models import Settings, PullRequest, ReviewRequest

settings = Settings()
engine = create_async_engine(settings.database_url)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

app = FastAPI()
Instrumentator().instrument(app).expose(app)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/post-review")
async def post_review(request: ReviewRequest):
    token = get_installation_token(request.installation_id)

    comments = [
        {"path": f.get("file"), "line": f.get("line"), "body": f.get("message")}
        for f in request.findings
        if f.get("file") and f.get("line") and f.get("message")
    ]

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.github.com/repos/{request.repo_full_name}/pulls/{request.pr_number}/reviews",
            json={"event": "COMMENT", "comments": comments},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.v3+json",
            },
            timeout=30,
        )
        response.raise_for_status()

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(PullRequest)
            .where(PullRequest.id == request.pr_id)
            .values(status="reviewed")
        )
        await session.commit()

    return {"status": "ok"}


def get_installation_token(installation_id: int) -> str:
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,
        "iss": settings.github_app_id,
    }
    private_key = settings.github_app_private_key.replace("\\n", "\n")
    encoded_jwt = jwt.encode(payload, private_key, algorithm="RS256")

    with httpx.Client() as client:
        response = client.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {encoded_jwt}",
                "Accept": "application/vnd.github.v3+json",
            },
        )
        response.raise_for_status()
        return response.json()["token"]
