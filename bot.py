import asyncio
import io
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import aiohttp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("SanskariExtractorBot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()
DEFAULT_CREDIT = os.getenv("DEFAULT_CREDIT", "✦ Extracted By: 𝐌𝐫. 𝐒𝐚𝐧𝐬𝐤𝐚𝐫𝐢 ✦").strip()
PORT = int(os.getenv("PORT", "10000"))

ALLOWED_ADMINS: set[int] = set()
if ADMIN_IDS_RAW:
    for part in ADMIN_IDS_RAW.split(","):
        part = part.strip()
        if part.isdigit():
            ALLOWED_ADMINS.add(int(part))


@dataclass
class InstituteProfile:
    key: str
    name: str
    short_label: str
    base_url: str
    login_path: str
    batches_path: str
    contents_path: str
    id_field: str = "phone"
    password_field: str = "password"
    extra_headers: Dict[str, str] = field(default_factory=dict)


INSTITUTES: Dict[str, InstituteProfile] = {
    "pw": InstituteProfile(
        key="pw",
        name="PW (Physics Wallah)",
        short_label="PW",
        base_url="https://api.penpencil.co",
        login_path="/v1/users/login",
        batches_path="/v3/batches/my-batches",
        contents_path="/v2/batches/{batch_id}/contents",
        id_field="username",
        password_field="password",
        extra_headers={"Accept": "application/json"},
    ),
    "rwa": InstituteProfile(
        key="rwa",
        name="RWA (Rojgar with Ankit)",
        short_label="RWA",
        base_url="https://rozgarapinew.teachx.in",
        login_path="/api/login",
        batches_path="/api/my-courses",
        contents_path="/api/course-contents/{batch_id}",
        id_field="phone",
        password_field="password",
        extra_headers={"Accept": "application/json"},
    ),
    "target_board": InstituteProfile(
        key="target_board",
        name="Target Board",
        short_label="Target Board",
        base_url="https://targetboard.classx.co.in",
        login_path="/post/userLogin",
        batches_path="/get/mycourse",
        contents_path="/get/allsubjectfrmlivecourseclass?courseid={batch_id}",
        id_field="email",
        password_field="password",
        extra_headers={"Accept": "application/json"},
    ),
    "disha_online": InstituteProfile(
        key="disha_online",
        name="Disha Online Classes",
        short_label="Disha Online",
        base_url="https://dishaonlineclasses.classx.co.in",
        login_path="/post/userLogin",
        batches_path="/get/mycourse",
        contents_path="/get/allsubjectfrmlivecourseclass?courseid={batch_id}",
        id_field="email",
        password_field="password",
        extra_headers={"Accept": "application/json"},
    ),
}


@dataclass
class UserSession:
    institute_key: Optional[str] = None
    custom_base_url: Optional[str] = None
    custom_login_path: str = "/post/userLogin"
    custom_batches_path: str = "/get/mycourse"
    custom_contents_path: str = "/get/allsubjectfrmlivecourseclass?courseid={batch_id}"
    custom_id_field: str = "email"
    custom_password_field: str = "password"
    mobile_or_id: Optional[str] = None
    token: Optional[str] = None
    user_info: Dict[str, Any] = field(default_factory=dict)
    batches: List[Dict[str, str]] = field(default_factory=list)
    selected_batch: Optional[Dict[str, str]] = None
    credit: str = DEFAULT_CREDIT
    awaiting_state: Optional[str] = None
    temp_login_id: Optional[str] = None
    extract_task: Optional[asyncio.Task] = field(default=None, repr=False)
    cancel_flag: bool = False


USER_SESSIONS: Dict[int, UserSession] = {}


def get_session(user_id: int) -> UserSession:
    if user_id not in USER_SESSIONS:
        USER_SESSIONS[user_id] = UserSession()
    return USER_SESSIONS[user_id]


def is_authorized(user_id: int) -> bool:
    if not ALLOWED_ADMINS:
        return True
    return user_id in ALLOWED_ADMINS


def get_active_profile(session: UserSession) -> Optional[InstituteProfile]:
    if session.institute_key == "custom" and session.custom_base_url:
        base = session.custom_base_url.rstrip("/")
        return InstituteProfile(
            key="custom",
            name=f"Custom ({base})",
            short_label="Custom API",
            base_url=base,
            login_path=session.custom_login_path,
            batches_path=session.custom_batches_path,
            contents_path=session.custom_contents_path,
            id_field=session.custom_id_field,
            password_field=session.custom_password_field,
            extra_headers={"Accept": "application/json"},
        )
    if session.institute_key and session.institute_key in INSTITUTES:
        return INSTITUTES[session.institute_key]
    return None


def build_headers(profile: InstituteProfile, token: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Mobile Safari/537.36"
        ),
        "Referer": profile.base_url + "/",
        "Origin": profile.base_url,
    }
    headers.update(profile.extra_headers)
    if token:
        headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
        headers["token"] = token
        headers["Auth-Token"] = token
    return headers


