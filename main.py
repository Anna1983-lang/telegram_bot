import os
import logging
import asyncio
from datetime import datetime
from textwrap import wrap
from aiohttp import web

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, FSInputFile, Update

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter

# ---------- Логирование ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Настройки (ENV) ----------
TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "1227847495,5791748471").split(",")]
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # https://<project>.onrender.com/webhook/<BOT_ID>
BOT_ID_PREFIX = TOKEN.split(":")[0]
WEBHOOK_PATH = f"/webhook/{BOT_ID_PREFIX}"

POLICY_PDF = "policy.pdf"
CONSENT_PDF = "consent2.pdf"
EXCEL_FILE = "consents.xlsx"

# ---------- Aiogram ----------
bot = Bot(TOKEN)
dp = Dispatcher()

# ---------- Excel ----------
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

def read_last_status_for_user(path: str, user_id: int):
    if not os.path.exists(path):
        return None
    wb = load_workbook(path, read_only=True)
    ws = wb.active
    last = None
    for row in ws.iter_rows(min_row=2, values_only=True):
        try:
            if int(row[1]) == user_id:
                last = row[5]
        except Exception:
            continue
    return last

def append_excel_entry(path: str, ts: str, user, status: str):
    init_excel_if_needed(path)
    wb = load_workbook(path)
    ws = wb.active
    ws.append([ts, user.id, user.username or "", user.first_name or "", user.last_name or "", status])
    wb.save(path)

def clear_excel(path: str):
    wb = Workbook()
    ws = wb.active
    ws.title = "Consents"
    ws.append(["Timestamp", "User ID", "Username", "First name", "Last name", "Status"])
    widths = [20, 15, 25, 20, 20, 15]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    wb.save(path)

# ---------- PDF ----------
pdfmetrics.registerFont(TTFont("DejaVu", "DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", "DejaVuSans-Bold.ttf"))

def make_confirmation_pdf(filename: str, user, status: str, ts: str) -> str:
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
        f"Telegram: @{user.username}" if user.username else f"ID: {user.id}",
        f"ФИО: {(user.first_name or '')} {(user.last_name or '')}".strip(),
        f"Документы: {POLICY_PDF}, {CONSENT_PDF}"
    ]
    for hl in header:
        for line in wrap(hl, 100):
            c.drawString(40, y, line)
            y -= 16
    y -= 8
    text = "Настоящим подтверждается выбор пользователя в электронном виде."
    for line in wrap(text, 100):
        c.drawString(40, y, line)
        y -= 16
    c.save()
    return filename

# ---------- Хэндлеры ----------
@dp.message(CommandStart())
async def cmd_start(m: Message):
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📄 Политика", callback_data="policy_pdf"),
         types.InlineKeyboardButton(text="📝 Согласие", callback_data="consent_pdf")],
        [types.InlineKeyboardButton(text="✅ Согласен", callback_data="agree"),
         types.InlineKeyboardButton(text="❌ Не согласен", callback_data="disagree")]
    ])
    await m.answer("Здравствуйте! Ознакомьтесь с документами и выберите действие:", reply_markup=kb)

@dp.message(Command("ping"))
async def cmd_ping(m: Message):
    await m.answer("pong")

@dp.message(Command("id"))
async def cmd_id(m: Message):
    await m.answer(f"Ваш ID: {m.from_user.id}")

@dp.callback_query(F.data == "policy_pdf")
async def send_policy(c: CallbackQuery):
    await c.message.answer_document(FSInputFile(POLICY_PDF), caption="Политика конфиденциальности")
    await c.answer()

@dp.callback_query(F.data == "consent_pdf")
async def send_consent(c: CallbackQuery):
    await c.message.answer_document(FSInputFile(CONSENT_PDF), caption="Текст согласия")
    await c.answer()

@dp.callback_query(F.data.in_({"agree", "disagree"}))
async def consent_handler(c: CallbackQuery):
    user, action = c.from_user, c.data
    status = "Согласен" if action == "agree" else "Не согласен"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    last = read_last_status_for_user(EXCEL_FILE, user.id)
    if last == status:
        await c.answer(f"Ваш выбор уже зафиксирован: {status}", show_alert=True)
        return

    append_excel_entry(EXCEL_FILE, ts, user, status)

    if status == "Согласен":
        tmp_pdf = f"confirmation_{user.id}.pdf"
        make_confirmation_pdf(tmp_pdf, user, status, ts)
        await c.message.answer_document(FSInputFile(tmp_pdf), caption="Подтверждение (PDF)")
        os.remove(tmp_pdf)

    await c.message.answer(f"Ваш выбор зафиксирован: {status}")
    await c.answer()

    # Уведомляем админов
    for admin in ADMIN_IDS:
        try:
            await bot.send_message(admin, f"📢 {user.full_name} ({user.id}) выбрал: {status}")
        except Exception:
            pass

@dp.message(Command("report"))
async def cmd_report(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        await m.answer("⛔ Только для администратора")
        return
    if not os.path.exists(EXCEL_FILE):
        await m.answer("Файл ещё не создан")
        return
    await m.answer_document(FSInputFile(EXCEL_FILE), caption="Отчёт по согласиям")

@dp.message(Command("clear"))
async def cmd_clear(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        await m.answer("⛔ Только для администратора")
        return
    clear_excel(EXCEL_FILE)
    await m.answer("Отчёт очищен ✅")

@dp.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer("/start /ping /id /report /clear /help")

# ---------- Webhook ----------
async def on_startup(app: web.Application):
    await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
    logger.info("Webhook установлен: %s", WEBHOOK_URL)

async def on_shutdown(app: web.Application):
    await bot.delete_webhook()
    await bot.session.close()
    logger.info("Webhook удалён")

async def handle(request: web.Request):
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return web.Response(text="ok")

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
        logger.error("WEBHOOK_URL не задан")
        raise SystemExit(1)
    web.run_app(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
