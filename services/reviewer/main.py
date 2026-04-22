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

    if not request.findings:
        return {"status": "ok"}

    inline_comments = []
    for f in request.findings:
        severity = f.get("severity", "info").upper()
        file_ref = f.get("file", "")
        line_ref = f.get("line")
        agent = f.get("agent", "")
        message = f.get("message", "")
        if file_ref and isinstance(line_ref, int) and line_ref > 0:
            inline_comments.append({
                "path": file_ref,
                "line": line_ref,
                "side": "RIGHT",
                "body": f"**[{severity}]** ({agent})\n{message}",
            })

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = f"https://api.github.com/repos/{request.repo_full_name}/pulls/{request.pr_number}/reviews"

    async with httpx.AsyncClient() as client:
        summary_lines = ["## AI Code Review\n"]
        for f in request.findings:
            severity = f.get("severity", "info").upper()
            file_ref = f.get("file", "unknown")
            line_ref = f.get("line", "?")
            agent = f.get("agent", "")
            message = f.get("message", "")
            summary_lines.append(f"**[{severity}]** `{file_ref}:{line_ref}` ({agent})\n{message}\n")
        summary_body = "\n".join(summary_lines)

        payload = {"event": "COMMENT", "body": summary_body, "comments": inline_comments}
        response = await client.post(url, json=payload, headers=headers, timeout=30)

        if response.status_code == 422 and inline_comments:
            # Fall back: post findings as a single review body comment
            body_lines = ["## AI Code Review\n"]
            for f in request.findings:
                severity = f.get("severity", "info").upper()
                file_ref = f.get("file", "unknown")
                line_ref = f.get("line", "?")
                agent = f.get("agent", "")
                message = f.get("message", "")
                body_lines.append(f"**[{severity}]** `{file_ref}:{line_ref}` ({agent})\n{message}\n")
            fallback_payload = {"event": "COMMENT", "body": "\n".join(body_lines)}
            response = await client.post(url, json=fallback_payload, headers=headers, timeout=30)

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
