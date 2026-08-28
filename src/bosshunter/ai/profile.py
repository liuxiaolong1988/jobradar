"""目标岗位画像模块：简历 → 初版画像生成、存取、校验。

画像存 data/profile_template.json（独立文件，不进 DB、不改 schema）。
评分时画像存在 → 画像驱动匹配；不存在 → 回退旧"简历 vs JD"逻辑。

Schema 设计对齐 docs/目标岗位画像-2026-08.md（魔王大人 2026-08-28 定稿 v1.0）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bosshunter.ai.credentials import get_ai_api_key
from bosshunter.cancellation import run_cancellable

PROFILE_VERSION = 1

# 六维固定 key（前端渲染顺序、评分 prompt 维度顺序都依赖它）
DIMENSION_KEYS = ["title_match", "reporting", "industry_scenario", "company_stage", "commute", "salary"]

MAX_WEIGHT = 100


class ProfileError(ValueError):
	"""Invalid profile template operations."""


@dataclass
class ProfileTemplate:
	"""目标岗位画像（可人工编辑、可 AI 生成初版）。"""

	version: int = PROFILE_VERSION
	positioning: str = ""            # 一句话定位（如"小平台CIO型IT一号位"）
	# 六维权重（0-100，程序按总和归一化；权重和不必恰好 100）
	weights: dict[str, int] = field(default_factory=lambda: {
		"title_match": 30,
		"reporting": 20,
		"industry_scenario": 20,
		"company_stage": 10,
		"commute": 10,
		"salary": 10,
	})
	# 各维度自由文本：评分依据/加分减分规则描述（拼进 prompt）
	dimension_notes: dict[str, str] = field(default_factory=lambda: {
		"title_match": "命中 IT负责人/IT总监/信息化总监/数字化负责人等主 title 家族满分；经理级以下减半；纯执行 title 否决",
		"reporting": "一级汇报（总经理/CEO）+ 参加经营会议 = 满分；总监级向副总汇报 = 中；向 IT 经理汇报 = 低分",
		"industry_scenario": "零售连锁/多门店规模化、中型企业数字化转型/系统换代 = 满分；转型期企业 = 高分；其它行业中性；JD 含 AI 应用探索加分",
		"company_stage": "中型成长期转型企业、创业型 + 前景 = 高分；成熟大企业 = 中；IT 被当成本中心 = 低分",
		"commute": "深圳龙华/龙岗/凤岗 30 分钟车程圈 = 满分；深圳其它区 = 中；通勤超 1 小时 = 低分",
		"salary": "月薪 3-5万×14薪 = 满分；更高 = 满分+；面议 = 中性；明显低于 3 万 = 大幅降权",
	})
	# 一票否决（title 关键词 + JD 判定规则，采集后 AI 前做粗筛 + 评分时复核）
	veto_titles: list[str] = field(default_factory=lambda: [
		"网络管理员", "IT工程师", "运维工程师", "桌面支持", "IT专员", "机房管理员",
		"开发工程师", "后端", "前端", "架构师", "算法工程师", "测试工程师",
	])
	veto_rules: list[str] = field(default_factory=lambda: [
		"外包/驻场性质（第三方服务商派驻客户现场）",
		"纯运维/纯技术无管理职责",
		"大小周/996/长期出差/外派驻点",
	])
	# JD 中 AI 信号加分词（附加分 ≤10）
	ai_signal_keywords: list[str] = field(default_factory=lambda: [
		"AI", "人工智能", "agent", "智能化", "数字化", "大模型",
	])
	ai_signal_max_bonus: int = 10
	# 目标薪资区间（前端展示 + 评分参照；空 = 不约束）
	salary_range: str = "3-5万/月 × 14薪"
	# 能力锚点：简历证据摘要（人岗匹配辅助分参照，AI 从简历提取）
	competence_anchors: list[dict[str, str]] = field(default_factory=list)
	# 采集搜索关键词建议（生成画像时 AI 顺带产出）
	search_keywords: list[str] = field(default_factory=lambda: [
		"IT负责人", "IT总监", "信息化总监", "数字化负责人", "CIO",
	])
	notes: str = ""                   # 自由备注（不进评分 prompt，仅前端展示）
	updated_at: str = ""


def _profile_path(data_dir: Path) -> Path:
	return data_dir / "profile_template.json"


def load_profile(data_dir: Path) -> ProfileTemplate | None:
	"""读取画像文件；不存在返回 None（评分走 resume 兜底）。"""
	path = _profile_path(data_dir)
	if not path.exists():
		return None
	try:
		raw = json.loads(path.read_text(encoding="utf-8"))
	except (json.JSONDecodeError, OSError):
		return None
	return profile_from_dict(raw)


def save_profile(data_dir: Path, template: ProfileTemplate) -> Path:
	"""校验并保存画像；返回文件路径。"""
	validate_profile(template)
	template.updated_at = _now_iso()
	path = _profile_path(data_dir)
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(profile_to_dict(template), ensure_ascii=False, indent=2), encoding="utf-8")
	return path


def delete_profile(data_dir: Path) -> bool:
	"""删除画像（回滚到 resume 兜底评分）。返回是否确实删除了文件。"""
	path = _profile_path(data_dir)
	if path.exists():
		path.unlink()
		return True
	return False


def profile_to_dict(t: ProfileTemplate) -> dict[str, Any]:
	return {
		"version": t.version,
		"positioning": t.positioning,
		"weights": dict(t.weights),
		"dimension_notes": dict(t.dimension_notes),
		"veto_titles": list(t.veto_titles),
		"veto_rules": list(t.veto_rules),
		"ai_signal_keywords": list(t.ai_signal_keywords),
		"ai_signal_max_bonus": t.ai_signal_max_bonus,
		"salary_range": t.salary_range,
		"competence_anchors": list(t.competence_anchors),
		"search_keywords": list(t.search_keywords),
		"notes": t.notes,
		"updated_at": t.updated_at,
	}


def profile_from_dict(raw: dict[str, Any]) -> ProfileTemplate:
	"""从 dict 构造（对旧版/缺字段容错：缺省值补齐）。"""
	if not isinstance(raw, dict):
		raise ProfileError("画像文件必须是 JSON 对象")
	defaults = ProfileTemplate()
	return ProfileTemplate(
		version=int(raw.get("version", PROFILE_VERSION)),
		positioning=str(raw.get("positioning", defaults.positioning)),
		weights={k: int(raw.get("weights", {}).get(k, v)) for k, v in defaults.weights.items()
			if isinstance(raw.get("weights", {}), dict)} if isinstance(raw.get("weights"), dict) else dict(defaults.weights),
		dimension_notes={k: str(raw.get("dimension_notes", {}).get(k, v)) for k, v in defaults.dimension_notes.items()
			if isinstance(raw.get("dimension_notes", {}), dict)} if isinstance(raw.get("dimension_notes"), dict) else dict(defaults.dimension_notes),
		veto_titles=[str(x) for x in raw.get("veto_titles", defaults.veto_titles) if str(x).strip()],
		veto_rules=[str(x) for x in raw.get("veto_rules", defaults.veto_rules) if str(x).strip()],
		ai_signal_keywords=[str(x) for x in raw.get("ai_signal_keywords", defaults.ai_signal_keywords) if str(x).strip()],
		ai_signal_max_bonus=int(raw.get("ai_signal_max_bonus", defaults.ai_signal_max_bonus)),
		salary_range=str(raw.get("salary_range", defaults.salary_range)),
		competence_anchors=[dict(a) for a in raw.get("competence_anchors", []) if isinstance(a, dict)],
		search_keywords=[str(x) for x in raw.get("search_keywords", defaults.search_keywords) if str(x).strip()],
		notes=str(raw.get("notes", "")),
		updated_at=str(raw.get("updated_at", "")),
	)


def validate_profile(t: ProfileTemplate) -> None:
	"""保存前校验：权重 0-100、六维齐全、数组字段类型。"""
	missing = [k for k in DIMENSION_KEYS if k not in t.weights]
	if missing:
		raise ProfileError(f"缺少权重维度: {', '.join(missing)}")
	for k, v in t.weights.items():
		if not isinstance(v, int) or not (0 <= v <= MAX_WEIGHT):
			raise ProfileError(f"权重 {k} 必须是 0-{MAX_WEIGHT} 的整数")
	total = sum(t.weights.values())
	if total <= 0:
		raise ProfileError("至少一个维度权重大于 0")
	if not isinstance(t.veto_titles, list) or not isinstance(t.veto_rules, list):
		raise ProfileError("否决词表必须是数组")
	if not (0 <= t.ai_signal_max_bonus <= 30):
		raise ProfileError("AI 信号附加分上限须在 0-30 之间")


# ---------------------------------------------------------------------------
# AI 生成初版画像：简历 → 画像 JSON
# ---------------------------------------------------------------------------

PROFILE_GEN_PROMPT = """你是一位资深职业顾问。请根据候选人简历，生成"目标岗位画像"——回答的问题是：什么样的岗位值得这位候选人投递。
不要评估候选人能力高低，只推断他的选择标准。

