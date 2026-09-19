"""AXIOM local auth and permission service.

Compatible with the Vue client's former Java /sys/* contract so login,
captcha, token checks and basic RBAC work without the original Java backend.
"""
from __future__ import annotations

import base64
import io
import os
import re
import random
import secrets
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any, Optional

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from fastapi import FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field
from store import AuthStore

AES_KEY = os.environ.get("LOGIN_AES_KEY", "AxiomCampusKey16").encode("utf-8")
AES_IV = os.environ.get("LOGIN_AES_IV", "AxiomCampusIv16!").encode("utf-8")
DEFAULT_USERNAME = os.environ.get("AXIOM_ADMIN_USER", "admin")
TOKEN_TTL_SECONDS = int(os.environ.get("AXIOM_TOKEN_TTL", "86400"))

CAPTCHA_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"

# ---- 图片上传（头像、智能体图标） ----
UPLOAD_SUBDIR = "uploads"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
# multipart 分隔符和头部的余量；正文超过它直接拒收，不把整个请求读进内存。
MAX_UPLOAD_REQUEST_BYTES = MAX_UPLOAD_BYTES + 64 * 1024
# Pillow 识别出的格式 → 落盘扩展名 / Content-Type。类型判定看文件内容，不信文件名。
IMAGE_FORMATS = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "GIF": ("gif", "image/gif"),
    "WEBP": ("webp", "image/webp"),
}
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
IMAGE_MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                     "gif": "image/gif", "webp": "image/webp"}
# 我们自己生成的对象路径长这样；静态接口和头像字段都只认这个形状，天然挡掉 ../ 和绝对路径。
UPLOAD_PATH_PATTERN = re.compile(rf"^{UPLOAD_SUBDIR}/[0-9a-f]{{32}}\.(png|jpg|gif|webp)$")

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
    db_path = os.environ.get("AXIOM_AUTH_DB", "data/auth.sqlite3")
    app.state.store = AuthStore(db_path, DEFAULT_USERNAME, password)
    # 上传的图片放在数据库旁边的 uploads/：容器里 /app/data 已经是持久卷，不用再挂一个。
    app.state.upload_dir = (Path(db_path).resolve().parent / UPLOAD_SUBDIR)
    app.state.upload_dir.mkdir(parents=True, exist_ok=True)
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
    if path in ("/sys/login", "/sys/user/register") or path.startswith("/sys/randomImage/"):
        # Do not trust client-supplied forwarded-IP headers.
        host = request.client.host if request.client else "unknown"
        login_request = path in ("/sys/login", "/sys/user/register")
        key = ("login:" if login_request else "captcha:") + host
        if not app.state.store.allow_attempt(key, 10 if login_request else 30, 300 if login_request else 60):
            return fail("请求过于频繁，请稍后重试", code=429, status=429)
    response = await call_next(request)
    # 处理器自己设了 Cache-Control（上传图片文件名随机、内容不变，可长期缓存）就不覆盖。
    response.headers.setdefault("Cache-Control", "no-store")
    return response


def ok(result: Any = None, message: str = "", code: int = 200) -> dict:
    """Jeecg 信封。message 默认留空：前端 axios 默认 successMessageMode='success'，
    凡是 success 且 message 非空的响应都会弹「成功」气泡，Java 端 Result.OK(data)
    的 message 就是空串——只有明确要提示的操作（注册等）才传文案。"""
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


MEMBER_PERMISSION_CODES = ["chat:view", "campus:view"]
ADMIN_PERMISSION_CODES = MEMBER_PERMISSION_CODES + [
    "admin",
    "admin:manager",
    "campus:admin",
    "system:user",
    "system:role",
]


def permission_codes(record: dict[str, Any] | None) -> list[str]:
    """Only the administrator sees admin:* — the console entry keys off it."""
    return ADMIN_PERMISSION_CODES if (record or {}).get("is_admin") else MEMBER_PERMISSION_CODES


def session_record(session: dict[str, Any]) -> dict[str, Any] | None:
    return app.state.store.user_record(session["user"]["username"])


def profile_payload(record: dict[str, Any]) -> dict[str, Any]:
    """个人资料列 → Jeecg 字段。birthday 空串给 None，前端 dayjs('') 判为无效日期会当空处理，
    但 Jeecg 原本就是 null，保持一致。"""
    return {
        "realname": record.get("realname") or "",
        "avatar": record.get("avatar") or "",
        "birthday": record.get("birthday") or None,
        "sex": int(record.get("sex") or 0),
        "email": record.get("email") or "",
        "phone": record.get("phone") or "",
    }


