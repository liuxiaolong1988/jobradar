"""AI Scorer - Match jobs against resume using Claude API."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from bosshunter.ai.credentials import AIRequestError, call_anthropic_text, get_ai_api_key
from bosshunter.cancellation import OperationCancelled, run_cancellable
from bosshunter.collection.text import clean_job_description
from bosshunter.db import (
    add_history,
    get_db,
    get_jobs_by_status,
    reset_ai_filtered_jobs,
    update_job_score,
    update_job_status,
    update_job_quick_score,
)
from bosshunter.ai.prefilter import quick_score
from bosshunter.scoring_selection import select_scoring_jobs, validate_options

# 画像驱动评分（存在 profile_template.json 时启用；否则回退"简历 vs JD"）
from bosshunter.ai.profile import DIMENSION_KEYS, load_profile

console = Console()


def get_scoring_concurrency(config: dict) -> int:
    """Return a conservative, user-configurable AI scoring worker count."""
    raw_value = config.get("ai", {}).get("scoring_concurrency", 1)
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = 1
    return max(1, min(value, 3))


SCORING_PROMPT = """你是一位严谨的招聘匹配评估员。请只依据简历与岗位JD中明确出现的事实进行评估，不补全、不猜测候选人能力。

## 候选人简历
{resume}

## 候选人个人信息
- 最高学历：{candidate_education}
- 求职招聘类型：{candidate_recruitment_type}

## 岗位信息
- 职位：{title}
- 公司：{company}
- 薪资：{salary}
- 要求：{experience}
- 学历要求：{education}
- 招聘类型：{recruitment_type}
- JD：{jd}

## 统一评分维度
逐项给分，不要自行输出总分；程序会统一求和：
1. 核心职责匹配（0-40分）：简历中有明确证据覆盖JD主要日常职责。
2. 可迁移证据（0-25分）：过往成果、工作方法和相邻经验能否迁移到该岗位。
3. 硬性要求（0-15分）：年限、学历、必备技能等明确硬要求的满足程度。
4. 工具与行业（0-10分）：工具、产品类型、客户类型或行业背景；JD仅写“优先/加分”时不能当作硬缺口。
5. 实际条件（0-10分）：城市、薪资、工作方式和稳定性等可判断条件。

## 封顶规则
仅在JD把相关内容作为核心职责或明确必备条件，且简历没有相应证据时填写caps：
- technical_required：必须掌握SQL、Linux、编程、服务器/私有化部署等硬技术，最终最高55分。
- sales_acquisition_core：岗位核心是销售获客、业绩指标或陌生开发，但简历没有对应证据，最终最高65分。
- weak_core_transfer：只有少量辅助职责可迁移，核心工作缺少直接或相邻证据，最终最高70分。
行业“优先”、工具可入职后学习、普通协作事项均不得触发封顶。hard_gaps只写JD明确要求且简历确实缺失的内容。

请严格输出一个JSON对象，不要Markdown，不要额外说明。五个score必须是整数且不得超过各自上限：
{{
  "role_summary": "岗位核心工作概括（40字内）",
  "core_duties": {{"score": 0, "evidence": "简历证据或差距（50字内）"}},
  "transferable_evidence": {{"score": 0, "evidence": "简历证据或差距（50字内）"}},
  "hard_requirements": {{"score": 0, "evidence": "满足情况（50字内）"}},
  "tools_industry": {{"score": 0, "evidence": "匹配情况（50字内）"}},
  "practical_fit": {{"score": 0, "evidence": "匹配情况（50字内）"}},
  "caps": [],
  "hard_gaps": [],
  "reason": "最关键的匹配判断（60字内）",
  "missing": "最关键缺失（40字内，没有则为空）"
}}
"""

REVIEW_PROMPT_SUFFIX = """

## 独立复核
下面是第一次评估结果。请重新核对简历证据与JD，不要迎合第一次结果；仍按上面的同一JSON结构输出各维度分数，不要输出总分。
第一次评估：{first_result}
"""

PROFILE_SCORING_PROMPT = """你是一位严谨的岗位价值评估员。候选人已定义"目标岗位画像"（他的择业标准）。
你的任务：评估**这个岗位值得他投递的程度**——不是评估他能不能干（人岗匹配只作辅助参考）。

