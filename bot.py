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
    "disha_online": InstituteProfile(
        key="disha_online",
        name="Disha Online Classes",
        short_label="Dishaonlineclasses",
        base_url="https://dishaonlineclassesapi.classx.co.in",
        login_path="/post/userLogin",
        batches_path="/get/mycourse",
        contents_path="/get/allsubjectfrmlivecourseclass?courseid={batch_id}",
        id_field="email",
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
}


@dataclass
class UserSession:
    institute_key: Optional[str] = "disha_online"
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
    target_channel_id: Optional[str] = None
    target_topic_id: Optional[int] = None
    include_masked_links: bool = True
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
    return INSTITUTES["disha_online"]


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
    for k in ["token", "access_token", "accessToken", "auth_token", "jwt", "session_token"]:
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


def is_http_url(url: str) -> bool:
    u = (url or "").strip()
    return u.startswith(("http://", "https://"))


def is_masked_or_invalid_url(url: str) -> bool:
    u = (url or "").strip().lower()
    return not u.startswith(("http://", "https://")) or "sealed.invalid" in u or u.endswith(".invalid")


def collect_media_entries(
    node: Any,
    topic_stack: List[str],
    entries: List[Tuple[str, str, str]],
    visited_ids: set[int],
    include_masked: bool = True,
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
            collect_media_entries(elem, topic_stack, entries, visited_ids, include_masked, depth + 1)
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
                if isinstance(val, str) and is_http_url(val):
                    clean_url = val.strip()
                    if is_masked_or_invalid_url(clean_url) and not include_masked:
                        continue
                    lower_u = clean_url.lower()
                    if kind == "video" and (".pdf" in lower_u or "test" in k.lower() or "quiz" in k.lower()):
                        continue
                    title_out = f"{base_title} PDF" if kind == "pdf" and "pdf" not in base_title.lower() else base_title
                    entries.append((topic_name, title_out, clean_url))
                    break

        for val in node.values():
            if isinstance(val, (dict, list)):
                collect_media_entries(val, next_stack, entries, visited_ids, include_masked, depth + 1)


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

    return "\n".join(lines).strip() + "\n"


def render_progress_bar(pct: float, length: int = 16) -> str:
    clamped = max(0.0, min(100.0, pct))
    filled = int(round((clamped / 100.0) * length))
    return "▓" * filled + "░" * (length - filled)


def parse_credential_input(raw_text: str) -> Tuple[Optional[str], Optional[str]]:
    cleaned = raw_text.strip()
    if "*" in cleaned:
        left, right = cleaned.split("*", 1)
        if left.strip() and right.strip():
            return left.strip(), right.strip()
    return None, None


async def perform_login(session: UserSession, login_id: str, password: str) -> Tuple[bool, str]:
    profile = get_active_profile(session)
    if not profile:
        return False, "Pehle platform/institute select karein."
    if not login_id or not password:
        return False, "ID/Mobile aur Password dono zaroori hain."

    session.mobile_or_id = login_id
    login_url = urljoin(profile.base_url + "/", profile.login_path.lstrip("/"))
    payload = {
        profile.id_field: login_id,
        profile.password_field: password,
        "mobile": login_id,
        "phone": login_id,
        "email": login_id,
        "username": login_id,
        "password": password,
    }
    headers = build_headers(profile)

    timeout = aiohttp.ClientTimeout(total=25)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.post(login_url, json=payload, headers=headers) as resp:
                text = await resp.text()
                if resp.status not in (200, 201):
                    return False, f"Login failed (HTTP {resp.status}) on {profile.base_url}."
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
                return True, f"Login successful ({profile.short_label})!"
    except asyncio.TimeoutError:
        return False, "Login request timeout ho gayi. Dobara try karein."
    except Exception as exc:
        logger.exception("Login error")
        return False, f"Login error: {exc}"


async def fetch_user_batches(session: UserSession) -> Tuple[bool, str]:
    profile = get_active_profile(session)
    if not profile or not session.token:
        return False, "Pehle login karein (ID*Password ya step-by-step)."

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


def resolve_target_chat(session: UserSession, fallback_chat_id: int) -> Tuple[int, Optional[int], bool]:
    if session.target_channel_id:
        raw_ch = session.target_channel_id.strip()
        try:
            chat_target: Any = int(raw_ch) if raw_ch.lstrip("-").isdigit() else raw_ch
            return chat_target, session.target_topic_id, True
        except Exception:
            return fallback_chat_id, None, False
    return fallback_chat_id, None, False


async def run_batch_extraction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session: UserSession,
    batch_id: str,
    batch_title: str,
    mode: str = "txt",
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
            "⚡ <b>EXTRACTION LIVE STATUS</b>\n\n"
            f"🏫 <b>Platform:</b> {profile.short_label}\n"
            f"📚 <b>Course:</b> {batch_title}\n"
            f"📊 <b>Progress:</b> [{render_progress_bar(0)}] 0.0%\n"
            "⏳ <b>Status:</b> Catalog & content fetch ho raha hai..."
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
    masked_count = 0
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
            "⚡ <b>EXTRACTION LIVE STATUS</b>\n\n"
            f"🏫 <b>Platform:</b> {profile.short_label}\n"
            f"📚 <b>Course:</b> {batch_title}\n"
            f"📌 <b>Current:</b> {step_text[:80]}\n"
            f"📊 <b>Progress:</b> [{bar}] {pct:.1f}%\n"
            f"🎬 <b>Videos:</b> {videos_cnt} | 📄 <b>PDFs:</b> {pdfs_cnt}\n"
            f"🔗 <b>Sealed/Masked:</b> {masked_count}\n"
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

    timeout = aiohttp.ClientTimeout(total=45)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http:
            await update_live(15.0, "API endpoint se topics/lectures fetch ho rahe hain...", force=True)
            async with http.get(contents_url, headers=headers) as resp:
                if resp.status != 200:
                    await update_live(100.0, f"Failed: HTTP {resp.status}", force=True)
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"❌ Course contents fetch nahi ho sake (HTTP {resp.status}).",
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

            await update_live(50.0, "Topics aur links parse kiye ja rahe hain...", force=True)

            raw_entries: List[Tuple[str, str, str]] = []
            collect_media_entries(
                payload,
                [batch_title],
                raw_entries,
                set(),
                include_masked=session.include_masked_links,
            )

            total_raw = max(1, len(raw_entries))
            seen_urls: set[str] = set()

            for idx, (topic, title, url) in enumerate(raw_entries, start=1):
                if session.cancel_flag:
                    await update_live((idx / total_raw) * 100.0, "Stopped by user.", force=True)
                    await context.bot.send_message(chat_id=chat_id, text="🛑 Extraction rok diya gaya hai.")
                    return

                if is_masked_or_invalid_url(url):
                    masked_count += 1

                dedup_key = f"{title}|{url}"
                if dedup_key in seen_urls:
                    continue
                seen_urls.add(dedup_key)
                collected.append((topic, title, url))

                if idx % 10 == 0 or idx == total_raw:
                    pct = 50.0 + ((idx / total_raw) * 35.0)
                    await update_live(pct, f"{title}")

            if not collected:
                await update_live(100.0, "Koi link nahi mila.", force=True)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="⚠️ Is course response se koi video/PDF link nahi mila.",
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
            filename = f"{clean_filename or 'course_export'}.txt"
            bio = io.BytesIO(txt_content.encode("utf-8"))
            bio.name = filename

            dest_chat, dest_topic, custom_dest = resolve_target_chat(session, chat_id)

            if mode == "deliver" and custom_dest:
                await update_live(88.0, "Target Channel / Group Topic mein deliver ho raha hai...", force=True)
                header_line = (
                    f"📚 <b>{batch_title}</b>\n"
                    f"🏫 <b>Platform:</b> {profile.short_label}\n"
                    f"🔗 <b>Total Items:</b> {len(collected)}\n"
                    f"{session.credit}"
                )
                await context.bot.send_message(
                    chat_id=dest_chat,
                    message_thread_id=dest_topic,
                    text=header_line,
                    parse_mode=ParseMode.HTML,
                )

                sent_items = 0
                for idx, (topic, title, url) in enumerate(collected, start=1):
                    if session.cancel_flag:
                        await context.bot.send_message(chat_id=chat_id, text="🛑 Delivery beech mein rok di gayi.")
                        return
                    safe_title = title.replace("<", "&lt;").replace(">", "&gt;")
                    safe_topic = topic.replace("<", "&lt;").replace(">", "&gt;")
                    item_msg = (
                        f"📌 <b>{safe_title}</b>\n"
                        f"📂 <i>{safe_topic}</i>\n"
                        f"🔗 {url}"
                    )
                    try:
                        await context.bot.send_message(
                            chat_id=dest_chat,
                            message_thread_id=dest_topic,
                            text=item_msg,
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True,
                        )
                        sent_items += 1
                    except Exception:
                        pass
                    if idx % 15 == 0 or idx == len(collected):
                        pct = 88.0 + ((idx / len(collected)) * 10.0)
                        await update_live(pct, f"Delivered {sent_items}/{len(collected)}")

            await update_live(100.0, "TXT file bheji ja rahi hai...", force=True)
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
            await context.bot.send_document(
                chat_id=chat_id,
                document=bio,
                caption=(
                    f"✅ <b>Course Export Complete!</b>\n\n"
                    f"🏫 <b>Platform:</b> {profile.short_label}\n"
                    f"📚 <b>Course:</b> {batch_title}\n"
                    f"🔗 <b>Total Links:</b> {len(collected)} (Sealed/Masked: {masked_count})\n"
                    f"📤 <b>Target Delivery:</b> {'Configured Channel/Topic' if (mode == 'deliver' and custom_dest) else 'TXT File Only'}\n"
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


def home_keyboard(session: UserSession) -> InlineKeyboardMarkup:
    profile = get_active_profile(session)
    inst_label = profile.short_label if profile else "Dishaonlineclasses"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(f"🏫 Platform: {inst_label}", callback_data="menu_institutes"),
                InlineKeyboardButton("🔐 Login (ID*Pass)", callback_data="start_login"),
            ],
            [
                InlineKeyboardButton("📚 My Courses", callback_data="list_batches"),
                InlineKeyboardButton("📄 Export TXT", callback_data="export_txt_now"),
            ],
            [
                InlineKeyboardButton("📣 Set Channel/Topic", callback_data="set_dest_help"),
                InlineKeyboardButton("🚀 Deliver to Channel", callback_data="deliver_now"),
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
                InlineKeyboardButton("🎓 Dishaonlineclasses", callback_data="inst_disha_online"),
                InlineKeyboardButton("📘 Target Board", callback_data="inst_target_board"),
            ],
            [
                InlineKeyboardButton("⚡ PW", callback_data="inst_pw"),
                InlineKeyboardButton("🎯 RWA", callback_data="inst_rwa"),
            ],
            [
                InlineKeyboardButton("➕ Custom API", callback_data="inst_custom"),
                InlineKeyboardButton("⬅️ Main Menu", callback_data="go_home"),
            ],
        ]
    )


