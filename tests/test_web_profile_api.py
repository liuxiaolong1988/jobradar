"""画像 API 路由测试：GET/POST/DELETE/generate（WSGI 直调，与现有路由测试同模式）。"""

import io
import json
import tempfile
import unittest
from pathlib import Path

from bosshunter.web import server


PROFILE_PAYLOAD = {
	"version": 1,
	"positioning": "中型成长企业 IT 一号位",
	"weights": {
		"title_match": 30, "reporting": 20, "industry_scenario": 20,
		"company_stage": 10, "commute": 10, "salary": 10,
	},
	"dimension_notes": {k: "note-" + k for k in [
		"title_match", "reporting", "industry_scenario", "company_stage", "commute", "salary"]},
	"veto_titles": ["运维工程师"],
	"veto_rules": ["外包驻场"],
	"ai_signal_keywords": ["AI"],
	"ai_signal_max_bonus": 10,
	"salary_range": "3-5万/月",
	"competence_anchors": [{"name": "治理", "strength": "strong", "evidence": "6000店"}],
	"search_keywords": ["IT负责人"],
	"notes": "",
}


class ProfileApiRouteTests(unittest.TestCase):
	def setUp(self):
		self.original_base_dir = server.BASE_DIR
		self.tmp = tempfile.TemporaryDirectory()
		base = Path(self.tmp.name)
		(base / "data").mkdir(parents=True, exist_ok=True)
		(base / "config.yaml").write_text("profile: {}\n", encoding="utf-8")
		server.set_base_dir(base)

	def tearDown(self):
		server.set_base_dir(self.original_base_dir)
		self.tmp.cleanup()

	def _request(self, path: str, method: str = "GET", json_body: dict | None = None):
		status_headers = {}

		def start_response(status, headers, exc_info=None):
			status_headers["status"] = status
			status_headers["headers"] = dict(headers)

		request_body = json.dumps(json_body).encode("utf-8") if json_body is not None else b""
		environ = {
			"REQUEST_METHOD": method,
			"PATH_INFO": path,
			"QUERY_STRING": "",
			"SERVER_NAME": "127.0.0.1",
			"SERVER_PORT": "8686",
			"wsgi.version": (1, 0),
			"wsgi.url_scheme": "http",
			"wsgi.input": io.BytesIO(request_body),
			"wsgi.errors": io.StringIO(),
			"wsgi.multithread": False,
			"wsgi.multiprocess": False,
			"wsgi.run_once": False,
		}
		if json_body is not None:
			environ["CONTENT_LENGTH"] = str(len(request_body))
			environ["CONTENT_TYPE"] = "application/json"

		response_iter = server.app(environ, start_response)
		try:
			body = b"".join(
				chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
				for chunk in response_iter
			).decode("utf-8")
		finally:
			close = getattr(response_iter, "close", None)
			if close:
				close()
		return status_headers["status"], body

	def _json(self, body: str) -> dict:
		return json.loads(body)

	def test_get_missing_returns_null(self):
		status, body = self._request("/api/profile")
		self.assertEqual(status, "200 OK")
		self.assertEqual(self._json(body), None)

	def test_save_then_get_roundtrip(self):
		status, body = self._request("/api/profile", "POST", PROFILE_PAYLOAD)
		self.assertEqual(status, "200 OK")
		self.assertTrue(self._json(body)["success"])
		status, body = self._request("/api/profile")
		got = self._json(body)
		self.assertEqual(got["positioning"], "中型成长企业 IT 一号位")
		self.assertEqual(got["weights"]["title_match"], 30)
		self.assertEqual(got["veto_titles"], ["运维工程师"])

	def test_save_invalid_weights_rejected(self):
		bad = dict(PROFILE_PAYLOAD, weights={"title_match": 999})
		status, body = self._request("/api/profile", "POST", bad)
		self.assertEqual(status, "400 Bad Request")
		self.assertIn("权重", self._json(body)["error"])

	def test_save_non_object_rejected(self):
		status, body = self._request("/api/profile", "POST", [1, 2])
		self.assertEqual(status, "400 Bad Request")

	def test_delete_after_save(self):
		self._request("/api/profile", "POST", PROFILE_PAYLOAD)
		status, body = self._request("/api/profile", "DELETE")
		self.assertTrue(self._json(body)["deleted"])
		status, body = self._request("/api/profile")
		self.assertEqual(self._json(body), None)

	def test_generate_requires_resume(self):
		status, body = self._request("/api/profile/generate", "POST", {})
		self.assertEqual(status, "400 Bad Request")
		self.assertIn("简历", self._json(body)["error"])


if __name__ == "__main__":
	unittest.main()
