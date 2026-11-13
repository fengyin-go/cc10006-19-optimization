"""操作审计接口：维护审计日志，覆盖提交审核、归档日志、忽略日志等动作。

身份通过请求头 X-Operator（以及可选的 X-Role）传入。这里只做 HTTP 层映射，
权限、归属与状态判断全部在 AuditService 里完成。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.audit import AuditRuleError, AuditService

router = APIRouter(prefix="/api/audit", tags=["操作审计"])

service = AuditService()

LIST_FIELDS = ["日志编号", "操作类型", "操作对象", "操作详情", "操作来源", "执行时间", "影响行数", "回滚能力"]
STATUSES = ["草稿", "待审核", "已归档", "已忽略"]

ERROR_STATUS = {
    "unauthenticated": 401,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "bad_request": 400,
}


def _raise_rule(error: AuditRuleError) -> None:
    raise HTTPException(
        status_code=ERROR_STATUS.get(error.code, 400),
        detail=error.message,
    )


def _operator(operator: str | None) -> str:
    try:
        return service.resolve_operator(operator)
    except AuditRuleError as error:
        _raise_rule(error)
        raise  # pragma: no cover - _raise_rule 必然抛出


# 注意：/export、/todos、/stats 必须声明在 /{entry_id} 之前，
# 否则会被路径参数抢先匹配成 entry_id 并返回 422。
@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出操作审计清单：返回全量数据，包含原负责人与当前负责人。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "audit", "total": total, "items": items}


@router.get("/stats", response_model=dict)
def audit_stats() -> dict[str, Any]:
    """审计统计：待处理量、质量告警、待处理影响行数、已转交数量实时汇总。"""
    return service.stats()


@router.get("/todos", response_model=dict)
def list_todos(
    x_operator: str | None = Header(default=None, alias="X-Operator"),
) -> dict[str, Any]:
    """当前负责人的待办列表；人员调整后只返回转交过来的未完成日志。"""
    operator = _operator(x_operator)
    items = service.list_todos(operator)
    return {
        "operator": operator,
        "operatorName": service.display_name(operator),
        "items": items,
        "total": len(items),
    }


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按日志编号检索"),
    status: str | None = Query(default=None, description="草稿、待审核、已归档、已忽略"),
    owner: str | None = Query(default=None, description="按当前负责人筛选"),
    pending: bool | None = Query(default=None, description="只看待办或只看已完成"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按日志编号、状态、负责人过滤操作审计列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword,
        status=status,
        owner=owner,
        pending=pending,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条审计日志明细；归档记录同样可读，只是不可修改。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"审计日志 {entry_id} 不存在")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(
    payload: EntryPayload,
    x_operator: str | None = Header(default=None, alias="X-Operator"),
    x_role: str | None = Header(default=None, alias="X-Role"),
) -> ActionResult:
    """登记一条审计日志；操作来源默认取当前操作人，并成为原负责人。"""
    operator = _operator(x_operator)
    try:
        entry, missing = service.create_entry(payload.values, operator=operator, role=x_role)
    except AuditRuleError as error:
        _raise_rule(error)
        raise  # pragma: no cover
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="审计日志已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(
    entry_id: int,
    payload: EntryPayload,
    x_operator: str | None = Header(default=None, alias="X-Operator"),
    x_role: str | None = Header(default=None, alias="X-Role"),
) -> ActionResult:
    """执行提交审核、归档、忽略或回滚；越权与非法流转会被拒绝并说明原因。"""
    operator = _operator(x_operator)
    action = str(payload.values.get("action") or "").strip()
    try:
        entry, message = service.run_action(entry_id, action, operator=operator, role=x_role)
    except AuditRuleError as error:
        _raise_rule(error)
        raise  # pragma: no cover
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/transfer", response_model=ActionResult)
def transfer_owner(
    entry_id: int,
    payload: EntryPayload,
    x_operator: str | None = Header(default=None, alias="X-Operator"),
    x_role: str | None = Header(default=None, alias="X-Role"),
) -> ActionResult:
    """人员调整时转交负责人：历史仍归原负责人，未完成待办转给新负责人。"""
    operator = _operator(x_operator)
    new_owner = str(payload.values.get("owner") or payload.values.get("负责人") or "").strip()
    reason = payload.values.get("reason") or payload.remark
    try:
        entry, message = service.transfer_owner(
            entry_id,
            new_owner,
            operator=operator,
            role=x_role,
            reason=str(reason) if reason else None,
        )
    except AuditRuleError as error:
        _raise_rule(error)
        raise  # pragma: no cover
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/ignore-note", response_model=ActionResult)
def add_ignore_note(
    entry_id: int,
    payload: EntryPayload,
    x_operator: str | None = Header(default=None, alias="X-Operator"),
) -> ActionResult:
    """归档记录只读；仅原负责人可补充忽略说明，状态与影响行数保持不变。"""
    operator = _operator(x_operator)
    note = str(payload.values.get("note") or payload.remark or "").strip()
    try:
        entry, message = service.add_ignore_note(entry_id, note, operator=operator)
    except AuditRuleError as error:
        _raise_rule(error)
        raise  # pragma: no cover
    return ActionResult(ok=True, message=message, entry=entry)