## 候选人简历
{resume}

## 输出要求
生成一个 JSON 对象（不要 Markdown、不要额外说明），字段如下：
{{
  "positioning": "一句话定位，40字内，描述他该投什么样的岗位（例如：中型成长企业IT一号位…）",
  "weights": {{
    "title_match": 30, "reporting": 20, "industry_scenario": 20,
    "company_stage": 10, "commute": 10, "salary": 10
  }},
  "dimension_notes": {{
    "title_match": "依据简历推断的 title 家族与评分规则，80字内",
    "reporting": "汇报线与职级偏好，80字内",
    "industry_scenario": "行业与业务场景偏好，80字内",
    "company_stage": "企业阶段与文化偏好，80字内",
    "commute": "地点偏好（从简历意向城市推断），80字内",
    "salary": "薪资期望（从简历薪酬推断），80字内"
  }},
  "veto_titles": ["依据岗位方向推断的排除 title，10-18个"],
  "veto_rules": ["一票否决规则描述，3-5条"],
  "ai_signal_keywords": ["JD中的AI/数字化加分词，5-8个"],
  "salary_range": "目标薪资区间简述，30字内",
  "competence_anchors": [
    {{"name": "能力块名称", "strength": "强|中|弱", "evidence": "简历证据摘要，40字内"}}
  ],
  "search_keywords": ["建议的招聘平台搜索关键词，5-8个"]
}}

