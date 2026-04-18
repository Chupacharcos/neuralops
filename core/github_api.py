"""GitHub API helpers usados por múltiples agentes."""
import os
import httpx
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")

GH_TOKEN = os.getenv("GITHUB_TOKEN")
GH_USER = os.getenv("GITHUB_USERNAME")
BASE = "https://api.github.com"
HEADERS = {
    "Authorization": f"token {GH_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}


async def create_issue(repo: str, title: str, body: str, labels: list[str] = None) -> str | None:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE}/repos/{GH_USER}/{repo}/issues",
            headers=HEADERS,
            json={"title": title, "body": body, "labels": labels or []},
        )
        if resp.status_code == 201:
            return resp.json()["html_url"]
    return None


async def create_pr(repo: str, title: str, body: str, head: str, base: str = "main") -> str | None:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE}/repos/{GH_USER}/{repo}/pulls",
            headers=HEADERS,
            json={"title": title, "body": body, "head": head, "base": base},
        )
        if resp.status_code == 201:
            return resp.json()["html_url"]
    return None


async def list_issues(repo: str, state: str = "open") -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE}/repos/{GH_USER}/{repo}/issues",
            headers=HEADERS,
            params={"state": state, "per_page": 50},
        )
        return resp.json() if resp.status_code == 200 else []


async def get_repo_info(repo: str) -> dict | None:
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BASE}/repos/{GH_USER}/{repo}", headers=HEADERS)
        return resp.json() if resp.status_code == 200 else None
