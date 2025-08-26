import asyncio
import os
import logging
from datetime import datetime
from textwrap import wrap
from aiohttp import web

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, FSInputFile, Update

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Настройки (ENV) ----------
TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "1227847495,5791748471").split(",")]
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
BOT_ID_PREFIX = TOKEN.split(":")[0]
WEBHOOK_PATH = f"/webhook/{BOT_ID_PREFIX}"

POLICY_PDF = "policy.pdf"
CONSENT_PDF = "consent2.pdf"
EXCEL_FILE = "agreements.xlsx"

# ---------- aiogram v3 ----------
router = Router()
dp = Dispatcher()
dp.include_router(router)
bot = Bot(TOKEN)

# ---------- Excel/PDF утилиты ----------
def init_excel(path: str, force=False):
    """Создаём Excel с заголовками (если force=True — пересоздаём)."""
    if os.path.exists(path) and not force:
        return
    wb = Workbook()
    ws = wb.active
    ws.title = "Consents"
    ws.append(["Timestamp", "User ID", "Username", "First name", "Last name", "Status"])
    widths = [20, 15, 25, 20, 20, 15]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    wb.save(path)

def append_excel_entry(path: str, ts: str, user, status: str):
    init_excel(path)
    wb = load_workbook(path)
    ws = wb.active
    ws.append([ts, user.id, user.username or "", user.first_name or "", user.last_name or "", status])
    wb.save(path)

def make_confirmation_pdf(filename: str, user, status: str, ts: str) -> str:
    # Подключаем шрифты (DejaVuSans поддерживает кириллицу)
    pdfmetrics.registerFont(TTFont("DejaVu", "DejaVuSans.ttf"))
    pdfmetrics.registerFont(TTFont("DejaVu-Bold", "DejaVuSans-Bold.ttf"))

    c = canvas.Canvas(filename, pagesize=A4)
    width, height = A4
    y = height - 40
    c.setFont("DejaVu-Bold", 14)
    c.drawString(40, y, "Подтверждение согласия")
    y -= 26
    c.setFont("DejaVu", 11)
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
    body = ("Настоящим подтверждается выбор пользователя относительно согласия "
            "на обработку персональных данных. Содержание согласия и политика "
            "были предоставлены пользователю в виде файлов PDF.")
    for line in wrap(body, 100):
        if y < 60:
            c.showPage()
            y = height - 40
            c.setFont("DejaVu", 11)
        c.drawString(40, y, line)
        y -= 16
    c.save()
    return filename

# ---------- Хэндлеры ----------
@router.message(CommandStart())
async def start(m: Message):
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📄 Политика (PDF)", callback_data="policy_pdf"),
         types.InlineKeyboardButton(text="📝 Согласие (PDF)", callback_data="consent_pdf")],
        [types.InlineKeyboardButton(text="✅ Согласен", callback_data="agree"),
         types.InlineKeyboardButton(text="❌ Не согласен", callback_data="disagree")]
    ])
    await m.answer("Здравствуйте! Ознакомьтесь с документами (PDF), затем выберите:", reply_markup=kb)

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
    user = c.from_user
    status = "Согласен" if c.data == "agree" else "Не согласен"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    append_excel_entry(EXCEL_FILE, ts, user, status)

    tmp_pdf = f"confirmation_{user.id}.pdf"
    try:
        make_confirmation_pdf(tmp_pdf, user, status, ts)
        await c.message.answer_document(FSInputFile(tmp_pdf), caption="Ваше подтверждение (PDF)")
    except Exception:
        logger.exception("Ошибка при генерации PDF")
    finally:
        try:
            if os.path.exists(tmp_pdf):
                os.remove(tmp_pdf)
        except Exception:
            pass

    await c.message.edit_text(f"Ваш выбор зафиксирован: {status}")
    await c.answer()

    # уведомление админам
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"📢 {user.full_name} ({user.id}) выбрал: {status}")
        except Exception:
            logger.warning("Не удалось уведомить админа %s", admin_id)

@router.message(Command("report"))
async def report_cmd(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        await m.answer("Команда доступна только администратору.")
        return
    init_excel(EXCEL_FILE)
    await m.answer_document(FSInputFile(EXCEL_FILE), caption="Отчёт по согласиям (Excel)")

@router.message(Command("clear"))
async def clear_cmd(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        await m.answer("Команда доступна только администратору.")
        return
    init_excel(EXCEL_FILE, force=True)
    await m.answer("📑 Отчёт очищен ✅")
    logger.info("Report cleared by %s", m.from_user.id)

# ---------- Webhook HTTP сервер (aiohttp) ----------
async def on_startup(_app: web.Application):
    try:
        me = await bot.get_me()
        logger.info("Bot started as @%s (id=%s). ADMIN_IDS=%s", me.username, me.id, ADMIN_IDS)
        await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
        logger.info("Webhook установлен: %s", WEBHOOK_URL)
    except Exception:
        logger.exception("Не удалось установить webhook")

async def on_shutdown(_app: web.Application):
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await bot.session.close()
        logger.info("Webhook удалён и сессия закрыта")
    except Exception:
        logger.exception("Не удалось удалить webhook")

async def handle(request: web.Request):
    try:
        data = await request.json()
        update = Update.model_validate(data)
        asyncio.create_task(dp.feed_update(bot, update))
        return web.Response(text="ok")
    except Exception:
        logger.exception("Ошибка обработки апдейта")
        return web.Response(status=500, text="error")

async def root(_):
    return web.Response(text="ok")

async def healthz(_):
    return web.Response(text="ok")

def create_app():
    app = web.Application()
    app.router.add_get("/", root)
    app.router.add_get("/healthz", healthz)
    app.router.add_post(WEBHOOK_PATH, handle)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_shutdown)
    return app

if __name__ == "__main__":
    if not WEBHOOK_URL:
        logger.error("ERROR: WEBHOOK_URL не задан")
        raise SystemExit(1)
    app = create_app()
    port = int(os.environ.get("PORT", 10000))
    web.run_app(app, host="0.0.0.0", port=port)
