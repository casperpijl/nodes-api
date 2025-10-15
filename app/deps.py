from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session


class IngestAuthed:
    """Represents an authenticated ingest request from n8n"""
    def __init__(self, org_id: str, token_name: str):
        self.org_id = org_id
        self.token_name = token_name


async def ingest_authed(req: Request, db: AsyncSession = Depends(get_session)) -> IngestAuthed:
    """
    Validates ingest token from Authorization header.
    Returns the org_id associated with the token.
    """
    auth_header = req.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header"
        )
    
    # Expect format: "Bearer sk_live_..."
    parts = auth_header.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format. Expected: Bearer <token>"
        )
    
    token = parts[1]
    
    # Look up token in database
    query = text("""
        SELECT org_id, name
        FROM ingest_tokens
        WHERE token = :token AND is_active = TRUE
    """)
    
    row = (await db.execute(query, {"token": token})).first()
    
    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive token"
        )
    
    org_id, token_name = row
    return IngestAuthed(str(org_id), token_name)
