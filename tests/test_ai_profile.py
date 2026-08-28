"""目标岗位画像模块测试：schema 校验、存取、AI 生成解析、评分注入。"""

import json
from pathlib import Path

import pytest

from bosshunter.ai.profile import (
	DIMENSION_KEYS,
	ProfileError,
	ProfileTemplate,
	delete_profile,
	generate_profile_from_resume,
	load_profile,
	profile_from_dict,
	profile_to_dict,
	save_profile,
	_extract_json,
)


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
	return tmp_path


@pytest.fixture()
def template() -> ProfileTemplate:
	return ProfileTemplate(positioning="中型成长企业 IT 一号位")


# ─── 存取 roundtrip ─────────────────────────────────────────

class TestProfileStore:
	def test_save_load_roundtrip(self, data_dir: Path, template: ProfileTemplate):
		save_profile(data_dir, template)
		loaded = load_profile(data_dir)
		assert loaded is not None
		assert loaded.positioning == "中型成长企业 IT 一号位"
		assert loaded.weights == template.weights
		assert loaded.updated_at != ""

	def test_load_missing_returns_none(self, data_dir: Path):
		assert load_profile(data_dir) is None

	def test_load_corrupt_returns_none(self, data_dir: Path):
		(data_dir / "profile_template.json").write_text("{not json", encoding="utf-8")
		assert load_profile(data_dir) is None

	def test_delete(self, data_dir: Path, template: ProfileTemplate):
		save_profile(data_dir, template)
		assert delete_profile(data_dir) is True
		assert load_profile(data_dir) is None
		assert delete_profile(data_dir) is False

	def test_from_dict_tolerates_missing_fields(self):
		t = profile_from_dict({"positioning": "x"})
		assert t.weights == ProfileTemplate().weights
		assert t.veto_titles  # 默认否决词仍在

	def test_from_dict_rejects_non_dict(self):
		with pytest.raises(ProfileError):
			profile_from_dict([1, 2])

	def test_roundtrip_dict(self, template: ProfileTemplate):
		t2 = profile_from_dict(profile_to_dict(template))
		assert t2.positioning == template.positioning
		assert t2.veto_rules == template.veto_rules


# ─── 校验 ────────────────────────────────────────────────────

class TestProfileValidation:
	def test_reject_bad_weight_range(self, data_dir: Path, template: ProfileTemplate):
		template.weights["title_match"] = 150
		with pytest.raises(ProfileError):
			save_profile(data_dir, template)

	def test_reject_all_zero_weights(self, data_dir: Path, template: ProfileTemplate):
		for k in template.weights:
			template.weights[k] = 0
		with pytest.raises(ProfileError):
			save_profile(data_dir, template)

	def test_reject_missing_dimension(self, data_dir: Path, template: ProfileTemplate):
		del template.weights["commute"]
		with pytest.raises(ProfileError):
			save_profile(data_dir, template)

	def test_reject_bad_ai_bonus(self, data_dir: Path, template: ProfileTemplate):
		template.ai_signal_max_bonus = 99
		with pytest.raises(ProfileError):
			save_profile(data_dir, template)

	def test_dimension_keys_complete(self):
		assert set(DIMENSION_KEYS) == {
			"title_match", "reporting", "industry_scenario",
			"company_stage", "commute", "salary",
		}


# ─── AI JSON 抠取 ───────────────────────────────────────────

class TestExtractJson:
	def test_plain(self):
		assert _extract_json('{"a": 1}') == {"a": 1}

	def test_markdown_fence(self):
		assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}

	def test_with_prose(self):
		assert _extract_json('好的，结果如下：\n{"a": 1}\n以上。') == {"a": 1}

	def test_no_json(self):
		assert _extract_json("没有任何 JSON") is None


# ─── 生成（mock AI 通道）─────────────────────────────────────

AI_REPLY = """```json
{
  "positioning": "中型成长企业IT一号位，直接向老板汇报",
  "weights": {"title_match": 30, "reporting": 20, "industry_scenario": 20,
              "company_stage": 10, "commute": 10, "salary": 10},
  "dimension_notes": {"title_match": "IT负责人/IT总监家族满分", "reporting": "一级汇报满分",
                      "industry_scenario": "零售连锁满分", "company_stage": "成长期高分",
                      "commute": "深圳龙华30分钟圈", "salary": "3-5万14薪"},
  "veto_titles": ["运维工程师", "网络管理员"],
  "veto_rules": ["外包驻场", "纯运维无管理"],
  "ai_signal_keywords": ["AI", "数字化"],
  "salary_range": "3-5万/月",
  "competence_anchors": [{"name": "连锁零售IT治理", "strength": "强", "evidence": "6000店连锁治理"}],
  "search_keywords": ["IT负责人", "IT总监"]
}
```"""


class TestGenerateProfile:
	def _config(self, monkeypatch):
		from bosshunter import ai as ai_pkg
		from bosshunter.ai import profile as profile_pkg
		monkeypatch.setattr(profile_pkg, "_call_ai", lambda prompt, config: AI_REPLY, raising=True)
		return {"ai": {"api_key": "test"}}

	def test_generate_ok(self, monkeypatch):
		config = self._config(monkeypatch)
		t = generate_profile_from_resume("刘小龙，12年IT管理经验…", config)
		assert t.positioning.startswith("中型成长企业")
		assert t.veto_titles == ["运维工程师", "网络管理员"]
		assert t.competence_anchors[0]["name"] == "连锁零售IT治理"
		assert t.search_keywords == ["IT负责人", "IT总监"]

	def test_generate_empty_resume(self):
		with pytest.raises(ProfileError):
			generate_profile_from_resume("   ", {})

	def test_generate_bad_json_falls_back(self, monkeypatch):
		from bosshunter.ai import profile as profile_pkg
		monkeypatch.setattr(profile_pkg, "_call_ai", lambda p, c: "抱歉我无法输出 JSON")
		with pytest.raises(ProfileError):
			generate_profile_from_resume("简历内容", {"ai": {"api_key": "k"}})

	def test_generate_bad_weights_falls_back_default(self, monkeypatch):
		from bosshunter.ai import profile as profile_pkg
		reply = json.dumps({"weights": {k: 0 for k in DIMENSION_KEYS}, "reason": "x"})
		monkeypatch.setattr(profile_pkg, "_call_ai", lambda p, c: reply)
		t = generate_profile_from_resume("简历内容", {"ai": {"api_key": "k"}})
		assert t.weights == ProfileTemplate().weights
