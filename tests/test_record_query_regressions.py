from unittest.mock import AsyncMock

import pytest
from src.agents.resolver import resolution_agent

INVOICE = '请查询订单 ORD-12003 的付款记录和发票进度，告诉我是否已付款、发票是否已开好。'
WARRANTY = '订单 ORD-12002 的网关设备出现故障，请查询保修权益，并说明维修前还需要确认什么。'




@pytest.mark.asyncio
@pytest.mark.parametrize('service', [
    {},
    {'tool':'warranty.get_entitlements','status':'error','data':None},
    {'tool':'billing.get_payment_invoices','status':'success','data':{'status':'not_found','records':[]}},
])
async def test_missing_or_wrong_lookup_cannot_generate_not_found(monkeypatch, service):
    generate = AsyncMock(return_value=('未查到保修记录', 0, 0))
    monkeypatch.setattr('src.agents.resolver.llm_provider.generate_resolution', generate)
    result = await resolution_agent.resolve({'description':WARRANTY, 'tool_context':{'service_query':service}})
    generate.assert_not_awaited()
    assert '尚未成功查询' in result['suggested_response']
    assert '未查到' not in result['suggested_response']
    assert result['degradation_level'] == 'human_required'

@pytest.mark.asyncio
@pytest.mark.parametrize('status,records', [('found',[{'coverage':'hardware_warranty'}]), ('not_found',[])])
async def test_successful_lookup_including_empty_result_reaches_resolver(monkeypatch,status,records):
    generate = AsyncMock(return_value=('经过查询确认的回复', 1, 1))
    monkeypatch.setattr('src.agents.resolver.llm_provider.generate_resolution',generate)
    result = await resolution_agent.resolve({'description':WARRANTY,'tool_context':{'service_query':{
        'tool':'warranty.get_entitlements','status':'success','data':{'status':status,'records':records}}}})
    generate.assert_awaited_once()
    assert result['suggested_response'] == '经过查询确认的回复'

