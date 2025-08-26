# main.py
import asyncio
import os
import logging
from datetime import datetime
from textwrap import wrap
from typing import List, Optional, Tuple

from aiohttp import web, ClientSession

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, FSInputFile, Update

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- ENV / SETTINGS ----------
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
if not TOKEN:
    logger.error("TELEGRAM_TOKEN not set in ENV")
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "1227847495,5791748471").replace(" ", "").split(",") if x
}
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")  # e.g. https://your-app.onrender.com/webhook/<botid>
BOT_ID_PREFIX = TOKEN.split(":")[0] if TOKEN else ""
WEBHOOK_PATH = f"/webhook/{BOT_ID_PREFIX}"

POLICY_PDF = "policy.pdf"
CONSENT_PDF = "consent2.pdf"
EXCEL_FILE = "consents.xlsx"

# PDF fonts (put TTF files in repo under fonts/ or set ENV)
PDF_FONT_REGULAR_PATH = os.environ.get("PDF_FONT_REGULAR", "fonts/DejaVuSans.ttf")
PDF_FONT_BOLD_PATH = os.environ.get("PDF_FONT_BOLD", "fonts/DejaVuSans-Bold.ttf")
PDF_FONT_REGULAR_NAME = "DejaVuSans"
PDF_FONT_BOLD_NAME = "DejaVuSans-Bold"

# ---------- aiogram setup ----------
router = Router()
dp = Dispatcher()
dp.include_router(router)
bot = Bot(TOKEN)

# ---------- Excel helpers ----------
def init_excel_if_needed(path: str):
    if os.path.exists(path):
        return
    wb = Workbook()
    ws = wb.active
    ws.title = "Consents"
    ws.append(["Timestamp", "User ID", "Username", "First name", "Last name", "Status"])
    widths = [20, 15, 25, 20, 20, 15]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    wb.save(path)

def read_all_rows(path: str) -> List[tuple]:
    if not os.path.exists(path):
        return []
    wb = load_workbook(path, read_only=True)
    ws = wb.active
    rows = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue
        rows.append(row)
    return rows

def read_last_status_for_user(path: str, user_id: int) -> Optional[str]:
    last = None
    for row in read_all_rows(path):
        try:
            uid = int(row[1])
        except Exception:
            continue
        if uid == user_id:
            last = row[5]
    return last

def append_excel_entry(path: str, ts: str, user, status: str):
    init_excel_if_needed(path)
    wb = load_workbook(path)
    ws = wb.active
    ws.append([ts, user.id, user.username or "", user.first_name or "", user.last_name or "", status])
    wb.save(path)

def rewrite_excel(path: str, rows: List[tuple]):
    wb = Workbook()
    ws = wb.active
    ws.title = "Consents"
    ws.append(["Timestamp", "User ID", "Username", "First name", "Last name", "Status"])
    widths = [20, 15, 25, 20, 20, 15]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for r in rows:
        ws.append(list(r))
    wb.save(path)

def filter_rows_by_period(rows: List[tuple], start: Optional[datetime], end: Optional[datetime]) -> List[tuple]:
    if not start and not end:
        return rows
    out = []
    for r in rows:
        try:
            ts = datetime.strptime(str(r[0]), "%Y-%m-%d %H:%M:%S")
        except Exception:
            try:
                ts = datetime.fromisoformat(str(r[0]))
            except Exception:
                continue
        if start and ts < start:
            continue
        if end and ts > end:
            continue
        out.append(r)
    return out

def parse_period(text_after_command: str) -> Tuple[Optional[datetime], Optional[datetime]]:
    s = (text_after_command or "").strip()
    if not s:
        return None, None
    parts = s.split()
    fmt_date = "%Y-%m-%d"
    fmt_dt = "%Y-%m-%d %H:%M"
    try:
        if len(parts) == 1:
            start = datetime.strptime(parts[0], fmt_date)
            end = datetime.strptime(parts[0], fmt_date).replace(hour=23, minute=59, second=59)
            return start, end
        elif len(parts) >= 2:
            try:
                start = datetime.strptime(" ".join(parts[0:2]), fmt_dt)
                if len(parts) >= 4:
                    end = datetime.strptime(" ".join(parts[2:4]), fmt_dt)
                else:
                    end = None
            except ValueError:
                start = datetime.strptime(parts[0], fmt_date)
                end = datetime.strptime(parts[1], fmt_date).replace(hour=23, minute=59, second=59)
            return start, end
    except Exception:
        return None, None

