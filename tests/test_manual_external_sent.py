import tempfile
from pathlib import Path

import pytest

from bosshunter.db import (
    JobDeletionConfirmationError,
    JobManualSentConflictError,
    get_db,
    insert_job,
    mark_external_jobs_sent,
)


def _job(job_id: str, platform: str) -> dict:
    return {
        "id": job_id,
        "title": "产品经理",
        "company": "示例公司",
        "source_platform": platform,
        "source_job_id": job_id,
        "url": "https://www.zhaopin.com/jobdetail/example.htm" if platform == "zhilian" else "https://jobs.51job.com/example.html",
    }


def test_external_manual_sent_is_atomic_and_idempotent():
    with tempfile.TemporaryDirectory() as temporary:
        db = get_db(Path(temporary) / "jobs.db")
        try:
            insert_job(db, _job("zhilian-manual", "zhilian"))

            first = mark_external_jobs_sent(db, ["zhilian-manual"], confirmed=True)
            second = mark_external_jobs_sent(db, ["zhilian-manual"], confirmed=True)
            status = db.execute("SELECT status FROM jobs WHERE id = ?", ("zhilian-manual",)).fetchone()["status"]
            history = db.execute(
                "SELECT action, detail FROM history WHERE job_id = ? ORDER BY id",
                ("zhilian-manual",),
            ).fetchall()
        finally:
            db.close()

    assert first["affected_count"] == 1
    assert second == {"requested_count": 1, "affected_count": 0, "already_sent": ["zhilian-manual"]}
    assert status == "sent"
    assert [(row["action"], row["detail"]) for row in history] == [
        ("manual_sent", "用户在智联招聘完成投递后手动标记"),
    ]


def test_manual_sent_accepts_boss_and_updates_all_platform_jobs():
    # 改造版：BOSS 也走人工投递，手动标记对全平台开放（原断言 boss 被拒绝已过时）
    with tempfile.TemporaryDirectory() as temporary:
        db = get_db(Path(temporary) / "jobs.db")
        try:
            insert_job(db, _job("external", "51job"))
            insert_job(db, _job("boss", "boss"))
            result = mark_external_jobs_sent(db, ["external", "boss"], confirmed=True)
            statuses = {
                row["id"]: row["status"]
                for row in db.execute("SELECT id, status FROM jobs WHERE id IN ('external', 'boss')").fetchall()
            }
            history = db.execute(
                "SELECT job_id, action FROM history ORDER BY job_id"
            ).fetchall()
        finally:
            db.close()

    assert result["affected_count"] == 2
    assert statuses == {"boss": "sent", "external": "sent"}
    assert {(row["job_id"], row["action"]) for row in history} == {
        ("boss", "manual_sent"),
        ("external", "manual_sent"),
    }


def test_manual_sent_requires_explicit_confirmation():
    with tempfile.TemporaryDirectory() as temporary:
        db = get_db(Path(temporary) / "jobs.db")
        try:
            insert_job(db, _job("external", "51job"))
            with pytest.raises(JobDeletionConfirmationError):
                mark_external_jobs_sent(db, ["external"], confirmed=False)
        finally:
            db.close()
