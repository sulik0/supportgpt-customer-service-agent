from unittest.mock import AsyncMock

import pytest
from src.agents.analyzer import ticket_analyzer_agent
from src.agents.resolver import resolution_agent

INVOICE = '请查询订单 ORD-12003 的付款记录和发票进度，告诉我是否已付款、发票是否已开好。'
WARRANTY = '订单 ORD-12002 的网关设备出现故障，请查询保修权益，并说明维修前还需要确认什么。'

@pytest.mark.parametrize('question,intent', [
    (INVOICE, 'billing_dispute'), (WARRANTY, 'warranty_claim'),
    ('保修期限一般有多久？', 'information_request'),
    ('What is the hardware warranty period?', 'information_request'),
    ('Check warranty coverage for my device ORD-12002.', 'warranty_claim'),
])
def test_concrete_queries_and_policy_are_distinct(question, intent):
    assert ticket_analyzer_agent._match_rule(question)['intent'] == intent


def test_explicit_shipping_and_billing_remain_ambiguous():
    assert ticket_analyzer_agent._match_rule('查询订单物流和发票记录') is None

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

@pytest.mark.asyncio
@pytest.mark.parametrize('question,intent', [(INVOICE, 'billing_dispute'), (WARRANTY, 'warranty_claim')])
async def test_analyzer_does_not_request_ambiguous_llm_classification(monkeypatch, question, intent):
    analyze = AsyncMock(side_effect=ValueError('invalid classifier JSON'))
    monkeypatch.setattr('src.agents.analyzer.llm_provider.analyze_ticket', analyze)
    result = await ticket_analyzer_agent.analyze({'description':question, 'subject':question})
    analyze.assert_not_awaited()
    assert result['intent'] == intent
    assert result['analyzer_strategy'] == 'rule'
    assert not result['errors']