def extract_token_from_payload(data: Any) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    candidate_keys = [
        "token",
        "access_token",
        "accessToken",
        "auth_token",
        "jwt",
        "session_token",
    ]
    for k in candidate_keys:
        val = data.get(k)
        if isinstance(val, str) and val.strip():
            return val.strip()
    for container_key in ["data", "user", "result", "response", "payload"]:
        sub = data.get(container_key)
        if isinstance(sub, dict):
            found = extract_token_from_payload(sub)
            if found:
                return found
    return None


def normalize_batches(raw: Any) -> List[Dict[str, str]]:
    items: List[Any] = []
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        for key in ["data", "batches", "courses", "items", "result", "list"]:
            val = raw.get(key)
            if isinstance(val, list):
                items = val
                break
            if isinstance(val, dict):
                for sub_key in ["batches", "courses", "items", "list", "data"]:
                    if isinstance(val.get(sub_key), list):
                        items = val[sub_key]
                        break
                if items:
                    break

    parsed: List[Dict[str, str]] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        batch_id = str(
            item.get("id")
            or item.get("_id")
            or item.get("batch_id")
            or item.get("batchId")
            or item.get("course_id")
            or item.get("courseId")
            or idx + 1
        ).strip()
        title = str(
            item.get("name")
            or item.get("title")
            or item.get("batch_name")
            or item.get("course_name")
            or f"Batch {idx + 1}"
        ).strip()
        if batch_id and title:
            parsed.append({"id": batch_id, "title": title})
    return parsed


def is_usable_url(url: str) -> bool:
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        return False
    lower = u.lower()
    if "sealed.invalid" in lower or lower.endswith(".invalid"):
        return False
    return True


def collect_media_entries(
    node: Any,
    topic_stack: List[str],
    entries: List[Tuple[str, str, str]],
    visited_ids: set[int],
    depth: int = 0,
) -> None:
    if depth > 14 or node is None:
        return
    obj_id = id(node)
    if obj_id in visited_ids:
        return
    visited_ids.add(obj_id)

    if isinstance(node, list):
        for elem in node:
            collect_media_entries(elem, topic_stack, entries, visited_ids, depth + 1)
        return

    if isinstance(node, dict):
        label = str(
            node.get("topic_name")
            or node.get("subject_name")
            or node.get("chapter_name")
            or node.get("section_name")
            or node.get("category_name")
            or node.get("title")
            or node.get("name")
            or ""
        ).strip()

        next_stack = list(topic_stack)
        if label and any(
            k in node for k in ("contents", "lectures", "videos", "topics", "subjects", "chapters", "items", "classes", "data")
        ):
            if not next_stack or next_stack[-1] != label:
                next_stack.append(label)

        base_title = str(
            node.get("lecture_title")
            or node.get("video_title")
            or node.get("pdf_title")
            or node.get("title")
            or node.get("name")
            or "Untitled"
        ).strip()
        topic_name = " > ".join(next_stack) if next_stack else "General"

        url_fields = [
            ("video", ["video_url", "videoUrl", "m3u8_url", "m3u8Url", "hls_url", "hlsUrl", "stream_url", "streamUrl", "mp4_url", "mp4Url", "play_url", "playUrl", "url"]),
            ("pdf", ["pdf_url", "pdfUrl", "notes_url", "notesUrl", "document_url", "documentUrl", "file_url", "fileUrl", "download_url", "downloadUrl"]),
            ("test", ["test_url", "testUrl", "quiz_url", "quizUrl", "exam_url", "examUrl"]),
        ]

        for kind, keys in url_fields:
            for k in keys:
                val = node.get(k)
                if isinstance(val, str) and is_usable_url(val):
                    clean_url = val.strip()
                    lower_u = clean_url.lower()
                    if kind == "video" and (".pdf" in lower_u or "test" in k.lower() or "quiz" in k.lower()):
                        continue
                    title_out = f"{base_title} PDF" if kind == "pdf" and "pdf" not in base_title.lower() else base_title
                    entries.append((topic_name, title_out, clean_url))
                    break

        for val in node.values():
            if isinstance(val, (dict, list)):
                collect_media_entries(val, next_stack, entries, visited_ids, depth + 1)


