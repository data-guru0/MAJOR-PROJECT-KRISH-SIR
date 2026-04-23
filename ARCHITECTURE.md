# AI Code Reviewer — Architecture & Design Document

---

## What This Project Does (Simple)

A GitHub bot that automatically reviews every Pull Request using AI. When a developer opens a PR, the bot reads the code changes and posts comments pointing out security vulnerabilities, bugs, style issues, and architecture problems — just like a senior developer would, but instantly and automatically.

---

## End User Journey

```
Developer opens a Pull Request on GitHub
          |
          | (GitHub sends a webhook event automatically)
          v
Bot receives the PR within seconds
          |
          v
4 AI agents analyze the code in parallel (~15-30 seconds)
          |
          v
Bot posts review comments directly on the PR
  - Inline comments on specific lines of code
  - Summary of all issues found
          |
          v
Developer reads the comments, fixes the issues, pushes again
          |
          v
Bot reviews the new changes automatically (same flow repeats)
          |
          v
Developer merges the PR
          |
          v
System learns from this PR to improve future reviews
```

---

## Full System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         INTERNET                                     │
└─────────────────────────┬───────────────────────────────────────────┘
                          │ HTTPS webhook event
                          v
┌─────────────────────────────────────────────────────────────────────┐
│                    AWS LOAD BALANCER (ALB)                           │
│              Public IP — routes traffic into cluster                 │
└─────────────────────────┬───────────────────────────────────────────┘
                          │
                          v
┌─────────────────────────────────────────────────────────────────────┐
│                   GATEWAY SERVICE (port 8000)                        │
│                                                                      │
│  1. Receives every request from GitHub                               │
│  2. Verifies HMAC-SHA256 signature — proves request is really from  │
│     GitHub and not a fake/malicious request                          │
│  3. Forwards verified requests to Webhook service                    │
└─────────────────────────┬───────────────────────────────────────────┘
                          │ verified request
                          v
┌─────────────────────────────────────────────────────────────────────┐
│                  WEBHOOK SERVICE (port 8001)                         │
│                                                                      │
│  1. Parses the GitHub event — extracts PR number, repo, commit SHA  │
│  2. Checks if this exact commit was already analyzed (deduplication) │
│  3. Saves the PR record to PostgreSQL database                       │
│  4. Queues an analysis job in Redis (Celery task queue)              │
│  5. Returns 202 Accepted to GitHub immediately                       │
└──────────┬──────────────────────────────────────────────────────────┘
           │ queues task
           v
┌─────────────────────────────────────────────────────────────────────┐
│              REDIS (ElastiCache) — Task Queue                        │
│                                                                      │
│  Holds pending jobs. Workers pick them up and process them.         │
│  Two separate queues:                                                │
│    webhook  — analysis jobs                                          │
│    learning — pattern learning jobs                                  │
└──────────┬──────────────────────────────────────────────────────────┘
           │ worker picks up job
           v
┌─────────────────────────────────────────────────────────────────────┐
│              WEBHOOK WORKER (Celery Worker)                          │
│                                                                      │
│  Background worker that calls the Orchestrator service               │
│  Runs independently — does not block the Webhook service            │
└──────────┬──────────────────────────────────────────────────────────┘
           │ HTTP call
           v
┌─────────────────────────────────────────────────────────────────────┐
│               ORCHESTRATOR SERVICE (port 8002)                       │
│                                                                      │
│  1. Fetches the actual code diff from GitHub API                     │
│  2. Loads past patterns from PostgreSQL (what issues did this        │
│     repo have before?)                                               │
│  3. Runs LangGraph — 4 AI agents in parallel:                        │
│                                                                      │
│     ┌──────────────────┐  ┌──────────────────┐                      │
│     │  Static Analysis  │  │     Security      │                     │
│     │  - complexity     │  │  - OWASP Top 10  │                      │
│     │  - unused vars    │  │  - hardcoded keys│                      │
│     │  - bad naming     │  │  - SQL injection │                      │
│     └────────┬─────────┘  └────────┬─────────┘                      │
│              │                     │                                  │
│     ┌────────┴─────────┐  ┌────────┴─────────┐                      │
│     │      Style        │  │   Architecture   │                      │
│     │  - formatting    │  │  - separation of │                      │
│     │  - readability   │  │    concerns      │                      │
│     │  - consistency   │  │  - error handling│                      │
│     └────────┬─────────┘  └────────┬─────────┘                      │
│              └──────────┬──────────┘                                 │
│                         v                                            │
│               Merge + Deduplicate findings                           │
│                         │                                            │
│  4. Saves all findings to PostgreSQL                                 │
│  5. Calls Reviewer service                                           │
└──────────┬──────────────────────────────────────────────────────────┘
           │ findings data
           v
