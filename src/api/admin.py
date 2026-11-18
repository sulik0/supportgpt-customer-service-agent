"""管理员资源：Tool 开关、Prompt、RAG 文档。"""

import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.auth.rbac import require_admin
from src.config import settings
from src.database import get_db
from src.models.db_models import ToolRuntimeSetting, User
from src.models.schemas import (
    AdminKnowledgeDocumentRequest,
    AdminKnowledgeDocumentResponse,
    AdminPromptBundleRequest,
    AdminPromptBundleResponse,
    AdminPromptRegistryResponse,
    AdminRagReindexResponse,
    AdminToolResponse,
    AdminToolUpdateRequest,
)
from src.promptops.registry import PromptRegistry
from src.rag.kb_versioning import kb_versioning_service
from src.tools.registry import tool_registry

router = APIRouter()


# --- ADMIN RESOURCE MANAGEMENT ---
async def _admin_tool_catalog(db: AsyncSession) -> list[AdminToolResponse]:
    """合并代码注册表与数据库运行开关。"""
    result = await db.execute(select(ToolRuntimeSetting))
    settings_by_name = {item.tool_name: item for item in result.scalars().all()}
    catalog: list[AdminToolResponse] = []
    for item in tool_registry.list_tools():
        runtime = settings_by_name.get(item["name"])
        catalog.append(
            AdminToolResponse(
                **item,
                disabled_reason=(
                    runtime.reason if runtime and not runtime.enabled else None
                ),
                updated_by=runtime.updated_by if runtime else None,
                updated_at=runtime.updated_at if runtime else None,
            )
        )
    return catalog


@router.get("/admin/resources/tools", response_model=list[AdminToolResponse])
async def list_admin_tools(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """仅管理员可查看 Tool 定义和当前启停状态。"""
    return await _admin_tool_catalog(db)


@router.put("/admin/resources/tools/{tool_name}", response_model=AdminToolResponse)
async def update_admin_tool(
    tool_name: str,
    req: AdminToolUpdateRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """持久化 Tool 开关，所有 Schema、RBAC 和高风险门禁保持不变。"""
    if tool_registry.get_definition(tool_name) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found"
        )
    reason = (req.reason or "").strip() or None
    if not req.enabled and reason is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Disabling a Tool requires a reason.",
        )
    runtime = await db.get(ToolRuntimeSetting, tool_name)
    if runtime is None:
        runtime = ToolRuntimeSetting(
            tool_name=tool_name, updated_by=current_user.username
        )
        db.add(runtime)
    runtime.enabled = req.enabled
    runtime.reason = reason
    runtime.updated_by = current_user.username
    runtime.updated_at = datetime.datetime.utcnow()
    await db.commit()
    tool_registry.set_enabled(tool_name, req.enabled)
    catalog = await _admin_tool_catalog(db)
    return next(item for item in catalog if item.name == tool_name)


def _prompt_bundle_response(bundle) -> AdminPromptBundleResponse:
    """将 Prompt Bundle 转换为管理视图。"""
    return AdminPromptBundleResponse(
        **bundle.metadata(),
        payload=bundle.payload(),
    )


@router.get("/admin/resources/prompts", response_model=AdminPromptRegistryResponse)
async def list_admin_prompts(current_user: User = Depends(require_admin)):
    """查看全部不可变 Bundle 及生产/预发布指针。"""
    registry = PromptRegistry(Path(settings.PROMPT_REGISTRY_DIR))
    return AdminPromptRegistryResponse(
        state=registry.state(),
        effective={
            environment: _prompt_bundle_response(registry.resolve(environment))
            for environment in ("staging", "production")
        },
        bundles=[AdminPromptBundleResponse(**item) for item in registry.list_bundles()],
    )


@router.post(
    "/admin/resources/prompts",
    response_model=AdminPromptBundleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_admin_prompt_candidate(
    req: AdminPromptBundleRequest,
    current_user: User = Depends(require_admin),
):
    """只创建候选快照；生产晋级仍必须通过 EvalOps 质量门禁。"""
    registry = PromptRegistry(Path(settings.PROMPT_REGISTRY_DIR))
    try:
        bundle = registry.register(req.model_dump())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return _prompt_bundle_response(bundle)


def _knowledge_document_response(document) -> AdminKnowledgeDocumentResponse:
    """将 KnowledgeDoc ORM 对象转换为对外模型。"""
    return AdminKnowledgeDocumentResponse(
        id=document.id,
        title=document.title,
        content=document.content,
        version=document.version,
        category=document.category,
        metadata=document.metadata_json or {},
        created_at=document.created_at,
    )


@router.get(
    "/admin/resources/rag-documents",
    response_model=list[AdminKnowledgeDocumentResponse],
)
async def list_admin_rag_documents(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """列出 SQL 中的全部权威知识文档。"""
    documents = await kb_versioning_service.list_documents(db)
    return [_knowledge_document_response(item) for item in documents]


@router.post(
    "/admin/resources/rag-documents",
    response_model=AdminKnowledgeDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_admin_rag_document(
    req: AdminKnowledgeDocumentRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """新建或幂等更新文档，同步重建它的向量 Chunk。"""
    document = await kb_versioning_service.register_document(
        db,
        doc_id=req.id,
        title=req.title,
        content=req.content,
        category=req.category,
        version=req.version,
        metadata=req.metadata,
    )
    return _knowledge_document_response(document)


@router.put(
    "/admin/resources/rag-documents/{doc_id}",
    response_model=AdminKnowledgeDocumentResponse,
)
async def update_admin_rag_document(
    doc_id: str,
    req: AdminKnowledgeDocumentRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """更新文档时严格保持 Path 与 Payload 标识一致。"""
    if doc_id != req.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document ID in path and payload must match.",
        )
    if await kb_versioning_service.get_document(db, doc_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge document not found.",
        )
    document = await kb_versioning_service.register_document(
        db,
        doc_id=req.id,
        title=req.title,
        content=req.content,
        category=req.category,
        version=req.version,
        metadata=req.metadata,
    )
    return _knowledge_document_response(document)


@router.delete("/admin/resources/rag-documents/{doc_id}", status_code=204)
async def delete_admin_rag_document(
    doc_id: str,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """删除文档及对应向量索引。"""
    deleted = await kb_versioning_service.delete_document(db, doc_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge document not found.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/admin/resources/rag-documents/reindex",
    response_model=AdminRagReindexResponse,
)
async def reindex_admin_rag_documents(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """从 SQL 权威文档全量刷新 ChromaDB 索引。"""
    count = await kb_versioning_service.reindex_all(db)
    return AdminRagReindexResponse(indexed_documents=count)
