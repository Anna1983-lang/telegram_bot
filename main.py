# main.py
import os
import logging
from datetime import datetime
from textwrap import wrap

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, FSInputFile

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Настройки ----------
TOKEN = os.environ.get("TELEGRAM_TOKEN", "8475192387:FAKE_TOKEN")
# список админов
ADMIN_IDS = {1227847495, 5791748471}

POLICY_PDF = "policy.pdf"
CONSENT_PDF = "consent2.pdf"
REPORT_FILE = "user_consents.xlsx"   # <-- новое имя файла отчёта

# ---------- aiogram ----------
router = Router()
dp = Dispatcher()
dp.include_router(router)
bot = Bot(TOKEN)

# ---------- Excel/PDF утилиты ----------
def init_excel(path: str, force: bool = False):
    """Создаём таблицу или пересоздаём при force=True"""
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
    c = canvas.Canvas(filename, pagesize=A4)
    width, height = A4
    y = height - 40
    c.setFont("DejaVuSans-Bold", 14)
    c.drawString(40, y, "Подтверждение выбора по согласию на обработку ПДн")
    y -= 26
    c.setFont("DejaVuSans", 11)
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
            y = height - 40
            c.setFont("DejaVuSans", 11)
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
    user = c.from_user
    action = c.data
    status = "Согласен" if action == "agree" else "Не согласен"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    append_excel_entry(REPORT_FILE, ts, user, status)

    # уведомления админу
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"📢 Пользователь {user.id} ({user.full_name}) выбрал: {status}")
        except Exception:
            pass

    if status == "Согласен":
        tmp_pdf = f"confirmation_{user.id}.pdf"
        try:
            make_confirmation_pdf(tmp_pdf, user, status, ts)
            await c.message.edit_text(f"Спасибо! Ваш выбор зафиксирован: {status}")
            await c.message.answer_document(FSInputFile(tmp_pdf), caption="Ваше подтверждение (PDF)")
        except Exception:
            logger.exception("Ошибка при генерации PDF")
            await c.message.edit_text(f"Спасибо! Ваш выбор зафиксирован: {status} (ошибка при создании PDF).")
        finally:
            if os.path.exists(tmp_pdf):
                os.remove(tmp_pdf)
    else:
        await c.message.edit_text("Отказ зафиксирован. Если передумаете — отправьте /start и согласуйте заново.")
    await c.answer()

@router.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer("Команды:\n• /start\n• /id\n• /ping\n• /report (админы)\n• /clear (админы)")

@router.message(Command("report"))
async def report_cmd(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        await m.answer("Команда доступна только администратору.")
        return
    if not os.path.exists(REPORT_FILE):
        await m.answer("Отчёт пока пуст (файл не найден).")
        return
    await m.answer_document(FSInputFile(REPORT_FILE), caption="Отчёт по согласиям (Excel)")

@router.message(Command("clear"))
async def clear_cmd(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        await m.answer("Команда доступна только администратору.")
        return
    init_excel(REPORT_FILE, force=True)
    await m.answer("📑 Отчёт очищен ✅")
    logger.info("Report cleared by admin %s", m.from_user.id)

# ---------- Запуск через polling ----------
if __name__ == "__main__":
    import asyncio
    async def main():
        init_excel(REPORT_FILE)
        logger.info("Bot polling started")
        await dp.start_polling(bot)

    asyncio.run(main())