## 推断原则
1. weights 总和建议 100；从他简历的求职意向、跳槽动机推断各维度重要性，不要平均主义
2. competence_anchors 列 5-8 条：只写简历里有明确证据的能力
3. veto 推断要贴合他的岗位方向（管理岗应排除纯执行岗）
4. search_keywords 用招聘平台常见搜索词（如 IT负责人、IT总监），不要生造
5. 所有文字用中文
"""


def _now_iso() -> str:
	from datetime import datetime
	return datetime.now().isoformat(timespec="seconds")


def _call_ai(prompt: str, config: dict) -> str:
	"""调 AI 返回文本（复用 credentials 通道；失败抛 ProfileError）。"""
	if not get_ai_api_key(config):
		raise ProfileError("未配置 AI API Key，无法生成画像（配置 → AI 设置）")
	ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
	try:
		token_limit = max(128, min(int(ai_cfg.get("scoring_max_tokens", 8192)), 65536))
	except (TypeError, ValueError):
		token_limit = 8192
	try:
		from bosshunter.ai.credentials import call_anthropic_text
		text = run_cancellable(
			lambda: call_anthropic_text(
				prompt,
				config,
				token_limit,
				timeout=ai_cfg.get("scoring_timeout_seconds", ai_cfg.get("timeout_seconds", 180)),
				purpose="scoring",
			),
			config,
		)
	except Exception as exc:  # noqa: BLE001 - AI 通道异常统一转画像错误
		raise ProfileError(f"AI 调用失败: {exc}") from exc
	if not text:
		raise ProfileError("AI 返回为空，画像生成失败")
	return text


def generate_profile_from_resume(resume_text: str, config: dict) -> ProfileTemplate:
	"""简历 → 初版画像（AI 生成 + 结构校验 + 缺省兜底）。"""
	resume = resume_text.strip()
	if not resume:
		raise ProfileError("简历内容为空：请先在配置页上传简历")
	if len(resume) > 6000:
		resume = resume[:6000]
	raw = _call_ai(PROFILE_GEN_PROMPT.format(resume=resume), config)
	data = _extract_json(raw)
	if not isinstance(data, dict):
		raise ProfileError("AI 返回的不是 JSON 对象，画像生成失败")
	# 生成结果只做"补齐"不做"替换"：AI 漏字段用默认值，字段类型错则丢弃用默认值
	template = profile_from_dict(data)
	# 权重兜底：AI 权重异常（全 0 / 超界）时回默认
	try:
		validate_profile(template)
	except ProfileError:
		template.weights = dict(ProfileTemplate().weights)
	template.competence_anchors = [a for a in template.competence_anchors if a.get("name")][:10]
	template.positioning = template.positioning[:120]
	return template


def _extract_json(text: str) -> Any:
	"""从 AI 回复中抠 JSON（容错 markdown 代码围栏/前后杂文）。"""
	text = text.strip()
	if text.startswith("```"):
		text = text.split("\n", 1)[-1] if "\n" in text else text
		text = text.rsplit("```", 1)[0]
	start = text.find("{")
	if start < 0:
		return None
	end = text.rfind("}")
	if end <= start:
		return None
	try:
		return json.loads(text[start:end + 1])
	except json.JSONDecodeError:
		return None
