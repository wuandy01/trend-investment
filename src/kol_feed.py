"""X (Twitter) 財經 KOL 追蹤：讀取公開分享的 Google Sheet 名單，並在設定 API 金鑰後抓取最新貼文。

名單本身不需要任何金鑰即可讀取（只要 Google Sheet 分享設定為「知道連結的人皆可查看」）。
抓取即時貼文則需要使用者自行在 developer.x.com 申請 X API 並在 .streamlit/secrets.toml 設定
Bearer Token，避免把金鑰寫進程式碼或聊天內容裡。
"""

from __future__ import annotations

import re

import pandas as pd
import requests
import streamlit as st

KOL_SHEET_ID = "1GByalwK-RUT0p4_ydFsTydHeun6Wwo825C5XyILm3IM"
KOL_SHEET_GID = "0"
KOL_SHEET_CSV_URL = (
    f"https://docs.google.com/spreadsheets/d/{KOL_SHEET_ID}/export?format=csv&gid={KOL_SHEET_GID}"
)

X_API_BASE = "https://api.twitter.com/2"

_USERNAME_RE = re.compile(r"(?:x\.com|twitter\.com)/([A-Za-z0-9_]+)")


def _extract_username(url) -> str | None:
    if not isinstance(url, str):
        return None
    m = _USERNAME_RE.search(url)
    return m.group(1) if m else None


@st.cache_data(ttl=1800, show_spinner=False)
def load_kol_roster() -> pd.DataFrame:
    """讀取使用者提供、公開分享的 Google Sheet KOL 名單。"""
    df = pd.read_csv(KOL_SHEET_CSV_URL, thousands=",")
    df.columns = [str(c).strip().lower() for c in df.columns]
    df["username"] = df.get("url", pd.Series(dtype=str)).apply(_extract_username)
    if "follower" in df.columns:
        df["follower"] = pd.to_numeric(df["follower"], errors="coerce")
    return df


def get_bearer_token() -> str | None:
    """從 st.secrets 讀取 X API Bearer Token；未設定時回傳 None（不會拋錯）。"""
    try:
        return st.secrets["x_api"]["bearer_token"]
    except Exception:
        return None


@st.cache_data(ttl=900, show_spinner=False)
def fetch_recent_tweets(username: str, bearer_token: str, max_results: int = 5) -> dict:
    """抓取指定帳號最新貼文。回傳 {'tweets': [...]} 或 {'error': '說明文字'}。"""
    headers = {"Authorization": f"Bearer {bearer_token}"}

    try:
        user_resp = requests.get(
            f"{X_API_BASE}/users/by/username/{username}", headers=headers, timeout=10
        )
    except requests.RequestException as exc:
        return {"error": f"連線失敗：{exc}"}

    if user_resp.status_code == 401:
        return {"error": "X API 金鑰無效或已過期。"}
    if user_resp.status_code == 429:
        return {"error": "已達 X API 速率限制，請稍後再試。"}
    if user_resp.status_code != 200:
        return {"error": f"查無使用者 @{username}（HTTP {user_resp.status_code}）。"}

    user_id = (user_resp.json().get("data") or {}).get("id")
    if not user_id:
        return {"error": f"查無使用者 @{username}。"}

    try:
        tweets_resp = requests.get(
            f"{X_API_BASE}/users/{user_id}/tweets",
            headers=headers,
            params={
                "max_results": max_results,
                "tweet.fields": "created_at,public_metrics",
                "exclude": "retweets,replies",
            },
            timeout=10,
        )
    except requests.RequestException as exc:
        return {"error": f"連線失敗：{exc}"}

    if tweets_resp.status_code == 429:
        return {"error": "已達 X API 速率限制，請稍後再試。"}
    if tweets_resp.status_code != 200:
        return {"error": f"抓取貼文失敗（HTTP {tweets_resp.status_code}）。"}

    return {"tweets": tweets_resp.json().get("data") or []}
