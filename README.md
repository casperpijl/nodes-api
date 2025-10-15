# Node Ingestion API

This is the **Node Ingestion API** - a lightweight, focused API specifically for n8n custom nodes to send workflow execution data.

## Endpoints

### Health Check

- `GET /health` - Health check endpoint

### Ingestion

- `POST /ingest/workflow-run` - Ingest workflow execution data from n8n nodes

## Features

- **Lightweight**: Only includes the minimal dependencies needed for ingestion
- **Token-based authentication**: Uses Bearer tokens stored in the `ingest_tokens` table
- **Auto-creates workflows**: If a workflow doesn't exist, it's automatically created
- **Secure**: Validates tokens against the database before accepting data

## Authentication

This API uses Bearer token authentication. n8n custom nodes must include the token in the Authorization header:

```
Authorization: Bearer sk_live_your_token_here
```

Tokens are managed through the Dashboard API's admin interface and stored in the `ingest_tokens` table.

## Payload Format

```json
{
  "workflow_name": "Send Welcome Email",
  "status": "success",
  "started_at": "2024-01-15T10:30:00Z",
  "ended_at": "2024-01-15T10:30:05Z",
  "error_message": null,
  "external_run_id": "n8n-exec-123",
  "metadata": {
    "emails_sent": 5,
    "records_processed": 100
  }
}
```

### Fields

- `workflow_name` (required): Name of the workflow
- `status` (required): One of `"success"`, `"failed"`, or `"running"`
- `started_at` (required): ISO 8601 timestamp
- `ended_at` (optional): ISO 8601 timestamp
- `error_message` (optional): Error details if status is "failed"
- `external_run_id` (optional): n8n execution ID for reference
- `metadata` (optional): Custom key-value pairs with execution metrics

## Environment Variables

```bash
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/dbname
CORS_ORIGIN=*  # Or specific origins like https://n8n.yourdomain.com
```

## Local Development

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Create a `.env` file with the required environment variables

3. Run the API:

```bash
uvicorn app.main:app --reload --port 8001
```

## Docker Deployment

```bash
docker build -t node-api .
docker run -p 8001:8000 --env-file .env node-api
```

## CapRover Deployment

This API is configured for CapRover deployment. Set the environment variables in CapRover:

- DATABASE_URL
- CORS_ORIGIN (usually set to "\*" for n8n integrations)

The `captain-definition` file is already configured.

## Why a Separate API?

Separating the ingestion API from the dashboard API provides several benefits:

1. **Scalability**: Can scale the ingestion API independently based on n8n traffic
2. **Security**: Different CORS policies and no cookie-based auth to worry about
3. **Simplicity**: Minimal dependencies = faster startup and smaller container
4. **Reliability**: Issues with dashboard don't affect data collection
5. **Performance**: Lightweight API optimized for high-throughput ingestion