## 候选人目标画像
- 定位：{positioning}
- 目标薪资：{salary_range}

### 评分维度与权重（按权重给分）
{dimension_rules}

### 一票否决（命中任意一条，对应维度记 0 并在 veto_hit 里写明）
{veto_rules}

### 人岗匹配辅助（能力锚点，供核心职责维度参考：岗位核心职责与锚点重叠则加分，JD 硬性要求明显超出锚点证据则减分）
{anchors}

## 候选人简历（证据核对用，截选）
{resume}

## 岗位信息
- 职位：{title}
- 公司：{company}
- 薪资：{salary}
- 要求：{experience}
- 学历要求：{education}
- JD：{jd}

## 评分规则
逐项给分，不要自行输出总分；程序会按权重加权求和：
1. title_match（0-{w_title}分）：岗位 title 与画像主 title 家族的匹配度。
2. reporting（0-{w_report}分）：汇报线与职级（从 JD 措辞推断，如"向总经理汇报""统筹公司IT"）。
3. industry_scenario（0-{w_industry}分）：行业、业务场景、转型阶段与画像偏好的匹配度。
4. company_stage（0-{w_stage}分）：企业阶段、规模、文化信号与画像的匹配度。
5. commute（0-{w_commute}分）：岗位地点与画像通勤圈（JD 无地点信息按中性给中低分）。
6. salary（0-{w_salary}分）：薪资与画像区间匹配度（面议给中性分）。
每个维度附 evidence（50字内，写 JD 中的依据）。
7. ai_signal（0-{w_ai}分，附加分）：JD 含 AI/agent/智能化/数字化探索信号的分。

