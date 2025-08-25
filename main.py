import asyncio
import logging
import os
import time
import shutil
from datetime import datetime
from textwrap import wrap

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, FSInputFile

from aiohttp import web

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter

logging.basicConfig(level=logging.INFO)

# ========== НАСТРОЙКИ ==========
# Твой токен (вставлен как просила)
TOKEN = "8475192387:AAESFlpUUqJzlqPTQkcAv1sDVeZJSFOQV0w"

# Названия файлов (положи рядом с main.py)
POLICY_PDF = "policy.pdf"
CONSENT_PDF = "consent2.pdf"  # если у тебя другой — замени здесь
EXCEL_FILE = "consents.xlsx"

# Админ (тот, кто получит /report и уведомления)
ADMIN_ID = 1227847495

# Попытка подключить DejaVu для корректной кириллицы в PDF, без фатала если нет файлов
try:
    pdfmetrics.registerFont(TTFont("DejaVu", "DejaVuSans.ttf"))
    pdfmetrics.registerFont(TTFont("DejaVu-Bold", "DejaVuSans-Bold.ttf"))
    DEFAULT_FONT = "DejaVu"
    DEFAULT_BOLD = "DejaVu-Bold"
except Exception:
    DEFAULT_FONT = "Helvetica"
    DEFAULT_BOLD = "Helvetica-Bold"

router = Router()

# ========== EXCEL HELPERS ==========
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

def append_excel_entry(path: str, ts: str, user, status: str):
    init_excel_if_needed(path)
    wb = load_workbook(path)
    ws = wb.active
    ws.append([
        ts,
        user.id,
        user.username or "",
        user.first_name or "",
        user.last_name or "",
        status
    ])
    wb.save(path)

def get_user_status(path: str, user_id: int):
    if not os.path.exists(path):
        return None
    wb = load_workbook(path)
    ws = wb.active
    # ищем последнюю запись пользователя (снизу вверх)
    for row in reversed(list(ws.iter_rows(min_row=2, values_only=True))):
        if row[1] == user_id:
            return row[5]  # статус
    return None

# ========== PDF подтверждение ==========
def make_confirmation_pdf(filename: str, user, status: str, ts: str) -> str:
    c = canvas.Canvas(filename, pagesize=A4)
    width, height = A4
    y = height - 40

    c.setFont(DEFAULT_BOLD, 14)
    c.drawString(40, y, "Подтверждение выбора по согласию на обработку ПДн")
    y -= 30

    c.setFont(DEFAULT_FONT, 11)
    header = [
        f"Выбор: {status}",
        f"Дата и время: {ts}",
        f"Telegram: @{user.username}" if user.username else f"Telegram user_id: {user.id}",
        f"ФИО: {(user.first_name or '')} {(user.last_name or '')}".strip(),
        f"Актуальные версии документов: {POLICY_PDF} / {CONSENT_PDF}"
    ]
    for hl in header:
        for line in wrap(hl, 95):
            c.drawString(40, y, line)
            y -= 18
    y -= 8

    body = ("Настоящим подтверждается зафиксированное волеизъявление пользователя "
            "в электронном виде. Содержание согласия и политики конфиденциальности "
            f"предоставлено пользователю в виде файлов PDF ({POLICY_PDF}, {CONSENT_PDF}) "
            "до момента выражения согласия/отказа.")
    for line in wrap(body, 95):
        if y < 60:
            c.showPage()
            y = height - 40
            c.setFont(DEFAULT_FONT, 11)
        c.drawString(40, y, line)
        y -= 18

    c.save()
    return filename

# ========== UI / клавиатура ==========
def start_keyboard():
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="📄 Политика (PDF)", callback_data="policy_pdf"),
            types.InlineKeyboardButton(text="📝 Согласие (PDF)", callback_data="consent_pdf"),
        ],
        [
            types.InlineKeyboardButton(text="✅ Согласен", callback_data="agree"),
            types.InlineKeyboardButton(text="❌ Не согласен", callback_data="disagree"),
        ],
    ])

# ========== ХЭНДЛЕРЫ ==========
@router.message(CommandStart())
async def start(m: Message):
    await m.answer(
        "Здравствуйте! Ознакомьтесь с документами (PDF), затем нажмите «✅ Согласен» или «❌ Не согласен».",
        reply_markup=start_keyboard()
    )

@router.message(Command("ping"))
async def ping(m: Message):
    await m.answer("pong ✅")

