#!/usr/bin/env python3
"""知答 ZhiDa 轻量告警检查器（cron 定时运行，零外部依赖）。

工作方式：拉取 /ready 与 /metrics，与上一次快照做差，算出窗口内的
错误率 / 延迟 / 组件状态，超过阈值就推送告警，恢复正常时推送恢复通知。

为什么要「快照做差」：Prometheus 的 counter 是进程启动以来的累计值，
单独一个点没有意义；必须两次采样求增量，才能得到「最近 5 分钟」的比率。
直方图同理，用相邻两次的桶计数差估算 P95。

设计要点：
- 冷却去重：同一告警默认 30 分钟内只推一次，避免每次轮询轰炸
- 恢复通知：故障消失时主动推一条「已恢复」，否则值班人员不知道何时好的
- 基线保护：首次运行 / 间隔过长（机器休眠、cron 停过）只建立基线不判定，
  避免拿跨越几小时的增量当「最近 5 分钟」而误报
- 健康检查 /ready 由 Docker 每 15s 调用，它和 /metrics 自身不计入业务流量

用法：
    python3 scripts/alert.py \
        --target http://localhost:8080 \
        --webhook-type wecom \
        --webhook-url 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx'

    # 只看结果不推送
    python3 scripts/alert.py --target http://localhost:8080 --dry-run

cron 示例（每 5 分钟，与 ALERT_WINDOW_SECONDS=300 对齐）：
    */5 * * * * cd /opt/zhida && .venv/bin/python scripts/alert.py >> logs/alert.log 2>&1
"""
import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# .env 里的 ALERT_* 配置对 cron 生效（cron 不加载 shell 配置）
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except Exception:
    pass


# /ready 是 Docker healthcheck 每 15s 的探针，/metrics 是抓取自身；
# 它们不是业务流量，计入会稀释错误率、污染 P95。
IGNORED_ROUTES = {"/health", "/ready", "/metrics"}
# 上传含 OCR，耗时天然波动大，不按延迟告警
NO_LATENCY_ALERT = {"/upload-doc"}

_LABEL_RE = re.compile(r'(\w+)="([^"]*)"')


@dataclass
class Alert:
    key: str
    level: str          # CRITICAL / WARNING / RECOVERY
    title: str
    detail: str = ""


def log(msg: str) -> None:
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {msg}", flush=True)


def env(name: str, default: str) -> str:
    return os.getenv(name, default)


