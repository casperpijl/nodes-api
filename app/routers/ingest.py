from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from typing import Optional, Dict, Any, List
import json

from ..deps import ingest_authed, IngestAuthed
from ..db import get_session

router = APIRouter(prefix="/ingest", tags=["ingest"])


class WorkflowRunPayload(BaseModel):
    """Payload sent from n8n workflow to report execution data"""
    workflow_name: str = Field(..., description="Name of the workflow (e.g., 'Send Welcome Email')")
    status: str = Field(..., description="Execution status: 'success', 'failed', or 'running'")
    started_at: datetime = Field(..., description="When the workflow execution started (ISO 8601 timestamp)")
    ended_at: Optional[datetime] = Field(None, description="When the workflow execution ended (ISO 8601 timestamp)")
    error_message: Optional[str] = Field(None, description="Error message if status is 'failed'")
    external_run_id: Optional[str] = Field(None, description="n8n execution ID for reference")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Custom metadata (e.g., emails_sent, records_processed)")


class WorkflowRunResponse(BaseModel):
    """Response after successfully ingesting workflow run"""
    ok: bool
    workflow_run_id: int
    workflow_id: str
    message: str


@router.post("/workflow-run", response_model=WorkflowRunResponse)
async def ingest_workflow_run(
    payload: WorkflowRunPayload,
    auth: IngestAuthed = Depends(ingest_authed),
    db: AsyncSession = Depends(get_session)
):
    """
    Ingest workflow execution data from n8n.
    
    This endpoint:
    1. Validates the ingest token (automatically extracts org_id from token)
    2. Creates workflow record if it doesn't exist (based on workflow_name + org_id)
    3. Inserts workflow_run record with execution data
    4. Returns the created workflow_run_id
    
    Authentication: Bearer token in Authorization header
    """
    
    # Validate status
    valid_statuses = ["success", "failed", "running"]
    if payload.status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
        )
    
    # Step 1: Get or create workflow record
    # First, try to find existing workflow by name and org_id
    workflow_query = text("""
        SELECT id FROM workflows
        WHERE org_id = :org_id AND name = :name
        LIMIT 1
    """)
    
    workflow_row = (await db.execute(
        workflow_query,
        {"org_id": auth.org_id, "name": payload.workflow_name}
    )).first()
    
    if workflow_row:
        workflow_id = workflow_row[0]
    else:
        # Create new workflow record
        create_workflow_query = text("""
            INSERT INTO workflows (org_id, name, active)
            VALUES (:org_id, :name, TRUE)
            RETURNING id
        """)
        
        result = await db.execute(
            create_workflow_query,
            {"org_id": auth.org_id, "name": payload.workflow_name}
        )
        workflow_id = result.scalar_one()
        await db.commit()
    
    # Step 2: Calculate duration_ms if both timestamps provided
    duration_ms = None
    if payload.started_at and payload.ended_at:
        duration_ms = int((payload.ended_at - payload.started_at).total_seconds() * 1000)
    
    # Step 3: Insert workflow_run record
    insert_run_query = text("""
        INSERT INTO workflow_runs (
            org_id,
            workflow_id,
            started_at,
            ended_at,
            status,
            duration_ms,
            error_message,
            external_run_id,
            payload
        )
        VALUES (
            :org_id,
            :workflow_id,
            :started_at,
            :ended_at,
            :status,
            :duration_ms,
            :error_message,
            :external_run_id,
            :payload
        )
        RETURNING id
    """)
    
    # Prepare payload JSONB (metadata + original payload for reference)
    # Convert to JSON string for asyncpg
    payload_jsonb = json.dumps(payload.metadata or {})
    
    result = await db.execute(insert_run_query, {
        "org_id": auth.org_id,
        "workflow_id": workflow_id,
        "started_at": payload.started_at,
        "ended_at": payload.ended_at,
        "status": payload.status,
        "duration_ms": duration_ms,
        "error_message": payload.error_message,
        "external_run_id": payload.external_run_id,
        "payload": payload_jsonb
    })
    
    workflow_run_id = result.scalar_one()
    await db.commit()
    
    return WorkflowRunResponse(
        ok=True,
        workflow_run_id=workflow_run_id,
        workflow_id=str(workflow_id),
        message=f"Workflow run recorded successfully for '{payload.workflow_name}'"
    )


# ============================================================================
# APPROVAL INGEST
# ============================================================================

class ApprovalAssetPayload(BaseModel):
    """Asset (file) attached to an approval"""
    role: str = Field(..., description="Asset role: 'source_email_body_html', 'draft_order_confirmation_pdf', etc.")
    storage_provider: str = Field(default="minio", description="Storage provider: 'minio', 's3', 'external', 'local'")
    storage_key: Optional[str] = Field(None, description="MinIO/S3 key path (e.g., 'approvals/order-1042/confirm.pdf')")
    external_url: str = Field(..., description="Presigned URL from MinIO/S3 (24-48h expiry)")
    filename: Optional[str] = Field(None, description="Original filename")
    mime_type: Optional[str] = Field(None, description="MIME type (e.g., 'application/pdf')")
    size_bytes: Optional[int] = Field(None, description="File size in bytes")


