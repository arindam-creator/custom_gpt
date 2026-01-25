import uvicorn
import httpx
import contextvars
from fastapi import FastAPI, Request
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from pydantic import BaseModel, Field
from typing import Optional, Literal, Dict, Any
import mcp.types as types
from settings import DJANGO_BASE_URL, PORT, DJANGO_AUTH_TOKEN
from oauth import router as auth_router

# --- State Management ---
current_user_token: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_user_token", default=None)

# --- Helpers ---
async def fetch_from_django(endpoint: str, method: str = "GET", params: dict = None, body: dict = None) -> Dict[str, Any]:
    token = current_user_token.get() or DJANGO_AUTH_TOKEN
    
    if not token:
        return {"error": "Authentication required."}

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"{DJANGO_BASE_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    
    async with httpx.AsyncClient() as client:
        try:
            if method == "GET":
                resp = await client.get(url, headers=headers, params=params)
            else:
                resp = await client.post(url, headers=headers, json=body)
            
            if resp.status_code == 204: return {"success": True}
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

# --- App Setup ---
app = FastAPI(title="Django MCP Bridge")
app.include_router(auth_router, prefix="/oauth")

# --- Auth Middleware ---
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    auth = request.headers.get("Authorization", "")
    token_var = None
    if auth.startswith("Bearer "):
        token_var = current_user_token.set(auth.split(" ")[1])
    try:
        return await call_next(request)
    finally:
        if token_var: current_user_token.reset(token_var)

# --- MCP Server Setup ---
mcp_server = Server("Django-CRM-Bridge")

# --- LOGIC HANDLERS ---
async def logic_get_tasks(limit: int = 10, status: str = None):
    params = {"limit": limit}
    if status: params["status"] = status
    return await fetch_from_django("tasks/", params=params)

async def logic_create_task(title: str, priority: str = "medium"):
    return await fetch_from_django("tasks/", method="POST", body={"title": title, "priority": priority})

# [ADDED] Logic for Latest Tasks
async def logic_get_latest_tasks():
    return await fetch_from_django("tasks/latest/")

# [ADDED] Logic for Stats
async def logic_get_stats():
    return await fetch_from_django("tasks/statistics/")

# --- MCP TOOL REGISTRATION (The Low-Level Way) ---

# 1. Register the List of Tools
@mcp_server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_crm_tasks",
            description="Fetch all tasks from the CRM with filtering.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 10},
                    "status": {"type": "string", "enum": ["to-do", "in_progress", "completed"]}
                }
            }
        ),
        types.Tool(
            name="create_crm_task",
            description="Create a new task in the CRM.",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high"]}
                },
                "required": ["title"]
            }
        ),
        # [ADDED] Tool for Latest Tasks
        types.Tool(
            name="get_latest_tasks",
            description="Fetch the most recent tasks from the CRM.",
            inputSchema={
                "type": "object",
                "properties": {} # No inputs needed
            }
        ),
        # [ADDED] Tool for Stats
        types.Tool(
            name="get_crm_stats",
            description="Get CRM task statistics (counts by status, priority, etc).",
            inputSchema={
                "type": "object",
                "properties": {} # No inputs needed
            }
        )
    ]

# 2. Register the Tool Execution Logic
@mcp_server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    if arguments is None:
        arguments = {}

    try:
        if name == "get_crm_tasks":
            result = await logic_get_tasks(
                limit=arguments.get("limit", 10),
                status=arguments.get("status")
            )
            return [types.TextContent(type="text", text=str(result))]
        
        elif name == "create_crm_task":
            result = await logic_create_task(
                title=arguments.get("title"),
                priority=arguments.get("priority", "medium")
            )
            return [types.TextContent(type="text", text=str(result))]

        # [ADDED] Handling for Latest Tasks
        elif name == "get_latest_tasks":
            result = await logic_get_latest_tasks()
            return [types.TextContent(type="text", text=str(result))]

        # [ADDED] Handling for Stats
        elif name == "get_crm_stats":
            result = await logic_get_stats()
            return [types.TextContent(type="text", text=str(result))]
            
        else:
            raise ValueError(f"Unknown tool: {name}")

    except Exception as e:
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]

# --- REST ENDPOINTS (For ChatGPT) ---
@app.get("/")
async def health_check():
    return {"status": "ok", "message": "Django MCP Bridge is running!"}

@app.get("/tasks/all")
async def api_get_tasks(limit: int = 10, status: Optional[str] = None):
    return await logic_get_tasks(limit, status)

# [ADDED] Endpoint for Latest Tasks
@app.get("/tasks/latest")
async def api_get_latest_tasks():
    return await logic_get_latest_tasks()

# [ADDED] Endpoint for Stats
@app.get("/stats")
async def api_get_stats():
    return await logic_get_stats()

class CreateTaskModel(BaseModel):
    title: str
    priority: Optional[str] = "medium"

@app.post("/tasks/create")
async def api_create_task(task: CreateTaskModel):
    return await logic_create_task(task.title, task.priority)

class UpdateTaskInput(BaseModel):
    title: Optional[str] = Field(None)
    task_type: Optional[Literal['to-do', 'email', 'whatsapp', 'call']] = Field(None)
    priority: Optional[Literal['none', 'low', 'medium', 'high']] = Field(None)
    status: Optional[Literal['in_progress', 'to-do', 'completed']] = Field(None)
    due_date: Optional[str] = Field(None)
    notes: Optional[str] = Field(None)

@app.patch("/tasks/{task_id}", operation_id="updateTask")
async def gpt_update_task(task_id: int, task: UpdateTaskInput):
    payload = task.model_dump(exclude_none=True)
    if not payload:
        return {"error": "No fields provided to update."}
    return await fetch_from_django(f"tasks/{task_id}/", method="PATCH", body=payload)

# --- MCP SSE Transport ---
sse = SseServerTransport("/messages")

@app.get("/sse")
async def handle_sse(request: Request):
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await mcp_server.run(streams[0], streams[1], mcp_server.create_initialization_options())

@app.post("/messages")
async def handle_messages(request: Request):
    await sse.handle_post_message(request.scope, request.receive, request._send)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)