def batch_action_keyboard(batch_idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📄 Export Course File (.TXT)", callback_data=f"run_txt_{batch_idx}"),
            ],
            [
                InlineKeyboardButton("🚀 Export TXT + Channel/Topic Delivery", callback_data=f"run_deliv_{batch_idx}"),
            ],
            [
                InlineKeyboardButton("📚 Back to Courses", callback_data="list_batches"),
                InlineKeyboardButton("🏠 Main Menu", callback_data="go_home"),
            ],
        ]
    )


def format_home_text(session: UserSession) -> str:
    profile = get_active_profile(session)
    inst_text = f"{profile.short_label} ({profile.base_url})" if profile else "Not Selected"
    login_text = f"Logged In ({session.mobile_or_id})" if session.token else "Not Logged In"
    batch_text = session.selected_batch["title"] if session.selected_batch else "Not Selected"
    dest_text = session.target_channel_id or "Not Set"
    if session.target_channel_id and session.target_topic_id:
        dest_text = f"{session.target_channel_id} (Topic #{session.target_topic_id})"
    masked_mode = "Included (TXT Export)" if session.include_masked_links else "Skipped"

    return (
        "⚡ <b>SANSKARI COURSE EXTRACTOR &amp; DELIVERY BOT</b>\n"
        f"<i>{session.credit}</i>\n\n"
        f"🏫 <b>Platform:</b> {inst_text}\n"
        f"🔐 <b>Auth Status:</b> {login_text}\n"
        f"📚 <b>Selected Course:</b> {batch_text}\n"
        f"📣 <b>Target Destination:</b> {dest_text}\n"
        f"🔗 <b>Sealed/Masked Links:</b> {masked_mode}\n\n"
        "<b>Commands:</b>\n"
        "• /start — Main menu &amp; buttons\n"
        "• /platform — Platform/Institute select karein\n"
        "• /login — <code>ID*Password</code> ya step-by-step login\n"
        "• /courses — Enrolled courses list\n"
        "• /export — Selected course ka <code>.txt</code> export\n"
        "• /channel &lt;id&gt; — Target channel set karein\n"
        "• /topic &lt;channel_id&gt; &lt;thread_id&gt; — Forum group topic set karein\n"
        "• /deliver — Course TXT + Channel/Topic delivery\n"
        "• /masked — Toggle sealed/masked links in TXT\n"
        "• /setcredit &lt;text&gt; — Credit text update karein\n"
        "• /status &amp; /logout &amp; /stop"
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
        reply_markup=home_keyboard(session),
    )


