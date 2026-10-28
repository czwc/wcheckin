#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WorkBuddy「Buddy 加油站」每日自动签到（纯标准库，零依赖）。

用法:
  python checkin.py                 # 执行签到（本地直接读客户端登录态文件）
  python checkin.py --show-token    # 打印可填入 GitHub Secrets 的凭证（token 默认脱敏，加 --full 显示完整 refreshToken）
环境变量（GitHub Actions 用）:
  WB_ACCESS_TOKEN / WB_REFRESH_TOKEN / WB_DOMAIN  -- 提供了就不读本地文件
  WORKBUDDY_AUTH_FILE                             -- 指定登录态文件路径
  WECOM_WEBHOOK                                   -- 可选，企业微信群机器人 webhook，签到结果推送
"""
import os
import sys
import json
import ssl
import urllib.request
import urllib.error
from pathlib import Path

TIMEOUT = 30
DEFAULT_DOMAIN = "www.workbuddy.ai"

AUTH_FILE_CANDIDATES = [
    # Windows
    r"%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop-ai.info",
    r"%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info",
    r"%APPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop-ai.info",
    r"%APPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info",
    # macOS
    "~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop-ai.info",
    "~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info",
]

CTX = ssl.create_default_context()  # 强制 TLS 证书校验


def dig(obj, key, default=None):
    """在嵌套 dict 里按广度找 key（含 auth 子层），只返回标量值。"""
    if not isinstance(obj, dict):
        return default
    if key in obj and isinstance(obj[key], (str, int)):
        return obj[key]
    for v in obj.values():
        if isinstance(v, dict):
            found = dig(v, key, default)
            if found is not None:
                return found
    return default


def get_credentials():
    """返回 (access_token, refresh_token, domain)。优先环境变量，其次本地登录态文件。

    遍历所有候选文件：跳过新版加密信封格式（$wbEncrypted/envelope），
    取第一个含明文 token 的文件。
    """
    at = os.environ.get("WB_ACCESS_TOKEN") or ""
    rt = os.environ.get("WB_REFRESH_TOKEN") or ""
    dm = os.environ.get("WB_DOMAIN") or ""
    if rt:  # 只要有 refreshToken 即可（无 accessToken 时自动 refresh）
        return at, rt, dm
    if at:  # 只有 accessToken 也允许直接用
        return at, rt, dm
    env_file = os.environ.get("WORKBUDDY_AUTH_FILE")
    candidates = ([env_file] if env_file else []) + AUTH_FILE_CANDIDATES
    encrypted_seen = False
    for raw in candidates:
        p = Path(os.path.expandvars(raw)).expanduser()
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[warn] 登录态文件解析失败 {p}: {e}", file=sys.stderr)
            continue
        auth_data = data.get("auth") if isinstance(data.get("auth"), dict) else data
        f_at, f_rt = dig(data, "accessToken"), dig(data, "refreshToken")
        # domain 优先取 auth.domain，避免命中 account.sso.domain 的空值
        f_dm = dig(auth_data, "domain") or dig(data, "domain") or ""
        if isinstance(f_at, dict) or isinstance(f_rt, dict):
            encrypted_seen = True  # 加密信封，跳过
            continue
        if f_at and f_rt:
            return str(f_at), str(f_rt), str(f_dm)
    raise RuntimeError(
        "未能读取到明文登录态。"
        + ("新版客户端登录态为加密信封（AES-256-GCM），脚本无法解密，见 README 替代方案。"
           if encrypted_seen else
           "请确认桌面客户端已登录，或用环境变量提供 WB_ACCESS_TOKEN/WB_REFRESH_TOKEN。")
    )


def http_json(method, url, headers, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {}
    except Exception as e:
        raise RuntimeError(f"请求失败 {url}: {e}")


def base_url(domain):
    if not domain:
        print(f"[warn] 未提供 domain，使用默认 {DEFAULT_DOMAIN}", file=sys.stderr)
        domain = DEFAULT_DOMAIN
    return domain if domain.startswith("http") else f"https://{domain}"


def refresh_access_token(base, refresh_token, domain):
    """用 refreshToken 换新 accessToken。"""
    headers = {
        "X-Refresh-Token": refresh_token,
        "X-Auth-Refresh-Source": "plugin",
    }
    if domain:
        headers["X-Domain"] = domain
    code, data = http_json("POST", f"{base}/v2/plugin/auth/token/refresh", headers, {})
    if code != 200:
        raise RuntimeError(f"refresh 失败 HTTP {code}: {json.dumps(data, ensure_ascii=False)[:200]}")
    new_at = dig(data, "accessToken") or dig(data, "access_token")
    if not new_at:
        raise RuntimeError(f"refresh 响应中未找到 accessToken: {json.dumps(data, ensure_ascii=False)[:200]}")
    return str(new_at)


def wecom_notify(webhook, text):
    if not webhook:
        return
    try:
        http_json("POST", webhook, {}, {"msgtype": "markdown", "markdown": {"content": text}})
    except Exception as e:
        print(f"[warn] 企业微信推送失败: {e}", file=sys.stderr)


def do_checkin():
    at, rt, dm = get_credentials()
    base = base_url(dm)
    headers = {"Authorization": f"Bearer {at}"}

    # 1. 查签到状态
    code, status = http_json("POST", f"{base}/v2/billing/meter/checkin-activity-status", headers, {})
    if code == 401 and rt:
        # token 失效 → 刷新后重试一次
        print("[info] accessToken 失效，尝试 refresh...", file=sys.stderr)
        at = refresh_access_token(base, rt, dm)
        headers = {"Authorization": f"Bearer {at}"}
        code, status = http_json("POST", f"{base}/v2/billing/meter/checkin-activity-status", headers, {})
    if code != 200:
        raise RuntimeError(f"查询签到状态失败 HTTP {code}: {json.dumps(status, ensure_ascii=False)[:300]}")

    today_checked = bool(dig(status, "today_checked_in") or dig(status, "todayCheckedIn"))
    # 活动未开启（active=false）：可能是活动已结束或未开始，直接如实上报
    if dig(status, "active") is False and not today_checked:
        return {"status": "ok", "action": "activity_inactive", "points": "",
                "msg": "签到活动未开启（active=false），可能已结束或未开始，无需领取"}

    # 2. 未签则领取
    if today_checked:
        return {"status": "ok", "action": "skip_already_signed", "points": 0,
                "msg": "今日已签到，跳过"}

    code, result = http_json("POST", f"{base}/v2/billing/meter/daily-checkin", headers, {})
    if code != 200:
        biz = dig(result, "code")
        msg = dig(result, "msg") or dig(result, "message") or ""
        if biz == 10001:  # 接口侧幂等/活动状态拦截
            return {"status": "ok", "action": "skip_already_signed", "points": "",
                    "msg": msg or "今日已签到（接口幂等返回）"}
        raise RuntimeError(f"签到请求失败 HTTP {code}: {json.dumps(result, ensure_ascii=False)[:300]}")

    biz_code = dig(result, "code")
    points = dig(result, "points") or dig(result, "addPoints") or dig(result, "reward") or ""
    msg = dig(result, "msg") or dig(result, "message") or json.dumps(result, ensure_ascii=False)[:200]
    if biz_code not in (None, 0, 200, "0", "200", "success"):
        return {"status": "error", "action": "checkin_rejected", "points": "",
                "msg": f"code={biz_code} {msg}"}
    return {"status": "ok", "action": "checked_in", "points": points, "msg": msg}


def main():
    if "--show-token" in sys.argv:
        at, rt, dm = get_credentials()
        full = "--full" in sys.argv
        mask = lambda s: (s[:8] + "..." + s[-6:]) if len(s) > 20 else "***"
        print(json.dumps({
            "domain": dm,
            "refreshToken": rt if full else mask(rt),
            "accessToken": at if full else mask(at),
            "hint": "GitHub Secrets 填 WB_REFRESH_TOKEN / WB_DOMAIN 即可；"
                    "加 --full 显示完整 refreshToken（不要泄露给任何人）",
        }, ensure_ascii=False, indent=2))
        return 0

    result = {}
    try:
        result = do_checkin()
    except Exception as e:
        result = {"status": "error", "action": "exception", "points": "", "msg": str(e)}

    out = json.dumps(result, ensure_ascii=False)
    print(out)

    webhook = os.environ.get("WECOM_WEBHOOK")
    if webhook:
        icon = "✅" if result["status"] == "ok" else "❌"
        action_text = {"checked_in": "领取成功", "skip_already_signed": "今日已签到"}.get(
            result["action"], result["action"])
        lines = [f"**WorkBuddy 签到** {icon}"]
        lines.append(f"> 状态：{action_text}")
        if result.get("points") not in ("", 0, None):
            lines.append(f"> 积分：+{result['points']}")
        if result["status"] != "ok":
            lines.append(f"> 异常：{result.get('msg', '')[:200]}")
        wecom_notify(webhook, "\n".join(lines))

    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