def format_txt_output(
    institute_name: str,
    batch_title: str,
    credit: str,
    entries: List[Tuple[str, str, str]],
) -> str:
    lines: List[str] = [
        f"# Institute : {institute_name}",
        f"# Batch     : {batch_title}",
        f"# Total     : {len(entries)}",
        f"# Credit    : {credit}",
        "",
    ]

    grouped: Dict[str, List[Tuple[str, str]]] = {}
    for topic, title, url in entries:
        grouped.setdefault(topic, []).append((title, url))

    for topic, items in grouped.items():
        lines.append(f"===== {batch_title} | {topic} =====")
        for title, url in items:
            safe_title = title.replace(":", " -").strip()
            lines.append(f"{safe_title}:{url}")
        lines.append("")

    return "
".join(lines).strip() + "
"


def render_progress_bar(pct: float, length: int = 16) -> str:
    clamped = max(0.0, min(100.0, pct))
    filled = int(round((clamped / 100.0) * length))
    return "▓" * filled + "░" * (length - filled)


async def perform_login(session: UserSession, password: str) -> Tuple[bool, str]:
    profile = get_active_profile(session)
    if not profile:
        return False, "Pehle `/institute` se koi institute select karein."
    if not session.mobile_or_id:
        return False, "Mobile number / ID missing hai."

    login_url = urljoin(profile.base_url + "/", profile.login_path.lstrip("/"))
    payload = {
        profile.id_field: session.mobile_or_id,
        profile.password_field: password,
        "mobile": session.mobile_or_id,
        "phone": session.mobile_or_id,
        "email": session.mobile_or_id,
        "password": password,
    }
    headers = build_headers(profile)

    timeout = aiohttp.ClientTimeout(total=25)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.post(login_url, json=payload, headers=headers) as resp:
                text = await resp.text()
                if resp.status not in (200, 201):
                    return False, f"Login failed (HTTP {resp.status}). Endpoint ya credentials check karein."
                try:
                    data = json.loads(text)
                except Exception:
                    return False, "Login response valid JSON nahi mila."

                token = extract_token_from_payload(data)
                if not token:
                    msg = str(data.get("message") or data.get("msg") or data.get("error") or "Token response mein nahi mila.")
                    return False, f"Login safal nahi hua: {msg}"

                session.token = token
                session.user_info = data if isinstance(data, dict) else {}
                return True, "Login successful! Session token save ho gaya."
    except asyncio.TimeoutError:
        return False, "Login request timeout ho gayi. Dobara try karein."
    except Exception as exc:
        logger.exception("Login error")
        return False, f"Login error: {exc}"