# ---------- PDF with Cyrillic ----------
def _register_pdf_fonts():
    try:
        registered = set(pdfmetrics.getRegisteredFontNames())
        if PDF_FONT_REGULAR_NAME not in registered and os.path.exists(PDF_FONT_REGULAR_PATH):
            pdfmetrics.registerFont(TTFont(PDF_FONT_REGULAR_NAME, PDF_FONT_REGULAR_PATH))
        if PDF_FONT_BOLD_NAME not in registered and os.path.exists(PDF_FONT_BOLD_PATH):
            pdfmetrics.registerFont(TTFont(PDF_FONT_BOLD_NAME, PDF_FONT_BOLD_PATH))
    except Exception:
        logger.exception("Не удалось зарегистрировать TTF-шрифты для PDF")

def make_confirmation_pdf(filename: str, user, status: str, ts: str) -> str:
    _register_pdf_fonts()
    c = canvas.Canvas(filename, pagesize=A4)
    width, height = A4
    y = height - 40
    try:
        c.setFont(PDF_FONT_BOLD_NAME, 14)
    except Exception:
        c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, "Подтверждение выбора по согласию на обработку ПДн")
    y -= 26
    try:
        c.setFont(PDF_FONT_REGULAR_NAME, 11)
    except Exception:
        c.setFont("Helvetica", 11)
    header = [
        f"Выбор: {status}",
        f"Дата и время: {ts}",
        f"Telegram: @{user.username}" if user.username else f"Telegram user_id: {user.id}",
        f"ФИО: {(user.first_name or '')} {(user.last_name or '')}".strip(),
        f"Документы: {POLICY_PDF} / {CONSENT_PDF}"
    ]
    for hl in header:
        for line in wrap(hl, 100):
            c.drawString(40, y, line)
            y -= 16
    y -= 8
    body = ("Настоящим подтверждается зафиксированное волеизъявление пользователя в электронном виде. "
            "Содержание согласия и политики конфиденциальности предоставлено пользователю в виде файлов PDF.")
    for line in wrap(body, 100):
        if y < 60:
            c.showPage()
            try:
                c.setFont(PDF_FONT_REGULAR_NAME, 11)
            except Exception:
                c.setFont("Helvetica", 11)
            y = height - 40
        c.drawString(40, y, line)
        y -= 16
    c.save()
    return filename

# ---------- Async helpers (notifications/pdf) ----------
async def _notify_admins_async(text: str):
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            logger.exception("Не удалось отправить уведомление админу %s", admin_id)

async def _generate_and_send_confirmation(tmp_pdf: str, user, status: str, ts: str, chat_id: int):
    try:
        make_confirmation_pdf(tmp_pdf, user, status, ts)
        await bot.send_document(chat_id, FSInputFile(tmp_pdf), caption="Ваше подтверждение (PDF)")
    except Exception:
        logger.exception("Ошибка при генерации/отправке PDF для user=%s", user.id)
        await _notify_admins_async(f"Ошибка при генерации PDF для user {user.id}")
    finally:
        try:
            if os.path.exists(tmp_pdf):
                os.remove(tmp_pdf)
        except Exception:
            pass

# ---------- Handlers ----------
@router.message(CommandStart())
async def start(m: Message):
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📄 Политика (PDF)", callback_data="policy_pdf"),
         types.InlineKeyboardButton(text="📝 Согласие (PDF)", callback_data="consent_pdf")],
        [types.InlineKeyboardButton(text="✅ Согласен", callback_data="agree"),
         types.InlineKeyboardButton(text="❌ Не согласен", callback_data="disagree")]
    ])
    await m.answer("Здравствуйте! Ознакомьтесь с документами (PDF), затем нажмите «✅ Согласен» или «❌ Не согласен».", reply_markup=kb)

