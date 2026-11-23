import sys
from unittest.mock import MagicMock, AsyncMock

# Inject mock modules before any imports to prevent real dependency loading issues
mock_ragas = MagicMock()
mock_ragas.evaluate = MagicMock(
    return_value={
        "faithfulness": 0.95,
        "answer_relevance": 0.90,
        "context_precision": 0.88,
        "context_recall": 0.85,
    }
)
sys.modules["ragas"] = mock_ragas
sys.modules["ragas.metrics"] = MagicMock()
sys.modules["datasets"] = MagicMock()

mock_deepeval = MagicMock()
sys.modules["deepeval"] = mock_deepeval
sys.modules["deepeval.test_case"] = MagicMock()
sys.modules["deepeval.metrics"] = MagicMock()

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.rbac import RoleChecker
from src.models.db_models import User, Ticket
from src.database import init_db, get_db
from src.rag.ingestion import (
    extract_text_from_file,
    extract_pdf_text,
    extract_docx_text,
)
from src.approval.workflows import human_it_loop_service
from src.models.schemas import ResponseApprovalRequest
from src.observability.tracing import init_tracing


# --- INGESTION MOCKING ---
def test_pdf_docx_parsers(mocker):
    # Mock PDF extraction
    mock_pdf_reader = MagicMock()
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "Mocked PDF text"
    mock_pdf_reader.pages = [mock_page]
    mocker.patch("PyPDF2.PdfReader", return_value=mock_pdf_reader)

    mocker.patch("builtins.open", mocker.mock_open(read_data=b"dummybytes"))
    extracted_pdf = extract_pdf_text("dummy.pdf")
    assert extracted_pdf == "Mocked PDF text"

    # Mock DOCX extraction
    mock_doc = MagicMock()
    mock_para = MagicMock()
    mock_para.text = "Mocked DOCX paragraph"
    mock_doc.paragraphs = [mock_para]
    mocker.patch("docx.Document", return_value=mock_doc)
    extracted_docx = extract_docx_text("dummy.docx")
    assert extracted_docx == "Mocked DOCX paragraph"


# --- TRACING COVERAGE ---
def test_tracing_initialization():
    from opentelemetry import metrics, trace

    init_tracing()
    assert trace.get_tracer_provider() is not None
    assert metrics.get_meter_provider() is not None


# --- EVALUATION MOCKING ---
@pytest.mark.asyncio
async def test_ragas_deepeval_adapters_try_blocks(mocker):
    from src.evaluation.ragas_eval import ragas_evaluator
    from src.evaluation.deepeval_eval import deepeval_evaluator

    # Set up DeepEval metrics return mocks
    mock_metric_inst = MagicMock()
    mock_metric_inst.score = 0.1
    mock_metric_inst.is_successful.return_value = True

    sys.modules["deepeval.metrics"].HallucinationMetric.return_value = mock_metric_inst
    sys.modules["deepeval.metrics"].AnswerRelevancyMetric.return_value = (
        mock_metric_inst
    )

    # Force api_key properties directly to bypass getenv cache check
    ragas_evaluator.api_key = "real-key-123"
    deepeval_evaluator.api_key = "real-key-123"
    sys.modules["datasets"].Dataset.from_dict.return_value = MagicMock()

    try:
        res_ragas = await ragas_evaluator.run_evaluation(
            "query", ["context"], "response"
        )
        assert res_ragas["faithfulness"] == 0.95

        res_deep = await deepeval_evaluator.run_evaluation(
            "query", ["context"], "response"
        )
        assert res_deep["hallucination_score"] == 0.1
    finally:
        # Reset keys to avoid side effects in other test suites
        ragas_evaluator.api_key = None
        deepeval_evaluator.api_key = None