## 输出
严格输出一个 JSON 对象，不要 Markdown，不要额外说明。score 必须是整数且不超过各自上限：
{{
  "role_summary": "岗位核心工作概括（40字内）",
  "title_match": {{"score": 0, "evidence": "…"}},
  "reporting": {{"score": 0, "evidence": "…"}},
  "industry_scenario": {{"score": 0, "evidence": "…"}},
  "company_stage": {{"score": 0, "evidence": "…"}},
  "commute": {{"score": 0, "evidence": "…"}},
  "salary": {{"score": 0, "evidence": "…"}},
  "ai_signal": {{"score": 0, "evidence": "…"}},
  "veto_hit": ["命中的一票否决条目，无则空数组"],
  "reason": "最关键的匹配判断（60字内）",
  "missing": "画像要求但岗位未满足的关键项（40字内，没有则为空）"
}}
"""

PROFILE_COMPONENT_BASE = 100  # 每个画像维度按满分基数给 AI，再加权折算


def _build_profile_scoring_prompt(job: dict, resume: str, profile: Any, config: dict | None = None, *, compact: bool = False) -> str:
    """画像驱动的评分 prompt：六维 + AI 信号附加分。"""
    config = config or {}
    resume_limit = 1200 if compact else 2400
    jd_limit = 900 if compact else 2000

    dim_names = {
        "title_match": "title/职能匹配",
        "reporting": "汇报线与职级",
        "industry_scenario": "行业与场景",
        "company_stage": "企业阶段与文化",
        "commute": "通勤/区位",
        "salary": "薪资",
    }
    total_weight = sum(profile.weights.values()) or 1
    dimension_rules = []
    for key in DIMENSION_KEYS:
        weight = profile.weights.get(key, 0)
        note = profile.dimension_notes.get(key, "")
        pct = round(weight * 100 / total_weight)
        dimension_rules.append(f"- {dim_names[key]}（权重 {pct}%）：{note}")
    veto_rules = "\n".join(f"- {r}" for r in profile.veto_rules) or "-（无）"
    veto_titles = "、".join(profile.veto_titles)
    if veto_titles:
        veto_rules += f"\n- title 命中以下任一关键词视为纯执行岗否决：{veto_titles}"
    anchors = "\n".join(
        f"- {a.get('name', '')}（{a.get('strength', '')}）：{a.get('evidence', '')}"
        for a in profile.competence_anchors[:8]
    ) or "-（无锚点，忽略人岗辅助参考）"

    return PROFILE_SCORING_PROMPT.format(
        positioning=profile.positioning or "未填写",
        salary_range=profile.salary_range or "未填写",
        dimension_rules="\n".join(dimension_rules),
        veto_rules=veto_rules,
        anchors=anchors,
        resume=_truncate_prompt_text(resume, resume_limit),
        title=job["title"],
        company=job["company"],
        salary=job["salary"],
        experience=job["experience"],
        education=job.get("education", "") or "未识别",
        jd=_truncate_prompt_text(clean_job_description(job.get("jd", "")), jd_limit),
        w_title=PROFILE_COMPONENT_BASE,
        w_report=PROFILE_COMPONENT_BASE,
        w_industry=PROFILE_COMPONENT_BASE,
        w_stage=PROFILE_COMPONENT_BASE,
        w_commute=PROFILE_COMPONENT_BASE,
        w_salary=PROFILE_COMPONENT_BASE,
        w_ai=int(profile.ai_signal_max_bonus),
    )


PROFILE_DIM_LABELS = {
    "title_match": "职能",
    "reporting": "汇报",
    "industry_scenario": "行业",
    "company_stage": "阶段",
    "commute": "通勤",
    "salary": "薪资",
}


def _validated_profile_result(text: str, profile: Any) -> ScoreResult | None:
    """解析画像模式 AI 回复：六维 + AI 信号 → 加权总分。"""
    result = _parse_score_response(text)
    if not isinstance(result, dict):
        return None
    dim_scores: dict[str, int] = {}
    for key in DIMENSION_KEYS:
        value = result.get(key)
        if not isinstance(value, dict):
            return None
        raw = value.get("score")
        if isinstance(raw, bool):
            return None
        try:
            score = int(raw)
        except (TypeError, ValueError):
            return None
        if score != raw or not 0 <= score <= PROFILE_COMPONENT_BASE:
            return None
        dim_scores[key] = score

    raw_ai = result.get("ai_signal", {})
    ai_bonus = 0
    if isinstance(raw_ai, dict):
        raw_bonus = raw_ai.get("score")
        try:
            ai_bonus = int(raw_bonus)
        except (TypeError, ValueError):
            ai_bonus = 0
        ai_bonus = max(0, min(ai_bonus, int(profile.ai_signal_max_bonus)))

    summary_reason = str(result.get("reason") or "").strip()
    if not summary_reason:
        return None
    missing = str(result.get("missing") or "").strip()

    raw_veto = result.get("veto_hit", [])
    veto_hits = [str(v) for v in raw_veto if str(v).strip()] if isinstance(raw_veto, list) else []

    weights: dict[str, int] = profile.weights
    total_weight = sum(weights.values()) or 1
    weighted = sum(dim_scores[k] * weights.get(k, 0) for k in DIMENSION_KEYS) / total_weight

    detail = "画像 " + " ".join(
        f"{PROFILE_DIM_LABELS[k]}{dim_scores[k]}" for k in DIMENSION_KEYS
    ) + (f" +AI{ai_bonus}" if ai_bonus else "")
    if veto_hits:
        final_score = 0
        reason = f"一票否决: {'；'.join(veto_hits[:3])} | {detail} | {summary_reason}"
    else:
        final_score = min(round(weighted) + ai_bonus, 100)
        reason = f"{detail} → {final_score} | {summary_reason}"
    if missing:
        reason = f"{reason} | 缺失: {missing}"
    return ScoreResult(
        score=final_score,
        raw_score=final_score,
        reason=reason,
        components=dim_scores,
        caps=(),
        summary_reason=summary_reason,
        missing=missing,
        structured=True,
    )


def _request_profile_score(
    job: dict,
    resume: str,
    profile: Any,
    config: dict,
    max_attempts: int,
) -> ScoreOutcome:
    """画像模式评分请求：失败重试同 legacy，但不做二次复核（结构独立）。"""
    response: str | None = None
    try:
        response = _call_claude(_build_profile_scoring_prompt(job, resume, profile, config), config)
    except AIRequestError as exc:
        if exc.kind in {"token_quota", "rate_limit", "auth", "network", "request_failed", "context_limit", "output_limit", "output_truncated"}:
            if exc.kind in {"token_quota", "rate_limit", "auth", "network", "request_failed"}:
                return ScoreOutcome(pause_reason=exc.user_message)
            return ScoreOutcome(failure_detail=exc.user_message)
        return ScoreOutcome(pause_reason=exc.user_message)

    result = _validated_profile_result(response, profile) if response else None
    for attempt in range(2, max_attempts + 1):
        if result is not None:
            break
        _notify(
            config,
            f"{job['company']}｜{job['title']} 未返回完整画像评分，正在重试（{attempt}/{max_attempts}）。",
        )
        try:
            response = _call_claude(_build_profile_scoring_prompt(job, resume, profile, config), config)
        except AIRequestError as retry_exc:
            if retry_exc.kind in {"token_quota", "rate_limit", "auth", "network", "request_failed"}:
                return ScoreOutcome(pause_reason=retry_exc.user_message)
            response = None
        result = _validated_profile_result(response, profile) if response else None

    if result is None:
        return ScoreOutcome(failure_detail="AI 未返回完整、可解析的画像评分 JSON")
    return ScoreOutcome(result=result)


COMPONENT_LIMITS = {
    "core_duties": 40,
    "transferable_evidence": 25,
    "hard_requirements": 15,
    "tools_industry": 10,
    "practical_fit": 10,
}
CAP_LIMITS = {
    "technical_required": (55, "硬技术缺口封顶55"),
    "sales_acquisition_core": (65, "核心销售获客封顶65"),
    "weak_core_transfer": (70, "核心职责迁移较弱封顶70"),
}


@dataclass(frozen=True)
class ScoreResult:
    score: int
    raw_score: int
    reason: str
    components: dict[str, int]
    caps: tuple[str, ...]
    summary_reason: str
    missing: str
    structured: bool


@dataclass(frozen=True)
class ScoreOutcome:
    result: ScoreResult | None = None
    failure_detail: str = ""
    pause_reason: str = ""


def _load_resume(config: dict) -> str:
    """Load resume from configured path."""
    resume_path = Path(config.get("profile", {}).get("resume_path", "./resume.md"))
    if not resume_path.exists():
        return ""
    return resume_path.read_text(encoding="utf-8")


def _call_claude(prompt: str, config: dict, max_tokens: int | None = None) -> str | None:
    """Call Claude API and return response text."""
    if not get_ai_api_key(config):
        console.print("[red]未设置当前 AI 服务所需的 API Key 环境变量或 config.yaml ai.api_key[/red]")
        return None
    ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
    token_limit = max_tokens if max_tokens is not None else ai_cfg.get("scoring_max_tokens", 8192)
    try:
        token_limit = max(128, min(int(token_limit or 8192), 65536))
    except (TypeError, ValueError):
        token_limit = 8192
    return run_cancellable(
        lambda: call_anthropic_text(
            prompt,
            config,
            token_limit,
            timeout=ai_cfg.get("scoring_timeout_seconds", ai_cfg.get("timeout_seconds", 180)),
            purpose="scoring",
        ),
        config,
    )


def _truncate_prompt_text(text: str, limit: int) -> str:
    """Keep both ends of long source text so compact retries retain key context."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    marker = "\n...[为适配模型上下文已裁剪]...\n"
    available = max(limit - len(marker), 2)
    head = max(int(available * 0.7), 1)
    return f"{text[:head]}{marker}{text[-(available - head):]}"