async def fetch_user_batches(session: UserSession) -> Tuple[bool, str]:
    profile = get_active_profile(session)
    if not profile or not session.token:
        return False, "Pehle `/login` se login karein."

    batches_url = urljoin(profile.base_url + "/", profile.batches_path.lstrip("/"))
    headers = build_headers(profile, session.token)
    timeout = aiohttp.ClientTimeout(total=25)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.get(batches_url, headers=headers) as resp:
                if resp.status == 405:
                    async with http.post(batches_url, headers=headers) as post_resp:
                        text = await post_resp.text()
                        status_code = post_resp.status
                else:
                    text = await resp.text()
                    status_code = resp.status

                if status_code != 200:
                    return False, f"Batches fetch nahi ho sake (HTTP {status_code})."
                try:
                    data = json.loads(text)
                except Exception:
                    return False, "Batches response valid JSON nahi tha."

                batches = normalize_batches(data)
                session.batches = batches
                if not batches:
                    return False, "Is account mein koi enrolled batch nahi mila."
                return True, f"{len(batches)} batches mil gaye."
    except asyncio.TimeoutError:
        return False, "Batches fetch karte waqt timeout aa gaya."
    except Exception as exc:
        logger.exception("Fetch batches error")
        return False, f"Batches error: {exc}"


async def run_batch_extraction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session: UserSession,
    batch_id: str,
    batch_title: str,
) -> None:
    profile = get_active_profile(session)
    if not profile or not session.token:
        return

    chat_id = update.effective_chat.id
    session.cancel_flag = False
    start_time = time.monotonic()

    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "⚡ <b>EXTRACTION LIVE STATUS</b>

"
            f"🏫 <b>Institute:</b> {profile.short_label}
"
            f"📚 <b>Batch:</b> {batch_title}
"
            f"📊 <b>Progress:</b> [{render_progress_bar(0)}] 0.0%