┌─────────────────────────────────────────────────────────────────────┐
│                 REVIEWER SERVICE (port 8003)                         │
│                                                                      │
│  1. Generates a GitHub installation token (JWT auth)                 │
│  2. Posts inline comments on specific lines of the PR               │
│  3. Posts a full summary review with all findings                   │
│  4. Falls back to body-only comment if inline fails                  │
│  5. Marks the PR as "reviewed" in PostgreSQL                         │
└──────────┬──────────────────────────────────────────────────────────┘
           │ GitHub API call
           v
┌─────────────────────────────────────────────────────────────────────┐
│                  GITHUB PULL REQUEST                                  │
│                                                                      │
│  Developer sees:                                                     │
│    - Inline comments on code lines (Files changed tab)              │
│    - Full review summary (Conversation tab)                          │
└─────────────────────────────────────────────────────────────────────┘

─── When PR is MERGED ──────────────────────────────────────────────────

GitHub sends "closed + merged" event
           │
           v
┌─────────────────────────────────────────────────────────────────────┐
│              LEARNER WORKER (Celery Worker)                           │
│                                                                      │
│  Calls Learner service                                               │
└──────────┬──────────────────────────────────────────────────────────┘
           v
┌─────────────────────────────────────────────────────────────────────┐
│                 LEARNER SERVICE (port 8004)                           │
│                                                                      │
│  Reads all warning/error findings from this PR                      │
│  Upserts them into the patterns table in PostgreSQL                 │
│  Next time the same repo gets a PR, the style agent gets these      │
│  patterns as context — making reviews smarter over time             │
└─────────────────────────────────────────────────────────────────────┘
```

---

## AWS Infrastructure

```
┌─────────────────────────────────────────────────────────────────────┐
│                        AWS Cloud (us-east-1)                         │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    VPC (Virtual Network)                      │   │
│  │                                                               │   │
│  │  ┌─────────────────────────────────────────────────────┐    │   │
│  │  │              EKS Cluster (Kubernetes)                │    │   │
│  │  │                                                      │    │   │
│  │  │   Node 1 (t3.medium)    Node 2 (t3.medium)          │    │   │
│  │  │   ┌─────────────────┐   ┌─────────────────┐         │    │   │
│  │  │   │ gateway pod x2  │   │ orchestrator x2 │         │    │   │
│  │  │   │ webhook pod x2  │   │ reviewer pod x2 │         │    │   │
│  │  │   │ learner pod x2  │   │ webhook-worker  │         │    │   │
│  │  │   │                 │   │ learner-worker  │         │    │   │
│  │  │   └─────────────────┘   └─────────────────┘         │    │   │
│  │  │                                                      │    │   │
│  │  │   Prometheus (monitoring namespace)                  │    │   │
│  │  │   Grafana    (monitoring namespace)                  │    │   │
│  │  └─────────────────────────────────────────────────────┘    │   │
│  │                                                               │   │
│  │  ┌───────────────┐  ┌───────────────┐  ┌─────────────────┐  │   │
│  │  │ RDS PostgreSQL│  │  ElastiCache  │  │   ECR (Docker   │  │   │
│  │  │   (db.t3.micro│  │  Redis        │  │   image repos)  │  │   │
│  │  │  pull_requests│  │  (cache.t3    │  │  gateway        │  │   │
│  │  │  findings     │  │   .micro)     │  │  webhook        │  │   │
│  │  │  patterns     │  │  Task queue   │  │  orchestrator   │  │   │
│  │  └───────────────┘  └───────────────┘  │  reviewer       │  │   │
│  │                                         │  learner        │  │   │
│  │                                         └─────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Technology Stack

### Application Layer

| Technology | What it does in this project |
|---|---|
| **Python 3.11** | All 5 services are written in Python |
| **FastAPI** | Web framework for all services — handles HTTP requests, fast and async |
| **Celery** | Background task queue — runs analysis jobs without blocking the webhook response |
| **LangGraph** | Runs 4 AI agents in parallel — manages the parallel execution and combines results |
| **OpenAI GPT-4o-mini** | The actual AI brain — reads code diffs and finds issues |
| **SQLAlchemy (async)** | Database ORM — reads and writes to PostgreSQL without blocking |
| **httpx** | HTTP client — calls GitHub API and internal services |
| **PyJWT** | Creates signed JWT tokens for GitHub App authentication |

### Infrastructure Layer

| Technology | What it does in this project |
|---|---|
| **AWS EKS** | Kubernetes cluster — runs all 5 services as containers |
| **AWS RDS (PostgreSQL 15)** | Database — stores pull requests, findings, and learned patterns |
| **AWS ElastiCache (Redis)** | In-memory task queue — passes jobs from webhook service to workers |
| **AWS ECR** | Container registry — stores Docker images for all 5 services |
| **AWS ALB** | Load balancer — receives traffic from internet and routes to the cluster |
| **AWS S3** | Object storage — available for report storage |
| **Terraform** | Infrastructure as code — creates and destroys all AWS resources with one command |

### Observability Layer

| Technology | What it does in this project |
|---|---|
| **Prometheus** | Scrapes metrics from all 5 services every 60 seconds |
| **Grafana** | Displays dashboards — request rate, latency, error rate per service |
| **LangFuse** | Traces every GPT call — shows exact prompt, response, token count, and cost |
| **prometheus-fastapi-instrumentator** | Auto-generates HTTP metrics for all FastAPI services |

### CI/CD Layer

| Technology | What it does in this project |
|---|---|
| **GitHub Actions** | 5 separate pipelines — one per service. Auto-deploys on every push |
| **Docker** | Each service is containerized — same image runs locally and on AWS |
| **GitHub OIDC** | Passwordless AWS authentication — no long-lived keys stored anywhere |

### Quality Layer

| Technology | What it does in this project |
|---|---|
| **RAGAS** | Evaluates AI comment quality every Monday — measures faithfulness and relevancy |
| **pytest** | Runs tests for each service on every pipeline run |
| **Kubernetes HPA** | Auto-scales orchestrator pods when PR volume is high |

---

## Database Schema

```
pull_requests
─────────────────────────────────
id            UUID  (primary key)
repo_full_name TEXT
pr_number      INT
head_sha       TEXT  (used for deduplication)
installation_id BIGINT
status         TEXT  (pending → reviewed)
created_at     TIMESTAMP

findings
─────────────────────────────────
id       UUID  (primary key)
pr_id    UUID  (foreign key → pull_requests)
file     TEXT  (which file had the issue)
line     INT   (which line)
severity TEXT  (info / warning / error / critical)
message  TEXT  (the actual AI comment)
agent    TEXT  (which agent found it: static_analysis / security / style / architecture)
created_at TIMESTAMP

patterns
─────────────────────────────────
id             UUID  (primary key)
repo_full_name TEXT
pattern_text   TEXT  (the issue message that keeps appearing)
frequency      INT   (how many times this pattern was seen)
updated_at     TIMESTAMP
```

---

## CI/CD Pipeline Flow

```
Developer pushes code to GitHub
          |
          v
GitHub Actions triggers the pipeline for that service only
          |
          ├── Job 1: TEST
          │     Install dependencies
          │     Run pytest
          │     (fails here if tests break — nothing gets deployed)
          │
          ├── Job 2: BUILD AND PUSH
          │     Build Docker image
          │     Push to ECR with commit SHA as tag
          │     Push again as :latest
          │
          └── Job 3: DEPLOY
                Connect to EKS cluster
                kubectl set image — rolling update
                Old pods stay running until new ones are healthy
                Zero downtime deployment
```

---

## Production Grade Features

| Feature | How it is implemented |
|---|---|
| **Zero downtime deploys** | Kubernetes rolling updates — new pods start before old ones stop |
| **Auto scaling** | HPA scales orchestrator from 2 to 10 pods based on CPU usage |
| **No duplicate analysis** | Webhook deduplicates by repo + PR number + commit SHA |
| **Fault tolerance** | 2 replicas per service — if one pod crashes, traffic goes to the other |
| **Async everything** | All database and HTTP calls are non-blocking (asyncio + asyncpg) |
| **Secure auth** | GitHub OIDC for AWS — no stored access keys. JWT for GitHub App auth |
| **HMAC verification** | Every webhook verified with SHA-256 signature before processing |
| **Graceful fallback** | If inline GitHub comments fail (422), falls back to review body comment |
| **Background processing** | Celery workers decouple analysis from HTTP response — GitHub gets 202 immediately |
| **Dedicated queues** | Webhook tasks and learning tasks on separate queues — one can't block the other |
| **Metrics** | Every service exposes /metrics — Prometheus scrapes, Grafana displays |
| **AI tracing** | Every GPT call traced in LangFuse — full prompt, response, cost, latency |
| **Weekly quality check** | RAGAS evaluates AI comment quality every Monday — alerts if quality drops |
| **Self-improving** | Learner service stores patterns from merged PRs — style agent gets smarter per repo |

---

## How the AI Works

```
Code Diff (what changed in the PR)
          │
          ├──────────────────────────────────────────────────────┐
          │                                                       │
          v                                                       v
┌─────────────────────┐                             ┌────────────────────────┐
│   Static Analysis   │                             │       Security          │
│                     │                             │                        │
│  Prompt:            │                             │  Prompt:               │
│  "You are a static  │                             │  "You are a security   │
│  analysis tool.     │                             │  scanner. Find OWASP   │
│  Find complexity,   │                             │  Top 10 vulns,         │
│  unused vars,       │                             │  hardcoded secrets,    │
│  bad naming..."     │                             │  SQL injection..."     │
│                     │                             │                        │
│  GPT-4o-mini        │                             │  GPT-4o-mini           │
│  responds with      │                             │  responds with         │
│  JSON array         │                             │  JSON array            │
└─────────┬───────────┘                             └────────────┬───────────┘
          │                                                       │
          │                    (parallel)                         │
          v                                                       v
┌─────────────────────┐                             ┌────────────────────────┐
│       Style          │                             │     Architecture        │
│                     │                             │                        │
│  Prompt includes    │                             │  Prompt:               │
│  past patterns from │                             │  "Find separation of   │
│  this repo — so it  │                             │  concerns violations,  │
│  knows what issues  │                             │  missing error         │
│  this team has had  │                             │  handling..."          │
│  before             │                             │                        │
└─────────┬───────────┘                             └────────────┬───────────┘
          │                                                       │
          └───────────────────────┬───────────────────────────────┘
                                  v
                    Merge all findings
                    Remove duplicates
                    Save to database
                    Post to GitHub PR
```

---

## Security Design

| Concern | Solution |
|---|---|
| Fake webhook requests | HMAC-SHA256 signature verified on every request at the gateway |
| AWS credentials in code | GitHub OIDC — temporary credentials issued per pipeline run, no stored keys |
| GitHub App auth | Short-lived JWT (10 min expiry) + installation token, never stored |
| Database access | RDS inside private VPC subnet — not accessible from internet |
| Redis access | ElastiCache inside private VPC subnet — not accessible from internet |
| Container images | Stored in private ECR — not public |

---

## Request Flow Timing

```
0ms    — GitHub sends webhook
~5ms   — Gateway verifies signature, forwards to webhook service
~20ms  — Webhook service saves PR to DB, queues Celery task
~25ms  — GitHub receives 202 Accepted response

(background — user does not wait for this)

~1s    — Celery worker picks up task, calls orchestrator
~2s    — Orchestrator fetches diff from GitHub API
~3s    — 4 AI agents start in parallel
~15-25s — All 4 agents complete (GPT-4o-mini response time)
~26s   — Findings merged, saved to DB
~27s   — Reviewer posts comments to GitHub PR

Total time from PR open to review comments: ~30 seconds
```