@router.message(Command("id"))
async def whoami(m: Message):
    await m.answer(f"Ваш ID: {m.from_user.id}")

@router.message(Command("ping"))
async def ping(m: Message):
    await m.answer("pong")

@router.callback_query(F.data == "policy_pdf")
async def send_policy_pdf(c: CallbackQuery):
    if not os.path.exists(POLICY_PDF):
        await c.answer("Файл policy.pdf не найден.", show_alert=True)
        return
    await c.message.answer_document(FSInputFile(POLICY_PDF), caption="Политика конфиденциальности (PDF)")
    await c.answer()

@router.callback_query(F.data == "consent_pdf")
async def send_consent_pdf(c: CallbackQuery):
    if not os.path.exists(CONSENT_PDF):
        await c.answer(f"Файл {CONSENT_PDF} не найден.", show_alert=True)
        return
    await c.message.answer_document(FSInputFile(CONSENT_PDF), caption="Текст согласия (PDF)")
    await c.answer()

@router.callback_query(F.data.in_({"agree", "disagree"}))
async def consent_handler(c: CallbackQuery):
    try:
        user = c.from_user
        action = c.data
        status = "Согласен" if action == "agree" else "Не согласен"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        last = read_last_status_for_user(EXCEL_FILE, user.id)
        if last == "Согласен" and status == "Согласен":
            await c.answer("Ваш выбор уже зафиксирован: Согласен. Изменить нельзя.", show_alert=True)
            return
        if last == "Не согласен" and status == "Не согласен":
            await c.answer("Ваш выбор уже зафиксирован: Не согласен. Изменить нельзя.", show_alert=True)
            return

        # Запись в Excel (быстро)
        append_excel_entry(EXCEL_FILE, ts, user, status)

        # Быстрый ответ пользователю
        await c.answer("Ваш выбор зафиксирован. Пожалуйста, дождитесь подтверждения.", show_alert=False)
        try:
            await c.message.edit_text(f"Спасибо! Ваш выбор зафиксирован: {status}")
        except Exception:
            logger.debug("Не удалось изменить текст сообщения в callback (возможно удалено)")

        # Уведомление админам в фоне
        admin_text = (f"🆕 Новый выбор по согласию\n"
                      f"Статус: {status}\n"
                      f"Время: {ts}\n"
                      f"User: {user.id} @{user.username or '—'} {user.first_name or ''} {user.last_name or ''}".strip())
        asyncio.create_task(_notify_admins_async(admin_text))

        # Если согласился — сгенерить PDF и отправить в фоне
        if status == "Согласен":
            tmp_pdf = f"confirmation_{user.id}.pdf"
            chat_id = c.message.chat.id if c.message else user.id
            asyncio.create_task(_generate_and_send_confirmation(tmp_pdf, user, status, ts, chat_id))
    except Exception:
        logger.exception("Unhandled exception in consent_handler")
        try:
            await c.answer("Произошла ошибка. Попробуйте позже.", show_alert=True)
        except Exception:
            pass

@router.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer(
        "Команды:\n"
        "• /start\n"
        "• /id\n"
        "• /ping\n"
        "• /report [YYYY-MM-DD] | [YYYY-MM-DD HH:MM YYYY-MM-DD HH:MM]\n"
        "• /clear_all — очистить все записи (админ)\n"
        "• /clear_user <user_id> — удалить записи пользователя (админ)"
    )

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

