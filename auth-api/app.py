"""AXIOM local auth and permission service.

Compatible with the Vue client's former Java /sys/* contract so login,
captcha, token checks and basic RBAC work without the original Java backend.
"""
from __future__ import annotations

import base64
import io
import os
import random
import secrets
from contextlib import asynccontextmanager
from typing import Any, Optional

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from fastapi import FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field
from store import AuthStore

AES_KEY = os.environ.get("LOGIN_AES_KEY", "AxiomCampusKey16").encode("utf-8")
AES_IV = os.environ.get("LOGIN_AES_IV", "AxiomCampusIv16!").encode("utf-8")
DEFAULT_USERNAME = os.environ.get("AXIOM_ADMIN_USER", "admin")
TOKEN_TTL_SECONDS = int(os.environ.get("AXIOM_TOKEN_TTL", "86400"))

CAPTCHA_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"

@asynccontextmanager
async def lifespan(app: FastAPI):
    if len(AES_KEY) not in (16, 24, 32) or len(AES_IV) != 16:
        raise RuntimeError("LOGIN_AES_KEY must be 16/24/32 bytes; LOGIN_AES_IV must be 16 bytes")
    password = os.environ.get("AXIOM_ADMIN_PASSWORD", "")
    minimum_length = 7 if os.environ.get("AXIOM_LOCAL_DEMO_LOGIN") == "true" else 12
    if len(password) < minimum_length or password.upper().startswith("CHANGE_ME"):
        raise RuntimeError(f"Set AXIOM_ADMIN_PASSWORD to a password of at least {minimum_length} characters")
    if not DEFAULT_USERNAME.strip() or TOKEN_TTL_SECONDS <= 0:
        raise RuntimeError("AXIOM_ADMIN_USER and AXIOM_TOKEN_TTL must be valid")
    app.state.store = AuthStore(os.environ.get("AXIOM_AUTH_DB", "data/auth.sqlite3"), DEFAULT_USERNAME, password)
    yield


app = FastAPI(title="AXIOM Auth API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.environ.get(
        "AXIOM_CORS_ORIGINS", "http://127.0.0.1:3200,http://localhost:3200"
    ).split(",") if origin.strip() and origin.strip() != "*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Access-Token", "Authorization", "X-Tenant-Id"],
)

@app.middleware("http")
async def login_rate_limit(request: Request, call_next):
    path = request.url.path
    if path == "/sys/login" or path.startswith("/sys/randomImage/"):
        # Do not trust client-supplied forwarded-IP headers.
        host = request.client.host if request.client else "unknown"
        login_request = path == "/sys/login"
        key = ("login:" if login_request else "captcha:") + host
        if not app.state.store.allow_attempt(key, 10 if login_request else 30, 300 if login_request else 60):
            return fail("请求过于频繁，请稍后重试", code=429, status=429)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


def ok(result: Any = None, message: str = "成功", code: int = 200) -> dict:
    return {"success": True, "code": code, "message": message, "result": result}


def fail(message: str, code: int = 500, status: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"success": False, "code": code, "message": message, "result": None},
    )


def decrypt_password(cipher_text: str) -> str:
    raw = (cipher_text or "").strip()
    if not raw:
        raise ValueError("Missing encrypted password")
    try:
        data = base64.b64decode(raw, validate=True)
        decryptor = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_IV)).decryptor()
        padded = decryptor.update(data) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid encrypted password") from exc


def admin_user() -> dict[str, Any]:
    return {
        "id": "1",
        "username": DEFAULT_USERNAME,
        "realname": "管理员",
        "avatar": "",
        "birthday": None,
        "sex": 1,
        "email": "admin@axiom.local",
        "phone": "",
        "orgCode": "A01",
        "status": 1,
        "delFlag": 0,
        "workNo": "admin",
        "post": "admin",
        "telephone": None,
        "createTime": "2026-09-17 00:00:00",
        "updateTime": None,
        "userIdentity": 2,
        "departIds": "1",
        "relTenantIds": "0",
        "clientId": None,
        "homePath": "/center/chat/campus",
        "loginTenantId": 0,
        "tenantId": 0,
        "roles": [{"roleName": "管理员", "value": "admin", "roleCode": "admin"}],
    }


def admin_role() -> dict[str, Any]:
    return {
        "id": "role-admin",
        "roleName": "管理员",
        "roleCode": "admin",
        "description": "AXIOM 本地管理员，拥有全部权限",
        "createTime": "2026-09-17 00:00:00",
    }