"
            "⏳ <b>Status:</b> Batch data fetch ho raha hai..."
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🛑 Stop Process", callback_data="stop_extract")]]
        ),
    )

    rel_path = profile.contents_path.format(batch_id=batch_id)
    contents_url = urljoin(profile.base_url + "/", rel_path.lstrip("/"))
    headers = build_headers(profile, session.token)
    collected: List[Tuple[str, str, str]] = []
    skipped_masked = 0
    last_edit = 0.0

    async def update_live(pct: float, step_text: str, force: bool = False) -> None:
        nonlocal last_edit
        now = time.monotonic()
        if not force and (now - last_edit) < 1.2:
            return
        last_edit = now
        elapsed = max(1, int(now - start_time))
        videos_cnt = sum(1 for _, t, u in collected if "pdf" not in t.lower() and not u.lower().endswith(".pdf"))
        pdfs_cnt = len(collected) - videos_cnt
        bar = render_progress_bar(pct)
        text = (
            "⚡ <b>EXTRACTION LIVE STATUS</b>

"
            f"🏫 <b>Institute:</b> {profile.short_label}
"
            f"📚 <b>Batch:</b> {batch_title}
"
            f"📌 <b>Current:</b> {step_text[:80]}
"
            f"📊 <b>Progress:</b> [{bar}] {pct:.1f}%
"
            f"🎬 <b>Videos:</b> {videos_cnt} | 📄 <b>PDFs:</b> {pdfs_cnt}
"
            f"🚫 <b>Masked Skipped:</b> {skipped_masked}
"
            f"⏱ <b>Elapsed:</b> {elapsed}s"
        )
        markup = (
            InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Stop Process", callback_data="stop_extract")]])
            if pct < 100.0 and not session.cancel_flag
            else None
        )
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
        except Exception:
            pass

    timeout = aiohttp.ClientTimeout(total=40)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http:
            await update_live(15.0, "API endpoint se lectures/topics fetch ho rahe hain...", force=True)
            async with http.get(contents_url, headers=headers) as resp:
                if resp.status != 200:
                    await update_live(100.0, f"Failed: HTTP {resp.status}", force=True)
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"❌ Batch contents fetch nahi ho sake (HTTP {resp.status}).",
                    )
                    return
                raw_text = await resp.text()
                try:
                    payload = json.loads(raw_text)
                except Exception:
                    await update_live(100.0, "Failed: Invalid JSON", force=True)
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="❌ Server se valid JSON response nahi aaya.",
                    )
                    return

            await update_live(55.0, "Topics aur clean URLs parse kiye ja rahe hain...", force=True)

            raw_entries: List[Tuple[str, str, str]] = []
            collect_media_entries(payload, [batch_title], raw_entries, set())

            total_raw = max(1, len(raw_entries))
            seen_urls: set[str] = set()

            for idx, (topic, title, url) in enumerate(raw_entries, start=1):
                if session.cancel_flag:
                    await update_live(
                        (idx / total_raw) * 100.0,
                        "Process user ne stop kar diya.",
                        force=True,
                    )
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="🛑 Extraction rok diya gaya hai.",
                    )
                    return

                if not is_usable_url(url):
                    skipped_masked += 1
                    continue

                if url in seen_urls:
                    continue
                seen_urls.add(url)
                collected.append((topic, title, url))

                if idx % 10 == 0 or idx == total_raw:
                    pct = 55.0 + ((idx / total_raw) * 40.0)
                    await update_live(pct, f"{title}")

            if not collected:
                await update_live(100.0, "Koi clean downloadable link nahi mila.", force=True)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "⚠️ Is batch response se koi clean video/PDF link nahi mila "
                        f"(Masked/invalid skipped: {skipped_masked})."
                    ),
                )
                return

            txt_content = format_txt_output(
                institute_name=profile.name,
                batch_title=batch_title,
                credit=session.credit,
                entries=collected,
            )
            clean_filename = "".join(
                c if c.isalnum() or c in (" ", "_", "-") else "_" for c in batch_title
            ).strip().replace(" ", "_")
            filename = f"{clean_filename or 'extracted_batch'}.txt"

            bio = io.BytesIO(txt_content.encode("utf-8"))
            bio.name = filename

            await update_live(100.0, "TXT file ready! Telegram par bheji ja rahi hai...", force=True)
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
            await context.bot.send_document(
                chat_id=chat_id,
                document=bio,
                caption=(
                    f"✅ <b>Extraction Complete!</b>

"
                    f"🏫 <b>Institute:</b> {profile.short_label}
"
                    f"📚 <b>Batch:</b> {batch_title}
"
                    f"🔗 <b>Clean Links:</b> {len(collected)}
"
                    f"🚫 <b>Masked Skipped:</b> {skipped_masked}
"
                    f"{session.credit}"
                ),
                parse_mode=ParseMode.HTML,
            )
    except asyncio.CancelledError:
        await context.bot.send_message(chat_id=chat_id, text="🛑 Extraction task cancel kar diya gaya.")
    except Exception as exc:
        logger.exception("Extraction failed")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Extraction error: {exc}")
    finally:
        session.extract_task = None
        session.cancel_flag = False


def home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🏫 Select Institute", callback_data="menu_institutes"),
                InlineKeyboardButton("🔐 Login", callback_data="start_login"),
            ],
            [
                InlineKeyboardButton("📚 My Batches", callback_data="list_batches"),
                InlineKeyboardButton("📄 Extract TXT", callback_data="quick_extract"),
            ],
            [
                InlineKeyboardButton("📊 Status", callback_data="show_status"),
                InlineKeyboardButton("🚪 Logout", callback_data="do_logout"),
            ],
        ]
    )


def institutes_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⚡ PW", callback_data="inst_pw"),
                InlineKeyboardButton("🎯 RWA", callback_data="inst_rwa"),
            ],
            [
                InlineKeyboardButton("📘 Target Board", callback_data="inst_target_board"),
                InlineKeyboardButton("🎓 Disha Online", callback_data="inst_disha_online"),
            ],
            [
                InlineKeyboardButton("➕ Custom URL", callback_data="inst_custom"),
            ],
            [
                InlineKeyboardButton("⬅️ Back to Home", callback_data="go_home"),
            ],
        ]
    )