@router.message(Command("report"))
async def report_cmd(m: Message):
    if not is_admin(m.from_user.id):
        await m.answer("Команда доступна только администратору.")
        return
    if not os.path.exists(EXCEL_FILE):
        await m.answer("Отчёт пока пуст (файл не найден).")
        return

    text_after = m.text.split(" ", 1)[1] if " " in m.text else ""
    start, end = parse_period(text_after)

    rows = read_all_rows(EXCEL_FILE)
    rows = filter_rows_by_period(rows, start, end)

    if not rows:
        await m.answer("За указанный период записей нет.")
        return

    tmp = "report_filtered.xlsx"
    try:
        rewrite_excel(tmp, rows)
        caption = "Отчёт по согласиям"
        if start or end:
            caption += f" (фильтр: {start or '…'} — {end or '…'})"
        await m.answer_document(FSInputFile(tmp), caption=caption)
    except Exception:
        logger.exception("Не удалось сформировать/отправить Excel")
        await m.answer("Ошибка при формировании отчёта. Проверьте логи.")
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

@router.message(Command("clear_all"))
async def clear_all_cmd(m: Message):
    if not is_admin(m.from_user.id):
        await m.answer("Команда доступна только администратору.")
        return
    rewrite_excel(EXCEL_FILE, [])
    await m.answer("Все записи очищены.")

@router.message(Command("clear_user"))
async def clear_user_cmd(m: Message):
    if not is_admin(m.from_user.id):
        await m.answer("Команда доступна только администратору.")
        return
    parts = m.text.strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await m.answer("Использование: /clear_user <user_id>")
        return
    target = int(parts[1])
    rows = read_all_rows(EXCEL_FILE)
    new_rows = [r for r in rows if int(r[1]) != target]
    rewrite_excel(EXCEL_FILE, new_rows)
    await m.answer(f"Записи пользователя {target} удалены ({len(rows) - len(new_rows)} шт.).")

# ---------- Webhook / server ----------
async def _keepalive_task():
    if not WEBHOOK_URL:
        return
    base = WEBHOOK_URL.split("/webhook")[0]
    url = base + "/healthz"
    while True:
        try:
            async with ClientSession() as s:
                async with s.get(url, timeout=5) as r:
                    await r.text()
        except Exception:
            logger.debug("Keepalive ping failed (ignored).")
        await asyncio.sleep(240)  # ~4 minutes

async def on_startup(app: web.Application):
    if not WEBHOOK_URL:
        logger.error("WEBHOOK_URL not set in ENV. Webhook will not be registered.")
    else:
        try:
            me = await bot.get_me()
            logger.info("Bot @%s (id=%s). ADMIN_IDS=%s", me.username, me.id, ADMIN_IDS)
            await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
            logger.info("Webhook set to %s", WEBHOOK_URL)
        except Exception:
            logger.exception("Failed to set webhook")

    # Start keepalive background task
    try:
        app["keepalive_task"] = asyncio.create_task(_keepalive_task())
    except Exception:
        logger.exception("Failed to start keepalive task")

async def on_shutdown(app: web.Application):
    # Cancel keepalive
    try:
        task = app.get("keepalive_task")
        if task:
            task.cancel()
    except Exception:
        pass
    # delete webhook and close bot session
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        logger.exception("Failed to delete webhook")
    try:
        await bot.session.close()
    except Exception:
        pass

async def handle(request: web.Request):
    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400, text="no json")

    logger.debug("Incoming update keys: %s", list(data.keys()))
    try:
        update = Update.model_validate(data)
        # don't await processing here — respond 200 OK immediately
        asyncio.create_task(dp.feed_update(bot, update))
        return web.Response(text="ok")
    except Exception:
        logger.exception("Ошибка обработки апдейта")
        return web.Response(status=500, text="error")

async def root(_request: web.Request):
    return web.Response(text="ok")

async def healthz(_request: web.Request):
    return web.Response(text="ok")

def create_app():
    app = web.Application()
    app.router.add_get("/", root)
    app.router.add_get("/healthz", healthz)
    # webhook path must match WEBHOOK_URL
    app.router.add_post(WEBHOOK_PATH, handle)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_shutdown)
    return app

if __name__ == "__main__":
    if not WEBHOOK_URL:
        logger.error("ERROR: WEBHOOK_URL environment variable is not set.")
        raise SystemExit(1)
    app = create_app()
    port = int(os.environ.get("PORT", 10000))
    web.run_app(app, host="0.0.0.0", port=port)
