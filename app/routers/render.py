from fastapi import APIRouter, Depends, Response, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional, Literal

from ..deps import ingest_authed, IngestAuthed

router = APIRouter(prefix="/render", tags=["render"])


PdfFormat = Literal[
    "Letter",
    "Legal",
    "Tabloid",
    "Ledger",
    "A0",
    "A1",
    "A2",
    "A3",
    "A4",
    "A5",
    "A6",
]


class PdfOptions(BaseModel):
    format: PdfFormat = Field(default="A4")
    landscape: bool = Field(default=False)
    printBackground: bool = Field(default=True)
    marginTop: str = Field(default="10mm")
    marginRight: str = Field(default="10mm")
    marginBottom: str = Field(default="10mm")
    marginLeft: str = Field(default="10mm")
    # Accept puppeteer-style and playwright-style values, normalize later
    waitUntil: Literal["load", "domcontentloaded", "networkidle0", "networkidle2", "networkidle", "commit"] = Field(default="networkidle0")
    fileName: Optional[str] = Field(default="document.pdf")


class HtmlRenderRequest(BaseModel):
    html: str = Field(..., description="HTML content to render")
    options: PdfOptions = Field(default_factory=PdfOptions)


@router.post("/pdf")
async def render_pdf(
    body: HtmlRenderRequest,
    auth: IngestAuthed = Depends(ingest_authed),
    return_: Optional[str] = None,
):
    try:
        # Lazy import to avoid import-time overhead
        from playwright.async_api import async_playwright
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Rendering engine not available: {e}",
        )

    # Map options
    fmt = body.options.format
    landscape = body.options.landscape
    print_bg = body.options.printBackground
    margins = {
        "top": body.options.marginTop,
        "right": body.options.marginRight,
        "bottom": body.options.marginBottom,
        "left": body.options.marginLeft,
    }
    # Normalize waitUntil for Playwright: `networkidle0/2` -> `networkidle`
    wait_until_in = body.options.waitUntil
    wait_until = "networkidle" if wait_until_in in ("networkidle0", "networkidle2", "networkidle") else wait_until_in

    # Render using Playwright
    pdf_bytes: bytes
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])  # type: ignore
            context = await browser.new_context()
            page = await context.new_page()
            await page.set_content(body.html, wait_until=wait_until)  # type: ignore
            pdf_bytes = await page.pdf(format=fmt, landscape=landscape, print_background=print_bg, margin=margins)  # type: ignore
        finally:
            try:
                await context.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass

    if return_ == "base64":
        import base64
        b64 = base64.b64encode(pdf_bytes).decode("ascii")
        return {
            "ok": True,
            "fileName": body.options.fileName or "document.pdf",
            "mimeType": "application/pdf",
            "data": b64,
            "size": len(pdf_bytes),
        }
    else:
        headers = {
            "Content-Type": "application/pdf",
            "Content-Disposition": f"inline; filename={body.options.fileName or 'document.pdf'}",
        }
        return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)
