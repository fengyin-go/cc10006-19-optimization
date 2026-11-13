"""操作审计业务规则：权限、归属、归档只读与状态流转都收在这里。

核心约束（路由层不做业务判断）：

1. 归档日志对所有人只读：不允许再提交审核、归档、忽略或回滚，也不允许改写历史。
2. 忽略日志只有「原操作来源对应的负责人」可以执行，其他人一律拒绝。
3. 待办按「当前负责人」流转；人员归属变更后，历史仍记在原负责人名下，
   只有尚未完成的待办转交新负责人。
4. 回滚只对标记为「可回滚」的日志开放，且写权限只属于原负责人，不随归属转交共享。
5. 任何状态变化都会写一条历史（含操作人、变更前后状态、影响行数、处理时间），
   并实时反映到待处理量、异常告警和影响行数汇总。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.seed import OWNER_DIRECTORY, OWNER_STATUS
from app.store import store

MODULE = "audit"
REQUIRED_FIELDS = ["日志编号", "操作类型", "操作对象"]
STATUS_ORDER = ["草稿", "待审核", "已归档", "已忽略"]
ARCHIVED_STATUSES = ["已归档", "已忽略"]
ACTION_RULES = {
    "提交审核": "待审核",
    "归档日志": "已归档",
    "忽略日志": "已忽略",
    "回滚操作": "草稿",
}
# 每个动作允许的起始状态，防止任何状态都能被写成任意目标状态。
SOURCE_STATUSES = {
    "提交审核": ["草稿"],
    "归档日志": ["待审核"],
    "忽略日志": ["草稿", "待审核"],
    "回滚操作": ["待审核"],
}
# 只有这些动作会改变状态；补充忽略说明只追加备注，不改状态。
STATUS_ACTIONS = set(ACTION_RULES)
AUDIT_OPERATOR_ROLE = "admin"


class AuditRuleError(Exception):
    """业务规则不满足。error_code 由路由层映射成 HTTP 状态码。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AuditService:
    # ------------------------------------------------------------------ 身份
    def resolve_operator(self, operator: str | None) -> str:
        """校验并归一化操作人账号。身份缺失或不在人员目录里都直接拒绝。"""
        account = str(operator or "").strip()
        if not account:
            raise AuditRuleError("unauthenticated", "缺少操作人身份，无法执行审计动作")
        if account not in OWNER_DIRECTORY:
            raise AuditRuleError("forbidden", f"账号 {account} 不在人员目录中，权限不足")
        return account

    def is_admin(self, operator: str, role: str | None = None) -> bool:
        # 管理员身份以服务端人员目录为准，不接受客户端自报的 X-Role 提权。
        return operator == AUDIT_OPERATOR_ROLE

    def display_name(self, account: str | None) -> str:
        if not account:
            return "—"
        return OWNER_DIRECTORY.get(account, account)

    # ------------------------------------------------------------------ 视图
    def _decorate(self, entry: dict[str, Any]) -> dict[str, Any]:
        """补齐展示字段，保证原负责人、当前负责人多处口径一致。"""
        original = str(entry.get("原负责人") or entry.get("操作来源") or "")
        current = str(entry.get("负责人") or original)
        entry["原负责人"] = original
        entry["负责人"] = current
        entry.setdefault("历史", [])
        entry.setdefault("忽略说明", None)
        entry.setdefault("归属变更时间", None)
        entry.setdefault("归属变更原因", None)
        entry["原负责人姓名"] = self.display_name(original)
        entry["负责人姓名"] = self.display_name(current)
        # 老数据的 history 可能缺展示字段，统一补齐，保证责任变更前后都能追溯。
        for item in entry["历史"]:
            item.setdefault("operatorName", self.display_name(item.get("operator")))
            item.setdefault("originalOwner", original)
            # 历史归属：转交动作取转交后的负责人，其余动作默认取原负责人，
            # 不能用当前负责人倒填，否则早期操作会被错误归到新负责人。
            item.setdefault("owner", item.get("toOwner") or original)
            item.setdefault("impact", self._impact(entry))
        return entry

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        owner: str | None = None,
        pending: bool | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("日志编号", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if owner:
            # 按当前负责人筛选待办；要查历史归属请显式用 original_owner。
            rows = [row for row in rows if str(row.get("负责人")) == owner]
        if pending is not None:
            rows = [row for row in rows if bool(row.get("pending")) is pending]
        rows = [self._decorate(row) for row in rows]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._decorate(entry) if entry is not None else None

    def list_todos(self, operator: str) -> list[dict[str, Any]]:
        """当前负责人的待办：只包含尚未完成的日志，已归档历史不会出现在待办里。"""
        rows = [
            self._decorate(row)
            for row in store.rows(MODULE)
            if bool(row.get("pending")) and str(row.get("负责人")) == operator
        ]
        return sorted(rows, key=lambda row: int(row.get("id", 0)))

    def stats(self) -> dict[str, Any]:
        """实时汇总：列表、质量告警、概览待处理量共用这一份口径。"""
        rows = [self._decorate(row) for row in store.rows(MODULE)]
        return {
            "total": len(rows),
            "archived": sum(1 for row in rows if row.get("status") == "已归档"),
            "ignored": sum(1 for row in rows if row.get("status") == "已忽略"),
            "pending": sum(1 for row in rows if row.get("pending")),
            "qualityAlerts": sum(1 for row in rows if row.get("abnormal")),
            "impactRows": sum(self._impact(row) for row in rows if row.get("pending")),
            "transferred": sum(1 for row in rows if row.get("负责人") != row.get("原负责人")),
        }

    def _impact(self, entry: dict[str, Any]) -> int:
        value = entry.get("影响行数")
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    # ------------------------------------------------------------------ 登记
    def create_entry(
        self,
        values: dict[str, Any],
        *,
        operator: str,
        role: str | None = None,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing

        source = str(values.get("操作来源") or operator).strip()
        if source not in OWNER_DIRECTORY:
            raise AuditRuleError("bad_request", f"操作来源 {source} 不在人员目录中")
        # 原负责人由操作来源决定，登记后不再随人员调整变化。
        original_owner = source
        current_owner = str(values.get("负责人") or source).strip()
        if current_owner not in OWNER_DIRECTORY:
            raise AuditRuleError("bad_request", f"负责人 {current_owner} 不在人员目录中")
        if current_owner != source and not self.is_admin(operator, role):
            raise AuditRuleError("forbidden", "只有管理员可以登记时就转交负责人")

        now = self._now()
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
        }
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["操作详情"] = values.get("操作详情")
        entry["操作来源"] = source
        entry["执行时间"] = values.get("执行时间") or now[:10]
        entry["影响行数"] = self._coerce_int(values.get("影响行数"))
        entry["回滚能力"] = values.get("回滚能力") or "不可回滚"
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["原负责人"] = original_owner
        entry["负责人"] = current_owner
        entry["负责人状态"] = OWNER_STATUS.get(current_owner, "在职")
        entry["归属变更时间"] = None
        entry["归属变更原因"] = None
        entry["忽略说明"] = None
        entry["历史"] = [self._history(
            action="登记",
            from_status=None,
            to_status=STATUS_ORDER[0],
            operator=operator,
            entry=entry,
            note=f"操作来源 {source}",
        )]
        rows.append(entry)
        return self._decorate(entry), []

    # ------------------------------------------------------------------ 动作
    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        operator: str,
        role: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            raise AuditRuleError("not_found", f"审计日志 {entry_id} 不存在")
        self._decorate(entry)

        action = str(action or "").strip()
        if action not in ACTION_RULES:
            raise AuditRuleError("bad_request", f"动作「{action}」不属于操作审计可执行范围")

        # 归档 / 已忽略：整条日志只读，任何状态动作都拒绝。
        if entry.get("status") in ARCHIVED_STATUSES:
            raise AuditRuleError(
                "conflict",
                f"审计日志 {entry_id} 处于「{entry.get('status')}」，归档记录只读，不能再执行「{action}」",
            )

        self._authorize(entry, action, operator, role)
        self._check_transition(entry, action)
        self._check_rollback(entry, action)

        target = ACTION_RULES[action]
        previous = str(entry.get("status"))
        entry["status"] = target
        entry["pending"] = target not in ARCHIVED_STATUSES
        # 只有待审核算质量告警；忽略或归档后告警同步消除。
        entry["abnormal"] = target == "待审核"
        entry["历史"].append(self._history(
            action=action,
            from_status=previous,
            to_status=target,
            operator=operator,
            entry=entry,
            note=action,
        ))
        return self._decorate(entry), f"审计日志已执行「{action}」，当前状态：{target}"

    def _authorize(self, entry: dict[str, Any], action: str, operator: str, role: str | None) -> None:
        original = str(entry.get("原负责人") or entry.get("操作来源"))
        current = str(entry.get("负责人") or original)

        if action == "忽略日志":
            # 忽略是原操作来源负责人的专属权限，不随归属转交共享。
            if operator != original:
                raise AuditRuleError(
                    "forbidden",
                    f"只有原负责人 {self.display_name(original)} 可以忽略该日志，当前账号无权操作",
                )
            return

        if action == "回滚操作":
            # 回滚写权限只属于原负责人，新负责人不共享。
            if operator != original:
                raise AuditRuleError(
                    "forbidden",
                    f"回滚写权限仅属于原负责人 {self.display_name(original)}，当前账号无权操作",
                )
            return

        # 提交审核 / 归档属于当前待办，由当前负责人处理。
        if operator != current and not self.is_admin(operator, role):
            raise AuditRuleError(
                "forbidden",
                f"该日志当前负责人为 {self.display_name(current)}，当前账号无权执行「{action}」",
            )

    def _check_transition(self, entry: dict[str, Any], action: str) -> None:
        current = str(entry.get("status"))
        allowed = SOURCE_STATUSES.get(action, [])
        if current not in allowed:
            raise AuditRuleError(
                "conflict",
                f"审计日志当前状态为「{current}」，不能执行「{action}」",
            )

    def _check_rollback(self, entry: dict[str, Any], action: str) -> None:
        if action != "回滚操作":
            return
        capability = str(entry.get("回滚能力") or "")
        if capability != "可回滚":
            raise AuditRuleError(
                "conflict",
                f"审计日志标记为「{capability or '不可回滚'}」，非法回滚请求已拒绝",
            )

    # ------------------------------------------------------------------ 归属
    def transfer_owner(
        self,
        entry_id: int,
        new_owner: str,
        *,
        operator: str,
        role: str | None = None,
        reason: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            raise AuditRuleError("not_found", f"审计日志 {entry_id} 不存在")
        self._decorate(entry)

        # 归档记录只读：不允许再变更归属，避免旧日志被重新拉回新负责人的待办。
        if entry.get("status") in ARCHIVED_STATUSES:
            raise AuditRuleError(
                "conflict",
                f"审计日志 {entry_id} 处于「{entry.get('status')}」，归档记录只读，不能变更归属",
            )

        original = str(entry.get("原负责人"))
        # 归属变更只允许管理员或原负责人发起；当前负责人不能把责任再转走。
        if not self.is_admin(operator, role) and operator != original:
            raise AuditRuleError("forbidden", "只有管理员或原负责人可以变更归属")

        target = str(new_owner or "").strip()
        if not target:
            raise AuditRuleError("bad_request", "缺少新负责人账号")
        if target not in OWNER_DIRECTORY:
            raise AuditRuleError("bad_request", f"账号 {target} 不在人员目录中")
        if OWNER_STATUS.get(target) != "在职":
            raise AuditRuleError("conflict", f"账号 {target} 当前不在职，不能接收待办")
        if target == str(entry.get("负责人")):
            raise AuditRuleError("conflict", "新负责人与当前负责人相同，无需变更")

        previous_owner = str(entry.get("负责人") or original)
        now = self._now()
        entry["负责人"] = target
        entry["负责人状态"] = OWNER_STATUS.get(target, "在职")
        entry["归属变更时间"] = now
        entry["归属变更原因"] = reason
        # 历史仍保留原负责人与原状态；未完成的待办随当前负责人转交，
        # 已归档的日志 pending 为 False，不会被重新激活。
        entry["历史"].append(self._history(
            action="变更负责人",
            from_status=entry.get("status"),
            to_status=entry.get("status"),
            operator=operator,
            entry=entry,
            note=f"{previous_owner} → {target}" + (f"，原因：{reason}" if reason else ""),
            extra={"fromOwner": previous_owner, "toOwner": target},
        ))
        return self._decorate(entry), f"审计日志负责人已由 {self.display_name(previous_owner)} 转交给 {self.display_name(target)}"

    def add_ignore_note(
        self,
        entry_id: int,
        note: str,
        *,
        operator: str,
    ) -> tuple[dict[str, Any] | None, str]:
        """归档记录只读，但原负责人可以补充一条忽略说明，只追加历史不改状态。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            raise AuditRuleError("not_found", f"审计日志 {entry_id} 不存在")
        self._decorate(entry)

        if entry.get("status") not in ARCHIVED_STATUSES:
            raise AuditRuleError("conflict", "只有已归档或已忽略的日志才能补充忽略说明")
        original = str(entry.get("原负责人"))
        if operator != original:
            raise AuditRuleError(
                "forbidden",
                f"只有原负责人 {self.display_name(original)} 可以补充忽略说明",
            )
        text = str(note or "").strip()
        if not text:
            raise AuditRuleError("bad_request", "忽略说明不能为空")

        existing = str(entry.get("忽略说明") or "").strip()
        entry["忽略说明"] = f"{existing}；{text}" if existing else text
        entry["历史"].append(self._history(
            action="补充忽略说明",
            from_status=entry.get("status"),
            to_status=entry.get("status"),
            operator=operator,
            entry=entry,
            note=text,
        ))
        return self._decorate(entry), "忽略说明已补充，归档日志状态保持不变"

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def _coerce_int(value: Any) -> int:
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M")

    def _history(
        self,
        *,
        action: str,
        from_status: str | None,
        to_status: str | None,
        operator: str,
        entry: dict[str, Any],
        note: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """历史统一记录人员、影响行数与处理时间，便于责任变更前后追溯。"""
        row: dict[str, Any] = {
            "action": action,
            "fromStatus": from_status,
            "toStatus": to_status,
            "operator": operator,
            "operatorName": self.display_name(operator),
            "originalOwner": entry.get("原负责人"),
            "owner": entry.get("负责人"),
            "at": self._now(),
            "impact": self._impact(entry),
            "note": note,
        }
        if extra:
            row.update(extra)
        return row
