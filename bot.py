"""GoogleニュースRSSから広島カープのニュースを取得し、Blueskyに投稿するBot。

使い方:
    python bot.py            # 本番（投稿する）
    python bot.py --dry-run  # 投稿せずに内容だけ表示（state も更新しない）
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import feedparser
from atproto import Client, models

# ---- 設定 ----
SEARCH_QUERY = "広島カープ"
FEED_URL = (
    "https://news.google.com/rss/search?q="
    + quote(SEARCH_QUERY)
    + "&hl=ja&gl=JP&ceid=JP:ja"
)
MAX_POSTS_PER_RUN = 5
MAX_ARTICLE_AGE = timedelta(hours=24)
STATE_RETENTION = timedelta(days=7)
STATE_PATH = Path(__file__).parent / "posted.json"
POST_PREFIX = "【カープニュース】"
BSKY_TEXT_LIMIT = 300  # Blueskyの投稿上限（grapheme数）


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    source: str
    link: str
    published: datetime


@dataclass(frozen=True)
class State:
    initialized: bool
    posted: dict[str, str]  # article id -> 記録日時(ISO)
    posted_titles: dict[str, str]  # 正規化タイトル -> 記録日時(ISO)


# ---- state ----
def load_state(path: Path) -> State:
    if not path.exists():
        return State(initialized=False, posted={}, posted_titles={})
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"{path} のJSONが壊れています: {e}") from e
    return State(
        initialized=bool(raw.get("initialized", False)),
        posted=dict(raw.get("posted", {})),
        posted_titles=dict(raw.get("posted_titles", {})),
    )


def save_state(path: Path, state: State) -> None:
    data = {
        "initialized": state.initialized,
        "posted": dict(sorted(state.posted.items())),
        "posted_titles": dict(sorted(state.posted_titles.items())),
    }
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def prune(records: dict[str, str], now: datetime) -> dict[str, str]:
    cutoff = now - STATE_RETENTION
    return {k: v for k, v in records.items() if datetime.fromisoformat(v) >= cutoff}


def record(state: State, articles: list[Article], now: datetime) -> State:
    stamp = now.isoformat()
    return State(
        initialized=True,
        posted={**state.posted, **{a.id: stamp for a in articles}},
        posted_titles={
            **state.posted_titles,
            **{normalize_title(a.title): stamp for a in articles},
        },
    )


# ---- フィード ----
_TRAILING_PAREN = re.compile(r"[（(][^（）()]*[）)]$")


def normalize_title(title: str) -> str:
    """重複判定用。空白除去＋末尾の（媒体名）を繰り返し除去して比較する。"""
    t = unicodedata.normalize("NFKC", "".join(title.split())).lower()
    while (stripped := _TRAILING_PAREN.sub("", t)) != t and stripped:
        t = stripped
    return t


def split_title(raw_title: str, source: str) -> str:
    """Googleニュースのタイトル末尾「 - 媒体名」を取り除く。"""
    suffix = f" - {source}"
    return raw_title[: -len(suffix)] if source and raw_title.endswith(suffix) else raw_title


def parse_entry(entry: feedparser.FeedParserDict) -> Article | None:
    published_parsed = entry.get("published_parsed")
    link = entry.get("link")
    if not published_parsed or not link:
        return None
    source = entry.get("source", {}).get("title", "") or ""
    return Article(
        id=entry.get("id") or link,
        title=split_title(entry.get("title", ""), source).strip(),
        source=source.strip(),
        link=link,
        published=datetime.fromtimestamp(
            calendar.timegm(published_parsed), tz=timezone.utc
        ),
    )


def fetch_articles(url: str) -> list[Article]:
    feed = feedparser.parse(url)
    if feed.get("bozo") and not feed.entries:
        raise RuntimeError(f"RSSの取得に失敗しました: {feed.get('bozo_exception')}")
    parsed = (parse_entry(e) for e in feed.entries)
    return [a for a in parsed if a is not None and a.title]


def select_new(articles: list[Article], state: State, now: datetime) -> list[Article]:
    """未投稿・新しい記事を古い順に返す（同タイトルはまとめて1件に）。"""
    fresh = [
        a
        for a in articles
        if a.id not in state.posted
        and normalize_title(a.title) not in state.posted_titles
        and now - a.published <= MAX_ARTICLE_AGE
    ]
    unique: dict[str, Article] = {}
    for a in sorted(fresh, key=lambda a: a.published):
        unique.setdefault(normalize_title(a.title), a)
    return list(unique.values())


# ---- 投稿 ----
def build_text(article: Article) -> str:
    source = f"（{article.source}）" if article.source else ""
    text = f"{POST_PREFIX}{article.title}{source}"
    if len(text) <= BSKY_TEXT_LIMIT:
        return text
    room = BSKY_TEXT_LIMIT - len(POST_PREFIX) - len(source) - 1
    return f"{POST_PREFIX}{article.title[:room]}…{source}"


def build_embed(article: Article) -> models.AppBskyEmbedExternal.Main:
    return models.AppBskyEmbedExternal.Main(
        external=models.AppBskyEmbedExternal.External(
            uri=article.link,
            title=article.title,
            description=article.source or "Google ニュース",
        )
    )


def login() -> Client:
    handle = os.environ.get("BSKY_HANDLE")
    password = os.environ.get("BSKY_APP_PASSWORD")
    if not handle or not password:
        raise RuntimeError(
            "環境変数 BSKY_HANDLE / BSKY_APP_PASSWORD が設定されていません"
        )
    client = Client()
    client.login(handle, password)
    return client


def post(client: Client, article: Article) -> None:
    client.send_post(text=build_text(article), embed=build_embed(article), langs=["ja"])


# ---- main ----
def run(dry_run: bool) -> int:
    now = datetime.now(timezone.utc)
    state = load_state(STATE_PATH)
    state = State(
        initialized=state.initialized,
        posted=prune(state.posted, now),
        posted_titles=prune(state.posted_titles, now),
    )
    articles = fetch_articles(FEED_URL)
    print(f"取得: {len(articles)}件")

    if not state.initialized:
        print("初回実行: 既存記事を投稿済みとして登録します（投稿はしません）")
        if dry_run:
            print("[dry-run] state は保存しません")
            for a in select_new(articles, state, now)[:MAX_POSTS_PER_RUN]:
                print(f"  (参考) {build_text(a)}")
        else:
            save_state(STATE_PATH, record(state, articles, now))
        return 0

    targets = select_new(articles, state, now)[:MAX_POSTS_PER_RUN]
    print(f"新着: {len(targets)}件")
    if dry_run:
        for a in targets:
            print(f"[dry-run] {build_text(a)}\n          {a.link}")
        return 0

    if not targets:
        save_state(STATE_PATH, state)
        return 0

    client = login()
    posted: list[Article] = []
    try:
        for a in targets:
            post(client, a)
            posted.append(a)
            print(f"投稿: {build_text(a)}")
    finally:
        # 途中で失敗しても、投稿済みの分は記録して二重投稿を防ぐ
        save_state(STATE_PATH, record(state, posted, now))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="カープニュースBluesky Bot")
    parser.add_argument("--dry-run", action="store_true", help="投稿せずに表示のみ")
    args = parser.parse_args()
    try:
        return run(args.dry_run)
    except Exception as e:  # noqa: BLE001 - Actionsのログに理由を残して失敗させる
        print(f"エラー: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