# --- LLM PROVIDER & EMBEDDING MOCKING ---
@pytest.mark.asyncio
async def test_providers_mocking(mocker):
    from src.llm.provider import (
        OpenAILLMProvider,
        AzureOpenAILLMProvider,
        MockLLMProvider,
    )
    from src.rag.embedding import OpenAIEmbeddingProvider

    # 1. Mock completions object
    mock_chat_completion = MagicMock()
    mock_choice = MagicMock()
    mock_message = MagicMock()
    mock_message.content = '{"sentiment": "positive", "priority": "low", "department": "general", "intent": "greet", "detected_emotions": ["calm"], "confidence_score": 0.99}'
    mock_choice.message = mock_message
    mock_chat_completion.choices = [mock_choice]

    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 100
    mock_usage.completion_tokens = 50
    mock_chat_completion.usage = mock_usage

    mock_async_client = MagicMock()
    mock_async_client.chat = MagicMock()
    mock_async_client.chat.completions = MagicMock()
    mock_async_client.chat.completions.create = AsyncMock(
        return_value=mock_chat_completion
    )

    # Mock OpenAI-compatible client construction
    openai_client = mocker.patch("openai.AsyncOpenAI", return_value=mock_async_client)
    mocker.patch("openai.AsyncAzureOpenAI", return_value=mock_async_client)

    # Test OpenAI-compatible LLM provider methods
    mocker.patch("src.llm.provider.settings.LLM_API_KEY", "compatible-api-key")
    mocker.patch("src.llm.provider.settings.LLM_BASE_URL", "https://llm.example.com/v1")
    mocker.patch("src.llm.provider.settings.LLM_MODEL_NAME", "compatible-chat-model")
    op_provider = OpenAILLMProvider()
    openai_client.assert_called_once_with(
        api_key="compatible-api-key",
        base_url="https://llm.example.com/v1",
        max_retries=0,
    )
    analysis, _, _ = await op_provider.classify_message("Hello")
    assert set(analysis) >= {"support_scope", "request", "intent"}
    await op_provider.generate_resolution("subject", "desc", "context")
    await op_provider.evaluate_qa("query", ["context"], "response")
    await op_provider.run_chat([{"role": "user", "content": "hi"}], "context")
    assert all(
        call.kwargs["model"] == "compatible-chat-model"
        for call in mock_async_client.chat.completions.create.await_args_list
    )

    # Test Azure LLM provider methods
    az_provider = AzureOpenAILLMProvider()
    az_analysis, _, _ = await az_provider.classify_message("Hello")
    assert set(az_analysis) >= {"support_scope", "request", "intent"}
    await az_provider.generate_resolution("subject", "desc", "context")
    await az_provider.evaluate_qa("query", ["context"], "response")
    await az_provider.run_chat([{"role": "user", "content": "hi"}], "context")

    # Test Mock LLM provider run_chat method (for coverage)
    mock_provider = MockLLMProvider()
    chat_res, _, _ = await mock_provider.run_chat(
        [{"role": "user", "content": "hi"}], "context"
    )
    assert "automated response" in chat_res.lower()

    # 2. Mock OpenAI Embeddings
    mock_emb_data = MagicMock()
    mock_emb_data.embedding = [0.1] * 1536
    mock_embeddings = MagicMock()
    mock_embeddings.data = [mock_emb_data, mock_emb_data]
    mock_async_client.embeddings = MagicMock()
    mock_async_client.embeddings.create = AsyncMock(return_value=mock_embeddings)

    emb_provider = OpenAIEmbeddingProvider()
    emb = await emb_provider.get_embedding("text")
    assert len(emb) == 1536
    embs = await emb_provider.get_embeddings(["text1", "text2"])
    assert len(embs) == 2


# --- DATABASE DEPENDENCY SCOPE ---
@pytest.mark.asyncio
async def test_database_get_db_yield():
    db_generator = get_db()
    # Pull database session from generator yield
    db = await anext(db_generator)
    assert isinstance(db, AsyncSession)
    try:
        # Complete generator loop
        await db_generator.asend(None)
    except StopAsyncIteration:
        pass


# --- APPROVAL WORKFLOWS DATABASE LOGS ---
@pytest.mark.asyncio
async def test_approval_workflows_database_commits(db_session: AsyncSession):
    # 1. Create a dummy ticket
    ticket = Ticket(
        customer_id="cust_999",
        subject="Workflow check",
        description="issue desc",
        status="open",
    )
    db_session.add(ticket)
    await db_session.commit()
    await db_session.refresh(ticket)

    # 2. Create pending approval
    appr = await human_it_loop_service.create_pending_approval(
        db_session, ticket.id, "AI proposed answer"
    )
    assert appr.id is not None
    assert appr.status == "pending"

    # 3. Retrieve list
    pending_list = await human_it_loop_service.get_pending_approvals(db_session)
    assert len(pending_list) > 0
    assert any(item.id == appr.id for item in pending_list)

    # 4. Process agent approval (success flow)
    req = ResponseApprovalRequest(approval_id=appr.id, status="approved")
    processed = await human_it_loop_service.process_agent_approval(
        db=db_session, approval_id=appr.id, agent_id=1, req=req
    )
    assert processed.status == "approved"
    assert processed.modified_response == "AI proposed answer"

    # Test error processing non-pending approval
    with pytest.raises(Exception):
        await human_it_loop_service.process_agent_approval(
            db=db_session, approval_id=appr.id, agent_id=1, req=req
        )


# --- FASTAPI REMAINING ROUTES ---
@pytest.mark.asyncio
async def test_remaining_fastapi_routes(
    client: AsyncClient, agent_headers: dict[str, str]
):
    # 1. Create a ticket to register ID 1
    ticket_payload = {
        "customer_id": "cust_101",
        "subject": "Slow performance",
        "description": "System is laggy",
    }
    create_res = await client.post(
        "/tickets", json=ticket_payload, headers=agent_headers
    )
    assert create_res.status_code == 201
    ticket_id = create_res.json()["id"]

    # 2. Get list of tickets
    list_res = await client.get("/tickets", headers=agent_headers)
    assert list_res.status_code == 200
    assert len(list_res.json()) > 0

    # 3. 已删除的旧接口不再提供
    for path in (
        "/summarize-ticket",
        "/suggest-response",
        "/analyze-sentiment",
        "/recommend-escalation",
    ):
        res = await client.post(
            path, json={"ticket_id": ticket_id, "kb_version": "v1"}, headers=agent_headers
        )
        assert res.status_code == 404
