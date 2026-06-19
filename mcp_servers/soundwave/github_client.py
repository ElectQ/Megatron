from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import httpx


class GitHubClient:
    """Client for reading Soundwave data from GitHub public repository."""

    def __init__(self, repo: str = "ElectQ/Soundwave", branch: str = "master"):
        self.repo = repo
        self.branch = branch
        self.base_url = f"https://api.github.com/repos/{repo}"
        self._cache: dict[str, Any] = {}
        self._cache_ttl = 300  # 5 minutes cache
        self._cache_time: dict[str, datetime] = {}

    async def _get(self, path: str) -> dict | list:
        """Make a GET request to GitHub API with caching."""
        cache_key = f"{self.base_url}/{path}"
        now = datetime.now()

        # Check cache
        if cache_key in self._cache:
            cached_time = self._cache_time.get(cache_key)
            if cached_time and (now - cached_time).total_seconds() < self._cache_ttl:
                return self._cache[cache_key]

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/{path}",
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            # Update cache
            self._cache[cache_key] = data
            self._cache_time[cache_key] = now

            return data

    async def list_data_directories(self) -> list[str]:
        """List all date directories in the data/ folder."""
        contents = await self._get("contents/data?ref=" + self.branch)
        if not isinstance(contents, list):
            return []

        directories = []
        for item in contents:
            if item.get("type") == "dir":
                # Validate date format (YYYY-MM-DD)
                name = item.get("name", "")
                try:
                    datetime.strptime(name, "%Y-%m-%d")
                    directories.append(name)
                except ValueError:
                    continue

        return sorted(directories, reverse=True)

    async def list_files_for_date(self, date: str) -> list[dict]:
        """List all JSON files for a specific date."""
        try:
            contents = await self._get(f"contents/data/{date}?ref={self.branch}")
            if not isinstance(contents, list):
                return []
            return [item for item in contents if item.get("name", "").endswith(".json")]
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return []
            raise

    async def get_file_content(self, path: str) -> dict:
        """Get the content of a specific file."""
        try:
            data = await self._get(f"contents/{path}?ref={self.branch}")
            if isinstance(data, dict) and data.get("content"):
                import base64

                content = base64.b64decode(data["content"]).decode("utf-8")
                return json.loads(content)
            elif isinstance(data, dict) and data.get("download_url"):
                # For large files, use download_url
                async with httpx.AsyncClient() as client:
                    response = await client.get(data["download_url"], timeout=30.0)
                    response.raise_for_status()
                    return response.json()
            return {}
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {}
            raise

    async def get_tweets_for_list(self, date: str, list_id: str) -> list[dict]:
        """Get tweets for a specific list on a specific date."""
        path = f"data/{date}/{list_id}.json"
        data = await self.get_file_content(path)
        return data.get("tweets", [])

    async def get_all_tweets_for_date(self, date: str) -> dict[str, list[dict]]:
        """Get all tweets for a specific date (all lists)."""
        files = await self.list_files_for_date(date)
        result = {}
        for file_info in files:
            file_name = file_info.get("name", "")
            if not file_name.endswith(".json"):
                continue
            list_id = file_name[:-5]  # Remove .json
            path = f"data/{date}/{file_name}"
            data = await self.get_file_content(path)
            result[list_id] = data.get("tweets", [])
        return result

    async def search_tweets(
        self,
        query: str,
        date_range: tuple[str, str] | None = None,
        max_results: int = 100,
    ) -> list[dict]:
        """Search tweets by keyword across date range."""
        directories = await self.list_data_directories()

        # Filter by date range
        if date_range:
            start_date, end_date = date_range
            directories = [
                d for d in directories if start_date <= d <= end_date
            ]

        results = []
        query_lower = query.lower()

        for date in directories:
            if len(results) >= max_results:
                break

            all_tweets = await self.get_all_tweets_for_date(date)
            for list_id, tweets in all_tweets.items():
                for tweet in tweets:
                    content = tweet.get("content", "").lower()
                    author = tweet.get("author_handle", "").lower()
                    hashtags = [h.lower() for h in tweet.get("hashtags", [])]

                    if (
                        query_lower in content
                        or query_lower in author
                        or any(query_lower in h for h in hashtags)
                    ):
                        results.append(tweet)
                        if len(results) >= max_results:
                            break

        return results[:max_results]

    async def get_stats(self, days: int = 30) -> dict[str, Any]:
        """Get statistics for the last N days."""
        directories = await self.list_data_directories()

        # Filter to last N days
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        recent_dirs = [d for d in directories if d >= cutoff_date]

        stats = []
        total_tweets = 0

        for date in recent_dirs:
            files = await self.list_files_for_date(date)
            day_total = 0
            lists = []

            for file_info in files:
                file_name = file_info.get("name", "")
                if not file_name.endswith(".json"):
                    continue
                list_id = file_name[:-5]
                path = f"data/{date}/{file_name}"
                data = await self.get_file_content(path)
                count = data.get("count", 0)
                day_total += count
                lists.append({
                    "list_id": list_id,
                    "list_name": data.get("list_name", ""),
                    "count": count,
                })

            total_tweets += day_total
            stats.append({
                "date": date,
                "total": day_total,
                "lists": lists,
            })

        return {
            "total_tweets": total_tweets,
            "days_tracked": len(recent_dirs),
            "latest_date": directories[0] if directories else None,
            "stats": stats,
        }


__all__ = ["GitHubClient"]
