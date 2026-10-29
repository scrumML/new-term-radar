"""Video radar providers. TikHub endpoints are documented in docs/VIDEO_RADAR.md.

No cookies or browser login required. Paid requests are opt-in via CLI scan.
Unknown response shapes fail explicitly rather than becoming an empty successful scan.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from . import UA, aisa, competition, review, wordage


class SourceError(RuntimeError):
    pass


class TikHub:
    def __init__(self, limit: int = 30):
        self.limit = limit
        self.calls = 0

    def get(self, endpoint: str, **params) -> dict:
        key = os.environ.get("TIKHUB_API_KEY", "").strip()
        if not key:
            raise SourceError("缺少 TIKHUB_API_KEY；请在本机环境变量或 GitHub Actions Secrets 中配置")
        if self.calls >= self.limit:
            raise SourceError("TikHub 本次请求次数已达上限，剩余任务未执行")
        self.calls += 1
        url = "https://api.tikhub.io/api/v1/tiktok/app/v3/" + endpoint + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=40) as response:
                body = json.load(response)
        except urllib.error.HTTPError as exc:
            # Never echo request headers, tokens or upstream bodies into public reports.
            raise SourceError(f"TikHub HTTP {exc.code}") from exc
        except (OSError, ValueError) as exc:
            raise SourceError(f"TikHub 网络或 JSON 错误：{type(exc).__name__}") from exc
        if not isinstance(body, dict) or body.get("code") != 200 or not isinstance(body.get("data"), dict):
            raise SourceError("TikHub 返回失败或数据结构变化，需要检查适配器")
        payload = body["data"]
        if payload.get("status_code", 0) != 0:
            raise SourceError("TikTok 上游返回非成功状态")
        return payload

    def search(self, query: str, region: str, days: int) -> list[dict]:
        publish = 1 if days <= 1 else 7 if days <= 7 else 30 if days <= 30 else 90
        payload = self.get("fetch_video_search_result", keyword=query, offset=0, count=20,
                           sort_type=0, publish_time=publish, region=region)
        if "aweme_list" in payload:
            return payload["aweme_list"] or []
        if "data" in payload and isinstance(payload["data"], list):
            return [row.get("aweme_info", row) for row in payload["data"] if isinstance(row, dict)]
        raise SourceError("TikHub 搜索响应缺少 aweme_list/data，未将其视为零结果")

    def comments(self, video_id: str) -> list[dict]:
        payload = self.get("fetch_video_comments", aweme_id=video_id, cursor=0, count=20)
        if "comments" not in payload:
            raise SourceError("TikHub 评论响应缺少 comments")
        return [{"id": str(c.get("cid") or ""), "text": str(c.get("text") or ""),
                 "author": str((c.get("user") or {}).get("uid") or "")}
                for c in payload["comments"] or []]


def search_web(query: str, location: int, language: str) -> tuple[list[dict], int]:
    """Reuse upstream's SERP client; this is a discovery index, never view-count evidence."""
    page, cost = competition.page_one(query, location, language)
    return page["results"], cost


def trends(keyword: str, geo: str, today) -> tuple[dict, int]:
    from datetime import timedelta
    response, cost = aisa.call([{"call_id": "vtrend", "tool": review.TOOL, "arguments": {"body": [{
        "keywords": [keyword], "location_code": {"US": 2840, "GB": 2826, "DE": 2276}.get(geo, 2840),
        "date_from": (today - timedelta(days=30)).isoformat(), "date_to": today.isoformat()}]}}])
    tasks = (response.get("vtrend") or {}).get("tasks") or []
    if not tasks or tasks[0].get("status_code") != 20000:
        raise SourceError("Trends 请求失败或返回缺失")
    points = review.settled(wordage.series(tasks[0]), today)
    if len(points) < 8 or max((p[1] for p in points), default=0) == 0:
        return {"status": "no_data", "keyword": keyword, "geo": geo}, cost
    # Relative interest in this keyword's OWN series; never an absolute search volume.
    earlier = sum(p[1] for p in points[:-3]) / len(points[:-3])
    latest = sum(p[1] for p in points[-3:]) / 3
    ratio = round(latest / earlier, 2) if earlier else None
    return {"status": "rising" if latest >= max(earlier * 1.5, earlier + 10) else "flat_or_falling",
            "keyword": keyword, "geo": geo, "baseline": round(earlier, 1), "latest": round(latest, 1),
            "ratio": ratio, "points": [[p[0].isoformat(), p[1]] for p in points]}, cost
