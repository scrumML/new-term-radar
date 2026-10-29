"""Personal TikTok AI remake radar, built on new-term-radar's paid research clients.

python -m radar videos scan --config video-radar.json
python -m radar videos scan --offline --import evidence.json
python -m radar videos pick 'a trend name'
"""
from __future__ import annotations

import argparse
import csv
import html
import io
import json
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from . import aisa, video_sources

DEFAULTS = {
    "regions": ["US"], "window_days": 7, "new_window_hours": 72,
    "seed_terms": ["hotel lobby", "ai raindance", "ai zombie", "burning bridges", "bulin 47"],
    "discovery_queries": ["ai video trend", "ai dance trend", "ai photo animation trend",
                          "ai couple trend", "new ai effect", "capcut ai template"],
    "ignore_terms": ["fyp", "foryou", "foryoupage", "viral", "trending", "trend", "ai", "aivideo",
                     "aitrend", "aifilter", "aivideos", "capcut", "tiktok", "fy", "fypシ", "aiart",
                     "dance", "filter", "template", "love", "funny", "foryourpage", "viralvideo"],
    "aliases": {"hotellobby": "hotel lobby", "airaindance": "ai raindance", "aizombie": "ai zombie",
                "burningbridges": "burning bridges", "bulin47": "bulin 47"},
    "min_views": 100000, "min_authors": 3, "min_hit_authors": 2,
    "max_candidates": 8, "max_tikhub_requests": 32, "comment_videos": 6,
    "research": {"enabled": True, "max_candidates": 3, "location_code": 2840, "language": "en"},
}
DEMAND = re.compile(r"how (?:do|did|can|to)|what (?:app|tool|ai)|which (?:app|tool|ai)|"
                    r"(?:where|send|share).{0,20}(?:template|prompt)|tutorial|怎么做|求模板|什么软件|"
                    r"como (?:fazer|hacer)|qu[eé] app|welche app", re.I)
FIT = re.compile(r"\bai\b|#ai\w*|template|filter|effect|animate|animation|photo|upload|"
                 r"image.to.video|motion|换脸|模板|特效", re.I)
LABELS = {"priority_review": "优先看视频", "watch": "继续观察", "insufficient": "证据不足", "seed": "历史种子参照"}


def utcnow():
    return datetime.now(timezone.utc)


def iso(value):
    return value.isoformat(timespec="seconds")


def stamp(value):
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            return datetime.fromtimestamp(float(value), timezone.utc)
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def number(value):
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (ValueError, TypeError):
        return None


def term_key(text):
    return " ".join(unicodedata.normalize("NFKC", str(text)).lower().strip(" #\t\n").split())