@router.callback_query(F.data == "policy_pdf")
async def send_policy_pdf(c: CallbackQuery):
    if not os.path.exists(POLICY_PDF):
        await c.answer("Файл policy.pdf не найден рядом с ботом.", show_alert=True)
        return
    f = FSInputFile(POLICY_PDF, filename=f"policy_{int(time.time())}.pdf")
    await c.message.answer_document(f, caption="Политика конфиденциальности (PDF)")
    await c.answer()

@router.callback_query(F.data == "consent_pdf")
async def send_consent_pdf(c: CallbackQuery):
    if not os.path.exists(CONSENT_PDF):
        await c.answer(f"Файл {CONSENT_PDF} не найден рядом с ботом.", show_alert=True)
        return
    f = FSInputFile(CONSENT_PDF, filename=f"consent_{int(time.time())}.pdf")
    await c.message.answer_document(f, caption="Текст согласия (PDF)")
    await c.answer()

@router.callback_query(F.data.in_({"agree", "disagree"}))
async def consent_handler(c: CallbackQuery):
    user = c.from_user
    status = "Согласен" if c.data == "agree" else "Не согласен"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    existing_status = get_user_status(EXCEL_FILE, user.id)

    # Защита от повторного ответа:
    # - если уже Согласен — менять нельзя
    # - если отказался — можно согласиться один раз
    if existing_status == "Согласен":
        await c.answer("Ваш выбор уже зафиксирован: Согласен. Изменить нельзя.", show_alert=True)
        return
    if existing_status == "Не согласен" and status == "Не согласен":
        await c.answer("Вы уже отказались ранее. Ответ зафиксирован.", show_alert=True)
        return

    # Запись в Excel
    append_excel_entry(EXCEL_FILE, ts, user, status)

    # Уведомление админу
    try:
        text = (f"Новый ответ!\n"
                f"ID: {user.id}\n"
                f"Имя: {(user.first_name or '')} {(user.last_name or '')}\n"
                f"Username: @{user.username}\n"
                f"Статус: {status}\n"
                f"Время: {ts}")
        await c.bot.send_message(ADMIN_ID, text)
    except Exception as e:
        logging.warning(f"Не удалось отправить уведомление админу: {e}")

    # Ответ пользователю и PDF (при согласии)
    if status == "Согласен":
        pdf_name = f"Подтверждение_{user.id}.pdf"
        make_confirmation_pdf(pdf_name, user, status, ts)
        await c.message.edit_text("Спасибо! Ваш выбор зафиксирован: Согласен")
        await c.message.answer_document(FSInputFile(pdf_name, filename=f"confirm_{int(time.time())}.pdf"),
                                        caption="Ваше подтверждение (PDF)")
        try:
            os.remove(pdf_name)
        except Exception:
            pass
    else:
        await c.message.edit_text("Отказ зафиксирован. Если передумаете — вы сможете согласиться один раз.")

    await c.answer()

@router.message(Command("report"))
async def send_report(m: Message):
    if m.from_user.id != ADMIN_ID:
        await m.answer("⛔ У вас нет доступа к этой команде")
        return
    if not os.path.exists(EXCEL_FILE):
        await m.answer("Файл consents.xlsx ещё не создан")
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_name = f"consents_{ts}.xlsx"
    shutil.copy(EXCEL_FILE, temp_name)
    await m.answer_document(FSInputFile(temp_name), caption="📊 Отчёт по согласиям")
    try:
        os.remove(temp_name)
    except Exception:
        pass

@router.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer(
        "Команды:\n"
        "• /start — показать кнопки\n"
        "• /ping — проверить, что бот жив\n"
        "• /report — админ получает consents.xlsx\n"
        "• 📄 Политика / 📝 Согласие — отправить PDF\n"
    )

@router.message()
async def any_message(m: Message):
    await m.answer(
        "Здравствуйте! Для начала работы нажмите /start или нажмите на кнопки.",
        reply_markup=start_keyboard()
    )

# ========== HTTP-сервер для Render (чтобы Web Service не "рубил" процесс) ==========
async def health(request):
    return web.Response(text="ok")

async def run_http_server():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    port = int(os.getenv("PORT", "10000"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()

    # держим сервер живым
    await asyncio.Event().wait()

# ========== Запуск бота ==========
async def run_bot():
    bot = Bot(TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

async def main():
    # запускаем HTTP-сервер и бота параллельно
    await asyncio.gather(
        run_http_server(),
        run_bot(),
    )

if __name__ == "__main__":
    asyncio.run(main())