def user_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Jeecg-shaped user object for either the administrator or a member account."""
    base = admin_user()
    profile = profile_payload(record)
    if record.get("is_admin"):
        # 管理员没填邮箱时沿用旧的占位地址，前端有地方拿它当展示文本。
        profile["email"] = profile["email"] or base["email"]
        base.update(profile)
        return base
    username = record["username"]
    base.update(profile)
    base.update({
        "id": f"u-{username}",
        "username": username,
        "realname": profile["realname"] or username,
        "workNo": username,
        "post": "member",
        "userIdentity": 1,
        "roles": [{"roleName": "成员", "value": "user", "roleCode": "user"}],
    })
    return base


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


def menus() -> list[dict[str, Any]]:
    """侧栏菜单。只下发工作台真正有的页面：
    /system/user、/system/role 那组 Jeecg 用户/角色管理页在 Java 后端下线后已从前端
    源码删除（views/system 只剩 loginmini 与几个仍被活代码 import 的 *.api.ts），
    /newapi/campus-assistant 的功能已并入 /admin 的「校园百事通」tab；这里只能指向
    routeHelper 动态 glob（agent/sys/system/peopleCenter）里仍然存在的页面。"""
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
    ]


def current_session(x_access_token: Optional[str], authorization: Optional[str]) -> Optional[dict[str, Any]]:
    token = (x_access_token or authorization or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        return None
    username = app.state.store.session_username(token)
    if username is None:
        return None
    record = app.state.store.user_record(username)
    if record is None:
        return None
    return {"user": user_payload(record)}


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
    record = app.state.store.user_record(username)
    if record is None:
        return fail("用户名或密码错误", code=401, status=401)
    token = app.state.store.create_session(TOKEN_TTL_SECONDS, username)
    return ok({"token": token, "userInfo": user_payload(record), "sysAllDictItems": {}})


USERNAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,31}$")
MIN_PASSWORD_LENGTH = 8


class RegisterBody(BaseModel):
    username: str = Field(default="", max_length=128)
    password: str = Field(default="", max_length=4096)
    realname: str = Field(default="", max_length=64)
    captcha: str = Field(default="", max_length=16)
    checkKey: str = Field(default="", max_length=128)


@app.post("/sys/user/register")
def register(body: RegisterBody):
    if not app.state.store.consume_captcha(body.checkKey, body.captcha.strip()):
        return fail("验证码错误或已过期，请刷新后重试", code=412)
    username = (body.username or "").strip()
    if not USERNAME_PATTERN.match(username):
        return fail("用户名需为 3-32 位字母、数字或下划线，且以字母开头", code=400)
    try:
        password = decrypt_password(body.password)
    except ValueError:
        return fail("注册加密参数或密码格式错误", code=400, status=400)
    if len(password) < MIN_PASSWORD_LENGTH:
        return fail(f"密码至少 {MIN_PASSWORD_LENGTH} 位", code=400)
    error = app.state.store.register_user(username, password, (body.realname or "").strip())
    if error:
        return fail(error, code=409)
    record = app.state.store.user_record(username)
    token = app.state.store.create_session(TOKEN_TTL_SECONDS, username)
    return ok({"token": token, "userInfo": user_payload(record), "sysAllDictItems": {}}, message="注册成功")


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
    return ok(permission_codes(session_record(session)))


@app.get("/sys/permission/getUserPermissionByToken")
def get_user_permission(
    x_access_token: Optional[str] = Header(None, alias="X-Access-Token"),
    authorization: Optional[str] = Header(None),
):
    session, error = require_user(x_access_token, authorization)
    if error:
        return error
    codes = permission_codes(session_record(session))
    auth = [{"action": code, "type": "1", "status": "1", "describe": code} for code in codes]
    return ok({"menu": menus(), "auth": auth, "allAuth": auth, "codeList": codes, "sysSafeMode": False})


# ---------------- 个人资料 ----------------

class UserEditBody(BaseModel):
    """前端 userEdit 只传改动的键（头像单独一次、基本资料一次），全部可选。
    id 收下但不用：只能改会话本人的资料，改谁由 token 决定。"""
    id: Optional[int | str] = None
    realname: Optional[str] = Field(default=None, max_length=100)
    avatar: Optional[str] = Field(default=None, max_length=256)
    birthday: Optional[str] = Field(default=None, max_length=32)
    sex: Optional[int | str] = None
    email: Optional[str] = Field(default=None, max_length=128)
    phone: Optional[str] = Field(default=None, max_length=32)


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_PATTERN = re.compile(r"^\+?[0-9-]{6,20}$")


def validate_profile_patch(body: UserEditBody) -> tuple[dict[str, Any], str]:
    """把请求体收敛成能直接写库的字段；返回 (字段, 错误文案)。"""
    fields: dict[str, Any] = {}
    if body.realname is not None:
        realname = body.realname.strip()
        if not realname:
            return {}, "名称不能为空"
        fields["realname"] = realname
    if body.avatar is not None:
        avatar = body.avatar.strip()
        # 头像只接受本服务签发的对象路径（或清空）：存任意 URL 会被其他用户的浏览器当 <img> 加载。
        if avatar and not UPLOAD_PATH_PATTERN.match(avatar):
            return {}, "头像地址无效，请重新上传"
        fields["avatar"] = avatar
    if body.birthday is not None:
        birthday = body.birthday.strip()[:10]
        if birthday:
            try:
                birthday = date.fromisoformat(birthday).isoformat()
            except ValueError:
                return {}, "生日格式应为 YYYY-MM-DD"
        fields["birthday"] = birthday
    if body.sex is not None:
        sex = str(body.sex).strip()
        if sex not in ("", "0", "1", "2"):
            return {}, "性别取值无效"
        fields["sex"] = int(sex or 0)
    if body.email is not None:
        email = body.email.strip()
        if email and not EMAIL_PATTERN.match(email):
            return {}, "邮箱格式不正确"
        fields["email"] = email
    if body.phone is not None:
        phone = body.phone.strip()
        if phone and not PHONE_PATTERN.match(phone):
            return {}, "手机号格式不正确"
        fields["phone"] = phone
    return fields, ""


@app.get("/sys/user/login/setting/getUserData")
def get_user_data(
    x_access_token: Optional[str] = Header(None, alias="X-Access-Token"),
    authorization: Optional[str] = Header(None),
):
    """「个人资料」弹窗的读接口：就是当前用户的 Jeecg 用户对象。"""
    session, error = require_user(x_access_token, authorization)
    if error:
        return error
    return ok(session["user"])


@app.api_route("/sys/user/login/setting/userEdit", methods=["POST", "PUT"])
def user_edit(
    body: UserEditBody,
    x_access_token: Optional[str] = Header(None, alias="X-Access-Token"),
    authorization: Optional[str] = Header(None),
):
    """改自己的资料。前端用 POST；Jeecg 原版是 PUT，两个都接。"""
    session, error = require_user(x_access_token, authorization)
    if error:
        return error
    fields, message = validate_profile_patch(body)
    if message:
        return fail(message, code=400)
    username = session["user"]["username"]
    if fields:
        app.state.store.update_user_profile(username, fields)
    record = app.state.store.user_record(username)
    return ok(user_payload(record))


# ---------------- 图片上传与静态文件 ----------------

def parse_multipart(body: bytes, content_type: str) -> dict[str, tuple[str, bytes]]:
    """最小 multipart/form-data 解析：{字段名: (文件名, 内容)}。

    不装 python-multipart 的原因：线上容器是源码挂载 + --reload，requirements 变了
    镜像不会自动重建；FastAPI 的 UploadFile 在导入期就会因缺依赖抛错，整个认证服务跟着挂。
    浏览器 FormData 生成的报文格式固定（CRLF 分隔、每段一个 Content-Disposition），
    这里只处理这一种，畸形报文一律当作没有文件。"""
    match = re.search(r'boundary="?([^";,]+)"?', content_type or "", re.IGNORECASE)
    if not match:
        return {}
    delimiter = b"--" + match.group(1).encode("latin-1")
    fields: dict[str, tuple[str, bytes]] = {}
    # 首段前面没有 CRLF，补一个让所有段的切法一致。
    for chunk in (b"\r\n" + body).split(b"\r\n" + delimiter)[1:]:
        if chunk.startswith(b"--"):
            break  # 结束分隔符
        head, sep, data = chunk.lstrip(b"\r\n").partition(b"\r\n\r\n")
        if not sep:
            continue
        headers = head.decode("latin-1", "replace")
        disposition = re.search(r"content-disposition:\s*form-data;(.*)", headers, re.IGNORECASE)
        if not disposition:
            continue
        params = dict(re.findall(r'\s*([A-Za-z0-9_-]+)="?([^";]*)"?', disposition.group(1)))
        name = params.get("name")
        if name:
            fields[name] = (params.get("filename", ""), data)
    return fields


def sniff_image(data: bytes) -> tuple[str, str] | None:
    """用 Pillow 按内容识别格式并校验完整性；返回 (扩展名, Content-Type)，不是允许的图片给 None。"""
    try:
        with Image.open(io.BytesIO(data)) as image:
            fmt = image.format
            image.verify()
    except Exception:  # Pillow 对坏文件抛的异常类型很多，统一当作不是图片
        return None
    return IMAGE_FORMATS.get(fmt or "")


@app.post("/sys/common/upload")
async def upload_image(request: Request):
    """Jeecg 的 /sys/common/upload：字段名 file，返回对象路径。
    路径同时放在 result 和 message：新组件（头像裁剪、智能体图标）读 result，
    Jeecg 老组件 JUpload/JImageUpload 读 message——Java 原版就是把路径写在 message 里。"""
    _, error = require_user(request.headers.get("X-Access-Token"), request.headers.get("Authorization"))
    if error:
        return error
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        return fail("请以 multipart/form-data 上传文件", code=400)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_REQUEST_BYTES:
        return fail("图片不能超过 2MB", code=400)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_UPLOAD_REQUEST_BYTES:
            return fail("图片不能超过 2MB", code=400)
    filename, data = parse_multipart(bytes(body), content_type).get("file", ("", b""))
    if not data:
        return fail("没有收到文件，请选择图片后重试", code=400)
    if len(data) > MAX_UPLOAD_BYTES:
        return fail("图片不能超过 2MB", code=400)
    # 文件名扩展名和内容都要过：扩展名挡明显错传，内容识别挡改名的可执行文件。
    suffix = Path(filename).suffix.lower().lstrip(".")
    if suffix and suffix not in IMAGE_EXTENSIONS:
        return fail("仅支持 png、jpg、gif、webp 图片", code=400)
    kind = sniff_image(data)
    if kind is None:
        return fail("文件不是有效的 png、jpg、gif 或 webp 图片", code=400)
    extension, _ = kind
    # 文件名用随机 id，不用用户给的：避免覆盖、路径穿越和可预测的地址。
    object_path = f"{UPLOAD_SUBDIR}/{secrets.token_hex(16)}.{extension}"
    target = app.state.upload_dir / Path(object_path).name
    target.write_bytes(data)
    return ok(object_path, message=object_path)


@app.get("/sys/common/static/{path:path}")
def static_file(path: str):
    """按上传时返回的对象路径把文件吐回去。<img src> 带不了 token，这里不做鉴权（Jeecg 原版也是
    白名单）；文件名是 128 位随机 id，猜不到。路径只认我们自己签发的形状，任何 ../、绝对路径、
    其他目录都是 404。"""
    if not UPLOAD_PATH_PATTERN.match(path):
        return fail("文件不存在", code=404, status=404)
    upload_dir: Path = app.state.upload_dir
    target = (upload_dir / Path(path).name).resolve()
    if target.parent != upload_dir or not target.is_file():
        return fail("文件不存在", code=404, status=404)
    return FileResponse(
        target,
        media_type=IMAGE_MEDIA_TYPES[target.suffix.lstrip(".")],
        headers={
            # 文件名随机且内容不变：可以放心长期缓存。
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


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
    records = [admin_user()] + [user_payload(r) for r in app.state.store.list_users()]
    total = len(records)
    return ok({"records": records, "total": total, "size": pageSize, "current": pageNo, "pages": 1})


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


@app.get("/sys/dict/getDictItems/{code}")
def dict_items(code: str, x_access_token: Optional[str] = Header(None, alias="X-Access-Token"),
               authorization: Optional[str] = Header(None)):
    _, error = require_user(x_access_token, authorization)
    if error:
        return error
    return ok([])


@app.get("/app/appInfo/my/all/list")
def empty_local_catalog(
    x_access_token: Optional[str] = Header(None, alias="X-Access-Token"),
    authorization: Optional[str] = Header(None),
):
    _, error = require_user(x_access_token, authorization)
    if error:
        return error
    return ok([])


@app.get("/ai/skill/list")
def skill_catalog_moved(
    x_access_token: Optional[str] = Header(None, alias="X-Access-Token"),
    authorization: Optional[str] = Header(None),
):
    """Skill 目录已迁至 agent-api（/agent-api/skill/list）。

    这里原本返回一个永远为空的 `[]`，主对话与 Skill 广场据此把「技能目录为空」当成事实，
    演示文稿助手因此报「未找到已启用的 ppt-studio」。改成与其它未实现接口一致的 404，
    避免再有人把这条桩当成有数据的接口来接。
    """
    _, error = require_user(x_access_token, authorization)
    if error:
        return error
    return fail("Skill 目录已迁至 agent-api：/agent-api/skill/list", code=404, status=404)


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def fallback(full_path: str, request: Request):
    _, error = require_user(request.headers.get("X-Access-Token"), request.headers.get("Authorization"))
    if error:
        return error
    return fail("当前本地认证服务未实现此接口", code=404, status=404)