def canonical_url(url):
    parts = urlsplit(str(url))
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    return urlunsplit(("https", parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def normalize(raw: dict, now=None) -> dict | None:
    """Normalize native TikHub items or the documented manual evidence format."""
    now = now or utcnow()
    author = raw.get("author") or {}
    if isinstance(author, dict):
        author = str(author.get("unique_id") or author.get("uniqueId") or author.get("uid") or "")
    vid = str(raw.get("aweme_id") or raw.get("id") or "")
    url = canonical_url(raw.get("url") or (f"https://www.tiktok.com/@{quote(str(author), safe='')}/video/{vid}"
                                          if vid.isdigit() and author else ""))
    match = re.search(r"/@[^/]+/video/(\d+)$", url)
    vid = match.group(1) if match else vid
    if not match or not vid or not url:
        return None
    host = urlsplit(url).hostname or ""
    if host != "tiktok.com" and not host.endswith(".tiktok.com"):
        return None
    metrics = raw.get("statistics") or raw.get("stats") or {}
    created = stamp(raw.get("published_at") or raw.get("create_time") or raw.get("createTime"))
    observed = stamp(raw.get("observed_at")) or now
    music = raw.get("music") or {}
    comments = raw.get("comments") if isinstance(raw.get("comments"), list) else []
    caption = str(raw.get("caption") or raw.get("desc") or "")[:3000]
    return {"id": vid, "url": url, "author": str(author), "caption": caption,
            "published_at": iso(created) if created else None, "observed_at": iso(observed),
            "views": number(raw.get("views", metrics.get("play_count", metrics.get("playCount")))),
            "likes": number(raw.get("likes", metrics.get("digg_count", metrics.get("diggCount")))),
            "shares": number(raw.get("shares", metrics.get("share_count", metrics.get("shareCount")))),
            "music_id": str(raw.get("music_id") or music.get("id") or ""),
            "terms": [term_key(t) for t in raw.get("terms", []) if isinstance(t, str)],
            "template_confirmed": raw.get("template_confirmed") is True,
            "ai_fit_confirmed": raw.get("ai_fit_confirmed") is True,
            "comments": [{"id": str(c.get("id") or c.get("cid") or ""), "text": str(c.get("text") or "")[:1000],
                          "author": str(c.get("author") or "")} if isinstance(c, dict) else
                         {"id": "", "text": str(c)[:1000], "author": ""} for c in comments],
            "source": str(raw.get("source") or "import"), "queries": list(raw.get("queries") or [])}


def extract_terms(video, cfg):
    ignored = {term_key(t).replace(" ", "") for t in cfg["ignore_terms"]}
    terms = list(video["terms"])
    for tag in re.findall(r"#([\w\u4e00-\u9fff]+)", video["caption"]):
        if term_key(tag).replace(" ", "") in ignored:
            continue
        split = re.sub(r"([a-z])([A-Z])", r"\1 \2", tag).replace("_", " ")
        terms.append(cfg["aliases"].get(term_key(tag), term_key(split)))
    for name in cfg["seed_terms"]:
        if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", video["caption"], re.I):
            terms.append(name)
    # A query is retrieval context, not proof the video belongs to that trend.
    return sorted({cfg["aliases"].get(term_key(t), term_key(t)) for t in terms
                   if 3 <= len(term_key(t)) <= 70 and term_key(t).replace(" ", "") not in ignored})


def connect(state: Path):
    state.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(state / "video-radar.db")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS video_samples (
            video_id TEXT, observed_at TEXT, views INTEGER, PRIMARY KEY(video_id, observed_at));
        CREATE TABLE IF NOT EXISTS video_candidates (
            term TEXT PRIMARY KEY, first_seen TEXT, last_seen TEXT, decision TEXT DEFAULT 'unreviewed', payload TEXT);
        CREATE TABLE IF NOT EXISTS video_runs (
            id INTEGER PRIMARY KEY, observed_at TEXT, status TEXT, payload TEXT);
    """)
    return conn


def velocity(conn, video):
    previous = conn.execute("SELECT observed_at,views FROM video_samples WHERE video_id=? AND observed_at<? "
                            "AND views IS NOT NULL ORDER BY observed_at DESC LIMIT 1",
                            (video["id"], video["observed_at"])).fetchone()
    if not previous or video["views"] is None:
        return None
    hours = (stamp(video["observed_at"]) - stamp(previous[0])).total_seconds() / 3600
    delta = video["views"] - previous[1]
    return round(delta / hours, 1) if hours >= 1 and delta >= 0 else None


def evaluate(term, videos, cfg, now, conn):
    recent = [v for v in videos if stamp(v["published_at"]) and
              timedelta(0) <= now - stamp(v["published_at"]) <= timedelta(days=cfg["window_days"]) and
              timedelta(0) <= now - stamp(v["observed_at"]) <= timedelta(hours=24)]
    authors = {v["author"].lower() for v in recent if v["author"]}
    hits = [v for v in recent if v["views"] is not None and v["views"] >= cfg["min_views"]]
    hit_authors = {v["author"].lower() for v in hits if v["author"]}
    demand = []
    seen_comments = set()
    for v in recent:
        for c in v["comments"]:
            key = c["id"] or (v["id"], c["text"].strip().lower())
            if DEMAND.search(c["text"]) and key not in seen_comments:
                seen_comments.add(key)
                demand.append({"text": c["text"], "url": v["url"], "id": c["id"]})
    confirmed = len({v["author"].lower() for v in hits if v["author"] and v["template_confirmed"]}) >= cfg["min_hit_authors"]
    fit = any(v["ai_fit_confirmed"] or FIT.search(v["caption"]) for v in recent)
    fresh = sum(1 for v in recent if now - stamp(v["published_at"]) <= timedelta(hours=cfg["new_window_hours"]))
    score_parts = {"multi_author": min(len(authors) * 5, 20), "hit_authors": min(len(hit_authors) * 10, 30),
                   "creation_demand": min(len(demand) * 5, 20), "recent_72h": min(fresh * 3, 15),
                   "ai_fit_clue": 5 if fit else 0, "same_effect_confirmed": 10 if confirmed else 0}
    enough = len(authors) >= cfg["min_authors"] and len(hit_authors) >= cfg["min_hit_authors"] and bool(demand) and fit
    status = "priority_review" if enough else "watch" if recent and fit else "insufficient"
    if term in {term_key(s) for s in cfg["seed_terms"]}:
        status = "seed"
    gaps = []
    if len(authors) < cfg["min_authors"]: gaps.append("近期独立作者不足")
    if len(hit_authors) < cfg["min_hit_authors"]: gaps.append("多个作者的高播放作品证据不足")
    if not demand: gaps.append("未采到明确的制作询问评论")
    if not confirmed: gaps.append("需观看作品确认同款效果，标签/音乐不能证明复刻")
    if not fit: gaps.append("需确认可用 AI 模板实现")
    gaps.extend(["需实测生成效果、成本与付费意愿", "新发现日期是本雷达首次记录时间，不是趋势诞生日期"])
    kept = conn.execute("SELECT first_seen,decision FROM video_candidates WHERE term=?", (term,)).fetchone()
    enriched = [{**v, "views_per_hour_since_previous_scan": velocity(conn, v)} for v in recent]
    return {"term": term, "status": status, "score": sum(score_parts.values()), "score_parts": score_parts,
            "first_seen": kept[0] if kept else iso(now), "decision": kept[1] if kept else "unreviewed",
            "recent_authors": len(authors), "hit_authors": len(hit_authors), "recent_videos": len(recent),
            "new_72h": fresh, "same_effect_confirmed": confirmed, "demand": demand, "videos": enriched,
            "excluded_or_undated_videos": len(videos) - len(recent), "gaps": gaps,
            "keywords": [f"{term} ai generator", f"{term} video generator", f"{term} template", f"how to make {term} video"],
            "links": {"tiktok": "https://www.tiktok.com/search?" + urlencode({"q": term}),
                      "trends": "https://trends.google.com/trends/explore?" + urlencode({"date": "now 7-d", "geo": cfg["regions"][0], "q": term}),
                      "google": "https://www.google.com/search?" + urlencode({"q": f"{term} ai generator"})}}


def merge_video(videos, v):
    old = videos.get(v["id"])
    if not old:
        videos[v["id"]] = v
        return
    newer, older = (v, old) if stamp(v["observed_at"]) >= stamp(old["observed_at"]) else (old, v)
    merged = {**newer, "terms": sorted(set(old["terms"] + v["terms"])),
              "queries": sorted(set(old["queries"] + v["queries"])),
              "template_confirmed": old["template_confirmed"] or v["template_confirmed"],
              "ai_fit_confirmed": old["ai_fit_confirmed"] or v["ai_fit_confirmed"]}
    seen = set()
    merged["comments"] = [c for c in newer["comments"] + older["comments"]
                          if not ((c["id"] or c["text"]) in seen or seen.add(c["id"] or c["text"]))]
    videos[v["id"]] = merged


def scan(cfg, state, imported=None, offline=False, now=None):
    now = now or utcnow()
    conn = connect(state)
    videos, errors, sources = {}, [], []
    cost = 0
    client = video_sources.TikHub(cfg["max_tikhub_requests"])
    for row in imported or []:
        if not isinstance(row, dict):
            errors.append("导入记录不是对象，已跳过")
            continue
        v = normalize(row, now)
        if v: merge_video(videos, v)
        else: errors.append("导入记录缺少有效的 TikTok 视频 ID/URL，已跳过")
    if imported is not None: sources.append("本地证据导入")

    def fetch(query):
        for region in cfg["regions"]:
            reserved = min(cfg["comment_videos"], cfg["max_tikhub_requests"] // 3)
            if client.calls >= cfg["max_tikhub_requests"] - reserved:
                note = "部分搜索因请求上限跳过，已为制作询问评论保留请求额度"
                if note not in errors: errors.append(note)
                return
            try:
                rows = client.search(query, region, cfg["window_days"])
                for raw in rows:
                    v = normalize({**raw, "source": "tikhub", "queries": [query], "observed_at": iso(now)}, now)
                    if v: merge_video(videos, v)
            except video_sources.SourceError as exc:
                errors.append(f"TikTok {region} / {query}: {exc}")
                break

    if not offline:
        # Fail before many duplicate missing-key errors; optional AIsa research can still run.
        import os
        if os.environ.get("TIKHUB_API_KEY", "").strip():
            sources.append("TikHub TikTok 搜索/评论（第三方数据）")
            watchlist = [r[0] for r in conn.execute(
                "SELECT term FROM video_candidates WHERE decision != 'rejected' AND last_seen >= ? "
                "ORDER BY decision='picked' DESC,last_seen DESC LIMIT ?",
                (iso(now-timedelta(days=7)), cfg["max_candidates"])).fetchall()]
            for query in list(dict.fromkeys(cfg["discovery_queries"] + watchlist + cfg["seed_terms"])):
                fetch(query)
            groups = group(videos.values(), cfg)
            discovered = sorted(groups, key=lambda k: -len(groups[k]))
            for name in [k for k in discovered if k not in {term_key(s) for s in cfg["seed_terms"]}][:cfg["max_candidates"]]:
                fetch(name)
            nonseeds = {k: v for k, v in group(videos.values(), cfg).items()
                        if k not in {term_key(s) for s in cfg["seed_terms"]}}
            # Round-robin across candidate groups so one old seed cannot consume every comment request.
            ranked = [sorted(items, key=lambda v: -(v["views"] or 0)) for items in nonseeds.values()]
            comment_targets, seen_ids = [], set()
            for index in range(max((len(items) for items in ranked), default=0)):
                for items in ranked:
                    if index < len(items) and items[index]["id"] not in seen_ids:
                        comment_targets.append(items[index])
                        seen_ids.add(items[index]["id"])
            for v in comment_targets[:cfg["comment_videos"]]:
                try: v["comments"] = client.comments(v["id"])
                except video_sources.SourceError as exc: errors.append(f"评论 {v['id']}: {exc}")
        else:
            errors.append("TikTok 采集未运行：缺少 TIKHUB_API_KEY；可用 --import 导入真实证据")
    candidates = [evaluate(k, items, cfg, now, conn) for k, items in group(videos.values(), cfg).items()]
    candidates.sort(key=lambda r: (r["status"] == "seed", -r["score"], r["term"]))
    research = cfg["research"]
    if not offline and research["enabled"]:
        # Explicit environment key only: no accidental use of a CLI's saved account.
        import os
        if not os.environ.get("AISA_API_KEY", "").strip():
            errors.append("搜索/Trends 核验未运行：缺少 AISA_API_KEY；报告提供手动核验链接")
        else:
            sources.append("AIsa / DataForSEO 搜索与 Trends")
            for r in [r for r in candidates if r["status"] != "seed"][:research["max_candidates"]]:
                try:
                    results, spent = video_sources.search_web(r["keywords"][0], research["location_code"], research["language"])
                    cost += spent
                    r["competition"] = {"results": results, "tool_pages": sum(p["kind"] == "tool" for p in results),
                                        "status": "observed" if results else "no_data",
                                        "note": "仅当前关键词的第一页样本；无 DR/域龄，不据此宣布低竞争"}
                    r["trends"], spent = video_sources.trends(r["keywords"][0], cfg["regions"][0], now.date())
                    cost += spent
                except (aisa.AisaError, video_sources.SourceError, OSError, ValueError) as exc:
                    errors.append(f"核验 {r['term']}: {exc}")
    # Save only after scoring, so a scan never compares its snapshot with itself.
    with conn:
        for v in videos.values():
            conn.execute("INSERT OR IGNORE INTO video_samples VALUES (?,?,?)", (v["id"], v["observed_at"], v["views"]))
        for r in candidates:
            conn.execute("INSERT INTO video_candidates(term,first_seen,last_seen,payload) VALUES (?,?,?,?) "
                         "ON CONFLICT(term) DO UPDATE SET last_seen=excluded.last_seen,payload=excluded.payload",
                         (r["term"], r["first_seen"], iso(now), json.dumps(r, ensure_ascii=False)))
        active = [r for r in candidates if r["status"] != "seed"]
        out = {"generated_at": iso(now), "display_time": now.astimezone(ZoneInfo("Europe/Berlin")).isoformat(timespec="seconds"),
               "mode": "offline_import" if offline else "live", "status": "partial" if errors and videos else "blocked" if errors else "ok",
               "sources": sources, "errors": errors, "video_count": len(videos), "candidates": active[:cfg["max_candidates"]],
               "seed_reference": [r for r in candidates if r["status"] == "seed"],
               "tikhub_requests": client.calls, "aisa_reported_cost_micros": cost,
               "cost_note": "TikHub 按请求计费，本程序仅限制次数，不估算账单；AIsa 异常请求费用可能未返回",
               "coverage_note": "搜索样本非 TikTok 全量；未采到不等于没有需求。评分是核查优先级，不是爆火或盈利概率。"}
        conn.execute("INSERT INTO video_runs(observed_at,status,payload) VALUES (?,?,?)", (iso(now), out["status"], json.dumps(out, ensure_ascii=False)))
    conn.close()
    return out


def group(videos, cfg):
    groups = {}
    for v in videos:
        for name in extract_terms(v, cfg): groups.setdefault(name, []).append(v)
    return groups


def markdown(out):
    safe = lambda s: str(s).replace("|", "／").replace("\n", " ")
    lines = ["# AI 视频趋势雷达", "", f"扫描时间：{out['display_time']} · 状态：{out['status']} · 模式：{out['mode']}", "",
             out["coverage_note"], "", "## 数据状态", "", f"采集视频：{out['video_count']}；TikHub 请求：{out['tikhub_requests']}；AIsa 已返回费用：${out['aisa_reported_cost_micros']/1e6:.4f}",
             out["cost_note"], ""]
    lines += [f"- {safe(e)}" for e in out["errors"]]
    lines += ["", "## 候选关键词", "", "| 词 | 核查分 | 状态 | 近期作者 | 高播放作者 | 制作询问 |", "|---|---:|---|---:|---:|---:|"]
    for r in out["candidates"]:
        lines.append(f"| {safe(r['term'])} | {r['score']} | {LABELS[r['status']]} | {r['recent_authors']} | {r['hit_authors']} | {len(r['demand'])} |")
    if not out["candidates"]: lines += ["", "本次没有足够数据形成候选；这不代表没有新趋势。"]
    for r in out["candidates"]:
        lines += ["", f"## {safe(r['term'])}", "", f"首次记录：{r['first_seen']}；人工决策：{r['decision']}。", "",
                  "建议核验的搜索词：" + " / ".join(r["keywords"]), "",
                  " · ".join(f"[{k}]({u})" for k, u in r["links"].items()), "",
                  "评分明细：" + safe(json.dumps(r["score_parts"], ensure_ascii=False)), "", "视频证据：", ""]
        for v in r["videos"][:8]:
            lines += [f"- [@{safe(v['author'])}]({v['url']}) · 发布 {v['published_at']} · 采样 {v['observed_at']} · 播放 {v['views'] if v['views'] is not None else '未知'} · 增速 {v['views_per_hour_since_previous_scan'] if v['views_per_hour_since_previous_scan'] is not None else '待下次扫描'} 播放/小时"]
        if r["demand"]:
            lines += ["", f"制作询问：{len(r['demand'])} 条匹配（打开原视频核实上下文）。"]
            # Short excerpts per video, below web quotation limits; full text remains private JSON evidence.
            quoted = set()
            for d in r["demand"]:
                if d["url"] not in quoted:
                    quoted.add(d["url"])
                    excerpt = " ".join(d["text"].split()[:10])[:60]
                    lines.append(f"- [{safe(excerpt)}]({d['url']})")
        if r.get("trends"): lines += ["", "工具词 Trends（相对热度）：" + safe(r["trends"]["status"])]
        if r.get("competition"):
            lines += ["", f"搜索第一页工具页面：{r['competition']['tool_pages']}；{r['competition']['note']}"]
            lines += [f"- [{safe(p['title'])}]({p['url']})" for p in r["competition"]["results"] if canonical_url(p["url"])]
        lines += ["", "仍需核实：", ""] + [f"- {s}" for s in r["gaps"]]
    lines += ["", "历史种子仅作参照：" + ", ".join(r["term"] for r in out["seed_reference"]), ""]
    return "\n".join(lines)


def write_report(out, directory):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "report.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "report.md").write_text(markdown(out), encoding="utf-8")
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["keyword", "review_score", "status", "authors", "hit_authors", "demand_comments", "decision", "generator_keyword", "tiktok", "trends"])
    for r in out["candidates"]:
        values = [r["term"], r["score"], LABELS[r["status"]], r["recent_authors"], r["hit_authors"], len(r["demand"]), r["decision"], r["keywords"][0], r["links"]["tiktok"], r["links"]["trends"]]
        writer.writerow(["'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in values])
    (directory / "keywords.csv").write_text("\ufeff" + stream.getvalue(), encoding="utf-8")
    body = html.escape(markdown(out))
    # Standalone local reader, no third-party scripts or requests, report text never executed as HTML.
    page = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 视频趋势雷达</title><style>body{margin:0;background:#10151e;color:#e9edf5;font:16px/1.7 system-ui}main{max-width:1100px;margin:auto;padding:36px 24px}h1{font-size:36px;margin-bottom:0}p{color:#aebbd0}input{padding:12px;width:90%;background:#1c2636;color:white;border:1px solid #526078;border-radius:8px}article{padding:22px;background:#1a2332;margin:18px 0;border-radius:12px}a{color:#74dccc;margin-right:15px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.7 system-ui}.pill{float:right;color:#74dccc}button{cursor:pointer;padding:8px;background:#74dccc;border:0;border-radius:5px}</style>
<main><h1>AI 视频趋势雷达</h1><p>先看复刻作品与制作需求，再挑选建站词。分数仅决定核查顺序。</p><p id="summary"></p><input id="filter" placeholder="搜索关键词 / 只看 picked"><div id="cards"></div><details><summary>完整证据与采集状态</summary><pre>""" + body + """</pre></details><p><a href="keywords.csv">下载关键词 CSV</a><a href="report.md">Markdown 报告</a><a href="report.json">原始证据 JSON</a></p></main><script>
const data=JSON.parse(document.getElementById('data').textContent);
function draw(){const q=document.getElementById('filter').value.toLowerCase(), root=document.getElementById('cards');root.replaceChildren();for(const r of data.candidates.filter(r=>(r.term+' '+r.decision).includes(q))){const a=document.createElement('article'),h=document.createElement('h2');h.textContent=r.term+' · '+r.score+' 分';a.append(h);const p=document.createElement('p');p.textContent=r.recent_authors+' 个近期作者 / '+r.hit_authors+' 个高播放作者 / '+r.demand.length+' 条制作询问 · '+r.decision;a.append(p);for(const [label,url] of Object.entries(r.links)){const link=document.createElement('a');link.textContent=label;link.href=url;link.target='_blank';link.rel='noopener noreferrer';a.append(link)}const b=document.createElement('p');b.textContent='工具词：'+r.keywords.join(' / ');a.append(b);for(const v of r.videos.slice(0,6)){const p=document.createElement('p'),link=document.createElement('a');link.textContent='@'+v.author+' · '+(v.views===null?'播放未知':v.views+' 播放');link.href=v.url;link.target='_blank';link.rel='noopener noreferrer';p.append(link);p.append(document.createTextNode('发布 '+v.published_at+' · 增速 '+(v.views_per_hour_since_previous_scan===null?'待复扫':v.views_per_hour_since_previous_scan+'/小时')));a.append(p)}const g=document.createElement('p');g.textContent=r.gaps.join('；');a.append(g);root.append(a)}}
document.getElementById('summary').textContent=data.display_time+' · '+data.status+' · '+data.video_count+' 个视频';document.getElementById('filter').oninput=draw;draw();</script></html>"""
    encoded = json.dumps(out, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    page = page.replace("<script>\nconst data", '<script type="application/json" id="data">' + encoded + '</script><script>\nconst data')
    (directory / "index.html").write_text(page, encoding="utf-8")


def load_config(path):
    cfg = json.loads(json.dumps(DEFAULTS))
    if path:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        cfg.update({k: v for k, v in raw.items() if k != "research"})
        cfg["research"].update(raw.get("research", {}))
    for k in ("window_days", "new_window_hours", "min_views", "min_authors", "min_hit_authors", "max_candidates", "max_tikhub_requests"):
        if not isinstance(cfg[k], int) or isinstance(cfg[k], bool) or cfg[k] <= 0:
            raise ValueError(f"配置 {k} 必须是正整数")
    if cfg["window_days"] > 90: raise ValueError("window_days 不能超过 90")
    if cfg["comment_videos"] < 0 or cfg["research"]["max_candidates"] < 0: raise ValueError("请求数量不能为负")
    if not cfg["regions"] or any(r not in ("US", "GB", "DE") for r in cfg["regions"]):
        raise ValueError("regions 支持 US、GB、DE；Trends 与 TikTok 使用相同国家")
    return cfg


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["scan", "pick", "reject", "list", "demo"])
    parser.add_argument("term", nargs="?")
    parser.add_argument("--config")
    parser.add_argument("--state", default="state/videos")
    parser.add_argument("--out", default="reports/videos/latest")
    parser.add_argument("--import", dest="import_path", help="Normalized JSON evidence: a list or {videos: [...]}.")
    parser.add_argument("--offline", action="store_true", help="No external calls; evaluate imported evidence only.")
    args = parser.parse_args(argv)
    try:
        state = Path(args.state)
        if args.action in ("pick", "reject", "list"):
            conn = connect(state)
            if args.action == "list":
                rows = conn.execute("SELECT term,decision,first_seen,last_seen FROM video_candidates ORDER BY last_seen DESC").fetchall()
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            else:
                if not args.term: parser.error("pick/reject 需要关键词")
                with conn:
                    cur = conn.execute("UPDATE video_candidates SET decision=? WHERE term=?", ("picked" if args.action == "pick" else "rejected", term_key(args.term)))
                if not cur.rowcount: raise ValueError("没有这个候选词；先扫描，再从报告中复制准确名称")
                print(f"已记录 {args.term}：{args.action}；下次报告会保留这个选择。")
            conn.close()
            return 0
        cfg = load_config(args.config)
        imported = None
        if args.import_path:
            imported = json.loads(Path(args.import_path).read_text(encoding="utf-8"))
            if isinstance(imported, dict): imported = imported.get("videos")
            if not isinstance(imported, list): raise ValueError("导入 JSON 必须是视频列表或 {videos: [...]} 对象")
        if args.action == "demo":
            now = utcnow()
            imported = [{"id": str(9000000000000000000+i), "url": f"https://www.tiktok.com/@demo{i}/video/{9000000000000000000+i}",
                         "author": f"demo{i}", "caption": "SYNTHETIC EXAMPLE #DemoMoonDance AI template", "terms": ["demo moon dance"],
                         "views": 120000+i*50000, "published_at": iso(now-timedelta(hours=12+i)), "source": "SYNTHETIC_DEMO",
                         "comments": ["What app is this?"], "template_confirmed": False} for i in range(3)]
            # Demo state can never mix with real discovery history.
            state = state / "demo"
            args.offline = True
        if args.offline and imported is None: parser.error("--offline 必须结合 --import；或使用 demo")
        out = scan(cfg, state, imported, args.offline)
        if args.action == "demo":
            out["mode"] = "SYNTHETIC_DEMO"
            out["coverage_note"] = "演示数据，全部虚构，不能用于选词或判断市场。" + out["coverage_note"]
        write_report(out, Path(args.out))
        print(f"视频雷达：{out['status']} · {out['video_count']} 视频 · {len(out['candidates'])} 候选\n报告：{Path(args.out).resolve() / 'index.html'}")
        for error in out["errors"]: print(error, file=sys.stderr)
        return 2 if out["status"] == "blocked" else 0
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        print(f"视频雷达失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
