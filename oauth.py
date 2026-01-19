import httpx
from fastapi import APIRouter, Request, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Optional
from itsdangerous import URLSafeTimedSerializer
from settings import DJANGO_LOGIN_URL, MCP_SECRET_KEY

serializer = URLSafeTimedSerializer(MCP_SECRET_KEY)
router = APIRouter()

@router.get("/authorize", response_class=HTMLResponse)
async def authorize_page(
    redirect_uri: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
):
    if not redirect_uri:
        return HTMLResponse("<h2>Missing redirect_uri. Start from ChatGPT.</h2>", status_code=400)

    return f"""
    <html>
    <body style="font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background: #f4f4f5;">
        <div style="background: white; padding: 2rem; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); width: 300px;">
            <h2 style="text-align: center; color: #333;">CRM Login</h2>
            <form action="/oauth/login" method="post">
                <input type="hidden" name="redirect_uri" value="{redirect_uri}">
                <input type="hidden" name="state" value="{state or ''}">
                <input type="email" name="email" placeholder="Email" required style="width: 100%; padding: 10px; margin-bottom: 10px; border: 1px solid #ddd; border-radius: 4px;">
                <input type="password" name="password" placeholder="Password" required style="width: 100%; padding: 10px; margin-bottom: 20px; border: 1px solid #ddd; border-radius: 4px;">
                <button type="submit" style="width: 100%; padding: 10px; background: #2563eb; color: white; border: none; border-radius: 4px; cursor: pointer;">Sign In</button>
            </form>
        </div>
    </body>
    </html>
    """

@router.post("/login")
async def login_and_authorize(
    email: str = Form(...),      
    password: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(...)
):
    async with httpx.AsyncClient() as client:
        try:
            # Try JSON login first (standard for DRF)
            payload = {"email": email, "password": password}
            response = await client.post(DJANGO_LOGIN_URL, json=payload, timeout=10.0)
            
            if response.status_code != 200:
                return HTMLResponse(f"<h3>Login Failed: {response.text}</h3>", status_code=401)
                
            data = response.json()
            # Support various token keys (DRF uses 'access', generic uses 'token')
            django_token = data.get("access") or data.get("token") or data.get("key")
            
            if not django_token:
                return HTMLResponse("<h3>Error: No token returned from backend.</h3>", status_code=400)

            # Encrypt the Django token into a temporary code
            auth_code = serializer.dumps(django_token)
            
            separator = "&" if "?" in redirect_uri else "?"
            final_url = f"{redirect_uri}{separator}code={auth_code}&state={state}"
            return RedirectResponse(url=final_url, status_code=303)

        except Exception as e:
            return HTMLResponse(f"<h3>System Error: {str(e)}</h3>", status_code=500)

@router.post("/token")
async def exchange_token(code: str = Form(...)):
    try:
        # Decrypt the code to get back the real Django token
        django_token = serializer.loads(code, max_age=300) # Code valid for 5 mins
        return {
            "access_token": django_token,
            "token_type": "Bearer",
            "expires_in": 3600
        }
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid or expired code")