def permission_rows() -> list[dict[str, Any]]:
    return [
        {"id": "p-campus", "name": "校园百事通", "url": "/center/chat/campus", "perms": "campus:view", "menuType": 1},
        {"id": "p-chat", "name": "主对话", "url": "/center/chat", "perms": "chat:view", "menuType": 1},
        {"id": "p-campus-admin", "name": "校园百事通配置", "url": "/newapi/campus-assistant", "perms": "campus:admin", "menuType": 1},
        {"id": "p-user", "name": "用户管理", "url": "/system/user", "perms": "system:user", "menuType": 1},
        {"id": "p-role", "name": "角色管理", "url": "/system/role", "perms": "system:role", "menuType": 1},
    ]


def menus() -> list[dict[str, Any]]:
    return [
        {
            "path": "/center",
            "component": "LAYOUT",
            "redirect": "/center/chat",
            "name": "campus-center",
            "id": "menu-center",
            "meta": {"title": "校园服务", "icon": "ant-design:home-outlined", "keepAlive": True},
            "children": [
                {
                    "path": "/center/chat",
                    "name": "campus-chat",
                    "id": "menu-chat",
                    "component": "peopleCenter/pages/ChatPage",
                    "meta": {"title": "新对话", "keepAlive": True},
                },
            ],
        },
        {
            "path": "/system",
            "component": "LAYOUT",
            "redirect": "/system/user",
            "name": "system-manage",
            "id": "menu-system",
            "meta": {"title": "权限管理", "icon": "ant-design:safety-certificate-outlined"},
            "children": [
                {
                    "path": "/system/user",
                    "name": "system-user",
                    "id": "menu-user",
                    "component": "system/user/index",
                    "meta": {"title": "用户管理"},
                },
                {
                    "path": "/system/role",
                    "name": "system-role",
                    "id": "menu-role",
                    "component": "system/role/index",
                    "meta": {"title": "角色管理"},
                },
            ],
        },
    ]


def current_session(x_access_token: Optional[str], authorization: Optional[str]) -> Optional[dict[str, Any]]:
    token = (x_access_token or authorization or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        return None
    if not app.state.store.valid_session(token):
        return None
    return {"user": admin_user()}


def require_user(x_access_token: Optional[str], authorization: Optional[str]):
    session = current_session(x_access_token, authorization)
    if not session:
        return None, fail("Token失效，请重新登录", code=401, status=401)
    return session, None


def render_captcha(text: str) -> str:
    image = Image.new("RGB", (120, 46), (247, 248, 250))
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 22)
    except OSError:
        font = ImageFont.load_default()
    for _ in range(18):
        draw.line(
            (
                random.randint(0, 120),
                random.randint(0, 46),
                random.randint(0, 120),
                random.randint(0, 46),
            ),
            fill=(200, 210, 220),
            width=1,
        )
    for index, char in enumerate(text):
        draw.text((12 + index * 24, 10), char, fill=(17, 24, 39), font=font)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


class LoginBody(BaseModel):
    username: str = Field(default="", max_length=128)
    password: str = Field(default="", max_length=4096)
    captcha: str = Field(default="", max_length=16)
    checkKey: str = Field(default="", max_length=128)
    loginOrgCode: str = Field(default="", max_length=128)


@app.get("/health")
def health():
    return {"status": "ok", "service": "axiom-auth"}


@app.get("/sys/randomImage/{check_key}")
def random_image(check_key: str):
    if not check_key or len(check_key) > 128:
        return fail("验证码标识无效", code=400, status=400)
    code = "".join(secrets.choice(CAPTCHA_ALPHABET) for _ in range(4))
    app.state.store.save_captcha(check_key, code)
    return ok(render_captcha(code))


@app.post("/sys/login")
def login(body: LoginBody):
    if not app.state.store.consume_captcha(body.checkKey, body.captcha.strip()):
        return fail("验证码错误或已过期，请刷新后重试", code=412)
    username = (body.username or "").strip()
    try:
        password = decrypt_password(body.password)
    except ValueError:
        return fail("登录加密参数或密码格式错误", code=400, status=400)
    if not app.state.store.verify_password(username, password):
        return fail("用户名或密码错误", code=401, status=401)
    token = app.state.store.create_session(TOKEN_TTL_SECONDS)
    user = admin_user()
    return ok({"token": token, "userInfo": user, "sysAllDictItems": {}})