def _build_scoring_prompt(job: dict, resume: str, config: dict | None = None, *, compact: bool = False) -> str:
    config = config or {}
    resume_limit = 1400 if compact else 3000
    jd_limit = 900 if compact else 2000
    return SCORING_PROMPT.format(
        resume=_truncate_prompt_text(resume, resume_limit),
        title=job["title"],
        company=job["company"],
        salary=job["salary"],
        experience=job["experience"],
        education=job.get("education", "") or "未识别",
        recruitment_type={"campus": "校招", "experienced": "社招"}.get(
            job.get("recruitment_type", ""), "未识别"
        ),
        candidate_education=config.get("profile", {}).get("education", "") or "未填写",
        candidate_recruitment_type={
            "campus": "校招",
            "experienced": "社招",
            "both": "校招/社招均可",
        }.get(config.get("profile", {}).get("recruitment_type", ""), "未填写"),
        jd=_truncate_prompt_text(clean_job_description(job.get("jd", "")), jd_limit),
    )


def _build_review_prompt(job: dict, resume: str, first: ScoreResult, config: dict | None = None) -> str:
    first_result = {
        "components": first.components,
        "caps": list(first.caps),
        "reason": first.summary_reason,
        "missing": first.missing,
    }
    return _build_scoring_prompt(job, resume, config) + REVIEW_PROMPT_SUFFIX.format(
        first_result=json.dumps(first_result, ensure_ascii=False),
    )


