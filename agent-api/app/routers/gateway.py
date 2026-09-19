"""Tool Gateway 审批接口（§11）：主对话敏感工具审批卡的出口。

`approval.required` 事件让前端弹出审批卡，用户放行/拒绝后打到这里；放行后同幂等键重试即执行。
原挂在 /workflow/gateway/* 下，工作流编排整体删除后原样搬到 /gateway/*（请求体/响应不变）。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.auth import UserContext, current_user
from app.services.gateway import tool_gateway

router = APIRouter(prefix="/gateway", tags=["gateway"])


class GatewayActionRequest(BaseModel):
    callId: str


@router.post("/approve")
async def gateway_approve(payload: GatewayActionRequest, user: UserContext = Depends(current_user)):
    return await tool_gateway.approve(payload.callId, user.user_id)


@router.post("/reject")
async def gateway_reject(payload: GatewayActionRequest, user: UserContext = Depends(current_user)):
    return await tool_gateway.reject(payload.callId, user.user_id)


@router.get("/calls")
async def gateway_calls(user: UserContext = Depends(current_user)):
    return await tool_gateway.list_calls(user.user_id)
