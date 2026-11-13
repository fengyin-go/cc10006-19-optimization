"""操作审计权限与归属规则回归测试。

不依赖 pytest / httpx：直接构造 Store 快照并调用服务层与路由层函数，
保证克隆下来用 `.venv/bin/python -m unittest` 就能跑。

覆盖：
1. 归档日志只读，任何人不能再归档 / 忽略 / 回滚。
2. 忽略只有原负责人可执行，越权拒绝。
3. 人员归属变更后，历史仍归原负责人，待办转给新负责人。
4. 非法回滚（能力不足、非原负责人、状态不允许）被拦截。
5. 状态变化同步影响待处理量、质量告警与影响行数。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.audit import AuditRuleError, AuditService
from app.services.quality import QualityService
from app.store import Store, store

ARCHIVED_ID = 3  # AUDI-0003，三个月前归档，原负责人 zhangwei
TRANSFERRED_ID = 6  # AUDI-0006，原负责人 zhangwei，待办已转给 lina
ROLLBACK_ID = 2  # AUDI-0002，可回滚，原负责人 lina


class AuditCase(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        self.service = AuditService()
        self.quality = QualityService()

    def assertRejected(self, code: str, fn) -> None:
        with self.assertRaises(AuditRuleError) as ctx:
            fn()
        self.assertEqual(ctx.exception.code, code, msg=ctx.exception.message)


class ArchivedReadonlyTests(AuditCase):
    def test_archived_entry_rejects_every_action(self) -> None:
        for operator in ("zhangwei", "lina", "admin"):
            for action in ("提交审核", "归档日志", "忽略日志", "回滚操作"):
                self.assertRejected(
                    "conflict",
                    lambda: self.service.run_action(ARCHIVED_ID, action, operator=operator),
                )

    def test_archived_entry_stays_readable(self) -> None:
        entry = self.service.get_entry(ARCHIVED_ID)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["status"], "已归档")
        self.assertFalse(entry["pending"])

    def test_archived_entry_cannot_change_owner(self) -> None:
        self.assertRejected(
            "conflict",
            lambda: self.service.transfer_owner(ARCHIVED_ID, "lina", operator="admin", role="admin"),
        )


class IgnorePermissionTests(AuditCase):
    def test_only_original_owner_can_ignore(self) -> None:
        self.assertRejected(
            "forbidden",
            lambda: self.service.run_action(ROLLBACK_ID, "忽略日志", operator="wangqiang"),
        )
        entry, _ = self.service.run_action(ROLLBACK_ID, "忽略日志", operator="lina")
        self.assertEqual(entry["status"], "已忽略")

    def test_original_owner_keeps_ignore_rights_after_transfer(self) -> None:
        # AUDI-0006 当前负责人是 lina，但原负责人是 zhangwei。
        self.assertRejected(
            "forbidden",
            lambda: self.service.run_action(TRANSFERRED_ID, "忽略日志", operator="lina"),
        )

    def test_unknown_operator_rejected(self) -> None:
        self.assertRejected("forbidden", lambda: self.service.resolve_operator("hacker"))
        self.assertRejected("unauthenticated", lambda: self.service.resolve_operator(""))

    def test_only_original_owner_can_add_ignore_note(self) -> None:
        self.assertRejected(
            "forbidden",
            lambda: self.service.add_ignore_note(ARCHIVED_ID, "补充", operator="lina"),
        )
        entry, _ = self.service.add_ignore_note(ARCHIVED_ID, "复核确认", operator="zhangwei")
        self.assertEqual(entry["status"], "已归档")
        self.assertFalse(entry["pending"])
        self.assertEqual(entry["历史"][-1]["action"], "补充忽略说明")


class OwnershipTransferTests(AuditCase):
    def test_history_stays_with_original_owner_and_todo_moves(self) -> None:
        before = self.service.get_entry(TRANSFERRED_ID)
        original_history = len(before["历史"])

        entry, _ = self.service.transfer_owner(
            TRANSFERRED_ID, "chenjing", operator="admin", role="admin", reason="调岗"
        )
        self.assertEqual(entry["原负责人"], "zhangwei")
        self.assertEqual(entry["负责人"], "chenjing")
        self.assertTrue(entry["pending"])
        self.assertEqual(len(entry["历史"]), original_history + 1)
        self.assertIn("zhangwei", [item["operator"] for item in entry["历史"]])

        self.assertNotIn(TRANSFERRED_ID, [row["id"] for row in self.service.list_todos("lina")])
        self.assertIn(TRANSFERRED_ID, [row["id"] for row in self.service.list_todos("chenjing")])

    def test_rollback_rights_do_not_move_with_owner(self) -> None:
        self.service.transfer_owner(ROLLBACK_ID, "chenjing", operator="admin", role="admin")
        self.assertRejected(
            "forbidden",
            lambda: self.service.run_action(ROLLBACK_ID, "回滚操作", operator="chenjing"),
        )
        entry, _ = self.service.run_action(ROLLBACK_ID, "回滚操作", operator="lina")
        self.assertEqual(entry["status"], "草稿")

    def test_transfer_rejects_inactive_or_same_owner(self) -> None:
        self.assertRejected(
            "conflict",
            lambda: self.service.transfer_owner(TRANSFERRED_ID, "zhangwei", operator="admin", role="admin"),
        )
        self.assertRejected(
            "conflict",
            lambda: self.service.transfer_owner(TRANSFERRED_ID, "lina", operator="admin", role="admin"),
        )
        self.assertRejected(
            "forbidden",
            lambda: self.service.transfer_owner(TRANSFERRED_ID, "chenjing", operator="wangqiang"),
        )


class RollbackTests(AuditCase):
    def test_rollback_blocked_when_not_allowed(self) -> None:
        self.assertRejected(
            "conflict",
            lambda: self.service.run_action(1, "回滚操作", operator="zhangwei"),
        )

    def test_rollback_blocked_for_wrong_owner(self) -> None:
        self.assertRejected(
            "forbidden",
            lambda: self.service.run_action(ROLLBACK_ID, "回滚操作", operator="wangqiang"),
        )

    def test_rollback_blocked_from_invalid_state(self) -> None:
        self.service.run_action(ROLLBACK_ID, "回滚操作", operator="lina")
        self.assertRejected(
            "conflict",
            lambda: self.service.run_action(ROLLBACK_ID, "回滚操作", operator="lina"),
        )


class AggregationSyncTests(AuditCase):
    def test_actions_update_pending_alert_and_impact(self) -> None:
        before = self.service.stats()
        self.assertGreater(before["pending"], 0)

        self.service.run_action(TRANSFERRED_ID, "归档日志", operator="lina")
        after = self.service.stats()

        self.assertEqual(after["pending"], before["pending"] - 1)
        self.assertEqual(after["qualityAlerts"], before["qualityAlerts"] - 1)
        self.assertEqual(after["impactRows"], before["impactRows"] - 5400)
        self.assertEqual(after["archived"], before["archived"] + 1)

        audit_row = next(row for row in store.overview()["modules"] if row["name"] == "audit")
        self.assertEqual(audit_row["pending"], after["pending"])
        self.assertEqual(audit_row["abnormal"], after["qualityAlerts"])

    def test_quality_alerts_track_audit_state(self) -> None:
        before = self.quality.alerts()["auditAlerts"]
        self.service.run_action(TRANSFERRED_ID, "归档日志", operator="lina")
        after = self.quality.alerts()["auditAlerts"]
        self.assertEqual(after, before - 1)


if __name__ == "__main__":
    unittest.main()