def _notify(config: dict, message: str, *, error: bool = False) -> None:
    console.print(f"[{'red' if error else 'yellow'}]{message}[/{'red' if error else 'yellow'}]")
    callback = config.get("_workbench_log")
    if callable(callback):
        callback(message)


def _parse_score_response(text: str) -> dict | None:
    """Parse JSON response from Claude."""
    try:
        # Try to find JSON in response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except json.JSONDecodeError:
        pass
    return None


def _format_structured_reason(
    components: dict[str, int],
    caps: tuple[str, ...],
    summary_reason: str,
    missing: str,
    *,
    reviewed: bool = False,
) -> tuple[int, int, str]:
    raw_score = sum(components.values())
    cap_details = [CAP_LIMITS[cap] for cap in caps if cap in CAP_LIMITS]
    score = min([raw_score, *(limit for limit, _ in cap_details)])
    labels = (
        f"职责{components['core_duties']}/40 · "
        f"证据{components['transferable_evidence']}/25 · "
        f"硬要求{components['hard_requirements']}/15 · "
        f"工具行业{components['tools_industry']}/10 · "
        f"实际条件{components['practical_fit']}/10"
    )
    parts = [f"二次复核后：{labels}" if reviewed else labels]
    if cap_details:
        parts.append("、".join(detail for _, detail in cap_details))
    if summary_reason:
        parts.append(summary_reason)
    reason = "；".join(parts)
    if missing:
        reason = f"{reason} | 缺失: {missing}"
    return score, raw_score, reason


def _structured_score_result(result: dict, *, reviewed: bool = False) -> ScoreResult | None:
    components: dict[str, int] = {}
    for key, limit in COMPONENT_LIMITS.items():
        value = result.get(key)
        if not isinstance(value, dict) or "score" not in value:
            return None
        raw_value = value["score"]
        if isinstance(raw_value, bool):
            return None
        try:
            score = int(raw_value)
        except (TypeError, ValueError):
            return None
        if score != raw_value or not 0 <= score <= limit:
            return None
        components[key] = score

    raw_caps = result.get("caps", [])
    if not isinstance(raw_caps, list):
        return None
    caps = tuple(dict.fromkeys(str(cap) for cap in raw_caps if str(cap) in CAP_LIMITS))
    summary_reason = str(result.get("reason") or "").strip()
    if not summary_reason:
        return None
    missing = str(result.get("missing") or "").strip()
    score, raw_score, reason = _format_structured_reason(
        components,
        caps,
        summary_reason,
        missing,
        reviewed=reviewed,
    )
    return ScoreResult(
        score=score,
        raw_score=raw_score,
        reason=reason,
        components=components,
        caps=caps,
        summary_reason=summary_reason,
        missing=missing,
        structured=True,
    )


def _validated_score_result(text: str) -> ScoreResult | None:
    """Accept only complete structured evidence scores."""
    result = _parse_score_response(text)
    if not isinstance(result, dict):
        return None
    if all(key in result for key in COMPONENT_LIMITS):
        return _structured_score_result(result)
    return None


def _merge_review_results(first: ScoreResult, review: ScoreResult) -> ScoreResult:
    """Average two independent structured assessments and keep the stricter cap."""
    components = {
        key: (first.components[key] + review.components[key]) // 2
        for key in COMPONENT_LIMITS
    }
    target_raw_score = (first.raw_score + review.raw_score + 1) // 2
    remainder = target_raw_score - sum(components.values())
    for key in COMPONENT_LIMITS:
        if remainder <= 0:
            break
        if (first.components[key] + review.components[key]) % 2:
            components[key] += 1
            remainder -= 1
    caps = tuple(dict.fromkeys((*first.caps, *review.caps)))
    missing = review.missing or first.missing
    score, raw_score, reason = _format_structured_reason(
        components,
        caps,
        review.summary_reason,
        missing,
        reviewed=True,
    )
    return ScoreResult(
        score=score,
        raw_score=raw_score,
        reason=reason,
        components=components,
        caps=caps,
        summary_reason=review.summary_reason,
        missing=missing,
        structured=True,
    )