@app.api_route("/sys/logout", methods=["GET", "POST"])
def logout(
    x_access_token: Optional[str] = Header(None, alias="X-Access-Token"),
    authorization: Optional[str] = Header(None),
):
    token = (x_access_token or authorization or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    app.state.store.revoke(token)
    return ok()


@app.get("/sys/user/getUserInfo")
def get_user_info(
    x_access_token: Optional[str] = Header(None, alias="X-Access-Token"),
    authorization: Optional[str] = Header(None),
):
    session, error = require_user(x_access_token, authorization)
    if error:
        return error
    return ok({"userInfo": session["user"], "sysAllDictItems": {}})


@app.get("/sys/permission/getPermCode")
def get_perm_code(
    x_access_token: Optional[str] = Header(None, alias="X-Access-Token"),
    authorization: Optional[str] = Header(None),
):
    session, error = require_user(x_access_token, authorization)
    if error:
        return error
    return ok(["admin", "campus:view", "campus:admin", "chat:view", "system:user", "system:role"])


@app.get("/sys/permission/getUserPermissionByToken")
def get_user_permission(
    x_access_token: Optional[str] = Header(None, alias="X-Access-Token"),
    authorization: Optional[str] = Header(None),
):
    session, error = require_user(x_access_token, authorization)
    if error:
        return error
    codes = ["admin", "campus:view", "campus:admin", "chat:view", "system:user", "system:role"]
    auth = [{"action": code, "type": "1", "status": "1", "describe": code} for code in codes]
    return ok({"menu": menus(), "auth": auth, "allAuth": auth, "codeList": codes, "sysSafeMode": False})


@app.get("/sys/user/list")
def user_list(
    x_access_token: Optional[str] = Header(None, alias="X-Access-Token"),
    authorization: Optional[str] = Header(None),
    pageNo: int = Query(1),
    pageSize: int = Query(10),
):
    session, error = require_user(x_access_token, authorization)
    if error:
        return error
    records = [admin_user()]
    return ok({"records": records, "total": 1, "size": pageSize, "current": pageNo, "pages": 1})


@app.get("/sys/role/list")
def role_list(
    x_access_token: Optional[str] = Header(None, alias="X-Access-Token"),
    authorization: Optional[str] = Header(None),
    pageNo: int = Query(1),
    pageSize: int = Query(10),
):
    session, error = require_user(x_access_token, authorization)
    if error:
        return error
    return ok({"records": [admin_role()], "total": 1, "size": pageSize, "current": pageNo, "pages": 1})


@app.get("/sys/permission/list")
def permission_list(
    x_access_token: Optional[str] = Header(None, alias="X-Access-Token"),
    authorization: Optional[str] = Header(None),
):
    session, error = require_user(x_access_token, authorization)
    if error:
        return error
    return ok(permission_rows())


@app.get("/sys/user/queryUserRole")
def query_user_role(
    x_access_token: Optional[str] = Header(None, alias="X-Access-Token"),
    authorization: Optional[str] = Header(None),
    userid: str = Query(""),
):
    session, error = require_user(x_access_token, authorization)
    if error:
        return error
    if userid and userid != "1":
        return fail("用户不存在", code=404, status=404)
    return ok(["role-admin"])


@app.get("/sys/dict/getDictItems/{code}")
def dict_items(code: str, x_access_token: Optional[str] = Header(None, alias="X-Access-Token"),
               authorization: Optional[str] = Header(None)):
    _, error = require_user(x_access_token, authorization)
    if error:
        return error
    return ok([])


@app.get("/ai/knowledge/base/queryById")
@app.get("/ai/knowledge/document/list")
@app.get("/ai/knowledge/acl/list")
@app.post("/ai/knowledge/retrieval/test")
@app.post("/ai/knowledge/retrieval/internal")
def knowledge_unavailable(x_access_token: Optional[str] = Header(None, alias="X-Access-Token"),
                          authorization: Optional[str] = Header(None)):
    _, error = require_user(x_access_token, authorization)
    if error:
        return error
    return fail("知识库业务服务尚未接入，暂不支持知识库管理和检索", code=503, status=503)


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def fallback(full_path: str, request: Request):
    _, error = require_user(request.headers.get("X-Access-Token"), request.headers.get("Authorization"))
    if error:
        return error
    return fail("当前本地认证服务未实现此接口", code=404, status=404)