class ApprovalIngestPayload(BaseModel):
    """Payload sent from n8n to create an approval"""
    type: str = Field(..., description="Approval type: 'order', 'linkedin_post', 'gmail_reply'")
    title: str = Field(..., description="Short title (e.g., 'Order BR-2025-1042')")
    preview: Dict[str, Any] = Field(..., description="UI preview data (free-form JSON)")
    data: Dict[str, Any] = Field(..., description="Execution payload (free-form JSON)")
    n8n_execute_webhook_url: str = Field(..., description="Full webhook URL to call on approval")
    assets: List[ApprovalAssetPayload] = Field(default_factory=list, description="List of assets")


class ApprovalIngestResponse(BaseModel):
    """Response after successfully creating an approval"""
    ok: bool
    approval_id: str
    message: str


@router.post("/approval", response_model=ApprovalIngestResponse)
async def ingest_approval(
    payload: ApprovalIngestPayload,
    auth: IngestAuthed = Depends(ingest_authed),
    db: AsyncSession = Depends(get_session)
):
    """
    Create a new approval with assets.
    
    This endpoint:
    1. Validates the ingest token (automatically extracts org_id from token)
    2. Validates the type is in allowed list
    3. Inserts approval record (status='pending')
    4. Inserts all assets (linked to approval_id)
    5. Logs 'created' event
    6. Returns approval_id
    
    Authentication: Bearer token in Authorization header
    
    Example payload for type='order':
    {
        "type": "order",
        "title": "Order BR-2025-1042",
        "preview": {
            "email_header": {
                "from": "customer@example.com",
                "to": "orders@company.com",
                "subject": "Bestelling BR-2025-1042",
                "date": "2025-10-23T09:41:00Z"
            },
            "badges": ["3 attachments", "€1,234.56"]
        },
        "data": {
            "order_ref": "BR-2025-1042",
            "customer_mail": {...},
            "internal_mail": {...},
            "source": {...}
        },
        "n8n_execute_webhook_url": "https://n8n.example.com/webhook/execute-order-BR-2025-1042",
        "assets": [...]
    }
    """
    
    # Validate type
    valid_types = ["order", "linkedin_post", "gmail_reply"]
    if payload.type not in valid_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid type. Must be one of: {', '.join(valid_types)}"
        )
    
    # Step 1: Insert approval record
    insert_approval_query = text("""
        INSERT INTO approvals (
            org_id,
            type,
            status,
            title,
            preview,
            data,
            n8n_execute_webhook_url
        )
        VALUES (
            :org_id,
            :type,
            'pending',
            :title,
            :preview,
            :data,
            :webhook_url
        )
        RETURNING id
    """)
    
    result = await db.execute(insert_approval_query, {
        "org_id": auth.org_id,
        "type": payload.type,
        "title": payload.title,
        "preview": json.dumps(payload.preview),
        "data": json.dumps(payload.data),
        "webhook_url": payload.n8n_execute_webhook_url
    })
    
    approval_id = result.scalar_one()
    
    # Step 2: Insert assets
    if payload.assets:
        insert_asset_query = text("""
            INSERT INTO approval_assets (
                approval_id,
                role,
                storage_provider,
                storage_key,
                external_url,
                filename,
                mime_type,
                size_bytes
            )
            VALUES (
                :approval_id,
                :role,
                :storage_provider,
                :storage_key,
                :external_url,
                :filename,
                :mime_type,
                :size_bytes
            )
        """)
        
        for asset in payload.assets:
            await db.execute(insert_asset_query, {
                "approval_id": approval_id,
                "role": asset.role,
                "storage_provider": asset.storage_provider,
                "storage_key": asset.storage_key,
                "external_url": asset.external_url,
                "filename": asset.filename,
                "mime_type": asset.mime_type,
                "size_bytes": asset.size_bytes
            })
    
    # Step 3: Log 'created' event
    insert_event_query = text("""
        INSERT INTO approval_events (approval_id, event, metadata)
        VALUES (:approval_id, 'created', :metadata)
    """)
    
    await db.execute(insert_event_query, {
        "approval_id": approval_id,
        "metadata": json.dumps({
            "type": payload.type,
            "asset_count": len(payload.assets),
            "token_name": auth.token_name
        })
    })
    
    await db.commit()
    
    return ApprovalIngestResponse(
        ok=True,
        approval_id=str(approval_id),
        message=f"Approval created successfully: {payload.title}"
    )