def _report_progress(
    config: dict,
    completed: int,
    total: int,
    scored: int,
    filtered: int,
    failed: int,
) -> None:
    callback = config.get("_workbench_score_progress")
    if callable(callback):
        callback({
            "completed": completed,
            "total": total,
            "scored": scored,
            "filtered": filtered,
            "failed": failed,
        })


def _report_checkpoint(
    config: dict,
    remaining_job_ids: list[str],
    *,
    status: str,
    pause_reason: str = "",
) -> None:
    callback = config.get("_workbench_score_checkpoint")
    if callable(callback):
        callback({
            "remaining_job_ids": list(remaining_job_ids),
            "status": status,
            "pause_reason": pause_reason,
        })


def _record_score_failure(db, job: dict, detail: str) -> None:
    """Keep a failed job pending while exposing a safe, retryable failure reason."""
    safe_detail = str(detail or "AI 未返回完整评分").strip()[:240]
    update_job_score(db, job["id"], 0, f"AI评分失败: {safe_detail}")
    add_history(db, job["id"], "score_failed", safe_detail)


def _request_score(
    job: dict,
    resume: str,
    config: dict,
    max_attempts: int,
) -> ScoreOutcome:
    """Request and validate one primary assessment without touching the database."""
    profile_template = config.get("_profile_template")
    if profile_template is not None:
        return _request_profile_score(job, resume, profile_template, config, max_attempts)
    ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
    response: str | None = None
    try:
        response = _call_claude(_build_scoring_prompt(job, resume, config), config)
    except AIRequestError as exc:
        if exc.kind == "output_truncated":
            _notify(config, f"{job['company']}｜{job['title']} 的评分回答被截断，正在增大输出 Token 上限后重试。")
            try:
                configured_tokens = int(ai_cfg.get("scoring_max_tokens", 8192) or 8192)
            except (TypeError, ValueError):
                configured_tokens = 8192
            retry_tokens = min(max(configured_tokens * 2, 512), 65536)
            try:
                response = _call_claude(_build_scoring_prompt(job, resume, config), config, retry_tokens)
            except AIRequestError as retry_exc:
                if retry_exc.kind in {"output_truncated", "output_limit", "context_limit"}:
                    return ScoreOutcome(failure_detail="调整输出 Token 后仍未获得完整评分")
                return ScoreOutcome(pause_reason=retry_exc.user_message)
        elif exc.kind == "output_limit":
            _notify(config, f"{job['company']}｜{job['title']} 正在降低输出 Token 上限后重试评分。")
            try:
                response = _call_claude(_build_scoring_prompt(job, resume, config), config, 128)
            except AIRequestError as retry_exc:
                if retry_exc.kind == "output_limit":
                    return ScoreOutcome(failure_detail="当前模型不接受调整后的输出 Token 设置")
                return ScoreOutcome(pause_reason=retry_exc.user_message)
        elif exc.kind == "context_limit":
            _notify(config, f"{job['company']}｜{job['title']} 内容较长，正在压缩后重试评分。")
            try:
                response = _call_claude(_build_scoring_prompt(job, resume, config, compact=True), config, 128)
            except AIRequestError as retry_exc:
                if retry_exc.kind == "context_limit":
                    return ScoreOutcome(failure_detail="压缩请求后仍超过模型上下文限制")
                return ScoreOutcome(pause_reason=retry_exc.user_message)
        else:
            return ScoreOutcome(pause_reason=exc.user_message)

    result = _validated_score_result(response) if response else None
    for attempt in range(2, max_attempts + 1):
        if result is not None:
            break
        _notify(
            config,
            f"{job['company']}｜{job['title']} 未返回完整评分，正在重试（{attempt}/{max_attempts}）。",
        )
        try:
            response = _call_claude(_build_scoring_prompt(job, resume, config), config)
        except AIRequestError as retry_exc:
            if retry_exc.kind in {"token_quota", "rate_limit", "auth", "network", "request_failed"}:
                return ScoreOutcome(pause_reason=retry_exc.user_message)
            response = None
        result = _validated_score_result(response) if response else None

    if result is None:
        return ScoreOutcome(failure_detail="AI 未返回完整、可解析的评分 JSON")
    return ScoreOutcome(result=result)