def format_home_text(session: UserSession) -> str:
    profile = get_active_profile(session)
    inst_text = profile.name if profile else "Not Selected"
    login_text = f"Logged In ({session.mobile_or_id})" if session.token else "Not Logged In"
    batch_text = session.selected_batch["title"] if session.selected_batch else "Not Selected"
    return (
        "⚡ <b>SANSKARI EXTRACTOR BOT</b>
"
        f"<i>{session.credit}</i>

"
        f"🏫 <b>Institute:</b> {inst_text}
"
        f"🔐 <b>Auth Status:</b> {login_text}
"
        f"📚 <b>Selected Batch:</b> {batch_text}

"
        "<b>Available Commands:</b>
"
        "• /start — Home screen & main buttons
"
        "• /institute — Institute buttons (PW, RWA, Target Board, Disha Online, Custom)
"
        "• /login — Mobile / ID & Password login
"
        "• /batches — Enrolled courses ki list
"
        "• /extract — Selected batch se TXT nikalna
"
        "• /setcredit &lt;text&gt; — Credit update karna
"
        "• /status — Current status dekhna
"
        "• /logout — Session clear karna
"
        "• /stop — Running task rokna"
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    session = get_session(update.effective_user.id)
    session.awaiting_state = None
    await update.message.reply_text(
        format_home_text(session),
        parse_mode=ParseMode.HTML,
        reply_markup=home_keyboard(),
    )


async def cmd_institute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    await update.message.reply_text(
        "🏫 <b>Apna Institute Select Karein:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=institutes_keyboard(),
    )


async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    session = get_session(update.effective_user.id)
    profile = get_active_profile(session)
    if not profile:
        await update.message.reply_text(
            "⚠️ Pehle institute select karein:",
            reply_markup=institutes_keyboard(),
        )
        return
    session.awaiting_state = "login_id"
    await update.message.reply_text(
        f"🔐 <b>{profile.name} Login</b>

Apna <b>Mobile Number / Email / User ID</b> bhejein:",
        parse_mode=ParseMode.HTML,
    )


async def cmd_batches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    session = get_session(update.effective_user.id)
    ok, info = await fetch_user_batches(session)
    if not ok:
        await update.message.reply_text(f"❌ {info}")
        return
    buttons = [
        [InlineKeyboardButton(f"📚 {b['title'][:45]}", callback_data=f"batch_{idx}")]
        for idx, b in enumerate(session.batches[:30])
    ]
    await update.message.reply_text(
        f"✅ <b>{info}</b>
Kisi bhi batch par click karke TXT extract karein:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def cmd_extract(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    session = get_session(update.effective_user.id)
    if not session.token:
        await update.message.reply_text("❌ Pehle `/login` se login karein.")
        return
    if not session.selected_batch:
        await cmd_batches(update, context)
        return
    if session.extract_task and not session.extract_task.done():
        await update.message.reply_text("⚠️ Ek extraction task pehle se chal raha hai. `/stop` se rok sakte hain.")
        return
    session.extract_task = asyncio.create_task(
        run_batch_extraction(
            update,
            context,
            session,
            session.selected_batch["id"],
            session.selected_batch["title"],
        )
    )


async def cmd_setcredit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    session = get_session(update.effective_user.id)
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Use: `/setcredit ✦ Extracted By: 𝐌𝐫. 𝐒𝐚𝐧𝐬𝐤𝐚𝐫𝐢 ✦`", parse_mode=ParseMode.MARKDOWN)
        return
    session.credit = text
    await update.message.reply_text(f"✅ Credit set ho gaya:
{session.credit}")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    session = get_session(update.effective_user.id)
    await update.message.reply_text(format_home_text(session), parse_mode=ParseMode.HTML, reply_markup=home_keyboard())


async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    session = get_session(update.effective_user.id)
    session.token = None
    session.batches = []
    session.selected_batch = None
    session.awaiting_state = None
    await update.message.reply_text("🚪 Session logout ho gaya.", reply_markup=home_keyboard())


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    session = get_session(update.effective_user.id)
    session.cancel_flag = True
    if session.extract_task and not session.extract_task.done():
        session.extract_task.cancel()
    await update.message.reply_text("🛑 Stop signal bhej diya gaya hai.")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not is_authorized(query.from_user.id):
        return
    await query.answer()

    session = get_session(query.from_user.id)
    data = query.data or ""

    if data == "go_home":
        session.awaiting_state = None
        await query.edit_message_text(
            format_home_text(session),
            parse_mode=ParseMode.HTML,
            reply_markup=home_keyboard(),
        )
    elif data == "menu_institutes":
        await query.edit_message_text(
            "🏫 <b>Apna Institute Select Karein:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=institutes_keyboard(),
        )
    elif data.startswith("inst_"):
        key = data.replace("inst_", "", 1)
        if key == "custom":
            session.institute_key = "custom"
            session.awaiting_state = "custom_url"
            await query.edit_message_text(
                "➕ <b>Custom API Base URL Bhejein:</b>

"
                "Example: <code>https://example-api.classx.co.in</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        if key in INSTITUTES:
            session.institute_key = key
            session.token = None
            session.batches = []
            session.selected_batch = None
            session.awaiting_state = "login_id"
            prof = INSTITUTES[key]
            await query.edit_message_text(
                f"✅ <b>{prof.name}</b> select ho gaya!

"
                "Ab apna <b>Mobile Number / Email / ID</b> bhejein:",
                parse_mode=ParseMode.HTML,
            )
    elif data == "start_login":
        profile = get_active_profile(session)
        if not profile:
            await query.edit_message_text(
                "⚠️ Pehle institute select karein:",
                reply_markup=institutes_keyboard(),
            )
            return
        session.awaiting_state = "login_id"
        await query.edit_message_text(
            f"🔐 <b>{profile.name} Login</b>

Apna <b>Mobile Number / Email / ID</b> bhejein:",
            parse_mode=ParseMode.HTML,
        )
    elif data == "list_batches":
        ok, info = await fetch_user_batches(session)
        if not ok:
            await query.edit_message_text(f"❌ {info}", reply_markup=home_keyboard())
            return
        buttons = [
            [InlineKeyboardButton(f"📚 {b['title'][:45]}", callback_data=f"batch_{idx}")]
            for idx, b in enumerate(session.batches[:30])
        ]
        buttons.append([InlineKeyboardButton("⬅️ Back to Home", callback_data="go_home")])
        await query.edit_message_text(
            f"✅ <b>{info}</b>
Kisi batch par click karte hi extraction shuru hoga:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    elif data.startswith("batch_"):
        idx_str = data.replace("batch_", "", 1)
        if not idx_str.isdigit():
            return
        idx = int(idx_str)
        if idx < 0 or idx >= len(session.batches):
            await query.edit_message_text("⚠️ Batch list purani ho gayi hai. `/batches` dobara chalayein.")
            return
        chosen = session.batches[idx]
        session.selected_batch = chosen
        if session.extract_task and not session.extract_task.done():
            await query.edit_message_text("⚠️ Ek extraction pehle se chal raha hai.")
            return
        session.extract_task = asyncio.create_task(
            run_batch_extraction(update, context, session, chosen["id"], chosen["title"])
        )
    elif data == "quick_extract":
        if not session.token:
            await query.edit_message_text("❌ Pehle login karein.", reply_markup=home_keyboard())
            return
        if not session.selected_batch:
            await query.edit_message_text("⚠️ Pehle `My Batches` se batch chunein.", reply_markup=home_keyboard())
            return
        if session.extract_task and not session.extract_task.done():
            await query.edit_message_text("⚠️ Extraction pehle se chal raha hai.")
            return
        session.extract_task = asyncio.create_task(
            run_batch_extraction(
                update,
                context,
                session,
                session.selected_batch["id"],
                session.selected_batch["title"],
            )
        )
    elif data == "show_status":
        await query.edit_message_text(
            format_home_text(session),
            parse_mode=ParseMode.HTML,
            reply_markup=home_keyboard(),
        )
    elif data == "do_logout":
        session.token = None
        session.batches = []
        session.selected_batch = None
        session.awaiting_state = None
        await query.edit_message_text("🚪 Logout ho gaya.", reply_markup=home_keyboard())
    elif data == "stop_extract":
        session.cancel_flag = True
        if session.extract_task and not session.extract_task.done():
            session.extract_task.cancel()


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text or not is_authorized(update.effective_user.id):
        return
    session = get_session(update.effective_user.id)
    text = update.message.text.strip()

    if session.awaiting_state == "custom_url":
        if not text.startswith(("http://", "https://")):
            await update.message.reply_text("⚠️ Valid `https://` URL bhejein.")
            return
        session.custom_base_url = text.rstrip("/")
        session.awaiting_state = "login_id"
        await update.message.reply_text(
            f"✅ Custom URL set: <code>{session.custom_base_url}</code>

"
            "Ab apna <b>Mobile Number / Email / ID</b> bhejein:",
            parse_mode=ParseMode.HTML,
        )
        return

    if session.awaiting_state == "login_id":
        session.temp_login_id = text
        session.awaiting_state = "login_password"
        await update.message.reply_text("🔑 Ab apna <b>Password</b> bhejein:", parse_mode=ParseMode.HTML)
        return

    if session.awaiting_state == "login_password":
        password = text
        session.awaiting_state = None
        try:
            await update.message.delete()
        except Exception:
            pass

        session.mobile_or_id = session.temp_login_id
        session.temp_login_id = None

        wait_msg = await update.message.reply_text("⏳ Login verify ho raha hai...")
        ok, msg = await perform_login(session, password)
        if not ok:
            await wait_msg.edit_text(f"❌ {msg}", reply_markup=home_keyboard())
            return

        b_ok, b_msg = await fetch_user_batches(session)
        if not b_ok:
            await wait_msg.edit_text(f"✅ {msg}
⚠️ {b_msg}", reply_markup=home_keyboard())
            return

        buttons = [
            [InlineKeyboardButton(f"📚 {b['title'][:45]}", callback_data=f"batch_{idx}")]
            for idx, b in enumerate(session.batches[:30])
        ]
        buttons.append([InlineKeyboardButton("🏠 Home", callback_data="go_home")])
        await wait_msg.edit_text(
            f"✅ <b>{msg}</b>
📚 <b>{b_msg}</b>

Kisi course/batch par click karein:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    await update.message.reply_text(
        format_home_text(session),
        parse_mode=ParseMode.HTML,
        reply_markup=home_keyboard(),
    )


async def start_health_server() -> Optional[asyncio.AbstractServer]:
    async def reader_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.read(1024)
            body = b"OK"
            headers = (
                b"HTTP/1.1 200 OK
"
                b"Content-Type: text/plain
"
                + f"Content-Length: {len(body)}
".encode("ascii")
                + b"Connection: close

"
            )
            writer.write(headers + body)
            await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    try:
        server = await asyncio.start_server(reader_handler, host="0.0.0.0", port=PORT)
        logger.info("Health check server listening on 0.0.0.0:%s", PORT)
        return server
    except Exception as exc:
        logger.warning("Health server start skipped: %s", exc)
        return None


async def run_bot() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable set nahi hai.")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("institute", cmd_institute))
    app.add_handler(CommandHandler("login", cmd_login))
    app.add_handler(CommandHandler("batches", cmd_batches))
    app.add_handler(CommandHandler("extract", cmd_extract))
    app.add_handler(CommandHandler("setcredit", cmd_setcredit))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("logout", cmd_logout))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    server = await start_health_server()
    async with app:
        await app.initialize()
        await app.bot.delete_webhook(drop_pending_updates=True)
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logger.info("Sanskari Extractor Bot polling started.")
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            if server:
                server.close()
                await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(run_bot())
