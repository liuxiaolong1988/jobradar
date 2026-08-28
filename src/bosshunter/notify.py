# -*- coding: utf-8 -*-
"""飞书消息推送（两种出口，按配置自动选择）。

出口优先级（is_notify_enabled / send_notification 里按顺序判定，命中第一个就走）：
    1. 自建应用私信 (notify.lark_app) —— 推荐。以应用名义给指定 open_id 的用户发飞书私信，
       手机 App 个人通知流可见，不需要建群。
    2. 群机器人 webhook (notify.feishu_webhook_url) —— 旧兜底。发到群里。

配置示例（config.yaml notify 节）：
    notify:
      enable: true
      # 出口1：自建应用私信（推荐，新建时填写你的 app_id / app_secret / 接收人 open_id）
      lark_app:
        app_id: "cli_xxxxxxxx"
        app_secret: "xxxxxxxxxxxxxxxx"
        open_id: "ou_xxxxxxxx"
      # 出口2：群机器人 webhook（没有 lark_app 时回退用这个）
      feishu_webhook_url: ""

设计原则：推送失败绝不影响主流程（只 console 警告，不抛异常）。
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from rich.console import Console

console = Console()

_TIMEOUT_SECONDS = 10.0
_TOKEN_CACHE: dict[str, Any] = {"token": None, "expire_at": 0.0}


# --------- 公共 ---------

def _get_notify_config(config: dict) -> dict:
    cfg = config.get("notify") or {}
    return cfg if isinstance(cfg, dict) else {}


def _get_lark_app(config: dict) -> dict | None:
    cfg = _get_notify_config(config)
    app_cfg = cfg.get("lark_app")
    if not isinstance(app_cfg, dict):
        return None
    app_id = str(app_cfg.get("app_id") or "").strip()
    app_secret = str(app_cfg.get("app_secret") or "").strip()
    open_id = str(app_cfg.get("open_id") or "").strip()
    if not (app_id and app_secret and open_id):
        return None
    return {"app_id": app_id, "app_secret": app_secret, "open_id": open_id}


def _get_webhook(config: dict) -> str:
    cfg = _get_notify_config(config)
    return str(cfg.get("feishu_webhook_url") or "").strip()


def is_notify_enabled(config: dict) -> bool:
    """只要有一个出口能发就视为启用。"""
    cfg = _get_notify_config(config)
    if cfg.get("enable") is False:
        return False
    return bool(_get_lark_app(config) or _get_webhook(config))


def send_notification(config: dict, text: str, *, title: str | None = None) -> bool:
    """发送一条通知（优先走自建应用私信，否则走群 webhook）。成功返回 True。"""
    if not text:
        return False
    app = _get_lark_app(config)
    if app:
        ok = _send_lark_app_message(app, text, title=title)
        if ok:
            return ok
        # 应用失败时不回退 webhook（用途不同：一个私信一个群），记一次失败后返回
        return False
    webhook = _get_webhook(config)
    if webhook and _get_notify_config(config).get("enable") is not False:
        return _send_webhook_text(webhook, text)
    return False


# --------- 出口1：自建应用私信 ---------

def _get_tenant_access_token(app: dict) -> str | None:
    """获取 tenant_access_token，缓存到过期前 30s。"""
    now = time.monotonic()
    cached = _TOKEN_CACHE.get("token")
    expire_at = float(_TOKEN_CACHE.get("expire_at") or 0.0)
    cache_key = str(app.get("app_id") or "")
    if cached and _TOKEN_CACHE.get("app_id") == cache_key and now < expire_at:
        return cached

    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    try:
        resp = httpx.post(
            url,
            json={"app_id": app["app_id"], "app_secret": app["app_secret"]},
            timeout=_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        body = resp.json()
        code = body.get("code", -1)
        if code not in (0, "0"):
            console.print(f"[yellow]飞书应用获取 token 失败: {body}[/yellow]")
            return None
        token = str(body.get("tenant_access_token") or "")
        expire = int(body.get("expire") or 7200)
        _TOKEN_CACHE["token"] = token
        _TOKEN_CACHE["app_id"] = cache_key
        _TOKEN_CACHE["expire_at"] = now + max(expire - 60, 30)
        return token
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]飞书应用 token 请求失败（不影响主流程）: {exc}[/yellow]")
        return None


def _send_lark_app_message(app: dict, text: str, *, title: str | None = None) -> bool:
    token = _get_tenant_access_token(app)
    if not token:
        return False
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"
    # 使用 post 卡片型消息比纯 text 更易读
    content_json = _build_post_payload(title or "智能求职 · 通知", text)
    payload = {
        "receive_id": app["open_id"],
        "msg_type": "post",
        "content": content_json,
    }
    try:
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        body = resp.json()
        code = body.get("code", -1)
        if code not in (0, "0"):
            console.print(
                f"[yellow]飞书应用发私信失败: code={code} msg={body.get('msg')} "
                f"{body.get('message', '')}[/yellow]"
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]飞书应用发私信请求失败（不影响主流程）: {exc}[/yellow]")
        return False


def _build_post_payload(title: str, body_text: str) -> str:
    """把标题 + 正文拼成飞书 post 消息（富文本 zh_cn）的 JSON 字符串。"""
    import json

    # 按行切内容，每一行一个 text tag
    lines = body_text.splitlines() or [body_text]
    contents: list[list[dict]] = []
    for line in lines:
        if not line.strip():
            # 空行就放一个空格保持段间距
            contents.append([{"tag": "text", "text": " "}])
        else:
            contents.append([{"tag": "text", "text": line}])
    return json.dumps(
        {
            "zh_cn": {
                "title": title,
                "content": contents,
            }
        },
        ensure_ascii=False,
    )


# --------- 出口2：群机器人 webhook ---------

def _send_webhook_text(webhook: str, text: str) -> bool:
    payload = {"msg_type": "text", "content": {"text": text}}
    try:
        resp = httpx.post(webhook, json=payload, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        try:
            body = resp.json()
            code = body.get("code", body.get("StatusCode", 0))
            if code not in (0, "0"):
                console.print(f"[yellow]飞书群 webhook 返回异常状态: {body}[/yellow]")
                return False
        except ValueError:
            pass
        return True
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]飞书群 webhook 推送失败（不影响主流程）: {exc}[/yellow]")
        return False


# --------- 兼容旧调用：老代码调用 push_feishu_text 时仍工作 ---------

def push_feishu_text(config: dict, text: str) -> bool:
    """兼容旧 API：老 monitor/execute 调用这一个函数。"""
    return send_notification(config, text)


# --------- 监测结果格式化 ---------

def format_reply_notification(replies: list[dict], cold_contacts: list[dict]) -> str:
    """把一轮监测结果格式化成飞书消息正文。

    replies: 已跟踪岗位的 HR 新回复（job + conversation）
    cold_contacts: 陌生 HR 主动联系（未匹配到已投岗位的会话）
    """
    lines: list[str] = []

    if replies:
        lines.append(f"■ 已投岗位有新回复（{len(replies)} 条）：")
        for item in replies:
            job = item.get("job") or {}
            conv = item.get("conversation") or {}
            lines.append(
                f"· {job.get('company', '?')} - {job.get('title', '?')}\n"
                f"  {conv.get('hr_name', '?')}：{str(conv.get('last_message', ''))[:80]}"
            )

    if cold_contacts:
        if lines:
            lines.append("")
        lines.append(f"■ 陌生 HR 主动联系（{len(cold_contacts)} 条）：")
        for conv in cold_contacts:
            lines.append(
                f"· {conv.get('hr_name', '?')}（{conv.get('company', '未知公司')}）\n"
                f"  {str(conv.get('last_message', ''))[:80]}"
            )

    if not lines:
        return ""

    lines.append("")
    lines.append("（请在 BOSS 直聘消息页处理；电脑端可回工作台查看详情）")
    return "\n".join(lines)