def _score_job_with_ai(
    job: dict,
    resume: str,
    config: dict,
    max_attempts: int,
) -> ScoreOutcome:
    """Run primary scoring and an optional independent review for borderline results."""
    outcome = _request_score(job, resume, config, max_attempts)
    first = outcome.result
    if first is None or outcome.pause_reason:
        return outcome

    if config.get("_profile_template") is not None:
        # 画像模式暂不做二次复核（复核 prompt 按 legacy 五维结构编写）
        return outcome

    ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
    review_enabled = ai_cfg.get("scoring_second_review", False) is True
    if not review_enabled or not first.structured or not 68 <= first.score <= 79:
        return outcome

    try:
        response = _call_claude(_build_review_prompt(job, resume, first, config), config)
    except AIRequestError as exc:
        if exc.kind in {"token_quota", "rate_limit", "auth", "network", "request_failed"}:
            return ScoreOutcome(result=first, pause_reason=exc.user_message)
        _notify(config, f"{job['company']}｜{job['title']} 二次复核未完成，保留第一次评分。")
        return outcome

    review = _validated_score_result(response) if response else None
    if review is None or not review.structured:
        _notify(config, f"{job['company']}｜{job['title']} 二次复核格式无效，保留第一次评分。")
        return outcome
    return ScoreOutcome(result=_merge_review_results(first, review))


