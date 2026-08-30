from __future__ import annotations

import base64
import re

import httpx

from .config import Settings, settings


class GitHubPublishError(RuntimeError):
    pass


def repo_slug(title: str, identifier: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or "paper"
    source = re.sub(r"[^a-zA-Z0-9._-]", "-", identifier).strip("-")
    return f"snapshot-{source}-{clean}"[:90].rstrip("-")


class GitHubPublisher:
    """Publishes one atomic commit using a GitHub App user access token."""

    def __init__(self, config: Settings = settings, client: httpx.Client | None = None):
        if not config.github_token:
            raise GitHubPublishError("GITHUB_USER_TOKEN is required (use a GitHub App user token)")
        self.config = config
        self.client = client or httpx.Client(
            base_url=config.github_api,
            headers={"Authorization": f"Bearer {config.github_token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"},
            timeout=60,
        )

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        response = self.client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise GitHubPublishError(
                f"GitHub {method} {path} returned {response.status_code}: {response.text[:300]}"
            )
        return response

    def ensure_private_repo(self, name: str, description: str) -> dict:
        response = self.client.get(f"/repos/{self.config.github_owner}/{name}")
        if response.status_code == 404:
            response = self._request("POST", "/user/repos", json={
                "name": name, "description": description[:350], "private": True,
                "auto_init": True, "has_issues": False, "has_projects": False,
                "has_wiki": False,
            })
        elif response.status_code >= 400:
            raise GitHubPublishError(f"cannot inspect repository: {response.text[:300]}")
        repo = response.json()
        if repo.get("owner", {}).get("login", "").lower() != self.config.github_owner.lower():
            raise GitHubPublishError("GitHub repository owner does not match configured owner")
        if not repo.get("private"):
            raise GitHubPublishError("refusing to publish evidence to a public repository")
        return repo

    def publish(self, *, name: str, description: str, files: dict[str, bytes],
                message: str) -> dict:
        repo = self.ensure_private_repo(name, description)
        owner = self.config.github_owner
        default_branch = repo.get("default_branch") or "main"
        ref = self._request("GET", f"/repos/{owner}/{name}/git/ref/heads/{default_branch}").json()
        parent_sha = ref["object"]["sha"]
        parent = self._request("GET", f"/repos/{owner}/{name}/git/commits/{parent_sha}").json()
        tree_items = []
        for path, data in sorted(files.items()):
            blob = self._request("POST", f"/repos/{owner}/{name}/git/blobs", json={
                "content": base64.b64encode(data).decode(), "encoding": "base64",
            }).json()
            tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = self._request("POST", f"/repos/{owner}/{name}/git/trees", json={
            "base_tree": parent["tree"]["sha"], "tree": tree_items,
        }).json()
        commit = self._request("POST", f"/repos/{owner}/{name}/git/commits", json={
            "message": message, "tree": tree["sha"], "parents": [parent_sha],
        }).json()
        self._request("PATCH", f"/repos/{owner}/{name}/git/refs/heads/{default_branch}",
                      json={"sha": commit["sha"], "force": False})
        return {"repo_id": str(repo["id"]), "repo_url": repo["html_url"],
                "commit_sha": commit["sha"], "branch": default_branch,
                "commit_url": f"{repo['html_url']}/commit/{commit['sha']}"}
