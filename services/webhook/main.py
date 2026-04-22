from fastapi import FastAPI, Request

from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
import uuid

from models import Settings, PullRequest, Base
from worker import analyze_pr, trigger_learning

settings = Settings()
engine = create_async_engine(settings.database_url)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

app = FastAPI()
Instrumentator().instrument(app).expose(app)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/events", status_code=202)
async def receive_event(request: Request):
    body = await request.json()

    action = body.get("action", "")
    pull_request = body.get("pull_request", {})

    if action == "closed" and pull_request.get("merged"):
        pr_number = pull_request.get("number")
        repo_full_name = body.get("repository", {}).get("full_name", "")
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(PullRequest).where(
                    PullRequest.repo_full_name == repo_full_name,
                    PullRequest.pr_number == pr_number,
                )
            )
            pr = result.scalar_one_or_none()
            if pr:
                trigger_learning.delay(repo_full_name, str(pr.id))
        return {"status": "accepted"}

    if action not in ("opened", "reopened", "synchronize"):
        return {"status": "skipped"}

    pr_number = pull_request.get("number")
    repo_full_name = body.get("repository", {}).get("full_name", "")
    head_sha = pull_request.get("head", {}).get("sha", "")
    installation_id = body.get("installation", {}).get("id", 0)

    pr_record = PullRequest(
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        head_sha=head_sha,
        installation_id=installation_id,
        status="pending",
    )

    async with AsyncSessionLocal() as session:
        session.add(pr_record)
        await session.commit()
        await session.refresh(pr_record)
        pr_id = str(pr_record.id)

    analyze_pr.delay(pr_id, pr_number, repo_full_name, head_sha, installation_id)

    return {"status": "accepted"}