def score_jobs(
    config: dict,
    *,
    scope: str = "pending",
    limit: int | None = None,
    job_ids: list[str] | None = None,
    force_rescore: bool = False,
    rescore_filtered: bool = False,
) -> tuple[int, int]:
    """Score every unscored pending job; previously scored jobs keep their result."""
    db = get_db()
    try:
        resume = _load_resume(config)
        if not resume:
            console.print("[red]无法读取简历文件[/red]")
            return 0, 0

        # 画像模式：存在 profile_template.json 时注入（否则走 legacy 简历匹配）
        data_dir = Path(config.get("_data_dir") or Path(__file__).resolve().parents[3] / "data")
        profile_template = load_profile(data_dir)
        if profile_template is not None:
            config["_profile_template"] = profile_template
            # 画像否决 title 并入预筛排除词（AI 前粗筛，省 token）
            merged_breakers = list(dict.fromkeys(
                [*config.get("profile", {}).get("deal_breakers", []), *profile_template.veto_titles]
            ))
            config.setdefault("profile", {})["deal_breakers"] = [b for b in merged_breakers if str(b).strip()]
            _notify(config, f"画像模式评分：按'{profile_template.positioning[:40]}'的六维权重匹配")

        if rescore_filtered:
            reset_count = reset_ai_filtered_jobs(db)
            _notify(config, f"已将 {reset_count} 个 AI 低分岗位加入重新评分队列。")

        options = validate_options(scope, limit, job_ids, force_rescore)
        pending_jobs = select_scoring_jobs(db, **options)
        # Preserve the lightweight mocked database seam used by legacy tests.
        if not pending_jobs and scope == "pending" and not job_ids:
            legacy_jobs = get_jobs_by_status(db, "pending")
            pending_jobs = legacy_jobs[:limit] if limit is not None else legacy_jobs
        if not pending_jobs:
            console.print("[yellow]没有待评分的岗位[/yellow]")
            return 0, 0

        threshold = config.get("scoring", {}).get("threshold", 60)
        remaining_job_ids = [str(job["id"]) for job in pending_jobs]
        _report_checkpoint(config, remaining_job_ids, status="running")

        def mark_completed(job_id: str) -> None:
            if job_id in remaining_job_ids:
                remaining_job_ids.remove(job_id)
            _report_checkpoint(config, remaining_job_ids, status="running")

        ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
        try:
            max_attempts = max(1, min(int(ai_cfg.get("scoring_max_attempts", 2) or 2), 3))
        except (TypeError, ValueError):
            max_attempts = 2
        concurrency = get_scoring_concurrency(config)
        stop_event = config.get("_workbench_stop_event")
        scored = 0
        filtered = 0
        prefiltered = 0
        processed = 0
        failed = 0
        pause_reason = ""

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task(f"评分中 (0/{len(pending_jobs)})", total=len(pending_jobs))
            ai_jobs: list[dict] = []
            for job in pending_jobs:
                if stop_event is not None and stop_event.is_set():
                    break
                qs, qs_reason = quick_score(job, config)
                update_job_quick_score(db, job["id"], qs)
                if qs == 0:
                    update_job_score(db, job["id"], qs, f"预筛不通过: {qs_reason}")
                    update_job_status(db, job["id"], "filtered")
                    filtered += 1
                    prefiltered += 1
                    processed += 1
                    mark_completed(str(job["id"]))
                    progress.update(
                        task,
                        advance=1,
                        description=f"评分中 ({processed}/{len(pending_jobs)}) [预筛淘汰{prefiltered}]",
                    )
                    _report_progress(
                        config,
                        processed,
                        len(pending_jobs),
                        scored,
                        filtered,
                        failed,
                    )
                else:
                    ai_jobs.append(job)

            executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="bosshunter-score")
            futures: dict[Future[ScoreOutcome], dict] = {}
            job_iter = iter(ai_jobs)

            def submit_next() -> bool:
                try:
                    next_job = next(job_iter)
                except StopIteration:
                    return False
                future = executor.submit(_score_job_with_ai, next_job, resume, config, max_attempts)
                futures[future] = next_job
                return True

            for _ in range(min(concurrency, len(ai_jobs))):
                submit_next()

            interrupted = False
            while futures:
                if stop_event is not None and stop_event.is_set():
                    interrupted = True
                    break
                done, _ = wait(futures, timeout=0.1, return_when=FIRST_COMPLETED)
                if not done:
                    continue
                for future in done:
                    job = futures.pop(future)
                    try:
                        outcome = future.result()
                    except OperationCancelled:
                        interrupted = True
                        break
                    except Exception as exc:
                        outcome = ScoreOutcome(failure_detail=f"评分任务异常: {type(exc).__name__}")

                    result = outcome.result
                    completed_job = False
                    if result is not None:
                        update_job_score(db, job["id"], result.score, result.reason)
                        if result.score >= threshold:
                            update_job_status(db, job["id"], "ready")
                            scored += 1
                        else:
                            update_job_status(db, job["id"], "filtered")
                            filtered += 1
                        completed_job = True
                    elif outcome.failure_detail:
                        failed += 1
                        _record_score_failure(db, job, outcome.failure_detail)
                        _notify(config, f"已跳过 {job['company']}｜{job['title']}：{outcome.failure_detail}。")
                        completed_job = True

                    if completed_job:
                        processed += 1
                        mark_completed(str(job["id"]))
                        progress.update(
                            task,
                            advance=1,
                            description=f"评分中 ({processed}/{len(pending_jobs)}) [预筛淘汰{prefiltered}]",
                        )
                        _report_progress(config, processed, len(pending_jobs), scored, filtered, failed)

                    if outcome.pause_reason:
                        pause_reason = outcome.pause_reason
                        interrupted = True
                        break
                    submit_next()
                if interrupted:
                    break

            if interrupted:
                for future in futures:
                    future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
            else:
                executor.shutdown(wait=True)

        if prefiltered > 0:
            console.print(f"[dim]  预筛阶段淘汰 {prefiltered} 个岗位（节省 {prefiltered} 次 API 调用）[/dim]")
        if pause_reason:
            _notify(
                config,
                f"AI 评分已安全暂停：{pause_reason}。已完成结果已保存，剩余 {len(remaining_job_ids)} 个岗位下次运行会继续处理。",
                error=True,
            )
        if failed:
            _notify(config, f"本轮有 {failed} 个岗位评分失败并保留为待处理，可稍后重试。")
        if remaining_job_ids:
            _report_checkpoint(
                config,
                remaining_job_ids,
                status="paused",
                pause_reason=pause_reason or "用户暂停或任务中断",
            )
        else:
            _report_checkpoint(
                config,
                [],
                status="completed_with_errors" if failed else "completed",
            )
        return scored, filtered
    finally:
        db.close()