async def cmd_platform(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    await update.message.reply_text(
        "🏫 <b>Platform / Institute Select Karein:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=institutes_keyboard(),
    )


async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    session = get_session(update.effective_user.id)
    profile = get_active_profile(session)
    if context.args:
        combo = " ".join(context.args).strip()
        uid, pwd = parse_credential_input(combo)
        if uid and pwd:
            wait_msg = await update.message.reply_text(f"🔐 Signing in to <b>{profile.short_label}</b>...", parse_mode=ParseMode.HTML)
            ok, msg = await perform_login(session, uid, pwd)
            if ok:
                b_ok, b_msg = await fetch_user_batches(session)
                await wait_msg.edit_text(f"✅ {msg}\n📚 {b_msg}", reply_markup=home_keyboard(session))
            else:
                await wait_msg.edit_text(f"❌ {msg}", reply_markup=home_keyboard(session))
            return

    session.awaiting_state = "login_combo_or_id"
    await update.message.reply_text(
        f"🔐 <b>Login — {profile.short_label}</b>\n"
        f"<code>{profile.base_url}</code>\n\n"
        "Send <b>ID*Password</b> (example: <code>your_id*your_password</code>)\n"
        "ya sirf <b>Mobile / Email / ID</b> bhejein:",
        parse_mode=ParseMode.HTML,
    )


async def cmd_courses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    session = get_session(update.effective_user.id)
    ok, info = await fetch_user_batches(session)
    if not ok:
        await update.message.reply_text(f"❌ {info}")
        return
    buttons = [
        [InlineKeyboardButton(f"📚 {b['title'][:45]}", callback_data=f"pick_batch_{idx}")]
        for idx, b in enumerate(session.batches[:30])
    ]
    await update.message.reply_text(
        f"✅ <b>{info}</b>\nCourse select karein:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    session = get_session(update.effective_user.id)
    if not session.token:
        await update.message.reply_text("❌ Pehle `/login` se login karein.")
        return
    if not session.selected_batch:
        await cmd_courses(update, context)
        return
    if session.extract_task and not session.extract_task.done():
        await update.message.reply_text("⚠️ Ek task pehle se chal raha hai. `/stop` se rok sakte hain.")
        return
    session.extract_task = asyncio.create_task(
        run_batch_extraction(
            update,
            context,
            session,
            session.selected_batch["id"],
            session.selected_batch["title"],
            mode="txt",
        )
    )


async def cmd_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    session = get_session(update.effective_user.id)
    if not context.args:
        session.awaiting_state = "set_channel"
        await update.message.reply_text(
            "📣 Target Channel ID ya `@username` bhejein (example: `-1001234567890`):"
        )
        return
    session.target_channel_id = context.args[0].strip()
    session.target_topic_id = None
    await update.message.reply_text(f"✅ Target channel set: <code>{session.target_channel_id}</code>", parse_mode=ParseMode.HTML)


async def cmd_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    session = get_session(update.effective_user.id)
    if len(context.args) >= 2 and context.args[1].isdigit():
        session.target_channel_id = context.args[0].strip()
        session.target_topic_id = int(context.args[1].strip())
        await update.message.reply_text(
            f"✅ Group Topic set: <code>{session.target_channel_id}</code> | Topic #{session.target_topic_id}",
            parse_mode=ParseMode.HTML,
        )
        return
    session.awaiting_state = "set_topic"
    await update.message.reply_text(
        "🧵 Group ID aur Topic Thread ID bhejein format mein:\n<code>-1001234567890 42</code>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_deliver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    session = get_session(update.effective_user.id)
    if not session.token:
        await update.message.reply_text("❌ Pehle login karein.")
        return
    if not session.selected_batch:
        await cmd_courses(update, context)
        return
    if not session.target_channel_id:
        await update.message.reply_text("⚠️ Pehle `/channel` ya `/topic` se target destination set karein.")
        return
    if session.extract_task and not session.extract_task.done():
        await update.message.reply_text("⚠️ Ek task pehle se chal raha hai.")
        return
    session.extract_task = asyncio.create_task(
        run_batch_extraction(
            update,
            context,
            session,
            session.selected_batch["id"],
            session.selected_batch["title"],
            mode="deliver",
        )
    )


async def cmd_masked(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    session = get_session(update.effective_user.id)
    session.include_masked_links = not session.include_masked_links
    state_txt = "ON (Sealed/Masked links included in TXT)" if session.include_masked_links else "OFF (Only non-masked URLs)"
    await update.message.reply_text(f"🔗 Masked links mode: <b>{state_txt}</b>", parse_mode=ParseMode.HTML)


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
    await update.message.reply_text(f"✅ Credit set ho gaya:\n{session.credit}")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    session = get_session(update.effective_user.id)
    await update.message.reply_text(format_home_text(session), parse_mode=ParseMode.HTML, reply_markup=home_keyboard(session))


async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
    session = get_session(update.effective_user.id)
    session.token = None
    session.batches = []
    session.selected_batch = None
    session.awaiting_state = None
    await update.message.reply_text("🚪 Session logout ho gaya.", reply_markup=home_keyboard(session))


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
            reply_markup=home_keyboard(session),
        )
    elif data == "menu_institutes":
        await query.edit_message_text(
            "🏫 <b>Platform / Institute Select Karein:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=institutes_keyboard(),
        )
    elif data.startswith("inst_"):
        key = data.replace("inst_", "", 1)
        if key == "custom":
            session.institute_key = "custom"
            session.awaiting_state = "custom_url"
            await query.edit_message_text(
                "➕ <b>Custom API Base URL Bhejein:</b>\n\n"
                "Example: <code>https://dishaonlineclassesapi.classx.co.in</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        if key in INSTITUTES:
            session.institute_key = key
            session.token = None
            session.batches = []
            session.selected_batch = None
            session.awaiting_state = "login_combo_or_id"
            prof = INSTITUTES[key]
            await query.edit_message_text(
                f"✅ <b>{prof.short_label}</b> select ho gaya!\n"
                f"<code>{prof.base_url}</code>\n\n"
                "Send <b>ID*Password</b> (example: <code>your_id*your_password</code>)\n"
                "ya sirf <b>Mobile / Email / ID</b> bhejein:",
                parse_mode=ParseMode.HTML,
            )
    elif data == "start_login":
        profile = get_active_profile(session)
        session.awaiting_state = "login_combo_or_id"
        await query.edit_message_text(
            f"🔐 <b>Login — {profile.short_label}</b>\n"
            f"<code>{profile.base_url}</code>\n\n"
            "Send <b>ID*Password</b> (example: <code>your_id*your_password</code>)\n"
            "ya sirf <b>Mobile / Email / ID</b> bhejein:",
            parse_mode=ParseMode.HTML,
        )
    elif data == "list_batches":
        ok, info = await fetch_user_batches(session)
        if not ok:
            await query.edit_message_text(f"❌ {info}", reply_markup=home_keyboard(session))
            return
        buttons = [
            [InlineKeyboardButton(f"📚 {b['title'][:45]}", callback_data=f"pick_batch_{idx}")]
            for idx, b in enumerate(session.batches[:30])
        ]
        buttons.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="go_home")])
        await query.edit_message_text(
            f"✅ <b>{info}</b>\nKisi course par click karein:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    elif data.startswith("pick_batch_"):
        idx_str = data.replace("pick_batch_", "", 1)
        if not idx_str.isdigit():
            return
        idx = int(idx_str)
        if idx < 0 or idx >= len(session.batches):
            await query.edit_message_text("⚠️ Course list refresh karein.")
            return
        chosen = session.batches[idx]
        session.selected_batch = chosen
        await query.edit_message_text(
            f"📚 <b>{chosen['title']}</b>\n"
            f"🆔 <b>Course ID:</b> <code>{chosen['id']}</code>\n\n"
            "Export ya Channel/Topic delivery select karein:",
            parse_mode=ParseMode.HTML,
            reply_markup=batch_action_keyboard(idx),
        )
    elif data.startswith("run_txt_") or data.startswith("run_deliv_"):
        is_deliv = data.startswith("run_deliv_")
        idx_str = data.replace("run_deliv_" if is_deliv else "run_txt_", "", 1)
        if not idx_str.isdigit():
            return
        idx = int(idx_str)
        if idx < 0 or idx >= len(session.batches):
            return
        chosen = session.batches[idx]
        session.selected_batch = chosen
        if is_deliv and not session.target_channel_id:
            session.awaiting_state = "set_channel"
            await query.edit_message_text(
                "📣 Pehle Target Channel ID ya Group+Topic bhejein\n"
                "(Example: <code>-1001234567890</code> ya <code>-1001234567890 42</code>):",
                parse_mode=ParseMode.HTML,
            )
            return
        if session.extract_task and not session.extract_task.done():
            await query.edit_message_text("⚠️ Ek task pehle se chal raha hai.")
            return
        session.extract_task = asyncio.create_task(
            run_batch_extraction(
                update,
                context,
                session,
                chosen["id"],
                chosen["title"],
                mode="deliver" if is_deliv else "txt",
            )
        )
    elif data == "export_txt_now":
        if not session.token:
            await query.edit_message_text("❌ Pehle login karein.", reply_markup=home_keyboard(session))
            return
        if not session.selected_batch:
            await query.edit_message_text("⚠️ Pehle `My Courses` se course select karein.", reply_markup=home_keyboard(session))
            return
        session.extract_task = asyncio.create_task(
            run_batch_extraction(
                update,
                context,
                session,
                session.selected_batch["id"],
                session.selected_batch["title"],
                mode="txt",
            )
        )
    elif data == "set_dest_help":
        session.awaiting_state = "set_channel_or_topic"
        await query.edit_message_text(
            "📣 <b>Set Target Channel / Group Topic</b>\n\n"
            "• Sirf Channel ID: <code>-1001234567890</code>\n"
            "• Group + Topic ID: <code>-1001234567890 42</code>",
            parse_mode=ParseMode.HTML,
        )
    elif data == "deliver_now":
        if not session.token:
            await query.edit_message_text("❌ Pehle login karein.", reply_markup=home_keyboard(session))
            return
        if not session.selected_batch:
            await query.edit_message_text("⚠️ Pehle `My Courses` se course select karein.", reply_markup=home_keyboard(session))
            return
        if not session.target_channel_id:
            session.awaiting_state = "set_channel_or_topic"
            await query.edit_message_text(
                "📣 Pehle Target Channel ID ya <code>ChannelID TopicID</code> bhejein:",
                parse_mode=ParseMode.HTML,
            )
            return
        session.extract_task = asyncio.create_task(
            run_batch_extraction(
                update,
                context,
                session,
                session.selected_batch["id"],
                session.selected_batch["title"],
                mode="deliver",
            )
        )
    elif data == "show_status":
        await query.edit_message_text(
            format_home_text(session),
            parse_mode=ParseMode.HTML,
            reply_markup=home_keyboard(session),
        )
    elif data == "do_logout":
        session.token = None
        session.batches = []
        session.selected_batch = None
        session.awaiting_state = None
        await query.edit_message_text("🚪 Logout ho gaya.", reply_markup=home_keyboard(session))
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
        session.awaiting_state = "login_combo_or_id"
        await update.message.reply_text(
            f"✅ Custom URL set: <code>{session.custom_base_url}</code>\n\n"
            "Ab <b>ID*Password</b> ya <b>Mobile / ID</b> bhejein:",
            parse_mode=ParseMode.HTML,
        )
        return

    if session.awaiting_state in ("set_channel", "set_channel_or_topic"):
        parts = text.split()
        session.target_channel_id = parts[0].strip()
        if len(parts) >= 2 and parts[1].isdigit():
            session.target_topic_id = int(parts[1].strip())
        else:
            session.target_topic_id = None
        session.awaiting_state = None
        topic_info = f" | Topic #{session.target_topic_id}" if session.target_topic_id else ""
        await update.message.reply_text(
            f"✅ Destination set: <code>{session.target_channel_id}</code>{topic_info}",
            parse_mode=ParseMode.HTML,
            reply_markup=home_keyboard(session),
        )
        return

    if session.awaiting_state == "set_topic":
        parts = text.split()
        if len(parts) >= 2 and parts[1].isdigit():
            session.target_channel_id = parts[0].strip()
            session.target_topic_id = int(parts[1].strip())
            session.awaiting_state = None
            await update.message.reply_text(
                f"✅ Topic set: <code>{session.target_channel_id}</code> | Topic #{session.target_topic_id}",
                parse_mode=ParseMode.HTML,
                reply_markup=home_keyboard(session),
            )
        else:
            await update.message.reply_text("⚠️ Format: <code>-1001234567890 42</code>", parse_mode=ParseMode.HTML)
        return

    if session.awaiting_state == "login_combo_or_id":
        uid, pwd = parse_credential_input(text)
        if uid and pwd:
            session.awaiting_state = None
            try:
                await update.message.delete()
            except Exception:
                pass
            wait_msg = await update.message.reply_text("⏳ Signing in...")
            ok, msg = await perform_login(session, uid, pwd)
            if not ok:
                await wait_msg.edit_text(f"❌ {msg}", reply_markup=home_keyboard(session))
                return
            b_ok, b_msg = await fetch_user_batches(session)
            buttons = [
                [InlineKeyboardButton(f"📚 {b['title'][:45]}", callback_data=f"pick_batch_{idx}")]
                for idx, b in enumerate(session.batches[:30])
            ]
            buttons.append([InlineKeyboardButton("🏠 Main Menu", callback_data="go_home")])
            await wait_msg.edit_text(
                f"✅ <b>{msg}</b>\n📚 <b>{b_msg}</b>\n\nCourse select karein:",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        session.temp_login_id = text
        session.awaiting_state = "login_password"
        await update.message.reply_text("🔑 Ab apna <b>Password</b> bhejein:", parse_mode=ParseMode.HTML)
        return

    if session.awaiting_state == "login_password":
        password = text
        uid = session.temp_login_id or ""
        session.temp_login_id = None
        session.awaiting_state = None
        try:
            await update.message.delete()
        except Exception:
            pass
        wait_msg = await update.message.reply_text("⏳ Login verify ho raha hai...")
        ok, msg = await perform_login(session, uid, password)
        if not ok:
            await wait_msg.edit_text(f"❌ {msg}", reply_markup=home_keyboard(session))
            return
        b_ok, b_msg = await fetch_user_batches(session)
        buttons = [
            [InlineKeyboardButton(f"📚 {b['title'][:45]}", callback_data=f"pick_batch_{idx}")]
            for idx, b in enumerate(session.batches[:30])
        ]
        buttons.append([InlineKeyboardButton("🏠 Main Menu", callback_data="go_home")])
        await wait_msg.edit_text(
            f"✅ <b>{msg}</b>\n📚 <b>{b_msg}</b>\n\nCourse select karein:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    await update.message.reply_text(
        format_home_text(session),
        parse_mode=ParseMode.HTML,
        reply_markup=home_keyboard(session),
    )


async def start_health_server() -> Optional[asyncio.AbstractServer]:
    async def reader_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.read(1024)
            body = b"OK"
            headers = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain\r\n"
                + f"Content-Length: {len(body)}\r\n".encode("ascii")
                + b"Connection: close\r\n\r\n"
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
    for cmd, fn in [
        ("start", cmd_start),
        ("platform", cmd_platform),
        ("institute", cmd_platform),
        ("login", cmd_login),
        ("courses", cmd_courses),
        ("batches", cmd_courses),
        ("export", cmd_export),
        ("channel", cmd_channel),
        ("topic", cmd_topic),
        ("deliver", cmd_deliver),
        ("masked", cmd_masked),
        ("setcredit", cmd_setcredit),
        ("status", cmd_status),
        ("logout", cmd_logout),
        ("stop", cmd_stop),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    server = await start_health_server()
    async with app:
        await app.initialize()
        await app.bot.delete_webhook(drop_pending_updates=True)
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logger.info("Sanskari Course Extractor & Delivery Bot started.")
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
