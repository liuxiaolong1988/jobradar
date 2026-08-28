"""画像驱动评分测试：prompt 构建、结果解析、加权计算、回退逻辑。"""

import json

import pytest

from bosshunter.ai.profile import ProfileTemplate
from bosshunter.ai.scorer import (
	PROFILE_COMPONENT_BASE,
	_build_profile_scoring_prompt,
	_request_score,
	_score_job_with_ai,
	_validated_profile_result,
)


@pytest.fixture()
def profile() -> ProfileTemplate:
	t = ProfileTemplate(positioning="中型成长企业 IT 一号位")
	t.competence_anchors = [{"name": "连锁零售IT治理", "strength": "strong", "evidence": "6000店连锁治理"}]
	return t


JOB = {
	"id": "j1",
	"title": "IT负责人",
	"company": "某零售集团",
	"salary": "30-50K·14薪",
	"experience": "5-10年",
	"education": "本科",
	"jd": "负责公司信息化建设，向总经理汇报，推动数字化转型与AI应用探索。",
}


def _ai_reply(**overrides) -> str:
	payload = {
		"role_summary": "IT一号位",
		"title_match": {"score": 90, "evidence": "IT负责人命中主title"},
		"reporting": {"score": 85, "evidence": "向总经理汇报"},
		"industry_scenario": {"score": 88, "evidence": "零售+数字化转型"},
		"company_stage": {"score": 70, "evidence": "成长期集团"},
		"commute": {"score": 60, "evidence": "地点未写"},
		"salary": {"score": 90, "evidence": "30-50K命中"},
		"ai_signal": {"score": 8, "evidence": "AI应用探索"},
		"veto_hit": [],
		"reason": "岗位与画像高度匹配",
		"missing": "",
	}
	payload.update(overrides)
	return json.dumps(payload, ensure_ascii=False)


# ─── prompt 构建 ─────────────────────────────────────────────

class TestProfilePrompt:
	def test_prompt_contains_profile_elements(self, profile):
		prompt = _build_profile_scoring_prompt(JOB, "简历内容", profile, {})
		assert "中型成长企业 IT 一号位" in prompt
		assert "一票否决" in prompt
		assert "6000店连锁治理" in prompt
		assert "title/职能匹配" in prompt
		assert "权重 30%" in prompt  # title_match 权重占比
		assert "IT负责人" in prompt

	def test_prompt_weight_pct_normalized(self, profile):
		# 权重 30/100 → 30%
		prompt = _build_profile_scoring_prompt(JOB, "r", profile, {})
		assert "权重 30%" in prompt
		assert "权重 10%" in prompt

	def test_prompt_empty_anchors(self, profile):
		profile.competence_anchors = []
		prompt = _build_profile_scoring_prompt(JOB, "r", profile, {})
		assert "无锚点" in prompt


# ─── 结果解析 ────────────────────────────────────────────────

class TestProfileResult:
	def test_weighted_score(self, profile):
		result = _validated_profile_result(_ai_reply(), profile)
		assert result is not None
		# (90*30+85*20+88*20+70*10+60*10+90*10)/100 = 83.6 → round 84 + ai 8 = 92
		assert result.score == 84 + 8
		assert result.caps == ()
		assert "画像" in result.reason and "+AI8" in result.reason

	def test_veto_zero_score(self, profile):
		result = _validated_profile_result(_ai_reply(veto_hit=["外包驻场性质"]), profile)
		assert result is not None
		assert result.score == 0
		assert "一票否决" in result.reason

	def test_missing_dim_rejected(self, profile):
		reply = json.loads(_ai_reply())
		del reply["commute"]
		assert _validated_profile_result(json.dumps(reply), profile) is None

	def test_out_of_range_rejected(self, profile):
		reply = json.loads(_ai_reply())
		reply["salary"]["score"] = 120
		assert _validated_profile_result(json.dumps(reply), profile) is None

	def test_ai_bonus_clamped(self, profile):
		result = _validated_profile_result(_ai_reply(), profile)
		assert result is not None
		profile.ai_signal_max_bonus = 3
		result2 = _validated_profile_result(_ai_reply(), profile)
		assert result2.score == 84 + 3

	def test_no_reason_rejected(self, profile):
		reply = json.loads(_ai_reply())
		reply["reason"] = ""
		assert _validated_profile_result(json.dumps(reply), profile) is None

	def test_max_score_capped_100(self, profile):
		reply = json.loads(_ai_reply())
		for k in ["title_match", "reporting", "industry_scenario", "company_stage", "commute", "salary"]:
			reply[k]["score"] = 100
		reply["ai_signal"]["score"] = 50
		result = _validated_profile_result(json.dumps(reply), profile)
		assert result is not None
		assert result.score <= 100


# ─── 模式分支与回退 ──────────────────────────────────────────

class TestProfileModeBranch:
	def test_request_score_uses_profile_path(self, profile, monkeypatch):
		from bosshunter.ai import scorer as scorer_pkg
		called = []
		monkeypatch.setattr(
			scorer_pkg, "_call_claude",
			lambda prompt, config, max_tokens=None: called.append(prompt) or _ai_reply(),
		)
		config = {"_profile_template": profile}
		outcome = _request_score(JOB, "简历", config, 2)
		assert outcome.result is not None
		assert called and "目标岗位画像" in called[0]

	def test_request_score_legacy_without_profile(self, monkeypatch):
		from bosshunter.ai import scorer as scorer_pkg
		monkeypatch.setattr(scorer_pkg, "_call_claude", lambda p, c, max_tokens=None: None)
		outcome = _request_score(JOB, "简历", {}, 1)
		# legacy 通道无 AI key 场景或空回复 → 无结果，但绝不能走画像 prompt
		assert outcome.result is None

	def test_second_review_skipped_in_profile_mode(self, profile, monkeypatch):
		from bosshunter.ai import scorer as scorer_pkg
		from bosshunter.ai.scorer import ScoreOutcome
		result = _validated_profile_result(_ai_reply(), profile)
		monkeypatch.setattr(
			scorer_pkg, "_request_score",
			lambda *a, **k: ScoreOutcome(result=result),
		)
		review_calls = []
		monkeypatch.setattr(
			scorer_pkg, "_call_claude",
			lambda p, c, max_tokens=None: review_calls.append(p) or "x",
		)
		config = {"ai": {"scoring_second_review": True}, "_profile_template": profile}
		outcome = _score_job_with_ai(JOB, "简历", config, 2)
		assert outcome.result is not None
		assert review_calls == []  # 画像模式不触发二次复核