def fetch(url: str, timeout: float) -> tuple[int | None, str]:
    """GET 请求，返回 (状态码, body)；网络错误返回 (None, 错误描述)。"""
    req = urllib.request.Request(url, headers={"Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _parse_sample(line: str):
    """解析一行 Prometheus 文本样本：name{labels} value [timestamp]。"""
    if not line or line.startswith("#"):
        return None
    name_labels, _, rest = line.partition(" ")
    if "{" in name_labels:
        name, _, label_part = name_labels.partition("{")
        labels = dict(_LABEL_RE.findall(label_part.rstrip("}")))
    else:
        name, labels = name_labels, {}
    try:
        value = float(rest.split()[0]) if rest else 0.0
    except (ValueError, IndexError):
        return None
    return name, labels, value


def collect_metrics(text: str):
    """抽取告警需要的三类序列：HTTP 计数、LLM 计数、HTTP 延迟直方图桶。"""
    http = {}      # "method|route|status" -> count
    llm = {}       # "operation|status" -> count
    buckets = {}   # "route|le" -> 累计计数
    for line in text.splitlines():
        parsed = _parse_sample(line)
        if not parsed:
            continue
        name, labels, value = parsed
        if name == "zhida_http_requests_total":
            key = f"{labels.get('method', '')}|{labels.get('route', '')}|{labels.get('status', '')}"
            http[key] = value
        elif name == "zhida_llm_requests_total":
            key = f"{labels.get('operation', '')}|{labels.get('status', '')}"
            llm[key] = value
        elif name == "zhida_http_request_duration_seconds_bucket":
            key = f"{labels.get('route', '')}|{labels.get('le', '')}"
            buckets[key] = value
    return http, llm, buckets


def counter_delta(current: dict, previous: dict) -> dict:
    """累计计数求增量。进程重启会让计数归零，此时直接用当前值（重启后的全部）。"""
    delta = {}
    for key, value in current.items():
        prev = previous.get(key)
        delta[key] = value if prev is None or value < prev else value - prev
    return delta


def _le_value(le: str) -> float:
    return math.inf if le == "+Inf" else float(le)


def compute_p95(bucket_delta: dict) -> dict:
    """用直方图桶增量估算各路由 P95 延迟（桶内线性插值）。

    桶只告诉「落在 (prev_le, le] 区间」，P95 落在某个桶里时按累计占比插值，
    这是 Prometheus histogram_quantile 的同样思路，精度取决于桶的密度。
    """
    by_route: dict[str, dict[str, float]] = {}
    for key, value in bucket_delta.items():
        route, _, le = key.partition("|")
        by_route.setdefault(route, {})[le] = value

    p95 = {}
    for route, buckets in by_route.items():
        total = buckets.get("+Inf", 0.0)
        if total <= 0:
            continue
        target = 0.95 * total
        prev_le, prev_cum = 0.0, 0.0
        for le, cum in sorted(buckets.items(), key=lambda kv: _le_value(kv[0])):
            if cum >= target:
                upper = _le_value(le)
                if math.isinf(upper):
                    # 落在 +Inf 桶：只能给出下界，用于判断「至少这么慢」
                    p95[route] = prev_le
                else:
                    frac = (target - prev_cum) / (cum - prev_cum) if cum > prev_cum else 1.0
                    p95[route] = prev_le + (upper - prev_le) * frac
                break
            prev_le, prev_cum = _le_value(le), cum
    return p95


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)     # 原子替换，避免写一半被下次读取


def check_ready(target: str, timeout: float, alerts: list[Alert]) -> None:
    status, body = fetch(f"{target}/ready", timeout)
    if status is None:
        alerts.append(Alert("ready", "CRITICAL", "服务不可达",
                            f"/ready 请求失败：{body}"))
        return
    if status == 200:
        return
    try:
        checks = json.loads(body).get("checks", {})
    except Exception:
        checks = {}
    detail = "，".join(f"{k}={v}" for k, v in checks.items()) or body[:200]
    alerts.append(Alert("ready", "CRITICAL", f"服务未就绪（/ready {status}）", detail))


def check_rates(delta_http: dict, delta_llm: dict, delta_buckets: dict, args, alerts: list[Alert]) -> dict:
    """按阈值生成告警，返回本次的统计摘要（供日志输出）。"""
    summary = {}

    # ---- HTTP 5xx / 429 ----
    business = {k: v for k, v in delta_http.items() if k.split("|")[1] not in IGNORED_ROUTES}
    total = sum(business.values())
    s5xx = sum(v for k, v in business.items() if k.split("|")[2].startswith("5"))
    s429 = sum(v for k, v in business.items() if k.split("|")[2] == "429")
    summary["http_requests"] = int(total)
    summary["http_5xx"] = int(s5xx)
    summary["http_429"] = int(s429)
    if total >= args.min_requests:
        rate_5xx = s5xx / total
        if rate_5xx > args.error_rate:
            alerts.append(Alert(
                "http_5xx", "CRITICAL", f"5xx 错误率 {rate_5xx:.1%} 超阈值 {args.error_rate:.0%}",
                f"窗口内 {int(s5xx)}/{int(total)} 次请求失败",
            ))
        rate_429 = s429 / total
        if rate_429 > args.rate_limit_ratio:
            alerts.append(Alert(
                "http_429", "WARNING", f"限流比例 {rate_429:.1%} 超阈值 {args.rate_limit_ratio:.0%}",
                f"窗口内 {int(s429)}/{int(total)} 次被限流，可能存在滥用或阈值配置过严",
            ))

    # ---- LLM 调用错误率 ----
    ok = sum(v for k, v in delta_llm.items() if k.endswith("|ok"))
    err = sum(v for k, v in delta_llm.items() if k.endswith("|error"))
    summary["llm_calls"] = int(ok + err)
    summary["llm_errors"] = int(err)
    if ok + err >= args.min_requests:
        rate = err / (ok + err)
        if rate > args.llm_error_rate:
            alerts.append(Alert(
                "llm_error", "CRITICAL", f"LLM 调用错误率 {rate:.1%} 超阈值 {args.llm_error_rate:.0%}",
                f"窗口内 {int(err)}/{int(ok + err)} 次调用失败（检查网关/密钥/配额）",
            ))

    # ---- 延迟 P95 ----
    p95 = compute_p95(delta_buckets)
    for route, value in sorted(p95.items()):
        if route in IGNORED_ROUTES or route in NO_LATENCY_ALERT:
            continue
        threshold = args.stream_p95_seconds if route == "/stream-query" else args.p95_seconds
        summary[f"p95{route}"] = round(value, 2)
        if value > threshold:
            alerts.append(Alert(
                f"latency:{route}", "WARNING",
                f"{route} P95 延迟 {value:.1f}s 超阈值 {threshold:.0f}s",
                "流式接口含模型生成时间，阈值单独放宽",
            ))
    return summary


def format_message(target: str, alerts: list[Alert], summary: dict) -> str:
    levels = [a.level for a in alerts]
    head = "CRITICAL" if "CRITICAL" in levels else ("WARNING" if "WARNING" in levels else "RECOVERY")
    lines = [
        f"【知答 ZhiDa】{head}",
        f"目标: {target}",
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    for a in alerts:
        lines.append(f"- [{a.level}] {a.title}")
        if a.detail:
            lines.append(f"  {a.detail}")
    if summary:
        lines.append("")
        lines.append(
            f"统计: 请求 {summary.get('http_requests', 0)} 次，"
            f"5xx {summary.get('http_5xx', 0)}，429 {summary.get('http_429', 0)}，"
            f"LLM {summary.get('llm_calls', 0)} 次"
        )
    return "\n".join(lines)


def send_webhook(args, alerts: list[Alert], summary: dict) -> None:
    message = format_message(args.target, alerts, summary)
    if args.dry_run or args.webhook_type == "none" or not args.webhook_url:
        log("WEBHOOK 未发送（dry-run/未配置），消息内容：\n" + message)
        return
    if args.webhook_type in ("wecom", "dingtalk"):
        # 企业微信与钉钉群机器人的文本消息结构相同；钉钉需在机器人安全设置里
        # 选择「自定义关键词」，并让消息包含该关键词（如"知答"）
        payload = {"msgtype": "text", "text": {"content": message}}
    else:
        payload = {
            "title": "知答 ZhiDa 告警",
            "target": args.target,
            "time": datetime.now().isoformat(timespec="seconds"),
            "alerts": [
                {"key": a.key, "level": a.level, "title": a.title, "detail": a.detail}
                for a in alerts
            ],
            "summary": summary,
        }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        args.webhook_url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            log(f"WEBHOOK 已发送 status={resp.status} resp={resp.read()[:200]!r}")
    except Exception as e:
        log(f"WEBHOOK 发送失败：{type(e).__name__}: {e}")


def build_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="知答 ZhiDa 告警检查器")
    p.add_argument("--target", default=env("ALERT_TARGET", "http://127.0.0.1:8080"),
                   help="服务地址（不含结尾斜杠）")
    p.add_argument("--webhook-type", default=env("ALERT_WEBHOOK_TYPE", "wecom"),
                   choices=["wecom", "dingtalk", "generic", "none"])
    p.add_argument("--webhook-url", default=env("ALERT_WEBHOOK_URL", ""))
    p.add_argument("--window", type=int, default=int(env("ALERT_WINDOW_SECONDS", "300")),
                   help="两次采样间隔（秒），应与 cron 频率一致")
    p.add_argument("--error-rate", type=float, default=float(env("ALERT_ERROR_RATE", "0.05")))
    p.add_argument("--rate-limit-ratio", type=float, default=float(env("ALERT_429_RATIO", "0.30")))
    p.add_argument("--llm-error-rate", type=float, default=float(env("ALERT_LLM_ERROR_RATE", "0.10")))
    p.add_argument("--p95-seconds", type=float, default=float(env("ALERT_P95_SECONDS", "30")))
    p.add_argument("--stream-p95-seconds", type=float,
                   default=float(env("ALERT_STREAM_P95_SECONDS", "120")))
    p.add_argument("--min-requests", type=int, default=int(env("ALERT_MIN_REQUESTS", "10")),
                   help="低于该样本量不判定比率，避免小样本误报")
    p.add_argument("--cooldown", type=int, default=int(env("ALERT_COOLDOWN_SECONDS", "1800")),
                   help="同一告警重复推送的最小间隔（秒）")
    p.add_argument("--state-file", default=env("ALERT_STATE_FILE", str(BASE_DIR / "logs" / "alert_state.json")))
    p.add_argument("--timeout", type=float, default=float(env("ALERT_TIMEOUT", "5")))
    p.add_argument("--dry-run", action="store_true", help="只打印不推送、不写状态")
    return p.parse_args()


def main() -> int:
    args = build_args()
    target = args.target.rstrip("/")
    state_path = Path(args.state_file)
    state = load_state(state_path)
    now = time.time()

    alerts: list[Alert] = []
    summary: dict = {}

    check_ready(target, args.timeout, alerts)

    status, body = fetch(f"{target}/metrics", args.timeout)
    http, llm, buckets = ({}, {}, {})
    if status == 200:
        http, llm, buckets = collect_metrics(body)
    else:
        # 服务都不可达时 ready 已告警，这里不再叠加，避免一条故障推两条
        if not any(a.key == "ready" for a in alerts):
            alerts.append(Alert("metrics", "WARNING", "指标抓取失败",
                                f"/metrics {status}：{body[:200]}"))

    # 首次运行或间隔异常（机器休眠/cron 停过）：只建基线，不拿陈旧增量判定
    prev_ts = state.get("ts")
    elapsed = now - prev_ts if prev_ts else None
    baseline_only = elapsed is None or elapsed <= 0 or elapsed > args.window * 3
    if baseline_only:
        gap = f"{elapsed:.0f}s" if elapsed is not None else "首次运行"
        log(f"建立基线（{gap}，超过 {args.window * 3}s 视为采样中断），本轮不做比率判定")
    else:
        delta_http = counter_delta(http, state.get("http", {}))
        delta_llm = counter_delta(llm, state.get("llm", {}))
        delta_buckets = counter_delta(buckets, state.get("buckets", {}))
        summary = check_rates(delta_http, delta_llm, delta_buckets, args, alerts)

    # ---- 冷却去重 + 恢复通知 ----
    firing = state.get("firing", {})
    current = {a.key: a for a in alerts}
    to_send: list[Alert] = []
    for key, alert in current.items():
        last = firing.get(key)
        if last is None or now - last >= args.cooldown:
            to_send.append(alert)
            firing[key] = now
    for key in list(firing):
        if key not in current:
            to_send.append(Alert(key, "RECOVERY", "已恢复", f"此前告警 {key} 现已消失"))
            del firing[key]

    if to_send:
        log("触发告警：" + "；".join(a.title for a in to_send))
        send_webhook(args, to_send, summary)
    elif current:
        # 故障仍在但处于冷却期：日志不能写「正常」，否则值班看日志会被误导
        log("告警持续中（冷却中）：" + "；".join(a.title for a in current.values())
            + f" 统计：{summary}")
    else:
        log(f"正常（窗口 {elapsed or 0:.0f}s）统计：{summary}")

    # 指标抓取失败时保留上次计数器，仍推进时间戳与冷却状态：
    # 否则服务宕机期间每轮都会重复推送同一条告警。
    if not args.dry_run:
        save_state(state_path, {
            "ts": now,
            "http": http if status == 200 else state.get("http", {}),
            "llm": llm if status == 200 else state.get("llm", {}),
            "buckets": buckets if status == 200 else state.get("buckets", {}),
            "firing": firing,
        })
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"告警检查器异常：{type(e).__name__}: {e}")
        sys.exit(1)
