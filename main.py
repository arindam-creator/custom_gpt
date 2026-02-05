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
            # Updated to support PATCH and PUT dynamically
            if method.upper() == "GET":
                resp = await client.get(url, headers=headers, params=params, timeout=10.0)
            elif method.upper() == "POST":
                resp = await client.post(url, headers=headers, json=body, timeout=10.0)
            elif method.upper() == "PATCH":
                resp = await client.patch(url, headers=headers, json=body, timeout=10.0)
            elif method.upper() == "PUT":
                resp = await client.put(url, headers=headers, json=body, timeout=10.0)
            
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

class ContactInput(BaseModel):
    name: str
    last_name: Optional[str] = None
    email: str
    title: Optional[str] = None
    mobile_phone: Optional[str] = None
    seniority: Optional[str] = None
    departments: Optional[str] = None
    country: Optional[str] = None

class UpdateContactInput(BaseModel):
    name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    title: Optional[str] = None
    mobile_phone: Optional[str] = None
    seniority: Optional[str] = None
    departments: Optional[str] = None

# --- LOGIC HANDLERS ---
async def logic_get_contacts(search: str = None, limit: int = 10):
    params = {"limit": limit}
    if search: params["search"] = search
    return await fetch_from_django("contacts/", params=params)

async def logic_create_contact(data: dict):
    return await fetch_from_django("contacts/", method="POST", body=data)

async def logic_update_status(task_id: int, status: str):
    return await fetch_from_django(f"tasks/{task_id}/update-status/", method="PATCH", body={"status": status})

async def logic_update_priority(task_id: int, priority: str):
    return await fetch_from_django(f"tasks/{task_id}/update-priority/", method="PATCH", body={"priority": priority})

async def logic_get_tasks(limit: int = 10, status: str = None):
    params = {"limit": limit}
    if status: params["status"] = status
    return await fetch_from_django("tasks/", params=params)

async def logic_create_task(title: str, priority: str = "medium"):
    return await fetch_from_django("tasks/", method="POST", body={"title": title, "priority": priority})

async def logic_get_latest_tasks():
    return await fetch_from_django("tasks/latest/")

async def logic_get_stats():
    return await fetch_from_django("tasks/statistics/")


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

@app.patch("/tasks/{task_id}/update-status", operation_id="updateTaskStatus")
async def api_update_status(task_id: int, data: Dict[str, str]):
    new_status = data.get("status")
    if not new_status:
        return {"error": "Missing status field"}
    return await logic_update_status(task_id, new_status)

@app.patch("/tasks/{task_id}/update-priority", operation_id="updateTaskPriority")
async def api_update_priority(task_id: int, data: Dict[str, str]):
    new_priority = data.get("priority")
    if not new_priority:
        return {"error": "Missing priority field"}
    return await logic_update_priority(task_id, new_priority)

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

@app.get("/contacts/search", operation_id="searchContacts")
async def api_search_contacts(search: Optional[str] = None, limit: int = 10):
    """Search for contacts by name, email, or title."""
    return await logic_get_contacts(search, limit)

@app.post("/contacts/create", operation_id="createContact")
async def api_create_contact(contact: ContactInput):
    """Create a new contact in the CRM."""
    return await logic_create_contact(contact.model_dump(exclude_none=True))

@app.patch("/contacts/{contact_id}", operation_id="updateContact")
async def api_update_contact(contact_id: int, contact: UpdateContactInput):
    """Update specific fields of an existing contact."""
    payload = contact.model_dump(exclude_none=True)
    if not payload:
        return {"error": "No fields provided to update."}
    return await fetch_from_django(f"contacts/{contact_id}/", method="PATCH", body=payload)

@app.delete("/contacts/{contact_id}", operation_id="deleteContact")
async def api_delete_contact(contact_id: int):
    """Delete a contact from the CRM."""
    return await fetch_from_django(f"contacts/{contact_id}/", method="DELETE")

# --- Pydantic Model for Email ---
class SendEmailInput(BaseModel):
    to: str = Field(..., description="Recipient email address")
    subject: str = Field(..., description="Subject line of the email")
    body: str = Field(..., description="Body of the email (supports HTML)")
    cc: Optional[str] = Field(None, description="Comma-separated CC email addresses")
    bcc: Optional[str] = Field(None, description="Comma-separated BCC email addresses")
    from_email: Optional[str] = Field(None, alias="from", description="Optional alias or 'from' address")

# --- Email Endpoint ---
@app.post("gamil/send_mail/", operation_id="sendEmail")
async def api_send_email(email_data: SendEmailInput):
    """
    Sends an email using the user's connected Gmail account in the CRM.
    """
    # Convert Pydantic model to a dict, handling the 'from' alias
    payload = email_data.model_dump(by_alias=True, exclude_none=True)
    
    # Forwards to your Django @api_view(['POST']) send_email
    # Note: Ensure your Django URL configuration routes 'email/send/' to your send_email function
    return await fetch_from_django("email/send/", method="POST", body=payload)

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