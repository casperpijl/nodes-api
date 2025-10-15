from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from typing import Optional, Dict, Any
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
