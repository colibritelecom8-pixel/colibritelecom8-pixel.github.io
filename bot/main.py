import re
import time
import urllib.request
import urllib
from datetime import datetime, timedelta
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

from vkbottle.bot import Bot, Message, rules
from vkbottle import Keyboard, Callback, KeyboardButtonColor, Text, GroupEventType, GroupTypes, User
import json
import sqlite3
from vkbottle.exception_factory import VKAPIError
import random
import os
import asyncio
import traceback
from collections import deque

# --- GSPREAD SETUP ---
try:
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
except ImportError:
    print("Warning: gspread or oauth2client not installed. Google Sheets sync will not work.")
with open("config.json", "r") as js:
    open_file = json.load(js)

try:
    with open("config.json", "r", encoding="utf-8") as js:
        open_file = json.load(js)
except json.JSONDecodeError as e:
    print(f"!!! КРИТИЧЕСКАЯ ОШИБКА в файле 'config.json' !!!")
    print(f"-> {e}")
    print("-> Проверьте, что после каждого элемента (кроме последнего) стоит запятая.")
    print("-> Пример: \"key1\": \"value1\",")
    exit()
except FileNotFoundError:
    print("!!! КРИТИЧЕСКАЯ ОШИБКА: Файл 'config.json' не найден!")
    print("-> Убедитесь, что файл 'config.json' находится в той же папке, что и бот.")
    exit()

if 'bot-token' not in open_file or not open_file['bot-token']:
    print("!!! КРИТИЧЕСКАЯ ОШИБКА: Токен бота не найден в 'config.json'!")
    print("-> Убедитесь, что вы вставили токен в поле 'bot-token'.")
    exit()

bot = Bot(token=open_file['bot-token'])

# --- ДЕДУЛИРОВАНИЕ СООБЩЕНИЙ ---
processed_messages = deque(maxlen=5000)
processed_event_ids = deque(maxlen=1000)
resolved_duels = deque(maxlen=2000)
econ_lock = asyncio.Lock()

# --- магазин тестеров конфиг ---
TSHOP_ITEMS = {
    "money_100k": {"name": "💰 100.000$", "price": 50, "type": "money", "val": 100000},
    "vip_7d": {"name": "✨ VIP на 7 дней", "price": 100, "type": "vip", "val": 7},
    "no_comm_24h": {"name": "📉 0% комиссии (24ч)", "price": 150, "type": "buff", "val": "no_comm"},
    "custom_prefix": {"name": "🎭 Свой префикс", "price": 300, "type": "prefix"},
    "tester_case": {"name": "📦 Кейс тестера", "price": 25, "type": "case"},
    "remove_prefix": {"name": "🗑 Снять префикс", "price": 0, "type": "remove_prefix"}
}

# --- кейсы конфиг ---
CASE_REWARDS = [
    {"name": "💰 100.000$", "type": "money", "val": 100000, "weight": 35},
    {"name": "💰 500.000$", "type": "money", "val": 500000, "weight": 10},
    {"name": "⭐ 20 баллов", "type": "points", "val": 20, "weight": 20},
    {"name": "⭐ 100 баллов", "type": "points", "val": 100, "weight": 5},
    {"name": "✨ VIP на 3 дня", "type": "vip", "val": 3, "weight": 10},
    {"name": "🧪 Набор юного тестера", "type": "trash", "val": "Набор юного тестера", "weight": 10},
    {"name": "💨 Пыль из серверной", "type": "trash", "val": "Пыль из серверной", "weight": 10}
]

# --- настройки тестерской системы багрепорт ---
LAST_ERRORS = deque(maxlen=10) # Храним последние 10 ошибок для /debuglog
TESTER_REWARD = {"money": 25000, "points": 10, "mats": 500}

# --- ГЛОБАЛЬНЫЕ ОБЩЕДОСТУПНЫЕ КОМАНДЫ (доступны для всех, проверка прав доступа выполняется внутри обработчика команд) ---
GLOBAL_PUBLIC_COMMANDS = [
    "join_duel", 
    "clan_accept_invite", "biz_accept_offer", "biz_decline_offer", "clan_war_accept", "clan_war_decline", "clan_boss_attack", "cancel_duel", "help_menu", "other_menu", "ghelp_main", "ghelp_page", "ticket_consider", "ticket_reject", "ticket_reply", "unwarn_btn", "unpred_btn", "biz_reclaim_from_clan",
    "clan_delete_ask", "clan_delete_yes", "clan_toggle_type", "clan_toggle_treasury", "clan_withdraw_ask", "remove_referrer", "tshop_buy", "tshop_menu", "clan_create_finish", "clan_pass_confirm", "clan_donate_confirm",
    "toggle_setting", "set_type", "type_page", "set_position", "buy_vip_tier", "slots_menu", "buy_slot"
]

# --- КОМАНДЫ ДЛЯ ПЕРСОНАЛА (могут быть нажаты модерами, даже если target_user_in_payload != user_id) ---
STAFF_COMMANDS = ["unmute", "unban", "kick", "unwarn_btn", "unpred_btn", "clear", "ungban", "ticket_reply", "ticket_consider", "ticket_reject", "stats", "warnhistory", "nicks", "gban", "gbanpl", "manage_moder", "moders_page"]


# --- вип конфиг ---
VIP_CONFIG = {
    1: {"name": "VIP I", "price": 500000, "comm": 0.05, "work_div": 1.5, "pay_bonus": 5, "prize_mult": 1.2, "color": KeyboardButtonColor.PRIMARY},
    2: {"name": "VIP II", "price": 1000000, "comm": 0.02, "work_div": 2.5, "pay_bonus": 15, "prize_mult": 1.5, "color": KeyboardButtonColor.POSITIVE}
}

# --- настройки логов ---
LOG_PEER_ID = 2000000000 + 11
CREATOR_ID = 460366734 # ID создателя бота
TESTER_CHAT_ID = 2000000000 + 4 # ID чата тестировщиков. 

async def send_log(text, keyboard=None, attachment=None):
    try: 
        params = {"peer_id": LOG_PEER_ID, "message": text, "random_id": 0, "disable_mentions": 1}
        if keyboard: params["keyboard"] = keyboard
        if attachment: params["attachment"] = attachment
        await bot.api.messages.send(**params)
    except: pass

async def log_action(user_id, chat_id, log_text, title=None):
    if chat_id == 4: return # Отключаем логи для тестерского чата
    try:
        user_info = await bot.api.users.get(user_ids=user_id)
        user_name = f"{user_info[0].first_name} {user_info[0].last_name}"
        user_link = f"[id{user_id}|{user_name}]"
        
        if not title:
            try:
                conv = await bot.api.messages.get_conversations_by_id(peer_ids=2000000000+chat_id)
                title = conv.items[0].chat_settings.title
            except: title = "Unknown"

        msg = (f"NEW LOG:\n1. Пользователь: {user_link}\n2. Лог: {log_text}\n3. Действие происходит в чате: {title} (CHAT_ID: {chat_id})")
        
        # Передаем attachment=None, так как в log_action его нет
        await send_log(msg, attachment=None)
    except: pass

def log_transaction(user_id, text):
    """Записывает финансовое действие в файл для истории без нагрузки на БД."""
    try:
        with open("transactions.log", "a", encoding="utf-8") as f:
            dt = datetime.now().strftime("%d.%m %H:%M")
            f.write(f"[{dt}] {user_id} | {text}\n")
    except: pass

async def get_logic(number):
    try:
        if number is None: return False
        return int(float(number)) >= 1
    except (ValueError, TypeError):
        return False

async def getID(arg: str) -> int:
    """Универсальное получение ID пользователя или группы из различных форматов."""
    if not arg:
        return None

    # 1. Формат [id123|Name] или [club123|Name]
    mention_match = re.match(r"\[(id|club)(\d+)\|.+?]", arg)
    if mention_match:
        object_id = int(mention_match.group(2))
        return -object_id if mention_match.group(1) == "club" else object_id

    # 2. Ссылки vk.com/id123, vk.com/screen_name и т.д.
    if "vk.com/" in arg or "vk.ru/" in arg or "vk.me/" in arg:
        parts = arg.rstrip('/').split('/')
        screen_name = parts[-1]
        if screen_name:
            if screen_name.startswith("id") and screen_name[2:].isdigit():
                return int(screen_name[2:])
            if screen_name.startswith("club") and screen_name[4:].isdigit():
                return -int(screen_name[4:])
            
            try:
                resolved = await bot.api.utils.resolve_screen_name(screen_name=screen_name)
                if resolved.type == 'user':
                    return resolved.object_id
                elif resolved.type == 'group':
                    return -resolved.object_id
            except Exception:
                pass

    # 3. Просто цифры или id123
    if arg.isdigit():
        return int(arg)
    if arg.startswith("id") and arg[2:].isdigit():
        return int(arg[2:])

    return None

async def get_target_user(message: Message, arguments: list, default_index=1):
    """Получить целевого пользователя из ответа, пересланного сообщения или аргумента."""
    if message.reply_message and message.reply_message.from_id > 0:
        return message.reply_message.from_id, None
    
    if message.fwd_messages and len(message.fwd_messages) > 0:
        if message.fwd_messages[0].from_id > 0:
            return message.fwd_messages[0].from_id, None
    
    if len(arguments) > default_index:
        user_id = await getID(arguments[default_index])
        if user_id:
            return user_id, None
    
    return None, "Укажите пользователя, ответьте на его сообщение или пришлите ссылку на профиль!"

async def get_registration_date(user_id=int):
    try:
        loop = asyncio.get_running_loop()
        def _fetch_sync():
            vk_link = f"https://vk.com/foaf.php?id={user_id}"
            req = urllib.request.Request(vk_link, headers={'User-Agent': 'Mozilla/5.0 ...'})
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.read().decode("windows-1251")
        
        vk_xml = await loop.run_in_executor(None, _fetch_sync)

        parsed_xml = re.findall(r'created dc:date="(.*)"', vk_xml)

        item = parsed_xml[0]
        sp_i = item.split('+')
        date_str = sp_i[0]

        PATTERN_IN = "%Y-%m-%dT%H:%M:%S"
        date_obj = datetime.strptime(date_str, PATTERN_IN)

        month_name_en = date_obj.strftime("%B")
        locales = {
            "November": "ноября", "October": "октября", "September": "сентября",
            "August": "августа", "July": "июля", "June": "июня", "May": "мая",
            "April": "апреля", "March": "марта", "February": "февраля",
            "January": "января", "December": "декабря"
        }
        month_name_ru = locales.get(month_name_en)

        return date_obj.strftime(f"%d-ого {month_name_ru} %Yг")

    except Exception:
        return "Не удалось определить"

async def get_string(text=[], arg=int):
    data_string = []
    for i in range(len(text)):
        if i < arg: pass
        else: data_string.append(text[i])
    return_string = " ".join(data_string)
    if return_string == "": return False
    else: return return_string

database = sqlite3.connect('database.db')
# Установка лимита размера базы данных (примерно 4 ГБ)
# 1024000 страниц * 4096 байт ≈ 4000 МБ
database.execute("PRAGMA max_page_count = 2621440")
# Также полезно включить режим авто-очистки для более эффективного использования места пока выкл
database.execute("PRAGMA auto_vacuum = INCREMENTAL")

# --- BOT RULES DEFAULT TEXT ---
CHAT_RULES_DEFAULT_TEXT = """
Правила чата "Общий чат | Игровая"
1. Основное:

• 1.1. Чат для общения и обсуждения игр.
• 1.2. Незнание правил не освобождает от ответственности.
• 1.3. Решения администрации окончательны (обращение — ЛС или /offer).
• 1.4. Правила могут изменяться без уведомления.

2. Запрещено:

• 2.1. Оскорбления и неуважение
Любые оскорбления, угрозы, дискриминация, а также завуалированные, саркастичные или провокационные сообщения.
Наказание: Мут 120 мин / Блокировка

• 2.2. Неадекватное и токсичное поведение
Провокации, разжигание конфликтов, токсичность, вредительство.
Наказание: Мут 120 мин / Блокировка

• 2.3. Спам и флуд
Много сообщений подряд, одинаковые сообщения, бессмысленные символы, стикеры, массовые упоминания.
Наказание: Мут 120 мин / Блокировка

• 2.4. Реклама
Любая реклама, кроме официальных ресурсов проекта.
Наказание: Мут 120 мин / Блокировка

• 2.5. Контент 18+ и шок-контент
Эротика, насилие, шокирующие материалы.
Наказание: Блокировка

• 2.6. Личная информация
Распространение данных без согласия (ФИО, телефон, соцсети и т.д.).
Наказание: Мут 120 мин / Блокировка

• 2.7. Политика и религия
Любые споры и обсуждения.
Наказание: Мут 120 мин

• 2.8. Продажа/покупка за реальные деньги
Любые сделки, обсуждения, намёки.
Наказание: Блокировка / Глобальная блокировка

• 2.9. Деструктив к проекту
Неконструктивная критика, призывы уйти. 
Наказание: Мут 120 мин / Блокировка / Глоб.блокировка

• 2.10. Оскорбление родных
Прямое или косвенное.
Наказание: Мут 120 мин / Блокировка

• 2.11. Вредоносные действия и ссылки
Фишинг, вирусы, подозрительные файлы, наркотики, терроризм и др. незаконные темы.
Наказание: Блокировка / Глобальная блокировка. 

• 2.12. Дискриминация
Расизм, нацизм, сексизм и любые формы нетерпимости.
Наказание: Мут 120 мин / Блокировка

• 2.13.  Обман и мошенничество
Введение в заблуждение, попытки обмана.
Наказание: Мут 120 мин / Блокировка

• 2.14. Пустые упоминания
Отметка пользователя без сообщения.
Наказание: Мут 120 мин

• 2.15. Использование багов и лазеек
Обход правил, использование недоработок.
Наказание: Мут 120 мин / Блокировка / Глоб.блокировка
Примечание: «Правило не расписано» — не оправдание, если нарушение очевидно.

• 2.16. Провокации на нарушение
Подстрекательство других пользователей.
Наказание: Мут 120 мин / Блокировка

• 2.17. Обход наказаний
Мультиаккаунты и любые способы обхода.
Наказание: Блокировка

• 2.18. Ники и аватары
Оскорбительные или провокационные.
Наказание: Предупреждение / Блокировка

• 2.19. Многократные нарушения
Более 5 наказаний за 7 дней.
Наказание: Блокировка

3. Наказания:

• Мут (120 мин) — за лёгкие нарушения.
• Бан — за повторные/серьёзные нарушения и обход наказаний.
• Глобал бан — за тяжкие нарушения (навсегда).
• Blacklist — за крайние нарушения (терроризм, взлом, массовый спам и т.д.).

4. Обжалование:
• Апелляция через /report или администрацию с доказательствами
"""
BOT_RULES_DEFAULT_TEXT = """🤖 Правила использования бота

1.  **Уважение.** Запрещены оскорбления, угрозы и дискриминация в адрес других пользователей или администрации бота. Будьте вежливы и конструктивны.
2.  **Спам.** Запрещено злоупотребление командами, флуд и спам сообщениями. Автоматическая система может временно заблокировать вас за чрезмерную активность.
3.  **Багоюз.** Категорически запрещено использование любых ошибок (багов) бота для получения нечестного преимущества или нарушения игрового баланса. Нарушение приведет к обнулению прогресса и блокировке.
4.  **Махинации.** Запрещены любые попытки обмана бота или других игроков, а также продажа/покупка игровой валюты за реальные деньги.
5.  **Сторонние программы.** Использование сторонних программ, скриптов, автокликеров или макросов для автоматизации действий в боте (например, для работы или майнинга) запрещено.
6.  **Конфиденциальность.** Не пытайтесь получить доступ к чужим данным или нарушить работу бота.
7.  **Администрация.** Решения администрации бота являются окончательными и не подлежат обсуждению. В случае несогласия, используйте команду `/offer` для подачи апелляции.
8.  **Ответственность.** Вы несете полную ответственность за свой аккаунт и действия, совершенные с него.
9.  **Технические проблемы.** В случае обнаружения ошибок или сбоев, немедленно сообщите об этом администрации через команду `/offer`.
"""

sql = database.cursor()

sql.execute("CREATE TABLE IF NOT EXISTS chats (chat_id BIGINT PRIMARY KEY, peer_id BIGINT, owner_id BIGINT, chat_title TEXT, welcome TEXT, invite_kick INTEGER, leave_kick INTEGER, in_pull INTEGER, silence INTEGER, filter INTEGER, antiflood INTEGER, chat_type TEXT DEFAULT 'def', bot_rules TEXT, project_info TEXT, games INTEGER DEFAULT 1, ignore_commands INTEGER DEFAULT 0);")
# Удалена дублирующаяся строка CREATE TABLE IF NOT EXISTS chats
# Миграция для старых баз (если колонки отсутствуют)
try:
    sql.execute("ALTER TABLE chats ADD COLUMN chat_type TEXT DEFAULT 'def';")
except:
    pass
try:
    sql.execute("ALTER TABLE chats ADD COLUMN bot_rules TEXT;")
except:
    pass
try:
    sql.execute("ALTER TABLE chats ADD COLUMN project_info TEXT;")
except:
    pass
try:
    sql.execute("ALTER TABLE chats ADD COLUMN rules TEXT;")
    sql.execute("ALTER TABLE chats ADD COLUMN project_info TEXT;")
except:
    pass
try:
    sql.execute("ALTER TABLE chats ADD COLUMN games INTEGER DEFAULT 1;")
except:
    pass
try:
    sql.execute("ALTER TABLE chats ADD COLUMN maint_ignore INTEGER DEFAULT 0;")
except:
    pass
try:
    sql.execute("ALTER TABLE chats ADD COLUMN ignore_commands INTEGER DEFAULT 0;")
except:
    pass
try:
    sql.execute("ALTER TABLE chats ADD COLUMN autopost INTEGER DEFAULT 1;")
except:
    pass
try:
    sql.execute("ALTER TABLE chats ADD COLUMN chat_title TEXT;")
except:
    pass
try:
    sql.execute("ALTER TABLE chats ADD COLUMN link_filter INTEGER DEFAULT 1;")
except:
    pass
sql.execute("CREATE TABLE IF NOT EXISTS clans (clan_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, owner_id BIGINT, tag TEXT, level INTEGER DEFAULT 1, exp INTEGER DEFAULT 0, money BIGINT DEFAULT 0, mats BIGINT DEFAULT 0, max_mats BIGINT DEFAULT 0, r0_name TEXT DEFAULT 'Участник', r1_name TEXT DEFAULT 'Модератор', r2_name TEXT DEFAULT 'Заместитель', r3_name TEXT DEFAULT 'Лидер', r4_name TEXT DEFAULT 'Боец', r5_name TEXT DEFAULT 'Старейшина');")
try:
    sql.execute("ALTER TABLE clans ADD COLUMN r4_name TEXT DEFAULT 'Боец';")
except:
    pass
try:
    sql.execute("ALTER TABLE clans ADD COLUMN r5_name TEXT DEFAULT 'Старейшина';")
except:
    pass
sql.execute("CREATE TABLE IF NOT EXISTS clan_wars (war_id INTEGER PRIMARY KEY AUTOINCREMENT, attacker_id INTEGER, defender_id INTEGER, attacker_score INTEGER DEFAULT 0, defender_score INTEGER DEFAULT 0, status TEXT DEFAULT 'active', start_time INTEGER, target_biz_id INTEGER DEFAULT 0);")
try:
    sql.execute("CREATE TABLE IF NOT EXISTS user_data (user_id BIGINT PRIMARY KEY, age INTEGER DEFAULT 0, has_pc INTEGER DEFAULT 0, discord TEXT DEFAULT 'Не указан', discord_numeric_id TEXT DEFAULT 'Не указан', forum TEXT DEFAULT 'Не указан', points INTEGER DEFAULT 0, last_appointment TEXT DEFAULT '0', global_ban INTEGER DEFAULT 0, aban INTEGER DEFAULT 0, preds INTEGER DEFAULT 0);") # Дублирующаяся строка, но сохранена для миграции
    sql.execute("ALTER TABLE user_data ADD COLUMN clan_id INTEGER DEFAULT 0;")
except:
    pass
try:
    sql.execute("ALTER TABLE user_data ADD COLUMN clan_rank TEXT DEFAULT 'Участник';")
except:
    pass
try:
    sql.execute("ALTER TABLE user_data ADD COLUMN referrer_id BIGINT DEFAULT 0;")
except:
    pass
try:
    sql.execute("ALTER TABLE user_data ADD COLUMN last_clan_mine INTEGER DEFAULT 0;")
except:
    pass
try:
    sql.execute("ALTER TABLE clan_wars ADD COLUMN end_time INTEGER;")
except:
    pass
try:
    sql.execute("ALTER TABLE clan_wars ADD COLUMN target_biz_id INTEGER DEFAULT 0;")
except:
    pass
try:
    sql.execute("ALTER TABLE clans ADD COLUMN type TEXT DEFAULT 'closed';")
except:
    pass
try:
    sql.execute("ALTER TABLE user_data ADD COLUMN last_clan_attack INTEGER DEFAULT 0;")
except:
    pass
try:
    sql.execute("ALTER TABLE clans ADD COLUMN treasury INTEGER DEFAULT 1;")
except:
    pass
try:
    sql.execute("ALTER TABLE clans ADD COLUMN r0_salary BIGINT DEFAULT 0;")
    sql.execute("ALTER TABLE clans ADD COLUMN r1_salary BIGINT DEFAULT 0;")
    sql.execute("ALTER TABLE clans ADD COLUMN r2_salary BIGINT DEFAULT 0;")
    sql.execute("ALTER TABLE clans ADD COLUMN r3_salary BIGINT DEFAULT 0;")
    sql.execute("ALTER TABLE clans ADD COLUMN r4_salary BIGINT DEFAULT 0;")
    sql.execute("ALTER TABLE clans ADD COLUMN r5_salary BIGINT DEFAULT 0;")
except:
    pass
sql.execute("CREATE TABLE IF NOT EXISTS clan_quests (clan_id INTEGER, quest_type TEXT, target INTEGER, progress INTEGER DEFAULT 0, reward_mats INTEGER, reward_exp INTEGER, status TEXT DEFAULT 'active', date TEXT, PRIMARY KEY(clan_id, date));")
try:
    sql.execute("ALTER TABLE clans ADD COLUMN tactic TEXT DEFAULT 'none';")
except:
    pass
try:
    sql.execute("ALTER TABLE clans ADD COLUMN tactic_end INTEGER DEFAULT 0;")
except:
    pass
try:
    sql.execute("ALTER TABLE clans ADD COLUMN wins INTEGER DEFAULT 0;")
except:
    pass

sql.execute("CREATE TABLE IF NOT EXISTS global_managers (user_id BIGINT PRIMARY KEY, level INTEGER);")
sql.execute("CREATE TABLE IF NOT EXISTS global_settings (key TEXT PRIMARY KEY, value TEXT);")
sql.execute("CREATE TABLE IF NOT EXISTS economy_users (chat_id BIGINT DEFAULT 0, user_id BIGINT, balance BIGINT DEFAULT 0, bank BIGINT DEFAULT 0, vip INTEGER DEFAULT 0, vip_level INTEGER DEFAULT 0, vip_until TEXT, daily_claimed_date TEXT, charity BIGINT DEFAULT 0, duels_won INTEGER DEFAULT 0, duels_lost INTEGER DEFAULT 0, duels_sum_won BIGINT DEFAULT 0, duels_sum_lost BIGINT DEFAULT 0, transfers_sent INTEGER DEFAULT 0, transfers_received INTEGER DEFAULT 0, transfers_sum_sent BIGINT DEFAULT 0, transfers_sum_received BIGINT DEFAULT 0, job INTEGER DEFAULT 0, last_job_time INTEGER DEFAULT 0, job_level INTEGER DEFAULT 1, job_exp INTEGER DEFAULT 0, used_promos TEXT DEFAULT '[]', deposits TEXT DEFAULT '[]', extras TEXT DEFAULT '{}', PRIMARY KEY(chat_id, user_id));")
sql.execute("CREATE TABLE IF NOT EXISTS economy_settings (chat_id BIGINT DEFAULT 0, key TEXT, value TEXT, PRIMARY KEY(chat_id, key));")
sql.execute("CREATE TABLE IF NOT EXISTS economy_stats (chat_id BIGINT DEFAULT 0, key TEXT, value TEXT, PRIMARY KEY(chat_id, key));")
sql.execute("CREATE TABLE IF NOT EXISTS chat_roles (chat_id BIGINT, name TEXT, priority INTEGER, PRIMARY KEY(chat_id, name));")
# Глобальные блокировки (гбан / гбанпл)
sql.execute("CREATE TABLE IF NOT EXISTS global_bans (user_id BIGINT PRIMARY KEY, ban_type TEXT, moder BIGINT, reason TEXT, date TEXT);")
sql.execute("CREATE TABLE IF NOT EXISTS clan_alliances (clan1 INTEGER, clan2 INTEGER, PRIMARY KEY(clan1, clan2));")
sql.execute("CREATE TABLE IF NOT EXISTS clan_ally_requests (from_clan INTEGER, to_clan INTEGER, PRIMARY KEY(from_clan, to_clan));")
    sql.execute("CREATE TABLE IF NOT EXISTS businesses (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, price BIGINT, profit_per_hour BIGINT, owner_id BIGINT DEFAULT 0, type TEXT DEFAULT 'default', last_collect INTEGER DEFAULT 0, active_route INTEGER DEFAULT 1, repair_until INTEGER DEFAULT 0, special_order_active INTEGER DEFAULT 0, clan_owner_id INTEGER DEFAULT 0, level INTEGER DEFAULT 1, tax_due_at INTEGER DEFAULT 0);")

    # Auctions table: stores active and finished auctions for businesses
    sql.execute(
        "CREATE TABLE IF NOT EXISTS auctions (id INTEGER PRIMARY KEY AUTOINCREMENT, biz_id INTEGER, seller_id BIGINT, start_time INTEGER, end_time INTEGER, min_bid BIGINT, highest_bid BIGINT DEFAULT 0, highest_bidder BIGINT DEFAULT 0, status TEXT DEFAULT 'active')"
    )
    sql.execute("CREATE INDEX IF NOT EXISTS idx_auctions_biz ON auctions(biz_id)")
try:
    sql.execute("ALTER TABLE businesses ADD COLUMN level INTEGER DEFAULT 1;")
except:
    pass
try:
    sql.execute("ALTER TABLE businesses ADD COLUMN tax_due_at INTEGER DEFAULT 0;")
except Exception as e:
    if "duplicate column name" not in str(e).lower():
        logging.error(f"Migration Error (tax_due_at): {e}")

database.commit()

# --- БЛОК АВТОМАТИЧЕСКОГО ВОССТАНОВЛЕНИЯ БИЗНЕСОВ ---
sql.execute("SELECT COUNT(*) FROM businesses")
if sql.fetchone()[0] == 0:
    # Если таблица пуста (после удаления), заполняем её стандартным набором (11 штук)
    default_businesses = [
        ("Курский вокзал", 10500000, 525000, "station"),
        ("Павелецкий вокзал", 10500000, 525000, "station"),
        ("Белорусский вокзал", 10500000, 525000, "station"),
        ("Рижский вокзал", 10500000, 525000, "station"),
        ("Казанский вокзал", 10500000, 525000, "station"),
        ("Шаурмичная у шахида🌯", 100000, 5000, "default"),
        ("Магазин 24/7🛒", 250000, 12500, "default"),
        ("Кофейня «У Палыча»☕", 500000, 25000, "default"),
        ("АЗС «ГазМяс»⛽", 1500000, 75000, "default"),
        ("ТЦ «Мармелад»🛍️", 5000000, 250000, "default"),
        ("IT-Компания «Skynet»💻", 25000000, 1250000, "default"),
        ("Нефтяная вышка ⛽", 100000000, 5000000, "default"),
        ("Космодром 🚀", 500000000, 14000000, "default"),
        ("Остров «Bora-Bora» 🏝️", 1000000000, 28000000, "default"),
        ("Гипермаркет «Лента»🛒", 15000000, 750000, "default"),
        ("Отель «Mariott»🏨", 35000000, 1750000, "default"),
        ("Ночной клуб «Status»🕺", 8000000, 400000, "default")
    ]
    sql.executemany("INSERT INTO businesses (name, price, profit_per_hour, type) VALUES (?, ?, ?, ?)", default_businesses)
    database.commit()

sql.execute("CREATE TABLE IF NOT EXISTS biz_offers (id INTEGER PRIMARY KEY AUTOINCREMENT, biz_id INTEGER, from_id BIGINT, to_id BIGINT, price BIGINT);")
sql.execute("CREATE TABLE IF NOT EXISTS clan_bosses (clan_id INTEGER PRIMARY KEY, boss_id INTEGER, current_hp INTEGER, max_hp INTEGER, start_time INTEGER, end_time INTEGER);")
sql.execute("CREATE TABLE IF NOT EXISTS clan_boss_attacks (clan_id INTEGER, user_id INTEGER, damage INTEGER, PRIMARY KEY(clan_id, user_id));")
sql.execute("CREATE TABLE IF NOT EXISTS pets (user_id BIGINT PRIMARY KEY, pet_id INTEGER, name TEXT, level INTEGER DEFAULT 1, exp INTEGER DEFAULT 0, hunger INTEGER DEFAULT 100, energy INTEGER DEFAULT 100, last_update INTEGER DEFAULT 0);")
sql.execute("CREATE TABLE IF NOT EXISTS user_roles (chat_id BIGINT, user_id BIGINT, role_name TEXT, PRIMARY KEY(chat_id, user_id));")
sql.execute("CREATE TABLE IF NOT EXISTS command_perms (chat_id BIGINT, command TEXT, priority INTEGER, PRIMARY KEY(chat_id, command));")
sql.execute("CREATE TABLE IF NOT EXISTS user_commands (chat_id BIGINT, user_id BIGINT, command TEXT, PRIMARY KEY(chat_id, user_id, command));")
sql.execute("CREATE TABLE IF NOT EXISTS notop_users (user_id BIGINT PRIMARY KEY);")
sql.execute("CREATE TABLE IF NOT EXISTS global_banwords (banword TEXT PRIMARY KEY);")
sql.execute("CREATE TABLE IF NOT EXISTS banned_chats (chat_id BIGINT PRIMARY KEY, reason TEXT, moder_id BIGINT, date TEXT);")
sql.execute("CREATE TABLE IF NOT EXISTS blacklist (user_id BIGINT PRIMARY KEY, reason TEXT, moder_id BIGINT, date TEXT);")
sql.execute("CREATE TABLE IF NOT EXISTS report_bans (user_id BIGINT PRIMARY KEY, reason TEXT, moder_id BIGINT, date TEXT, end_time INTEGER DEFAULT 0);")
sql.execute("CREATE TABLE IF NOT EXISTS support_tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id BIGINT, type TEXT, text TEXT, date TEXT, status TEXT DEFAULT 'pending', chat_id BIGINT, target_id BIGINT);")
# Система тестировщиков
sql.execute("CREATE TABLE IF NOT EXISTS testers (user_id BIGINT PRIMARY KEY, level INTEGER, handled INTEGER DEFAULT 0);")
try:
    sql.execute("ALTER TABLE support_tickets ADD COLUMN tester_id BIGINT DEFAULT 0;")
except: pass
try:
    sql.execute("ALTER TABLE support_tickets ADD COLUMN attachment TEXT;")
except: pass
try:
    sql.execute("ALTER TABLE support_tickets ADD COLUMN tester_comment TEXT;")
except: pass

sql.execute("CREATE TABLE IF NOT EXISTS clan_boss_cooldowns (clan_id INTEGER, boss_id INTEGER, ready_at INTEGER, PRIMARY KEY(clan_id, boss_id));")


# # Добавить end_time в таблицу report_bans
try:
    sql.execute("ALTER TABLE report_bans ADD COLUMN end_time INTEGER DEFAULT 0;")
except sqlite3.OperationalError:
    pass # Столбец уже существует
database.commit()

user_states = {}
user_casino_cooldown = {}
user_clan_war_cooldown = {}
user_duel_cooldown = {}
user_boss_cooldown = {}
user_pet_cooldown = {}

async def get_priority(user_id, chat_id):
    try:
        # Проверка на заморозку прав (aban)
        sql.execute("SELECT aban FROM user_data WHERE user_id = ?", (user_id,))
        a_res = sql.fetchone()
        if a_res and a_res[0] == 1: return 0

        sql.execute("SELECT level FROM global_managers WHERE user_id = ?", (user_id,))
        fetch = sql.fetchone()
        if fetch:
            lvl = fetch[0]
            if lvl == 1: return 21  # Модератор
            if lvl == 2: return 41  # Ст. Модератор
            if lvl == 3: return 51  # Администратор
            if lvl == 4: return 61  # Ст. Администратор
            if lvl >= 5: return 200 # Разработчик
            return 0 # Если уровень не соответствует известным, возвращаем 0
        
        if chat_id is None:
            return 0
        
        sql.execute("SELECT owner_id FROM chats WHERE chat_id = ?", (chat_id,))
        fetch = sql.fetchone()
        if fetch and fetch[0] == user_id: return 100
        
        sql.execute("SELECT r.priority, r.name FROM user_roles ur JOIN chat_roles r ON ur.chat_id = r.chat_id AND ur.role_name = r.name WHERE ur.chat_id = ? AND ur.user_id = ?", (chat_id, user_id))
        fetch = sql.fetchone()
        if fetch: return fetch[0]
        # Проверка старой таблицы permissions (если она еще используется)
        sql.execute(f"SELECT level FROM permissions_{chat_id} WHERE user_id = ?", (user_id,))
        fetch = sql.fetchone()
        if fetch: # Если пользователь найден в старой таблице permissions
            lv_map = {0:0, 1:21, 2:41, 3:51, 4:61, 5:100}
            return lv_map.get(fetch[0], 0)
        return 0
    except Exception:
        return 0

async def get_tester_role(user_id: int) -> int:
    """Возвращает уровень прав тестировщика (0-3)."""
    sql.execute("SELECT level FROM testers WHERE user_id = ?", (user_id,))
    res = sql.fetchone()
    return res[0] if res else 0

async def get_global_role(user_id: int) -> int:
    """Возвращает уровень глобальной роли пользователя (0-4)."""
    sql.execute("SELECT level FROM global_managers WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    return fetch[0] if fetch else 0

_priority_to_level = {0: 0, 21: 1, 41: 2, 51: 3, 61: 4, 100: 5, 120: 5, 140: 5, 160: 5, 180: 5, 200: 6}

async def get_role(user_id: int, chat_id: int) -> int:
    p = await get_priority(user_id, chat_id)
    return _priority_to_level.get(p, 0)

async def get_custom_role_name(user_id, chat_id):
    sql.execute("SELECT role_name FROM user_roles WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    fetch = sql.fetchone()
    return fetch[0] if fetch else None

async def check_perm(user_id, chat_id, command, def_lvl):
    p = await get_priority(user_id, chat_id)
    if p >= 200: return True
    
    sql.execute("SELECT 1 FROM user_commands WHERE chat_id = ? AND user_id = ? AND command = ?", (chat_id, user_id, command.lower()))
    if sql.fetchone(): return True
    
    sql.execute("SELECT priority FROM command_perms WHERE chat_id = ? AND command = ?", (chat_id, command.lower()))
    fetch = sql.fetchone()
    if fetch: return p >= fetch[0]
    lvls = {0:0, 1:21, 2:41, 3:51, 4:61, 5:100, 6:200}
    return p >= lvls.get(def_lvl, 0)

sql.execute("CREATE TABLE IF NOT EXISTS user_data (user_id BIGINT PRIMARY KEY, age INTEGER DEFAULT 0, has_pc INTEGER DEFAULT 0, discord TEXT DEFAULT 'Не указан', discord_numeric_id TEXT DEFAULT 'Не указан', forum TEXT DEFAULT 'Не указан', points INTEGER DEFAULT 0, last_appointment TEXT DEFAULT '0', global_ban INTEGER DEFAULT 0, aban INTEGER DEFAULT 0, preds INTEGER DEFAULT 0);")
# Миграция для добавления колонки position
try:
    sql.execute("ALTER TABLE user_data ADD COLUMN position TEXT DEFAULT 'Не указана';")
except:
    pass
try:
    sql.execute("ALTER TABLE user_data ADD COLUMN discord_numeric_id TEXT DEFAULT 'Не указан';")
except:
    pass
try:
    sql.execute("ALTER TABLE user_data ADD COLUMN last_clan_salary INTEGER DEFAULT 0;")
except:
    pass
try:
    sql.execute("ALTER TABLE user_data ADD COLUMN clan_mats_mined INTEGER DEFAULT 0;")
except:
    pass
try:
    sql.execute("ALTER TABLE user_data ADD COLUMN clan_war_points INTEGER DEFAULT 0;")
except:
    pass
try:
    sql.execute("ALTER TABLE user_data ADD COLUMN custom_prefix TEXT DEFAULT NULL;")
except: pass
try:
    sql.execute("ALTER TABLE user_data ADD COLUMN inventory TEXT DEFAULT '[]';")
except: pass
try:
    sql.execute("ALTER TABLE user_data ADD COLUMN ref_cancel_count INTEGER DEFAULT 0;")
except: pass
try:
    sql.execute("ALTER TABLE user_data ADD COLUMN no_comm_until INTEGER DEFAULT 0;")
except: pass
try:
    sql.execute("ALTER TABLE user_data ADD COLUMN biz_slots INTEGER DEFAULT 0;")
except: pass
try:
    sql.execute("ALTER TABLE user_data ADD COLUMN has_notop INTEGER DEFAULT 0;")
except: pass
database.commit()

# --- GOOGLE SHEETS СИНХРОН ---
# Список таблиц для синхронизации (обновлено)
SHEETS_CONFIG = [
    {
        "name": "CHEREPOVETS | Модерация Discord", # Название первой таблицы
        "sheet_chat_id": 8, # ID беседы с никами (Журнал) для первой таблицы
        "apps_chat_id": 2000000000 + 26, # Беседа для заявок первой таблицы
        "worksheet_name": "Отчетность",
        "apps_worksheet_name": "Набор на младшего модератора",
        "vk_id_col": 0,      # Столбец A (Игровой Nick_Name)
        "points_col": 17,    # Столбец L
        "app_col": 2,        # Столбец C (Отметка времени)
        "last_app_row": 0,
        "sync_columns": {
             "age": 4,        # Столбец E
             "discord": 9,    # Столбец G
             "forum": 10,      # Столбец J
             "position": 1,  # Столбец L
             "points": 17     # Столбец L (если баллы там же)
         },
        "app_form_cols": {
            "date": 0, "email": 1, "name_age": 3, "nick": 4, "discord_id": 5,
            "discord_tag": 6, "vk_link": 7, "forum_link": 8, "telegram_link": 9,
            "timezone": 10, "current_position": 11, "experience": 12, "goals": 13,
            "time_available": 14, "why_you": 15
        }
    },
    # {
    #     "name": "UFA | Модерация Discord", 
    #     "sheet_chat_id": 12, 
    #     "apps_chat_id": 2000000020,
    #     "worksheet_name": "Список состава",
    #     "apps_worksheet_name": "Заявки",
    #     "vk_id_col": 0,
    #     "points_col": 5,
    #     "app_col": 6,
    #     "last_app_row": 0,
    #     "app_form_cols": {
    #         "date": 1, "email": 2, "discord_id": 3, "name_age": 4, "timezone": 5,
    #         "nick": 6, "vk_link": 7, "forum_link": 8, "telegram_link": 9,
    #         "current_position": 10, "experience": 11, "goals": 12, "time_available": 12,
    #         "why_you": 13
    #     }
    # },
    {
        "name": "ASTANA | Модерация Discord",
        "sheet_chat_id": 48,
        "apps_chat_id": 2000000052,
        "worksheet_name": "Модераторы",
        "apps_worksheet_name": "Заявки",
        "vk_id_col": 0,
        "points_col": 8,
        "app_col": 3,
        "last_app_row": 0,
        "sync_columns": {
            "age": 18,        # Столбец Q
            "discord": 10,     # Столбец I (Discord Tag)
            "discord_id": 9, # Столбец K (Discord Numeric ID)
            "forum": 5,       # Столбец J (Ссылка на форум)
            "position": 1,   # Столбец L (индекс 11)
            "points": 8,     # Столбец I (Баллы)
            "warns": 6,       # Столбец G (выговоры)
            "preds": 7        # Столбец H (преды)
        },
        "app_form_cols": {
            "date": 2, "email": 3, "discord_id": 6, "name_age": 4, "timezone": 100,
            "nick": 5, "vk_link": 8, "forum_link": 9, "telegram_link": 10,
            "current_position": 11, "experience": 12, "goals": 13, "time_available": 14,
            "why_you": 15, "discord_tag": 7
        }
    }
]

gs_client = None
try:
    if 'gspread' in globals():
        # Используем современный метод авторизации gspread (обновлено)
        gs_client = gspread.service_account(filename="cred.json")
except Exception as e:
    print(f"Google Sheets Setup Error: {e}")

async def sync_user_to_sheet(user_id, chat_id, field_type, value):
    """Updates Google Sheet when points, warns or preds change in bot"""
    if not gs_client: return

    loop = asyncio.get_running_loop()

    for config in SHEETS_CONFIG:
        # Синхронизируем только если chat_id совпадает с настройками таблицы
        if config["sheet_chat_id"] != chat_id: continue

        try:
            # Сначала получаем ник пользователя из базы данных конкретного чата
            user_nick = None
            try:
                sql.execute(f"SELECT nick FROM nicks_{config['sheet_chat_id']} WHERE user_id = ?", (user_id,))
                res = sql.fetchone()
                if res: user_nick = res[0]
            except Exception: pass
            
            if not user_nick: 
                # Пользователя нет в никах этой беседы, пропускаем эту таблицу
                continue 

            def _update(conf, u_nick, f_type, val):
                sh = gs_client.open(conf["name"])
                ws = sh.worksheet(conf["worksheet_name"])
                cell = ws.find(u_nick)
                if cell:
                    col_idx = 0
                    formatted_val = str(val)
                    s_cols = conf.get("sync_columns", {})
                    
                    if f_type == 'points': 
                        col_idx = conf["points_col"] + 1
                    elif f_type == 'warns' and 'warns' in s_cols: 
                        col_idx = s_cols["warns"] + 1
                        formatted_val = f"{val}/3"
                    elif f_type == 'preds' and 'preds' in s_cols:
                        col_idx = s_cols["preds"] + 1
                        formatted_val = f"{val}/2"
                    elif f_type == 'last_appointment':
                        col_idx = conf["app_col"] + 1
                        try:
                            dt = datetime.fromisoformat(str(val))
                            formatted_val = dt.strftime("%d.%m.%Y")
                        except: pass
                    elif f_type in s_cols:
                        col_idx = s_cols[f_type] + 1

                    if col_idx > 0:
                        ws.update_cell(cell.row, col_idx, formatted_val)
                        logging.info(f"[GSPREAD] Поле {f_type} для '{u_nick}' в таблице '{conf['name']}' успешно обновлено: {formatted_val}")
                    return True
                return False
            
            await loop.run_in_executor(None, _update, config, user_nick, field_type, value)

        except Exception as e:
            logging.error(f"[GSPREAD] Ошибка синхронизации {field_type} для '{user_id}': {e}")
 
async def sync_data_from_sheet(config):
    """Syncs points and appointment dates from a single sheet config."""
    loop = asyncio.get_running_loop()
    
    def _get_updates_from_sheet(conf):
        sh = gs_client.open(conf["name"])
        ws_report = sh.worksheet(conf["worksheet_name"])
        rows = ws_report.get_all_values()
        updates = []
        s_cols = conf.get("sync_columns", {})

        for i in range(1, len(rows)):
            row = rows[i]
            if len(row) <= conf["points_col"]: continue
            
            nick_str = row[conf["vk_id_col"]].strip()
            
            # Собираем данные по маппингу
            data = {
                "points": row[conf["points_col"]].strip() if len(row) > conf["points_col"] else "0",
                "last_app": row[conf["app_col"]] if len(row) > conf["app_col"] else "",
                "age": row[s_cols['age']] if 'age' in s_cols and len(row) > s_cols['age'] else None,
                "discord": row[s_cols['discord']] if 'discord' in s_cols and len(row) > s_cols['discord'] else None,
                "discord_numeric_id": row[s_cols['discord_id']] if 'discord_id' in s_cols and len(row) > s_cols['discord_id'] else None,
                "forum": row[s_cols['forum']] if 'forum' in s_cols and len(row) > s_cols['forum'] else None,
                "position": row[s_cols['position']] if 'position' in s_cols and len(row) > s_cols['position'] else None,
                "warns": row[s_cols['warns']].split('/')[0] if 'warns' in s_cols and len(row) > s_cols['warns'] else None,
                "preds": row[s_cols['preds']].split('/')[0] if 'preds' in s_cols and len(row) > s_cols['preds'] else None,
            }
            
            if nick_str:
                updates.append((nick_str, data))
        return updates

    sheet_updates = await loop.run_in_executor(None, _get_updates_from_sheet, config)

    # Применяем все обновления из таблицы в базу данных бота
    for nick, data in sheet_updates:
        try:
            sql.execute(f"SELECT user_id FROM nicks_{config['sheet_chat_id']} WHERE nick = ?", (nick,))
            res = sql.fetchone()
            if res:
                uid = res[0]
                ud = await get_user_data(uid)
                
                updates_sql = []
                values_sql = []

                # 1. Синхронизация баллов
                p_str = data['points'].replace(" ", "")
                p_val = int(p_str) if p_str.isdigit() else 0
                if ud['points'] != p_val:
                    updates_sql.append("points = ?")
                    values_sql.append(p_val)
                
                # 2. Синхронизация даты (превращаем в ISO формат)
                if data['last_app']:
                    iso_date = None
                    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d-%m-%Y"):
                        try:
                            dt = datetime.strptime(data['last_app'].strip(), fmt)
                            iso_date = dt.strftime("%Y-%m-%d")
                            break
                        except ValueError: continue
                    
                    if iso_date and ud['last_appointment'] != iso_date:
                        updates_sql.append("last_appointment = ?")
                        values_sql.append(iso_date)

                # 3. Синхронизация остальных полей
                if data['age'] and data['age'].isdigit() and ud['age'] != int(data['age']):
                    updates_sql.append("age = ?"); values_sql.append(int(data['age']))
                
                if data['discord'] and ud['discord'] != data['discord']:
                    updates_sql.append("discord = ?"); values_sql.append(data['discord'])

                if data['discord_numeric_id'] and ud['discord_numeric_id'] != data['discord_numeric_id']:
                    updates_sql.append("discord_numeric_id = ?"); values_sql.append(data['discord_numeric_id'])
                
                if data['forum'] and ud['forum'] != data['forum']:
                    updates_sql.append("forum = ?"); values_sql.append(data['forum'])
                
                if data['position'] and ud['position'] != data['position']:
                    updates_sql.append("position = ?"); values_sql.append(data['position'])

                # 4. Синхронизация выговоров и предов
                if data['warns'] and data['warns'].isdigit():
                    w_val = int(data['warns'])
                    sql.execute(f"UPDATE warns_{config['sheet_chat_id']} SET count = ? WHERE user_id = ?", (w_val, uid))
                
                if data['preds'] and data['preds'].isdigit():
                    p_val = int(data['preds'])
                    if ud['preds'] != p_val:
                        updates_sql.append("preds = ?"); values_sql.append(p_val)

                if updates_sql:
                    values_sql.append(uid)
                    sql.execute(f"UPDATE user_data SET {', '.join(updates_sql)} WHERE user_id = ?", tuple(values_sql))
                    database.commit()
        except Exception: pass

async def google_sheets_loop():
    """Background task to sync Sheet -> DB and check new apps"""
    if not gs_client: return
    logging.info("[GSPREAD] Запуск цикла синхронизации Google Sheets.")
    # Инициализация: считаем текущие строки, чтобы не спамить старыми заявками
    loop = asyncio.get_running_loop()
    for config in SHEETS_CONFIG:
        if config["sheet_chat_id"] == 0: continue
        try:
            def _init_rows(conf):
                sh = gs_client.open(conf["name"])
                ws = sh.worksheet(conf["apps_worksheet_name"])
                logging.info(f"[GSPREAD] Подключено к '{conf['name']}' (ID: {sh.id})")
                vals = ws.get_all_values()
                for i in range(len(vals) - 1, -1, -1):
                    if any(str(cell).strip() for cell in vals[i]):
                        return i + 1
                return 0
            config["last_app_row"] = await loop.run_in_executor(None, _init_rows, config)
        except Exception as e:
            logging.error(f"[GSPREAD] Ошибка инициализации таблицы '{config['name']}': {e}")

    while True:
        for config in SHEETS_CONFIG:
            if config["sheet_chat_id"] == 0: continue
            if config["apps_chat_id"] == 0:
                logging.warning(f"[GSPREAD] Пропущена проверка заявок для {config['name']}: apps_chat_id не установлен (равен 0).")
                continue
            
            try:
                # 1. Синхронизация баллов и дат
                await sync_data_from_sheet(config)
                
                # 2. Проверка новых заявок
                def _check_apps_task(conf):
                    sh = gs_client.open(conf["name"])
                    ws_apps = sh.worksheet(conf["apps_worksheet_name"])
                    all_apps = ws_apps.get_all_values()
                    return all_apps
                
                all_apps = await loop.run_in_executor(None, _check_apps_task, config)
                
                # Обработка заявок
                real_count = 0
                for i in range(len(all_apps) - 1, -1, -1):
                    if any(str(cell).strip() for cell in all_apps[i]):
                        real_count = i + 1
                        break
                current_count = real_count
                
                if current_count < config["last_app_row"]:
                    config["last_app_row"] = current_count
                elif current_count > config["last_app_row"]:
                    new_rows = all_apps[config["last_app_row"]:current_count]
                    for row in new_rows:
                        # Пропускаем строки, которые не содержат никакой полезной информации (пустые или только с пробелами)
                        if not any(str(cell).strip() for cell in row):
                            logging.debug(f"[GSPREAD] Пропущена пустая «фантомная» строка в {config['name']}.")
                            continue
                        try:
                            app_cols = config.get("app_form_cols", {})
                            def get_col(idx): return row[idx] if len(row) > idx else "-"
                            msg = (f"📝 Новая заявка ({config['name']})!\n\n" # Updated message structure
                                   f"📅 Отметка времени: {get_col(app_cols['date'])}\n"
                                   f"📧 Почта: {get_col(app_cols['email'])}\n"
                                   f"🆔 Discord ID: {get_col(app_cols['discord_id'])}\n"
                                   f"👤 Имя и возраст: {get_col(app_cols['name_age'])}\n"
                                   f"⏰ Часовой пояс: {get_col(app_cols['timezone'])}\n" # New field
                                   f"🎮 Ник: {get_col(app_cols['nick'])}\n"
                                   f"🔗 VK: {get_col(app_cols['vk_link'])}\n"
                                   f"📖 Форум: {get_col(app_cols['forum_link'])}\n"
                                   f"✈ Telegram: {get_col(app_cols['telegram_link'])}\n\n"
                                   f"💼 Активная должность: {get_col(app_cols['current_position'])}\n"
                                   f"📁 Есть опыт: {get_col(app_cols['experience'])}\n"
                                   f"⚜️ Цели на посту: {get_col(app_cols['goals'])}\n"
                                   f"⏰ Время: {get_col(app_cols['time_available'])}\n"
                                   f"❓ Почему именно вы: {get_col(app_cols['why_you'])}")
                        except Exception as format_e:
                            logging.error(f"[GSPREAD] Ошибка при формировании сообщения для {config['name']} из строки: {row} - {format_e}")
                            msg = f"📝 Новая заявка ({config['name']}) (ошибка форматирования)!\n\n" + "\n".join([f"🔹 {col}" for col in row if col.strip()])

                        try: await bot.api.messages.send(peer_id=config["apps_chat_id"], message=msg, random_id=0, disable_mentions=1) # Отправляем уведомление
                        except Exception as ex: logging.error(f"Failed to send app notification: {ex}")

                    if new_rows:
                        logging.info(f"[GSPREAD] Обработано новых заявок: {len(new_rows)} в таблице '{config['name']}'.")
                    config["last_app_row"] = current_count
            except Exception as e:
                logging.error(f"[GSPREAD] Ошибка в цикле синхронизации для '{config['name']}': {e}")
        
        await asyncio.sleep(60) # Проверка каждую минуту

async def get_user_data(user_id: int):
    sql.execute("SELECT age, has_pc, discord, discord_numeric_id, forum, points, last_appointment, global_ban, aban, preds, position, clan_id, clan_rank, last_clan_mine, last_clan_attack, last_clan_salary, referrer_id, custom_prefix, inventory, ref_cancel_count, no_comm_until, biz_slots, has_notop FROM user_data WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    if fetch is None:
        sql.execute("INSERT INTO user_data (user_id) VALUES (?)", (user_id,))
        database.commit()
        return {
            'age': 0, 'has_pc': 0, 'discord': 'Не указан', 'discord_numeric_id': 'Не указан',
            'forum': 'Не указан', 'points': 0, 'last_appointment': '0', 'global_ban': 0,
            'aban': 0, 'preds': 0, 'position': 'Не указана', 'clan_id': 0, 'clan_rank': 'Участник',
            'last_clan_mine': 0, 'last_clan_attack': 0, 'last_clan_salary': 0, 'referrer_id': 0,
            'custom_prefix': None, 'inventory': '[]', 'ref_cancel_count': 0, 'no_comm_until': 0,
            'biz_slots': 0, 'has_notop': 0
        } # Добавлены custom_prefix и inventory
    return {
        'age': fetch[0], 'has_pc': fetch[1], 'discord': fetch[2], 'discord_numeric_id': fetch[3],
        'forum': fetch[4], 'points': fetch[5], 'last_appointment': fetch[6], 'global_ban': fetch[7],
        'aban': fetch[8], 'preds': fetch[9], 'position': fetch[10] if fetch[10] else 'Не указана',
        'clan_id': fetch[11] if len(fetch) > 11 else 0, 'clan_rank': fetch[12] if len(fetch) > 12 else 'Участник',
        'last_clan_mine': fetch[13] if len(fetch) > 13 else 0,
        'last_clan_attack': fetch[14] if len(fetch) > 14 else 0,
        'last_clan_salary': fetch[15] if len(fetch) > 15 else 0,
        'referrer_id': fetch[16] if len(fetch) > 16 else 0,
        'custom_prefix': fetch[17] if len(fetch) > 17 else None,
        'inventory': fetch[18] if len(fetch) > 18 else '[]',
        'ref_cancel_count': fetch[19] if len(fetch) > 19 else 0,
        'no_comm_until': fetch[20] if len(fetch) > 20 else 0,
        'biz_slots': fetch[21] if len(fetch) > 21 else 0,
        'has_notop': fetch[22] if len(fetch) > 22 else 0
    }

async def update_user_data(user_id: int, key: str, value):
    sql.execute(f"UPDATE user_data SET {key} = ? WHERE user_id = ?", (value, user_id))
    database.commit()
    
    # Список ключей, изменения которых должны мгновенно улетать в Google Таблицу
    sync_keys = ['points', 'preds', 'age', 'discord', 'discord_numeric_id', 'forum', 'position', 'last_appointment', 'has_pc']
    
    if key in sync_keys:
        for config in [c for c in SHEETS_CONFIG if c["sheet_chat_id"] > 0]:
            asyncio.create_task(sync_user_to_sheet(user_id, config["sheet_chat_id"], key, value))

def _json_load(value, default):
    if value is None:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _json_dump(value):
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _parse_setting_value(key, value):
    if value is None:
        return None
    if isinstance(value, str):
        if value.startswith('{') or value.startswith('['):
            try:
                return json.loads(value)
            except Exception:
                pass
        if value.isdigit():
            return int(value)
        try:
            return int(value) if '.' not in value else float(value)
        except Exception:
            return value
    return value


def _parse_stat_value(value):
    if value is None:
        return 0
    try:
        return int(value)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return 0


def _default_economy():
    return {
        "users": {},
        "settings": {
            "daily_reward_min": 0,
            "daily_reward_max": 15000,
            "max_bet": 15000000,
            "vip_cost": 50000,
            "vip_duration_days": 30,
            "deposit_percent_min": 3,
            "deposit_percent_max": 3
        },
        "server_stats": { "collected_commissions": 0 }
    }


def _normalize_economy_chat_id(chat_id=0):
    if chat_id is None:
        return 0
    if int(chat_id) == 9:
        return 0
    return int(chat_id)


def _ensure_user_in_economy(economy, user_id_str):
    if user_id_str not in economy['users']:
        economy['users'][user_id_str] = {
            'balance': 0, 'bank': 0, 'vip': False, 'vip_level': 0, 'vip_until': None,
            'daily_claimed': False, 'daily_claimed_date': None, 'charity': 0,
            'deposits': [], 'duels_won': 0, 'duels_lost': 0, 'duels_sum_won': 0,
            'duels_sum_lost': 0, 'transfers_sent': 0, 'transfers_received': 0,
            'transfers_sum_sent': 0, 'transfers_sum_received': 0, 'used_promos': [],
            'job': 0, 'last_job_time': 0, 'job_level': 1, 'job_exp': 0
        }
        return True
    return False


def _economy_user_to_db_row(user_id_str, user_data):
    extras = {}
    known_keys = {
        'balance', 'bank', 'vip', 'vip_level', 'vip_until', 'daily_claimed_date',
        'charity', 'deposits', 'duels_won', 'duels_lost', 'duels_sum_won',
        'duels_sum_lost', 'transfers_sent', 'transfers_received',
        'transfers_sum_sent', 'transfers_sum_received', 'job', 'last_job_time',
        'job_level', 'job_exp', 'used_promos'
    }
    for key, value in user_data.items():
        if key not in known_keys:
            extras[key] = value
    return (
        int(user_id_str),
        int(user_data.get('balance', 0) or 0),
        int(user_data.get('bank', 0) or 0),
        1 if user_data.get('vip') else 0,
        int(user_data.get('vip_level', 0) or 0),
        user_data.get('vip_until'),
        user_data.get('daily_claimed_date'),
        int(user_data.get('charity', 0) or 0),
        int(user_data.get('duels_won', 0) or 0),
        int(user_data.get('duels_lost', 0) or 0),
        int(user_data.get('duels_sum_won', 0) or 0),
        int(user_data.get('duels_sum_lost', 0) or 0),
        int(user_data.get('transfers_sent', 0) or 0),
        int(user_data.get('transfers_received', 0) or 0),
        int(user_data.get('transfers_sum_sent', 0) or 0),
        int(user_data.get('transfers_sum_received', 0) or 0),
        int(user_data.get('job', 0) or 0),
        int(user_data.get('last_job_time', 0) or 0),
        int(user_data.get('job_level', 1) or 1),
        int(user_data.get('job_exp', 0) or 0),
        _json_dump(user_data.get('used_promos', [])),
        _json_dump(user_data.get('deposits', [])),
        _json_dump(extras)
    )


def _economy_db_row_to_user(row):
    (user_id, balance, bank, vip, vip_level, vip_until, daily_claimed_date, charity,
     duels_won, duels_lost, duels_sum_won, duels_sum_lost, transfers_sent,
     transfers_received, transfers_sum_sent, transfers_sum_received, job,
     last_job_time, job_level, job_exp, used_promos, deposits, extras) = row
    user_data = {
        'balance': int(balance or 0),
        'bank': int(bank or 0),
        'vip': bool(vip),
        'vip_level': int(vip_level or 0),
        'vip_until': vip_until,
        'daily_claimed': False,
        'daily_claimed_date': daily_claimed_date,
        'charity': int(charity or 0),
        'deposits': _json_load(deposits, []),
        'duels_won': int(duels_won or 0),
        'duels_lost': int(duels_lost or 0),
        'duels_sum_won': int(duels_sum_won or 0),
        'duels_sum_lost': int(duels_sum_lost or 0),
        'transfers_sent': int(transfers_sent or 0),
        'transfers_received': int(transfers_received or 0),
        'transfers_sum_sent': int(transfers_sum_sent or 0),
        'transfers_sum_received': int(transfers_sum_received or 0),
        'job': int(job or 0),
        'last_job_time': int(last_job_time or 0),
        'job_level': int(job_level or 1),
        'job_exp': int(job_exp or 0),
        'used_promos': _json_load(used_promos, [])
    }
    extras_data = _json_load(extras, {})
    if isinstance(extras_data, dict):
        user_data.update(extras_data)
    return user_data


def _migrate_economy_from_json(chat_id=0):
    chat_id = _normalize_economy_chat_id(chat_id)
    if not os.path.exists('economy.json'):
        return
    sql.execute("SELECT COUNT(*) FROM economy_users WHERE chat_id = ?", (chat_id,))
    if sql.fetchone()[0] > 0:
        return
    try:
        with open('economy.json', 'r', encoding='utf-8') as f:
            old_data = json.load(f)
    except Exception:
        return
    if not isinstance(old_data, dict):
        return
    if 'settings' in old_data:
        for key, value in old_data['settings'].items():
            sql.execute("INSERT OR REPLACE INTO economy_settings (chat_id, key, value) VALUES (?, ?, ?)",
                        (chat_id, key, _json_dump(value)))
    if 'server_stats' in old_data:
        for key, value in old_data['server_stats'].items():
            sql.execute("INSERT OR REPLACE INTO economy_stats (chat_id, key, value) VALUES (?, ?, ?)",
                        (chat_id, key, str(value)))
    if 'users' in old_data:
        for user_id_str, user_data in old_data['users'].items():
            row = _economy_user_to_db_row(user_id_str, user_data)
            sql.execute(
                "INSERT OR REPLACE INTO economy_users (chat_id, user_id, balance, bank, vip, vip_level, vip_until, daily_claimed_date, charity, duels_won, duels_lost, duels_sum_won, duels_sum_lost, transfers_sent, transfers_received, transfers_sum_sent, transfers_sum_received, job, last_job_time, job_level, job_exp, used_promos, deposits, extras) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (chat_id, *row)
            )
    database.commit()
    try:
        os.replace('economy.json', 'economy.json.bak')
    except Exception:
        pass


def load_economy(chat_id=0):
    chat_id = _normalize_economy_chat_id(chat_id)
    economy = _default_economy()
    _migrate_economy_from_json(chat_id)
    sql.execute("SELECT key, value FROM economy_settings WHERE chat_id = ?", (chat_id,))
    for key, value in sql.fetchall():
        economy['settings'][key] = _parse_setting_value(key, value)
    sql.execute("SELECT key, value FROM economy_stats WHERE chat_id = ?", (chat_id,))
    for key, value in sql.fetchall():
        economy['server_stats'][key] = _parse_stat_value(value)
    sql.execute("SELECT user_id, balance, bank, vip, vip_level, vip_until, daily_claimed_date, charity, duels_won, duels_lost, duels_sum_won, duels_sum_lost, transfers_sent, transfers_received, transfers_sum_sent, transfers_sum_received, job, last_job_time, job_level, job_exp, used_promos, deposits, extras FROM economy_users WHERE chat_id = ?", (chat_id,))
    for row in sql.fetchall():
        economy['users'][str(row[0])] = _economy_db_row_to_user(row)
    return economy


def save_economy(data, chat_id=0):
    chat_id = _normalize_economy_chat_id(chat_id)
    for key, value in data.get('settings', {}).items():
        sql.execute("INSERT OR REPLACE INTO economy_settings (chat_id, key, value) VALUES (?, ?, ?)",
                    (chat_id, key, _json_dump(value)))
    for key, value in data.get('server_stats', {}).items():
        sql.execute("INSERT OR REPLACE INTO economy_stats (chat_id, key, value) VALUES (?, ?, ?)",
                    (chat_id, key, str(value)))
    for user_id_str, user_data in data.get('users', {}).items():
        row = _economy_user_to_db_row(user_id_str, user_data)
        sql.execute(
            "INSERT OR REPLACE INTO economy_users (chat_id, user_id, balance, bank, vip, vip_level, vip_until, daily_claimed_date, charity, duels_won, duels_lost, duels_sum_won, duels_sum_lost, transfers_sent, transfers_received, transfers_sum_sent, transfers_sum_received, job, last_job_time, job_level, job_exp, used_promos, deposits, extras) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, *row)
        )
    database.commit()


def build_top_keyboard(chat_id):
    kb = Keyboard(inline=True)
    kb.add(Callback("💰 Деньги", {"command": "top", "sub": "money", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
    kb.add(Callback("🐾 Петы", {"command": "top", "sub": "pet", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY).row()
    kb.add(Callback("👷 Работа", {"command": "top", "sub": "work", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
    return kb


async def build_top_text(chat_id, sub="money"):
    sql.execute("SELECT user_id FROM notop_users")
    notop_ids = {row[0] for row in sql.fetchall()}

    if sub in ['пет', 'pet', 'питомец']:
        sql.execute("SELECT user_id, level, name FROM pets ORDER BY level DESC LIMIT 100")
        res = sql.fetchall()
        users_list = []
        for uid, lvl, p_name in res:
            if uid in notop_ids:
                continue
            users_list.append((uid, lvl, p_name))
            if len(users_list) >= 10:
                break

        if not users_list:
            return "🐾 Топ питомцев пуст!", build_top_keyboard(chat_id)

        msg = "🐾 Топ 10 самых прокачанных питомцев:\n"
        msg += "▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
        for idx, (uid, lvl, p_name) in enumerate(users_list, 1):
            u_name = await get_user_name(uid, chat_id)
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(idx, f"{idx}.")
            msg += f"{medal} [id{uid}|{u_name}]\n"
            msg += f"ㅤ 🐾 «{p_name}» — {lvl} уровень\n\n"
        return msg.strip(), build_top_keyboard(chat_id)

    if sub in ['работа', 'work', 'работать']:
        economy = load_economy(chat_id)
        users_list = []
        for uid_str, user_data in economy['users'].items():
            uid = int(uid_str)
            if uid in notop_ids:
                continue
            users_list.append((uid, user_data.get('job_level', 1), user_data.get('job_exp', 0)))

        users_list.sort(key=lambda x: (x[1], x[2]), reverse=True)
        top_users = users_list[:10]

        if not top_users:
            return "👷 Топ рабочих пуст!", build_top_keyboard(chat_id)

        msg = "👷 Топ 10 мастеров своего дела:\n"
        msg += "▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
        for idx, (uid, lvl, exp) in enumerate(top_users, 1):
            u_name = await get_user_name(uid, chat_id)
            job_id = economy['users'].get(str(uid), {}).get('job', 0)
            job_name = JOBS.get(job_id, JOBS[0])['name']
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(idx, f"{idx}.")
            msg += f"{medal} [id{uid}|{u_name}]\n"
            msg += f"ㅤ 💼 {job_name} | {lvl} уровень\n\n"
        return msg.strip(), build_top_keyboard(chat_id)

    economy = load_economy(chat_id)
    users_list = []
    for user_id_str, user_data in economy['users'].items():
        uid = int(user_id_str)
        if uid in notop_ids:
            continue
        total_balance = user_data.get('balance', 0) + user_data.get('bank', 0)
        users_list.append((uid, total_balance))

    users_list.sort(key=lambda x: x[1], reverse=True)
    top_users = users_list[:10]

    if not top_users:
        return "Нет данных о пользователях!", build_top_keyboard(chat_id)

    msg = "🏆 Топ 10 самых богатых пользователей:\n"
    msg += "▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
    for idx, (uid, balance) in enumerate(top_users, 1):
        ud_top = economy['users'].get(str(uid), {})
        vip_mark = ""
        v_lvl = ud_top.get('vip_level', 0)
        if v_lvl == 1:
            vip_mark = "⭐ "
        elif v_lvl == 2:
            vip_mark = "🌟 "
        elif ud_top.get('vip'):
            vip_mark = "⭐ "

        name = await get_user_name(uid, chat_id)
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(idx, f"{idx}.")
        msg += f"{medal} {vip_mark}[id{uid}|{name}]\n"
        msg += f"ㅤ 💰 Баланс: {balance:,}$\n\n".replace(",", ".")
    return msg.strip(), build_top_keyboard(chat_id)


async def get_balance(user_id, chat_id=0):
    async with econ_lock:
        economy = load_economy(chat_id)
        user_id_str = str(user_id)
        if _ensure_user_in_economy(economy, user_id_str):
            save_economy(economy, chat_id)
        balance = economy['users'][user_id_str]['balance']
        if isinstance(balance, float):
            balance = int(round(balance))
            economy['users'][user_id_str]['balance'] = balance
            save_economy(economy, chat_id)
        return balance

async def get_user_economy_data(user_id, chat_id=0):
    async with econ_lock:
        economy = load_economy(chat_id)
        user_id_str = str(user_id)
        changed = _ensure_user_in_economy(economy, user_id_str)
        user_data = economy['users'][user_id_str]
        if user_data.get('vip') and user_data.get('vip_until'):
            try:
                if datetime.fromisoformat(user_data['vip_until']) < datetime.now():
                    user_data['vip'] = False
                    user_data['vip_level'] = 0
                    user_data['vip_until'] = None
                    changed = True
            except Exception:
                pass
        if 'job' not in user_data:
            user_data['job'] = 0
            user_data['last_job_time'] = 0
            changed = True
        if 'job_level' not in user_data:
            user_data['job_level'] = 1
            user_data['job_exp'] = 0
            changed = True
        for money_field in ['balance', 'bank', 'duels_sum_won', 'duels_sum_lost', 'transfers_sum_sent', 'transfers_sum_received']:
            if money_field in user_data and isinstance(user_data[money_field], float):
                user_data[money_field] = int(round(user_data[money_field]))
                changed = True
        if isinstance(user_data.get('deposits'), list):
            for deposit in user_data['deposits']:
                if isinstance(deposit, dict) and isinstance(deposit.get('amount'), float):
                    deposit['amount'] = int(round(deposit['amount']))
                    changed = True
        if changed:
            save_economy(economy, chat_id)
        return user_data

async def set_balance(user_id, amount, chat_id=0):
    async with econ_lock:
        economy = load_economy(chat_id)
        user_id_str = str(user_id)
        _ensure_user_in_economy(economy, user_id_str)
        if isinstance(amount, float):
            amount = int(round(amount))
        economy['users'][user_id_str]['balance'] = amount
        save_economy(economy, chat_id)

async def add_balance(user_id, amount, chat_id=0):
    async with econ_lock:
        economy = load_economy(chat_id)
        user_id_str = str(user_id)
        _ensure_user_in_economy(economy, user_id_str)
        if isinstance(amount, float):
            amount = int(round(amount))
        economy['users'][user_id_str]['balance'] += amount
        save_economy(economy, chat_id)

async def subtract_balance(user_id, amount, chat_id=0):
    async with econ_lock:
        economy = load_economy(chat_id)
        user_id_str = str(user_id)
        _ensure_user_in_economy(economy, user_id_str)
        balance = economy['users'][user_id_str]['balance']
        if isinstance(balance, float):
            balance = int(round(balance))
        if isinstance(amount, float):
            amount = int(round(amount))
        if balance >= amount:
            economy['users'][user_id_str]['balance'] = balance - amount
            save_economy(economy, chat_id)
            return True
        return False

async def get_bank(user_id, chat_id=0):
    async with econ_lock:
        economy = load_economy(chat_id)
        user_id_str = str(user_id)
        _ensure_user_in_economy(economy, user_id_str)
        bank = economy['users'][user_id_str]['bank']
        if isinstance(bank, float):
            bank = int(round(bank))
            economy['users'][user_id_str]['bank'] = bank
            save_economy(economy, chat_id)
        return bank

async def set_bank(user_id, amount, chat_id=0):
    async with econ_lock:
        if isinstance(amount, float):
            amount = int(round(amount))
        economy = load_economy(chat_id)
        user_id_str = str(user_id)
        _ensure_user_in_economy(economy, user_id_str)
        economy['users'][user_id_str]['bank'] = amount
        save_economy(economy, chat_id)

async def get_daily_claimed_today(user_id, chat_id=0):
    economy = load_economy(chat_id)
    user_id_str = str(user_id)
    if user_id_str not in economy['users']:
        await get_balance(user_id, chat_id)
        economy = load_economy(chat_id)
    claimed_date = economy['users'][user_id_str].get('daily_claimed_date')
    return claimed_date == datetime.now().date().isoformat()

async def set_daily_claimed(user_id, chat_id=0):
    async with econ_lock:
        economy = load_economy(chat_id)
        user_id_str = str(user_id)
        _ensure_user_in_economy(economy, user_id_str)
        economy['users'][user_id_str]['daily_claimed_date'] = datetime.now().date().isoformat()
        save_economy(economy, chat_id)


async def callback_handlers(message: GroupTypes.MessageEvent):
    payload = message.object.payload or {}
    user_id = message.object.user_id
    chat_id = payload.get("chatId")
    # Определяем владельца меню, чтобы передать его дальше в кнопках
    menu_owner = payload.get("initiator") or payload.get("target") or payload.get("user") or payload.get("sender_id") or user_id
    event_acknowledged = False
    # Проверка, что command является строкой, иначе приводим к строке
    command = str(payload.get("command")).lower()

    async def send_event_answer_safe(event_data=None, snackbar_text=None):
        nonlocal event_acknowledged
        if event_acknowledged:
            return True
        event_acknowledged = True
        try:
            params = {
                "event_id": message.object.event_id,
                "peer_id": message.object.peer_id,
                "user_id": message.object.user_id,
            }
            if snackbar_text:
                params["event_data"] = json.dumps({"type": "show_snackbar", "text": snackbar_text})
            elif event_data:
                params["event_data"] = json.dumps(event_data)
            try:
                await bot.api.messages.send_message_event_answer(**params)
            except VKAPIError as e:
                if e.code != 100: logging.error(f"Event answer failed: {e}")
        except Exception as e:
            logging.error(f"Event answer failed: {e}")
            return False # Возвращаем False, если не удалось отправить ответ

    if command == "top":
        sub = payload.get("sub") or "money"
        msg, kb = await build_top_text(chat_id, sub)
        try:
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        except Exception as e:
            logging.error(f"Top callback edit failed: {e}")
            try:
                await bot.api.messages.send(peer_id=message.object.peer_id, message=msg, keyboard=kb, random_id=0, disable_mentions=1)
            except Exception as e2:
                logging.error(f"Top callback fallback send failed: {e2}")
        await send_event_answer_safe()
        return True

    if command == "buy_vip_tier":

        tier = int(payload.get("tier"))
        conf = VIP_CONFIG.get(tier)
        if not conf: return
        
        balance = await get_balance(user_id)
        if balance < conf['price']:
            error_msg = f"❌ Недостаточно средств! Нужно {conf['price']:,}$".replace(",", ".")
            await send_event_answer_safe(snackbar_text=error_msg)
            await bot.api.messages.send(peer_id=message.object.peer_id, message=f"⚠️ {await get_user_link(user_id)}, {error_msg}", random_id=0)
            return True
            
        await subtract_balance(user_id, conf['price'])
        
        econ = load_economy()
        u_str = str(user_id)
        if u_str not in econ['users']: await get_balance(user_id); econ = load_economy()
        
        u_data = econ['users'][u_str]
        current_now = datetime.now()
        days = 30
        
        if u_data.get('vip') and u_data.get('vip_until'):
            try:
                base_date = datetime.fromisoformat(u_data['vip_until'])
                if base_date < current_now: base_date = current_now
            except:
                base_date = current_now
        else:
            base_date = current_now
            
        until_dt = base_date + timedelta(days=days)
        
        u_data['vip'] = True
        u_data['vip_level'] = tier
        u_data['vip_until'] = until_dt.isoformat()
        log_transaction(user_id, f"Купил VIP {conf['name']} за {conf['price']}$")
        save_economy(econ)
        
        await send_event_answer_safe(snackbar_text=f"✨ Куплен {conf['name']}!")
        await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=f"✅ Поздравляем! Вы приобрели статус «{conf['name']}» на 30 дней!", keyboard=None)
        return True

    if command == "clan_donate_confirm":
        await send_event_answer_safe()
        biz_id = payload.get("biz_id")
        target_clan_id = payload.get("clan_id")
        initiator_user_id = payload.get("user") # Пользователь, инициировавший команду /clan donate
        
        # Проверка безопасности: убедитесь, что инициатором является тот, кто нажимает на кнопку.
        if user_id != initiator_user_id:
            return await send_event_answer_safe(snackbar_text="❌ Это не ваша кнопка подтверждения!")

        # Повторно проверить право собственности и членство в клане.
        ud = await get_user_data(initiator_user_id)
        if ud.get('clan_id') != target_clan_id:
            return await send_event_answer_safe(snackbar_text="❌ Вы больше не состоите в этом клане!")
            
        # Проверка лимита бизнесов у клана (макс 1)
        cursor = database.cursor()
        cursor.execute("SELECT COUNT(*) FROM businesses WHERE clan_owner_id = ?", (target_clan_id,))
        if cursor.fetchone()[0] >= 1:
            return await send_event_answer_safe(snackbar_text="❌ У клана уже есть бизнес (лимит 1)!")

        cursor.execute("SELECT owner_id, name FROM businesses WHERE id = ?", (biz_id,))
        biz_data = cursor.fetchone()
        
        if not biz_data:
            return await send_event_answer_safe(snackbar_text="❌ Бизнес не найден или уже передан!")
        
        if biz_data[0] != initiator_user_id:
            return await send_event_answer_safe(snackbar_text="❌ Вы больше не владеете этим бизнесом!")
            
        biz_name = biz_data[1]

        cursor.execute("UPDATE businesses SET owner_id = 0, clan_owner_id = ? WHERE id = ?", (target_clan_id, biz_id))
        database.commit()
        await save_clan_to_json(target_clan_id)
        
        cursor.execute("SELECT name FROM clans WHERE clan_id = ?", (target_clan_id,))
        clan_name = cursor.fetchone()[0]
        
        await send_event_answer_safe(snackbar_text=f"🏢 Бизнес «{biz_name}» передан клану «{clan_name}»!")
        
        # Отредактировать исходное сообщение, чтобы показать успех.
        await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=f"🏢 Бизнес «{biz_name}» успешно передан во владение клана «{clan_name}»!\nТеперь любой участник клана может собирать с него прибыль.", keyboard=None)
        return True

    if command == "jobs_menu":
        await send_event_answer_safe()
        msg = "💼 Центр занятости\nВыберите профессию, чтобы узнать подробности и трудоустроиться:"
        kb = Keyboard(inline=True)
        job_items = [(jid, data) for jid, data in JOBS.items() if jid > 0]
        for i, (jid, data) in enumerate(job_items):
            kb.add(Callback(data['name'], {"command": "job_info", "job_id": jid, "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY)
            if (i + 1) % 2 == 0: kb.row()
        
        if len(job_items) % 2 != 0: kb.row()
        kb.add(Callback("❌ Закрыть", {"command": "delete_msg", "user": user_id}), color=KeyboardButtonColor.NEGATIVE)
        try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        except: pass
        return True

    if command == "job_info":
        await send_event_answer_safe()
        jid = int(payload.get("job_id"))
        j_data = JOBS.get(jid)
        if not j_data: return
        
        price_str = "Бесплатно" if j_data['cost'] == 0 else f"{j_data['cost']:,}$".replace(",", ".")
        msg = (f"👨‍💼 Профессия: {j_data['name']}\n"
               f"💰 Зарплата: {j_data['min_pay']:,}-{j_data['max_pay']:,}$\n"
               f"🏷 Стоимость обучения: {price_str}\n"
               f"⏳ Перезарядка: {j_data['cooldown']} мин.\n\n"
               f"Вы хотите устроиться на эту работу?").replace(",", ".")
        
        kb = Keyboard(inline=True)
        kb.add(Callback("✅ Устроиться", {"command": "job_join", "job_id": jid, "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE)
        kb.row().add(Callback("<< Назад", {"command": "jobs_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.SECONDARY)
        try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        except: pass
        return True

    if command == "job_join":
        jid = int(payload.get("job_id"))
        ud_eco = await get_user_economy_data(user_id)
        if ud_eco.get('job', 0) == jid: return await send_event_answer_safe(snackbar_text="❌ Вы уже работаете здесь!")
        cost = JOBS[jid]['cost']
        if not await subtract_balance(user_id, cost):
            error_msg = f"❌ Недостаточно средств для обучения! Нужно {cost:,}$".replace(",", ".")
            await send_event_answer_safe(snackbar_text=error_msg)
            await bot.api.messages.send(peer_id=message.object.peer_id, message=f"⚠️ {await get_user_link(user_id)}, {error_msg}", random_id=0)
            return True
        economy = load_economy(); economy['users'][str(user_id)]['job'] = jid; save_economy(economy)
        await send_event_answer_safe(snackbar_text=f"✅ Вы устроились: {JOBS[jid]['name']}!")
        try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=f"🎊 Поздравляем! Вы успешно устроились на работу: {JOBS[jid]['name']}!", keyboard=None)
        except: pass
        return True

    if command == "biz_accept_offer":
        oid = payload.get("oid")
        sql.execute("SELECT biz_id, from_id, to_id, price FROM biz_offers WHERE id = ?", (oid,))
        offer = sql.fetchone()
        if not offer:
            return await send_event_answer_safe(snackbar_text="❌ Предложение уже недействительно!")
        
        bid, seller_id, target_id, price = offer
        
        if user_id != target_id:
            return await send_event_answer_safe(snackbar_text="❌ Это предложение не для вас!")
            
        # Проверка слотов у покупателя при принятии сделки
        ud_full = await get_user_data(user_id)
        sql.execute("SELECT COUNT(*) FROM businesses WHERE owner_id = ?", (user_id,))
        current_count = sql.fetchone()[0]
        total_slots = 2 + ud_full.get('biz_slots', 0)
        
        if current_count >= total_slots:
            error_msg = f"❌ У вас нет свободных слотов для бизнеса ({current_count}/{total_slots})!"
            await send_event_answer_safe(snackbar_text=error_msg)
            await bot.api.messages.send(peer_id=message.object.peer_id, message=f"⚠️ {await get_user_link(user_id)}, {error_msg}\n🛒 Купить слоты: /слоты", random_id=0)
            return True

        buyer_bal = await get_balance(user_id)
        if buyer_bal < price:
            error_msg = f"❌ Недостаточно средств для покупки бизнеса! Нужно {price:,}$".replace(",", ".")
            await send_event_answer_safe(snackbar_text=error_msg)
            await bot.api.messages.send(peer_id=message.object.peer_id, message=f"⚠️ {await get_user_link(user_id)}, {error_msg}", random_id=0)
            return True
            
        sql.execute("SELECT owner_id, name FROM businesses WHERE id = ?", (bid,))
        biz_data = sql.fetchone()
        if not biz_data or biz_data[0] != seller_id:
            sql.execute("DELETE FROM biz_offers WHERE id = ?", (oid,))
            database.commit()
            return await send_event_answer_safe(snackbar_text="❌ Продавец больше не владеет этим бизнесом!")

        # Расчет комиссии
        ud_seller_eco = await get_user_economy_data(seller_id)
        v_lvl = ud_seller_eco.get('vip_level', 0)
        comm_rate = VIP_CONFIG[v_lvl]['comm'] if v_lvl in VIP_CONFIG else 0.10
        
        ud_seller_full = await get_user_data(seller_id)
        if ud_seller_full.get('no_comm_until', 0) > time.time():
            comm_rate = 0.0
            
        commission = int(price * comm_rate)
        receive_amount = price - commission

        # Процесс сделки
        if await subtract_balance(user_id, price):
            await add_balance(seller_id, receive_amount)
            
            # Обновление статистики сервера (комиссия в экономику)
            econ = load_economy()
            if 'server_stats' not in econ: econ['server_stats'] = {'collected_commissions': 0}
            econ['server_stats']['collected_commissions'] += commission
            save_economy(econ)
            
            log_transaction(user_id, f"Купил бизнес «{biz_data[1]}» (ID: {bid}) у ID{seller_id} за {price}$")
            log_transaction(seller_id, f"Продал бизнес «{biz_data[1]}» пользователю ID{user_id} за {price}$ (получено {receive_amount}$)")

            sql.execute("UPDATE businesses SET owner_id = ?, clan_owner_id = 0 WHERE id = ?", (user_id, bid))
            sql.execute("DELETE FROM biz_offers WHERE id = ?", (oid,))
            database.commit()
            
            await send_event_answer_safe(snackbar_text=f"🎉 Вы купили бизнес «{biz_data[1]}»!")
            
            buyer_link = await get_user_link(user_id)
            seller_link = await get_user_link(seller_id)
            
            msg = f"🤝 Сделка совершена!\n👤 {buyer_link} купил бизнес «{biz_data[1]}» у {seller_link} за {price:,}$!\n📉 Комиссия сделки ({int(comm_rate*100)}%): {commission:,}$".replace(",", ".")
            try:
                await bot.api.messages.edit(
                    peer_id=message.object.peer_id,
                    conversation_message_id=message.object.conversation_message_id,
                    message=msg,
                    keyboard=None
                )
            except: pass
        return True

    if command == "biz_decline_offer":
        oid = payload.get("oid")
        sql.execute("SELECT from_id, to_id FROM biz_offers WHERE id = ?", (oid,))
        offer = sql.fetchone()
        if not offer:
            return await send_event_answer_safe(snackbar_text="❌ Предложение уже недействительно!")
        
        seller_id, target_id = offer
        if user_id != target_id and user_id != seller_id:
            return await send_event_answer_safe(snackbar_text="❌ Вы не участвуете в этой сделке!")
            
        sql.execute("DELETE FROM biz_offers WHERE id = ?", (oid,))
        database.commit()
        
        await send_event_answer_safe(snackbar_text="❌ Предложение отклонено.")
        try:
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message="❌ Сделка отменена.", keyboard=None)
        except: pass
        return True

    # --- TESTER CALLBACKS ---
    if command == "bug_report_menu":
        await send_event_answer_safe()
        t_lvl = await get_tester_role(user_id)
        if t_lvl < 1 and await get_role(user_id, chat_id) < 6: return
        
        status_filter = payload.get("filter", "pending")
        sql.execute("SELECT id, user_id, text, status FROM support_tickets WHERE type = 'bug' AND status = ? ORDER BY id DESC LIMIT 8", (status_filter,))
        bugs = sql.fetchall()
        
        user_ids_to_fetch = {b[1] for b in bugs}
        user_links = {uid: await get_user_link(uid) for uid in user_ids_to_fetch}

        status_map_ru = {"pending": "НОВЫЕ", "in_work": "В РАБОТЕ", "pending_review": "НА ПРОВЕРКЕ", "fixed": "ИСПРАВЛЕНО", "rejected": "ОТКЛОНЕНО", "sent_to_dev": "ПЕРЕДАНО"}
        current_status_ru = status_map_ru.get(status_filter, status_filter.upper())
        msg = f"🧪 Панель тестировщика | Статус: {current_status_ru}\n\n"
        kb = Keyboard(inline=True)
        
        if not bugs:
            msg += "Список пуст."
        else:
            for i, b in enumerate(bugs):
                reporter_link = user_links.get(b[1], f"[id{b[1]}|Неизвестный]")
                msg += f"#{b[0]} | От: {reporter_link}\n📝 {b[2][:50]}...\n\n"
                kb.add(Callback(f"🔎 #{b[0]}", {"command": "bug_view", "id": b[0], "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY)
                if (i + 1) % 4 == 0 and (i + 1) < len(bugs): kb.row()
        
        if kb.buttons and len(kb.buttons[-1]) > 0: kb.row()
            
        kb.add(Callback("🆕 Новые", {"command": "bug_report_menu", "filter": "pending", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.SECONDARY)
        kb.add(Callback("🛠 Работа", {"command": "bug_report_menu", "filter": "in_work", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.SECONDARY)
        kb.add(Callback("🧐 Пров.", {"command": "bug_report_menu", "filter": "pending_review", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.SECONDARY).row()
        kb.add(Callback("✅ Испр.", {"command": "bug_report_menu", "filter": "fixed", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.SECONDARY)
        if await get_role(user_id, chat_id) >= 6:
            kb.add(Callback("🚀 Перед.", {"command": "bug_report_menu", "filter": "sent_to_dev", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.SECONDARY)
        kb.add(Callback("❌ Закрыть", {"command": "delete_msg", "user": user_id}), color=KeyboardButtonColor.NEGATIVE)
        
        try:
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        except Exception as e:
            print(f"Failed to edit message for bug_report_menu: {e}")
            await bot.api.messages.send(peer_id=message.object.peer_id, message=msg, keyboard=kb, random_id=0)
        return True

    if command == "bug_view":
        await send_event_answer_safe()
        bid = payload.get("id")
        sql.execute("SELECT user_id, text, status, date, tester_id, attachment, tester_comment FROM support_tickets WHERE id = ?", (bid,))
        res = sql.fetchone()
        if not res: return
        u_id, text, status, date, tester_id, att, t_comment = res
        
        reporter_link = await get_user_link(u_id)
        tester_link = await get_user_link(tester_id) if tester_id else "Не назначен"

        t_lvl = await get_tester_role(user_id)
        status_emoji = {"pending": "🆕 Новые", "in_work": "🛠 В работе", "pending_review": "🧐 На проверке старшими", "fixed": "✅ Исправлено", "rejected": "❌ Отклонено", "sent_to_dev": "🚀 Передано разработчику"}
        
        # Исправлена ​​ошибка, из-за которой не отображались вложения: если строка пустая, передать None.
        final_att = att if att and len(att) > 5 else None

        msg = (f"🐞 Баг-репорт #{bid}\n"
               f"👤 Отправитель: {reporter_link}\n"
               f"📅 Дата: {date}\n"
               f"📊 Статус: {status_emoji.get(status, status.upper())}\n"
               f"👨‍💻 Тестировщик: {tester_link}\n\n"
               f"📄 Описание:\n{text}\n\n"
               f"💬 Коммент. тестера: {t_comment if t_comment else 'Нет'}")
        
        kb = Keyboard(inline=True)
        if not tester_id and t_lvl >= 1:
            kb.add(Callback("🛠 Взять", {"command": "bug_action", "act": "take", "id": bid, "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE)
        
        if (tester_id == user_id or t_lvl >= 2 or await get_role(user_id, chat_id) >= 6) and status == 'in_work':
            kb.add(Callback("💬 Коммент", {"command": "bug_action", "act": "comment", "id": bid, "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY)
            kb.add(Callback("✉️ Ответ", {"command": "bug_action", "act": "reply", "id": bid, "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
            kb.add(Callback("🚩 Старшим", {"command": "bug_action", "act": "send_to_senior", "id": bid, "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE)

        if t_lvl >= 2 and status in ['in_work', 'pending_review']:
            kb.add(Callback("✅ Испр.", {"command": "bug_action", "act": "fix", "id": bid, "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE)
            kb.add(Callback("❌ Отклон.", {"command": "bug_action", "act": "reject", "id": bid, "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.NEGATIVE).row()

        if t_lvl >= 2:
            kb.add(Callback("🚀 Разрабу", {"command": "bug_action", "act": "send_to_dev", "id": bid, "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY)
            
        kb.row().add(Callback("<< Назад", {"command": "bug_report_menu", "filter": status, "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.SECONDARY)
        try:
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb, attachment=final_att)
        except Exception as e:
            print(f"Failed to edit message for bug_view: {e}")
            await bot.api.messages.send(peer_id=message.object.peer_id, message=msg, keyboard=kb, attachment=final_att, random_id=0)
        return True

    if command == "bug_action":
        act = payload.get("act"); bid = payload.get("id")
        t_lvl = await get_tester_role(user_id)
        
        if act == "take": # Обновлено
            sql.execute("UPDATE support_tickets SET status = 'in_work', tester_id = ? WHERE id = ?", (user_id, bid))
            database.commit()
            await send_event_answer_safe({"type": "show_snackbar", "text": "🚀 Вы взяли баг на тестирование!"})
            # Обновляем меню, чтобы сразу увидеть изменения
            new_payload = {"command": "bug_view", "id": bid, "chatId": chat_id, "user": user_id}
            message.object.payload = new_payload
            return await callback_handlers(message)
        elif act == "comment":
            user_states[user_id] = {"action": "add_bug_comment", "bid": bid, "chat_id": chat_id}
            await bot.api.messages.send(peer_id=message.object.peer_id, message=f"✍️ Введите ваш комментарий к багу #{bid}:", random_id=0)
            return await send_event_answer_safe(snackbar_text="Жду комментарий...")
        elif act == "reply":
            sql.execute("SELECT user_id FROM support_tickets WHERE id = ?", (bid,))
            reporter = sql.fetchone()[0]
            user_states[user_id] = {
                "action": "bug_reply", 
                "bid": bid, 
                "target_user": reporter,
                "chat_id": chat_id
            }
            await bot.api.messages.send(peer_id=message.object.peer_id, message=f"✍️ Введите ответ автору бага #{bid} (или «отмена»):", random_id=0)
            return await send_event_answer_safe(snackbar_text="Жду текст ответа...")
        elif act == "send_to_senior":
            sql.execute("UPDATE support_tickets SET status = 'pending_review' WHERE id = ?", (bid,))
            database.commit()
            
            # Уведомление старших (Lvl 2) и главных (Lvl 3) тестеров
            sql.execute("SELECT user_id FROM testers WHERE level >= 2")
            seniors = sql.fetchall()
            sender_link = await get_user_link(user_id)
            
            kb_notif = Keyboard(inline=True).add(Callback("🔎 Проверить", {"command": "bug_view", "id": bid, "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY)
            
            # 1. Рассылка в личные сообщения
            for (s_uid,) in seniors:
                if s_uid != user_id: # Не отправляем уведомление самому себе
                    try:
                        await bot.api.messages.send(
                            user_id=s_uid, 
                            message=f"🚩 Внимание! Тестировщик {sender_link} передал баг-репорт #{bid} на проверку руководству.", 
                            keyboard=kb_notif, 
                            random_id=0
                        )
                    except: pass
            
            # 2. Уведомление в общий чат тестеров
            try:
                await bot.api.messages.send(
                    peer_id=TESTER_CHAT_ID,
                    message=f"🚩 Баг-репорт #{bid} передан на проверку старшим тестировщикам!\n🧪 Отправил: {sender_link}",
                    keyboard=kb_notif,
                    random_id=0
                )
            except: pass

            await send_event_answer_safe(snackbar_text="🚩 Баг передан. Руководство отдела уведомлено!")
            
        elif act == "fix":
            if t_lvl < 2: return await send_event_answer_safe(snackbar_text="❌ Только Ст. Тестер может закрывать баги!")
            sql.execute("SELECT tester_id, user_id FROM support_tickets WHERE id = ?", (bid,))
            ticket_res = sql.fetchone()
            if not ticket_res: return await send_event_answer_safe(snackbar_text="❌ Ошибка: тикет не найден!")
            tid, reporter = ticket_res
            
            sql.execute("UPDATE support_tickets SET status = 'fixed' WHERE id = ?", (bid,))
            if tid > 0:
                sql.execute("UPDATE testers SET handled = handled + 1 WHERE user_id = ?", (tid,))
                # Награда тестеру
                await add_balance(tid, TESTER_REWARD['money'])
                ud_t = await get_user_data(tid)
                await update_user_data(tid, 'points', ud_t['points'] + TESTER_REWARD['points'])
                if ud_t['clan_id'] > 0:
                    sql.execute("UPDATE clans SET mats = mats + ? WHERE clan_id = ?", (TESTER_REWARD['mats'], ud_t['clan_id']))
                try: await bot.api.messages.send(user_id=tid, message=f"💎 Баг #{bid} исправлен! Вы получили: {TESTER_REWARD['money']}$, {TESTER_REWARD['points']} баллов и {TESTER_REWARD['mats']} мат. в клан.", random_id=0)
                except: pass
            
            try: await bot.api.messages.send(user_id=reporter, message=f"✅ Ваш баг-репорт #{bid} был исправлен! Спасибо за помощь.", random_id=0)
            except: pass
            database.commit()
            await send_event_answer_safe({"type": "show_snackbar", "text": "✅ Баг отмечен как исправленный!"})
            
        elif act == "reject":
            if t_lvl < 2: return await send_event_answer_safe(snackbar_text="❌ Недостаточно прав!")
            sql.execute("UPDATE support_tickets SET status = 'rejected' WHERE id = ?", (bid,))
            database.commit()
            await send_event_answer_safe({"type": "show_snackbar", "text": "❌ Баг отклонен."})
            
        elif act == "send_to_dev":
            if t_lvl < 1: return await send_event_answer_safe({"type": "show_snackbar", "text": "❌ Недостаточно прав!"})
            sql.execute("SELECT user_id, text, attachment FROM support_tickets WHERE id = ?", (bid,))
            rep_data = sql.fetchone()
            if not rep_data: return await send_event_answer_safe({"type": "show_snackbar", "text": "❌ Баг не найден!"})
            
            reporter, b_text, b_att = rep_data
            dev_msg = (f"🛠 ПЕРЕДАЧА БАГ-РЕПОРТА #{bid}\n"
                       f"🧪 От тестера: {await get_user_link(user_id)}\n"
                       f"👤 От игрока: {await get_user_link(reporter)}\n\n"
                       f"📝 Описание:\n{b_text}")
            
            # Клавиатура для разработчика в PM
            dev_kb = Keyboard(inline=True)
            dev_kb.add(Callback("✅ Исправлено", {"command": "bug_action", "act": "fix", "id": bid}), color=KeyboardButtonColor.POSITIVE)
            dev_kb.add(Callback("❌ Отклонить", {"command": "bug_action", "act": "reject", "id": bid}), color=KeyboardButtonColor.NEGATIVE)

            try:
                await bot.api.messages.send(peer_id=CREATOR_ID, message=dev_msg, attachment=b_att if b_att else None, keyboard=dev_kb, random_id=0)
                sql.execute("UPDATE support_tickets SET status = 'sent_to_dev' WHERE id = ?", (bid,))
                database.commit()
                await send_event_answer_safe({"type": "show_snackbar", "text": "✅ Успешно передано разработчику в ЛС!"})
            except:
                await send_event_answer_safe({"type": "show_snackbar", "text": "❌ Ошибка: Убедитесь, что у разработчика открыты ЛС!"})

        # Возврат в меню без повторного ответа на событие
        try:
            new_payload = {"command": "bug_report_menu", "filter": "pending", "chatId": chat_id}
            message.object.payload = new_payload
            # Вызываем напрямую логику отрисовки меню, чтобы избежать двойного ответа на event_id
            return await callback_handlers(message)
        except: pass

    if command == "clan_manage_menu":
        if not await check_clan_perms(user_id, 4):
            return await send_event_answer_safe(snackbar_text="Недостаточно прав!")

        await send_event_answer_safe()
        ud = await get_user_data(user_id)
        clan_id = ud.get('clan_id')
        sql.execute("SELECT type, money, mats, treasury FROM clans WHERE clan_id = ?", (clan_id,))
        c_data = sql.fetchone()
        if not c_data: return await send_event_answer_safe(snackbar_text="❌ Ошибка: данные клана не найдены.")
        
        c_type, money, mats, treasury = c_data
        type_str = "🔓 Открытый" if c_type == 'open' else "🔒 Закрытый"
        treasury_str = "🔓 Открыта" if treasury else "🔒 Закрыта"
        
        msg = (f"⚙️ Меню управления кланом\n"
               f"Тип: {type_str}\n"
               f"Казна ({treasury_str}): {money:,}$ | {mats:,} мат.".replace(",", "."))
        
        kb = Keyboard(inline=True)
        kb.add(Callback("🚩 Тактики", {"command": "clan_tactics_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
        kb.add(Callback("📊 Активность", {"command": "clan_activity_view", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
        kb.add(Callback("🆙 Улучшить", {"command": "clan_upgrade_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE)
        kb.add(Callback(f"Тип: {type_str}", {"command": "clan_toggle_type", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
        kb.add(Callback("👹 Рейды", {"command": "clan_raid_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.NEGATIVE)
        kb.add(Callback("🏢 Бизнесы", {"command": "clan_biz_list", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
        kb.add(Callback(f"Казна: {treasury_str}", {"command": "clan_toggle_treasury", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY)
        kb.add(Callback("💰 Снять", {"command": "clan_withdraw_ask", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE).row()
        kb.add(Callback("<< Назад", {"command": "clan_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.SECONDARY)
        try: # Обновлено
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        except Exception as e:
            logging.error(f"Failed to edit message for clan_menu: {e}")
            await bot.api.messages.send(peer_id=message.object.peer_id, message=msg, keyboard=kb, random_id=0)
        return True

    # --- Бизнес & рейд обработчик ---
    if command == "biz_buy":
        await send_event_answer_safe() # Обновлено
        bid = payload.get("biz_id")
        sql.execute("SELECT name, price, owner_id, clan_owner_id FROM businesses WHERE id = ?", (bid,))
        res = sql.fetchone()
        if not res: return await send_event_answer_safe(snackbar_text="❌ Бизнес не найден!") # Обновлено
        name, price, owner, clan_owner = res
        if owner != 0 or clan_owner != 0: return await send_event_answer_safe(snackbar_text="❌ Бизнес уже куплен!")
        
        ud_full = await get_user_data(user_id)
        sql.execute("SELECT COUNT(*) FROM businesses WHERE owner_id = ?", (user_id,))
        current_count = sql.fetchone()[0]
        total_slots = 2 + ud_full.get('biz_slots', 0)
        
        if current_count >= total_slots:
            error_msg = f"❌ У вас нет свободных слотов для бизнеса ({current_count}/{total_slots})!"
            await send_event_answer_safe(snackbar_text=error_msg)
            await bot.api.messages.send(peer_id=message.object.peer_id, message=f"⚠️ {await get_user_link(user_id)}, {error_msg}\n🛒 Купить слоты: /слоты", random_id=0)
            return True

        now = int(time.time())
        if not await subtract_balance(user_id, price):
            error_msg = f"❌ Недостаточно средств для покупки бизнеса! Нужно {price:,}$".replace(",", ".")
            await send_event_answer_safe(snackbar_text=error_msg)
            await bot.api.messages.send(peer_id=message.object.peer_id, message=f"⚠️ {await get_user_link(user_id)}, {error_msg}", random_id=0)
            return True
        sql.execute("UPDATE businesses SET owner_id = ?, clan_owner_id = 0, last_collect = ?, tax_due_at = ? WHERE id = ?", (user_id, now, now + 86400, bid))
        await normalize_business_profit_by_price(bid)
        log_transaction(user_id, f"Купил бизнес «{name}» (ID: {bid}) за {price}$")
        database.commit()
        await send_event_answer_safe(snackbar_text=f"✅ Вы купили бизнес «{name}»!")
        
        owner_link = await get_user_link(user_id)
        msg = f"🏢 Бизнес: {name} (ID: {bid})\n🏷 Цена: {price:,}$\n👤 Владелец: {owner_link}\n⚙ Статус: ✅ Работает".replace(",", ".")
        kb = Keyboard(inline=True).add(Callback("💰 Собрать", {"command": "biz_collect", "biz_id": bid, "chatId": chat_id, "initiator": menu_owner}), color=KeyboardButtonColor.PRIMARY) # Обновлено
        try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        except: pass
        return True

    if command == "biz_collect":
        bid = int(payload.get("biz_id"))
        sql.execute("SELECT name, owner_id, last_collect, clan_owner_id, repair_until, profit_per_hour, special_order_active, price FROM businesses WHERE id = ?", (bid,))
        res = sql.fetchone() # Обновлено
        if not res: return
        name, owner, last_col, clan_owner, repair_until, base_profit, was_special, price = res
        
        ud = await get_user_data(user_id)
        is_personal_owner = (owner == user_id)
        is_clan_manager = (clan_owner > 0 and clan_owner == ud.get('clan_id') and await check_clan_perms(user_id, 4))

        if not is_personal_owner and not is_clan_manager: # Обновлено
            return await send_event_answer_safe(snackbar_text="❌ У вас нет прав на сбор прибыли!")
            
        now = int(time.time())
        if now < repair_until:
            rem = int((repair_until - now) / 60)
            return await send_event_answer_safe(snackbar_text=f"🛠 Бизнес в ремонте! Еще {rem} мин.")
            
        cd = 3600
        if now - last_col < cd:
            rem = int((cd - (now - last_col)) / 60)
            msg_rem = f"{rem} мин." if rem > 0 else "меньше минуты"
            return await send_event_answer_safe(snackbar_text=f"⏳ Прибыль будет через {msg_rem}.")
            # Обновлено
        tax_ok, tax_status = await check_business_tax_status(bid)
        if not tax_ok:
            if tax_status == 'auctioned':
                return await send_event_answer_safe(snackbar_text="❌ Бизнес выставлен на аукцион из-за неуплаты налога.")
            amount = get_business_daily_tax(price)
            return await send_event_answer_safe(snackbar_text=f"❌ Налог по бизнесу не оплачен. Оплатите {amount:,}$ в течении 24 часов, иначе бизнес будет выставлен на аукцион.")
        profit, failed = await calculate_biz_profit(bid)

        tax_rate = await get_business_income_tax_rate(owner, clan_owner)
        tax_amount = int(profit * tax_rate)
        profit -= tax_amount

        # Бонус для тестировщиков: отсутствие ошибок/сбоев в тестовых чатах для тестировщиков.
        c_type = await get_chat_type(chat_id)
        if c_type == 'test' and await get_tester_role(user_id) >= 1:
            failed = False

        if profit <= 0 and not failed:
            return await send_event_answer_safe(snackbar_text="❌ У этого бизнеса нет прибыли! Попробуйте его улучшить.")

        if failed:
            repair_time = now + 1800
            sql.execute("UPDATE businesses SET repair_until = ?, last_collect = ?, special_order_active = 0 WHERE id = ?", (repair_time, now, bid))
            database.commit()
            
            # Уведомление владельца в ЛС при аварии спецзаказа
            if was_special:
                target_notify = owner
                if owner == 0 and clan_owner > 0:
                    sql.execute("SELECT owner_id FROM clans WHERE clan_id = ?", (clan_owner,))
                    c_own = sql.fetchone()
                    if c_own: target_notify = c_own[0]
                
                if target_notify > 0:
                    try:
                        await bot.api.messages.send(
                            user_id=target_notify,
                            message=f"🆘 КРИТИЧЕСКАЯ СИТУАЦИЯ: {name}\n\n📦 Спецзаказ завершился аварией! Поезд сошел с рельс, ценный груз уничтожен.\n🛠 Станция закрыта на ремонт (30 мин).",
                            random_id=0
                        )
                    except: pass

            return await send_event_answer_safe(snackbar_text="💥 Авария! Бизнес на ремонте (30 мин).")
            
        if clan_owner > 0:
            sql.execute("UPDATE clans SET money = money + ? WHERE clan_id = ?", (profit, clan_owner))
            sql.execute("UPDATE businesses SET last_collect = ?, special_order_active = 0 WHERE id = ?", (now, bid))
            log_transaction(user_id, f"Сбор прибыли с клан-бизнеса ID{bid}: +{profit}$ (в казну клана {clan_owner})")
            database.commit()
            await save_clan_to_json(clan_owner)
            await send_event_answer_safe(snackbar_text=f"💰 Прибыль {profit:,}$ зачислена в казну клана! (налог {tax_amount:,}$)")
        else:
            await add_balance(user_id, profit)
            sql.execute("UPDATE businesses SET last_collect = ?, special_order_active = 0 WHERE id = ?", (now, bid))
            log_transaction(user_id, f"Сбор прибыли с бизнеса ID{bid}: +{profit}$")
            database.commit()
            await send_event_answer_safe(snackbar_text=f"💰 Собрано: {profit:,}$ (налог {tax_amount:,}$)")

        # Автоматически обновляем меню управления бизнесом
        sql.execute("SELECT type FROM businesses WHERE id = ?", (bid,))
        b_type = sql.fetchone()[0]
        refresh_cmd = "depo" if b_type == "station" else "biz_manage"
        new_payload = {"command": refresh_cmd, "biz_id": int(bid), "chatId": chat_id, "user": menu_owner, "initiator": menu_owner}
        await asyncio.sleep(0.5) # Небольшая задержка, чтобы избежать Flood Control при обновлении
        try: obj_data = message.object.model_dump() # Обновлено
        except: obj_data = message.object.dict().copy()
        obj_data['payload'] = new_payload
        return await callback_handlers(GroupTypes.MessageEvent(group_id=message.group_id, event_id=message.event_id, object=obj_data))

    if command == "clan_biz_list":
        print(f"clan_biz_list called with payload: {payload}")
        await send_event_answer_safe()
        if not await check_clan_perms(user_id, 4): 
            return await send_event_answer_safe(snackbar_text="❌ Недостаточно прав для управления бизнесами клана!")
        ud = await get_user_data(user_id); clan_id = ud['clan_id']
        sql.execute("SELECT id, name FROM businesses WHERE clan_owner_id = ? LIMIT 21", (clan_id,))
        bizs = sql.fetchall()
        print(f"clan businesses: {bizs}")
        msg = "🏢 Бизнесы клана:\n\n"
        kb = Keyboard(inline=True)
        if bizs:
            for i, b in enumerate(bizs[:15]):
                msg += f"• {b[1]} (ID: {b[0]})\n"
                kb.add(Callback(f"⚙ {b[1][:15]}", {"command": "biz_manage", "biz_id": b[0], "chatId": chat_id, "initiator": menu_owner}), color=KeyboardButtonColor.PRIMARY)
                if (i + 1) % 3 == 0: kb.row()
            if len(bizs[:15]) % 3 != 0: kb.row()
        else:
            msg += "У клана пока нет бизнесов."
        kb.add(Callback("<< Назад", {"command": "clan_manage_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.SECONDARY)
        try:
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        except Exception as e:
            pass
            await bot.api.messages.send(peer_id=message.object.peer_id, message=msg, keyboard=kb, random_id=0)
        return True

    if command == "clan_raid_menu":
        await send_event_answer_safe() # Обновлено
        if not await check_clan_perms(user_id, 4): return
        ud = await get_user_data(user_id); clan_id = ud['clan_id']
        sql.execute("SELECT boss_id, current_hp, max_hp, end_time FROM clan_bosses WHERE clan_id = ?", (clan_id,))
        active = sql.fetchone()
        if active and time.time() < active[3]:
            b = BOSSES.get(active[0])
            msg = f"⚔ РЕЙД: {b['name']}\n❤️ HP: {active[1]}/{active[2]}\n⏳ Конец: {int((active[3]-time.time())/60)} мин"
            kb = Keyboard(inline=True).add(Callback("💥 Атаковать", {"command": "clan_boss_attack", "chatId": chat_id, "user": menu_owner}), color=KeyboardButtonColor.NEGATIVE)
            kb.row().add(Callback("<< Назад", {"command": "clan_manage_menu", "chatId": chat_id, "user": menu_owner}), color=KeyboardButtonColor.SECONDARY)
        else:
            if active: sql.execute("DELETE FROM clan_bosses WHERE clan_id = ?", (clan_id,)); database.commit() # Обновлено
            msg = "👹 Выберите босса для призыва из казны:"
            kb = Keyboard(inline=True)
            now = int(time.time())
            for bid, bd in BOSSES.items():
                sql.execute("SELECT ready_at FROM clan_boss_cooldowns WHERE clan_id = ? AND boss_id = ?", (clan_id, bid))
                cd_res = sql.fetchone()
                if cd_res and now < cd_res[0]:
                    rem_min = (cd_res[0] - now) // 60
                    btn_text = f"⏳ {bd['name']} ({rem_min}м)"
                    kb.add(Callback(btn_text, {"command": "none"}), color=KeyboardButtonColor.SECONDARY).row()
                else:
                    sm = f"{bd['cost_money']/1e6:.1f}M".replace(".0", "") if bd['cost_money'] >= 1e6 else f"{bd['cost_money']//1000}k" if bd['cost_money'] >= 1000 else str(bd['cost_money'])
                    smt = f"{bd['cost_mats']/1e6:.1f}M".replace(".0", "") if bd['cost_mats'] >= 1e6 else f"{bd['cost_mats']//1000}k" if bd['cost_mats'] >= 1000 else str(bd['cost_mats'])
                    btn_text = f"{bd['name']} ({sm}$ | {smt}м)"
                    kb.add(Callback(btn_text, {"command": "clan_boss_summon", "boss_id": bid, "chatId": chat_id, "user": menu_owner}), color=KeyboardButtonColor.PRIMARY).row()
            kb.add(Callback("<< Назад", {"command": "clan_manage_menu", "chatId": chat_id, "user": menu_owner}), color=KeyboardButtonColor.SECONDARY)
        try:
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        except Exception as e:
            pass
            await bot.api.messages.send(peer_id=message.object.peer_id, message=msg, keyboard=kb, random_id=0)
        return True

    if command == "clan_boss_summon":
        if not await check_clan_perms(user_id, 4): return # Обновлено
        bid = payload.get("boss_id"); b = BOSSES.get(bid)
        ud = await get_user_data(user_id); clan_id = ud['clan_id']
        sql.execute("SELECT ready_at FROM clan_boss_cooldowns WHERE clan_id = ? AND boss_id = ?", (clan_id, bid))
        cd_res = sql.fetchone()
        if cd_res and time.time() < cd_res[0]:
            rem = int((cd_res[0] - time.time()) // 60)
            return await send_event_answer_safe(snackbar_text=f"❌ Босс на перезарядке! Еще {rem} мин.")

        ud = await get_user_data(user_id); clan_id = ud['clan_id']
        sql.execute("SELECT money, mats FROM clans WHERE clan_id = ?", (clan_id,))
        c = sql.fetchone()
        if c[0] < b['cost_money'] or c[1] < b['cost_mats']: return await send_event_answer_safe(snackbar_text="❌ Мало ресурсов в казне!")
        sql.execute("UPDATE clans SET money = money - ?, mats = mats - ? WHERE clan_id = ?", (b['cost_money'], b['cost_mats'], clan_id))
        sql.execute("INSERT OR REPLACE INTO clan_bosses (clan_id, boss_id, current_hp, max_hp, start_time, end_time) VALUES (?, ?, ?, ?, ?, ?)", (clan_id, bid, b['hp'], b['hp'], int(time.time()), int(time.time() + b['time'])))
        database.commit() # Обновлено
        await send_event_answer_safe(snackbar_text=f"👹 {b['name']} призван!")
        new_payload = {"command": "clan_raid_menu", "chatId": chat_id, "user": menu_owner}
        obj_data = message.object.dict() if hasattr(message.object, 'dict') else message.object.model_dump()
        obj_data['payload'] = new_payload
        return await callback_handlers(GroupTypes.MessageEvent(group_id=message.group_id, event_id=message.event_id, object=obj_data))

    if command == "clan_boss_attack":
        ud = await get_user_data(user_id); clan_id = ud['clan_id'] # Обновлено
        if time.time() - user_boss_cooldown.get(user_id, 0) < 40:
            return await send_event_answer_safe(snackbar_text=f"⏳ Откат {int(40-(time.time()-user_boss_cooldown[user_id]))} сек.")
        
        sql.execute("SELECT boss_id, current_hp, max_hp, end_time FROM clan_bosses WHERE clan_id = ?", (clan_id,))
        res = sql.fetchone()
        if not res or time.time() > res[3]: return await send_event_answer_safe(snackbar_text="❌ Босс ушел!")
        
        dmg = random.randint(50, 500)
        # Атомарное обновление HP в базе (защита от одновременных нажатий)
        sql.execute("UPDATE clan_bosses SET current_hp = MAX(0, current_hp - ?) WHERE clan_id = ?", (dmg, clan_id))
        database.commit()
        
        # Получаем актуальное HP после удара
        sql.execute("SELECT current_hp FROM clan_bosses WHERE clan_id = ?", (clan_id,))
        new_hp = sql.fetchone()[0]
        user_boss_cooldown[user_id] = time.time()

        if new_hp <= 0:
            b = BOSSES.get(res[0])
            # Установка КД на босса после убийства
            ready_at = int(time.time() + b.get('summon_cd', 3600))
            sql.execute("INSERT OR REPLACE INTO clan_boss_cooldowns (clan_id, boss_id, ready_at) VALUES (?, ?, ?)", (clan_id, res[0], ready_at))
            sql.execute("UPDATE clans SET money = money + ?, exp = exp + ? WHERE clan_id = ?", (b['reward_money'], b['reward_exp'], clan_id))
            sql.execute("DELETE FROM clan_bosses WHERE clan_id = ?", (clan_id,))
            database.commit()
            await send_event_answer_safe(snackbar_text=f"💥 КРИТИЧЕСКИЙ УДАР! -{dmg} HP")
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=f"🎉 {b['name']} повержен! Казна пополнена: {b['reward_money']:,}$ и {b['reward_exp']} EXP.".replace(",", "."), keyboard=None)
        else:
            await send_event_answer_safe(snackbar_text=f"💥 Удар! -{dmg} HP. Осталось: {new_hp}")
            # Обновляем визуальное состояние HP в сообщении рейда
            b = BOSSES.get(res[0])
            msg = f"⚔ РЕЙД: {b['name']}\n❤️ HP: {new_hp}/{res[2]}\n⏳ Конец: {int((res[3]-time.time())/60)} мин"
            kb = Keyboard(inline=True).add(Callback("💥 Атаковать", {"command": "clan_boss_attack", "chatId": chat_id, "user": menu_owner}), color=KeyboardButtonColor.NEGATIVE)
            kb.row().add(Callback("<< Назад", {"command": "clan_manage_menu", "chatId": chat_id, "user": menu_owner}), color=KeyboardButtonColor.SECONDARY)
            try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
            except: pass
        return True

    if command == "depo":
        await send_event_answer_safe() # Обновлено
        bid = payload.get("biz_id")
        sql.execute("SELECT id, name, active_route, special_order_active, repair_until FROM businesses WHERE id = ?", (bid,))
        biz = sql.fetchone()
        if not biz: return
        bid, name, route_id, special, repair = biz
        status = "✅ Пути исправны" if time.time() > repair else f"🛠 В РЕМОНТЕ ({int((repair-time.time())/60)} мин)"
        route_name = STATION_ROUTES[route_id]['name']
        msg = f"🚋 Депо станции: {name}\n⚙ Статус: {status}\n🚩 Активный маршрут: {route_name}\n📦 Спецзаказ: {'Активен' if special else 'Нет'}" # Обновлено
        kb = Keyboard(inline=True)
        kb.add(Callback("💰 Прибыль", {"command": "biz_collect", "biz_id": bid, "chatId": chat_id, "initiator": menu_owner}), color=KeyboardButtonColor.POSITIVE)
        kb.add(Callback("🛣 Маршруты", {"command": "depo_routes", "biz_id": bid, "chatId": chat_id, "initiator": menu_owner}), color=KeyboardButtonColor.PRIMARY).row()
        kb.add(Callback("📦 Спецзаказ", {"command": "depo_special", "biz_id": bid, "chatId": chat_id, "initiator": menu_owner}), color=KeyboardButtonColor.PRIMARY).row()
        try:
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        except Exception as e:
            if "Flood control" in str(e):
                return True
            print(f"Failed to edit message for depo: {e}")
            await bot.api.messages.send(peer_id=message.object.peer_id, message=msg, keyboard=kb, random_id=0)
        return True

    if command == "depo_routes":
        await send_event_answer_safe() # Обновлено
        bid = payload.get("biz_id")
        msg = "🛣 Выберите маршрут станции:\n\n"
        kb = Keyboard(inline=True)
        for rid, rd in STATION_ROUTES.items():
            msg += f"{rid}. {rd['name']} (Прибыль: {rd['profit']:,}$, Риск: {rd['risk']}%)\n".replace(",", ".")
            kb.add(Callback(rd['name'], {"command": "set_route", "biz_id": bid, "route": rid, "initiator": menu_owner, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY).row()
        kb.add(Callback("<< Назад", {"command": "depo", "biz_id": bid, "chatId": chat_id, "initiator": menu_owner}), color=KeyboardButtonColor.SECONDARY)
        try:
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        except Exception as e:
            if "Flood control" in str(e):
                return True
            print(f"Failed to edit message for depo_routes: {e}")
            await bot.api.messages.send(peer_id=message.object.peer_id, message=msg, keyboard=kb, random_id=0)
        return True

    if command == "set_route":
        bid = payload.get("biz_id"); rid = payload.get("route") # Обновлено
        sql.execute("SELECT owner_id, clan_owner_id FROM businesses WHERE id = ?", (bid,))
        res = sql.fetchone()
        if res:
            owner, clan_owner = res
            ud = await get_user_data(user_id)
            if owner == user_id or (clan_owner > 0 and clan_owner == ud.get('clan_id') and await check_clan_perms(user_id, 4)):
                sql.execute("UPDATE businesses SET active_route = ?, special_order_active = 0 WHERE id = ?", (rid, bid))
                database.commit() # Обновлено
                await send_event_answer_safe(snackbar_text=f"✅ Маршрут «{STATION_ROUTES[int(rid)]['name']}» установлен!")
                
                # Возврат в меню депо
                new_payload = {"command": "depo", "biz_id": bid, "chatId": chat_id, "initiator": menu_owner}
                try: obj_data = message.object.model_dump()
                except: obj_data = message.object.dict().copy()
                obj_data['payload'] = new_payload
                return await callback_handlers(GroupTypes.MessageEvent(group_id=message.group_id, event_id=message.event_id, object=obj_data))
        return True

    if command == "depo_special":
        await send_event_answer_safe()
        bid = payload.get("biz_id")
        sql.execute("SELECT name, special_order_active FROM businesses WHERE id = ?", (bid,))
        res = sql.fetchone()
        if not res: return
        name, special = res
        
        status = "✅ АКТИВЕН" if special else "❌ Не активен"
        msg = (f"📦 Спецзаказ: {name}\n\n"
               f"Это контракт на перевозку ценных государственных грузов.\n"
               f"💰 Прибыль: 800.000$\n"
               f"⚠ Риск аварии: 35%\n\n"
               f"⚙ Статус: {status}\n\n"
               f"💡 Спецзаказ действует на один сбор прибыли. После завершения рейса необходимо активировать его снова.")
        
        kb = Keyboard(inline=True)
        if not special:
            kb.add(Callback("✅ Активировать", {"command": "set_special", "biz_id": bid, "chatId": chat_id, "initiator": menu_owner}), color=KeyboardButtonColor.POSITIVE)
        else:
            kb.add(Callback("❌ Отключить", {"command": "set_special", "biz_id": bid, "chatId": chat_id, "initiator": menu_owner}), color=KeyboardButtonColor.NEGATIVE)
        kb.row().add(Callback("<< Назад", {"command": "depo", "biz_id": bid, "chatId": chat_id, "initiator": menu_owner}), color=KeyboardButtonColor.SECONDARY)
        
        try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        except: pass
        return True

    if command == "set_special":
        bid = payload.get("biz_id")
        sql.execute("SELECT owner_id, clan_owner_id, special_order_active FROM businesses WHERE id = ?", (bid,))
        res = sql.fetchone()
        if res:
            owner, clan_owner, current_special = res
            ud = await get_user_data(user_id)
            if owner == user_id or (clan_owner > 0 and clan_owner == ud.get('clan_id') and await check_clan_perms(user_id, 4)):
                new_val = 0 if current_special else 1
                sql.execute("UPDATE businesses SET special_order_active = ? WHERE id = ?", (new_val, bid))
                database.commit()
                text = "активирован" if new_val else "деактивирован"
                await send_event_answer_safe(snackbar_text=f"📦 Спецзаказ {text}!")
                
                new_payload = {"command": "depo_special", "biz_id": bid, "chatId": chat_id, "initiator": user_id}
                try: obj_data = message.object.model_dump()
                except: obj_data = message.object.dict().copy()
                obj_data['payload'] = new_payload
                return await callback_handlers(GroupTypes.MessageEvent(group_id=message.group_id, event_id=message.event_id, object=obj_data))
        return True

    if command == "biz_manage":
        bid = int(payload.get("biz_id"))
        sql.execute("SELECT name, owner_id, type, repair_until, profit_per_hour, last_collect, level, price, clan_owner_id, tax_due_at FROM businesses WHERE id = ?", (bid,))
        res = sql.fetchone()
        if not res: return
        name, owner, b_type, repair, profit, last_col, lvl, base_price, clan_owner, tax_due_at = res
        
        ud = await get_user_data(user_id)
        is_personal_owner = (owner == user_id)
        is_clan_manager = (clan_owner > 0 and clan_owner == ud.get('clan_id') and await check_clan_perms(user_id, 4))
        is_clan_leader = (clan_owner > 0 and clan_owner == ud.get('clan_id') and await check_clan_perms(user_id, 5))

        if not is_personal_owner and not is_clan_manager: # Обновлено
            return await send_event_answer_safe(snackbar_text="❌ У вас нет прав на управление этим бизнесом!")

        await send_event_answer_safe()

        now = int(time.time())
        status = "✅ Работает" if now > repair else f"🛠 В ремонте ({int((repair-now)/60)} мин)"
        
        # Для станций показываем актуальную прибыль маршрута, для остальных — из БД
        display_profit = profit # Обновлено
        if b_type == 'station':
            sql.execute("SELECT active_route FROM businesses WHERE id = ?", (bid,))
            route_id = sql.fetchone()[0]
            display_profit = STATION_ROUTES.get(route_id, STATION_ROUTES[1])['profit']

        rem_collect = 0
        cd = 3600
        if now - last_col < cd:
            rem_collect = int((cd - (now - last_col)) / 60)
        
        collect_status = "✅ Готов к сбору" if rem_collect <= 0 else f"⏳ Будет через {rem_collect} мин"
        
        upgrade_cost = int(base_price * 0.4 * lvl)

        tax_msg = "Налог: не задан"
        if tax_due_at:
            if now > tax_due_at + 86400:
                tax_msg = "Налог просрочен, оплатите срочно или бизнес будет выставлен на аукцион"
            elif now > tax_due_at:
                due_left = int((tax_due_at + 86400 - now) / 3600)
                tax_msg = f"Налог просрочен, до аукциона {due_left} ч"
            else:
                tax_msg = f"Следующий налог: {datetime.fromtimestamp(tax_due_at).strftime('%d.%m %H:%M')}"

        msg = (f"💼 Меню управления: {name}\n"
               f"⭐ Уровень: {lvl}\n"
               f"⚙ Статус: {status}\n"
               f"💰 Прибыль: {display_profit:,}$/час\n"
               f"📌 {tax_msg}\n"
               f"📦 Сбор: {collect_status}").replace(",", ".")
        
        kb = Keyboard(inline=True)
        kb.add(Callback("💰 Собрать", {"command": "biz_collect", "biz_id": bid, "chatId": chat_id, "initiator": menu_owner}), color=KeyboardButtonColor.POSITIVE) # Обновлено
        if lvl < 5 and b_type != 'station':
            kb.add(Callback(f"🆙 Улучшить ({upgrade_cost:,}$)".replace(",","."), {"command": "biz_upgrade_menu", "biz_id": bid, "chatId": chat_id, "initiator": menu_owner}), color=KeyboardButtonColor.PRIMARY)
        
        if b_type == 'station':
            kb.add(Callback("🚋 Меню Депо", {"command": "depo", "biz_id": bid, "chatId": chat_id, "initiator": menu_owner}), color=KeyboardButtonColor.PRIMARY)
        kb.add(Callback("💸 Оплатить налог", {"command": "biz_pay_tax", "biz_id": bid, "chatId": chat_id, "initiator": menu_owner}), color=KeyboardButtonColor.SECONDARY)
        if is_clan_leader and clan_owner > 0: # Если это клановый бизнес и пользователь - лидер клана
            kb.add(Callback("Забрать себе", {"command": "biz_reclaim_from_clan", "biz_id": bid, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.NEGATIVE)
            
        kb.row().add(Callback("📊 Информация", {"command": "biz_info_btn", "biz_id": bid, "chatId": chat_id, "initiator": menu_owner}), color=KeyboardButtonColor.PRIMARY)
        kb.add(Callback("❌ Закрыть", {"command": "delete_msg", "initiator": menu_owner}), color=KeyboardButtonColor.SECONDARY)
        
        try:
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        except Exception as e:
            if "Flood control" in str(e):
                return True
            print(f"Failed to edit message for biz_manage: {e}")
            await bot.api.messages.send(peer_id=message.object.peer_id, message=msg, keyboard=kb, random_id=0)
        return True

    if command == "biz_pay_tax":
        bid = int(payload.get("biz_id"))
        sql.execute("SELECT owner_id, clan_owner_id FROM businesses WHERE id = ?", (bid,))
        row = sql.fetchone()
        if not row:
            return await send_event_answer_safe(snackbar_text="❌ Бизнес не найден!")
        owner, clan_owner = row

        ud = await get_user_data(user_id)
        is_personal_owner = (owner == user_id)
        is_clan_manager = (clan_owner > 0 and clan_owner == ud.get('clan_id') and await check_clan_perms(user_id, 4))
        if not is_personal_owner and not is_clan_manager:
            return await send_event_answer_safe(snackbar_text="❌ У вас нет прав на оплату налога для этого бизнеса!")

        success, msg = await pay_business_tax(bid, user_id, clan_owner)
        await send_event_answer_safe(snackbar_text=msg)
        if success:
            new_payload = {"command": "biz_manage", "biz_id": bid, "chatId": chat_id, "initiator": menu_owner}
            try:
                obj_data = message.object.model_dump() if hasattr(message.object, 'model_dump') else message.object.dict().copy()
                obj_data['payload'] = new_payload
                return await callback_handlers(GroupTypes.MessageEvent(group_id=message.group_id, event_id=message.event_id, object=obj_data))
            except:
                return True
        return True

    if command == "biz_upgrade_menu":
        bid = int(payload.get("biz_id")) # Обновлено
        sql.execute("SELECT name, owner_id, level, price, profit_per_hour, clan_owner_id FROM businesses WHERE id = ?", (bid,))
        res = sql.fetchone()
        if not res: return
        name, owner, lvl, price, profit, clan_owner, b_type = res[0], res[1], res[2], res[3], res[4], res[5], ""
        sql.execute("SELECT type FROM businesses WHERE id = ?", (bid,))
        b_type = sql.fetchone()[0]

        ud = await get_user_data(user_id)
        if owner != user_id and not (clan_owner > 0 and clan_owner == ud.get('clan_id') and await check_clan_perms(user_id, 4)):
            return await send_event_answer_safe(snackbar_text="❌ У вас нет прав на улучшение этого бизнеса!") # Обновлено
        if b_type == 'station': return await send_event_answer_safe(snackbar_text="❌ Вокзалы улучшаются через маршруты в Депо!")
        
        if lvl >= 5:
            return await send_event_answer_safe(snackbar_text="🏆 Максимальный уровень достигнут!")

        await send_event_answer_safe()

        upgrade_cost = int(price * 0.4 * lvl)
        balance = await get_balance(user_id)
        check_icon = "✅" if balance >= upgrade_cost else "❌"
        new_profit = int(profit * 1.25)

        msg = (f"🏢 Улучшение бизнеса: {name}\n"
               f"📊 Текущий уровень: {lvl}\n"
               f"🆙 Улучшение до уровня {lvl + 1}\n\n"
               f"🎁 Бонусы:\n"
               f"💰 Прибыль: {profit:,} ➔ {new_profit:,}$/час\n"
               f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
               f"📉 Требования:\n"
               f"{check_icon} Деньги: {balance:,}/{upgrade_cost:,}$\n"
               f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬").replace(",", ".")
        
        kb = Keyboard(inline=True).add(Callback("💎 Подтвердить", {"command": "biz_upgrade_do", "biz_id": bid, "chatId": chat_id, "initiator": menu_owner}), color=KeyboardButtonColor.POSITIVE).row().add(Callback("<< Назад", {"command": "biz_manage", "biz_id": bid, "chatId": chat_id, "initiator": menu_owner}), color=KeyboardButtonColor.SECONDARY)
        try:
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        except Exception as e:
            if "Flood control" in str(e):
                return True
            print(f"Failed to edit message for biz_upgrade_menu: {e}")
            await bot.api.messages.send(peer_id=message.object.peer_id, message=msg, keyboard=kb, random_id=0)
        return True

    if command == "biz_upgrade_do":
        await send_event_answer_safe() # Обновлено
        bid = int(payload.get("biz_id"))
        sql.execute("SELECT name, owner_id, level, price, profit_per_hour, clan_owner_id FROM businesses WHERE id = ?", (bid,))
        res = sql.fetchone()
        if not res: return
        name, owner, lvl, price, profit, clan_owner, b_type = res[0], res[1], res[2], res[3], res[4], res[5], ""
        sql.execute("SELECT type FROM businesses WHERE id = ?", (bid,))
        b_type = sql.fetchone()[0]

        ud = await get_user_data(user_id)
        if owner != user_id and not (clan_owner > 0 and clan_owner == ud.get('clan_id') and await check_clan_perms(user_id, 4)): # Обновлено
            return await send_event_answer_safe(snackbar_text="❌ У вас нет прав на улучшение!")
        if b_type == 'station': return await send_event_answer_safe(snackbar_text="❌ Вокзалы нельзя улучшать таким способом!")
        
        if lvl >= 5:
            return await send_event_answer_safe(snackbar_text="🏆 Максимальный уровень достигнут!")

        upgrade_cost = int(price * 0.4 * lvl)
        if not await subtract_balance(user_id, upgrade_cost):
            error_msg = f"❌ Недостаточно средств для улучшения! Нужно {upgrade_cost:,}$".replace(",", ".")
            await send_event_answer_safe(snackbar_text=error_msg)
            await bot.api.messages.send(peer_id=message.object.peer_id, message=f"⚠️ {await get_user_link(user_id)}, {error_msg}", random_id=0)
            return True

        new_lvl = lvl + 1
        # Увеличиваем прибыль на 25% от текущей
        new_profit = int(profit * 1.25)
        
        sql.execute("UPDATE businesses SET level = ?, profit_per_hour = ? WHERE id = ?", (new_lvl, new_profit, bid)) # Обновлено
        database.commit()
        
        await send_event_answer_safe(snackbar_text=f"✅ Уровень повышен до {new_lvl}!")
        
        # Возвращаемся в меню управления (через вызов команды заново)
        new_payload = {"command": "biz_manage", "biz_id": bid, "chatId": chat_id, "initiator": menu_owner}
        obj_data = message.object.dict() if hasattr(message.object, 'dict') else message.object.model_dump()
        obj_data['payload'] = new_payload
        return await callback_handlers(GroupTypes.MessageEvent(group_id=message.group_id, event_id=message.event_id, object=obj_data))

    if command == "biz_info_btn":
        bid = int(payload.get("biz_id"))
        sql.execute("SELECT name, price, owner_id, type, repair_until, clan_owner_id FROM businesses WHERE id = ?", (bid,))
        res = sql.fetchone()
        if not res: return
        name, price, owner, b_type, repair, clan_owner = res
        
        ud = await get_user_data(user_id)
        owner_str = await get_user_link(owner) if owner > 0 else "Государство"
        if clan_owner > 0: # Обновлено
            sql.execute("SELECT name FROM clans WHERE clan_id = ?", (clan_owner,))
            owner_str = f"Клан «{sql.fetchone()[0]}»"

        status = "✅ Работает" if time.time() > repair else f"🛠 Ремонт ({int((repair-time.time())/60)} мин)"
        msg = f"🏢 Бизнес: {name} (ID: {bid})\n🏷 Цена: {price:,}$\n👤 Владелец: {owner_str}\n⚙ Статус: {status}".replace(",", ".")
        kb = Keyboard(inline=True)
        if owner == 0 and clan_owner == 0: 
            kb.add(Callback("🛒 Купить", {"command": "biz_buy", "biz_id": bid, "chatId": chat_id, "initiator": menu_owner}), color=KeyboardButtonColor.POSITIVE) # Обновлено
        elif owner == user_id or (clan_owner > 0 and clan_owner == ud.get('clan_id') and await check_clan_perms(user_id, 4)): 
            kb.add(Callback("⚙ Управление", {"command": "biz_manage", "biz_id": bid, "chatId": chat_id, "initiator": menu_owner}), color=KeyboardButtonColor.PRIMARY)

        await send_event_answer_safe()
        try:
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb, disable_mentions=1)
        except Exception as e:
            if "Flood control" in str(e):
                return True
            print(f"Failed to edit message for biz_info_btn: {e}")
            await bot.api.messages.send(peer_id=message.object.peer_id, message=msg, keyboard=kb, random_id=0)
        return True

    if command == "pet_action":
        act = payload.get("act") # Обновлено
        p = await get_pet_data(user_id)
        if not p: return await send_event_answer_safe(snackbar_text="❌ У вас нет питомца!")

        # Кулдаун для действий с питомцем (3 секунды)
        now_ts = time.time()
        if now_ts - user_pet_cooldown.get(user_id, 0) < 3:
            return await send_event_answer_safe(snackbar_text="⏳ Не так часто! Питомец устал.")
        user_pet_cooldown[user_id] = now_ts

        if act == "feed":
            cost = 100
            if not await subtract_balance(user_id, cost):
                error_msg = f"❌ Недостаточно средств! Нужно {cost}$ для корма."
                await send_event_answer_safe(snackbar_text=error_msg)
                await bot.api.messages.send(peer_id=message.object.peer_id, message=f"⚠️ {await get_user_link(user_id)}, {error_msg}", random_id=0)
                return True
            # Обновлено
            new_hunger = min(100, p['hunger'] + 30)
            sql.execute("UPDATE pets SET hunger = ? WHERE user_id = ?", (new_hunger, user_id))
            database.commit()
            await send_event_answer_safe(snackbar_text="🍖 Питомец сыт и доволен! (+30% сытости)")
            
        elif act == "play":
            if p['energy'] >= 100:
                return await send_event_answer_safe(snackbar_text="😴 Питомец полон сил и не хочет играть!")
            
            new_energy = min(100, p['energy'] + 25)
            new_hunger = max(0, p['hunger'] - 10)
            new_exp = p['exp'] + random.randint(15, 30)
            new_lvl = p['lvl']
            # Обновлено
            if new_exp >= p['lvl'] * 150: # Порог опыта для уровня
                new_lvl += 1
                new_exp = 0
                await send_event_answer_safe(snackbar_text=f"🆙 Уровень вашего питомца повышен до {new_lvl}!")
            else:
                gain = new_exp - p['exp']
                await send_event_answer_safe(snackbar_text=f"🎾 Вы поиграли! +{gain} EXP (+25% энергии)")

            sql.execute("UPDATE pets SET energy = ?, hunger = ?, exp = ?, level = ? WHERE user_id = ?", (new_energy, new_hunger, new_exp, new_lvl, user_id))
            database.commit() # Обновлено

        # Обновляем сообщение с текущим состоянием
        p_upd = await get_pet_data(user_id)
        p_conf = PETS.get(p_upd['id'])
        exp_needed = p_upd['lvl'] * 150
        bar = "🟦" * int((p_upd['exp']/exp_needed)*10) + "⬜" * (10 - int((p_upd['exp']/exp_needed)*10))
        upd_text = (f"{p_conf['emoji']} Питомец: {p_upd['name']}\n"
                    f"⭐ Уровень: {p_upd['lvl']}\n"
                    f"📈 Опыт: {p_upd['exp']}/{exp_needed}\n{bar}\n"
                    f"🍖 Сытость: {p_upd['hunger']}%\n"
                    f"⚡ Энергия: {p_upd['energy']}%")
        kb = Keyboard(inline=True).add(Callback("🍖 Кормить (100$)", {"command": "pet_action", "act": "feed", "user": menu_owner}), color=KeyboardButtonColor.POSITIVE)
        kb.add(Callback("🎾 Играть", {"command": "pet_action", "act": "play", "user": menu_owner}), color=KeyboardButtonColor.PRIMARY)
        # Обновлено
        try:
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=upd_text, keyboard=kb)
        except: pass
        return True

    # --- Переносить из main_event_handlers для предотвращения конфликтов. ---
    if command == "delete_msg":
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id) # Обновлено
        return True

    if command == "unwarn_btn":
        if await get_role(user_id, chat_id) < 1: return await send_event_answer_safe(snackbar_text="⛔ Недостаточно прав!")
        await send_event_answer_safe()
        target_user = payload.get("user")
        new_warns = await unwarn(chat_id, target_user) # Обновлено
        await bot.api.messages.send(peer_id=message.object.peer_id, message=f"✅ Выговор снят. Осталось: {new_warns}/3", random_id=0)
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        return True

    if command == "unpred_btn":
        if await get_role(user_id, chat_id) < 1: return await send_event_answer_safe(snackbar_text="⛔ Недостаточно прав!")
        await send_event_answer_safe()
        target_user = payload.get("user")
        ud = await get_user_data(target_user)
        new_preds = max(0, ud['preds'] - 1) # Обновлено
        await update_user_data(target_user, 'preds', new_preds)
        await bot.api.messages.send(peer_id=message.object.peer_id, message=f"✅ Предупреждение снято. Осталось: {new_preds}/2", random_id=0)
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        return True

    if command == "nicks":
        if await get_role(user_id, chat_id) < 1: return await send_event_answer_safe(snackbar_text="⛔ Недостаточно прав!")
        await send_event_answer_safe() # Обновлено
        nicks = await nlist(chat_id, 1)
        text = "Ники в беседе:\n" + "\n".join(nicks)
        try:
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=text)
        except Exception as e:
            print(f"Failed to edit message for nicks: {e}")
            await bot.api.messages.send(peer_id=message.object.peer_id, message=text, random_id=0)
        return True

    if command == "set_type":
        chat_type = payload.get("type")
        # Проверяем права через API без устаревшего .json() (обновлено)
        members_resp = await bot.api.messages.get_conversation_members(peer_id=message.object.peer_id)
        is_admin = False
        for item in members_resp.items:
            if item.member_id == user_id and (item.is_admin or item.is_owner):
                is_admin = True
                break
        
        if await get_role(user_id, chat_id) < 6 and not is_admin:
            await send_event_answer_safe(snackbar_text="Только разработчик или админ может менять тип!")
            return True
        
        type_names = {'def': 'DEF - Общие', 'ext': 'EXT - Расширенная', 'pl': 'PL - Беседа игроков', 'hel': 'HEL - Беседа хеллперов', 'test': 'TEST - Беседа тестеров', 'ruk': 'RUK - Руководство'}
        type_name = type_names.get(chat_type, chat_type.upper())
        
        try:
            sql.execute("UPDATE chats SET chat_type = ? WHERE chat_id = ?", (chat_type, chat_id)) # Обновлено
            database.commit()
            await send_event_answer_safe(snackbar_text=f"✅ Тип установлен: {type_name}")
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=f"✅ Тип беседы изменен на: {type_name}", keyboard=None)
        except Exception as e:
            await send_event_answer_safe(snackbar_text=f"❌ Ошибка БД: {e}")
        return True

    if command == "type_page":
        await send_event_answer_safe() # Обновлено
        page = int(payload.get("page", 1))
        kb = Keyboard(inline=True)
        if page == 2:
            kb.add(Callback("MED", {"command": "set_type", "type": "med", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
            kb.add(Callback("RUK", {"command": "set_type", "type": "ruk", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
            kb.add(Callback("USERS", {"command": "set_type", "type": "users", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE).row()
            kb.add(Callback("<< Назад", {"command": "type_page", "page": 1, "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY)
        else:
            kb.add(Callback("DEF", {"command": "set_type", "type": "def", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            kb.add(Callback("EXT", {"command": "set_type", "type": "ext", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            kb.add(Callback("PL", {"command": "set_type", "type": "pl", "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE).row()
            kb.add(Callback("Дальше >>", {"command": "type_page", "page": 2, "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY)
        
        await bot.api.messages.edit(peer_id=message.object.peer_id, message="Выберите тип беседы:", conversation_message_id=message.object.conversation_message_id, keyboard=kb)
        return True

    if command == "clan_menu":
        await send_event_answer_safe() # Обновлено
        text, kb = await get_clan_menu_data(user_id, chat_id)
        if text:
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=text, keyboard=kb)
        return True

    if command == "kick":
        if await get_role(user_id, chat_id) < 1:
            return await send_event_answer_safe(snackbar_text="Недостаточно прав!")
        target = payload.get("user")
        if await equals_roles(user_id, target, chat_id) < 2:
            return await send_event_answer_safe(snackbar_text="У цели права выше ваших!") # Обновлено
        await send_event_answer_safe()
        target_link = await get_user_link(target)
        try:
            await bot.api.messages.remove_chat_user(chat_id, target)
            await bot.api.messages.send(peer_id=message.object.peer_id, message=f"👢 Пользователь {target_link} исключен.", random_id=0)
        except:
            await bot.api.messages.send(peer_id=message.object.peer_id, message=f"❌ Не удалось кикнуть {target_link}.", random_id=0)
        return True

    if command == "unmute":
        if await get_role(user_id, chat_id) < 1:
            return await send_event_answer_safe(snackbar_text="Недостаточно прав!")
        target = payload.get("user") # Обновлено
        await send_event_answer_safe()
        await unmute(target, chat_id)
        target_link = await get_user_link(target)
        await bot.api.messages.send(peer_id=message.object.peer_id, message=f"🔊 С пользователя {target_link} снят мут.", random_id=0)
        return True

    if command == "unban":
        if await get_role(user_id, chat_id) < 2:
            return await send_event_answer_safe(snackbar_text="Недостаточно прав!")
        target = payload.get("user") # Обновлено
        await send_event_answer_safe()
        await unban(target, chat_id)
        target_link = await get_user_link(target)
        await bot.api.messages.send(peer_id=message.object.peer_id, message=f"✅ Пользователь {target_link} разблокирован.", random_id=0)
        return True

    if command == "stats":
        await send_event_answer_safe()
        target = payload.get("user") # Обновлено
        ud = await get_user_data(target)
        msgs = await message_stats(target, chat_id)
        u_name = await get_user_name(target, chat_id)
        role_lvl = await get_role(target, chat_id)
        
        t_role = await get_tester_role(target)
        t_names = {1: " (Тестер)", 2: " (Старший тестер)", 3: " (Главный тестер)"}
        t_handled = ""
        if t_role > 0:
            sql.execute("SELECT handled FROM testers WHERE user_id = ?", (target,))
            h_res = sql.fetchone()
            if h_res: t_handled = f"\n🛠 Исправлено: {h_res[0]}"
        tester_info = f"{t_names.get(t_role, '')}{t_handled}"
        
        roles = {0: "Пользователь", 1: "Модератор", 2: "Ст. Модератор", 3: "Админ", 4: "Ст. Админ", 5: "Владелец", 6: "Разработчик"}
        target_link = await get_user_link(target)
        text = f"📊 Статистика {target_link}:\nРоль: {roles.get(role_lvl)}{tester_info}\nСообщений: {msgs['count']}\nПоследнее: {msgs['last']}\nБаллы: {ud['points']:,}".replace(",", ".")
        await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=text, keyboard=None)
        return True

    if command == "warnhistory":
        await send_event_answer_safe()
        target = payload.get("user") # Обновлено
        history = await warnhistory(target, chat_id)
        target_link = await get_user_link(target)
        text = f"📜 История варнов {target_link}:\n" + ("\n".join(history) if history else "Чисто.")
        await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=text, keyboard=None)
        return True

    if command == "casino":
        # Переносим логику казино из main_event_handlers
        bet = payload.get("bet") # Обновлено
        original_user = payload.get("user")
        if user_id != original_user:
            return await send_event_answer_safe(snackbar_text="Это не ваша ставка!")
        
        if time.time() - user_casino_cooldown.get(user_id, 0) < 10:
            return await send_event_answer_safe(snackbar_text="⏳ Подождите 10 секунд!")
        
        await send_event_answer_safe()
        user_casino_cooldown[user_id] = time.time()
        
        ud_eco = await get_user_economy_data(user_id)
        if not await subtract_balance(user_id, bet):
            return await bot.api.messages.send(peer_id=message.object.peer_id, message="❌ Недостаточно средств!", random_id=0)
            
        ud_full = await get_user_data(user_id)
        forced_rate = 0.0 if ud_full.get('no_comm_until', 0) > time.time() else None
        results, win, mult, commission, comm_rate = get_casino_result(bet, ud_eco.get('vip_level', 0), forced_rate=forced_rate)
        res_str = f"[{results[0]} {results[1]} {results[2]}]"
        
        user_link = await get_user_link(user_id)
        if win > 0:
            await add_balance(user_id, win)
            new_bal = await get_balance(user_id)
            log_transaction(user_id, f"Казино: ставка {bet}$, результат {res_str}, выигрыш +{win}$")
            final_text = f"🎰 Игрок {user_link}\n💰 Ставка: {bet:,}$\n🎡 Результат: {res_str}\n✅ Выигрыш: {win:,}$ (x{mult})\n💰 Баланс: {new_bal:,}$".replace(",", ".")
            if mult >= 30:
                announcement = f"🔥 ВНИМАНИЕ! 🔥\n🎰 Игрок {user_link} сорвал КУШ в казино!\n📈 Множитель: x{mult}\n💰 Сумма выигрыша: {win:,}$!".replace(",", ".")
                await bot.api.messages.send(peer_id=message.object.peer_id, message=announcement, random_id=0, disable_mentions=1)
        else:
            new_bal = await get_balance(user_id)
            log_transaction(user_id, f"Казино: ставка {bet}$, результат {res_str}, проигрыш")
            final_text = f"🎰 Игрок {user_link}\n💰 Ставка: {bet:,}$\n🎡 Результат: {res_str}\n❌ Вы проиграли!\n💰 Баланс: {new_bal:,}$".replace(",", ".")
            
        await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=final_text, keyboard=None)
        return True

    if command == "other_menu":
        await send_event_answer_safe() # Обновлено
        category = payload.get("category")
        kb = Keyboard(inline=True)
        if category == "economy":
            text = "💰 Экономика:\n/баланс, /приз, /передать, /дуэль, /казино, /депозит, /работа"
            kb.add(Callback("<< Назад", {"command": "other_menu", "category": "main", "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY)
        elif category == "clans":
            text = "🏰 Кланы:\n/clan, /topclan, /clan create, /clan invite, /clan kick, /clan war"
            kb.add(Callback("<< Назад", {"command": "other_menu", "category": "main", "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY) # Обновлено
        else:
            text = "🎮 Меню «Другое»:"
            kb.add(Callback("💰 Экономика", {"command": "other_menu", "category": "economy", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            kb.add(Callback("🏰 Кланы", {"command": "other_menu", "category": "clans", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
        
        await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=text, keyboard=kb)
        return True

    if command == "ticket_reply":
        await send_event_answer_safe() # Обновлено
        tid = payload.get("id")
        sql.execute("SELECT user_id FROM support_tickets WHERE id = ?", (tid,))
        res = sql.fetchone()
        if not res:
            return await bot.api.messages.send(peer_id=message.object.peer_id, message="❌ Тикет не найден!", random_id=0)
            
        user_states[user_id] = {
            "action": "reply_ticket", 
            "target_user": res[0], 
            "tid": tid,
            "source_peer": message.object.peer_id,
            "source_cmid": message.object.conversation_message_id
        }
        await bot.api.messages.send(peer_id=message.object.peer_id, message=f"✍️ Введите ответ на тикет #{tid} (или «отмена»):", random_id=0)
        return True

    if command == "ticket_consider":
        tid = payload.get("id") # Обновлено
        sql.execute("UPDATE support_tickets SET status = 'в рассмотрении' WHERE id = ?", (tid,))
        database.commit()
        await send_event_answer_safe(snackbar_text="Тикет переведен в рассмотрение")
        try:
            resp = await bot.api.messages.get_by_conversation_message_id(peer_id=message.object.peer_id, conversation_message_ids=[message.object.conversation_message_id])
            if resp.items:
                new_text = resp.items[0].text.replace("⏳ Статус: Ожидание", "⏳ Статус: На рассмотрении")
                await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=new_text)
        except: pass
        return True

    if command == "ticket_reject":
        tid = payload.get("id") # Обновлено
        sql.execute("UPDATE support_tickets SET status = 'отклонено' WHERE id = ?", (tid,))
        database.commit()
        await send_event_answer_safe(snackbar_text="Тикет отклонен")
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        return True

    if command == "toggle_setting":
        key = payload.get("key") # Обновлено
        perm_map = {"antiflood": 5, "filter": 4, "invite_kick": 5, "leave_kick": 5, "silence": 3, "games": 6, "link_filter": 5}
        if await get_role(user_id, chat_id) < perm_map.get(key, 5):
            await send_event_answer_safe(snackbar_text="⛔ Недостаточно прав!")
            return True
            
        sql.execute(f"SELECT {key} FROM chats WHERE chat_id = ?", (chat_id,))
        new_val = 0 if sql.fetchone()[0] else 1
        sql.execute(f"UPDATE chats SET {key} = ? WHERE chat_id = ?", (new_val, chat_id))
        database.commit() # Обновлено
        
        await send_event_answer_safe(snackbar_text="✅ Настройка изменена!")
        
        sql.execute("SELECT antiflood, filter, invite_kick, leave_kick, silence, games, link_filter FROM chats WHERE chat_id = ?", (chat_id,))
        af, fltr, ik, lk, slnc, gms, lnk = sql.fetchone()
        def s_status(val): return "✅" if val else "❌"
        msg_text = (f"⚙️ Настройки беседы ID: {chat_id}\n\n"
                   f"{s_status(af)} Антифлуд\n"
                   f"{s_status(fltr)} Фильтр слов\n"
                   f"{s_status(ik)} Инвайт-кик\n"
                   f"{s_status(lk)} Лив-кик\n"
                   f"{s_status(slnc)} Тихий режим\n"
                   f"{s_status(gms)} Игровые команды\n"
                   f"{s_status(lnk)} Фильтр ссылок")
        kb = Keyboard(inline=True)
        kb.add(Callback("🛡 Антифлуд", {"command": "toggle_setting", "key": "antiflood", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY)
        kb.add(Callback("🚫 Фильтр", {"command": "toggle_setting", "key": "filter", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
        kb.add(Callback("🚪 Инвайт-кик", {"command": "toggle_setting", "key": "invite_kick", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY)
        kb.add(Callback("👢 Лив-кик", {"command": "toggle_setting", "key": "leave_kick", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
        kb.add(Callback("🔇 Тишина", {"command": "toggle_setting", "key": "silence", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY)
        kb.add(Callback("🔗 Ссылки", {"command": "toggle_setting", "key": "link_filter", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
        if await get_role(user_id, chat_id) >= 6: kb.add(Callback("🎮 Игры", {"command": "toggle_setting", "key": "games", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
        else: pass # Обновлено
        kb.add(Callback("❌ Закрыть", {"command": "delete_msg", "user": user_id}), color=KeyboardButtonColor.SECONDARY)
        try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg_text, keyboard=kb)
        except: pass
        return True

    # В качестве резервного варианта по умолчанию вращение любой другой кнопки прекращается.
    if not event_acknowledged:
        await send_event_answer_safe() # Обновлено
    return True

async def check_chat(chat_id=int):
    sql.execute("SELECT * FROM chats WHERE chat_id = ?", (chat_id,))
    if sql.fetchone() == None: return False
    else: return True

async def new_chat(chat_id=int, peer_id=int, owner_id=int, chat_title:str = None):
    default_welcome = (
        "Привет, %n! 👋 Добро пожаловать в нашу беседу.\n\n"
        "📌 Пожалуйста, ознакомься с правилами, чтобы избежать наказаний:\n"
        "📜 Правила этой беседы — /правила\n"
        "🤖 Общие правила бота — /правилабота\n\n"
        "👤 По всем вопросам и предложениям пиши владельцу: [id460366734|Написать мне]\n\n"
        "Желаем приятного общения!"
    )
    
    sql.execute("INSERT INTO chats (chat_id, peer_id, owner_id, chat_title, welcome, invite_kick, leave_kick, in_pull, silence, filter, antiflood, chat_type, bot_rules, project_info, games, rules, ignore_commands, maint_ignore, autopost) VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, 'def', NULL, NULL, 1, ?, 0, 0, 1)", 
                (chat_id, peer_id, owner_id, chat_title or "", default_welcome, CHAT_RULES_DEFAULT_TEXT))
    sql.execute(f"CREATE TABLE IF NOT EXISTS permissions_{chat_id} (user_id BIGINT, level BIGINT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS nicks_{chat_id} (user_id BIGINT, nick TEXT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS banwords_{chat_id} (banword TEXT PRIMARY KEY, duration INTEGER DEFAULT 30);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS warns_{chat_id} (user_id BIGINT, count BIGINT, moder BIGINT, reason TEXT, date BIGINT, date_string TEXT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS warnhistory_{chat_id} (user_id BIGINT, count BIGINT, moder BIGINT, reason TEXT, date BIGINT, date_string TEXT, time BIGINT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS mutes_{chat_id} (user_id BIGINT, moder TEXT, reason TEXT, date BIGINT, date_string TEXT, time BIGINT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS bans_{chat_id} (user_id BIGINT, moder BIGINT, reason TEXT, date BIGINT, date_string TEXT);")
    sql.execute(f"CREATE TABLE IF NOT EXISTS messages_{chat_id} (user_id BIGINT, date BIGINT, date_string TEXT, message_id BIGINT, cmid BIGINT, message_text TEXT DEFAULT '');")
    sql.execute(f"CREATE TABLE IF NOT EXISTS punishments_{chat_id} (user_id BIGINT, date TEXT);")
    database.commit()

async def get_warns(user_id=int, chat_id=int):
    sql.execute(f"SELECT count FROM warns_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    if fetch == None: return 0
    else: return fetch[0]

async def get_user_name(user_id=int, chat_id=int):
    sql.execute(f"SELECT nick FROM nicks_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    name = fetch[0] if fetch is not None else None 

    if name is None:
        try:
            info = await bot.api.users.get(user_ids=user_id)
            if info:
                name = f"{info[0].first_name} {info[0].last_name}"
            else:
                name = 'Пользователь'
        except Exception:
            name = 'Пользователь'

    # Добавляем кастомный префикс (из тестершопа), если он есть
    sql.execute("SELECT custom_prefix FROM user_data WHERE user_id = ?", (user_id,))
    p_res = sql.fetchone()
    if p_res and p_res[0]:
        clean_prefix = str(p_res[0]).replace("[", "").replace("]", "")
        name = f"[{clean_prefix}] {name}"
        
    return name

async def get_user_link(user_id: int, name_case: str = "nom"):
    if user_id < 0:
        try:
            group = await bot.api.groups.get_by_id(group_id=abs(user_id))
            return f"[club{abs(user_id)}|{group[0].name}]"
        except:
            return f"@club{abs(user_id)}"
    try:
        info = await bot.api.users.get(user_ids=user_id, name_case=name_case)
        return f"[id{user_id}|{info[0].first_name} {info[0].last_name}]"
    except:
        return f"@id{user_id}"

async def get_bot_ping_ms():
    start = time.perf_counter()
    try:
        await bot.api.users.get(user_ids=1)
    except Exception:
        return None
    return int((time.perf_counter() - start) * 1000)

async def get_full_user_display(user_id, chat_id):
    role_lvl = await get_role(user_id, chat_id) # Обновлено
    roles = {0: "Пользователь", 1: "Модератор", 2: "Старший Модератор", 3: "Администратор", 4: "Старший Администратор", 5: "Владелец беседы", 6: "Разработчик бота"}
    role_name = (await get_custom_role_name(user_id, chat_id)) or roles.get(role_lvl, "Пользователь")
    user_name = await get_user_name(user_id, chat_id)
    return f"{role_name} | {user_name}"

async def get_first_name_safe(user_id: int) -> str:
    try:
        info = await bot.api.users.get(user_ids=user_id)
        if info:
            return info[0].first_name
    except Exception:
        pass
    return "Пользователь"

async def is_nick(user_id=int, chat_id=int):
    sql.execute(f"SELECT nick FROM nicks_{chat_id} WHERE user_id = ?", (user_id,))
    if sql.fetchone() == None: return False
    else: return True

async def setnick(user_id=int, chat_id=int, nick=str):
    sql.execute(f"SELECT nick FROM nicks_{chat_id} WHERE user_id = ?", (user_id,))
    if sql.fetchone() == None:
        sql.execute(f"INSERT INTO nicks_{chat_id} VALUES (?, ?)", (user_id, nick))
        database.commit()
    else:
        sql.execute(f"UPDATE nicks_{chat_id} SET nick = ? WHERE user_id = ?", (nick, user_id))
        database.commit()

async def rnick(user_id=int, chat_id=int):
    sql.execute(f"DELETE FROM nicks_{chat_id} WHERE user_id = ?", (user_id,))
    database.commit()

async def equals_roles(user_id_sender=int, user_id_two=int, chat_id=int):
    if await get_role(user_id_sender, chat_id) > await get_role(user_id_two, chat_id): # Обновлено
        return 2
    elif await get_role(user_id_sender, chat_id) == await get_role(user_id_two, chat_id):
        return 1
    else: return 0

async def get_acc(chat_id=int, nick=str):
    sql.execute(f"SELECT user_id FROM nicks_{chat_id} WHERE nick = ?", (nick,))
    fetch = sql.fetchone()
    if fetch == None: return False
    else: return fetch[0]

async def get_nick(user_id=int, chat_id=int):
    sql.execute(f"SELECT nick FROM nicks_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    if fetch == None: return False
    else: return fetch[0]

async def nlist(chat_id=int, page=int):
    sql.execute(f"SELECT * FROM nicks_{chat_id}")
    fetch = sql.fetchall()
    nicks = []
    gi = 0
    with open("config.json", "r") as json_file:
        open_file = json.load(json_file)
    max_nicks = open_file['nicks_max']
    for i in fetch: # Обновлено
        gi = gi + 1
        if page * max_nicks >= gi and page * max_nicks - max_nicks < gi:
            info = await bot.api.users.get(user_ids=i[0])
            if info:
                user_name = f"{info[0].first_name} {info[0].last_name}"
            else:
                user_name = "Удаленный пользователь"
            
            nicks.append(f'{gi}) @id{i[0]} ({user_name}) - {i[1]}')
    return nicks

async def nonick(chat_id=int, page=int):
    sql.execute(f"SELECT * FROM nicks_{chat_id}")
    fetch = sql.fetchall()
    nicks = []
    for i in fetch:
        nicks.append(i[0])

    gi = 0
    nonick = []
    with open("config.json", "r") as json_file:
        open_file = json.load(json_file)
    max_nonick = open_file['nonick_max']
    users = await bot.api.messages.get_conversation_members(peer_id=2000000000+chat_id) # Обновлено
    users = json.loads(users.json())
    for i in users["profiles"]:
        if not i['id'] in nicks:
            gi = gi + 1
            if page*max_nonick >= gi and page*max_nonick-max_nonick < gi:
                nonick.append(f"{gi}) @id{i['id']} ({i['first_name']} {i['last_name']})")

    return nonick

async def warn(chat_id=int, user_id=int, moder=int, reason=str):
    actualy_warns = await get_warns(user_id, chat_id)
    date = time.time()
    cd = str(datetime.now()).split('.') # Обновлено
    date_string = cd[0]
    sql.execute(f"INSERT INTO warnhistory_{chat_id} (user_id, count, moder, reason, date, date_string) VALUES (?, ?, ?, ?, ?, ?)", (user_id, actualy_warns+1, moder, reason, date, date_string))
    database.commit()
    if actualy_warns < 1:
        sql.execute(f"INSERT INTO warns_{chat_id} VALUES (?, 1, ?, ?, ?, ?)", (user_id, moder, reason, date, date_string))
        database.commit()
        res_warns = 1
    else:
        sql.execute(f"UPDATE warns_{chat_id} SET user_id = ?, count = ?, moder = ?, reason = ?, date = ?, date_string = ? WHERE user_id = ?", (user_id, actualy_warns+1, moder, reason, date, date_string, user_id))
        database.commit()
        res_warns = actualy_warns+1
    
    asyncio.create_task(sync_user_to_sheet(user_id, chat_id, 'warns', res_warns))
    return res_warns

async def clear_warns(chat_id=int, user_id=int):
    sql.execute(f"DELETE FROM warns_{chat_id} WHERE user_id = ?", (user_id,))
    database.commit()
    asyncio.create_task(sync_user_to_sheet(user_id, chat_id, 'warns', 0))

async def unwarn(chat_id=int, user_id=int):
    warns = await get_warns(user_id, chat_id)
    if warns < 2: await clear_warns(chat_id, user_id)
    else: # Обновлено
        sql.execute(f"UPDATE warns_{chat_id} SET count = ? WHERE user_id = ?", (warns-1, user_id))
        database.commit()
        asyncio.create_task(sync_user_to_sheet(user_id, chat_id, 'warns', warns-1))

    return warns-1

async def gwarn(user_id=int, chat_id=int):
    sql.execute(f"SELECT * FROM warns_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    if fetch == None: return False
    else:
        return {
            'count': fetch[1],
            'moder': fetch[2],
            'reason': fetch[3],
            'time': fetch[5]
        }

async def warnhistory(user_id=int, chat_id=int):
    sql.execute(f"SELECT * FROM warnhistory_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchall()
    warnhistory_mass = []
    gi = 0 # Обновлено
    if fetch == None: return False
    else:
        for i in fetch:
            gi = gi + 1
            warnhistory_mass.append(f"{gi}) [id{i[2]}|Модератор] | {i[3]} | {i[5]}")

    return warnhistory_mass

async def warnlist(chat_id=int):
    sql.execute(f"SELECT * FROM warns_{chat_id}")
    fetch = sql.fetchall()
    warns = [] # Обновлено
    gi = 0
    for i in fetch:
        gi = gi + 1
        warns.append(f"{gi}) [id{i[0]}|Пользователь] | {i[3]} | [id{i[2]}|Модератор] | {i[1]}/3 | {i[5]}")

    if fetch == None: return False
    return warns

async def staff(chat_id=int):
    roles_data = {} # Обновлено

    # 1. Получаем всех глобальных менеджеров и отдельно разработчиков
    sql.execute("SELECT user_id, level FROM global_managers")
    all_global_managers = sql.fetchall()
    global_manager_ids = {user_id for user_id, level in all_global_managers if user_id > 0}
    developer_ids = {user_id for user_id, level in all_global_managers if level >= 5 and user_id > 0}
    
    if developer_ids:
        role_name = "Разработчик бота"
        roles_data[role_name] = []
        for dev_id in developer_ids:
            name = await get_user_name(dev_id, chat_id)
            roles_data[role_name].append(f"— [id{dev_id}|{name}]")

    # 2. Получаем локальный персонал (стандартные роли)
    sql.execute(f"SELECT * FROM permissions_{chat_id}")
    fetch = sql.fetchall()
    
    std_names = {1: "Модераторы", 2: "Старшие модераторы", 3: "Администраторы", 4: "Старшие администраторы"}
    
    if fetch:
        for i in fetch:
            # Пропускаем всех, у кого есть любая глобальная роль
            if i[0] in global_manager_ids: continue
            level = i[1]
            if level in std_names:
                role_name = std_names[level]
                if role_name not in roles_data: roles_data[role_name] = []
                name = await get_user_name(i[0], chat_id)
                roles_data[role_name].append(f"— [id{i[0]}|{name}]")

    # 3. Получаем кастомные роли
    sql.execute("SELECT ur.user_id, ur.role_name, r.priority FROM user_roles ur JOIN chat_roles r ON ur.chat_id = r.chat_id AND ur.role_name = r.name WHERE ur.chat_id = ? ORDER BY r.priority DESC", (chat_id,))
    fetch_custom = sql.fetchall()
    for i in fetch_custom:
        # Пропускаем всех, у кого есть любая глобальная роль
        if i[0] in global_manager_ids: continue
        role_name = i[1] # Обновлено
        if role_name not in roles_data: roles_data[role_name] = []
        name = await get_user_name(i[0], chat_id)
        roles_data[role_name].append(f"— [id{i[0]}|{name}]")

    return roles_data

async def mute(user_id=int, chat_id=int, moder=int, reason=str, mute_time=int):
    cd = str(datetime.now()).split('.')
    date_string = cd[0] # Обновлено
    sql.execute(f"INSERT INTO mutes_{chat_id} VALUES (?, ?, ?, ?, ?, ?)", (user_id, moder, reason, time.time(), date_string, mute_time))
    database.commit()

async def get_mute(user_id=int, chat_id=int):
    await checkMute(chat_id, user_id)

    sql.execute(f"SELECT * FROM mutes_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()

    if fetch == None: return False
    else:
        return {
            'moder': fetch[1],
            'reason': fetch[2],
            'date': fetch[4],
            'time': fetch[5]
        }

async def unmute(user_id=int, chat_id=int):
    sql.execute(f"DELETE FROM mutes_{chat_id} WHERE user_id = ?", (user_id,))
    database.commit()

async def mutelist(chat_id=int):
    sql.execute(f"SELECT * FROM mutes_{chat_id}")
    fetch = sql.fetchall()
    mutes = [] # Обновлено
    if fetch==None: return False
    else:
        for i in fetch:
            if not await checkMute(chat_id, i[0]):
                do_time = datetime.fromisoformat(i[4]) + timedelta(minutes=int(i[5] or 0))
                mute_time = str(do_time).split('.')[0]
                try:
                    int(i[1])
                    mutes.append(f"[id{i[1]}|модератор] | {i[2]} | [id{i[0]}|Пользователь] | До: {mute_time}")
                except: mutes.append(f"Бот | {i[2]} | [id{i[0]}|Пользователь] | До: {mute_time}")

    return mutes

async def checkMute(chat_id=int, user_id=int):
    sql.execute(f"SELECT * FROM mutes_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    if not fetch == None: # Обновлено
        do_time = datetime.fromisoformat(fetch[4]) + timedelta(minutes=int(fetch[5] or 0))
        if datetime.now() > do_time:
            sql.execute(f"DELETE FROM mutes_{chat_id} WHERE user_id = ?", (user_id,))
            database.commit()
            return True
        else: return False
    return False

async def check_quit(chat_id=int):
    sql.execute("SELECT silence FROM chats WHERE chat_id = ?", (chat_id,))
    fetch = sql.fetchone()
    if fetch == None: return False # Обновлено
    else:
        silence_val = fetch[0] if fetch[0] is not None else 0
        return await get_logic(silence_val)

async def get_banwords(chat_id=None):
    sql.execute("SELECT banword FROM global_banwords")
    banwords = []
    fetch = sql.fetchall()
    for i in fetch:
        banwords.append(i[0])
    return banwords

async def get_local_banwords(chat_id):
    try:
        sql.execute(f"SELECT banword, duration FROM banwords_{chat_id}")
        return sql.fetchall()
    except sqlite3.OperationalError as e:
        if "no such column: duration" in str(e):
            sql.execute(f"ALTER TABLE banwords_{chat_id} ADD COLUMN duration INTEGER DEFAULT 30")
            database.commit()
            sql.execute(f"SELECT banword, duration FROM banwords_{chat_id}")
            return sql.fetchall()
        return []

async def clear(user_id=int, chat_id=int, group_id=int, peer_id=int):
    sql.execute(f"SELECT cmid FROM messages_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchall()
    cmids = [] # Обновлено
    gi = 0
    for i in fetch:
        gi = gi + 1
        if gi <= 199:
            cmids.append(i[0])

    # Дополнительная очистка сообщений бота/групп (так как они не сохраняются в БД)
    if user_id < 0:
        try:
            history = await bot.api.messages.get_history(peer_id=peer_id, count=200)
            for item in history.items:
                if item.from_id == user_id:
                    if item.conversation_message_id not in cmids:
                        cmids.append(item.conversation_message_id)
        except Exception: pass

    if cmids:
        try: await bot.api.messages.delete(group_id=group_id, peer_id=peer_id, delete_for_all=True, cmids=cmids) # Обновлено
        except: pass

    sql.execute(f"DELETE FROM messages_{chat_id} WHERE user_id = ?", (user_id,))
    database.commit()

async def ensure_message_text_columns(chat_id=int):
    try:
        sql.execute(f"ALTER TABLE messages_{chat_id} ADD COLUMN message_text TEXT DEFAULT ''")
        database.commit()
    except sqlite3.OperationalError:
        pass

async def new_message(user_id=int, message_id=int, cmid=int, chat_id=int, message_text:str = ""):
    cd = str(datetime.now()).split('.')
    date_string = cd[0] # Обновлено
    await ensure_message_text_columns(chat_id)
    sql.execute(f"INSERT INTO messages_{chat_id} VALUES (?, ?, ?, ?, ?, ?)", (user_id, time.time(), date_string, message_id, cmid, message_text))
    database.commit()

async def checkban(user_id=int, chat_id=int):
    sql.execute(f"SELECT * FROM bans_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    if fetch == None: return False
    else:
        return {
            'moder': fetch[1],
            'reason': fetch[2],
            'date': fetch[4]
        }

async def ban(user_id=int, moder=int, chat_id=int, reason=str):
    sql.execute(f"SELECT user_id FROM bans_{chat_id} WHERE user_id = ?", (user_id,))
    fetch = sql.fetchone()
    cd = str(datetime.now()).split('.') # Обновлено
    date_string = cd[0]
    if fetch == None:
        sql.execute(f"INSERT INTO bans_{chat_id} VALUES (?, ?, ?, ?, ?)", (user_id, moder, reason, time.time(), date_string))
        database.commit()
    else:
        sql.execute(f"DELETE FROM bans_{chat_id} WHERE user_id = ?", (user_id,))
        sql.execute(f"INSERT INTO bans_{chat_id} VALUES (?, ?, ?, ?, ?)",(user_id, moder, reason, time.time(), date_string))
        database.commit()

async def unban(user_id=int, chat_id=int):
    sql.execute(f"DELETE FROM bans_{chat_id} WHERE user_id = ?", (user_id,))
    database.commit()

async def roleG(user_id=int, chat_id=int, role=int):
    sql.execute(f"SELECT user_id FROM permissions_{chat_id} WHERE user_id = ?", (user_id,)) # Исправлена опечатка
    fetch = sql.fetchone()
    if fetch == None:
        if role == 0: sql.execute(f"DELETE FROM permissions_{chat_id} WHERE user_id = ?", (user_id,))
        else: sql.execute(f"INSERT INTO permissions_{chat_id} VALUES (?, ?)", (user_id, role))
    else:
        if role == 0: sql.execute(f"DELETE FROM permissions_{chat_id} WHERE user_id = ?", (user_id,))
        else: sql.execute(f"UPDATE permissions_{chat_id} SET level = ? WHERE user_id = ?", (role, user_id))

    database.commit()

async def banlist(chat_id=int):
    sql.execute(f"SELECT * FROM bans_{chat_id}")
    fetch = sql.fetchall()
    banlist = [] # Обновлено
    for i in fetch:
        banlist.append(f"[id{i[1]}|Модератор] | {i[2]} | [id{i[0]}|Пользователь] | {i[4]}")

    return banlist

async def quiet(chat_id=int):
    sql.execute("SELECT silence FROM chats WHERE chat_id = ?", (chat_id,))
    fetch = sql.fetchone()
    if fetch is None: return False
    result = fetch[0]
    if not await get_logic(result): # Обновлено
        sql.execute("UPDATE chats SET silence = 1 WHERE chat_id = ?", (chat_id,))
        database.commit()
        return True
    else:
        sql.execute("UPDATE chats SET silence = 0 WHERE chat_id = ?", (chat_id,))
        database.commit()
        return False

async def get_server_chats(chat_id=int):
    sql.execute("SELECT in_pull FROM chats WHERE chat_id = ?", (chat_id,))
    fetch = sql.fetchone()
    if fetch == None: return False # Обновлено
    if not await get_logic(fetch[0]): return False
    sql.execute("SELECT chat_id FROM chats WHERE in_pull = ?", (fetch[0],))
    result = []
    fetch2 = sql.fetchall()
    for i in fetch2:
        result.append(i[0])

    return result

async def get_server_id(chat_id=int):
    sql.execute("SELECT in_pull FROM chats WHERE chat_id = ?", (chat_id,))
    fetch = sql.fetchone()
    return fetch[0]

async def rnickall(chat_id=int):
    sql.execute(f"DELETE FROM nicks_{chat_id}")
    database.commit()

async def banwords(slovo=str, delete=bool):
    if delete: # Обновлено
        sql.execute("DELETE FROM global_banwords WHERE banword = ?", (slovo, ))
    else:
        sql.execute("INSERT OR IGNORE INTO global_banwords (banword) VALUES (?)", (slovo,))
    database.commit()

async def get_filter(chat_id=int):
    sql.execute("SELECT filter FROM chats WHERE chat_id = ?", (chat_id,))
    fetch = sql.fetchone()
    if fetch is None: return False
    if fetch is None: return False
    return await get_logic(fetch[0]) # Обновлено

async def set_filter(chat_id=int, value=int):
    sql.execute("UPDATE chats SET filter = ? WHERE chat_id = ?", (value, chat_id))
    database.commit()

async def get_antiflood(chat_id=int):
    sql.execute("SELECT antiflood FROM chats WHERE chat_id = ?", (chat_id,))
    fetch = sql.fetchone()
    return await get_logic(fetch[0]) # Обновлено

async def set_antiflood(chat_id=int, value=int):
    sql.execute("UPDATE chats SET antiflood = ? WHERE chat_id = ?", (value, chat_id))
    database.commit()

async def get_link_filter(chat_id=int):
    sql.execute("SELECT link_filter FROM chats WHERE chat_id = ?", (chat_id,))
    fetch = sql.fetchone()
    if fetch is None: return True
    return await get_logic(fetch[0])

async def set_link_filter(chat_id=int, value=int):
    sql.execute("UPDATE chats SET link_filter = ? WHERE chat_id = ?", (value, chat_id))
    database.commit()

async def get_games(chat_id=int):
    sql.execute("SELECT games FROM chats WHERE chat_id = ?", (chat_id,))
    fetch = sql.fetchone()
    if fetch is None: return True # Обновлено
    return await get_logic(fetch[0])

async def set_games(chat_id=int, value=int):
    sql.execute("UPDATE chats SET games = ? WHERE chat_id = ?", (value, chat_id))
    database.commit()

async def get_spam(user_id=int, chat_id=int):
    sql.execute(f"SELECT date_string FROM messages_{chat_id}  WHERE user_id = ? ORDER BY date_string DESC LIMIT 3", (user_id,))
    fetch = sql.fetchall()
    # Обновлено
    if len(fetch) < 3:
        return False
        
    list_messages = []
    for i in fetch:
        list_messages.append(datetime.fromisoformat(i[0]))

    if list_messages[0] - list_messages[2] < timedelta(seconds=2): return True
    else: return False

async def set_welcome(chat_id=int, text=int):
    sql.execute("UPDATE chats SET welcome = ? WHERE chat_id = ?", (text, chat_id))
    database.commit()

async def get_welcome(chat_id=int):
    sql.execute("SELECT welcome FROM chats WHERE chat_id = ?", (chat_id, ))
    fetch = sql.fetchone()
    if str(fetch[0]).lower().strip() == "off": return False # Обновлено
    else: return str(fetch[0])

async def invite_kick(chat_id=int, change=None):
    sql.execute("SELECT invite_kick FROM chats WHERE chat_id = ?", (chat_id, ))
    sql.execute("SELECT invite_kick FROM chats WHERE chat_id = ?", (chat_id,))
    fetch = sql.fetchone()
    if not change == None:
        if await get_logic(fetch[0]): # Обновлено
            sql.execute("UPDATE chats SET invite_kick = 0 WHERE chat_id = ?", (chat_id, ))
            database.commit()
            return False
        else:
            sql.execute("UPDATE chats SET invite_kick = 1 WHERE chat_id = ?", (chat_id,))
            database.commit()
            return True
    else:
        return await get_logic(fetch[0])

            # Обновлено
async def leave_kick(chat_id=int, change=None):
    sql.execute("SELECT leave_kick FROM chats WHERE chat_id = ?", (chat_id,))
    fetch = sql.fetchone()
    if fetch == None: return False
    if change == None: return await get_logic(fetch[0])
    if await get_logic(fetch[0]):
        sql.execute("UPDATE chats SET leave_kick = 0 WHERE chat_id = ?", (chat_id,))
        database.commit()
        return False
    else:
        sql.execute("UPDATE chats SET leave_kick = 1 WHERE chat_id = ?", (chat_id,))
        database.commit()
        return True

async def message_stats(user_id=int, chat_id=int):
    try:
        sql.execute(f"SELECT date_string FROM messages_{chat_id} WHERE user_id = ?", (user_id, ))
        fetch_all = sql.fetchall() # Обновлено
        sql.execute(f"SELECT date_string FROM messages_{chat_id} WHERE user_id = ? ORDER BY date_string DESC LIMIT 1", (user_id,))
        fetch_last = sql.fetchone()
        last = fetch_last[0]
        return {
            'count': len(fetch_all),
            'last': last
        }
    except: return {
        'count': 0, # Обновлено
        'last': 0
    }

async def set_server(chat_id=int, value=int):
    sql.execute("UPDATE chats SET in_pull = ? WHERE chat_id = ?", (value, chat_id))
    database.commit()

async def get_all_peerids():
    sql.execute("SELECT peer_id FROM chats")
    fetch = sql.fetchall()
    peer_ids = []
    for i in fetch:
        peer_ids.append(i[0])

    return peer_ids

async def add_punishment(chat_id=int, user_id=int):
    cd = str(datetime.now()).split('.')
    date_string = cd[0]
    sql.execute(f"INSERT INTO punishments_{chat_id} VALUES (?, ?)", (user_id, date_string)) # Обновлено
    database.commit()

async def get_sliv(user_id=int, chat_id=int):
    sql.execute(f"SELECT date FROM punishments_{chat_id}  WHERE user_id = ? ORDER BY date DESC LIMIT 3", (user_id,))
    fetch = sql.fetchall()
    
    if len(fetch) < 3:
        return False # Обновлено

    list_messages = []
    for i in fetch:
        list_messages.append(datetime.fromisoformat(i[0]))

    if list_messages[0] - list_messages[2] < timedelta(seconds=6):
        return True
    else:
        return False

async def staff_zov(chat_id=int):
    sql.execute(f"SELECT user_id FROM permissions_{chat_id}")
    sql.execute(f"SELECT user_id FROM permissions_{chat_id}")
    fetch = sql.fetchall() # Обновлено
    staff_zov_str = []
    for i in fetch:
        staff_zov_str.append(f"[id{i[0]}|⚜️]")

    return ''.join(staff_zov_str)

async def get_global_ban(user_id: int):
    sql.execute("SELECT ban_type, moder, reason, date FROM global_bans WHERE user_id = ?", (user_id,))
    res = sql.fetchone() # Обновлено
    if not res: return None
    return {'type': res[0], 'moder': res[1], 'reason': res[2], 'date': res[3]}

async def get_chat_type(chat_id: int):
    sql.execute("SELECT chat_type FROM chats WHERE chat_id = ?", (chat_id,))
    res = sql.fetchone()
    return res[0] if res else 'def'

async def delete_message(group_id=int, peer_id=int, cmid=int):
    try: await bot.api.messages.delete(group_id=group_id, peer_id=peer_id, delete_for_all=True, cmids=cmid) # Обновлено
    except: pass

async def set_onwer(user=int, chat=int):
    sql.execute("UPDATE chats SET owner_id = ? WHERE chat_id = ?", (user, chat))
    database.commit()

# --- CLAN SYSTEM CONFIG & HELPERS ---
CLAN_LEVELS = {
    1: {"title": "Базовый", "exp": 0, "max_m": 5, "price": 0, "mats": 0, "bonus": 0, "storage": 50000},
    2: {"title": "Начинающий", "exp": 500, "max_m": 10, "price": 120000, "mats": 5000, "bonus": 2, "storage": 100000},
    3: {"title": "Продвинутый", "exp": 2000, "max_m": 15, "price": 400000, "mats": 20000, "bonus": 5, "storage": 250000},
    4: {"title": "Элитный", "exp": 5000, "max_m": 20, "price": 1200000, "mats": 60000, "bonus": 7, "storage": 500000},
    5: {"title": "Легендарный", "exp": 15000, "max_m": 25, "price": 4000000, "mats": 150000, "bonus": 10, "storage": 1000000},
    6: {"title": "Магистры", "exp": 40000, "max_m": 30, "price": 8000000, "mats": 400000, "bonus": 12, "storage": 2500000},
    7: {"title": "Повелители", "exp": 100000, "max_m": 35, "price": 20000000, "mats": 1000000, "bonus": 15, "storage": 5000000},
    8: {"title": "Завоеватели", "exp": 250000, "max_m": 40, "price": 40000000, "mats": 2500000, "bonus": 17, "storage": 10000000},
    9: {"title": "Титаны", "exp": 500000, "max_m": 45, "price": 80000000, "mats": 6000000, "bonus": 20, "storage": 25000000},
    10: {"title": "Божества", "exp": 1000000, "max_m": 50, "price": 200000000, "mats": 15000000, "bonus": 25, "storage": 50000000}
}

# --- CLAN RAID BOSSES CONFIG ---
BOSSES = {
    1: {"name": "👺 Гоблин-Мародер", "hp": 5000, "cost_money": 50000, "cost_mats": 2000, "reward_money": 150000, "reward_exp": 1000, "time": 1800, "summon_cd": 3600},
    2: {"name": "🗿 Каменный Голем", "hp": 25000, "cost_money": 300000, "cost_mats": 15000, "reward_money": 800000, "reward_exp": 5000, "time": 3600, "summon_cd": 7200},
    3: {"name": "🐲 Древний Дракон", "hp": 100000, "cost_money": 1500000, "cost_mats": 75000, "reward_money": 5000000, "reward_exp": 25000, "time": 10800, "summon_cd": 14400}
}

# --- PETS SYSTEM CONFIG ---
PETS = {
    1: {"name": "Собака", "cost": 50000, "emoji": "🐶", "desc": "Бонус к зарплате", "base_salary_bonus": 1, "base_mats_bonus": 0, "base_clan_exp_bonus": 0},
    2: {"name": "Кот", "cost": 150000, "emoji": "🐱", "desc": "Бонус к материалам", "base_salary_bonus": 0, "base_mats_bonus": 2, "base_clan_exp_bonus": 0},
    3: {"name": "Хомяк", "cost": 500000, "emoji": "🐹", "desc": "Бонус к опыту клана", "base_salary_bonus": 0, "base_mats_bonus": 0, "base_clan_exp_bonus": 1},
    4: {"name": "Дракон", "cost": 2000000, "emoji": "🐲", "desc": "Бонус к ЗП и мат.", "base_salary_bonus": 2, "base_mats_bonus": 2, "base_clan_exp_bonus": 0}
}
PET_BONUS_PER_LEVEL = 0.2 # Каждый уровень добавляет 0.2% к соответствующим бонусам.

# --- CLAN TACTICS CONFIG ---
CLAN_TACTICS = {
    "aggression": {"name": "⚔ Агрессия", "desc": "+1 очко к атаке в войне\n📉 -20% к добыче ресурсов", "duration": 3600, "cost": 0},
    "industry": {"name": "⛏ Промышленность", "desc": "+15% к добыче ресурсов\n📉 -1 очко атаки (мин. 1)", "duration": 3600, "cost": 0},
    "training": {"name": "🎓 Обучение", "desc": "+10 EXP клана за каждый майнинг\n📉 -10% к добыче ресурсов", "duration": 3600, "cost": 0}
}

STATION_ROUTES = {
    1: {"name": "Легкий", "profit": 400000, "risk": 10},
    2: {"name": "Средний", "profit": 500000, "risk": 20},
    3: {"name": "Сложный", "profit": 600000, "risk": 25}
}

# --- JOBS SYSTEM CONFIG ---
JOBS = {
    0: {"name": "Безработный", "cost": 0, "min_pay": 1000, "max_pay": 2000, "cooldown": 15},
    1: {"name": "Дворник", "cost": 20000, "min_pay": 3500, "max_pay": 6000, "cooldown": 30},
    2: {"name": "Таксист", "cost": 100000, "min_pay": 10000, "max_pay": 18000, "cooldown": 45},
    3: {"name": "Менеджер", "cost": 300000, "min_pay": 25000, "max_pay": 45000, "cooldown": 60},
    4: {"name": "Бизнесмен", "cost": 1000000, "min_pay": 70000, "max_pay": 120000, "cooldown": 90},
    5: {"name": "Олигарх", "cost": 5000000, "min_pay": 250000, "max_pay": 500000, "cooldown": 180},
    6: {"name": "Машинист Поезда", "cost": 2500000, "min_pay": 150000, "max_pay": 200000, "cooldown": 100}
}

async def get_job_cooldown(user_id, job_id, ud_eco):
    job_data = JOBS.get(job_id, JOBS[0])
    cooldown_min = job_data['cooldown']
    v_lvl = ud_eco.get('vip_level', 0)
    t_lvl = await get_tester_role(user_id)
    
    if v_lvl in VIP_CONFIG:
        cooldown_min /= VIP_CONFIG[v_lvl]['work_div']
    elif ud_eco.get('vip'): # Совместимость со старым флагом VIP
        cooldown_min /= 2
        
    return int(cooldown_min * 60)

# --- CASINO LOGIC ---
CASINO_SYMBOLS = ['7️⃣', '💎', '🔔', '💰', '🍉', '🍇', '🍋', '🍒']
CASINO_WEIGHTS = [3, 4, 5, 6, 15, 15, 15, 15]

def get_casino_result(bet, vip_level, forced_rate=None):
    r1 = random.choices(CASINO_SYMBOLS, weights=CASINO_WEIGHTS)[0]
    r2 = random.choices(CASINO_SYMBOLS, weights=CASINO_WEIGHTS)[0]
    r3 = random.choices(CASINO_SYMBOLS, weights=CASINO_WEIGHTS)[0]
    
    mult = 0
    if r1 == r2 == r3:
        if r1 == '7️⃣': mult = 100
        elif r1 == '💎': mult = 30
        elif r1 == '🔔': mult = 15
        elif r1 == '💰': mult = 10
        else: mult = 5
    elif r1 == r2 or r2 == r3 or r1 == r3:
        mult = 2
    
    if forced_rate is not None:
        comm_rate = forced_rate
    elif vip_level in VIP_CONFIG:
        comm_rate = VIP_CONFIG[vip_level]['comm']
    else:
        comm_rate = 0.10
        
    raw_win = int(bet * mult)
    commission = int(raw_win * comm_rate)
    win = raw_win - commission
    
    return [r1, r2, r3], win, mult, commission, comm_rate

async def get_pet_data(user_id):
    sql.execute("SELECT pet_id, name, level, exp, hunger, energy, last_update FROM pets WHERE user_id = ?", (user_id,))
    res = sql.fetchone()
    if not res: return None
    
    pet_id, name, lvl, exp, hunger, energy, last_upd = res
    now = int(time.time())
    if last_upd == 0: 
        last_upd = now
        sql.execute("UPDATE pets SET last_update = ? WHERE user_id = ?", (now, user_id)); database.commit()
    
    diff = now - last_upd
    hours = diff // 3600
    if hours > 0:
        decay = hours * 5
        hunger = max(0, hunger - decay)
        energy = max(0, energy - decay)
        sql.execute("UPDATE pets SET hunger = ?, energy = ?, last_update = ? WHERE user_id = ?", (hunger, energy, now, user_id)); database.commit()
    return {"id": pet_id, "name": name, "lvl": lvl, "exp": exp, "hunger": hunger, "energy": energy}

async def get_pet_bonus(user_id):
    p = await get_pet_data(user_id)
    if not p or p['hunger'] < 20 or p['energy'] < 10: return {"salary": 0, "mats": 0, "clan_exp": 0}
    
    pid = p['id']
    pet_level = p['lvl']
    pet_config = PETS.get(pid)

    if not pet_config:
        return {"salary": 0, "mats": 0, "clan_exp": 0}

    # Рассчитать базовые бонусы
    salary_bonus = pet_config.get("base_salary_bonus", 0)
    mats_bonus = pet_config.get("base_mats_bonus", 0)
    clan_exp_bonus = pet_config.get("base_clan_exp_bonus", 0)

    # Примените бонус, зависящий от уровня питомца (только если уровень питомца > 1).
    if pet_level > 1:
        level_additive_bonus = (pet_level - 1) * PET_BONUS_PER_LEVEL
        if salary_bonus > 0:
            salary_bonus += level_additive_bonus
        if mats_bonus > 0:
            mats_bonus += level_additive_bonus
        if clan_exp_bonus > 0:
            clan_exp_bonus += level_additive_bonus
        
    bonuses = {"salary": salary_bonus, "mats": mats_bonus, "clan_exp": clan_exp_bonus}
    return bonuses

async def get_clan_online_count(clan_id: int) -> int:
    sql.execute("SELECT user_id FROM user_data WHERE clan_id = ?", (clan_id,))
    res = sql.fetchall()
    if not res: return 0
    uids = [r[0] for r in res]
    try:
        users = await bot.api.users.get(user_ids=uids, fields=['online'])
        return sum(1 for u in users if u.online)
    except: return 0

async def calculate_biz_profit(biz_id):
    sql.execute("SELECT type, profit_per_hour, active_route, repair_until, special_order_active FROM businesses WHERE id = ?", (biz_id,))
    b_type, base_profit, route_id, repair_until, special = sql.fetchone()
    now = int(time.time())
    if b_type != 'station': return base_profit, False # Upgraded profit for regular businesses
    if now < repair_until: return max(0, base_profit - 50000), True
    route = STATION_ROUTES.get(route_id, STATION_ROUTES[1])
    risk = 35 if special else route['risk']
    failed = random.randint(1, 100) <= risk
    return (800000 if special else route['profit']), failed


async def normalize_business_profit_by_price(bid):
    sql.execute("SELECT price, profit_per_hour, level, type FROM businesses WHERE id = ?", (bid,))
    row = sql.fetchone()
    if not row:
        return
    price, profit_per_hour, level, b_type = row
    if b_type == 'station' or level != 1:
        return
    expected = get_business_price_based_profit(price)
    if profit_per_hour != expected:
        sql.execute("UPDATE businesses SET profit_per_hour = ? WHERE id = ?", (expected, bid))
        database.commit()


def get_business_profit_rate(price):
    if price >= 500_000_000:
        return 0.028
    if price > 200_000_000:
        return 0.04
    return 0.05


def get_business_daily_tax(price):
    return int(price * 0.03)


def get_business_price_based_profit(price):
    return int(price * get_business_profit_rate(price))


async def get_business_entity_count(owner_id, clan_owner):
    if owner_id and owner_id > 0:
        sql.execute("SELECT COUNT(*) FROM businesses WHERE owner_id = ?", (owner_id,))
    elif clan_owner and clan_owner > 0:
        sql.execute("SELECT COUNT(*) FROM businesses WHERE clan_owner_id = ?", (clan_owner,))
    else:
        return 0
    result = sql.fetchone()
    return result[0] if result else 0


async def get_business_income_tax_rate(owner_id, clan_owner):
    count = await get_business_entity_count(owner_id, clan_owner)
    return 0.05 if count > 2 else 0.03


async def check_business_tax_status(bid):
    sql.execute("SELECT owner_id, clan_owner_id, tax_due_at, price FROM businesses WHERE id = ?", (bid,))
    row = sql.fetchone()
    if not row:
        return True, None
    owner_id, clan_owner, tax_due_at, price = row
    now = int(time.time())
    if tax_due_at == 0 and (owner_id > 0 or clan_owner > 0):
        tax_due_at = now + 86400
        sql.execute("UPDATE businesses SET tax_due_at = ? WHERE id = ?", (tax_due_at, bid))
        database.commit()
        return True, None

    if tax_due_at == 0:
        return True, None

    if now > tax_due_at + 86400:
        # Автоматический старт аукциона: создаём запись в auctions и снимаем владельца
        duration_minutes = 60
        end_time = now + duration_minutes * 60
        seller = owner_id if owner_id and owner_id > 0 else 0
        # Убираем владельца у бизнеса
        sql.execute("UPDATE businesses SET owner_id = 0, clan_owner_id = 0, tax_due_at = 0 WHERE id = ?", (bid,))
        # Создаём аукцион (min_bid = цена бизнеса)
        sql.execute("SELECT price FROM businesses WHERE id = ?", (bid,))
        p_row = sql.fetchone()
        min_bid = p_row[0] if p_row else 0
        sql.execute("INSERT INTO auctions (biz_id, seller_id, start_time, end_time, min_bid, status) VALUES (?, ?, ?, ?, ?, 'active')", (bid, seller, now, end_time, min_bid))
        database.commit()
        return False, "auctioned"
    if now > tax_due_at:
        return False, "overdue"
    return True, None


async def pay_business_tax(bid, payer_id, clan_owner):
    sql.execute("SELECT price, tax_due_at FROM businesses WHERE id = ?", (bid,))
    row = sql.fetchone()
    if not row:
        return False, "Бизнес не найден."
    price, tax_due_at = row
    amount = get_business_daily_tax(price)
    if clan_owner and clan_owner > 0:
        sql.execute("SELECT money FROM clans WHERE clan_id = ?", (clan_owner,))
        clan_money = sql.fetchone()
        if not clan_money or clan_money[0] < amount:
            return False, f"В казне клана недостаточно средств ({amount:,}$).".replace(",", ".")
        sql.execute("UPDATE clans SET money = money - ? WHERE clan_id = ?", (amount, clan_owner))
    else:
        if not await subtract_balance(payer_id, amount):
            return False, f"Недостаточно средств для оплаты налога: {amount:,}$".replace(",", ".")
    next_due = int(time.time()) + 86400
    sql.execute("UPDATE businesses SET tax_due_at = ? WHERE id = ?", (next_due, bid))
    database.commit()
    return True, f"Налог оплачен: {amount:,}$. Следующий платёж через 24 часа.".replace(",", ".")

CLAN_WAR_COST_MONEY = 50000
CLAN_WAR_COST_MATS = 500
CLAN_WAR_COST_EXP = 50

async def save_clan_to_json(clan_id):
    if not clan_id: return
    try:
        if os.path.exists("clans.json"):
            try:
                with open("clans.json", "r", encoding="utf-8") as f:
                    root_data = json.load(f)
            except: root_data = {}
        else:
            root_data = {}

        if "clans" in root_data and isinstance(root_data["clans"], dict):
            target_dict = root_data["clans"]
        else:
            target_dict = root_data

        sql.execute("SELECT name, tag, level, exp, money, mats, max_mats, owner_id, type FROM clans WHERE clan_id = ?", (clan_id,))
        clan = sql.fetchone()
        if not clan:
            if str(clan_id) in target_dict:
                del target_dict[str(clan_id)]
                with open("clans.json", "w", encoding="utf-8") as f:
                    json.dump(root_data, f, indent=4, ensure_ascii=False)
            return

        name, tag, level, exp, money, mats, max_mats, owner_id, c_type = clan
        
        sql.execute("SELECT user_id, clan_rank FROM user_data WHERE clan_id = ?", (clan_id,))
        members = sql.fetchall()
        
        uids = [m[0] for m in members]
        if owner_id not in uids: uids.append(owner_id)
        
        user_map = {}
        if uids:
            try:
                users_info = await bot.api.users.get(user_ids=uids) # Обновлено
                user_map = {u.id: f"{u.first_name} {u.last_name}" for u in users_info}
            except: pass
            
        members_formatted = []
        for m in members:
            uid, rank = m
            u_name = user_map.get(uid, f"@id{uid}")
            custom_rank = await get_custom_rank(clan_id, rank)
            members_formatted.append(f"• {u_name} — {custom_rank}")
            
        clan_level_info = CLAN_LEVELS.get(level, CLAN_LEVELS[1])
        creator_name = user_map.get(owner_id, f"@id{owner_id}")
        type_str = "Открытый" if c_type == 'open' else "Закрытый"

        target_dict[str(clan_id)] = {"👑 Титул": clan_level_info['title'],"🏰 Клан": f"{name} [{tag}] (ID: {clan_id})","🔒 Тип": type_str,"👥 Участники": f"{len(members)}/{clan_level_info['max_m']}","📦 Склад": {"💰 Деньги": f"{money:,}$".replace(",", "."),"⛏ Материалы": f"{mats:,}/{max_mats:,}".replace(",", "."),"✨ EXP": f"{exp:,}".replace(",", ".")},"📊 Статистика клана": {"👤 Создатель": creator_name},"👥 Состав клана": members_formatted}

        with open("clans.json", "w", encoding="utf-8") as f:
            json.dump(root_data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving clan {clan_id}: {e}") # Обновлено

async def get_custom_rank(clan_id, rank_str):
    # Mapping: Rank Name -> Storage Column Index
    # 0:Участник(r0), 1:Боец(r4), 2:Модератор(r1), 3:Старейшина(r5), 4:Заместитель(r2), 5:Лидер(r3)
    ranks_map = {"Лидер": 3, "Заместитель": 2, "Старейшина": 5, "Модератор": 1, "Боец": 4, "Участник": 0}
    r_num = ranks_map.get(rank_str, 0)
    sql.execute(f"SELECT r{r_num}_name FROM clans WHERE clan_id = ?", (clan_id,))
    res = sql.fetchone()
    return res[0] if res and res[0] else rank_str

async def check_clan_perms(user_id, req_lvl):
    ud = await get_user_data(user_id)
    rank = ud.get('clan_rank', 'Участник')
    levels = { # Обновлено
        "Лидер": 5, "Заместитель": 4, "Старейшина": 3, 
        "Модератор": 2, "Боец": 1, "Участник": 0
    }
    return levels.get(rank, 0) >= req_lvl

async def finish_war(war_id, chat_id=None):
    sql.execute("SELECT attacker_id, defender_id, attacker_score, defender_score, target_biz_id FROM clan_wars WHERE war_id = ?", (war_id,))
    war = sql.fetchone() # Обновлено
    if not war: return
    att_id, def_id, att_score, def_score, target_biz_id = war
    
    sql.execute("UPDATE clan_wars SET status = 'ended' WHERE war_id = ?", (war_id,))
    
    winner_id = None
    loser_id = None
    if att_score > def_score: winner_id = att_id
    elif def_score > att_score: winner_id = def_id
    
    if att_score > def_score: loser_id = def_id
    elif def_score > att_score: loser_id = att_id

    wager_money = CLAN_WAR_COST_MONEY
    wager_mats = CLAN_WAR_COST_MATS
    wager_exp = CLAN_WAR_COST_EXP
    
    if winner_id:
        biz_msg = "" # Обновлено
        if target_biz_id > 0 and winner_id == att_id:
            sql.execute("UPDATE businesses SET clan_owner_id = ?, owner_id = 0 WHERE id = ?", (winner_id, target_biz_id))
            sql.execute("SELECT name FROM businesses WHERE id = ?", (target_biz_id,))
            biz_msg = f"\n🏢 Клан захватил контроль над бизнесом «{sql.fetchone()[0]}»!"

        reward_money = wager_money * 2
        reward_mats = wager_mats * 2
        reward_exp = wager_exp * 2
        
        sql.execute("UPDATE clans SET exp = exp + ?, money = money + ?, mats = mats + ?, wins = wins + 1 WHERE clan_id = ?", (reward_exp, reward_money, reward_mats, winner_id))
        sql.execute("SELECT owner_id, name FROM clans WHERE clan_id = ?", (winner_id,))
        win_info = sql.fetchone()
        # Обновлено
        msg = (f"🏆 Ваш клан «{win_info[1] if win_info else 'Unknown'}» победил в войне #{war_id}!\n"
               f"🆚 Итоговый счет: {max(att_score, def_score)}:{min(att_score, def_score)}\n\n"
               f"💰 Выигрыш (возврат ставки + куш): {reward_money:,}$\n"
               f"⛏ Материалы: {reward_mats:,}\n"
               f"✨ Опыт: {reward_exp:,}\n{biz_msg}\n"
               f"💀 С проигравших списана ставка в полном объеме.")

        if win_info:
            try:
                await bot.api.messages.send(
                    user_id=win_info[0], 
                    message=msg, 
                    random_id=0
                )
            except: pass
        
        if chat_id:
            try: await bot.api.messages.send(peer_id=2000000000+chat_id, message=msg, random_id=0)
            except: pass # Обновлено
    else:
        # Draw - return wagers
        sql.execute("UPDATE clans SET exp = exp + ?, money = money + ?, mats = mats + ? WHERE clan_id IN (?, ?)", (wager_exp, wager_money, wager_mats, att_id, def_id))
        msg = f"🤝 Война #{war_id} завершилась ничьей!\n↩️ Ставки возвращены обоим кланам."
        if chat_id:
            try: await bot.api.messages.send(peer_id=2000000000+chat_id, message=msg, random_id=0)
            except: pass
            # Обновлено
    database.commit()

async def check_war_status(clan_id, chat_id=None):
    sql.execute("SELECT war_id, attacker_id, defender_id, end_time FROM clan_wars WHERE (attacker_id = ? OR defender_id = ?) AND status = 'active'", (clan_id, clan_id))
    war = sql.fetchone()
    if war and time.time() > war[3]:
        await finish_war(war[0], chat_id)
        return None
    return war

async def check_daily_quest_progress(clan_id, amount, q_type="mine"): # Обновлено
    today = datetime.now().strftime("%Y-%m-%d")
    sql.execute("SELECT target, progress, reward_mats, reward_exp, status FROM clan_quests WHERE clan_id = ? AND date = ? AND quest_type = ?", (clan_id, today, q_type))
    quest = sql.fetchone()
    
    if quest and quest[4] == 'active':
        target, progress, r_mats, r_exp, status = quest
        new_progress = progress + amount
        
        if new_progress >= target:
            sql.execute("UPDATE clan_quests SET progress = ?, status = 'completed' WHERE clan_id = ? AND date = ? AND quest_type = ?", (new_progress, clan_id, today, q_type))
            sql.execute("UPDATE clans SET mats = mats + ?, exp = exp + ? WHERE clan_id = ?", (r_mats, r_exp, clan_id))
            return True, r_mats, r_exp
        else:
            sql.execute("UPDATE clan_quests SET progress = ? WHERE clan_id = ? AND date = ? AND quest_type = ?", (new_progress, clan_id, today, q_type))
    return False, 0, 0

async def get_daily_quest_info(clan_id): # Обновлено
    today = datetime.now().strftime("%Y-%m-%d")
    sql.execute("SELECT quest_type, target, progress, reward_mats, reward_exp, status FROM clan_quests WHERE clan_id = ? AND date = ?", (clan_id, today))
    quest = sql.fetchone()
    
    if not quest:
        quest_types = [
            {"type": "mine", "target_min": 3000, "target_max": 8000},
            {"type": "deposit", "target_min": 1000000, "target_max": 5000000},
            {"type": "war_points", "target_min": 20, "target_max": 50},
            {"type": "deposit", "target_min": 500000, "target_max": 2000000},
            {"type": "economic_boom", "target_min": 250000, "target_max": 750000},
            {"type": "duel_wins", "target_min": 5, "target_max": 15},
            {"type": "work_shifts", "target_min": 10, "target_max": 30},
        ]
        selected_quest = random.choice(quest_types)

        q_type = selected_quest["type"]
        target = random.randint(selected_quest["target_min"], selected_quest["target_max"])
        r_mats = random.randint(1000, 2500)
        r_exp = random.randint(200, 500)
        
        if q_type == "deposit":
            r_mats = int(r_mats * 1.5)
            r_exp = int(r_exp * 1.2)
        elif q_type == "war_points":
            r_mats = int(r_mats * 1.2)
            r_exp = int(r_exp * 1.5)
        elif q_type == "economic_boom":
            r_mats = int(r_mats * 1.3)
            r_exp = int(r_exp * 1.3)
        elif q_type == "duel_wins":
            r_mats = int(r_mats * 1.1)
            r_exp = int(r_exp * 1.8)
        elif q_type == "work_shifts":
            r_mats = int(r_mats * 1.2)
            r_exp = int(r_exp * 1.2)

        sql.execute("INSERT INTO clan_quests (clan_id, quest_type, target, reward_mats, reward_exp, date) VALUES (?, ?, ?, ?, ?, ?)", (clan_id, q_type, target, r_mats, r_exp, today))
        database.commit()
        return q_type, target, 0, r_mats, r_exp, "active"
    
    return quest
async def get_clan_max_members(clan_id):
    sql.execute("SELECT level FROM clans WHERE clan_id = ?", (clan_id,))
    level = sql.fetchone()[0]
    return CLAN_LEVELS.get(level, CLAN_LEVELS[1])['max_m']

async def get_clan_header_text(clan_id, user_id):
    sql.execute("SELECT name, tag, level, type FROM clans WHERE clan_id = ?", (clan_id,)) # Обновлено
    res = sql.fetchone()
    if not res: return ""
    name, tag, level, c_type = res
    type_str = "Открытый" if c_type == 'open' else "Закрытый"
    
    clan_level_info = CLAN_LEVELS.get(level, CLAN_LEVELS[1])
    sql.execute("SELECT count(*) FROM user_data WHERE clan_id = ?", (clan_id,))
    members_count = sql.fetchone()[0]
    
    ud = await get_user_data(user_id)
    rank_name = await get_custom_rank(clan_id, ud['clan_rank'])
    
    bonus_percent = clan_level_info.get('bonus', 0)
    bonus_line = f"⛏ Бонус к добыче: +{bonus_percent}%\n" if bonus_percent > 0 else ""

    return (f"👑 Титул: {clan_level_info['title']}\n🏰 Клан: {name} [{tag}] (ID: {clan_id})\n🔒 Тип: {type_str}\n👥 Участники: {members_count}/{clan_level_info['max_m']}\n⭐ Ваше звание: {rank_name}\n{bonus_line}\n")

async def get_clan_menu_data(user_id, chat_id):
    ud = await get_user_data(user_id)
    clan_id = ud.get('clan_id', 0) # Обновлено
    if not clan_id: return None, None
    
    sql.execute("SELECT name, tag, level, exp, money, mats, max_mats, owner_id, tactic, tactic_end FROM clans WHERE clan_id = ?", (clan_id,))
    c = sql.fetchone()
    if not c: return None, None

    # Check wars
    await check_war_status(clan_id, chat_id) # Проверить срок действия
    sql.execute("SELECT war_id, attacker_id, defender_id, attacker_score, defender_score, end_time, status FROM clan_wars WHERE (attacker_id = ? OR defender_id = ?) AND status IN ('active', 'pending')", (clan_id, clan_id))
    war = sql.fetchone()
    
    war_text = ""
    if war:
        if war[6] == 'active':
            score = f"{war[3]}:{war[4]}"
            rem_time = int((war[5] - time.time()) / 60)
            war_text = f"\n⚔ Война (ID: {war[0]})! Счет: {score} | ⏳ {rem_time} мин"
        elif war[6] == 'pending' and war[2] == clan_id:
            war_text = f"\n📩 Входящий вызов на войну (ID: {war[0]})!"
        elif war[6] == 'pending' and war[1] == clan_id:
            war_text = f"\n⏳ Ожидание ответа на вызов (ID: {war[0]})..."

    # Daily Quest
    q_type, q_target, q_progress, q_r_mats, q_r_exp, q_status = await get_daily_quest_info(clan_id)
    quest_text = ""
    
    quest_descriptions = {
        "mine": f"Добыть {q_target:,} мат.",
        "deposit": f"Внести в казну {q_target:,}$",
        "war_points": f"Набрать {q_target:,} очков в войнах",
        "economic_boom": f"Заработать на работах {q_target:,}$",
        "duel_wins": f"Выиграть {q_target:,} дуэлей",
        "work_shifts": f"Отработать {q_target} смен"
    }

    if q_status == "active":
        pct = int((q_progress / q_target) * 100) if q_target > 0 else 0
        quest_desc = quest_descriptions.get(q_type, "Неизвестное задание").replace(",",".")
        quest_text = f"\n📜 Задание: {quest_desc}\n📊 Прогресс: {q_progress:,}/{q_target:,} ({pct}%)\n🎁 Награда: {q_r_mats:,} мат. | {q_r_exp:,} exp"
    else:
        quest_text = f"\n📜 Ежедневное задание выполнено! ✅"

    # Tactic Info (обновлено)
    tactic_slug, tactic_end = c[8], c[9]
    tactic_text = ""
    if tactic_slug and tactic_slug != 'none':
        if time.time() > tactic_end:
            sql.execute("UPDATE clans SET tactic = 'none' WHERE clan_id = ?", (clan_id,)) # Auto-expire
        else:
            t_info = CLAN_TACTICS.get(tactic_slug, {})
            rem_min = int((tactic_end - time.time()) / 60)
            tactic_text = f"\n🚩 Тактика: {t_info.get('name', tactic_slug)} ({rem_min} мин)"

    try: owner_name = await get_user_name(c[7], chat_id)
    except: owner_name = "Неизвестно"

    clan_level_info = CLAN_LEVELS.get(c[2], CLAN_LEVELS[1]) # Обновлено
    max_mats = c[6] if c[6] > 0 else clan_level_info.get('storage', 200000)

    # Calculate online members
    online_count = await get_clan_online_count(clan_id)

    header = await get_clan_header_text(clan_id, user_id)
    text = (f"{header}"
            f"🟢 Онлайн: {online_count}\n"
            f"📦 Склад:\n"
            f"💰 Деньги: {c[4]:,}$\n"
            f"⛏ Материалы: {c[5]:,}/{max_mats:,}\n"
            f"✨ EXP: {c[3]:,}\n\n"
            f"📊 Статистика клана:\n"
            f"👤 Создатель: [id{c[7]}|{owner_name}]"
            f"{quest_text}{war_text}{tactic_text}".replace(",", "."))

    kb = Keyboard(inline=True).add(Callback("👥 Состав", {"command":"clan_members", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY)
    kb.add(Callback("⛏ Добыча", {"command":"clan_mine", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.SECONDARY).row()
    if war:
        if war[6] == 'pending' and war[2] == clan_id and await check_clan_perms(user_id, 4):
            kb.add(Callback("✅ Принять", {"command":"clan_war_accept", "war_id": war[0], "chatId": chat_id, "user": user_id, "public": True}), color=KeyboardButtonColor.POSITIVE).add(Callback("❌ Отказ", {"command":"clan_war_decline", "war_id": war[0], "chatId": chat_id, "user": user_id, "public": True}), color=KeyboardButtonColor.NEGATIVE).row()
        elif war[6] == 'active':
             kb.add(Callback("⚔️ Атаковать", {"command":"clan_attack", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.NEGATIVE).row()
    
    if await check_clan_perms(user_id, 4):
        kb.add(Callback("⚙️ Управление", {"command": "clan_manage_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE).row()
    return text, kb
 
async def get_clan_menu_info_by_id(clan_id, viewer_id, chat_id):
    """Показывает информацию о конкретном клане для любого зрителя."""
    if not clan_id: return None, None
    
    sql.execute("SELECT name, tag, level, exp, money, mats, max_mats, owner_id, tactic, tactic_end FROM clans WHERE clan_id = ?", (clan_id,))
    c = sql.fetchone()
    if not c: return None, None

    clan_level_info = CLAN_LEVELS.get(c[2], CLAN_LEVELS[1])
    max_mats = c[6] if c[6] > 0 else clan_level_info.get('storage', 200000)
    online_count = await get_clan_online_count(clan_id)
    
    try: owner_name = await get_user_name(c[7], chat_id)
    except: owner_name = "Неизвестно"

    text = (f"🏰 Информация о клане: {c[0]} [{c[1]}]\n"
            f"👑 Титул: {clan_level_info['title']}\n"
            f"👥 Участники: {online_count} онлайн\n"
            f"📦 Склад:\n"
            f"💰 Деньги: {c[4]:,}$\n"
            f"⛏ Материалы: {c[5]:,}/{max_mats:,}\n"
            f"✨ EXP: {c[3]:,}\n\n"
            f"👤 Создатель: [id{c[7]}|{owner_name}]").replace(",", ".")

    kb = Keyboard(inline=True)
    kb.add(Callback("👥 Состав", {"command":"clan_members", "chatId": chat_id, "user": viewer_id, "public": True}), color=KeyboardButtonColor.PRIMARY)
    kb.add(Callback("❌ Закрыть", {"command": "delete_msg"}), color=KeyboardButtonColor.NEGATIVE)
    
    return text, kb

async def prune_old_messages(days_to_keep=2, run_vacuum=False):
    """Deletes messages older than a specified number of days from all message tables."""
    try:
        print(f"[PRUNING] Starting cleanup of messages older than {days_to_keep} days...")
        sql.execute("SELECT chat_id FROM chats")
        all_chats = sql.fetchall()
        
        cutoff_timestamp = time.time() - (days_to_keep * 86400)
        
        total_deleted = 0
        for (chat_id,) in all_chats:
            try:
                cur = database.cursor()
                cur.execute(f"DELETE FROM messages_{chat_id} WHERE date < ?", (cutoff_timestamp,))
                deleted_in_chat = cur.rowcount
                if deleted_in_chat > 0: # Обновлено
                    total_deleted += deleted_in_chat
                    database.commit()
            except sqlite3.OperationalError as e:
                if "no such table" in str(e):
                    pass
                else:
                    print(f"[PRUNING] Error processing chat {chat_id}: {e}")

        if total_deleted > 0:
            print(f"[PRUNING] Cleanup finished. Deleted {total_deleted} old messages.")

        if run_vacuum:
            print("[PRUNING] Starting nightly database vacuum...")
            try:
                # Сжимаем базу данных, чтобы реально освободить место на диске
                database.commit()
                old_isolation = database.isolation_level
                database.isolation_level = None # Включаем autocommit для VACUUM
                database.execute("VACUUM")
                database.isolation_level = old_isolation
                print("[PRUNING] Database vacuumed and optimized.")
            except Exception as ve:
                print(f"[PRUNING] Vacuum error: {ve}")

        return total_deleted

    except Exception as e: # Обновлено
        print(f"[PRUNING] An unexpected error occurred during message pruning: {e}")
        return 0

async def pruning_loop():
    """Background task that runs data pruning periodically."""
    last_vacuum_date = None
    while True:
        now = datetime.now()
        # Проверяем, наступила ли ночь (3-4 часа утра) и не выполняли ли мы вакуум сегодня
        should_vacuum = False
        if 3 <= now.hour <= 4 and now.date() != last_vacuum_date:
            should_vacuum = True
            last_vacuum_date = now.date()

        # Очистка старых сообщений происходит каждые 2 часа, вакуум — только по флагу
        await prune_old_messages(days_to_keep=2, run_vacuum=should_vacuum)
        await asyncio.sleep(7200) # Спим 2 часа

@bot.on.chat_message(rules.ChatActionRule("chat_kick_user"))
async def user_leave(message: Message) -> None:
    user_id = message.from_id
    chat_id = message.chat_id
    if not await check_chat(chat_id): return True # Обновлено
    if not message.action.member_id == message.from_id: return True
    user_link = await get_user_link(user_id)
    if await leave_kick(chat_id):
        try: await bot.api.messages.remove_chat_user(chat_id, user_id)
        except: pass
        await message.answer(f"{user_link} вышел(-а) из беседы", disable_mentions=1)
    else:
        keyboard = (
            Keyboard(inline=True)
            .add(Callback("Исключить", {"command": "kick", "user": user_id, "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
        )
        await message.answer(f"{user_link} вышел(-а) из беседы", disable_mentions=1, keyboard=keyboard)

@bot.on.chat_message(rules.ChatActionRule("chat_invite_user_by_link"))
async def user_joined_link(message: Message) -> None:
    user_id = message.from_id # Обновлено
    chat_id = message.chat_id
    if not await check_chat(chat_id): return True

    # Проверка на ЧС бота
    sql.execute("SELECT reason FROM blacklist WHERE user_id = ?", (user_id,))
    blacklist_reason = sql.fetchone()
    if blacklist_reason:
        user_link = await get_user_link(user_id) # Обновлено
        try: await bot.api.messages.remove_chat_user(chat_id, user_id)
        except: pass
        await message.answer(f"Пользователь {user_link} находится в черном списке бота и не может присоединиться.\nПричина: {blacklist_reason[0]}", disable_mentions=1)
        return True

    gb = await get_global_ban(user_id)
    if gb:
        chat_type = await get_chat_type(chat_id)
        if gb['type'] == 'all' or (gb['type'] == 'pl' and chat_type == 'pl'):
            try: await bot.api.messages.remove_chat_user(chat_id, user_id)
            except: pass
            # Обновлено
            user_link = await get_user_link(user_id)
            ban_type_text = "общей блокировке" if gb['type'] == 'all' else "блокировке игровых бесед"
            global_text = "глобально" if gb['type'] == 'all' else "PL"
            
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Снять глобальный бан", {"command": "ungban", "user": user_id, "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
            )
            await message.answer(
                f"{user_link}, находится в {ban_type_text}!\n"
                f"Информация о блокировке:\n"
                f"{await get_user_link(gb['moder'])} (Модератор) | {gb['reason']} | {gb['date']} МСК (UTC+3)", disable_mentions=1, keyboard=keyboard
            )
            return True

    checkban_str = await checkban(user_id, chat_id)
    if checkban_str: # Обновлено
        try: await bot.api.messages.remove_chat_user(chat_id, user_id)
        except: pass
        user_link = await get_user_link(user_id)
        keyboard = (
            Keyboard(inline=True)
            .add(Callback("Снять бан", {"command": "unban", "user": user_id, "chatId": chat_id}),color=KeyboardButtonColor.POSITIVE)
        )
        await message.answer(f"{user_link}, находится в блокировке беседы!\nИнформация о блокировке:\n{await get_user_link(checkban_str['moder'])} (Модератор) | {checkban_str['reason']} | {checkban_str['date']} МСК (UTC+3)",disable_mentions=1, keyboard=keyboard)
        return True

    welcome = await get_welcome(chat_id)
    if welcome: # Обновлено
        invited_name = await get_first_name_safe(user_id)
        welcome = welcome.replace('%u', f'@id{user_id}')
        welcome = welcome.replace('%n', f'@id{user_id} ({invited_name})')
        welcome = welcome.replace('%i', f'@id{user_id}')
        welcome = welcome.replace('%p', f'@id{user_id} ({invited_name})')
        await message.answer(welcome)

@bot.on.chat_message(rules.ChatActionRule("chat_invite_user"))
async def user_joined(message: Message) -> None:
    invited_user = message.action.member_id
    user_id = message.from_id
    chat_id = message.chat_id
    if not await check_chat(chat_id): return True

    # Проверка на ЧС бота
    sql.execute("SELECT reason FROM blacklist WHERE user_id = ?", (invited_user,))
    blacklist_reason = sql.fetchone()
    if blacklist_reason:
        user_link = await get_user_link(invited_user) # Обновлено
        try: await bot.api.messages.remove_chat_user(chat_id, invited_user)
        except: pass
        await message.answer(f"Пользователь {user_link} находится в черном списке бота и не может присоединиться.\nПричина: {blacklist_reason[0]}", disable_mentions=1)
        return True

    gb = await get_global_ban(invited_user)
    if gb:
        chat_type = await get_chat_type(chat_id)
        if gb['type'] == 'all' or (gb['type'] == 'pl' and chat_type == 'pl'):
            try: await bot.api.messages.remove_chat_user(chat_id, invited_user)
            except: pass
            # Обновлено
            target_link = await get_user_link(invited_user)
            ban_type_text = "общей блокировке" if gb['type'] == 'all' else "блокировке игровых бесед"
            global_text = "глобально" if gb['type'] == 'all' else "PL"
            
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Снять глобальный бан", {"command": "ungban", "user": invited_user, "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
            )
            await message.answer(
                f"{target_link}, находится в {ban_type_text}!\n"
                f"Информация о блокировке:\n"
                f"{await get_user_link(gb['moder'])} (Модератор) | {gb['reason']} | {gb['date']} МСК (UTC+3)",
                disable_mentions=1, keyboard=keyboard
            )
            return True

    if invited_user == -224437676:
        await message.answer("Бот успешно добавлен в беседу!\nДля его активации, выдайте боту звезду в беседе и напишите /start!") # Обновлено
    elif user_id == invited_user:
        gb = await get_global_ban(invited_user)
        if gb:
            chat_type = await get_chat_type(chat_id)
            if gb['type'] == 'all' or (gb['type'] == 'pl' and chat_type == 'pl'):
                    try: await bot.api.messages.remove_chat_user(chat_id, invited_user)
                    except: pass
                    target_link = await get_user_link(invited_user)
                    keyboard = (
                        Keyboard(inline=True)
                        .add(Callback("Снять глобальный бан", {"command": "ungban", "user": invited_user, "chatId": chat_id}),color=KeyboardButtonColor.POSITIVE)
                    )
                    await message.answer(
                            f"{target_link} заблокирован(-а) глобально!\n\n"
                            f"Информация:\n{await get_user_link(gb['moder'])} (Модератор) | {gb['reason']} | {gb['date']}",
                        disable_mentions=1, keyboard=keyboard
                    )
                    return True

        checkban_str = await checkban(invited_user, chat_id)
        if checkban_str:
            try: await bot.api.messages.remove_chat_user(chat_id, invited_user)
            except: pass
            target_link = await get_user_link(invited_user)
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Снять бан", {"command": "unban", "user": invited_user, "chatId": chat_id}),color=KeyboardButtonColor.POSITIVE)
            )
            await message.answer(f"{target_link}, находится в блокировке беседы!\nИнформация о блокировке:\n{await get_user_link(checkban_str['moder'])} (Модератор) | {checkban_str['reason']} | {checkban_str['date']} МСК (UTC+3)",disable_mentions=1, keyboard=keyboard)
            return True

        welcome = await get_welcome(chat_id) # Обновлено
        if welcome:
            invited_name = await get_first_name_safe(invited_user)
            inviter_name = await get_first_name_safe(user_id)
            welcome = welcome.replace('%u', f'@id{invited_user}')
            welcome = welcome.replace('%n', f'@id{invited_user} ({invited_name})')
            welcome = welcome.replace('%i', f'@id{user_id}')
            welcome = welcome.replace('%p', f'@id{user_id} ({inviter_name})')
            await message.answer(welcome)
    else:
        if await get_role(user_id, chat_id) < 1 and await invite_kick(chat_id): # Обновлено
            try: await bot.api.messages.remove_chat_user(chat_id, invited_user)
            except: pass
            return True

        gb = await get_global_ban(invited_user)
        if gb:
            chat_type = await get_chat_type(chat_id)
            if gb['type'] == 'all' or (gb['type'] == 'pl' and chat_type == 'pl'):
                    try: await bot.api.messages.remove_chat_user(chat_id, invited_user)
                    except: pass
                    target_link = await get_user_link(invited_user)
                    keyboard = (
                        Keyboard(inline=True)
                        .add(Callback("Снять глобальный бан", {"command": "ungban", "user": invited_user, "chatId": chat_id}),
                             color=KeyboardButtonColor.POSITIVE)
                    )
                    await message.answer(
                        f"{target_link} заблокирован(-а) глобально!\n\n"
                        f"Информация:\n{await get_user_link(gb['moder'])} (Модератор) | {gb['reason']} | {gb['date']}",
                        disable_mentions=1, keyboard=keyboard
                    )
                    return True

        checkban_str = await checkban(invited_user, chat_id)
        if checkban_str:
            try: await bot.api.messages.remove_chat_user(chat_id, invited_user)
            except: pass
            target_link = await get_user_link(invited_user)
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Снять бан", {"command": "unban", "user": invited_user, "chatId": chat_id}),
                     color=KeyboardButtonColor.POSITIVE)
                .add(Callback("Снять бан", {"command": "unban", "user": invited_user, "chatId": chat_id}),color=KeyboardButtonColor.POSITIVE)
            )
            await message.answer(f"{target_link}, находится в блокировке беседы!\nИнформация о блокировке:\n{await get_user_link(checkban_str['moder'])} (Модератор) | {checkban_str['reason']} | {checkban_str['date']} МСК (UTC+3)", disable_mentions=1, keyboard=keyboard)
            await message.answer(f"{target_link} заблокирован(-а) в этой беседе!\n\nИнформация о блокировке:\n{await get_user_link(checkban_str['moder'])} (Модератор) | {checkban_str['reason']} | {checkban_str['date']}", disable_mentions=1, keyboard=keyboard)
            return True

        welcome = await get_welcome(chat_id)
        if welcome: # Обновлено
            invited_name = await get_first_name_safe(invited_user)
            inviter_name = await get_first_name_safe(user_id)
            welcome = welcome.replace('%u', f'@id{invited_user}')
            welcome = welcome.replace('%n', f'@id{invited_user} ({invited_name})')
            welcome = welcome.replace('%i', f'@id{user_id}')
            welcome = welcome.replace('%p', f'@id{user_id} ({inviter_name})')
            await message.answer(welcome)

@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=GroupTypes.MessageEvent)
async def main_event_handlers(message: GroupTypes.MessageEvent): # Обновлено
    payload = message.object.payload or {}
    if message.object.event_id != "0" and message.object.event_id in processed_event_ids:
        return True
    if message.object.event_id != "0":
        processed_event_ids.append(message.object.event_id)
    event_acknowledged = False # Flag to track if an event answer has been sent

    # Helper to send event answer and set flag
    async def send_event_answer_safe(event_data=None, snackbar_text=None):
        nonlocal event_acknowledged
        if event_acknowledged:
            return True
        event_acknowledged = True
        try:
            params = {
                "event_id": message.object.event_id,
                "peer_id": message.object.peer_id,
                "user_id": message.object.user_id,
            }
            if snackbar_text:
                params["event_data"] = json.dumps({"type": "show_snackbar", "text": snackbar_text})
            elif event_data:
                params["event_data"] = json.dumps(event_data)
            try:
                await bot.api.messages.send_message_event_answer(**params)
            except VKAPIError as e:
                if e.code != 100: print(f"Error answering event: {e}")
            return True
        except Exception as e:
            print(f"Error answering event: {e}")
            return False
    command = str(payload.get("command")).lower()
    user_id = message.object.user_id
    chat_id = payload.get("chatId")

    if chat_id and await get_mute(user_id, chat_id):
        await send_event_answer_safe(snackbar_text="❌ Вы не можете использовать команды, так как у вас мут!")
        return True

    # Универсальная проверка владельца меню (кто вызвал команду)
    # Проверяем все возможные ключи, в которых может храниться ID инициатора
    expected_owner = payload.get("initiator") or payload.get("target") or payload.get("user") or payload.get("sender_id")
    is_public = payload.get("public") or command in GLOBAL_PUBLIC_COMMANDS

    if expected_owner and expected_owner != user_id and not is_public:
        # Разрешаем персоналу использовать кнопки из STAFF_COMMANDS даже если они не владельцы меню
        is_staff = await get_role(user_id, chat_id) >= 1
        if not (is_staff and command in STAFF_COMMANDS):
            await send_event_answer_safe(snackbar_text="⛔ Это меню вызвал другой пользователь!")
            return True

    if command == "delete_msg":
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id) # Обновлено
        return True

    if command == "cancel":
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id) # Обновлено
        return True

    if command == "biz_page":
        await send_event_answer_safe()
        page = int(payload.get("page", 1))
        per_page = 6
        
        sql.execute("SELECT COUNT(*) FROM businesses")
        total_count = sql.fetchone()[0]
        total_pages = (total_count + per_page - 1) // per_page
        if page > total_pages: page = total_pages
        if page < 1: page = 1
        
        offset = (page - 1) * per_page
        sql.execute("SELECT id FROM businesses LIMIT ? OFFSET ?", (per_page, offset))
        res = sql.fetchall()
        
        msg = f"🏢 Список предприятий | Страница {page}/{total_pages}\n📝 Нажмите на ID бизнеса для просмотра информации.\n\n"
        kb = Keyboard(inline=True)
        
        for i, b in enumerate(res):
            kb.add(Callback(f"🆔 {b[0]}", {"command": "biz_info_btn", "biz_id": b[0], "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY)
            if (i + 1) % 3 == 0: kb.row()
        
        if res and len(res) % 3 != 0: kb.row()
        
        if total_pages > 1:
            if page > 1:
                kb.add(Callback("⏪", {"command": "biz_page", "page": page - 1, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY)
            kb.add(Callback(f"📄 {page}/{total_pages}", {"command": "none"}), color=KeyboardButtonColor.SECONDARY)
            if page < total_pages:
                kb.add(Callback("⏩", {"command": "biz_page", "page": page + 1, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY)
            kb.row()
            
        kb.add(Callback("❌ Закрыть", {"command": "delete_msg", "initiator": user_id}), color=KeyboardButtonColor.SECONDARY)
        
        try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        except: pass
        return True

    if command == "ticket_reply":
        tid = payload.get("id")
        sql.execute("SELECT user_id FROM support_tickets WHERE id = ?", (tid,))
        res = sql.fetchone()
        if not res: # Обновлено
            await send_event_answer_safe(snackbar_text="Тикет не найден!")
            return True
            
        user_states[user_id] = {
            "action": "reply_ticket", 
            "target_user": res[0], 
            "tid": tid,
            "source_peer": message.object.peer_id,
            "source_cmid": message.object.conversation_message_id
        }
        await send_event_answer_safe(snackbar_text="Введите ответ в чат")
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"✍️ Введите текст ответа для тикета #{tid}:",
            random_id=0
        )
        return True

    if command == "ticket_consider":
        tid = payload.get("id")
        sql.execute("SELECT user_id, type FROM support_tickets WHERE id = ?", (tid,)) # Обновлено
        res = sql.fetchone()
        if not res: return
        
        sql.execute("UPDATE support_tickets SET status = 'в рассмотрении' WHERE id = ?", (tid,))
        database.commit()
        
        type_text = "предложение" if res[1] == "offer" else "жалобу"
        try:
            await bot.api.messages.send(
                user_id=res[0],
                message=f"⏳ Ваша {type_text} #{tid} принята на рассмотрение администрацией.",
                random_id=0
            )
        except: pass
        
        await send_event_answer_safe(snackbar_text=f"Тикет #{tid} переведен в рассмотрение")
        
        # Update current message
        try:
            resp = await bot.api.messages.get_by_conversation_message_id(peer_id=message.object.peer_id, conversation_message_ids=[message.object.conversation_message_id])
            if resp.items:
                new_text = resp.items[0].text.replace("⏳ Статус: Ожидание", "⏳ Статус: На рассмотрении")
                # Пересоздаем клавиатуру, чтобы кнопки не исчезли после редактирования текста
                kb = (
                    Keyboard(inline=True)
                    .add(Callback("✉️ Ответить", {"command": "ticket_reply", "id": tid}), color=KeyboardButtonColor.PRIMARY)
                    .add(Callback("❌ Отклонить", {"command": "ticket_reject", "id": tid}), color=KeyboardButtonColor.NEGATIVE)
                )
                await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=new_text, keyboard=kb)
        except: pass
        return True

    if command == "ticket_reject":
        tid = payload.get("id")
        sql.execute("SELECT user_id, type FROM support_tickets WHERE id = ?", (tid,)) # Обновлено
        res = sql.fetchone()
        if not res: return
        
        sql.execute("UPDATE support_tickets SET status = 'отклонено' WHERE id = ?", (tid,))
        database.commit()
        
        type_text = "предложение" if res[1] == "offer" else "жалобу"
        try:
            await bot.api.messages.send(
                user_id=res[0],
                message=f"❌ Ваша {type_text} #{tid} была отклонена администрацией.",
                random_id=0
            )
        except: pass
        
        await send_event_answer_safe(snackbar_text=f"Тикет #{tid} отклонен")
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        return True

    if command == "unwarn_btn":
        await send_event_answer_safe()
        if await get_role(user_id, chat_id) < 1:
            await send_event_answer_safe(snackbar_text="Недостаточно прав!")
            return True
        # Обновлено
        target_user = payload.get("user")
        new_warns = await unwarn(chat_id, target_user)
        moder_link = await get_user_link(user_id)
        target_link = await get_user_link(target_user)

        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"✅ {moder_link} снял(-а) выговор {target_link} ({new_warns}/3).",
            random_id=0,
            disable_mentions=1
        )
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        return True

    if command == "unpred_btn":
        await send_event_answer_safe()
        if await get_role(user_id, chat_id) < 1:
            await send_event_answer_safe(snackbar_text="Недостаточно прав!")
            return True
        target_user = payload.get("user") # Обновлено
        ud = await get_user_data(target_user)
        new_preds = max(0, ud['preds'] - 1)
        await update_user_data(target_user, 'preds', new_preds)
        moder_link = await get_user_link(user_id)
        target_link = await get_user_link(target_user)
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"✅ {moder_link} снял(-а) предупреждение {target_link} ({new_preds}/2).",
            random_id=0,
            disable_mentions=1
        )
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        return True

    if command == "edit_field":
        if await get_role(user_id, chat_id) < 3:
            await send_event_answer_safe(snackbar_text="Недостаточно прав!")
            return True

        # ... (rest of edit_field logic) ... (обновлено)
            
        field = payload.get("field")
        target_user = payload.get("user")
        
        field_names = {
            "age": "Возраст",
            "has_pc": "Доступ к ПК",
            "discord": "Discord",
            "forum": "Forum",
            "points": "Баллы",
            "last_appointment": "Дата повышения"
        }
        # Обновлено
        target_link = await get_user_link(target_user)
        command_map = {
            "age": "setage",
            "has_pc": "setpc",
            "discord": "setdiscord",
            "forum": "setforum",
            "points": "setpoints",
            "last_appointment": "setlast"
        }
        # Обновлено
        target_val = "20" if field == "age" else "1" if field == "has_pc" else "user#1234" if field == "discord" else "link" if field == "forum" else "100" if field == "points" else "2024-01-01"
        
        user_states[user_id] = {
            "field": field,
            "target_user": target_user,
            "chat_id": chat_id
        }
        
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"📝 Введите новое значение для поля «{field_names.get(field)}» для пользователя {await get_user_link(target_user)}.\n\n"
                    f"Чтобы отменить, введите «отмена».",
            random_id=0
        )
        
    if command == "nicksminus": # Обновлено
        if await get_role(user_id, chat_id) < 1:
            await send_event_answer_safe(snackbar_text="Недостаточно прав!")
            return True

        await send_event_answer_safe({}) # Acknowledge success
        page = int(payload.get("page", 1))
        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "nicksMinus", "page": page - 1, "chatId": chat_id}),
                 color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("Без ников", {"command": "nonicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            .add(Callback("⏩", {"command": "nicksPlus", "page": page - 1, "chatId": chat_id}),
                 color=KeyboardButtonColor.POSITIVE)
        )
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id) # Обновлено
    if command == "nicksplus":
        if await get_role(user_id, chat_id) < 1:
            await send_event_answer_safe(snackbar_text="Недостаточно прав!")
            return True
        await send_event_answer_safe({}) # Acknowledge success

        page = int(payload.get("page", 1))

        nicks = await nlist(chat_id, page + 1)
        if len(nicks) < 1:
            await send_event_answer_safe(snackbar_text="Это последняя страница!")
            return True
        # Обновлено
        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "nicksMinus", "page": page+1, "chatId": chat_id}),
                 color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("Без ников", {"command": "nonicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            .add(Callback("⏩", {"command": "nicksPlus", "page": page+1, "chatId": chat_id}),
                 color=KeyboardButtonColor.POSITIVE)
        )
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id) # Обновлено
        nicks_str = '\n'.join(nicks)
        await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"Пользователи с ником [{page + 1} страница]:\n{nicks_str}\n\nПользователи без ников: /nonick", disable_mentions=1, random_id=0, keyboard=keyboard)

    if command == "nonicks":
        if await get_role(user_id, chat_id) < 1:
            await send_event_answer_safe(snackbar_text="Недостаточно прав!")
            return True
        await send_event_answer_safe({}) # Acknowledge success
        # Обновлено
        nonicks = await nonick(chat_id, 1)
        nonick_list = '\n'.join(nonicks)
        if nonick_list == "": nonick_list = "Пользователи без ников отсутствуют!"

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "nonickMinus", "page": 1, "chatId": chat_id}),
                 color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("С никами", {"command": "nicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            .add(Callback("⏩", {"command": "nonickPlus", "page": 1, "chatId": chat_id}),
                 color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id) # Обновлено
        await bot.api.messages.send(peer_id=2000000000+chat_id, message=f"Пользователи без ников [1]:\n{nonick_list}\n\nПользователи с никами: /nlist", disable_mentions=1, random_id=0 ,keyboard=keyboard)

    if command == "nicks":
        if await get_role(user_id, chat_id) < 1:
            await send_event_answer_safe(snackbar_text="Недостаточно прав!")
            return True
        await send_event_answer_safe({}) # Acknowledge success
        # Обновлено
        nicks = await nlist(chat_id, 1)
        nick_list = '\n'.join(nicks)
        if nick_list == "": nick_list = "Ники отсутствуют!"

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "nicksMinus", "page": 1, "chatId": chat_id}),
                 color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("Без ников", {"command": "nonicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            .add(Callback("⏩", {"command": "nicksPlus", "page": 1, "chatId": chat_id}),
                 color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id) # Обновлено
        await bot.api.messages.send(peer_id=2000000000+chat_id, message=f"Пользователи с ником [1 страница]:\n{nick_list}\n\nПользователи без ников: /nonick",
                            disable_mentions=1, keyboard=keyboard, random_id=0)

    if command == "nonickminus":
        if await get_role(user_id, chat_id) < 1:
            await send_event_answer_safe(snackbar_text="Недостаточно прав!")
            return True

        page = int(payload.get("page", 1)) # Обновлено
        if page <= 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это первая страница!"})
            )
            return True

        nonicks = await nonick(chat_id, 1) # Обновлено
        nonick_list = '\n'.join(nonicks)
        if nonick_list == "": nonick_list = "Пользователи без ников отсутствуют!"

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("⏪", {"command": "nonickMinus", "page": page+1, "chatId": chat_id}),
                 color=KeyboardButtonColor.NEGATIVE)
            .add(Callback("С никами", {"command": "nicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            .add(Callback("⏩", {"command": "nonickPlus", "page": page+1, "chatId": chat_id}),
                 color=KeyboardButtonColor.POSITIVE)
        )

        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id) # Обновлено
        await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"Пользователи без ников [{page-1}]:\n{nonick_list}\n\nПользователи с никами: /nlist", disable_mentions=1, random_id=0, keyboard=keyboard)

    if command == "nonickplus":
        if await get_role(user_id, chat_id) < 1:
            await send_event_answer_safe(snackbar_text="Недостаточно прав!")
            return True

        page = int(payload.get("page", 1)) # Обновлено
        nonicks = await nonick(chat_id, page+1)
        if len(nonicks) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это последняя страница!"})
            )
            return True

        nonicks_str = '\n'.join(nonicks) # Обновлено
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        await bot.api.messages.send(peer_id=2000000000 + chat_id,
                                    message=f"Пользователи без ников [{page + 1}]:\n{nonicks_str}\n\nПользователи с никами: /nlist",
                                    disable_mentions=1, random_id=0, keyboard=keyboard)

    if command == "clear":
        await send_event_answer_safe()
        if await get_role(user_id, chat_id) < 1:
            await send_event_answer_safe(snackbar_text="Недостаточно прав!")
            return True

        user = payload.get("user") # Обновлено
        await clear(user, chat_id, message.group_id, 2000000000+chat_id)
        x = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000+chat_id, conversation_message_ids=message.object.conversation_message_id, group_id=message.group_id)
        x = json.loads(x.json())['items'][0]['text']
        await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=x, conversation_message_id=message.object.conversation_message_id, keyboard=None)
        await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"{await get_user_link(user_id)} очистил(-а) сообщения", disable_mentions=1, random_id=0)
        try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
        except: u_name = f"@id{user}"
        await log_action(user_id, chat_id, f"Очистил сообщения пользователя {u_name}.")

    if command == "unwarn":
        await send_event_answer_safe()
        if await get_role(user_id, chat_id) < 1:
            await send_event_answer_safe(snackbar_text="Недостаточно прав!")
            return True

        user = payload.get("user") # Обновлено
        if await equals_roles(user_id, user, chat_id) < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Вы не можете снять пред данному пользователю!"})
            )
            return True

        await unwarn(chat_id, user) # Обновлено
        x = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000 + chat_id,conversation_message_ids=message.object.conversation_message_id,group_id=message.group_id)
        x = json.loads(x.json())['items'][0]['text']
        try: await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=x, conversation_message_id=message.object.conversation_message_id, keyboard=None)
        except: pass
        await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"{await get_user_link(user_id)} снял(-а) предупреждение {await get_user_link(user)}", disable_mentions=1, random_id=0)

    if command == 'stats':
        await send_event_answer_safe()
        if await get_role(user_id, chat_id) < 1:
            await send_event_answer_safe(snackbar_text="Недостаточно прав!")
            return True

        user = payload.get("user") # Обновлено
        info = await bot.api.users.get(user)
        if info:
            first_name = info[0].first_name
            last_name = info[0].last_name
        else:
            first_name = "Неизвестно"
            last_name = "Неизвестно"

        role = await get_role(user, chat_id)
        t_role = await get_tester_role(user)
        t_names = {1: " (Тестер)", 2: " (Старший тестер)", 3: " (Главный тестер)"}
        tester_status = t_names.get(t_role, "")
        if t_role > 0:
            sql.execute("SELECT handled FROM testers WHERE user_id = ?", (user,))
            h_res = sql.fetchone()
            if h_res: tester_status += f"\n🛠 Исправлено багов: {h_res[0]}"

        warns = await get_warns(user, chat_id)
        if await is_nick(user, chat_id):
            nick = await get_user_name(user, chat_id)
        else:
            nick = "Нет"
        messages = await message_stats(user, chat_id)

        roles = {0: "Пользователь", 1: "Модератор", 2: "Старший Модератор", 3: "Администратор",
                 4: "Старший Администратор", 5: "Владелец беседы", 6: "Разработчик бота"}

        x = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000 + chat_id,
                                                                  conversation_message_ids=message.object.conversation_message_id,
                                                                  group_id=message.group_id)
        x = json.loads(x.json())['items'][0]['text']
        try:
            await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=x,conversation_message_id=message.object.conversation_message_id, keyboard=None)
        except Exception:
            pass
        await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"{await get_user_link(user_id)}, статистика {await get_user_link(user)} (пользователя):\nИмя и фамилия: {first_name} {last_name}\nНик: {nick}\nРоль: {roles.get(role)}{tester_status}\nВсего предупреждений: {warns}/3\nВсего сообщений: {messages['count']}\nПоследнее сообщение: {messages['last']}", disable_mentions=1, random_id=0)
        return True

    if command == "activewarns":
        if await get_role(user_id, chat_id) < 1:
            await send_event_answer_safe(snackbar_text="Недостаточно прав!")
            return True
        await send_event_answer_safe({}) # Acknowledge success


        user = payload.get("user") # Обновлено
        warns = await gwarn(user, chat_id)
        string_info = str
        if not warns: string_info = "Активных предупреждений нет!"
        else: string_info = f"{await get_user_link(warns['moder'])} (Модератор) | {warns['reason']} | {warns['count']}/3 | {warns['time']}"

        keyboard = (
            Keyboard(inline=True)
            .add(Callback("История предупреждений", {"command": "warnhistory", "user": user, "chatId": chat_id}),
                 color=KeyboardButtonColor.PRIMARY)
        )

        x = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000 + chat_id,
                                                                  conversation_message_ids=message.object.conversation_message_id,
                                                                  group_id=message.group_id)
        x = json.loads(x.json())['items'][0]['text']
        try:
            await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=x,
                                        conversation_message_id=message.object.conversation_message_id, keyboard=None)
        except Exception:
            pass
        await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"{await get_user_link(user_id)}, информация о активных предупреждениях {await get_user_link(user)} (пользователя):\n{string_info}", disable_mentions=1, keyboard=keyboard, random_id=0)

    if command == "warnhistory": # Обновлено
        if await get_role(user_id, chat_id) < 1:
            await send_event_answer_safe(snackbar_text="Недостаточно прав!")
            return True
        await send_event_answer_safe({}) # Acknowledge success


        user = payload.get("user") # Обновлено

        warnhistory_mass = await warnhistory(user, chat_id)
        if not warnhistory_mass:wh_string = "Предупреждений не было!"
        else:wh_string = '\n'.join(warnhistory_mass)

        x = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000 + chat_id,
                                                                  conversation_message_ids=message.object.conversation_message_id,
                                                                  group_id=message.group_id)
        x = json.loads(x.json())['items'][0]['text']
        try:
            await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=x,
                                        conversation_message_id=message.object.conversation_message_id, keyboard=None)
        except Exception:
            pass
        await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"Информация о всех предупреждениях {await get_user_link(user)}\nКоличество предупреждений пользователя: {await get_warns(user, chat_id)}\n\nИнформация о последних 10 предупреждений пользователя:\n{wh_string}",disable_mentions=1, random_id=0)
        await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=x,
                                    conversation_message_id=message.object.conversation_message_id, keyboard=None)
        await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"Информация о всех предупреждениях {await get_user_link(user)}\nКоличество предупреждений пользователя: {await get_warns(user, chat_id)}\n\nИнформация о последних 10 предупреждениях пользователя:\n{wh_string}",disable_mentions=1)

    if command == "unmute":
        await send_event_answer_safe()
        if await get_role(user_id, chat_id) < 1:
            await send_event_answer_safe(snackbar_text="Недостаточно прав!")
            return True

        user = payload.get("user") # Обновлено

        if await get_role(user_id, chat_id) <= await get_role(user, chat_id):
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True

        await unmute(user, chat_id) # Обновлено
        try:
            resp = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000 + chat_id,
                                                                  conversation_message_ids=message.object.conversation_message_id,
                                                                  group_id=message.group_id)
            msg_text = resp.items[0].text
            await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=msg_text, conversation_message_id=message.object.conversation_message_id, keyboard=None)
            await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"{await get_user_link(user_id)} размутил(-а) {await get_user_link(user)}", disable_mentions=1, random_id=0)
        except: pass

    if command == "unban":
        await send_event_answer_safe()
        if await get_role(user_id, chat_id) < 2:
            await send_event_answer_safe(snackbar_text="Недостаточно прав!")
            return True

        user = payload.get("user") # Обновлено
        if await equals_roles(user_id, user, chat_id) < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps(
                    {"type": "show_snackbar", "text": "Вы не можете снять бан данному пользователю!"})
            )
            return True

        await unban(user, chat_id) # Обновлено
        try:
            resp = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000 + chat_id,
                                                                  conversation_message_ids=message.object.conversation_message_id,
                                                                  group_id=message.group_id)
            msg_text = resp.items[0].text
            await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=msg_text, conversation_message_id=message.object.conversation_message_id, keyboard=None)
            await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"{await get_user_link(user_id)} разблокировал(-а) {await get_user_link(user)}", disable_mentions=1, random_id=0)
        except: pass

    if command == "kick":
        await send_event_answer_safe()
        if await get_role(user_id, chat_id) < 1:
            await send_event_answer_safe(snackbar_text="Недостаточно прав!")
            return True

        user = payload.get("user") # Обновлено
        if await equals_roles(user_id, user, chat_id) < 2:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps(
                    {"type": "show_snackbar", "text": "Вы не можете кикнуть данного пользователя!"})
            )
            return True

        try: await bot.api.messages.remove_chat_user(chat_id, user) # Обновлено
        except: pass

        try:
            resp = await bot.api.messages.get_by_conversation_message_id(peer_id=2000000000 + chat_id,
                                                                  conversation_message_ids=message.object.conversation_message_id,
                                                                  group_id=message.group_id)
            msg_text = resp.items[0].text
            await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=msg_text, conversation_message_id=message.object.conversation_message_id, keyboard=None)
            await bot.api.messages.send(peer_id=2000000000 + chat_id, message=f"{await get_user_link(user_id)} кикнул(-а) {await get_user_link(user)}", disable_mentions=1, random_id=0)
        except: pass
        reason = "Не указана" # Define reason for logging
        try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
        except: u_name = f"@id{user}"
        await log_action(user_id, chat_id, f"Исключил пользователя {u_name}.\nПричина: {reason if reason else 'Не указана'}")
 
    if command == "alt":
        if await get_role(user_id, chat_id) < 1:
            await send_event_answer_safe(snackbar_text="Недостаточно прав!")
            return True

        commands_levels = {
            1: [
                '\nКоманды модераторов:',
                '/setnick — snick, nick, addnick, ник, сетник, аддник',
                '/removenick —  removenick, clearnick, cnick, рник, удалитьник, снятьник',
                '/getnick — gnick, гник, гетник',
                '/getacc — acc, гетакк, аккаунт, account',
                '/nlist — ники, всеники, nlist, nickslist, nicklist, nicks',
                '/nonick — nonicks, nonicklist, nolist, nnlist, безников, ноникс',
                '/kick — кик, исключить',
                '/warn — пред, варн, pred, предупреждение',
                '/unwarn — унварн, анварн, снятьпред, минуспред',
                '/getwarn — gwarn, getwarns, гетварн, гварн',
                '/warnhistory — historywarns, whistory, историяварнов, историяпредов',
                '/warnlist — warns, wlist, варны, варнлист',
                '/staff — стафф',
                '/reg — registration, regdate, рег, регистрация, датарегистрации',
                '/mute — мут, мьют, муте, addmute',
                '/unmute — снятьмут, анмут, унмут, снятьмут',
                '/alt — альт, альтернативные',
                '/getmute -- gmute, гмут, гетмут, чекмут',
                '/mutelist -- mutes, муты, мутлист',
                '/clear -- чистка, очистить, очистка',
                '/getban -- чекбан, гетбан, checkban',
                '/delete -- удалить',
                '/aban — абан, заморозить',
                '/unaban — упабан, разморозить',
                '/tstats — тестстат',
                '/modstats — мстатс'
            ],
            2: [
                '\nКоманды старших модераторов:',
                '/ban — бан, блокировка',
                '/unban -- унбан, снятьбан',
                '/addmoder -- moder',
                '/removerole -- rrole, снятьроль',
                '/zov - зов, вызов',
                '/online - ozov, озов',
                '/onlinelist - olist, олист',
                '/banlist - bans, банлист, баны',
                '/inactive - ilist, inactive',
                '/masskick - mkick'
            ],
            3: [
                '\nКоманды администраторов:',
                '/quiet -- silence, тишина',
                '/skick -- скик, снят',
                '/sban -- сбан',
                '/sunban — сунбан, санбан',
                '/addsenmoder — senmoder',
                '/rnickall -- allrnick, arnick, mrnick',
                '/sremovenick -- srnick',
                '/szov -- serverzov, сзов',
                '/srole -- prole, pullrole'
            ],
            4: [
                '\nКоманды старших администраторов:',
                '/addadmin -- admin',
                '/pullinfo -- pulli',
                '/banwords -- bws',
                '/filter -- none',
                '/sremoverole -- srrole'
            ],
            5: [
                '\nСписок команд владельца беседы',
                '/antiflood -- af',
                '/welcometext -- welcome, wtext',
                '/invite -- none',
                '/leave -- none',
                '/addsenadmin -- senadm, addsenadm, senadmin',
                '/setpull -- pull',
                '/setowner -- owner',
                '/setleader -- сетлидер',
                '/removeleader -- снятьлидера'
            ]
        }

        user_role = await get_role(user_id, chat_id)

        commands = []
        for i in commands_levels.keys():
            if i <= user_role:
                for b in commands_levels[i]:
                    commands.append(b)

        level_commands = '\n'.join(commands)

        try:
            await bot.api.messages.edit(peer_id=2000000000 + chat_id, message=f"Альтернативные команды\n\n{level_commands}",
                                        conversation_message_id=message.object.conversation_message_id, keyboard=None)
        except Exception:
            pass
    if command == "set_type":
        chat_id = payload.get("chatId")
        chat_type = payload.get("type")
        
        # Проверяем, что это владелец беседы или админ
        members = await bot.api.messages.get_conversation_members(peer_id=message.object.peer_id)
        members = members.model_dump() if hasattr(members, "model_dump") else json.loads(members.json())
        owner_id = None
        is_admin = False
        for item in members['items']:
            if item['member_id'] == -message.group_id:  # Группа
                owner_id = item['member_id']
            elif item['member_id'] == user_id and item.get('is_admin', False):
                is_admin = True
        # Обновлено
        role = await get_role(user_id, chat_id)
        if role < 6 and not is_admin:
            await send_event_answer_safe(snackbar_text="Только разработчик или админ может менять тип!")
            return True
        
        type_names = {
            'def': 'DEF - Общие',
            'ext': 'EXT - Расширенная',
            'pl': 'PL - Беседа игроков',
            'hel': 'HEL - Беседа хеллперов',
            'ld': 'LD - Беседа лидеров',
            'adm': 'ADM - Беседа администраторов',
            'mod': 'MOD - Беседа модераторов',
            'tex': 'TEX - Беседа техов',
            'test': 'TEST - Беседа тестеров',
            'med': 'MED - Беседа медиа-партнёров',
            'ruk': 'RUK - Беседа руководства',
            'users': 'USERS - Беседа пользователей'
        }
        
        type_name = type_names.get(chat_type, chat_type)
        
        try:
            # Сохраняем тип беседы и owner_id (обновлено)
            sql.execute("INSERT OR REPLACE INTO chats (chat_id, peer_id, owner_id, chat_type) VALUES (?, ?, ?, ?)", (chat_id, message.object.peer_id, owner_id, chat_type))
            database.commit()
            
            # Отправляем уведомление
            await send_event_answer_safe(snackbar_text=f"✅ Тип установлен: {type_name}")
            
            # Редактируем сообщение в беседе
            try:
                await bot.api.messages.edit(
                    peer_id=message.object.peer_id,
                    message=f"✅ Тип беседы изменен на: {type_name}",
                    conversation_message_id=message.object.conversation_message_id,
                    keyboard=None
                )
            except Exception as edit_e:
                # Если редактирование не удалось, отправляем новое сообщение
                await bot.api.messages.send(
                    peer_id=message.object.peer_id,
                    message=f"✅ Тип беседы изменен на: {type_name}",
                    random_id=random.randint(1, 1000000)
                )
        except Exception as e:
            logging.error(f"Ошибка при установке типа: {e}")
            await send_event_answer_safe(snackbar_text=f"❌ Ошибка: {e}")

    if command == "type_page":
        await send_event_answer_safe()
        page = int(payload.get("page", 1))
        chat_id = payload.get("chatId")

        if page == 2:
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("MED", {"command": "set_type", "type": "med", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("RUK", {"command": "set_type", "type": "ruk", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("USERS", {"command": "set_type", "type": "users", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .row()
                .add(Callback("<< Назад", {"command": "type_page", "page": 1, "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY)
            )
        else:
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("DEF", {"command": "set_type", "type": "def", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("EXT", {"command": "set_type", "type": "ext", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("PL", {"command": "set_type", "type": "pl", "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
                .row()
                .add(Callback("HEL", {"command": "set_type", "type": "hel", "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
                .add(Callback("LD", {"command": "set_type", "type": "ld", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("ADM", {"command": "set_type", "type": "adm", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .row()
                .add(Callback("MOD", {"command": "set_type", "type": "mod", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("TEX", {"command": "set_type", "type": "tex", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("TEST", {"command": "set_type", "type": "test", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .row()
                .add(Callback("Дальше >>", {"command": "type_page", "page": 2, "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY)
            )

        try:
            await bot.api.messages.edit(
                peer_id=message.object.peer_id,
                message="Выберите тип беседы:",
                conversation_message_id=message.object.conversation_message_id,
                keyboard=keyboard
            )
        except Exception as e:
            # Отправляем новое сообщение, если редактирование не удалось
            await bot.api.messages.send(
                peer_id=message.object.peer_id,
                message="Выберите тип беседы:",
                keyboard=keyboard,
                random_id=random.randint(1, 1000000)
            )

    if command == "set_position":
        await send_event_answer_safe()
        position = payload.get("position")
        target_user = payload.get("user")
        chat_id = payload.get("chatId")
        initiator_id = payload.get("initiator")

        if await get_role(initiator_id, chat_id) < 6:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
            return True
        
        await update_user_data(target_user, 'position', position)
        
        moder_name = await get_user_name(initiator_id, chat_id)
        target_name = await get_user_name(target_user, chat_id)
        
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"✅ Должность выдана!\n"
                    f"От: {moder_name}\n"
                    f"Кому: {target_name}\n"
                    f"📋 Должность: {position}",
            random_id=0,
            disable_mentions=1
        )
        await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
        try: u_info = await bot.api.users.get(user_ids=target_user); u_name = f"[id{target_user}|{u_info[0].first_name} {u_info[0].last_name}]"
        except: u_name = f"@id{target_user}"
        await log_action(initiator_id, chat_id, f"Выдал должность «{position}» пользователю {u_name}.")
        return True

    if command == "pet_sell_confirm":
        p_data = await get_pet_data(user_id)
        if not p_data:
            await send_event_answer_safe(snackbar_text="❌ У вас нет питомца!")
            return True
        
        pid = p_data['id']
        refund = int(PETS[pid]['cost'] * 0.5)
        
        sql.execute("DELETE FROM pets WHERE user_id = ?", (user_id,))
        database.commit()
        await add_balance(user_id, refund)
        
        await send_event_answer_safe(snackbar_text="✅ Питомец продан!")
        try:
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, 
                                        message=f"🦦 Питомец «{p_data['name']}» отправлен в зоопарк. Получено: {refund:,}$.".replace(",", "."), keyboard=None)
        except: pass
        return True

    if command == "remove_referrer":
        ud = await get_user_data(user_id)
        if ud.get('referrer_id', 0) == 0:
            await send_event_answer_safe(snackbar_text="❌ У вас не установлен пригласивший!")
            return True
        
        cancel_count = ud.get('ref_cancel_count', 0)
        if cancel_count >= 3:
            await send_event_answer_safe(snackbar_text="❌ Лимит отмен исчерпан (макс. 3)!")
            return True

        await update_user_data(user_id, 'referrer_id', 0)
        await update_user_data(user_id, 'ref_cancel_count', cancel_count + 1)
        await send_event_answer_safe(snackbar_text=f"✅ Удалено! Попыток: {cancel_count + 1}/3")
        
        try:
            await bot.api.messages.edit(
                peer_id=message.object.peer_id,
                conversation_message_id=message.object.conversation_message_id,
                message=f"✅ Пригласивший успешно удален.\nИспользовано отмен: {cancel_count + 1} из 3.",
                keyboard=None
            )
        except: pass
        return True
    
    # --- CLAN CALLBACKS ---
    if command in ["clan_members", "clan_mine", "clan_upgrade_menu", "upgrade_do", "clan_menu", "clan_war_accept", "clan_war_decline", "clan_attack", "clan_manage_menu", "clan_delete_ask", "clan_delete_yes", "clan_toggle_type", "clan_toggle_treasury", "clan_withdraw_ask", "clan_tactics_menu", "clan_set_tactic", "clan_activity_view"]: # Обновлено
        owner_id = payload.get("user")
        is_public = payload.get("public")
        if owner_id and owner_id != user_id and not is_public:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "⛔ Это меню не для вас!"})
            )
            return True

    if command == "clan_menu": # Обновлено
        await send_event_answer_safe()
        text, kb = await get_clan_menu_data(user_id, chat_id)
        if text:
            try:
                await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=text, keyboard=kb, disable_mentions=1)
            except Exception:
                try:
                    await bot.api.messages.send(peer_id=message.object.peer_id, message=text, keyboard=kb, random_id=0, disable_mentions=1)
                except Exception:
                    pass
        return True

    if command == "clan_attack":
        ud = await get_user_data(user_id) # Обновлено
        clan_id = ud.get('clan_id')
        if not clan_id: return

        last_attack = ud.get('last_clan_attack', 0)
        cooldown = 60 # 1 minute
        if time.time() - last_attack < cooldown:
            rem = int(cooldown - (time.time() - last_attack))
            return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": f"⏳ Атака готова через {rem} сек."})
            )

        war = await check_war_status(clan_id, chat_id)
        if not war: # Обновлено
            return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Ваш клан не в войне!"})
            )

        is_attacker = (war[1] == clan_id); points = random.randint(2, 5)
        sql.execute("SELECT tactic, tactic_end FROM clans WHERE clan_id = ?", (clan_id,))
        tac_res = sql.fetchone()
        if tac_res and tac_res[0] != 'none' and tac_res[1] > time.time():
            if tac_res[0] == 'aggression': points += 1
            elif tac_res[0] == 'industry': points = max(1, points - 1)
            
        col = "attacker_score" if is_attacker else "defender_score"
        sql.execute(f"UPDATE clan_wars SET {col} = {col} + ? WHERE war_id = ?", (points, war[0])) # Обновлено
        
        # Quest progress for war points
        quest_msg = ""
        q_completed_war, qr_mats_war, qr_exp_war = await check_daily_quest_progress(clan_id, points, "war_points")
        if q_completed_war:
            quest_msg = f" | ✅ Квест: +{qr_mats_war:,} м. +{qr_exp_war:,} exp".replace(",",".")

        sql.execute("UPDATE user_data SET last_clan_attack = ?, clan_war_points = COALESCE(clan_war_points, 0) + ? WHERE user_id = ?", (int(time.time()), points, user_id)) # Обновлено
        database.commit()

        await send_event_answer_safe(snackbar_text=f"💥 Атака! +{points} очков.{quest_msg}")
        
        # Notification in chat (обновлено)
        sql.execute("SELECT attacker_score, defender_score, attacker_id, defender_id FROM clan_wars WHERE war_id = ?", (war[0],))
        w_data = sql.fetchone()
        if w_data:
            att_score, def_score, att_id, def_id = w_data
            
            sql.execute("SELECT name FROM clans WHERE clan_id = ?", (att_id,)); att_name = sql.fetchone()[0]
            sql.execute("SELECT name FROM clans WHERE clan_id = ?", (def_id,)); def_name = sql.fetchone()[0]
            user_name = await get_user_name(user_id, chat_id)
            sql.execute("SELECT name FROM clans WHERE clan_id = ?", (clan_id,)); my_clan_name = sql.fetchone()[0]
            
            action = random.choice(["атакует в лоб", "заходит с фланга", "уничтожает отряд противника", "ведет наступление", "сеет хаос в рядах врага"])
            notif = (f"⚔ Внимание! Боец [id{user_id}|{user_name}] из клана «{my_clan_name}» {action}!\n"
                     f"💥 +{points} к счету войны.\n"
                     f"📊 Текущий счёт: {att_name} {att_score} : {def_score} {def_name}") # Обновлено
            try: await bot.api.messages.send(peer_id=message.object.peer_id, message=notif, random_id=0, disable_mentions=1)
            except: pass

        # Update menu
        text, kb = await get_clan_menu_data(user_id, chat_id)
        if text:
            try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=text, keyboard=kb, disable_mentions=1)
            except: pass
        return True

    if command == "clan_activity_view":
        await send_event_answer_safe() # Обновлено
        if not await check_clan_perms(user_id, 4):
             return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
        
        ud = await get_user_data(user_id)
        clan_id = ud.get('clan_id') # Обновлено
        
        sql.execute("SELECT user_id, clan_rank, last_clan_mine, last_clan_attack, clan_mats_mined, clan_war_points FROM user_data WHERE clan_id = ? ORDER BY clan_mats_mined DESC", (clan_id,))
        members = sql.fetchall()
        
        msg = "📊 Активность участников (Всего добыто | Очки войны):\n\n"

        for m in members:
            uid, rank, l_mine, l_att, c_mats, c_war = m # Обновлено
            u_name = await get_user_name(uid, chat_id)
            
            if c_mats is None: c_mats = 0
            if c_war is None: c_war = 0
            
            msg += f"👤 [id{uid}|{u_name}]\n⛏ {c_mats:,} | ⚔ {c_war:,}\n\n".replace(",", ".")
            
        if len(msg) > 4000: msg = msg[:4000] + "\n... (список обрезан)"
        # Обновлено
        kb = Keyboard(inline=True)
        kb.add(Callback("🔄 Обновить", {"command": "clan_activity_view", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE)
        kb.row()
        kb.add(Callback("<< Назад", {"command": "clan_manage_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.SECONDARY)
        
        try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        except: pass
        return True

    if command == "moders_page":
        await send_event_answer_safe()
        if await get_role(user_id, chat_id) < 3: return
        
        # Находим конфиг таблицы для текущего чата
        config = None
        for c in SHEETS_CONFIG:
            if c["sheet_chat_id"] == chat_id or (c.get("apps_chat_id", 0) - 2000000000) == chat_id:
                config = c
                break

        # Если не нашли прямого совпадения, пробуем найти через сервер (pull)
        if not config:
            sql.execute("SELECT in_pull FROM chats WHERE chat_id = ?", (chat_id,))
            res_p = sql.fetchone()
            if res_p and res_p[0] != 0:
                curr_pull = res_p[0]
                for c in SHEETS_CONFIG:
                    sql.execute("SELECT in_pull FROM chats WHERE chat_id = ?", (c["sheet_chat_id"],))
                    res_s = sql.fetchone()
                    if res_s and res_s[0] == curr_pull:
                        config = c
                        break

        if not config or not gs_client:
            await bot.api.messages.send(peer_id=message.object.peer_id, message="❌ Этот чат не привязан к Google Таблице или сервис недоступен.", random_id=0)
            return

        page = int(payload.get("page", 1))
        per_page = 6
        
        # Асинхронное получение данных из таблицы
        loop = asyncio.get_running_loop()
        def _fetch():
            sh = gs_client.open(config["name"])
            ws = sh.worksheet(config["worksheet_name"])
            return ws.get_all_values()
            
        rows = await loop.run_in_executor(None, _fetch)
        
        all_mods = []
        # Пропускаем шапку и ищем модераторов
        for i in range(1, len(rows)):
            row = rows[i]
            if len(row) <= config["vk_id_col"]: continue
            nick = row[config["vk_id_col"]].strip()
            
            # Фильтр служебных слов
            if not nick or nick.lower() in ['вакантно', '-', 'ник', 'игровой ник']: continue
            
            # Сопоставляем ник из таблицы с ID пользователя в этом чате
            sql.execute(f"SELECT user_id FROM nicks_{config['sheet_chat_id']} WHERE nick = ?", (nick,))
            res = sql.fetchone()
            if res:
                all_mods.append((res[0], nick))
        
        if not all_mods:
            await bot.api.messages.send(peer_id=message.object.peer_id, message="⚠️ В таблице не найдено модераторов, чьи ники связаны с базой бота (/setnick).", random_id=0)
            return

        total_pages = (len(all_mods) + per_page - 1) // per_page
        if page > total_pages: page = total_pages
        if page < 1: page = 1
        
        start = (page - 1) * per_page
        current_mods = all_mods[start : start + per_page]
        
        msg = f"🛡 Состав модерации из таблицы «{config['name']}»\nСтр. {page}/{total_pages}:"
        kb = Keyboard(inline=True)
        
        for i, (m_id, m_nick) in enumerate(current_mods):
            kb.add(Callback(f"👤 {m_nick[:15]}", {"command": "manage_moder", "target": m_id, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY)
            if (i + 1) % 2 == 0: kb.row()
            
        if kb.buttons and kb.buttons[-1]: kb.row()
        
        if total_pages > 1:
            if page > 1:
                kb.add(Callback("⏪", {"command": "moders_page", "page": page - 1, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY)
            kb.add(Callback(f"{page}/{total_pages}", {"command": "none"}), color=KeyboardButtonColor.SECONDARY)
            if page < total_pages:
                kb.add(Callback("⏩", {"command": "moders_page", "page": page + 1, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY)
            kb.row()
            
        kb.add(Callback("❌ Закрыть", {"command": "delete_msg", "initiator": user_id}), color=KeyboardButtonColor.SECONDARY)
        
        try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        except: pass
        return True

    if command == "manage_moder":
        await send_event_answer_safe()
        if await get_role(user_id, chat_id) < 3: return
        
        target = payload.get("target")
        ud = await get_user_data(target)
        
        msg = (f"⚙️ Данные модератора {await get_user_link(target)}\n\n"
               f"⚡ Возраст: {ud['age']}\n"
               f"💻 ПК: {'Есть' if ud['has_pc'] else 'Нет'}\n"
               f"📘 Discord: {ud['discord']}\n"
               f"📕 Forum: {ud['forum']}\n"
               f"📋 Должность: {ud.get('position', 'Не указана')}\n"
               f"💲 Баллы: {ud['points']}")

        kb = Keyboard(inline=True)
        kb.add(Callback("⚡ Возраст", {"command": "edit_field", "field": "age", "user": target, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY)
        kb.add(Callback("💻 ПК", {"command": "edit_field", "field": "has_pc", "user": target, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY).row()
        kb.add(Callback("📘 Discord", {"command": "edit_field", "field": "discord", "user": target, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY)
        kb.add(Callback("📕 Forum", {"command": "edit_field", "field": "forum", "user": target, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY).row()
        kb.add(Callback("💲 Баллы", {"command": "edit_field", "field": "points", "user": target, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY)
        kb.add(Callback("⤴️ Повышение", {"command": "edit_field", "field": "last_appointment", "user": target, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY).row()
        
        kb.add(Callback("⏪ К списку", {"command": "moders_page", "page": 1, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.SECONDARY)
        
        try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb, disable_mentions=1)
        except: pass
        return True

    if command == "slots_menu":
        await send_event_answer_safe()
        ud = await get_user_data(user_id)
        purchased = ud.get('biz_slots', 0)
        clan_id = ud.get('clan_id', 0)
        
        sql.execute("SELECT COUNT(*) FROM businesses WHERE owner_id = ?", (user_id,))
        personal_biz = sql.fetchone()[0]

        clan_biz = 0
        if clan_id > 0:
            sql.execute("SELECT COUNT(*) FROM businesses WHERE clan_owner_id = ?", (clan_id,))
            clan_biz = sql.fetchone()[0]
        
        total_slots = 2 + purchased
        next_price = 5000000 + (purchased * 5000000)
        
        msg = (f"📂 Управление слотами бизнеса\n\n"
               f"👤 Личных бизнесов: {personal_biz}/{total_slots}\n"
               f"🏰 Бизнесов клана: {clan_biz}\n"
               f"📊 Всего под управлением: {personal_biz + clan_biz}\n\n"
               f"🆓 Базовые слоты: 2\n"
               f"➕ Куплено слотов: {purchased}\n\n"
               f"🛒 Следующий слот стоит: {next_price:,}$".replace(",", "."))
        
        kb = Keyboard(inline=True)
        kb.add(Callback(f"➕ Купить слот", {"command": "buy_slot", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE)
        kb.row().add(Callback("❌ Закрыть", {"command": "delete_msg", "user": user_id}), color=KeyboardButtonColor.SECONDARY)
        
        try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        except: await bot.api.messages.send(peer_id=message.object.peer_id, message=msg, keyboard=kb, random_id=0)
        return True

    if command == "buy_slot":
        ud = await get_user_data(user_id)
        purchased = ud.get('biz_slots', 0)
        price = 5000000 + (purchased * 5000000)
        
        if not await subtract_balance(user_id, price):
            error_msg = f"❌ Недостаточно средств! Нужно {price:,}$".replace(",", ".")
            await send_event_answer_safe(snackbar_text=error_msg)
            await bot.api.messages.send(peer_id=message.object.peer_id, message=f"⚠️ {await get_user_link(user_id)}, {error_msg}", random_id=0)
            return True
            
        await update_user_data(user_id, 'biz_slots', purchased + 1)
        await send_event_answer_safe(snackbar_text="✅ Слот успешно приобретен!")
        
        # Refresh menu
        new_payload = {"command": "slots_menu", "chatId": chat_id, "user": user_id}
        try: obj_data = message.object.model_dump()
        except: obj_data = message.object.dict().copy()
        obj_data['payload'] = new_payload
        return await main_event_handlers(GroupTypes.MessageEvent(group_id=message.group_id, event_id=message.event_id, object=obj_data))

    if command == "clan_manage_menu":
        if not await check_clan_perms(user_id, 4): # Обновлено
            return await send_event_answer_safe(snackbar_text="Недостаточно прав!")

        await send_event_answer_safe()
        ud = await get_user_data(user_id)
        clan_id = ud.get('clan_id')
        sql.execute("SELECT type, money, mats, treasury FROM clans WHERE clan_id = ?", (clan_id,))
        c_data = sql.fetchone()
        if not c_data: return await send_event_answer_safe(snackbar_text="❌ Ошибка: данные клана не найдены.")
        
        c_type, money, mats, treasury = c_data
        type_str = "🔓 Открытый" if c_type == 'open' else "🔒 Закрытый"
        treasury_str = "🔓 Открыта" if treasury else "🔒 Закрыта"
        
        msg = (f"⚙️ Меню управления кланом\n"
               f"Тип: {type_str}\n"
               f"Казна ({treasury_str}): {money:,}$ | {mats:,} мат.".replace(",", "."))
        
        kb = Keyboard(inline=True)
        kb.add(Callback("🚩 Тактики", {"command": "clan_tactics_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row() # Обновлено
        kb.add(Callback("📊 Активность", {"command": "clan_activity_view", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
        kb.add(Callback("🆙 Улучшить", {"command": "clan_upgrade_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE)
        kb.add(Callback(f"Тип: {type_str}", {"command": "clan_toggle_type", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
        kb.add(Callback("👹 Рейды", {"command": "clan_raid_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.NEGATIVE)
        kb.add(Callback("🏢 Бизнесы", {"command": "clan_biz_list", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
        kb.add(Callback(f"Казна: {treasury_str}", {"command": "clan_toggle_treasury", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY)
        kb.add(Callback("💰 Снять", {"command": "clan_withdraw_ask", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE).row()
        kb.add(Callback("<< Назад", {"command": "clan_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.SECONDARY)
        try:
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        except:
            await bot.api.messages.send(peer_id=message.object.peer_id, message=msg, keyboard=kb, random_id=0)
        return True

    if command == "clan_biz_list":
        await send_event_answer_safe() # Обновлено
        if not await check_clan_perms(user_id, 4): return True
        ud = await get_user_data(user_id); clan_id = ud['clan_id']
        sql.execute("SELECT id, name FROM businesses WHERE clan_owner_id = ?", (clan_id,))
        bizs = sql.fetchall()
        msg = "🏢 Бизнесы клана:\n\n"
        kb = Keyboard(inline=True)
        if bizs:
            for i, b in enumerate(bizs[:9]): # Лимит 9 бизнесов (3 ряда по 3 кнопки + 1 кнопка назад = 10)
                msg += f"• {b[1]} (ID: {b[0]})\n"
                kb.add(Callback(f"⚙ {b[1][:15]}", {"command": "biz_manage", "biz_id": b[0], "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY)
                if (i + 1) % 3 == 0: kb.row()
            if kb.buttons and kb.buttons[-1]: kb.row()
        else:
            msg += "У клана пока нет бизнесов."
        kb.add(Callback("<< Назад", {"command": "clan_manage_menu", "chatId": chat_id, "user": user_id, "initiator": user_id}), color=KeyboardButtonColor.SECONDARY)
        try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        except: await bot.api.messages.send(peer_id=message.object.peer_id, message=msg, keyboard=kb, random_id=0)
        return True

    if command == "clan_raid_menu":
        await send_event_answer_safe() # Обновлено
        if not await check_clan_perms(user_id, 4): return True
        ud = await get_user_data(user_id); clan_id = ud['clan_id']
        sql.execute("SELECT boss_id, current_hp, max_hp, end_time FROM clan_bosses WHERE clan_id = ?", (clan_id,))
        active = sql.fetchone()
        if active and time.time() < active[3]:
            b = BOSSES.get(active[0])
            msg = f"⚔ РЕЙД: {b['name']}\n❤️ HP: {active[1]}/{active[2]}\n⏳ Конец: {int((active[3]-time.time())/60)} мин"
            kb = Keyboard(inline=True).add(Callback("💥 Атаковать", {"command": "clan_boss_attack", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.NEGATIVE)
            kb.row().add(Callback("<< Назад", {"command": "clan_manage_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.SECONDARY)
        else:
            if active: sql.execute("DELETE FROM clan_bosses WHERE clan_id = ?", (clan_id,)); database.commit() # Обновлено
            msg = "👹 Выберите босса для призыва из казны:"
            kb = Keyboard(inline=True)
            now = int(time.time())
            for bid, bd in BOSSES.items():
                sql.execute("SELECT ready_at FROM clan_boss_cooldowns WHERE clan_id = ? AND boss_id = ?", (clan_id, bid))
                cd_res = sql.fetchone()
                if cd_res and now < cd_res[0]:
                    rem_min = (cd_res[0] - now) // 60
                    btn_text = f"⏳ {bd['name']} ({rem_min}м)"
                    kb.add(Callback(btn_text, {"command": "none"}), color=KeyboardButtonColor.SECONDARY).row()
                else:
                    sm = f"{bd['cost_money']/1e6:.1f}M".replace(".0", "") if bd['cost_money'] >= 1e6 else f"{bd['cost_money']//1000}k" if bd['cost_money'] >= 1000 else str(bd['cost_money'])
                    smt = f"{bd['cost_mats']/1e6:.1f}M".replace(".0", "") if bd['cost_mats'] >= 1e6 else f"{bd['cost_mats']//1000}k" if bd['cost_mats'] >= 1000 else str(bd['cost_mats'])
                    btn_text = f"{bd['name']} ({sm}$ | {smt}м)"
                    kb.add(Callback(btn_text, {"command": "clan_boss_summon", "boss_id": bid, "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
            kb.add(Callback("<< Назад", {"command": "clan_manage_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.SECONDARY)
        try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        except: await bot.api.messages.send(peer_id=message.object.peer_id, message=msg, keyboard=kb, random_id=0)
        return True

    if command == "clan_boss_summon":
        if not await check_clan_perms(user_id, 4): return True # Обновлено
        bid = payload.get("boss_id"); b = BOSSES.get(bid)
        ud = await get_user_data(user_id); clan_id = ud['clan_id']
        sql.execute("SELECT ready_at FROM clan_boss_cooldowns WHERE clan_id = ? AND boss_id = ?", (clan_id, bid))
        cd_res = sql.fetchone()
        if cd_res and time.time() < cd_res[0]:
            rem = int((cd_res[0] - time.time()) // 60)
            return await send_event_answer_safe(snackbar_text=f"❌ Босс на перезарядке! Еще {rem} мин.")

        ud = await get_user_data(user_id); clan_id = ud['clan_id']
        sql.execute("SELECT money, mats FROM clans WHERE clan_id = ?", (clan_id,))
        c = sql.fetchone()
        if c[0] < b['cost_money'] or c[1] < b['cost_mats']: return await send_event_answer_safe(snackbar_text="❌ Мало ресурсов в казне!")
        sql.execute("UPDATE clans SET money = money - ?, mats = mats - ? WHERE clan_id = ?", (b['cost_money'], b['cost_mats'], clan_id))
        sql.execute("INSERT OR REPLACE INTO clan_bosses (clan_id, boss_id, current_hp, max_hp, start_time, end_time) VALUES (?, ?, ?, ?, ?, ?)", (clan_id, bid, b['hp'], b['hp'], int(time.time()), int(time.time() + b['time'])))
        database.commit() # Обновлено
        await send_event_answer_safe(snackbar_text=f"👹 {b['name']} призван!")
        return await main_event_handlers(message)

    # Дублирующаяся логика атаки удалена, теперь она корректно обрабатывается в callback_handlers выше.
    if command == "clan_boss_attack":
        return await callback_handlers(message)
 
    if command == "clan_tactics_menu":
        await send_event_answer_safe()
        if not await check_clan_perms(user_id, 4): return # Deputy+
        
        ud = await get_user_data(user_id)
        clan_id = ud.get('clan_id')
        
        sql.execute("SELECT tactic, tactic_end FROM clans WHERE clan_id = ?", (clan_id,)) # Обновлено
        res = sql.fetchone()
        
        current_tactic_slug = 'none'
        tactic_end_ts = 0
        if res:
            current_tactic_slug, tactic_end_ts = res
        
        is_active = current_tactic_slug != 'none' and time.time() < tactic_end_ts

        if is_active:
            rem_min = int((tactic_end_ts - time.time()) / 60)
            active_tactic_name = CLAN_TACTICS.get(current_tactic_slug, {}).get('name', 'Неизвестная')
            msg = (f"🚩 Центр стратегического планирования\n\n"
                   f"Активная тактика: {active_tactic_name}\n"
                   f"⏳ Завершится через: {rem_min} мин.\n\n"
                   f"Сменить тактику можно будет после завершения текущей.")
            kb = Keyboard(inline=True).add(Callback("<< Назад", {"command": "clan_manage_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.SECONDARY)
        else:
            msg = (f"🚩 Центр стратегического планирования\n"
                   f"Активная тактика: Отсутствует\n\n"
                   f"Выберите тактику на следующий час:")
            
            kb = Keyboard(inline=True)
            for slug, info in CLAN_TACTICS.items():
                kb.add(Callback(info['name'], {"command": "clan_set_tactic", "tactic": slug, "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
            kb.add(Callback("<< Назад", {"command": "clan_manage_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.SECONDARY)
        
        await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        return True

    if command == "clan_set_tactic":
        if not await check_clan_perms(user_id, 4): return # Обновлено
        new_tactic = payload.get("tactic")
        t_info = CLAN_TACTICS.get(new_tactic)
        if not t_info: return
        
        ud = await get_user_data(user_id)
        clan_id = ud.get('clan_id')

        # Проверка на активную тактику (обновлено)
        sql.execute("SELECT tactic, tactic_end FROM clans WHERE clan_id = ?", (clan_id,))
        res = sql.fetchone()
        if res and res[0] != 'none' and time.time() < res[1]:
            rem_min = int((res[1] - time.time()) / 60)
            return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": f"❌ Нельзя сменить тактику! Осталось: {rem_min} мин."})
            )
        
        end_time = int(time.time() + t_info['duration'])
        sql.execute("UPDATE clans SET tactic = ?, tactic_end = ? WHERE clan_id = ?", (new_tactic, end_time, clan_id)) # Обновлено
        database.commit()
        
        await bot.api.messages.send_message_event_answer(
            event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
            event_data=json.dumps({"type": "show_snackbar", "text": f"Тактика «{t_info['name']}» активирована!"})
        )
        # Return to menu
        new_payload = {"command": "clan_manage_menu", "chatId": chat_id, "user": user_id}
        try:
            obj_data = message.object.model_dump()
        except AttributeError:
            obj_data = message.object.dict()
        obj_data['payload'] = new_payload
        new_message_event = GroupTypes.MessageEvent(group_id=message.group_id, event_id=message.event_id, object=obj_data)
        return await callback_handlers(new_message_event)
        return True

    if command == "clan_members":
        await send_event_answer_safe() # Обновлено
        ud = await get_user_data(user_id)
        clan_id = ud.get('clan_id')
        if not clan_id:
            return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Вы не в клане!"})
            )
        
        sql.execute("SELECT user_id, clan_rank FROM user_data WHERE clan_id = ? ORDER BY CASE clan_rank WHEN 'Лидер' THEN 1 WHEN 'Заместитель' THEN 2 WHEN 'Старейшина' THEN 3 WHEN 'Модератор' THEN 4 WHEN 'Боец' THEN 5 ELSE 6 END", (clan_id,))
        members = sql.fetchall() # Обновлено

        # Get max members
        max_m = await get_clan_max_members(clan_id)

        member_ids = [m[0] for m in members]
        online_map = {}
        if member_ids:
            try:
                u_infos = await bot.api.users.get(user_ids=member_ids, fields=['online'])
                for u in u_infos:
                    online_map[u.id] = "🟢" if u.online else "🔴" # Обновлено
            except: pass

        await send_event_answer_safe()
        header = await get_clan_header_text(clan_id, user_id)
        msg = "👥 Состав клана:\n"
        for m in members:
            uid = m[0]
            u_name = await get_user_name(uid, chat_id)
            rank_custom = await get_custom_rank(clan_id, m[1]) # Обновлено
            status_emoji = online_map.get(uid, "🔴")
            msg += f"• {status_emoji} [id{uid}|{u_name}] — {rank_custom}\n"
            
        kb = Keyboard(inline=True).add(Callback("<< Назад", {"command": "clan_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.SECONDARY)
        try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=header + msg, keyboard=kb)
        except: pass
        return True

    if command == "clan_mine":
        ud = await get_user_data(user_id) # Обновлено
        clan_id = ud.get('clan_id')
        if not clan_id: return
        
        last_mine = ud.get('last_clan_mine', 0)
        cooldown = 120 # 2 minutes
        if time.time() - last_mine < cooldown:
            rem = int(cooldown - (time.time() - last_mine))
            return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": f"⏳ Подождите {rem} сек."})
            )

        sql.execute("SELECT level, mats, max_mats FROM clans WHERE clan_id = ?", (clan_id,))
        c_res = sql.fetchone() # Обновлено
        if not c_res: return
        level, current_mats, db_max_mats = c_res

        clan_info = CLAN_LEVELS.get(level, CLAN_LEVELS[1])
        max_storage = db_max_mats if db_max_mats > 0 else clan_info.get('storage', 200000)

        if current_mats >= max_storage:
             return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": f"📦 Склад переполнен! ({max_storage:,})".replace(",", ".")})
            )

        bonus_percent = clan_info.get('bonus', 0)
        base_mats = random.randint(300, 900) # Обновлено
        bonus_mats = int(base_mats * (bonus_percent / 100))

        # Добавляем бонус от питомца (Дракон/Кот)
        pet_b = await get_pet_bonus(user_id)
        pet_mats_bonus = int(base_mats * (pet_b.get('mats', 0) / 100))
        
        mats = base_mats + bonus_mats + pet_mats_bonus

        # Tactics Bonus/Malus
        sql.execute("SELECT tactic, tactic_end FROM clans WHERE clan_id = ?", (clan_id,))
        tac_res = sql.fetchone()
        tactic_active = False
        tactic_bonus_text = ""
        if tac_res and tac_res[0] != 'none' and tac_res[1] > time.time():
            tactic_active = True
            if tac_res[0] == 'industry':
                mats = int(mats * 1.15)
                tactic_bonus_text = " [⛏ +15%]"
            elif tac_res[0] == 'aggression':
                mats -= int(base_mats * 0.20) # Штраф применяется к базовой добыче, а не к общей
                tactic_bonus_text = " [⚔ -20%]"
            elif tac_res[0] == 'training':
                mats -= int(base_mats * 0.10) # Штраф применяется к базовой добыче, а не к общей
                tactic_bonus_text = " [🎓 -10%]"
        # Обновлено
        vip_bonus = 0
        vip_bonus_text = ""
        if ud.get('vip', False):
            vip_bonus = int(mats * 0.10)
            mats += vip_bonus

        if current_mats + mats > max_storage: mats = max_storage - current_mats
        
        extra_exp_sql = pet_b.get('clan_exp', 0) # Бонус от Хомяка
        
        if tactic_active and tac_res[0] == 'training':
            extra_exp_sql = 10

        sql.execute("UPDATE clans SET mats = mats + ?, exp = exp + ? WHERE clan_id = ?", (mats, extra_exp_sql, clan_id))
        
        # Quest logic
        quest_msg = "" # Обновлено
        q_completed, qr_mats, qr_exp = await check_daily_quest_progress(clan_id, mats, "mine")
        if q_completed:
            quest_msg += f" | ✅ Квест: +{qr_mats:,} м. +{qr_exp:,} exp".replace(",",".")

        # War logic
        war = await check_war_status(clan_id, chat_id)
        war_msg = ""
        if war: # Обновлено
            is_attacker = (war[1] == clan_id)
            points = random.randint(1, 3)
            
            # War Tactic check (Mining gives very low war points usually, but let's apply small factor if needed or keep raw)
            # Currently tactics affect Attack Action mostly.
            
            col = "attacker_score" if is_attacker else "defender_score"
            sql.execute(f"UPDATE clan_wars SET {col} = {col} + ? WHERE war_id = ?", (points, war[0])) # Обновлено
            sql.execute(f"SELECT {col} FROM clan_wars WHERE war_id = ?", (war[0],))
            new_score = sql.fetchone()[0]
            war_msg = f" | ⚔ +{points} (Всего: {new_score})"
            
            # Quest progress for war points
            q_completed_war, qr_mats_war, qr_exp_war = await check_daily_quest_progress(clan_id, points, "war_points")
            if q_completed_war:
                quest_msg += f" | ✅ Квест (война): +{qr_mats_war:,} м. +{qr_exp_war:,} exp".replace(",",".")
            
        sql.execute("UPDATE user_data SET last_clan_mine = ?, clan_mats_mined = COALESCE(clan_mats_mined, 0) + ? WHERE user_id = ?", (int(time.time()), mats, user_id)) # Обновлено
        database.commit()
        
        bonus_text = f" (+{bonus_percent}%)" if bonus_percent > 0 else ""
        if pet_b.get('mats', 0) > 0: bonus_text += f" [🐶 +{pet_b['mats']}%]"
        if vip_bonus > 0: vip_bonus_text = " [VIP +5%]"
        if extra_exp_sql > 0: bonus_text += f" [✨ +{extra_exp_sql} EXP]"
        
        await send_event_answer_safe(snackbar_text=f"⛏ Добыто {mats} мат.{bonus_text}{tactic_bonus_text}{vip_bonus_text}{war_msg}{quest_msg}")
        # Обновлено
        if war:
            sql.execute("SELECT attacker_score, defender_score, attacker_id, defender_id FROM clan_wars WHERE war_id = ?", (war[0],))
            w_data = sql.fetchone()
            if w_data:
                att_score, def_score, att_id, def_id = w_data
                
                sql.execute("SELECT name FROM clans WHERE clan_id = ?", (att_id,))
                att_name = sql.fetchone()[0]
                sql.execute("SELECT name FROM clans WHERE clan_id = ?", (def_id,))
                def_name = sql.fetchone()[0]
                
                user_name = await get_user_name(user_id, chat_id)
                sql.execute("SELECT name FROM clans WHERE clan_id = ?", (clan_id,))
                my_clan_name = sql.fetchone()[0]
                
                phrases = ["наносит сокрушительный удар", "прорывает оборону", "укрепляет позиции", "совершает тактический маневр", "ведет клан к победе"]
                action = random.choice(phrases)
                # Обновлено
                notif = (f"⚔ Внимание! Боец [id{user_id}|{user_name}] из клана «{my_clan_name}» {action}!\n"
                         f"💥 +{points} к счету войны.\n"
                         f"📊 Текущий счёт: {att_name} {att_score} : {def_score} {def_name}")
                try: await bot.api.messages.send(peer_id=message.object.peer_id, message=notif, random_id=0, disable_mentions=1)
                except: pass

        # Update menu
        text, kb = await get_clan_menu_data(user_id, chat_id)
        if text:
            try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=text, keyboard=kb, disable_mentions=1)
            except: pass
        return True

    if command == "clan_war_accept":
        war_id = payload.get("war_id")
        is_public = payload.get("public", False)
        # Обновлено
        if not await check_clan_perms(user_id, 4): # 4 = Заместитель, 5 = Лидер
            return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав для принятия/отклонения войны!"})
            )

        await send_event_answer_safe()
        ud = await get_user_data(user_id) # Обновлено
        # Double check
        sql.execute("SELECT defender_id FROM clan_wars WHERE war_id = ? AND status = 'pending'", (war_id,))
        res = sql.fetchone()
        
        if not res: return
        clan_id = ud.get('clan_id')

        # Проверяем ресурсы защитника (обновлено)
        sql.execute("SELECT money, mats, exp FROM clans WHERE clan_id = ?", (clan_id,))
        def_res = sql.fetchone()
        if def_res[0] < CLAN_WAR_COST_MONEY or def_res[1] < CLAN_WAR_COST_MATS or def_res[2] < CLAN_WAR_COST_EXP:
             return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "У клана недостаточно ресурсов для войны!"})
            )

        if not res or res[0] != ud.get('clan_id'):
             return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Ошибка!"})
            )
        
        # Списываем ресурсы у обоих кланов (обновлено)
        sql.execute("SELECT attacker_id FROM clan_wars WHERE war_id = ?", (war_id,))
        att_id = sql.fetchone()[0]
        
        sql.execute("SELECT money, mats, exp FROM clans WHERE clan_id = ?", (att_id,))
        att_res = sql.fetchone()
        if att_res[0] < CLAN_WAR_COST_MONEY or att_res[1] < CLAN_WAR_COST_MATS or att_res[2] < CLAN_WAR_COST_EXP:
             return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "У атакующего клана недостаточно ресурсов!"})
            )

        sql.execute("UPDATE clans SET money = money - ?, mats = mats - ?, exp = exp - ? WHERE clan_id IN (?, ?)", (CLAN_WAR_COST_MONEY, CLAN_WAR_COST_MATS, CLAN_WAR_COST_EXP, att_id, clan_id))
        end_time = int(time.time() + 1200) # 20 minutes war
        sql.execute("UPDATE clan_wars SET status = 'active', start_time = ?, end_time = ? WHERE war_id = ?", (int(time.time()), end_time, war_id)) # Обновлено
        database.commit()
        await save_clan_to_json(clan_id)
        await save_clan_to_json(att_id)
        
        sql.execute("SELECT name FROM clans WHERE clan_id = ?", (att_id,))
        att_name = sql.fetchone()[0]
        sql.execute("SELECT name FROM clans WHERE clan_id = ?", (clan_id,))
        def_name = sql.fetchone()[0]
        msg = (f"⚔ Война #{war_id} началась!\n"
               f"🛡 {def_name} принял вызов от ⚔ {att_name}!\n" # Обновлено
               f"⏳ Битва продлится 10 минут.\n"
               f"💰 С обоих кланов списана ставка: {CLAN_WAR_COST_MONEY}$")

        if is_public:
            await delete_message(message.group_id, message.object.peer_id, message.object.conversation_message_id)
            await bot.api.messages.send(peer_id=message.object.peer_id, message=msg, random_id=0)
        else:
            text, kb = await get_clan_menu_data(user_id, chat_id)
            if text:
                try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=text, keyboard=kb, disable_mentions=1)
                except: pass
            
            if chat_id:
                try: await bot.api.messages.send(peer_id=2000000000+chat_id, message=msg, random_id=0)
                except: pass # Обновлено
        return True

    if command == "clan_war_decline":
        await send_event_answer_safe()
        war_id = payload.get("war_id")
        
        if not await check_clan_perms(user_id, 4): # 4 = Заместитель, 5 = Лидер
            return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав для принятия/отклонения войны!"})
            )

        # Simple delete
        sql.execute("DELETE FROM clan_wars WHERE war_id = ? AND status = 'pending'", (war_id,)) # Обновлено
        database.commit()
        text, kb = await get_clan_menu_data(user_id, chat_id)
        if text:
            try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=text, keyboard=kb, disable_mentions=1)
            except: pass
        return True

    if command == "clan_upgrade_menu":
        await send_event_answer_safe() # Обновлено
        ud = await get_user_data(user_id)
        clan_id = ud.get('clan_id')
        sql.execute("SELECT level, money, mats FROM clans WHERE clan_id = ?", (clan_id,))
        res = sql.fetchone()
        if not res: return
        lvl, mon, mat = res
        up = CLAN_LEVELS.get(lvl + 1)
        
        if not up:
            return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "🏆 Максимальный уровень!"})
            )
            
        check = lambda c, n: "✅" if c >= n else "❌"
        header = await get_clan_header_text(clan_id, user_id) # Обновлено
        
        sql.execute("SELECT exp FROM clans WHERE clan_id = ?", (clan_id,))
        exp = sql.fetchone()[0]
        
        cur_stats = CLAN_LEVELS.get(lvl, CLAN_LEVELS[1])

        text = (f"{header}📊 Текущий уровень: {lvl} ({cur_stats['title']})\n"
                f"🆙 Улучшение до уровня {lvl + 1} ({up['title']})\n\n"
                f"🎁 Бонусы:\n"
                f"👥 Места: {cur_stats['max_m']} ➔ {up['max_m']}\n"
                f"📦 Склад: {cur_stats['storage']:,} ➔ {up['storage']:,}\n"
                f"⛏ Бонус: {cur_stats['bonus']}% ➔ {up['bonus']}%\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
                f"📉 Требования:\n"
                f"{check(mon, up['price'])} Деньги: {mon:,}/{up['price']:,}$\n"
                f"{check(mat, up['mats'])} Маты: {mat:,}/{up['mats']:,}\n"
                f"{check(exp, up['exp'])} EXP: {exp:,}/{up['exp']:,}\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬".replace(",", "."))
        kb = Keyboard(inline=True).add(Callback("💎 Подтвердить", {"command": "upgrade_do", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE).row().add(Callback("<< Назад", {"command": "clan_manage_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.SECONDARY) # Обновлено
        await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=text, keyboard=kb)
        return True

    if command == "upgrade_do":
        ud = await get_user_data(user_id)
        clan_id = ud.get('clan_id')
        sql.execute("SELECT level, money, mats FROM clans WHERE clan_id = ?", (clan_id,))
        lvl, mon, mat = sql.fetchone()
        up = CLAN_LEVELS.get(lvl + 1)
        # Обновлено
        # Добавлена проверка EXP
        sql.execute("SELECT exp FROM clans WHERE clan_id = ?", (clan_id,))
        exp = sql.fetchone()[0]
        
        if up and mon >= up['price'] and mat >= up['mats'] and exp >= up['exp']:
            sql.execute("UPDATE clans SET level=level+1, money=money-?, mats=mats-? WHERE clan_id=?", (up['price'], up['mats'], clan_id))
            database.commit()
            await save_clan_to_json(clan_id)
            await log_action(user_id, chat_id, f"Улучшил клан (ID: {clan_id}) до уровня {lvl + 1}.") # Обновлено
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message="🎊 Клан успешно улучшен!", keyboard=None)
            
            sql.execute("SELECT name, level FROM clans WHERE clan_id = ?", (clan_id,))
            c_info = sql.fetchone()
            if c_info:
                c_name, new_level_num = c_info
                new_level_info = CLAN_LEVELS.get(new_level_num)
                if new_level_info:
                    chat_id = payload.get("chatId")
                    if chat_id:
                        try:
                            await bot.api.messages.send(
                                peer_id=2000000000 + chat_id,
                                message=f"🎉 Поздравляем! Клан «{c_name}» достиг нового уровня: {new_level_info['title']}!",
                                random_id=0
                            )
                        except: pass
        else:
             await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "❌ Недостаточно ресурсов!"})
            )
        return True

    if command == "clan_toggle_type":
        if not await check_clan_perms(user_id, 4): return # Обновлено
        ud = await get_user_data(user_id)
        clan_id = ud.get('clan_id')
        
        sql.execute("SELECT type FROM clans WHERE clan_id = ?", (clan_id,))
        c_type = sql.fetchone()[0]
        new_type = 'closed' if c_type == 'open' else 'open'
        
        sql.execute("UPDATE clans SET type = ? WHERE clan_id = ?", (new_type, clan_id))
        database.commit() # Обновлено
        await save_clan_to_json(clan_id)
        
        type_str = "🔓 Открытый" if new_type == "open" else "🔒 Закрытый"
        await send_event_answer_safe(snackbar_text=f"Тип изменен на: {type_str}")
        type_str = "🔓 Открытый" if new_type == "open" else "🔒 Закрытый"
        await send_event_answer_safe(snackbar_text=f"Тип изменен на: {type_str}")
        type_str = "🔓 Открытый" if new_type == "open" else "🔒 Закрытый"
        await send_event_answer_safe(snackbar_text=f"Тип изменен на: {type_str}")
        type_str = "🔓 Открытый" if new_type == "open" else "🔒 Закрытый"
        await send_event_answer_safe(snackbar_text=f"Тип изменен на: {type_str}")
        
        sql.execute("SELECT money, mats, treasury FROM clans WHERE clan_id = ?", (clan_id,))
        money, mats, treasury = sql.fetchone()
        treasury_str = "🔓 Открыта" if treasury else "🔒 Закрыта"
        
        msg = (f"⚙️ Меню управления кланом\n"
               f"Тип: {type_str}\n"
               f"Казна ({treasury_str}): {money:,}$ | {mats:,} мат.".replace(",", "."))
        
        kb = Keyboard(inline=True)
        kb.add(Callback("🚩 Тактики", {"command": "clan_tactics_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
        kb.add(Callback("📊 Активность", {"command": "clan_activity_view", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
        kb.add(Callback("🆙 Улучшить", {"command": "clan_upgrade_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE)
        kb.add(Callback(f"Тип: {type_str}", {"command": "clan_toggle_type", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
        kb.add(Callback("👹 Рейды", {"command": "clan_raid_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.NEGATIVE)
        kb.add(Callback("🏢 Бизнесы", {"command": "clan_biz_list", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
        kb.add(Callback(f"Казна: {treasury_str}", {"command": "clan_toggle_treasury", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY)
        kb.add(Callback("💰 Снять", {"command": "clan_withdraw_ask", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE).row() # Обновлено
        if await check_clan_perms(user_id, 5):
             kb.add(Callback("❌ Удалить клан", {"command": "clan_delete_ask", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.NEGATIVE).row()
        kb.add(Callback("<< Назад", {"command": "clan_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.SECONDARY)
        
        await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        return True

    if command == "clan_toggle_treasury":
        if not await check_clan_perms(user_id, 4): return # Обновлено
        ud = await get_user_data(user_id)
        clan_id = ud.get('clan_id')
        
        sql.execute("SELECT treasury FROM clans WHERE clan_id = ?", (clan_id,))
        c_treasury = sql.fetchone()[0]
        new_treasury = 0 if c_treasury else 1
        
        sql.execute("UPDATE clans SET treasury = ? WHERE clan_id = ?", (new_treasury, clan_id))
        database.commit() # Обновлено
        
        await send_event_answer_safe(snackbar_text=f"Казна {'открыта' if new_treasury else 'закрыта'}!")
        
        # Re-render menu logic
        sql.execute("SELECT type, money, mats FROM clans WHERE clan_id = ?", (clan_id,))
        c_type, money, mats = sql.fetchone()
        type_str = "🔓 Открытый" if c_type == 'open' else "🔒 Закрытый"
        treasury_str = "🔓 Открыта" if new_treasury else "🔒 Закрыта"
        
        msg = (f"⚙️ Меню управления кланом\n"
               f"Тип: {type_str}\n"
               f"Казна ({treasury_str}): {money:,}$ | {mats:,} мат.".replace(",", "."))
        
        kb = Keyboard(inline=True)
        kb.add(Callback("🚩 Тактики", {"command": "clan_tactics_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
        kb.add(Callback("📊 Активность", {"command": "clan_activity_view", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
        kb.add(Callback("🆙 Улучшить", {"command": "clan_upgrade_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE)
        kb.add(Callback(f"Тип: {type_str}", {"command": "clan_toggle_type", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
        kb.add(Callback("👹 Рейды", {"command": "clan_raid_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.NEGATIVE)
        kb.add(Callback("🏢 Бизнесы", {"command": "clan_biz_list", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
        kb.add(Callback(f"Казна: {treasury_str}", {"command": "clan_toggle_treasury", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY)
        kb.add(Callback("💰 Снять", {"command": "clan_withdraw_ask", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE).row() # Обновлено
        if await check_clan_perms(user_id, 5):
             kb.add(Callback("❌ Удалить клан", {"command": "clan_delete_ask", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.NEGATIVE).row()
        kb.add(Callback("<< Назад", {"command": "clan_menu", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.SECONDARY)
        
        await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        return True

    if command == "clan_withdraw_ask":
        if not await check_clan_perms(user_id, 4): # Обновлено
            return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Недостаточно прав!"})
            )
        
        user_states[user_id] = {
            "action": "clan_withdraw",
            "chat_id": chat_id,
            "msg_id": message.object.conversation_message_id # Save to delete if needed
        }
        
        await send_event_answer_safe(snackbar_text="💰 Введите сумму для снятия в чат")
        await bot.api.messages.send(
            peer_id=message.object.peer_id,
            message=f"💰 Введите сумму для снятия из казны:",
            random_id=0
        )
        return True
        await send_event_answer_safe(snackbar_text="💰 Введите сумму для снятия из казны:")

    if command == "clan_create_finish":
        initiator = payload.get("user") # Обновлено
        if initiator and initiator != user_id:
             return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "⛔ Это меню не для вас!"})
            )
        name = payload.get("name")
        c_type = payload.get("type")
        
        sql.execute("INSERT INTO clans (name, owner_id, tag, type) VALUES (?, ?, ?, ?)", (name, user_id, name[:3].upper(), c_type)) # Обновлено
        new_id = sql.lastrowid
        await update_user_data(user_id, 'clan_id', new_id)
        await update_user_data(user_id, 'clan_rank', 'Лидер')
        await save_clan_to_json(new_id)
        await log_action(user_id, chat_id, f"Создал клан «{name}» (ID: {new_id}).")
        await send_event_answer_safe()
        
        type_str = "Открытый" if c_type == 'open' else "Закрытый"
        await bot.api.messages.edit(
            peer_id=message.object.peer_id,
            conversation_message_id=message.object.conversation_message_id,
            message=f"🎊 Клан «{name}» успешно создан!\nТип: {type_str}",
            keyboard=None
        )
        return True

    if command == "clan_pass_confirm":
        new_leader_id = payload.get("new_leader") # Обновлено
        initiator_id = payload.get("user") # Это текущий лидер (user_id)

        if not await check_clan_perms(user_id, 5): 
            await send_event_answer_safe(snackbar_text="Вы больше не лидер!")
            return True

        ud = await get_user_data(user_id)
        clan_id = ud.get('clan_id')
        
        # Меняем владельца в таблице clans
        sql.execute("UPDATE clans SET owner_id = ? WHERE clan_id = ?", (new_leader_id, clan_id)) # Обновлено
        # Понижаем старого лидера до Заместителя
        sql.execute("UPDATE user_data SET clan_rank = 'Заместитель' WHERE user_id = ?", (initiator_id,))
        # Повышаем нового лидера
        sql.execute("UPDATE user_data SET clan_rank = 'Лидер' WHERE user_id = ?", (new_leader_id,))
        database.commit()
        await save_clan_to_json(clan_id)

        new_leader_name = await get_user_name(new_leader_id, chat_id)
        await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, 
                                    message=f"👑 Лидерство клана успешно передано пользователю [id{new_leader_id}|{new_leader_name}]!", keyboard=None)
        await log_action(user_id, chat_id, f"Передал лидерство клана (ID: {clan_id}) пользователю {new_leader_id}.")
        return True

    if command == "clan_accept_invite":
        target = payload.get("target") # Обновлено
        clan_id = payload.get("clan_id")
        
        if user_id != target:
            return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это приглашение не для вас!"})
            )
            
        ud = await get_user_data(user_id)
        if ud.get('clan_id'): # Обновлено
             return await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Вы уже в клане!"})
            )
            
        await update_user_data(user_id, 'clan_id', clan_id)
        await save_clan_to_json(clan_id)
        # Обновлено
        sql.execute("SELECT name FROM clans WHERE clan_id = ?", (clan_id,))
        c_name = sql.fetchone()[0]
        user_link = await get_user_link(user_id)
        
        await bot.api.messages.edit(
            peer_id=message.object.peer_id, 
            conversation_message_id=message.object.conversation_message_id, 
            message=f"✅ {user_link} вступил в клан «{c_name}»!", 
            keyboard=None,
            disable_mentions=1
        )
        return True

    # --- END CLAN CALLBACKS ---
 
    if command == "casino":
        if time.time() - user_casino_cooldown.get(user_id, 0) < 10:
             await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": f"⏳ Подождите {int(10 - (time.time() - user_casino_cooldown[user_id]))} сек."})
            )
             return True

        bet = payload.get("bet") # Обновлено
        original_user = payload.get("user")
        
        if user_id != original_user:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это не ваша ставка!"})
            )
            return True

        ud_eco = await get_user_economy_data(user_id) # Обновлено
        economy = load_economy()
        
        max_bet = economy['settings']['max_bet']
        
        if bet > max_bet:
             await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id,
                peer_id=message.object.peer_id,
                user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": f"Максимальная ставка: {max_bet}!"})
            )
             return True

        if not await subtract_balance(user_id, bet):
            error_msg = "❌ Недостаточно средств для ставки!"
            await send_event_answer_safe(snackbar_text=error_msg)
            await bot.api.messages.send(peer_id=message.object.peer_id, message=f"⚠️ {await get_user_link(user_id)}, {error_msg}", random_id=0)
            return True

        user_casino_cooldown[user_id] = time.time() # Обновлено

        user_name = await get_user_name(user_id, chat_id)
        user_link = f"[id{user_id}|{user_name}]"
        slots_emojis = ['🍒', '🍋', '🍇', '💎', '7️⃣', '🔔', '💰', '🍉', '🎰', '🎲', '🎯']
        msg_text = f"🎰 Казино запущено!\n👤 Игрок: {user_link}\n💰 Ставка: {bet:,}$\n\n🎡 Крутим... [🎰 🎰 🎰]".replace(",", ".")

        sent_msg_id = await bot.api.messages.send(peer_id=message.object.peer_id, message=msg_text, disable_mentions=1, random_id=0)
        # Обновлено
        try:
            msgs = await bot.api.messages.get_by_id(message_ids=[sent_msg_id])
            sent_cmid = msgs.items[0].conversation_message_id
        except: sent_cmid = 0
        
        # Calculate result immediately
        results, win, mult, commission, comm_rate = get_casino_result(bet, ud_eco.get('vip'))
        r1, r2, r3 = results

        for _ in range(4):
            await asyncio.sleep(0.3)
            spin_text = f"[{random.choice(slots_emojis)} {random.choice(slots_emojis)} {random.choice(slots_emojis)}]"
            try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=sent_cmid, message=f"🎰 Казино запущено!\n👤 Игрок: {user_link}\n💰 Ставка: {bet:,}$\n\n🎡 Крутим... {spin_text}".replace(",", "."), disable_mentions=1)
            except: pass
        
        await asyncio.sleep(0.5)
        spin_text = f"[{r1} {random.choice(slots_emojis)} {random.choice(slots_emojis)}]"
        try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=sent_cmid, message=f"🎰 Казино запущено!\n👤 Игрок: {user_link}\n💰 Ставка: {bet:,}$\n\n🎡 Крутим... {spin_text}".replace(",", "."), disable_mentions=1)
        except: pass

        await asyncio.sleep(0.6)
        spin_text = f"[{r1} {r2} {random.choice(slots_emojis)}]"
        try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=sent_cmid, message=f"🎰 Казино запущено!\n👤 Игрок: {user_link}\n💰 Ставка: {bet:,}$\n\n🎡 Крутим... {spin_text}".replace(",", "."), disable_mentions=1)
        except: pass

        await asyncio.sleep(0.8)

        res_str = f"[{r1} {r2} {r3}]"
        keyboard = None
        if win > 0:
            await add_balance(user_id, win)
            
            # Обновляем статистику сервера (комиссия в экономику)
            econ = load_economy()
            if 'server_stats' not in econ: econ['server_stats'] = {'collected_commissions': 0}
            econ['server_stats']['collected_commissions'] += commission
            save_economy(econ)

            final_text = f"🎰 Казино запущено!\n👤 Игрок: {user_link}\n💰 Ставка: {bet_int:,}$\n\n🎡 Результат: {res_str}\n✅ Выигрыш: {win:,}$ (x{mult})\n📉 Комиссия ({int(comm_rate*100)}%): {commission:,}$".replace(",", ".")
        else:
            final_text = f"🎰 Казино запущено!\n👤 Игрок: {user_link}\n💰 Ставка: {bet_int:,}$\n\n🎡 Результат: {res_str}\n❌ Вы проиграли!".replace(",", ".")
            keyboard = None

        try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=sent_cmid, message=final_text, disable_mentions=1, keyboard=keyboard)
        except: await bot.api.messages.send(peer_id=message.object.peer_id, message=final_text, disable_mentions=1, keyboard=keyboard, random_id=0)
        
        await bot.api.messages.send_message_event_answer(event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id) # Обновлено
        return True

    if command == "join_duel":
        creator_id = int(payload.get("creator"))
        amount = int(payload.get("amount"))
        joiner_id = user_id
        
        duel_key = f"duel_{message.object.peer_id}_{message.object.conversation_message_id}"
        if duel_key in resolved_duels:
            return await send_event_answer_safe(snackbar_text="❌ Эта дуэль уже завершена или отменена!")

        duel_cd = 15 # Обновлено
        if time.time() - user_duel_cooldown.get(joiner_id, 0) < duel_cd:
            rem = int(duel_cd - (time.time() - user_duel_cooldown.get(joiner_id, 0)))
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": f"⏳ Кулдаун дуэли! Подождите {rem} сек."})
            )
            return True

        if joiner_id == creator_id:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Нельзя играть с самим собой!"})
            )
            return True
            
        resolved_duels.append(duel_key)

        if not await subtract_balance(joiner_id, amount):
            error_msg = "❌ Недостаточно средств для участия в дуэли!"
            await send_event_answer_safe(snackbar_text=error_msg)
            await bot.api.messages.send(peer_id=message.object.peer_id, message=f"⚠️ {await get_user_link(user_id)}, {error_msg}", random_id=0)
            if duel_key in resolved_duels: resolved_duels.remove(duel_key)
            return True
            
        user_duel_cooldown[joiner_id] = time.time() # Обновлено

        winner = random.choice([creator_id, joiner_id])
        loser = joiner_id if winner == creator_id else creator_id
        
        ud_winner_eco = await get_user_economy_data(winner)
        v_lvl = ud_winner_eco.get('vip_level', 0)
        comm_rate = VIP_CONFIG[v_lvl]['comm'] if v_lvl in VIP_CONFIG else 0.10
        ud_winner_full = await get_user_data(winner)
        if ud_winner_full.get('no_comm_until', 0) > time.time(): comm_rate = 0.0

        total_pool = amount * 2
        commission = int(total_pool * comm_rate)
        win_amount = total_pool - commission
        
        await add_balance(winner, win_amount) # Обновлено
        
        # Quest progress for duel wins
        quest_msg = "" # Инициализируем quest_msg
        q_completed = False # Инициализируем q_completed
        qr_mats = 0 # Инициализируем qr_mats
        qr_exp = 0 # Инициализируем qr_exp
        winner_ud = await get_user_data(winner)
        if winner_ud.get('clan_id'): # Если победитель в клане, тогда получаем значения
            q_completed, qr_mats, qr_exp = await check_daily_quest_progress(winner_ud['clan_id'], 1, "duel_wins")
            if q_completed:
                quest_msg = f"\n\n🎉 Клан выполнил ежедневное задание!"
        
        # Stats
        econ = load_economy() # Обновлено
        winner_str, loser_str = str(winner), str(loser)
        if winner_str not in econ['users']: await get_balance(winner); econ = load_economy()
        if loser_str not in econ['users']: await get_balance(loser); econ = load_economy()
        
        econ['users'][winner_str]['duels_won'] = econ['users'][winner_str].get('duels_won', 0) + 1
        econ['users'][winner_str]['duels_sum_won'] = econ['users'][winner_str].get('duels_sum_won', 0) + (win_amount - amount) # Profit calculation might be slightly off due to commission, but usually acceptable
        econ['users'][loser_str]['duels_lost'] = econ['users'][loser_str].get('duels_lost', 0) + 1
        econ['users'][loser_str]['duels_sum_lost'] = econ['users'][loser_str].get('duels_sum_lost', 0) + amount
        if 'server_stats' not in econ: econ['server_stats'] = {'collected_commissions': 0}
        econ['server_stats']['collected_commissions'] += commission # Обновлено
        log_transaction(winner, f"Дуэль: победа над ID{loser}, куш +{win_amount}$ (ставка {amount}$)")
        log_transaction(loser, f"Дуэль: поражение от ID{winner}, потеряно -{amount}$")
        save_economy(econ)
        
        try: c_info = await bot.api.users.get(user_ids=creator_id); c_name = f"{c_info[0].first_name} {c_info[0].last_name}"
        except: c_name = "Игрок 1"
        try: j_info = await bot.api.users.get(user_ids=joiner_id); j_name = f"{j_info[0].first_name} {j_info[0].last_name}"
        except: j_name = "Игрок 2"
        
        c_link = f"[id{creator_id}|{c_name}]"
        j_link = f"[id{joiner_id}|{j_name}]"
        w_link = f"[id{winner}|{c_name if winner == creator_id else j_name}]"
        
        msg = (f"💥 Дуэль завершена!\n\n{c_link} vs {j_link}\n👑 Победитель: {w_link}\n\n💰 Он забирает {win_amount:,}$ (Комиссия {int(comm_rate*100)}%: {commission:,}$){quest_msg}".replace(",", ".")) # Обновлено
        try:
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=None, group_id=message.group_id)
        except Exception:
            # Если не удалось отредактировать (флуд), просто отправляем результат новым сообщением
            await bot.api.messages.send(peer_id=message.object.peer_id, message=msg, random_id=0, disable_mentions=1)
            
        await send_event_answer_safe()
        return True # Обновлено

    if command == "cancel_duel":
        creator_id = int(payload.get("creator")) # Обновлено
        amount = int(payload.get("amount")) # Обновлено
        chat_id = payload.get("chatId")

        duel_key = f"duel_{message.object.peer_id}_{message.object.conversation_message_id}"
        if duel_key in resolved_duels:
            return await send_event_answer_safe(snackbar_text="❌ Эта дуэль уже была отменена или принята!")
        
        if user_id != creator_id and await get_role(user_id, chat_id) < 1:
            await bot.api.messages.send_message_event_answer(
                event_id=message.object.event_id, peer_id=message.object.peer_id, user_id=message.object.user_id,
                event_data=json.dumps({"type": "show_snackbar", "text": "Это не ваша дуэль!"})
            )
            return True
            # Обновлено
            
        resolved_duels.append(duel_key)
        await add_balance(creator_id, amount)
        msg = "❌ Дуэль отменена, деньги возвращены."
        try:
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=None, group_id=message.group_id)
        except Exception:
            await bot.api.messages.send(peer_id=message.object.peer_id, message=msg, random_id=0)

        await send_event_answer_safe()
        return True # Обновлено

    if command == "other_menu":
        await send_event_answer_safe()
        category = payload.get("category")
        text = ""
        kb = Keyboard(inline=True)
        
        if category == "main":
            text = "🎮 Меню «Другое»:\nВыберите раздел:"
            kb.add(Callback("💰 Экономика", {"command": "other_menu", "category": "economy", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            kb.add(Callback("🏢 Бизнесы", {"command": "other_menu", "category": "business", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY) # Обновлено
            kb.add(Callback("🏰 Кланы", {"command": "other_menu", "category": "clans", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY).row()
            kb.add(Callback("💼 Работа", {"command": "other_menu", "category": "jobs", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            kb.add(Callback("🎟️ Прочее", {"command": "other_menu", "category": "misc", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY).row()
            kb.add(Callback("❌ Закрыть", {"command": "delete_msg"}), color=KeyboardButtonColor.NEGATIVE)
            
        elif category == "economy":
            text = "💰 Экономика:\n\n" + "\n".join([
                '/баланс -- состояние счета',
                '/приз -- ежедневный бонус',
                '/передать -- перевод средств',
                '/дуэль -- игра 1 на 1',
                '/казино -- испытай удачу',
                '/положить -- вклад в банк',
                '/снять -- вывод из банка',
                '/депозит -- пассивный доход (VIP)',
                '/благо -- благотворительность',
                '/топ [деньги/пет/работа] -- рейтинги',
                '/топблаго -- рейтинг меценатов',
                '/buyvip -- купить VIP статус',
                '/открытьдепозит -- создать вклад',
                '/закрытьдепозит -- снять вклад',
                '/jobs -- список вакансий',
                '/work -- выйти на смену',
                '/pet -- мой питомец',
                '/промо -- активировать код'
            ])
            if await get_tester_role(user_id) >= 1:
                text += "\n🛠 /bugreport -- меню багов" # Обновлено
            kb.add(Callback("<< Назад", {"command": "other_menu", "category": "main", "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY)

        elif category == "business":
            text = "🏢 Бизнесы:\n\n" + "\n".join([
                '/biz -- список всех бизнесов',
                '/biz [ID] -- информация об объекте',
                '/mybiz -- ваши предприятия и управление',
                '/слоты -- увеличить лимит бизнесов',
                '/depo -- управление станциями',
                '/biz donate [ID] -- пожертвовать клану',
                '/sell biz [ID] [ID игрока] [Цена] -- продажа игроку'
            ])
            kb.add(Callback("<< Назад", {"command": "other_menu", "category": "main", "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY)

        elif category == "clans":
            text = "🏰 Кланы:\n\n" + "\n".join([
                '/clan -- меню клана',
                '/topclan -- рейтинг кланов',
                '/clan create -- создать клан',
                '/clan join -- вступить',
                '/clan invite -- пригласить',
                '/clan kick -- выгнать',
                '/clan leave -- выйти',
                '/clan deposit -- пополнить казну',
                '/clan withdraw -- снять из казны',
                '/clan war -- объявить войну',
                '/clan setname -- сменить название',
                '/clan settag -- сменить тег',
                '/clan rank -- управление рангами',
                '/clan setsalary -- настройка зарплат',
                '/clan salary -- зарплаты',
                '/clan bizinfo [ID] -- инфо о бизнесе клана',
                '/clan bizwar [ID] -- захват чужого бизнеса' # Обновлено
            ])
            kb.add(Callback("<< Назад", {"command": "other_menu", "category": "main", "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY)

        elif category == "jobs":
            text = "💼 Работа:\n\n" + "\n".join([
                '/jobs -- интерактивное меню вакансий',
                '/joinjob -- устроиться на работу',
                '/work -- выйти на смену',
                '/myjob -- моя статистика',
                '/quitjob -- уволиться'
            ])
            kb.add(Callback("<< Назад", {"command": "other_menu", "category": "main", "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY)

        elif category == "misc":
            text = "🎟️ Прочее:\n\n" + "\n".join([
                '/промо -- активировать код',
                '/promolist -- доступные коды',
                '/offer -- предложение'
            ])
            kb.add(Callback("<< Назад", {"command": "other_menu", "category": "main", "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY)

        try:
            await bot.api.messages.send(peer_id=message.object.peer_id, message=text, keyboard=kb, random_id=0) # Обновлено
        except Exception as e:
            print(f"Send failed: {e}")
        return True

    if command == "ghelp_page":
        await send_event_answer_safe()
        lvl = payload.get("lvl")
        g_cmds = { # Обновлено
                1: ["🛡 Зам. руководителя:", "• /gstaff — состав руководства", "• /infochat [ID] — инфо о беседе", "• /blacklist — ЧС бота", "• /banschats — забан. чаты", "• /gbanlist — список гбанов"],
            2: ["👑 Осн. зам. руководителя:", "• /editowner — передать владельца", "• /addblack — ЧС бота", "• /unblack — унчс", "• /banid — забанить чат", "• /unbanid — унбан чата", "• /notoplist — скрытые из топа", "• /aban — заморозить права", "• /unaban — разморозить"],
            3: ["💎 Спец. руководитель:", "• /grole — выдать г-роль (1-5)", "• /infoid — беседы пользователя", "• /clearchat — сброс чата", "• /banreport — бан репорта", "• /unbanreport — снять бан репорта", "• /addtester — назначить тестера", "• /removetester — снять тестера"],
            4: ["👾 Разработчик:", "• /gzov — глобальный сбор", "• /debuglog — лог ошибок", "• /resetbugs — сброс тикетов", "• /maintenance — режим тех. работ", "• /ignorechat — игнор команд чата", "• /exception — исключение из тех. работ", "• /takevip — удалить VIP", "• /resetclanbiz — изъять бизнес клана", "• /setpetlvl — уровень питомца"]
        }
        
        category_data = g_cmds.get(lvl, ["Нет данных"])
        text = category_data[0] + "\n\n" + "\n".join(category_data[1:])
        # Обновлено
        kb = Keyboard(inline=True).add(Callback("<< Назад", {"command": "ghelp_main", "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY)
        try:
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=text, keyboard=kb)
            return True
        except Exception: pass
        return True

    if command == "ghelp_main":
        await send_event_answer_safe()
        global_level = await get_global_role(user_id) # Обновлено
        if global_level < 1: return await send_event_answer_safe(snackbar_text="❌ У вас нет глобальных прав!")
        kb = Keyboard(inline=True)
        if global_level >= 1: kb.add(Callback("Зам. рук.", {"command": "ghelp_page", "lvl": 1, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
        if global_level >= 2: kb.add(Callback("Осн. зам. рук.", {"command": "ghelp_page", "lvl": 2, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
        if global_level >= 3: kb.row().add(Callback("Спец. рук.", {"command": "ghelp_page", "lvl": 3, "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
        if global_level >= 4: kb.add(Callback("Разработчик", {"command": "ghelp_page", "lvl": 4, "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
        
        try:
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message="📖 Глобальные команды. Выберите ваш уровень:", keyboard=kb)
        except Exception:
            try:
                await bot.api.messages.send(peer_id=message.object.peer_id, message="📖 Глобальные команды. Выберите ваш уровень:", keyboard=kb, random_id=0)
            except Exception:
                pass
        return True

    if command == "help_menu":
        await send_event_answer_safe() # Обновлено
        category = payload.get("category")
        user_role = await get_role(user_id, chat_id)
        role_names = {1: "Модератор", 2: "Старший модератор", 3: "Админ", 4: "Старший админ", 5: "Владелец", 6: "Разработчик"}
        text = ""
        kb = Keyboard(inline=True)
        
        # Словари с командами (перенесены для использования в меню)
        commands_levels = {
            1: [
                '/setnick -- установить ник',
                '/removenick -- удалить ник',
                '/getnick -- узнать ник',
                '/getacc -- найти аккаунт по нику',
                '/nlist -- список ников',
                '/nonick -- пользователи без ников',
                '/kick -- исключить пользователя',
                '/warn -- выдать выговор',
                '/unwarn -- снять выговор',
                '/pred -- выдать предупреждение',
                '/unpred -- снять предупреждение',
                '/clearpreds -- очистить предупреждения',
                '/getwarn -- активные выговоры',
                '/warnhistory -- история выговоров',
                '/warnlist -- список выговоров',
                '/staff -- персонал беседы',
                '/reg -- дата регистрации',
                '/mute -- замутить пользователя',
                '/unmute -- размутить пользователя',
                '/mutelist -- список мутов',
                '/clear -- очистка сообщений',
                '/delete -- удалить сообщение',
                '/givecmds -- список команд',
                '/aban -- заморозить права',
                '/unaban -- разморозить права',
                '/инвентарь -- ваши предметы',
                '/использовать -- юзать предмет',
                '/реф -- реферальная система',
                '/modstats -- стат. модератора',
                '/tstats -- стат. тестера'
            ],
            2: [
                '/ban -- забанить в беседе',
                '/unban -- разбанить в беседе',
                '/banlist -- список забаненных',
                '/addmoder -- назначить модератором',
                '/removerole -- снять роль',
                '/zov -- общий сбор',
                '/online -- сбор онлайн',
                '/onlinelist -- список онлайн',
                '/inactivelist -- неактивные пользователи',
                '/masskick -- массовое исключение'
            ],
            3: [
                '/quiet -- тихий режим',
                '/skick -- исключить из связанных бесед',
                '/sban -- забанить в связанных беседах',
                '/sunban -- разбанить в связанных беседах',
                '/addsenmoder -- назначить ст. модератором',
                '/rnickall -- удалить все ники',
                '/sremovenick -- удалить ник в связанных беседах',
                '/szov -- сбор связанных бесед',
                '/srole -- выдать роль в связанных беседах',
                '/editstats -- изменить статистику'
            ],
            4: [
                '/addadmin -- назначить администратором',
                '/serverinfo -- информация о сервере',
                '/filter -- фильтр слов',
                '/demote -- исключить без ролей',
                '/infochat -- инфо о любой беседе',
                '/infoid -- список бесед игрока',
                '/status -- состояние бота',
                '/settings -- настройки чата',
                '/фильтр -- слова-фильтры',
                '/addtester -- назначить тестера',
                '/removetester -- снять тестера'
            ],
            5: [
                '/antiflood -- режим антифлуда',
                '/welcometext -- текст приветствия',
                '/invite -- инвайт модераторами',
                '/leave -- кик при выходе',
                '/addsenadmin -- назначить ст. администратором',
                '/setleader -- выдать статус Руководства',
                '/removeleader -- снять статус Руководства',
                '/server -- привязка сервера',
                '/link_status -- статус синхронизации',
                '/logs -- последние логи',
                '/autopost -- пост из группы',
                '/linkfilter -- фильтр ссылок'
            ],
            6: [
                '/sync -- синхронизация БД',
                '/выдатьмонеты -- выдать валюту',
                '/выдатьвип -- выдать VIP',
                '/givecmd -- выдать команду',
                '/uncmd -- забрать команду',
                '/раздача -- раздача денег',
                '/type -- тип беседы',
                '/сетправила -- правила беседы',
                '/сетправилабота -- правила бота',
                '/сетинфо -- инфо проекта',
                '/games -- игровые команды',
                '/создатьпромо -- создать промокод',
                '/удалитьпромо -- удалить промокод',
                '/editowner -- передать владельца',
                '/forceowner -- забрать владельца',
                '/masskick all -- кик всех без ролей',
                '/say -- сообщение от бота',
                '/banwords -- управление запрещенными словами',
                '/givemats -- выдать материалы',
                '/giveexp -- выдать опыт',
                '/setdev -- права разработчика',
                '/news -- рассылка новостей',
                '/выдатьдолжность -- установить должность',
                '/удалитьдолжность -- убрать должность',
                '/cancelwar -- отменить войну',
                '/activewars -- активные войны',
                '/resetclanbiz -- изъять бизнес у клана',
                '/setpetlvl -- уровень питомца',
                '/takevip -- удалить VIP',
                '/resetbugs -- сброс всех тикетов',
                '/maintenance -- режим тех. работ',
                '/ignorechat -- игнор команд в чате',
                '/exception -- исключение из тех. работ',
                '/gban -- глобальный бан',
                '/gbanpl -- глобальный бан (PL)',
                '/ungban -- снять глобальный бан',
                '/gzov -- глобальное объявление',
                '/setleader -- выдать статус руководства',
                '/removeleader -- снять статус руководства',
                '/grole -- выдать глобальную роль',
                '/grrole -- снять глобальную роль',
                '/maintenance -- режим тех. работ',
                '/ignorechat -- игнорирование команд в чате',
                '/exception -- исключение из тех. работ',
                '/resetbugs -- полная очистка тикетов',
                '/giveupgrade -- выдать улучшение клану',
                '/resetmoney -- обнулить баланс',
                '/setbalance -- установить баланс',
                '/newrole -- создать роль',
                '/delrole -- удалить роль',
                '/role -- выдать роль',
                '/editcmd -- приоритет команды',
                '/stats_eco -- экономика сервера',
                '/delclan -- удалить клан',
                '/gzov -- глобальный сбор',
                '/addtester -- назначить тестера',
                '/removetester -- снять тестера',
                '/debuglog -- последние ошибки',
                '/resetwork -- сброс КД работы',
                '/resetwarcd -- сброс КД войн',
                '/clearchat -- полный сброс чата',
                '/chatid -- ID чата',
                '/giveslot -- выдать слот',
                '/dbprune -- чистка сообщений',
                '/wipe_economy -- глобальный вайп',
                '/delbiz -- удалить бизнес'
            ]
        }

        if category == "main":
            text = "📚 Выберите категорию команд:"
            kb.add(Callback("👤 Пользователь", {"command": "help_menu", "category": "user", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            kb.add(Callback("🏢 Бизнесы", {"command": "help_menu", "category": "business", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            kb.add(Callback("💰 Экономика", {"command": "help_menu", "category": "economy", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            kb.add(Callback("🏰 Кланы", {"command": "help_menu", "category": "clans", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY).row()
            if await get_tester_role(user_id) >= 1 or user_role >= 6:
                kb.add(Callback("🧪 Тестеры", {"command": "help_menu", "category": "tester", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            if user_role >= 1:
                kb.add(Callback("🛡 Модерация", {"command": "help_menu", "category": "staff", "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY).row()
            kb.add(Callback("❌ Закрыть", {"command": "delete_msg", "user": user_id}), color=KeyboardButtonColor.NEGATIVE)
            
        elif category == "user":
            text = "👤 Команды пользователя:\n\n" + "\n".join([
                '/help -- меню помощи',
                '/stats -- статистика',
                '/getid -- ID пользователя',
                '/info -- инфо о проекте',
                '/моирефы -- список приглашенных',
                '/слоты -- слоты для бизнеса',
                '/правила -- правила беседы',
                '/инвентарь -- ваши предметы',
                '/реф -- реферальная система',
                '/q -- выход из беседы',
                '/bug [текст] -- отправить баг-репорт',
                '/infobot -- инфо о боте',
                '/other -- меню категорий',
                '/offer -- предложение'
            ])
            kb.add(Callback("<< Назад", {"command": "help_menu", "category": "main", "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY)

        elif category == "tester":
            text = "🧪 Команды тестировщика:\n\n" + "\n".join([
                '/bugreport -- панель управления багами',
                '/bug_stats -- ваша статистика',
                '/tstats -- статистика тестера (в чате тестеров)',
                '/bug_invite -- позвать разраба в чат',
                '/testers -- список состава тестеров',
                '/tshop -- магазин наград',
                '/инвентарь -- ваши предметы',
                '/debuglog -- последние ошибки (3+ lvl)',
                '/devbugs -- баги в очереди разработки',
                '/выдатьбаллы/снятьбаллы -- управление очками'
            ])
            if await get_tester_role(user_id) >= 3 or await get_global_role(user_id) >= 5:
                text += "\n• /addtester -- назначить тестера\n• /removetester -- снять тестера"
            if await get_global_role(user_id) >= 5 or await get_tester_role(user_id) >= 3:
                text += "\n\n🛠 Управление балллами:\n/выдатьбаллы [user] [кол-во]\n/снятьбаллы [user] [кол-во]"
            kb.add(Callback("<< Назад", {"command": "help_menu", "category": "main", "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY)

        elif category == "economy":
            text = "💰 Экономика:\n\n" + "\n".join([
                '/баланс -- баланс',
                '/приз -- бонус',
                '/передать -- перевод',
                '/дуэль -- дуэль',
                '/казино -- казино',
                '/положить -- в банк',
                '/снять -- из банка',
                '/благо -- благо',
                '/топ -- топ богатых',
                '/notop -- скрыться из топа',
                '/buyvip -- купить VIP',
                '/открытьдепозит -- создать вклад',
                '/закрытьдепозит -- забрать вклад',
                '/промо -- промокод',
                '/promolist -- список кодов',
                '/jobs -- центр занятости (меню)',
                '/work -- выйти на смену',
                '/pet -- мой питомец',
                '/pet shop -- магазин',
                '/myjob -- моя статистика',
                '/joinjob -- устроиться',
                '/quitjob -- уволиться'
            ])
            kb.add(Callback("<< Назад", {"command": "help_menu", "category": "main", "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY)

        elif category == "business":
            text = "🏢 Команды бизнеса:\n\n" + "\n".join([
                '/biz -- список бизнесов',
                '/biz [ID] -- инфо/покупка',
                '/mybiz -- управление своими точками',
                '/слоты -- купить доп. слоты',
                '/depo -- управление станцией',
                '/biz donate [ID] -- передать бизнес клану',
                '/sell biz [ID] [user] [цена] -- продажа'
            ])
            kb.add(Callback("<< Назад", {"command": "help_menu", "category": "main", "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY)

        elif category == "clans":
            text = "🏰 Кланы:\n\n" + "\n".join([
                '/clan -- кланы',
                '/topclan -- топ кланов',
                '/clan create -- создать клан',
                '/clan join -- вступить в клан',
                '/clan invite -- пригласить',
                '/clan kick -- выгнать',
                '/clan leave -- выйти',
                '/clan deposit -- пополнить',
                '/clan biz -- бизнесы клана',
                '/clan bizinfo [ID] -- статус бизнеса клана',
                '/clan withdraw -- снять',
                '/clan war -- начать войну',
                '/clan bizwar [ID] -- захват чужого бизнеса',
                '/clan donate [ID] -- передать бизнес клану',
                '/clan setname -- сменить имя',
                '/clan settag -- сменить тег',
                '/clan giverank -- выдать ранг',
                '/clan setsalary -- настройка зарплат',
                '/clan promote -- повысить',
                '/clan degrade -- понизить',
                '/clan demote -- разжаловать',
                '/clan setrank -- название ранга',
                '/clan salary -- получить зарплату',
                '/clan setsalary -- настроить зарплату',
                '/clan passleader -- передать управление',
                '/clan passleader -- передать лидерство', # Обновлено
                '/clan delete -- удалить клан',
                '/clan reclaimbiz -- забрать бизнес клана себе (только лидер)'

            ])
            kb.add(Callback("<< Назад", {"command": "help_menu", "category": "main", "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY)

        elif category == "staff":
            text = "🛡 Выберите уровень прав:"
            row_len = 0
            for lvl in range(1, 7):
                if user_role >= lvl:
                    role_names = {1: "Модератор", 2: "Старший модератор", 3: "Администратор", 4: "Старший администратор", 5: "Владелец", 6: "Разработчик"}
                    kb.add(Callback(f"Lvl {lvl} ({role_names[lvl]})", {"command": "help_menu", "category": f"lvl{lvl}", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                    row_len += 1
                    if row_len % 2 == 0: kb.row()
            
            if row_len % 2 != 0: kb.row()
            kb.add(Callback("<< Назад", {"command": "help_menu", "category": "main", "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY)

        elif category.startswith("lvl"):
            lvl = int(category[3:])
            cmds = commands_levels.get(lvl, ["Нет команд"])
            text = f"👮 Команды уровня {lvl}:\n\n" + "\n".join(cmds)
            kb.add(Callback("<< Назад", {"command": "help_menu", "category": "staff", "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY)

        try:
            await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=text, keyboard=kb)
        except Exception:
            try:
                await bot.api.messages.send(peer_id=message.object.peer_id, message=text, keyboard=kb, random_id=0)
            except Exception:
                pass
        return True

    # Резервные обработчики bug_report_menu, bug_view и bug_action удалены из main_event_handlers.
    # Все операции теперь надежно обрабатываются в callback_handlers.

    if command == "type_page":
        await send_event_answer_safe()
        page = int(payload.get("page", 1))
        chat_id = payload.get("chatId")

        if page == 2:
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("MED", {"command": "set_type", "type": "med", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("RUK", {"command": "set_type", "type": "ruk", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("USERS", {"command": "set_type", "type": "users", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .row()
                .add(Callback("<< Назад", {"command": "type_page", "page": 1, "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY)
            )
        else:
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("DEF", {"command": "set_type", "type": "def", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("EXT", {"command": "set_type", "type": "ext", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("PL", {"command": "set_type", "type": "pl", "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
                .row()
                .add(Callback("HEL", {"command": "set_type", "type": "hel", "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
                .add(Callback("LD", {"command": "set_type", "type": "ld", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("ADM", {"command": "set_type", "type": "adm", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .row()
                .add(Callback("MOD", {"command": "set_type", "type": "mod", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("TEX", {"command": "set_type", "type": "tex", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("TEST", {"command": "set_type", "type": "test", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .row()
                .add(Callback("Дальше >>", {"command": "type_page", "page": 2, "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY)
            )

        try:
            await bot.api.messages.edit(
                peer_id=message.object.peer_id,
                message="Выберите тип беседы:",
                conversation_message_id=message.object.conversation_message_id,
                keyboard=keyboard
            )
        except Exception as e:
            # Отправляем новое сообщение, если редактирование не удалось
            await bot.api.messages.send(
                peer_id=message.object.peer_id,
                message="Выберите тип беседы:",
                keyboard=keyboard,
                random_id=0
            )
        return True

    if command == "set_type":
        chat_id = payload.get("chatId") # Обновлено
        chat_type = payload.get("type")
        
        # Проверяем, что это владелец беседы или админ
        members = await bot.api.messages.get_conversation_members(peer_id=message.object.peer_id)
        members = json.loads(members.json())
        owner_id = None
        is_admin = False
        for item in members['items']:
            if item['member_id'] == -message.group_id:  # Группа
                owner_id = item['member_id']
            elif item['member_id'] == user_id and item.get('is_admin', False):
                is_admin = True
        # Обновлено
        role = await get_role(user_id, chat_id)
        if role < 6 and not is_admin:
            await send_event_answer_safe(snackbar_text="Только разработчик или админ может менять тип!")
            return True
        
        type_names = {
            'def': 'DEF - Общие',
            'ext': 'EXT - Расширенная',
            'pl': 'PL - Беседа игроков',
            'hel': 'HEL - Беседа хеллперов',
            'ld': 'LD - Беседа лидеров',
            'adm': 'ADM - Беседа администраторов',
            'mod': 'MOD - Беседа модераторов',
            'tex': 'TEX - Беседа техов',
            'test': 'TEST - Беседа тестеров',
            'med': 'MED - Беседа медиа-партнёров',
            'ruk': 'RUK - Беседа руководства',
            'users': 'USERS - Беседа пользователей'
        }
        
        type_name = type_names.get(chat_type, chat_type)
        
        try:
            # Сохраняем тип беседы и owner_id (обновлено)
            sql.execute("INSERT OR REPLACE INTO chats (chat_id, peer_id, owner_id, chat_type) VALUES (?, ?, ?, ?)", (chat_id, message.object.peer_id, owner_id, chat_type))
            database.commit()
            
            # Отправляем уведомление
            await send_event_answer_safe(snackbar_text=f"✅ Тип установлен: {type_name}")
            
            # Редактируем сообщение в беседе
            try:
                await bot.api.messages.edit(
                    peer_id=message.object.peer_id,
                    message=f"✅ Тип беседы изменен на: {type_name}",
                    conversation_message_id=message.object.conversation_message_id,
                    keyboard=None
                )
            except Exception as edit_e:
                # Если редактирование не удалось, отправляем новое сообщение
                await bot.api.messages.send(
                    peer_id=message.object.peer_id,
                    message=f"✅ Тип беседы изменен на: {type_name}",
                    random_id=0
                )
        except Exception as e:
            await send_event_answer_safe(snackbar_text=f"❌ Ошибка: {e}")
        return True

    if command == "tshop_buy":
        item_id = payload.get("item") # Обновлено
        item = TSHOP_ITEMS.get(item_id)
        if not item: return
        
        ud = await get_user_data(user_id)
        if ud['points'] < item['price']:
            error_msg = f"❌ Недостаточно баллов тестера! Нужно: {item['price']}"
            await send_event_answer_safe(snackbar_text=error_msg)
            await bot.api.messages.send(peer_id=message.object.peer_id, message=f"⚠️ {await get_user_link(user_id)}, {error_msg}", random_id=0)
            return True
            
        await update_user_data(user_id, 'points', ud['points'] - item['price'])
        
        msg = None
        if item['type'] == "money":
            await add_balance(user_id, item['val']) # Обновлено
            log_transaction(user_id, f"Тестершоп: обменял {item['price']} баллов на {item['val']}$")
            msg = f"✅ Вы обменяли {item['price']} баллов на {item['val']:,}$!".replace(",", ".")
        
        elif item['type'] == "vip":
            econ = load_economy()
            u_str = str(user_id)
            if u_str not in econ['users']: await get_balance(user_id); econ = load_economy()
            
            current_now = datetime.now()
            u_data = econ['users'][u_str] # Обновлено
            
            if u_data.get('vip') and u_data.get('vip_until'):
                until_dt = datetime.fromisoformat(u_data['vip_until'])
                if until_dt < current_now: until_dt = current_now
                new_until = (until_dt + timedelta(days=item['val'])).isoformat()
            else:
                new_until = (current_now + timedelta(days=item['val'])).isoformat()
                
            u_data['vip'] = True
            u_data['vip_level'] = 1
            u_data['vip_until'] = new_until
            save_economy(econ)
            msg = f"✅ Куплен VIP статус на {item['val']} дней!" # Обновлено

        elif item['type'] == "buff":
            expire = int(time.time() + 86400)
            sql.execute("UPDATE user_data SET no_comm_until = ? WHERE user_id = ?", (expire, user_id))
            database.commit()
            msg = "✅ Бонус «0% комиссии» активирован на 24 часа!"

        elif item['type'] == "prefix":
            user_states[user_id] = {"action": "set_custom_prefix"}
            await bot.api.messages.send(peer_id=message.object.peer_id, message="🎭 Введите ваш уникальный префикс в чат (до 10 символов):", random_id=0)
            return await send_event_answer_safe(snackbar_text="✍️ Жду префикс в чате!")

        elif item['type'] == "case":
            rewards = CASE_REWARDS
            weights = [r['weight'] for r in rewards]
            reward = random.choices(rewards, weights=weights)[0]
            
            reward_msg = ""
            if reward['type'] == "money":
                await add_balance(user_id, reward['val'])
                reward_msg = f"💰 {reward['val']:,}$".replace(",", ".")
            elif reward['type'] == "points":
                final_points = ud['points'] + reward['val']
                await update_user_data(user_id, 'points', final_points)
                reward_msg = f"⭐ {reward['val']} баллов"
            elif reward['type'] == "vip":
                econ = load_economy()
                u_str = str(user_id)
                if u_str not in econ['users']: await get_balance(user_id); econ = load_economy()
                u_data = econ['users'][u_str]
                current_now = datetime.now()
                if u_data.get('vip') and u_data.get('vip_until'):
                    until_dt = datetime.fromisoformat(u_data['vip_until'])
                    if until_dt < current_now: until_dt = current_now
                    new_until = (until_dt + timedelta(days=reward['val'])).isoformat()
                else:
                    new_until = (current_now + timedelta(days=reward['val'])).isoformat()
                u_data['vip'] = True
                u_data['vip_until'] = new_until
                save_economy(econ)
                reward_msg = f"✨ VIP на {reward['val']} дн."
            elif reward['type'] == "trash":
                inventory = json.loads(ud.get('inventory', '[]'))
                inventory.append(reward['val'])
                await update_user_data(user_id, 'inventory', json.dumps(inventory, ensure_ascii=False))
                reward_msg = f"📦 {reward['val']}"
            
            final_msg = f"🎲 Открытие кейса тестера...\n\n🎉 Вы получили: {reward_msg}!"
            await bot.api.messages.send(peer_id=message.object.peer_id, message=final_msg, random_id=0)
            return await send_event_answer_safe(snackbar_text="✅ Кейс открыт!")
            
        elif item['type'] == "remove_prefix":
            sql.execute("UPDATE user_data SET custom_prefix = NULL WHERE user_id = ?", (user_id,))
            database.commit()
            msg = "✅ Ваш уникальный префикс был успешно удален!"
            
        if msg: await send_event_answer_safe(snackbar_text=msg)
        # Обновлено
        # Обновляем меню магазина
        new_payload = {"command": "tshop_menu", "chatId": chat_id}
        try: obj_data = message.object.model_dump()
        except: obj_data = message.object.dict().copy()
        obj_data['payload'] = new_payload
        return await callback_handlers(GroupTypes.MessageEvent(group_id=message.group_id, event_id=message.event_id, object=obj_data))

    if command == "tshop_menu":
        await send_event_answer_safe() # Обновлено
        ud = await get_user_data(user_id)
        msg = f"🧪 Магазин Тестировщиков\n⭐ Ваши баллы: {ud['points']}\n\nВыберите товар для покупки:"
        kb = Keyboard(inline=True)
        count = 0
        for key, val in TSHOP_ITEMS.items():
            if key == "remove_prefix" and not ud.get('custom_prefix'):
                continue
            kb.add(Callback(f"{val['name']} — {val['price']} б.", {"command": "tshop_buy", "item": key, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            count += 1
            if count % 2 == 0: kb.row()
        
        if count % 2 != 0: kb.row()
        kb.add(Callback("❌ Закрыть", {"command": "delete_msg"}), color=KeyboardButtonColor.SECONDARY)
        try: await bot.api.messages.edit(peer_id=message.object.peer_id, conversation_message_id=message.object.conversation_message_id, message=msg, keyboard=kb)
        except: await bot.api.messages.send(peer_id=message.object.peer_id, message=msg, keyboard=kb, random_id=0)
        return True

    # Если кнопка не обработана в текущей функции (например, управление бизнесом), 
    # передаем управление во второй обработчик. (обновлено)
    if not event_acknowledged:
        await callback_handlers(message)
    return True

@bot.on.chat_message()
async def on_chat_message(message: Message):
    if message.message_id in processed_messages:
        return
    processed_messages.append(message.message_id)
    bot_identifiers = ['!', '+', '/']

    user_id = message.from_id
    chat_id = message.chat_id
    peer_id = message.peer_id
    arguments = message.text.split() # Обновлено
    arguments_lower = message.text.lower().split()

    if user_id in user_states:
        state = user_states[user_id]
        prompt_msg_id = state.get("msg_id")
        if message.text.lower() == "отмена":
            del user_states[user_id]
            await message.reply("❌ Действие отменено.")
            if prompt_msg_id:
                try: await bot.api.messages.delete(message_ids=[prompt_msg_id], delete_for_all=True, group_id=message.group_id)
                except: pass
            return True
            
        action = state.get("action")
        if action == "add_bug_comment":
            bid = state["bid"]
            sql.execute("UPDATE support_tickets SET tester_comment = ? WHERE id = ?", (message.text, bid))
            database.commit()
            del user_states[user_id]
            await message.answer(f"✅ Комментарий к багу #{bid} успешно добавлен!")
            return True

        if action == "bug_reply":
            target_user = state["target_user"]
            bid = state["bid"]
            try:
                await bot.api.messages.send(
                    user_id=target_user,
                    message=f"✉️ Тестировщик ответил на ваш баг-репорт #{bid}:\n\n{message.text}",
                    random_id=0
                )
                await message.answer(f"✅ Ответ на баг-репорт #{bid} успешно отправлен пользователю!")
            except Exception as e:
                await message.answer(f"❌ Не удалось отправить сообщение автору: {e}")
            
            del user_states[user_id]
            return True

        # Обновлено
        if action == "clan_withdraw":
            chat_id_st = state["chat_id"]
            try:
                amount = int(message.text)
            except:
                del user_states[user_id]
                await message.reply("❌ Сумма должна быть числом!")
                return True
            
            if amount <= 0:
                del user_states[user_id]
                await message.answer("❌ Сумма должна быть больше 0!")
                return True
            
            ud = await get_user_data(user_id)
            clan_id = ud.get('clan_id')
            if not clan_id or not await check_clan_perms(user_id, 4):
                del user_states[user_id]
                await message.answer("❌ Ошибка доступа!")
                return True

            sql.execute("SELECT money FROM clans WHERE clan_id = ?", (clan_id,))
            c_money = sql.fetchone()[0]
            if c_money < amount:
                 del user_states[user_id]
                 await message.reply("❌ В казне недостаточно средств!")
                 return True
            
            sql.execute("UPDATE clans SET money = money - ? WHERE clan_id = ?", (amount, clan_id)) # Обновлено
            database.commit()
            await add_balance(user_id, amount)
            await save_clan_to_json(clan_id)
            
            del user_states[user_id]
            await message.answer(f"✅ Вы сняли {amount} монет из казны клана!")
            if prompt_msg_id:
                try: await bot.api.messages.delete(message_ids=[prompt_msg_id], delete_for_all=True, group_id=message.group_id)
                except: pass
            return True

        if action == "set_custom_prefix": # Добавлено
            new_prefix = message.text.strip().replace("[", "").replace("]", "").replace("|", "")
            if len(new_prefix) > 10:
                return await message.reply("❌ Слишком длинный префикс! Максимум 10 символов.")
            
            sql.execute("UPDATE user_data SET custom_prefix = ? WHERE user_id = ?", (new_prefix, user_id))
            database.commit()
            del user_states[user_id]
            
            await message.reply(f"✅ Ваш уникальный префикс установлен: [{new_prefix}]!\nТеперь он виден всем в стаффе и ответах бота.")
            log_moderation_file(f"[id{user_id}] установил префикс: {new_prefix}") # Обновлено
            return True

        field = state.get("field")
        if field:
            target_user = state["target_user"]
            chat_id_st = state["chat_id"]
            new_value = message.text

            if field == "has_pc":
                if new_value not in ["0", "1"]:
                    await message.reply("⚠️ Для поля ПК введите 1 (есть) или 0 (нет).")
                    return True
                new_value = int(new_value)
            elif field in ["age", "points"]:
                if not new_value.isdigit():
                    await message.reply("⚠️ Введите числовое значение.")
                    return True
                new_value = int(new_value)
            elif field == "last_appointment":
                try:
                    datetime.fromisoformat(new_value)
                except ValueError:
                    await message.reply("⚠️ Неверный формат даты! Используйте YYYY-MM-DD (например: 2024-04-15).\nУбедитесь, что месяц не больше 12.")
                    return True
                
            await update_user_data(target_user, field, new_value)
            del user_states[user_id] # Обновлено
            
            field_names = {
                "age": "Возраст", "has_pc": "Доступ к ПК", "discord": "Discord",
                "forum": "Forum", "points": "Баллы", "last_appointment": "Дата повышения",
                "position": "Должность"
            }
            target_name = await get_user_name(target_user, chat_id_st)
            await message.reply(f"✅ Поле «{field_names.get(field)}» для пользователя [id{target_user}|{target_name}] успешно обновлено на: {new_value}")
            
            try: u_info = await bot.api.users.get(user_ids=target_user); u_name = f"[id{target_user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{target_user}"
            await log_action(user_id, chat_id_st, f"Изменил поле «{field_names.get(field)}» пользователю {u_name} на: {new_value}")
            if prompt_msg_id:
                try: await bot.api.messages.delete(message_ids=[prompt_msg_id], delete_for_all=True, group_id=message.group_id)
                except: pass
            return True

    try:
        command_identifier = arguments[0].strip()[0]
        command = arguments_lower[0][1:]
    except:
        command_identifier = " "
        command = " "

    # Проверка на бан чата
    sql.execute("SELECT reason FROM banned_chats WHERE chat_id = ?", (chat_id,)) # Обновлено
    banned_chat_reason = sql.fetchone()
    if banned_chat_reason:
        # Просто игнорируем любые команды из забаненного чата
        return True

    if command_identifier in bot_identifiers:
        # 1. Проверка глобальных тех. работ с учетом исключения для чата
        if command in ['логи', 'logs_money', 'транзакции']:
            if await get_role(user_id, chat_id) < 1:
                return await message.reply("❌ Недостаточно прав!")
            
            target, err = await get_target_user(message, arguments)
            if err: return await message.reply(err)
            
            if not os.path.exists("transactions.log"):
                return await message.reply("📝 Логи транзакций пока пусты.")

            res_lines = []
            target_id_str = str(target)
            
            try:
                # Читаем файл с конца, чтобы не грузить ОЗУ большими объемами
                with open("transactions.log", "r", encoding="utf-8") as f:
                    # Берем последние 2000 строк для поиска (хватит для большинства случаев)
                    all_lines = deque(f, maxlen=2000)
                    for line in reversed(all_lines):
                        # Проверяем, упоминается ли ID пользователя в строке лога
                        if f" {target_id_str} |" in line or f"ID{target_id_str}" in line:
                            res_lines.append(line.strip())
                        if len(res_lines) >= 15: break # Показываем только последние 15 действий
            except:
                return await message.reply("❌ Ошибка чтения файла логов.")

            if not res_lines:
                return await message.reply(f"🔍 Свежих транзакций для пользователя {target} не найдено.")

            t_name = await get_user_name(target, chat_id)
            await message.reply(f"💳 Последние 15 транзакций [id{target}|{t_name}]:\n\n" + "\n".join(res_lines), disable_mentions=1)
            return True

        sql.execute("SELECT value FROM global_settings WHERE key = 'maintenance_mode'")
        g_maint = sql.fetchone()
        if g_maint and g_maint[0] == "1":
            # Проверяем, помечен ли этот чат как исключение
            sql.execute("SELECT maint_ignore FROM chats WHERE chat_id = ?", (chat_id,))
            m_res = sql.fetchone()
            is_exception = m_res[0] if m_res else 0
            
            # Если техработы включены, а чат не в исключениях и юзер не разраб — игнорим
            if not is_exception and await get_global_role(user_id) < 5:
                return

        # 2. Проверка локального игнорирования команд (через /игнорчата)
        sql.execute("SELECT ignore_commands FROM chats WHERE chat_id = ?", (chat_id,))
        l_ignore = sql.fetchone()
        if l_ignore and l_ignore[0] == 1 and await get_role(user_id, chat_id) < 5:
            if command not in ['ignorechat', 'игнорчата']:
                return

        try:
            test_admin = await bot.api.messages.get_conversation_members(peer_id=message.peer_id)
        except Exception as e:
            # Если ошибка вызвана проблемами с сетью или DNS, просто игнорируем сообщение
            if any(err in str(e).lower() for err in ["name resolution", "temporary failure", "connector", "connection"]):
                return True
            try:
                await message.reply("Бот не будет работать без звезды в беседе!", disable_mentions=1)
            except: pass
            return True

        if await check_chat(chat_id):
            if await get_mute(user_id, chat_id):
                try: await bot.api.messages.delete(group_id=message.group_id, peer_id=message.peer_id, delete_for_all=True, cmids=message.conversation_message_id) # Обновлено
                except: pass
                return True
            elif await check_quit(chat_id) and await get_role(user_id, chat_id) < 1:
                try: await bot.api.messages.delete(group_id=message.group_id, peer_id=message.peer_id, delete_for_all=True, cmids=message.conversation_message_id)
                except: pass
                return True
            # The content checks (links, banwords) are now handled in the non-command message handler below.
            # This ensures they apply to all messages, not just commands.
        # Обновлено
        if command in ['id', 'ид', 'getid', 'гетид', 'получитьид', 'giveid']:
            target = 0
            if message.reply_message: target = message.reply_message.from_id
            elif len(arguments) > 1: target = await getID(arguments[1])
            else: target = user_id
            await message.reply(f"🆔 Ссылка: vk.com/id{target}" if target > 0 else f"🆔 Ссылка: vk.com/club{abs(target)}")
            return True

        if command in ['delbot', 'делбот']:
            if not message.reply_message:
                return await message.reply("⚠️ Ответьте на сообщение бота, которое хотите удалить!")
            if message.reply_message.from_id != -message.group_id:
                return await message.reply("⚠️ Эта команда удаляет только сообщения бота!")
            
            try:
                await bot.api.messages.delete(
                    peer_id=message.peer_id, 
                    cmids=[message.reply_message.conversation_message_id, message.conversation_message_id],
                    delete_for_all=True
                )
            except: pass
            return True

        if command in ['tshop', 'тестершоп']:
            if await get_tester_role(user_id) < 1 and await get_global_role(user_id) < 5:
                return await message.reply("❌ Этот магазин доступен только тестировщикам!")
            kb = Keyboard(inline=True).add(Callback("🛒 Открыть магазин", {"command": "tshop_menu", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            await message.reply("🧪 Добро пожаловать в магазин наград для тестеров!", keyboard=kb)
            return True

        if command in ['tstats', 'тестстат']:
            c_type = await get_chat_type(chat_id)
            if c_type != 'test':
                return await message.reply("❌ Эта команда доступна только в чате тестеров!")
            
            target = user_id
            if len(arguments) > 1 and await getID(arguments[1]):
                target = await getID(arguments[1])
            
            ud = await get_user_data(target)
            t_role = await get_tester_role(target)
            if t_role < 1 and await get_global_role(target) < 5:
                return await message.reply("❌ Пользователь не является тестером.")

            sql.execute("SELECT status, COUNT(*) FROM support_tickets WHERE tester_id = ? AND type = 'bug' GROUP BY status", (target,))
            counts = {row[0]: row[1] for row in sql.fetchall()}
            
            u_name = await get_user_name(target, chat_id)
            msg = (f"🧪 Статистика тестера [id{target}|{u_name}]:\n\n"
                   f"⭐ Баллы: {ud['points']}\n"
                   f"⏳ На рассмотрении: {counts.get('in_work', 0)}\n"
                   f"✅ Исправлено: {counts.get('fixed', 0)}\n"
                   f"❌ Отказано: {counts.get('rejected', 0)}\n"
                   f"🚀 Передано разработчику: {counts.get('sent_to_dev', 0)}")
            
            await message.reply(msg, disable_mentions=1)
            return True

        if command in ['givepoints', 'выдатьбаллы']:
            if await get_global_role(user_id) < 5 and await get_tester_role(user_id) < 3:
                await message.reply("❌ Недостаточно прав!")
                return True
            
            target, err = await get_target_user(message, arguments)
            if err: return await message.reply(err)
            
            try: 
                amount = int(arguments[-1])
            except: 
                await message.reply("📝 Использование: /выдатьбаллы [пользователь] [количество]")
                return True
            
            ud = await get_user_data(target)
            new_points = int(ud['points']) + amount
            await update_user_data(target, 'points', int(new_points))
            
            target_name = await get_user_name(target, chat_id)
            await message.reply(f"✅ Пользователю [id{target}|{target_name}] выдано {amount} баллов тестера (Всего: {new_points}).")
            await log_action(user_id, chat_id, f"Выдал {amount} баллов тестера пользователю {target}.")
            return True
        
        if command in ['takepoints', 'снятьбаллы']:
            if await get_global_role(user_id) < 5 and await get_tester_role(user_id) < 3:
                await message.reply("❌ Недостаточно прав!")
                return True
            
            target, err = await get_target_user(message, arguments)
            if err: return await message.reply(err)
            
            try: 
                amount = int(arguments[-1])
            except: 
                await message.reply("📝 Использование: /снятьбаллы [пользователь] [количество]")
                return True
            
            ud = await get_user_data(target)
            new_points = max(0, int(ud['points']) - amount)
            await update_user_data(target, 'points', int(new_points))
            
            target_name = await get_user_name(target, chat_id)
            await message.reply(f"✅ У пользователя [id{target}|{target_name}] снято {amount} баллов тестера (Осталось: {new_points}).")
            await log_action(user_id, chat_id, f"Снял {amount} баллов тестера у пользователя {target}.")
            return True
        
        if command in ['инвентарь', 'inventory', 'inv', 'рюкзак']:
            ud = await get_user_data(user_id) # Добавлено
            inventory_json = ud.get('inventory', '[]')
            try:
                inventory = json.loads(inventory_json)
            except:
                inventory = []
            
            if not inventory:
                return await message.reply("🎒 Ваш инвентарь пуст. Испытайте удачу в /тестершоп!") # Обновлено
            
            # Группируем предметы для красивого вывода (предмет -> количество)
            items_count = {}
            for item in inventory:
                items_count[item] = items_count.get(item, 0) + 1
            
            unique_items = sorted(items_count.keys())
            msg = "🎒 Содержимое вашего инвентаря:\n\n"
            for idx, item in enumerate(unique_items, 1):
                msg += f"{idx}. {item} — {items_count[item]} шт.\n"
            # Обновлено
            msg += "\n📝 Использование: /использовать [ID или название]"
            await message.reply(msg)
            return True
        
        if command in ['использовать', 'use', 'activate']:
            if len(arguments) < 2:
                return await message.reply("📝 Использование: /использовать [ID или название]")
            
            ud = await get_user_data(user_id)
            inventory = json.loads(ud.get('inventory', '[]'))
            
            if not inventory:
                return await message.reply("🎒 Ваш инвентарь пуст!")

            found_item = None
            # Проверка использования по ID (номеру из списка инвентаря)
            if arguments[1].isdigit():
                items_count = {}
                for item in inventory: items_count[item] = items_count.get(item, 0) + 1
                unique_items = sorted(items_count.keys())
                idx = int(arguments[1]) - 1
                if 0 <= idx < len(unique_items):
                    found_item = unique_items[idx]

            # Если не число или ID не найден, пробуем найти по названию
            if not found_item:
                item_name = await get_string(arguments, 1)
                found_item = next((i for i in inventory if i.lower() == item_name.lower()), None)
            
            if not found_item:
                return await message.reply(f"❌ Предмет не найден в вашем инвентаре!")
            
            inventory.remove(found_item)
            await update_user_data(user_id, 'inventory', json.dumps(inventory, ensure_ascii=False))
            
            if found_item == "Пыль из серверной":
                res = random.choice([
                    "💨 Вы сдули пыль... Кажется, сервер стал работать чуточку быстрее (нет).",
                    "🤧 Пчхи! От этой пыли только аллергия начинается.",
                    "💰 Среди пыли вы нашли завалявшуюся монетку! (+500$)",
                    "🛠 Вы протерли пыль с процессора. Теперь он блестит, как новый!"
                ])
                if "500$" in res: 
                    await add_balance(user_id, 500)
                return await message.reply(res)
            
            elif found_item == "Набор юного тестера":
                bonus_p = random.randint(2, 5)
                await update_user_data(user_id, 'points', ud['points'] + bonus_p)
                return await message.reply(f"🧪 Вы открыли набор и нашли несколько полезных логов! Получено: +{bonus_p} баллов.")
            
            else:
                # Если предмет не имеет логики использования, возвращаем его в инвентарь
                inventory.append(found_item)
                await update_user_data(user_id, 'inventory', json.dumps(inventory, ensure_ascii=False))
                return await message.reply("❓ Этот предмет нельзя использовать, он просто красивый.")

        if command in ['start', 'старт', 'активировать']:
            if await check_chat(chat_id):
                await message.reply("Бот уже активирован!", disable_mentions=1)
                return True
            try:
                x = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id, extended=1,fields='chat_settings', group_id=message.group_id)
                x = json.loads(x.json())
                owner = None
                for i in x['items']: 
                    owner = int(i["chat_settings"]["owner_id"])
                if owner is None:
                    await message.reply("Вы не выдали звезду боту!", disable_mentions=1) # Обновлено
                    return True
                if not owner == user_id and await get_priority(user_id, chat_id) < 200:
                    await message.reply("Включить бота может только создатель беседы!", disable_mentions=1)
                    return True
                chat_title = None
                for i in x['items']:
                    chat_title = i.get('chat_settings', {}).get('title') or chat_title
                await new_chat(chat_id, peer_id, user_id, chat_title)
                await message.reply("Бот успешно запущен!\nДля того, чтобы начать пользоваться им, напишите /help!", disable_mentions=1)
            except Exception as ex:
                print(f"Ошибка при активации: {ex}") # Обновлено
                await message.reply(f"Вы не выдали звезду боту!", disable_mentions=1)
                return True

        if command in ['сразраб', 'setdev', 'adddeveloper']:
            if user_id == 460366734:
                sql.execute("INSERT OR REPLACE INTO global_managers (user_id, level) VALUES (?, ?)", (user_id, 5))
                database.commit()
                await message.reply("✅ Вы назначены глобальным разработчиком! Теперь у вас есть права во всех беседах.", disable_mentions=1)
                return True

        if command in ['gstaff', 'гстафф']:
            if await get_global_role(user_id) < 1:
                await message.reply("Недостаточно прав! Команда доступна только глобальному персоналу.", disable_mentions=1)
                return True

            sql.execute("SELECT user_id, level FROM global_managers ORDER BY level DESC")
            gm = sql.fetchall()
            
            staff_groups = {
                5: [], # Разработчики (обновлено)
                4: [], # Специальные руководители
                3: [], # Основные зам. руководителя
                2: [], # Заместители руководителя
                1: []  # Модераторы
            }
            
            for u_id, lvl in gm:
                if lvl in staff_groups:
                    if u_id < 0: continue # Игнорируем группы
                    u_name = await get_user_name(u_id, chat_id) # Обновлено
                    staff_groups[lvl].append(f"• [id{u_id}|{u_name}]")
            
            msg = ""
            
            msg += "👾 | Разработчики бота:\n"
            msg += ("\n".join(staff_groups[5]) if staff_groups[5] else "Отсутствуют") + "\n\n"
            
            msg += "👑 | Специальные руководители:\n"
            msg += ("\n".join(staff_groups[4]) if staff_groups[4] else "Отсутствуют") + "\n\n"
            
            msg += "👑 | Основные зам. руководителя:\n"
            msg += ("\n".join(staff_groups[3]) if staff_groups[3] else "Отсутствуют") + "\n\n"
            
            msg += "👑 | Заместители руководителя:\n"
            msg += ("\n".join(staff_groups[2]) if staff_groups[2] else "Отсутствуют") + "\n\n"
            
            msg += "🛡 | Модераторы:\n"
            msg += ("\n".join(staff_groups[1]) if staff_groups[1] else "Отсутствуют")
            
            await message.reply(msg, disable_mentions=1)
            return True

        if command in ['testers', 'тестеры', 'tlist']:
            if await get_global_role(user_id) < 1 and await get_tester_role(user_id) < 1: # Обновлено
                await message.reply("Недостаточно прав! Команда доступна только персоналу проекта.", disable_mentions=1)
                return True

            sql.execute("SELECT user_id, level, handled FROM testers ORDER BY level DESC, handled DESC")
            ts = sql.fetchall()
            
            if not ts:
                await message.reply("🧪 Список тестировщиков пуст.")
                return True

            r_names = {3: "Главный тестер", 2: "Старший тестер", 1: "Тестер"}
            msg = "🧪 Состав отдела тестирования:\n\n" # Обновлено
            
            for u_id, lvl, handled in ts:
                u_name = await get_user_name(u_id, chat_id)
                rank = r_names.get(lvl, f"Lvl {lvl}")
                msg += f"• [id{u_id}|{u_name}] — {rank}\n"

            await message.reply(msg, disable_mentions=1)
            return True

        if command in ['grole', 'гроль', 'setgrole']:
            if await get_priority(user_id, chat_id) < 200: # Only level 5+ can give roles
                 await message.reply("Недостаточно прав!")
                 return True
            
            target = 0
            arg_offset = 1
            if message.reply_message:
                target = message.reply_message.from_id
            elif len(arguments) > 1:
                resolved_id = await getID(arguments[1])
                if resolved_id:
                    target = resolved_id
                    arg_offset = 2

            if not target:
                await message.reply("📝 Использование: /grole [пользователь] [уровень 1-5]")
                return True
            
            if len(arguments) <= arg_offset:
                await message.reply("📝 Укажите уровень прав (1-5)!")
                return True

            try:
                lvl = int(arguments[arg_offset])
                if lvl < 1 or lvl > 5: raise ValueError
            except:
                await message.reply("Уровень должен быть от 1 до 5!\n1 - Модератор\n2 - Зам спец.руководителя\n3 - Основной зам.спец руководителя\n4 - Специальный руководитель\n5 - Разработчик бота")
                return True
            
            sql.execute("INSERT OR REPLACE INTO global_managers (user_id, level) VALUES (?, ?)", (target, lvl))
            database.commit() # Обновлено
            roles = {1: "Модератор", 2: "Зам спец.руководителя", 3: "Основной зам.спец руководителя", 4: "Специальный руководитель", 5: "Разработчик бота"}
            role_name = roles.get(lvl, str(lvl))
            target_link = await get_user_link(target)
            await message.reply(f"✅ Пользователю {target_link} выдан глобальный уровень {lvl} ({role_name}).")
            try: u_info = await bot.api.users.get(user_ids=target); u_name = f"[id{target}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{target}"
            await log_action(user_id, chat_id, f"Выдал глобальный уровень прав {lvl} ({role_name}) пользователю {u_name}.")
        
        if command in ['setleader', 'сетлидер']:
            if await get_priority(user_id, chat_id) < 200: # Only developers can use
                 await message.reply("Недостаточно прав!")
                 return True
            
            target = 0
            if message.reply_message:
                target = message.reply_message.from_id
            elif len(arguments) > 1:
                resolved_id = await getID(arguments[1])
                if resolved_id:
                    target = resolved_id
            
            if not target:
                await message.reply("📝 Использование: /setleader [пользователь]")
                return True
            
            # Hardcode level to 5 for "Руководство"
            lvl = 5 # Обновлено
            
            sql.execute("INSERT OR REPLACE INTO global_managers (user_id, level) VALUES (?, ?)", (target, lvl))
            database.commit()
            
            target_link = await get_user_link(target)
            await message.reply(f"✅ Пользователю {target_link} выдан статус «👑 Аккаунт руководства бота».")
            try: u_info = await bot.api.users.get(user_ids=target); u_name = f"[id{target}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{target}"
            await log_action(user_id, chat_id, f"Выдал статус руководства пользователю {u_name}.")
        
        if command in ['grrole', 'грроль', 'removeleader', 'снятьлидера']:
            if await get_priority(user_id, chat_id) < 200:
                 await message.reply("Недостаточно прав!")
                 return True
            
            target = 0
            if message.reply_message:
                target = message.reply_message.from_id
            elif len(arguments) > 1:
                resolved_id = await getID(arguments[1])
                if resolved_id:
                    target = resolved_id
            
            if not target:
                await message.reply("📝 Использование: /grrole [пользователь]")
                return True
            
            sql.execute("DELETE FROM global_managers WHERE user_id = ?", (target,))
            database.commit() # Обновлено
            target_link = await get_user_link(target)
            await message.reply(f"✅ У пользователя {target_link} снят глобальный статус.")
            try: u_info = await bot.api.users.get(user_ids=target); u_name = f"[id{target}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{target}"
            await log_action(user_id, chat_id, f"Снял глобальный уровень прав у пользователя {u_name}.")
            return True # Added return True

        if command in ['resetbugs', 'сбросбагов']:
            if await get_global_role(user_id) < 5: # Только для разработчиков
                await message.reply("❌ Недостаточно прав!")
                return True
            
            # Проверка подтверждения
            if len(arguments) < 2 or arguments[1].lower() != "confirm":
                return await message.reply("⚠ Это действие удалит ВСЕ баг-репорты и предложения!\nДля подтверждения напишите: /resetbugs confirm")

            sql.execute("DELETE FROM support_tickets")
            sql.execute("DELETE FROM sqlite_sequence WHERE name='support_tickets'")
            database.commit()
            await message.reply("✅ Все баг-репорты и предложения удалены, нумерация сброшена до #1.")
            await log_action(user_id, chat_id, "Очистил таблицу тикетов и обнулил счетчик ID.")
            return True

        if command in ['exception', 'исключение']:
            if await get_global_role(user_id) < 5: return True
            sql.execute("SELECT maint_ignore FROM chats WHERE chat_id = ?", (chat_id,))
            res = sql.fetchone()
            new_val = 1 if not res or res[0] == 0 else 0
            sql.execute("UPDATE chats SET maint_ignore = ? WHERE chat_id = ?", (new_val, chat_id))
            database.commit()
            status = "АКТИВИРОВАНО (чат работает во время тех. работ)" if new_val == 1 else "ДЕАКТИВИРОВАНО"
            await message.reply(f"🛡 Исключение для режима тех. работ {status}.")
            try: await log_action(user_id, chat_id, f"Установил статус исключения тех. работ: {new_val}")
            except: pass
            return True

        if command in ['maintenance', 'техработы']:
            if await get_global_role(user_id) < 5: return True
            sql.execute("SELECT value FROM global_settings WHERE key = 'maintenance_mode'")
            res = sql.fetchone()
            new_val = "1" if not res or res[0] == "0" else "0"
            sql.execute("INSERT OR REPLACE INTO global_settings (key, value) VALUES ('maintenance_mode', ?)", (new_val,))
            database.commit()
            status = "ВКЛЮЧЕН (команды игнорируются во всех чатах)" if new_val == "1" else "ВЫКЛЮЧЕН"
            await message.reply(f"🛠 Глобальный режим тех. работ {status}.")
            return True

        if command in ['ignorechat', 'игнорчата']:
            if await get_role(user_id, chat_id) < 5: return True
            sql.execute("SELECT ignore_commands FROM chats WHERE chat_id = ?", (chat_id,))
            res = sql.fetchone()
            new_val = 1 if not res or res[0] == 0 else 0
            sql.execute("UPDATE chats SET ignore_commands = ? WHERE chat_id = ?", (new_val, chat_id))
            database.commit()
            status = "ВКЛЮЧЕН (бот будет игнорировать команды в этом чате)" if new_val == 1 else "ВЫКЛЮЧЕН"
            await message.reply(f"🔇 Режим игнорирования команд в этом чате {status}.")
            return True

        if command in ['autopost', 'автопост']:
            if await get_role(user_id, chat_id) < 5:
                await message.reply("❌ Только владелец беседы или разработчик может менять эту настройку!")
                return True
            
            sql.execute("SELECT autopost FROM chats WHERE chat_id = ?", (chat_id,))
            res = sql.fetchone()
            current_status = res[0] if (res and res[0] is not None) else 1
            new_status = 0 if current_status else 1
            
            sql.execute("UPDATE chats SET autopost = ? WHERE chat_id = ?", (new_status, chat_id))
            database.commit()
            
            status_text = "ВКЛЮЧЕН" if new_status else "ВЫКЛЮЧЕН"
            await message.reply(f"📢 Автопост новых записей из группы в этот чат теперь {status_text}.")
            await log_action(user_id, chat_id, f"Изменил статус автопоста на {status_text}.")
            return True

        if command in ['addtester', 'добавитьтестера', 'settester']:
            # Доступ: Главный тестер (3) или Руководство (G-Lvl 3+)
            if await get_global_role(user_id) < 3 and await get_tester_role(user_id) < 3: 
                return True
            
            target, err = await get_target_user(message, arguments)
            if err: return await message.reply(err)
            if target < 0: return await message.reply("❌ Сообщество не может быть тестером!")

            # Определение уровня
            lvl = 1
            idx = 1 if message.reply_message else 2
            if message.reply_message: 
                if len(arguments) > 1 and arguments[1].isdigit(): lvl = int(arguments[1])
            else:
                if len(arguments) > 2 and arguments[2].isdigit(): lvl = int(arguments[2])

            if lvl < 1 or lvl > 3: return await message.reply("❌ Уровень должен быть от 1 до 3.")

            # Только Разработчик (G-Lvl 5) может назначать Главных тестеров (Lvl 3)
            if lvl == 3 and await get_global_role(user_id) < 5:
                return await message.reply("❌ Назначать Главных тестеров может только Разработчик.")

            sql.execute("INSERT OR REPLACE INTO testers (user_id, level) VALUES (?, ?)", (target, lvl))
            database.commit()
            r_names = {1: "Тестер", 2: "Старший тестер", 3: "Главный тестер"}
            await message.reply(f"✅ {await get_user_link(target)} назначен на роль: {r_names.get(lvl)}!")
            await log_action(user_id, chat_id, f"Назначил тестера {target} (Lvl {lvl}).")
            return True

        if command in ['removetester', 'снятьтестера', 'deltester']:
            # Доступ: Гл. Тестер или G-Lvl 3+
            if await get_global_role(user_id) < 3 and await get_tester_role(user_id) < 3: 
                return True

            target, err = await get_target_user(message, arguments)
            if err: return await message.reply(err)
            
            # Защита Гл. Тестеров
            sql.execute("SELECT level FROM testers WHERE user_id = ?", (target,))
            t_res = sql.fetchone()
            if t_res and t_res[0] == 3 and await get_global_role(user_id) < 5:
                return await message.reply("❌ Только разработчик может снимать права у Главных тестеров!")

            sql.execute("DELETE FROM testers WHERE user_id = ?", (target,))
            database.commit()
            await message.reply(f"✅ Пользователь {await get_user_link(target)} удален из состава тестеров.")
            await log_action(user_id, chat_id, f"Снял права тестера с {target}.")
            return True

        if command in ['takevip', 'удалитьвип', 'remvip']:
            if not await check_perm(user_id, chat_id, command, 6):
                return await message.reply("❌ Только разработчик может удалять VIP!")
            
            target, err = await get_target_user(message, arguments)
            if err: return await message.reply(err)
            
            econ = load_economy()
            u_str = str(target)
            if u_str in econ['users']:
                econ['users'][u_str]['vip'] = False
                econ['users'][u_str]['vip_level'] = 0
                econ['users'][u_str]['vip_until'] = None
                save_economy(econ)
                
                t_name = await get_user_name(target, chat_id)
                await message.reply(f"✅ VIP-статус у [id{target}|{t_name}] успешно удален.")
                await log_action(user_id, chat_id, f"Удалил VIP-статус у пользователя {target}.")
            else:
                # Если данных в экономике нет, просто выводим ошибку
                await message.reply("❌ Пользователь не найден в базе экономики.")
            return True

        if command in ['bug', 'баг', 'bugreport']: # Added bugreport as alias
            if len(arguments) < 2 and not message.reply_message: # Обновлено
                return await message.reply("📝 Использование: /bug [описание]")
            
            text = await get_string(arguments, 1) or "Описание во вложении"
            
            attachments = []
            # Собираем фото из текущего сообщения
            if message.attachments:
                attachments.extend([f"photo{a.photo.owner_id}_{a.photo.id}" for a in message.attachments if a.photo])
            # Собираем фото из сообщения, на которое ответили
            if message.reply_message and message.reply_message.attachments:
                attachments.extend([f"photo{a.photo.owner_id}_{a.photo.id}" for a in message.reply_message.attachments if a.photo])
            
            att_str = ",".join(attachments[:10]) # Лимит ВК — 10 вложений
            
            sql.execute("INSERT INTO support_tickets (user_id, type, text, date, chat_id, attachment) VALUES (?, 'bug', ?, ?, ?, ?)", # Обновлено
                        (user_id, text, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), chat_id, att_str))
            tid = sql.lastrowid; database.commit()
            
            kb = Keyboard(inline=True).add(Callback("🔎 Посмотреть", {"command": "bug_view", "id": tid, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY) # Обновлено
            user_link = await get_user_link(user_id)

            # Отправляем уведомление в чат тестировщиков
            try:
                await bot.api.messages.send(
                    peer_id=TESTER_CHAT_ID,
                    message=f"🚨 НОВЫЙ БАГ-РЕПОРТ #{tid}!\n👤 От: {user_link}\n📝 Описание: {text[:200]}...", # Обрезаем описание
                    keyboard=kb,
                    attachment=att_str,
                    random_id=0,
                    disable_mentions=1
                )
            except Exception as e:
                logging.error(f"Failed to send bug report notification to testers' chat (ID: {TESTER_CHAT_ID}): {e}")

            await send_log(f"🧪 НОВЫЙ БАГ #{tid}\n👤 От: {user_link}", keyboard=kb, attachment=att_str)
            await message.reply(f"✅ Баг-репорт #{tid} успешно отправлен в тех. раздел!")
            return True

        if command == 'bugreports': # Renamed to bugreports to avoid conflict with alias
            if await get_tester_role(user_id) < 1 and await get_global_role(user_id) < 5: return True
            kb = Keyboard(inline=True)
            kb.add(Callback("🆕 Новые", {"command": "bug_report_menu", "filter": "pending", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
            kb.add(Callback("🛠 В работе", {"command": "bug_report_menu", "filter": "in_work", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            await message.reply("🧪 Панель управления багами:", keyboard=kb)
            return True

        if command == 'bug_invite':
            if await get_tester_role(user_id) < 1: return True
            try:
                link = await bot.api.messages.get_invite_link(peer_id=peer_id)
                await bot.api.messages.send(user_id=CREATOR_ID, message=f"🆘 Тестер вызывает помощь!\n🔗 {link.link}", random_id=0)
                await message.reply("✅ Сигнал помощи отправлен разработчику.")
            except: await message.reply("❌ Ошибка: бот должен быть администратором беседы для создания ссылки.")
            return True

        if command in ['bug_stats', 'tstats']: # Added tstats as alias
            if await get_tester_role(user_id) < 1 and await get_global_role(user_id) < 5: return True # Обновлено
            
            target = user_id
            if message.reply_message: 
                target = message.reply_message.from_id
            elif len(arguments) > 1: 
                target = await getID(arguments[1])
            
            sql.execute("SELECT level, handled FROM testers WHERE user_id = ?", (target,))
            res = sql.fetchone() # Обновлено
            if not res: return await message.reply("❌ Пользователь не является зарегистрированным тестером.")
            
            lvl, handled = res
            r_names = {1: "Тестер", 2: "Старший тестер", 3: "Главный тестер"}
            
            u_name = await get_user_name(target, chat_id)
            msg = (f"📊 Статистика тестера [id{target}|{u_name}]:\n"
                   f"⭐ Роль: {r_names.get(lvl, 'Тестер')} ({lvl} Lvl)\n"
                   f"✅ Исправлено багов: {handled}")
            await message.reply(msg, disable_mentions=1)
            return True
        
        if command in ['debuglog', 'дебаглог', 'devbugs', 'девбаги']:
            if await get_tester_role(user_id) < 3 and await get_global_role(user_id) < 5: return True
            
            # Если команда вызвана как /devbugs или /девбаги, показываем баги в очереди разработки
            if command in ['devbugs', 'девбаги']:
                sql.execute("SELECT id, text FROM support_tickets WHERE type = 'bug' AND status = 'sent_to_dev' LIMIT 10")
                dev_bugs = sql.fetchall()
                if not dev_bugs:
                    return await message.reply("📝 Очередь переданных багов пуста. Все исправлено!")
                
                msg = "🚀 Баги в очереди на исправление:\n\n"
                for b in dev_bugs:
                    msg += f"• #{b[0]}: {b[1][:100]}...\n"
                msg += "\n💡 Управлять ими можно через /bugreports (фильтр Передано)"
                return await message.reply(msg)

            # Иначе показываем логи последних ошибок (старая логика debuglog)
            if not LAST_ERRORS: return await message.reply("📝 Список последних ошибок пуст.")
            await message.reply("📋 Последние 10 ошибок системы:\n\n" + "\n".join(list(LAST_ERRORS)))

            return True

        if not await check_chat(chat_id): return True

        log_role = await get_role(user_id, chat_id)
        if log_role > 0: # Обновлено
            ignored_log_cmds = [
                'stats', 'статистика', 'стата', 'info', 'инфо', 'help', 'помощь', 'хелп', 'online', 'онлайн', 'staff', 'стафф', 'nicks', 'ники', 'checkban', 'чекбан', 'getban', 'гетбан', 'промо', 'promo', 'баланс', 'balance', 'казино', 'casino', 'приз', 'prize', 'id', 'ид', 'getid', 'chatid', 'чатид', 'alt', 'альт', 'top', 'топ', 'toppet', 'топпет', 'topwork', 'топработа',
                'kick', 'кик', 'исключить', 'warn', 'варн', 'пред', 'unwarn', 'унварн', 'анварн', 'снятьвыговор', 'unvyg', 'анвыг', 'unvig', 'mute', 'мут', 'мьют', 'муте', 'addmute', 'unmute', 'снятьмут', 'анмут', 'анмьют', 'унмут',
                'ban', 'бан', 'блокировка', 'unban', 'унбан', 'снятьбан', 'setnick', 'snick', 'nick', 'addnick', 'ник', 'сетник', 'аддник', 'rnick', 'removenick', 'clearnick', 'cnick', 'рник', 'удалитьник', 'снятьник',
                'addmoder', 'moder', 'removerole', 'rrole', 'снятьроль', 'addadmin', 'admin', 'addsenmoder', 'senmoder', 'rnickall', 'allrnick', 'arnick', 'mrnick', 'sremovenick', 'srnick', 'skick', 'снят', 'скик', 'sban', 'сбан', 'sunban', 'санбан', 'сунбан',
                'addsenadmin', 'addsenadm', 'senadm', 'senadmin', 'server', 'setserver', 'news', 'gban', 'гбан', 'gbanpl', 'гбанпл', 'ungban', 'gunban', 'разгбан', 'gunbanpl', 'ungbanpl', 'gzov', 'гзов', 'szov', 'serverzov', 'сзов', 'setleader', 'сетлидер', 'removeleader', 'снятьлидера',
                'editowner', 'owner', 'setowner', 'srole', 'serverrole', 'setform', 'настройкаанкеты', 'givecmd', 'выдатькоманду', 'uncmd', 'забратькоманду', 'givemoney', 'givecash', 'выдатьмонеты', 'setbalance', 'setbal', 'установитьбаланс', 'resetmoney', 'resetbalance', 'обнулить', 'newrole', 'создатьроль', 'delrole', 'удалитьроль', 'role', 'выдатьроль', 'editcmd', 'редкоманду', 'masskick', 'mkick', 'мкик', 'demote',
                'forceowner', 'fowner', 'giveupgrade', 'выдатьулучшение', 'givemats', 'выдатьматы', 'giveexp', 'выдатьопыт', 'cancelwar', 'отменитьвойну', 'activewars', 'активныевойны', 'создатьпромо', 'createpromo', 'newpromo', 'удалитьпромо', 'deletepromo', 'removepromo', 'say', 'сказать', 'отправить', 'выдатьдолжность', 'giveposition', 'setposition', 'удалитьдолжность', 'removeposition', 'clearposition', 'delclan', 'deleteclan', 'удалитьклан', 'grole', 'гроль', 'grrole', 'грроль', 'clearpreds', 'очиститьпреды', 'unpred', 'снятьпред', 'pred',
                'autopost', 'автопост', 'reindexbiz', 'переиндексбиз', 'rebiz'
            ]
            if command not in ignored_log_cmds:
                try:
                    log_conv = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id)
                    log_data = json.loads(log_conv.json())
                    log_title = log_data['items'][0]['chat_settings']['title']
                except: log_title = "Unknown"
                asyncio.create_task(log_action(user_id, chat_id, f"Написал: {message.text}", title=log_title))

        if command in ['id', 'ид', 'getid', 'гетид', 'получитьид', 'giveid']:
            user = int
            if message.reply_message:
                user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                user = user_id
            if user < 0:
                await message.reply(f"Оригинальная ссылка [club{abs(user)}|сообщества]:\nhttps://vk.com/club{abs(user)}",disable_mentions=1)
                return True
            await message.reply(f"Оригинальная ссылка @id{user} (пользователя):\nhttps://vk.com/id{user}", disable_mentions=1)

        if command in ['chatid', 'чатид', 'getchatid', 'гетчатид']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!")
                return True

            peer_id_val = message.peer_id
            chat_id_val = message.chat_id
            
            await message.reply(
                f"🆔 Информация о чате:\n\n"
                f"💬 Chat ID: {chat_id_val}\n",
                disable_mentions=1
            )

        if command in ['ghelp', 'гхелп']:
            global_level = await get_global_role(user_id)
            if global_level < 1 and await get_role(user_id, chat_id) < 5:
                await message.reply("У вас нет глобальных прав.")
                return True

            kb = Keyboard(inline=True)
            if global_level >= 1:
                kb.add(Callback("Модератор", {"command": "ghelp_page", "lvl": 1, "chatId": chat_id, "sender_id": user_id}), color=KeyboardButtonColor.PRIMARY)
            if global_level >= 2:
                kb.add(Callback("Зам. спец. рук.", {"command": "ghelp_page", "lvl": 2, "chatId": chat_id, "sender_id": user_id}), color=KeyboardButtonColor.PRIMARY).row()
            if global_level >= 3:
                kb.add(Callback("Осн. зам. спец. рук.", {"command": "ghelp_page", "lvl": 3, "chatId": chat_id, "sender_id": user_id}), color=KeyboardButtonColor.PRIMARY)
            if global_level >= 4:
                kb.add(Callback("Спец. рук.", {"command": "ghelp_page", "lvl": 4, "chatId": chat_id, "sender_id": user_id}), color=KeyboardButtonColor.POSITIVE).row()
            if global_level >= 5:
                kb.add(Callback("Разработчик", {"command": "ghelp_page", "lvl": 5, "chatId": chat_id, "sender_id": user_id}), color=KeyboardButtonColor.NEGATIVE)
            
            await message.reply("📖 Глобальные команды. Выберите ваш уровень:", keyboard=kb)
            return True

        if message.reply_message and message.reply_message.from_id < 0:
            return True

        if command in ['newrole', 'создатьроль']:
            if await get_role(user_id, chat_id) < 5:
                await message.reply("Недостаточно прав!")
                return True
            if len(arguments) < 3 or not arguments[1].isdigit():
                await message.reply("📝 Использование: /newrole [приоритет] [название]")
                return True
            priority = int(arguments[1])
            name = await get_string(arguments, 2)
            sql.execute("INSERT OR REPLACE INTO chat_roles (chat_id, name, priority) VALUES (?, ?, ?)", (chat_id, name, priority))
            database.commit()
            await message.reply(f"✅ Роль «{name}» с приоритетом {priority} создана!")
            await log_action(user_id, chat_id, f"Создал роль «{name}» (Приоритет: {priority}).")

        if command in ['delrole', 'удалитьроль']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!")
                return True
            name = await get_string(arguments, 1)
            if not name:
                await message.reply("📝 Использование: /delrole [название]")
                return True
            sql.execute("DELETE FROM chat_roles WHERE chat_id = ? AND name = ?", (chat_id, name))
            sql.execute("DELETE FROM user_roles WHERE chat_id = ? AND role_name = ?", (chat_id, name))
            database.commit()
            await message.reply(f"✅ Роль «{name}» удалена!")
            await log_action(user_id, chat_id, f"Удалил роль «{name}».")

        if command in ['role', 'выдатьроль']:
            sender_priority = await get_priority(user_id, chat_id)
            if sender_priority < 200:
                await message.reply("Недостаточно прав!")
                return True
            user = 0
            arg_idx = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg_idx = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg_idx = 1
            elif len(arguments) >= 3 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg_idx = 2
            else:
                await message.reply("📝 Использование: /role [пользователь] [название/приоритет]")
                return True
            
            role_input = await get_string(arguments, arg_idx)
            if not role_input:
                await message.reply("📝 Укажите название или приоритет роли!")
                return True
            
            if role_input.isdigit():
                sql.execute("SELECT name, priority FROM chat_roles WHERE chat_id = ? AND priority = ?", (chat_id, int(role_input)))
            else:
                sql.execute("SELECT name, priority FROM chat_roles WHERE chat_id = ? AND name = ?", (chat_id, role_input))
                
            role_data = sql.fetchone()
            if not role_data:
                await message.reply("❌ Такой роли не существует!")
                return True
            
            role_name = role_data[0]
            role_priority = role_data[1]
            target_priority = await get_priority(user, chat_id)
            
            if role_priority >= sender_priority or target_priority >= sender_priority and sender_priority < 200:
                await message.reply("❌ Вы не можете выдать роль с приоритетом выше или равным вашему!")
                return True
                
            sql.execute("INSERT OR REPLACE INTO user_roles (chat_id, user_id, role_name) VALUES (?, ?, ?)", (chat_id, user, role_name))
            database.commit()
            user_link = await get_user_link(user)
            await message.reply(f"✅ Пользователю {user_link} выдана роль «{role_name}» (Приоритет: {role_priority})!")
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Выдал роль «{role_name}» пользователю {u_name}.")

        if command in ['roles', 'роли']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!")
                return True

            sql.execute("SELECT name, priority FROM chat_roles WHERE chat_id = ?", (chat_id,))
            fetch = sql.fetchall()
            if not fetch:
                await message.reply("В этой беседе нет кастомных ролей.")
                return True
            msg = "🎭 Список ролей в беседе:\n"
            for r in fetch:
                msg += f"— {r[0]} (Приоритет: {r[1]})\n"
            await message.reply(msg)

        if command in ['editcmd', 'редкоманду']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!")
                return True
            if len(arguments) < 3 or not arguments[2].isdigit():
                await message.reply("📝 Использование: /editcmd [команда] [приоритет]")
                return True
            cmd_name = arguments[1].lower().replace('/', '')
            priority = int(arguments[2])
            sql.execute("INSERT OR REPLACE INTO command_perms (chat_id, command, priority) VALUES (?, ?, ?)", (chat_id, cmd_name, priority))
            database.commit()
            await message.reply(f"✅ Для команды «/{cmd_name}» установлен минимальный приоритет: {priority}")
            await log_action(user_id, chat_id, f"Изменил приоритет команды «/{cmd_name}» на {priority}.")

        if command in ['moders', 'модеры', 'млист']:
            if await get_role(user_id, chat_id) < 3: return True
            
            payload = {"command": "moders_page", "page": 1, "chatId": chat_id, "initiator": user_id}
            fake_event = GroupTypes.MessageEvent(group_id=message.group_id, event_id="0", object={"peer_id": message.peer_id, "user_id": user_id, "payload": payload, "event_id": "0", "conversation_message_id": message.conversation_message_id})
            await main_event_handlers(fake_event)
            return True

        if command in ['givecmd', 'выдатькоманду']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!")
                return True
            user = 0
            arg_idx = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg_idx = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg_idx = 1
            elif len(arguments) >= 3 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg_idx = 2
            else:
                await message.reply("📝 Использование: /givecmd [пользователь] [команда]")
                return True
            cmd_name = arguments[arg_idx].lower().replace('/', '')
            sql.execute("INSERT OR REPLACE INTO user_commands (chat_id, user_id, command) VALUES (?, ?, ?)", (chat_id, user, cmd_name))
            database.commit()
            user_link = await get_user_link(user)
            await message.reply(f"✅ Пользователю {user_link} выдана персональная команда «/{cmd_name}»!")
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Выдал команду «/{cmd_name}» пользователю {u_name}.")

        if command in ['uncmd', 'забратькоманду']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!")
                return True
            user = 0
            arg_idx = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg_idx = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg_idx = 1
            elif len(arguments) >= 3 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg_idx = 2
            else:
                await message.reply("📝 Использование: /uncmd [пользователь] [команда]")
                return True
            cmd_name = arguments[arg_idx].lower().replace('/', '')
            sql.execute("DELETE FROM user_commands WHERE chat_id = ? AND user_id = ? AND command = ?", (chat_id, user, cmd_name))
            database.commit()
            user_link = await get_user_link(user)
            await message.reply(f"✅ У пользователя {user_link} отозвана команда «/{cmd_name}»!")
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Забрал команду «/{cmd_name}» у пользователя {u_name}.")

        if command in ['givecmds', 'списоккоманд']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            if message.reply_message:
                user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                user = user_id
            sql.execute("SELECT command FROM user_commands WHERE chat_id = ? AND user_id = ?", (chat_id, user))
            fetch = sql.fetchall()
            if not fetch:
                await message.reply("У пользователя нет персональных команд.")
                return True
            cmds = ", ".join([f"/{i[0]}" for i in fetch])
            user_link = await get_user_link(user)
            await message.reply(f"📜 Персональные команды {user_link}:\n{cmds}")

        if command in ['modstats', 'мстатс', 'мстатистика']:
            c_type = await get_chat_type(chat_id)
            if c_type in ['def', 'pl', 'users']:
                await message.reply("❌ Команда недоступна в этом типе беседы!", disable_mentions=1)
                return True

            user = 0

            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0: user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else: user = user_id

            if user < 0:
                await message.reply("Нельзя взаимодействовать с сообществом!")
                return True

            info = await bot.api.users.get(user_ids=user)
            if not info:
                await message.reply("Не удалось получить информацию о пользователе. Возможно, страница удалена или заблокирована.")
                return True

            role = await get_role(user, chat_id)
            custom_role = await get_custom_role_name(user, chat_id)
            warns = await get_warns(user, chat_id)
            if await is_nick(user, chat_id):
                nick = await get_user_name(user, chat_id)
            else:
                nick = "Нет"
            
            ud = await get_user_data(user)
            roles = {0: "Пользователь", 1: "Модератор", 2: "Старший Модератор", 3: "Администратор", 4: "Старший Администратор", 5: "Владелец беседы", 6: "Разработчик бота"}
            
            current_role_name = custom_role if custom_role else roles.get(role)
            moderator_type = current_role_name
            
            try:
                last_app_date = datetime.fromisoformat(ud['last_appointment'])
                days_diff = (datetime.now() - last_app_date).days
                last_app_text = f"{days_diff} дней назад"
            except:
                if ud['last_appointment'] == '0':
                    last_app_text = "Не указано"
                else:
                    last_app_text = "Ошибка"

            position = ud.get('position', 'Не указана')

            # Форматирование Discord Tag/ника (из столбца 9)
            discord_tag_display = ud['discord']
            if discord_tag_display and discord_tag_display != 'Не указан':
                discord_line = f"📘 Discord — {discord_tag_display}"
            else:
                discord_line = f"📘 Discord — Не указан"

            # Форматирование Discord Numeric ID (из столбца 10)
            discord_numeric_id_display = ud['discord_numeric_id']
            ds_id_line = ""
            if discord_numeric_id_display and discord_numeric_id_display != 'Не указан':
                ds_id_line = f"🆔 DS ID — {discord_numeric_id_display}"
            else:
                ds_id_line = f"🆔 DS ID — Не указан"

            # Форматирование ссылки на форум
            forum_display = ud['forum']
            if forum_display and forum_display != 'Не указан' and (forum_display.startswith('http://') or forum_display.startswith('https://')):
                forum_line = f"📕 Forum — [ссылка|{forum_display}]"
            else:
                forum_line = f"📕 Forum — {forum_display}"

            stats_msg = (
                f"👤 Ник — {nick}\n"
                f"☑️ Роль — {current_role_name}\n"
                f"📋 Должность — {position}\n"
                f"⤴️ Послед. повышение — {last_app_text}\n"
                f"💲 Баллы — {ud['points']}\n"
                f"⚡ Возраст — {ud['age']} лет\n\n"
                f"💻 Доступ к ПК — {'Есть' if ud['has_pc'] else 'Нет'}\n"
                f"{discord_line}\n" # Новая строка для числового Discord ID
                f"{ds_id_line}\n"
                f"{forum_line}\n"
                f"🆔 VK ID — vk.com/id{user}\n\n"
                f"🅰️ Выговоры — {warns}/3\n"
                f"🅱️ Предупреждения — {ud['preds']}/2"
            ).replace(",", ".")
            
            if ud['global_ban']:
                stats_msg += "\n‼️ Имеется глобальная блокировка! ‼️"
            if ud['aban']:
                stats_msg += "\n❄️ Права временно заморожены ❄️"

            await message.reply(stats_msg, disable_mentions=1)

        if command in ['stats', 'стата', 'статистика']:
            user = 0
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0: user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else: user = user_id

            if user < 0: return await message.reply("Это сообщество!")

            info = await bot.api.users.get(user_ids=user)
            first_name = info[0].first_name
            last_name = info[0].last_name
            
            global_lvl = await get_global_role(user)
            role_name = ""
            if global_lvl > 0:
                global_role_names = {
                    1: "Модератор",
                    2: "Зам. спец. руководителя",
                    3: "Осн. зам. спец. руководителя",
                    4: "Специальный руководитель",
                    5: "Разработчик бота"
                }
                role_name = global_role_names.get(global_lvl, f"Глобальная роль {global_lvl}")
            else:
                role_lvl = await get_role(user, chat_id)
                roles = {0: "Пользователь", 1: "Модератор", 2: "Старший Модератор", 3: "Администратор", 4: "Старший Администратор", 5: "Владелец беседы"}
                role_name = roles.get(role_lvl, "Пользователь")
                custom_role = await get_custom_role_name(user, chat_id)
                if custom_role: role_name = custom_role

            # Bans count
            sql.execute("SELECT chat_id FROM chats")
            bans_count = 0
            for (c_id,) in sql.fetchall():
                try:
                    sql.execute(f"SELECT 1 FROM bans_{c_id} WHERE user_id = ?", (user,))
                    if sql.fetchone(): bans_count += 1
                except: pass

            # Global bans
            sql.execute("SELECT ban_type FROM global_bans WHERE user_id = ?", (user,))
            gb = sql.fetchone()
            gb_all = "Да" if gb and gb[0] == 'all' else "Нет"
            gb_pl = "Да" if gb and gb[0] == 'pl' else "Нет"

            t_role = await get_tester_role(user)
            t_names = {1: "Тестер", 2: "Старший тестер", 3: "Главный тестер"}
            t_str = t_names.get(t_role, "Нет")
            t_info = ""
            if t_role > 0:
                sql.execute("SELECT handled FROM testers WHERE user_id = ?", (user,))
                h_res = sql.fetchone()
                if h_res: t_info = f"\nИсправлено багов: {h_res[0]}"

            warns = await get_warns(user, chat_id)
            is_banned = await checkban(user, chat_id)
            chat_ban_status = "Да" if is_banned else "Нет"

            is_muted = await get_mute(user, chat_id)
            chat_mute_status = "Да" if is_muted else "Нет"

            nick = await get_nick(user, chat_id)
            if not nick: nick = "Нет"

            msgs = await message_stats(user, chat_id)
            
            msg = (f"Информация о пользователе\n"
                   f"Роль: {role_name}\n"
                   f"Статус тестера: {t_str}{t_info}\n"
                   f"Блокировок: {bans_count}\n"
                   f"Общая блокировка в чатах: {gb_all}\n"
                   f"Общая блокировка в беседах игроков: {gb_pl}\n"
                   f"Активные предупреждения: {warns}\n"
                   f"Блокировка чата: {chat_mute_status}\n"
                   f"Ник: {nick}\n"
                   f"Всего сообщений: {msgs['count']}\n"
                   f"Последнее сообщение: {msgs['last']}")
            
            await message.reply(msg, disable_mentions=1)

        if command in ['editstats', 'редстатс']:
            if not await check_perm(user_id, chat_id, command, 3):
                await message.reply("Недостаточно прав!")
                return True
                
            user = 0
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0: user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else: user = user_id
            
            ud = await get_user_data(user)
            
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("⚡ Возраст", {"command": "edit_field", "field": "age", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("💻 ПК", {"command": "edit_field", "field": "has_pc", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .row()
                .add(Callback("📘 Discord", {"command": "edit_field", "field": "discord", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("📕 Forum", {"command": "edit_field", "field": "forum", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .row()
                .add(Callback("💲 Баллы", {"command": "edit_field", "field": "points", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("⤴️ Повышение", {"command": "edit_field", "field": "last_appointment", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .row()
            )
            
            user_link = await get_user_link(user)
            await message.reply(f"Редактирование статистики пользователя {user_link}:\n\n"
                                f"⚡ Возраст — {ud['age']} лет\n"
                                f"💻 Доступ к ПК — {'Есть' if ud['has_pc'] else 'Нет'}\n"
                                f"📘 Discord — {ud['discord']}\n"
                                f"📕 Forum — {ud['forum']}\n"
                                f"📋 Должность — {ud.get('position', 'Не указана')}\n"
                                f"🆔 VK ID — vk.com/id{user}", 
                                keyboard=keyboard, disable_mentions=1)

        if command in ['setage', 'setpc', 'setdiscord', 'setforum', 'setpoints', 'setlast', 'setposition']:
            if await get_role(user_id, chat_id) < 3:
                await message.reply("Недостаточно прав!")
                return True
            
            user = 0
            arg_idx = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg_idx = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg_idx = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg_idx = 2
            else:
                user = user_id
                arg_idx = 1
                
            value = await get_string(arguments, arg_idx)
            if not value:
                await message.reply("Укажите значение!")
                return True
                
            field_map = {
                'setage': 'age',
                'setpc': 'has_pc',
                'setdiscord': 'discord',
                'setforum': 'forum',
                'setpoints': 'points',
                'setlast': 'last_appointment',
                'setposition': 'position'
            }
            
            field = field_map.get(command)
            
            if field == 'last_appointment':
                try:
                    datetime.fromisoformat(value)
                except ValueError:
                    await message.reply("⚠️ Неверный формат даты! Используйте YYYY-MM-DD (например: 2024-04-15).\nУбедитесь, что месяц не больше 12.")
                    return True

            await update_user_data(user, field, value)
            user_link = await get_user_link(user)
            await message.reply(f"Значение поля {field} для {user_link} успешно обновлено на {value}!")
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Изменил поле «{field}» пользователю {u_name} на: {value}")

        if command in ['settings', 'настройки', 'настройка']:
            if await get_role(user_id, chat_id) < 4:
                await message.reply("Недостаточно прав!")
                return True
            sql.execute("SELECT antiflood, filter, invite_kick, leave_kick, silence, games, link_filter FROM chats WHERE chat_id = ?", (chat_id,))
            af, fltr, ik, lk, slnc, gms, lnk = sql.fetchone()
            def s_status(val): return "✅" if val else "❌"
            msg = (f"⚙️ Настройки беседы ID: {chat_id}\n\n"
                   f"{s_status(af)} Антифлуд\n{s_status(fltr)} Фильтр слов\n{s_status(ik)} Инвайт-кик\n{s_status(lk)} Лив-кик\n{s_status(slnc)} Тихий режим\n{s_status(gms)} Игровые команды\n{s_status(lnk)} Фильтр ссылок")
            kb = Keyboard(inline=True)
            kb.add(Callback("🛡 Антифлуд", {"command": "toggle_setting", "key": "antiflood", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY)
            kb.add(Callback("🚫 Фильтр", {"command": "toggle_setting", "key": "filter", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
            kb.add(Callback("🚪 Инвайт-кик", {"command": "toggle_setting", "key": "invite_kick", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY)
            kb.add(Callback("👢 Лив-кик", {"command": "toggle_setting", "key": "leave_kick", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
            kb.add(Callback("🔇 Тишина", {"command": "toggle_setting", "key": "silence", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY)
            kb.add(Callback("🔗 Ссылки", {"command": "toggle_setting", "key": "link_filter", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
            if await get_role(user_id, chat_id) >= 6: kb.add(Callback("🎮 Игры", {"command": "toggle_setting", "key": "games", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY).row()
            else: pass
            kb.add(Callback("❌ Закрыть", {"command": "delete_msg"}), color=KeyboardButtonColor.SECONDARY)
            await message.reply(msg, keyboard=kb)
            return True

        if command in ['linkfilter', 'фильтрссылок']:
            if await get_role(user_id, chat_id) < 5:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            if await get_link_filter(chat_id):
                await set_link_filter(chat_id, 0)
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выключил(-а) фильтр ссылок", disable_mentions=1)
                await log_action(user_id, chat_id, "Выключил фильтр ссылок.")
            else:
                await set_link_filter(chat_id, 1)
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) включил(-а) фильтр ссылок", disable_mentions=1)
                await log_action(user_id, chat_id, "Включил фильтр ссылок.")
            return True

        if command in ['pred', 'пред']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!")
                return True
            
            user = 0
            arg_idx = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg_idx = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg_idx = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg_idx = 2
            else:
                await message.reply("Укажите пользователя!")
                return True
            
            if user < 0:
                await message.reply("Нельзя выдавать предупреждения сообществам!")
                return True

            if await equals_roles(user_id, user, chat_id) == 0:
                await message.reply("Вы не можете выдавать предупреждения этому пользователю!")
                return True

            reason = await get_string(arguments, arg_idx)
            if not reason:
                return await message.reply("Укажите причину предупреждения!")

            ud = await get_user_data(user)
            new_preds = ud['preds'] + 1
            
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Снять пред", {"command": "unpred_btn", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("Очистить", {"command": "delete_msg"}), color=KeyboardButtonColor.SECONDARY)
            )

            moder_name = await get_user_name(user_id, chat_id)
            target_name = await get_user_name(user, chat_id)
            
            moder_link = f"[id{user_id}|{moder_name}]"
            target_link = f"[id{user}|{target_name}]"

            if new_preds >= 2:
                await update_user_data(user, 'preds', 0)
                await warn(chat_id, user, user_id, f"[Авто-выговор за 2/2 предов] {reason}")
                await message.answer(f"{moder_link} выдал(-а) предупреждение {target_link}\n"
                                     f"Причина: {reason}\n"
                                     f"Количество предупреждений: 2/2\n\n"
                                     f"❗ Пользователь получил 1 выговор за накопление 2-х предупреждений.", 
                                     disable_mentions=1, keyboard=keyboard)
            else:
                await update_user_data(user, 'preds', new_preds)
                await message.answer(f"{moder_link} выдал(-а) предупреждение {target_link}\n"
                                     f"Причина: {reason}\n"
                                     f"Количество предупреждений: {new_preds}", 
                                     disable_mentions=1, keyboard=keyboard)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Выдал предупреждение пользователю {u_name} ({new_preds}/2).\nПричина: {reason}")

        if command in ['unpred', 'снятьпред']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!")
                return True
            
            user = 0
            if message.reply_message:
                user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!")
                return True
            
            ud = await get_user_data(user)
            if ud['preds'] <= 0:
                await message.reply("У пользователя нет предупреждений!")
                return True
                
            new_preds = ud['preds'] - 1
            await update_user_data(user, 'preds', new_preds)
            
            moder_name = await get_user_name(user_id, chat_id)
            target_name = await get_user_name(user, chat_id)
            moder_link = f"[id{user_id}|{moder_name}]"
            target_link = f"[id{user}|{target_name}]"
            
            await message.answer(f"✅ {moder_link} снял предупреждение {target_link} ({new_preds}/2).", disable_mentions=1)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Снял предупреждение с пользователя {u_name}.")

        if command in ['clearpreds', 'очиститьпреды']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!")
                return True
            
            user = 0
            if message.reply_message:
                user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!")
                return True
            
            await update_user_data(user, 'preds', 0)
            
            moder_name = await get_user_name(user_id, chat_id)
            target_name = await get_user_name(user, chat_id)
            moder_link = f"[id{user_id}|{moder_name}]"
            target_link = f"[id{user}|{target_name}]"
            
            await message.answer(f"🧹 {moder_link} полностью очистил предупреждения {target_link}.", disable_mentions=1)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Очистил предупреждения у пользователя {u_name}.")

        game_cmds = [
            'приз', 'prize', 'reward', 'баланс', 'balance', 'монеты', 'деньги', 
            'дуэль', 'duel', 'передать', 'отправить', 'send', 'transfer', 
            'топ', 'top', 'лучшие', 'toppet', 'топпет', 'topwork', 'топработа',
            'положить', 'deposit', 'депозит', 'снять', 'withdraw', 'вывести', 
            'благо', 'charity', 'благотворительность', 'топблаго', 'topcharity', 'топблаготворительность', 
            'казино', 'casino', 'buyvip', 'купитьвип', 'vip', 'промо', 'promo', 'бонус', 
            'promolist', 'списокпромо', 'кодысписок', 'открытьдепозит', 'opendepositvip', 
            'закрытьдепозит', 'closedepositvip', 'clan', 'клан', 'topclan', 'топклан', 'клантоп',
            'jobs', 'работы', 'профессии', 'устроиться', 'joinjob', 'setjob', 'работа', 'work', 'работать',
            'myjob', 'jobstats', 'mywork', 'мояработа', 'профстат', 'уволиться', 'quitjob', 'leavejob',
            'biz', 'business', 'бизнес', 'mybiz', 'моибизнесы', 'мойбиз', 'sell', 'depo',
            'pet', 'питомец', 'слоты', 'slots', 'бизслоты', 'реф', 'ref', 'пригласил', 'referral',
            'моирефы', 'myrefs', 'myref'
        ]

        if command in game_cmds and not await get_games(chat_id):
             await message.reply("⚠️ Игровые команды отключены в этой беседе!", disable_mentions=1)
             return True

        if command in ['приз', 'prize', 'reward']:
            # Проверяем, получал ли пользователь приз сегодня
            already_claimed = await get_daily_claimed_today(user_id)
            if already_claimed:
                await message.reply("Вы уже получили приз сегодня! Приходите завтра!", disable_mentions=1)
                return True
            
            # Генерируем рандомный приз от 0 до 5000
            economy = load_economy()
            prize = random.randint(economy['settings']['daily_reward_min'], economy['settings']['daily_reward_max'])
            
            ud_eco = await get_user_economy_data(user_id)
            v_lvl = ud_eco.get('vip_level', 0)
            mult = VIP_CONFIG[v_lvl]['prize_mult'] if v_lvl in VIP_CONFIG else 1
            if mult > 1:
                prize = int(prize * mult)
                msg_vip = f" (VIP x{mult})"
            else: msg_vip = ""

            # Добавляем приз на баланс
            await add_balance(user_id, prize)
            await set_daily_claimed(user_id)
            
            await message.reply(f"🎉 Ты получил приз {prize}!{msg_vip}", disable_mentions=1)

        if command in ['баланс', 'balance', 'монеты', 'деньги']:
            target_user = user_id
            if message.reply_message: target_user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0: target_user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): target_user = await getID(arguments[1])

            if target_user < 0:
                return await message.reply("У сообществ нет баланса!")

            # Скрытие баланса руководства
            target_role = await get_role(target_user, chat_id)
            requester_role = await get_role(user_id, chat_id)
            if target_role >= 6 and requester_role < 6 and target_user != user_id:
                return await message.reply("🔒 Баланс руководства скрыт от глаз обычных смертных!", disable_mentions=1)

            # Получаем данные пользователя
            user_data = await get_user_economy_data(target_user)
            balance = int(round(user_data.get('balance', 0)))
            bank = int(round(user_data.get('bank', 0)))
            
            # Дуэли
            duels_won = user_data.get('duels_won', 0)
            duels_lost = user_data.get('duels_lost', 0)
            duels_sum_won = user_data.get('duels_sum_won', 0)
            duels_sum_lost = user_data.get('duels_sum_lost', 0)
            
            # Переводы
            transfers_sent = user_data.get('transfers_sent', 0)
            transfers_received = user_data.get('transfers_received', 0)
            transfers_sum_sent = user_data.get('transfers_sum_sent', 0)
            transfers_sum_received = user_data.get('transfers_sum_received', 0)
            
            # VIP
            vip_status = "VIP" if user_data.get('vip') else "Обычный"
            vip_until = user_data.get('vip_until')
            
            if vip_until and user_data.get('vip'):
                vip_date = datetime.fromisoformat(vip_until)
                days_diff = (vip_date - datetime.now()).days
                hours_diff = ((vip_date - datetime.now()).seconds // 3600) % 24
                mins_diff = ((vip_date - datetime.now()).seconds // 60) % 60
                vip_time = f"{days_diff}d {hours_diff}h {mins_diff}m"
            else:
                vip_time = "N/A"
            
            # Депозиты
            deposits = user_data.get('deposits', [])
            deposits_info = ""
            if deposits and isinstance(deposits, list) and len(deposits) > 0:
                deposit = deposits[0]
                deposit_amount = deposit.get('amount', 0)
                if isinstance(deposit_amount, float):
                    deposit_amount = int(round(deposit_amount))
                deposit_percent = deposit.get('percent', 5)
                try:
                    deposit_created = datetime.fromisoformat(deposit.get('created', ''))
                    time_diff = datetime.now() - deposit_created
                    days_passed = time_diff.days
                    hours_passed = (time_diff.seconds // 3600) % 24
                    mins_passed = (time_diff.seconds // 60) % 60
                    deposits_info = f"\n💎 Депозит: {deposit_amount} на {deposit_percent}%\n⏳ Прошло: {days_passed}д {hours_passed}ч {mins_passed}м"
                except Exception:
                    deposits_info = f"\n💎 Депозит: {deposit_amount} на {deposit_percent}%"
            
            # Получаем информацию о пользователе
            try:
                user_info = await bot.api.users.get(user_ids=target_user)
                user_name = f"[id{target_user}|{user_info[0].first_name} {user_info[0].last_name}]"
            except:
                user_name = f"[id{target_user}|Unknown]"
            
            # Формируем сообщение
            msg = f"👤 У пользователя {user_name}\n"
            msg += f"💰 Ваш баланс: {balance:,}$\n\n"
            msg += f"🏦 Счет в банке: {bank:,}$\n\n"
            msg += f"🎯 Дуэлей выиграно: {duels_won}\n"
            msg += f"💥 Дуэлей проиграно: {duels_lost}\n"
            msg += f"✅ Всего выиграно: {duels_sum_won:,}$\n"
            msg += f"❌ Всего проиграно: {duels_sum_lost:,}$\n\n"
            msg += f"📤 Отправлено переводами: {transfers_sum_sent:,}$\n"
            msg += f"📥 Получено переводами: {transfers_sum_received:,}$\n\n"
            msg += f"⭐ Статус: {vip_status}\n"
            
            if vip_status == "VIP":
                msg += f"⏳ До окончания статуса: {vip_time}\n"
            
            msg += deposits_info
            msg = msg.replace(",", ".")
            if await get_global_role(target_user) > 0:
                msg += "\n👑 Аккаунт руководства бота"
            
            await message.answer(msg, disable_mentions=1)

        if command in ['jobs', 'работы', 'профессии']:
            msg = "💼 Центр занятости\nВыберите профессию, чтобы узнать подробности и трудоустроиться:"
            kb = Keyboard(inline=True)
            job_items = [(jid, data) for jid, data in JOBS.items() if jid > 0]
            for i, (jid, data) in enumerate(job_items):
                kb.add(Callback(data['name'], {"command": "job_info", "job_id": jid, "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.PRIMARY)
                if (i + 1) % 2 == 0: kb.row()
            
            if len(job_items) % 2 != 0: kb.row()
            kb.add(Callback("❌ Закрыть", {"command": "delete_msg", "user": user_id}), color=KeyboardButtonColor.NEGATIVE)
            await message.reply(msg, keyboard=kb)
            return True

        if command in ['устроиться', 'joinjob', 'setjob']:
            if len(arguments) < 2: return await message.reply("📝 Использование: /устроиться [ID профессии]")
            try: job_id = int(arguments[1])
            except: return await message.reply("ID должен быть числом!")
            
            if job_id not in JOBS: return await message.reply("Такой профессии нет!")
            
            ud_eco = await get_user_economy_data(user_id)
            current_job = ud_eco.get('job', 0)
            
            if current_job == job_id: return await message.reply("Вы уже работаете здесь!")
            
            cost = JOBS[job_id]['cost']
            if not await subtract_balance(user_id, cost):
                return await message.reply(f"❌ Недостаточно средств! Стоимость обучения: {cost:,}$".replace(",", "."))
            
            # Сохраняем новую работу
            economy = load_economy()
            str_uid = str(user_id)
            economy['users'][str_uid]['job'] = job_id
            save_economy(economy)
            
            await message.reply(f"✅ Поздравляем! Вы устроились на должность «{JOBS[job_id]['name']}»!")

        if command in ['работа', 'work', 'работать']:
            ud_eco = await get_user_economy_data(user_id)
            job_id = ud_eco.get('job', 0)
            last_work = ud_eco.get('last_job_time', 0)
            
            job_data = JOBS.get(job_id, JOBS[0])
            
            cooldown_sec = await get_job_cooldown(user_id, job_id, ud_eco)
            
            if time.time() - last_work < cooldown_sec:
                rem_sec = int(cooldown_sec - (time.time() - last_work))
                rem_min = rem_sec // 60
                rem_s = rem_sec % 60
                return await message.answer(f"⏳ Смена еще не началась! Отдохните {rem_min}м {rem_s}с.")
            
            # Система уровней
            job_lvl = ud_eco.get('job_level', 1)
            job_exp = ud_eco.get('job_exp', 0)
            v_lvl = ud_eco.get('vip_level', 0)
            
            # Базовая зарплата (ограничена диапазоном из конфига)
            base_salary = random.randint(job_data['min_pay'], job_data['max_pay'])
            
            # Дополнительные бонусы профессии (15% для машиниста по умолчанию)
            base_job_bonus = 15 if job_id == 6 else 0
            
            lvl_bonus_step = 2 # 2% за каждый уровень мастерства
            bonus_percent = (job_lvl - 1) * lvl_bonus_step
            pet_b = await get_pet_bonus(user_id)
            is_vip_active = ud_eco.get('vip', False)
            
            # Складываем бонусы аддитивно (Профессия + Мастерство + Питомец + VIP)
            actual_vip_bonus = VIP_CONFIG[v_lvl]['pay_bonus'] if (is_vip_active and v_lvl in VIP_CONFIG) else (25 if is_vip_active else 0)
            total_bonus_percent = int(base_job_bonus + bonus_percent + pet_b.get('salary', 0) + actual_vip_bonus)
            
            salary = int(base_salary * (1 + total_bonus_percent / 100))
            # Начисление опыта
            exp_gain = random.randint(5, 15)
            if v_lvl > 0: exp_gain = int(exp_gain * 1.5) # VIP бонус к опыту
            new_exp = job_exp + exp_gain
            exp_needed = job_lvl * 100 # Порог опыта
            
            lvl_msg = ""
            if new_exp >= exp_needed:
                job_lvl += 1
                new_exp -= exp_needed
                lvl_msg = f"\n🆙 Уровень мастерства повышен! Теперь {job_lvl} ур. (+{bonus_percent + 5}% к ЗП)"
            
            # Quest progress for economic boom
            quest_msg = ""
            ud = await get_user_data(user_id)
            if ud.get('clan_id'):
                q_completed, qr_mats, qr_exp = await check_daily_quest_progress(ud['clan_id'], salary, "economic_boom")
                if q_completed:
                    quest_msg = f"\n✅ Клан выполнил ежедневное задание! Награда: +{qr_mats:,} мат. +{qr_exp:,} exp".replace(",",".")
                
                # Квест клана: Трудоголики (смены)
                q_completed_s, qr_mats_s, qr_exp_s = await check_daily_quest_progress(ud['clan_id'], 1, "work_shifts")
                if q_completed_s:
                    quest_msg += f"\n✅ Клан выполнил задание «Трудоголики»! Награда: +{qr_mats_s:,} мат. +{qr_exp_s:,} exp".replace(",",".")

            economy = load_economy()
            str_uid = str(user_id)
            economy['users'][str_uid]['last_job_time'] = int(time.time())
            economy['users'][str_uid]['job_level'] = job_lvl
            economy['users'][str_uid]['job_exp'] = new_exp
            save_economy(economy)
            
            await add_balance(user_id, salary)
            log_transaction(user_id, f"Зарплата ({job_data['name']}): +{salary}$")
            p_data = await get_pet_data(user_id)
            pet_emoji = PETS[p_data['id']]['emoji'] if p_data else "🐾"
            
            vip_text = ""
            if actual_vip_bonus > 0: vip_text += f" ⚡(+{actual_vip_bonus}%)"
            if base_job_bonus > 0: vip_text += f" 🚂(+{base_job_bonus}%)"
            if pet_b['salary'] > 0: vip_text += f" {pet_emoji}(+{pet_b['salary']}%)"
            if bonus_percent > 0: vip_text += f" ⚒(+{bonus_percent}%)"
            
            # Referral Bonus Logic
            ud = await get_user_data(user_id)
            ref_msg = ""
            if ud.get('referrer_id', 0) > 0:
                ref_bonus = int(salary * 0.01)
                if ref_bonus > 0:
                    await add_balance(ud['referrer_id'], ref_bonus)
                    ref_msg = f"\n🤝 Пригласивший вас получил бонус: {ref_bonus:,}$".replace(",", ".")

            await message.answer(f"🔨 Вы отработали смену ({job_data['name']}) и получили {salary:,}${vip_text}!\n📊 Опыт: {new_exp}/{job_lvl * 100} (+{exp_gain}){lvl_msg}{quest_msg}{ref_msg}".replace(",", "."))

        if command in ['myjob', 'jobstats', 'mywork', 'мояработа', 'профстат']:
            target, _ = await get_target_user(message, arguments)
            if not target: target = user_id
            
            ud_eco_target = await get_user_economy_data(target)
            job_id = ud_eco_target.get('job', 0)
            job_lvl = ud_eco_target.get('job_level', 1)
            job_exp = ud_eco_target.get('job_exp', 0)
            last_work = ud_eco_target.get('last_job_time', 0)
            
            job_data = JOBS.get(job_id, JOBS[0])
            
            exp_needed = job_lvl * 100
            bonus_percent = (job_lvl - 1) * 5
            
            pct = min(1.0, job_exp / exp_needed) if exp_needed > 0 else 0
            filled = int(pct * 10)
            bar = "🟦" * filled + "⬜" * (10 - filled)
            
            cooldown_sec = await get_job_cooldown(target, job_id, ud_eco_target)
            
            if time.time() - last_work < cooldown_sec:
                rem = int(cooldown_sec - (time.time() - last_work))
                status = f"⏳ До смены: {rem // 60}м {rem % 60}с"
            else: status = "✅ Готов к работе!"
            
            u_name = await get_user_name(target, chat_id)
            msg = (f"👷 Статистика работы [id{target}|{u_name}]:\n"
                   f"💼 Профессия: {job_data['name']}\n"
                   f"⭐ Уровень мастерства: {job_lvl}\n"
                   f"📈 Опыт: {job_exp}/{exp_needed}\n"
                   f"{bar}\n"
                   f"💰 Бонус к зарплате: +{bonus_percent}%\n"
                   f"💵 Базовая ставка: {job_data['min_pay']:,}-{job_data['max_pay']:,}$\n"
                   f"{status}".replace(",", "."))
            
            await message.reply(msg)

        if command in ['уволиться', 'quitjob', 'leavejob']:
            ud_eco = await get_user_economy_data(user_id)
            if ud_eco.get('job', 0) == 0:
                return await message.reply("Вы и так безработный!")
            
            economy = load_economy()
            str_uid = str(user_id)
            economy['users'][str_uid]['job'] = 0
            save_economy(economy)
            
            await message.reply("🚪 Вы уволились и теперь безработный.")

        if command in ['resetwork', 'сбросработы', 'обнулитьработу']:
            if not await check_perm(user_id, chat_id, command, 6):
                await message.reply("❌ Только разработчик может сбрасывать КД работы!", disable_mentions=1)
                return True
            
            target_user = 0
            if message.reply_message:
                target_user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                target_user = await getID(arguments[1])
            else:
                await message.reply("📝 Использование: /resetwork [пользователь]", disable_mentions=1)
                return True
            
            economy = load_economy()
            str_uid = str(target_user)
            if str_uid not in economy['users']:
                await get_balance(target_user)
                economy = load_economy()
            
            economy['users'][str_uid]['last_job_time'] = 0
            save_economy(economy)
            
            try: u_info = await bot.api.users.get(user_ids=target_user); u_name = f"[id{target_user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{target_user}"
            
            await message.reply(f"✅ Кулдаун работы сброшен для {u_name}!", disable_mentions=1)
            await log_action(user_id, chat_id, f"Сбросил КД работы пользователю {u_name}.")

        if command in ['info', 'инфо', 'информация']:
            project_info = "ℹ️ Официальные ресурсы проекта:\n\n🔗 VK: https://vk.com/cherepovets.teams.manager\n\n🌐 Developer: https://vk.com/id460366734\n\n📱 Discord: ..."
            sql.execute("SELECT value FROM global_settings WHERE key = 'project_info'")
            fetch = sql.fetchone()
            if fetch:
                project_info = fetch[0]
            await message.reply(project_info, disable_mentions=1)
        
        if command in ['сетинфо', 'setinfo']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            if len(arguments) < 2:
                await message.reply("📝 Использование: /сетинфо [текст]", disable_mentions=1)
                return True
            
            new_info = await get_string(arguments, 1)
            sql.execute("INSERT OR REPLACE INTO global_settings (key, value) VALUES ('project_info', ?)", (new_info,))
            database.commit()
            await message.reply(f"✅ Информация обновлена!", disable_mentions=1)

        if command in ['biz', 'business', 'бизнес']:
            if len(arguments) > 1:
                # Подкоманда пожертвования: /biz donate [ID]
                if arguments[1].lower() in ['donate', 'пожертвовать', 'вклан']:
                    if len(arguments) < 3:
                        return await message.reply("📝 Использование: /biz donate [ID]")
                    try: bid = int(arguments[2])
                    except: return await message.reply("ID бизнеса должен быть числом!")
                    
                    ud = await get_user_data(user_id)
                    clan_id = ud.get('clan_id')
                    if not clan_id: return await message.reply("❌ Вы не состоите в клане!")
                    
                    # Проверка лимита бизнесов у клана (макс 1)
                    sql.execute("SELECT COUNT(*) FROM businesses WHERE clan_owner_id = ?", (clan_id,))
                    if sql.fetchone()[0] >= 1:
                        return await message.reply("❌ Ваш клан уже владеет предприятием (лимит 1)!")

                    sql.execute("SELECT owner_id, name FROM businesses WHERE id = ?", (bid,))
                    res = sql.fetchone()
                    if not res: return await message.reply("❌ Бизнес не найден!")
                    if res[0] != user_id: return await message.reply("❌ Это не ваш бизнес!")
                    
                    sql.execute("UPDATE businesses SET owner_id = 0, clan_owner_id = ? WHERE id = ?", (clan_id, bid))
                    database.commit()
                    
                    sql.execute("SELECT name FROM clans WHERE clan_id = ?", (clan_id,))
                    c_name = sql.fetchone()[0]
                    return await message.reply(f"🏢 Бизнес «{res[1]}» успешно передан во владение клана «{c_name}»!\n"
                                             f"Теперь любой участник клана может собирать с него прибыль.")
                if arguments[1].isdigit():
                    bid = int(arguments[1])
                    sql.execute("SELECT name, price, owner_id, type, repair_until, clan_owner_id FROM businesses WHERE id = ?", (bid,))
                    res = sql.fetchone()
                    if not res: return await message.reply("Бизнес не найден!")
                    name, price, owner, b_type, repair, clan_owner = res
                    ud = await get_user_data(user_id); owner_str = await get_user_link(owner) if owner > 0 else "Государство"
                    if clan_owner > 0:
                        sql.execute("SELECT name FROM clans WHERE clan_id = ?", (clan_owner,)); owner_str = f"Клан «{sql.fetchone()[0]}»"
                    status = "✅ Работает" if time.time() > repair else f"🛠 Ремонт ({int((repair-time.time())/60)} мин)"
                    msg = f"🏢 Бизнес: {name} (ID: {bid})\n🏷 Цена: {price:,}$\n👤 Владелец: {owner_str}\n⚙ Статус: {status}".replace(",", ".")
                    kb = Keyboard(inline=True)
                    if owner == 0 and clan_owner == 0: kb.add(Callback("🛒 Купить", {"command": "biz_buy", "biz_id": bid, "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
                    elif owner == user_id or (clan_owner > 0 and clan_owner == ud.get('clan_id') and await check_clan_perms(user_id, 4)): 
                        kb.add(Callback("⚙ Управление", {"command": "biz_manage", "biz_id": bid, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY)
                    return await message.reply(msg, keyboard=kb, disable_mentions=1)
            else:
                sql.execute("SELECT b.id, b.name, b.price, b.owner_id, b.clan_owner_id, c.name FROM businesses b LEFT JOIN clans c ON b.clan_owner_id = c.clan_id")
                res = sql.fetchall()
                if not res:
                    return await message.reply("🏢 В городе пока нет доступных предприятий!")
                
                page = 1
                if len(arguments) > 1 and arguments[1].isdigit():
                    bid_or_page = int(arguments[1])
                    sql.execute("SELECT id FROM businesses WHERE id = ?", (bid_or_page,))
                    if not sql.fetchone():
                        page = bid_or_page

                per_page = 6
                sql.execute("SELECT COUNT(*) FROM businesses")
                total_count = sql.fetchone()[0]
                total_pages = (total_count + per_page - 1) // per_page
                if page > total_pages: page = total_pages
                if page < 1: page = 1
                
                offset = (page - 1) * per_page
                sql.execute("SELECT id FROM businesses LIMIT ? OFFSET ?", (per_page, offset))
                res = sql.fetchall()

                msg = f"🏢 Список предприятий | Страница {page}/{total_pages}\n📝 Нажмите на ID бизнеса для просмотра информации.\n\n"
                kb = Keyboard(inline=True)
                
                for i, b in enumerate(res):
                    kb.add(Callback(f"🆔 {b[0]}", {"command": "biz_info_btn", "biz_id": b[0], "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY)
                    if (i + 1) % 3 == 0: kb.row()
                
                if res and len(res) % 3 != 0: kb.row()
                
                if total_pages > 1:
                    if page > 1:
                        kb.add(Callback("⏪", {"command": "biz_page", "page": page - 1, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY)
                    kb.add(Callback(f"📄 {page}/{total_pages}", {"command": "none"}), color=KeyboardButtonColor.SECONDARY)
                    if page < total_pages:
                        kb.add(Callback("⏩", {"command": "biz_page", "page": page + 1, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY)
                    kb.row()
                
                kb.add(Callback("❌ Закрыть", {"command": "delete_msg", "initiator": user_id}), color=KeyboardButtonColor.SECONDARY)
                
                return await message.reply(msg, keyboard=kb)

        if command in ['mybiz', 'моибизнесы', 'мойбиз']:
            ud = await get_user_data(user_id); clan_id = ud.get('clan_id', 0)
            cursor = database.cursor()
            cursor.execute("SELECT id, name FROM businesses WHERE owner_id = ?", (user_id,))
            personal = cursor.fetchall()
            
            clan_biz = []
            if clan_id:
                cursor.execute("SELECT id, name FROM businesses WHERE clan_owner_id = ?", (clan_id,))
                clan_biz = cursor.fetchall()

            if not personal and not clan_biz:
                return await message.reply("😔 У вас пока нет предприятий. Купите что-нибудь через /biz или вступите в клан!")
            
            msg = "🏢 Список ваших владений:\n\n"
            kb = Keyboard(inline=True)
            max_buttons = 9 # Лимит для inline-клавиатуры
            btn_count = 0
            if personal:
                msg += "👤 Личные:\n"
                for b in personal:
                    if btn_count >= max_buttons: break
                    msg += f"• {b[1]} [ID: {b[0]}]\n"
                    kb.add(Callback(f"⚙ ID: {b[0]}", {"command": "biz_manage", "biz_id": int(b[0]), "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY)
                    btn_count += 1
                    if btn_count % 3 == 0: kb.row()
            
            if clan_biz:
                msg += "\n🏰 Клановые предприятия:\n"
                for b in clan_biz:
                    if btn_count >= max_buttons: break
                    msg += f"• {b[1]} [ID: {b[0]}]\n"
                    if await check_clan_perms(user_id, 4):
                        kb.add(Callback(f"🛡 ID: {b[0]}", {"command": "biz_manage", "biz_id": int(b[0]), "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY)
                        btn_count += 1
                        if btn_count % 3 == 0: kb.row()
            
            if btn_count > 0 and kb.buttons and kb.buttons[-1] != [] and btn_count < 10: kb.row()
            kb.add(Callback("❌ Закрыть", {"command": "delete_msg"}), color=KeyboardButtonColor.SECONDARY)
            await message.reply(msg, keyboard=kb)

        if command in ['sell']:
            if len(arguments) < 3 or arguments[1].lower() != 'biz':
                return await message.reply("📝 Использование: /sell biz [ID] [ID игрока] [Цена]\nПродажа государству (50%): /sell biz [ID] gov")
            
            try:
                bid = int(arguments[2])
            except: return await message.reply("❌ ID бизнеса должен быть числом!")

            # --- ПРОДАЖА ГОСУДАРСТВУ ---
            if len(arguments) >= 4 and arguments[3].lower() in ['gov', 'гос', 'государство']:
                sql.execute("SELECT owner_id, price, name FROM businesses WHERE id = ?", (bid,))
                res = sql.fetchone()
                if not res: return await message.reply("❌ Бизнес не найден!")
                if res[0] != user_id: return await message.reply("❌ Это не ваш бизнес!")

                refund = int(res[1] * 0.5)
                # Сброс прибыли к базовой (5% от цены) и уровня к 1
                base_profit = int(res[1] * 0.05)
                
                sql.execute("UPDATE businesses SET owner_id = 0, clan_owner_id = 0, level = 1, profit_per_hour = ?, special_order_active = 0, active_route = 1 WHERE id = ?", (base_profit, bid))
                database.commit()
                await add_balance(user_id, refund)
                
                await message.reply(f"✅ Бизнес «{res[2]}» продан государству за {refund:,}$!".replace(",", "."))
                await log_action(user_id, chat_id, f"Продал бизнес «{res[2]}» (ID: {bid}) государству за {refund}$.")
                return True

            # --- ПРОДАЖА ИГРОКУ ---
            if len(arguments) < 5:
                return await message.reply("📝 Использование: /sell biz [ID] [ID покупателя] [Цена]")
            
            try: 
                target = await getID(arguments[3]); price = int(arguments[4])
                if not target: raise ValueError
            except: return await message.reply("❌ Укажите корректного покупателя и цену!")
            
            if price < 0: return await message.reply("❌ Цена не может быть отрицательной!")

            sql.execute("SELECT owner_id FROM businesses WHERE id = ?", (bid,))
            res = sql.fetchone()
            if not res or res[0] != user_id: return await message.reply("❌ Это не ваш бизнес!")

            # Проверка слотов у цели при отправке предложения (для удобства)
            t_ud = await get_user_data(target)
            sql.execute("SELECT COUNT(*) FROM businesses WHERE owner_id = ?", (target,))
            t_count = sql.fetchone()[0]
            t_slots = 2 + t_ud.get('biz_slots', 0)
            
            if t_count >= t_slots:
                return await message.reply(f"❌ У пользователя {await get_user_link(target)} нет свободных слотов для бизнеса ({t_count}/{t_slots})!", disable_mentions=1)
            
            sql.execute("INSERT INTO biz_offers (biz_id, from_id, to_id, price) VALUES (?, ?, ?, ?)", (bid, user_id, target, price))
            oid = sql.lastrowid; database.commit()
            
            target_link = await get_user_link(target)
            kb = (
                Keyboard(inline=True)
                .add(Callback("✅ Купить", {"command": "biz_accept_offer", "oid": oid}), color=KeyboardButtonColor.POSITIVE)
                .add(Callback("❌ Отмена", {"command": "biz_decline_offer", "oid": oid}), color=KeyboardButtonColor.NEGATIVE)
            )
            await message.answer(f"{target_link}, вам предлагают купить бизнес #{bid} за {price:,}$!".replace(",", "."), keyboard=kb, disable_mentions=1)

        if command == 'depo':
            ud = await get_user_data(user_id)
            if len(arguments) > 1 and arguments[1].isdigit():
                bid = int(arguments[1])
                sql.execute("SELECT id, name, active_route, special_order_active, repair_until FROM businesses WHERE id = ? AND type = 'station'", (bid,))
            else:
                sql.execute("SELECT id, name, active_route, special_order_active, repair_until FROM businesses WHERE (owner_id = ? OR clan_owner_id = ?) AND type = 'station' LIMIT 1", (user_id, ud.get('clan_id', 0)))
            
            biz = sql.fetchone()
            if not biz: return await message.reply("У вас нет станций!")
            
            bid, name, route_id, special, repair = biz
            status = "✅ Пути исправны" if time.time() > repair else f"🛠 В РЕМОНТЕ ({int((repair-time.time())/60)} мин)"
            route_name = STATION_ROUTES[route_id]['name']
            msg = f"🚋 Депо станции: {name}\n⚙ Статус: {status}\n🚩 Активный маршрут: {route_name}\n📦 Спецзаказ: {'Активен' if special else 'Нет'}"
            
            kb = Keyboard(inline=True)
            kb.add(Callback("💰 Прибыль", {"command": "biz_collect", "biz_id": bid, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.POSITIVE)
            kb.add(Callback("🛣 Маршруты", {"command": "depo_routes", "biz_id": bid, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY).row()
            kb.add(Callback("📦 Спецзаказ", {"command": "depo_special", "biz_id": bid, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY).row()
            await message.reply(msg, keyboard=kb)

        if command in ['pet', 'питомец']:
            if len(arguments) > 1:
                action = arguments[1].lower()
                if action == "shop":
                    msg = "🐾 Зоомагазин:\n\n"
                    for pid, pdata in PETS.items(): msg += f"{pid}. {pdata['emoji']} {pdata['name']} — {pdata['cost']:,}$\n✨ Бонус: {pdata['desc']}\n\n"
                    msg += "📝 Чтобы купить питомца, введите: /pet buy [ID]"
                    
                    # Updated pet shop message to reflect level-based bonuses
                    msg = "🐾 Зоомагазин:\n\n"
                    for pid, pdata in PETS.items():
                        bonus_desc = []
                        if pdata.get("base_salary_bonus", 0) > 0: bonus_desc.append(f"+{pdata['base_salary_bonus']}% к зарплате")
                        if pdata.get("base_mats_bonus", 0) > 0: bonus_desc.append(f"+{pdata['base_mats_bonus']}% к материалам")
                        if pdata.get("base_clan_exp_bonus", 0) > 0: bonus_desc.append(f"+{pdata['base_clan_exp_bonus']}% к опыту клана")
                        msg += f"{pid}. {pdata['emoji']} {pdata['name']} — {pdata['cost']:,}$\n✨ Бонус: {', '.join(bonus_desc)} (+{PET_BONUS_PER_LEVEL}% за каждый уровень)\n\n"
                    msg += "📝 Чтобы купить питомца, введите: /pet buy [ID]"
                    return await message.reply(msg.replace(",", "."))
                elif action == "buy":
                    if len(arguments) < 3:
                        return await message.reply("📝 Использование: /pet buy [ID]")
                    try:
                        pid = int(arguments[2])
                    except ValueError:
                        return await message.reply("❌ ID питомца должен быть числом!")
                    if pid not in PETS:
                        return await message.reply("❌ Такого питомца нет в магазине!")
                    p_data = await get_pet_data(user_id)
                    if p_data: return await message.reply("У вас уже есть питомец!")
                    if not await subtract_balance(user_id, PETS[pid]['cost']): return await message.reply("Нет денег!")
                    sql.execute("INSERT INTO pets (user_id, pet_id, name, last_update) VALUES (?, ?, ?, ?)", (user_id, pid, PETS[pid]['name'], int(time.time())))
                    database.commit(); return await message.reply(f"🎉 Теперь у вас есть {PETS[pid]['name']}!")
                elif action in ["setname", "имя", "переименовать", "name"]:
                        p_data = await get_pet_data(user_id)
                        if not p_data: return await message.reply("❌ У вас нет питомца!")
                        new_name = await get_string(arguments, 2)
                        if not new_name: return await message.reply("📝 Использование: /pet setname [новое имя]")
                        if len(new_name) > 20: return await message.reply("❌ Имя слишком длинное (макс. 20 символов)!")
                        sql.execute("UPDATE pets SET name = ? WHERE user_id = ?", (new_name, user_id))
                        database.commit()
                        return await message.reply(f"✅ Теперь вашего питомца зовут «{new_name}»!")
                elif action in ["zoo", "sell", "продать", "отдать"]:
                    p_data = await get_pet_data(user_id)
                    if not p_data: return await message.reply("❌ У вас нет питомца!")
                    
                    pid = p_data['id']
                    refund = int(PETS[pid]['cost'] * 0.5)
                    
                    kb = (
                        Keyboard(inline=True)
                        .add(Callback("✅ Подтвердить", {"command": "pet_sell_confirm", "user": user_id}), color=KeyboardButtonColor.POSITIVE)
                        .add(Callback("❌ Отмена", {"command": "delete_msg"}), color=KeyboardButtonColor.NEGATIVE)
                    )
                    return await message.reply(f"⚠️ Вы уверены, что хотите отдать питомца «{p_data['name']}» в зоопарк?\n"
                                             f"💰 Вы получите {refund:,}$ (50% стоимости).".replace(",", "."), keyboard=kb)
            
            p = await get_pet_data(user_id)
            if not p: return await message.reply("У вас нет питомца!\nКупить: /pet shop")
            p_conf = PETS.get(p['id'])
            exp_needed = p['lvl'] * 150
            bar = "🟦" * int((p['exp']/exp_needed)*10) + "⬜" * (10 - int((p['exp']/exp_needed)*10))
            text = (f"{p_conf['emoji']} Питомец: {p['name']}\n"
                    f"⭐ Уровень: {p['lvl']}\n"
                    f"📈 Опыт: {p['exp']}/{exp_needed}\n{bar}\n"
                    f"🍖 Сытость: {p['hunger']}%\n"
                    f"⚡ Энергия: {p['energy']}%")
            kb = Keyboard(inline=True).add(Callback("🍖 Кормить (100$)", {"command": "pet_action", "act": "feed", "user": user_id}), color=KeyboardButtonColor.POSITIVE)
            kb.add(Callback("🎾 Играть", {"command": "pet_action", "act": "play", "user": user_id}), color=KeyboardButtonColor.PRIMARY)
            await message.reply(text, keyboard=kb)

        if command in ['правила', 'rules']:
            rules_text = "📋 Правила беседы:\nПравила пока не установлены."
            sql.execute("SELECT rules FROM chats WHERE chat_id = ?", (chat_id,))
            fetch = sql.fetchone()
            if fetch and fetch[0]:
                rules_text = fetch[0]
            await message.reply(rules_text, disable_mentions=1)
        
        if command in ['сетправила', 'setrules']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            
            raw_rules_text = await get_string(arguments, 1)
            
            if not raw_rules_text:
                await message.reply("📝 Использование: /сетправила [текст | default | off]", disable_mentions=1)
                return True

            final_rules_text = raw_rules_text
            if raw_rules_text.lower() == "default":
                final_rules_text = CHAT_RULES_DEFAULT_TEXT
                await message.reply(f"✅ Правила беседы установлены по умолчанию!", disable_mentions=1)
            elif raw_rules_text.lower() == "off":
                final_rules_text = None # Store NULL in DB
                await message.reply(f"✅ Правила беседы отключены!", disable_mentions=1)
            else:
                await message.reply(f"✅ Правила беседы обновлены!", disable_mentions=1)

            sql.execute("UPDATE chats SET rules = ? WHERE chat_id = ?", (final_rules_text, chat_id))
            database.commit()
            await log_action(user_id, chat_id, f"Изменил правила беседы: {final_rules_text if final_rules_text else 'OFF'}")

        if command in ['правилабота', 'botrules', 'ботправила']:
            sql.execute("SELECT value FROM global_settings WHERE key = 'bot_rules'")
            fetch = sql.fetchone()
            if fetch:
                bot_rules = fetch[0]
            else:
                bot_rules = BOT_RULES_DEFAULT_TEXT
            await message.reply(bot_rules, disable_mentions=1)
        
        if command in ['сетправилабота', 'setbotrules']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            
            new_rules = await get_string(arguments, 1) or BOT_RULES_DEFAULT_TEXT
            sql.execute("INSERT OR REPLACE INTO global_settings (key, value) VALUES ('bot_rules', ?)", (new_rules,))
            database.commit()
            await message.reply(f"✅ Правила бота обновлены!", disable_mentions=1)

        if command in ['infobot', 'ботинфо', 'информациябота']:
            await message.reply(
                "🤖 Moderation Manager | Официальные ресурсы:\n"
                "📚 Документация: ...\n"
                "💬 Поддержка: /offer\n"
                "🆘 Помощь: /help",
                disable_mentions=1
            )

        if command in ['q', 'выход', 'quit', 'leave']:
            try:
                await message.answer("До встречи!", disable_mentions=1)
                await bot.api.messages.remove_chat_user(chat_id, user_id)
            except:
                await message.answer("Не удалось покинуть беседу!", disable_mentions=1)

        if command in ['other', 'другое']:
            text = "🎮 Меню «Другое»:\nВыберите раздел:"
            kb = Keyboard(inline=True)
            kb.add(Callback("💰 Экономика", {"command": "other_menu", "category": "economy", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            kb.add(Callback("🏰 Кланы", {"command": "other_menu", "category": "clans", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY).row()
            kb.add(Callback("💼 Работа", {"command": "other_menu", "category": "jobs", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            kb.add(Callback("🎟️ Прочее", {"command": "other_menu", "category": "misc", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY).row()
            kb.add(Callback("❌ Закрыть", {"command": "delete_msg", "user": user_id}), color=KeyboardButtonColor.NEGATIVE)
            await message.reply(text, disable_mentions=1, keyboard=kb)

        if command in ['дуэль', 'duel']:
            if len(arguments) < 2:
                await message.reply("📝 Использование: /дуэль [сумма]")
                return True
            
            duel_cd = 15 # Кулдаун в секундах
            if time.time() - user_duel_cooldown.get(user_id, 0) < duel_cd:
                rem = int(duel_cd - (time.time() - user_duel_cooldown.get(user_id, 0)))
                return await message.reply(f"⏳ Вы сможете создать новую дуэль через {rem} сек.")

            try: amount = int(arguments[1])
            except: return await message.reply("Укажите корректную сумму!")
            
            if amount <= 0: return await message.reply("Сумма должна быть больше 0!")
            
            if not await subtract_balance(user_id, amount):
                await message.reply("Недостаточно средств!")
                return True
            
            user_duel_cooldown[user_id] = time.time()

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("⚔️ Вступить", {"command": "join_duel", "creator": user_id, "amount": amount, "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
                .add(Callback("❌ Отмена", {"command": "cancel_duel", "creator": user_id, "amount": amount, "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
            )
            
            await message.reply(f"⚔️ Дуэль на {amount:,}$ создана!\nНажми на кнопку чтобы вступить.".replace(",", "."), keyboard=keyboard)

        if command in ['передать', 'отправить', 'send', 'transfer']:
            recipient_id = user_id
            amount = 0
            
            if message.reply_message:
                recipient_id = message.reply_message.from_id
                try:
                    amount = int(arguments[1])
                except:
                    await message.reply("Укажите корректную сумму!", disable_mentions=1)
                    return True
            elif len(arguments) >= 3 and await getID(arguments[1]):
                recipient_id = await getID(arguments[1])
                try:
                    amount = int(arguments[2])
                except:
                    await message.reply("Укажите корректную сумму!", disable_mentions=1)
                    return True
            else:
                await message.reply("📝 Использование: /передать [пользователь] [сумма]", disable_mentions=1)
                return True
            
            if recipient_id == user_id:
                await message.reply("Вы не можете отправить деньги сами себе!", disable_mentions=1)
                return True
            
            if amount <= 0:
                await message.reply("Сумма должна быть больше 0!", disable_mentions=1)
                return True
            
            if not await subtract_balance(user_id, amount):
                await message.reply("Недостаточно средств!", disable_mentions=1)
                return True
            
            ud_eco = await get_user_economy_data(user_id)
            v_lvl = ud_eco.get('vip_level', 0)
            comm_rate = VIP_CONFIG[v_lvl]['comm'] if v_lvl in VIP_CONFIG else 0.10
            ud_full = await get_user_data(user_id) # Keep this line as ud_full is used below

            if ud_full.get('no_comm_until', 0) > time.time(): comm_rate = 0.0

            commission = int(amount * comm_rate)
            final_amount = amount - commission
            
            await add_balance(recipient_id, final_amount)
            
            # Обновляем статистику
            econ = load_economy()
            sender_str = str(user_id)
            recipient_str = str(recipient_id)
            if sender_str not in econ['users']:
                await get_balance(user_id)
                econ = load_economy()
            if recipient_str not in econ['users']:
                await get_balance(recipient_id)
                econ = load_economy()
            
            econ['users'][sender_str]['transfers_sent'] = econ['users'][sender_str].get('transfers_sent', 0) + 1
            econ['users'][sender_str]['transfers_sum_sent'] = econ['users'][sender_str].get('transfers_sum_sent', 0) + amount
            econ['users'][recipient_str]['transfers_received'] = econ['users'][recipient_str].get('transfers_received', 0) + 1
            econ['users'][recipient_str]['transfers_sum_received'] = econ['users'][recipient_str].get('transfers_sum_received', 0) + final_amount
            if 'server_stats' not in econ: econ['server_stats'] = {'collected_commissions': 0}
            econ['server_stats']['collected_commissions'] += commission
            save_economy(econ)
            
            log_transaction(user_id, f"Перевод {amount}$ пользователю [id{recipient_id}|ID{recipient_id}] (комиссия {commission}$)")
            log_transaction(recipient_id, f"Получен перевод {final_amount}$ от [id{user_id}|ID{user_id}]")
            
            # Получаем информацию о пользователях для линкабельных имен
            try:
                sender_info = await bot.api.users.get(user_ids=user_id)
                sender_name = f"[id{user_id}|{sender_info[0].first_name} {sender_info[0].last_name}]"
            except:
                sender_name = f"[id{user_id}|Unknown]"
            
            try:
                recipient_info = await bot.api.users.get(user_ids=recipient_id)
                recipient_name = f"[id{recipient_id}|{recipient_info[0].first_name} {recipient_info[0].last_name}]"
            except:
                recipient_name = f"[id{recipient_id}|Unknown]"
            
            await message.reply(
                f"✅ Перевод выполнен!\n"
                f"От: {sender_name}\n"
                f"Кому: {recipient_name}\n"
                f"Сумма: {amount} (Комиссия {int(comm_rate*100)}%: {commission})\n"
                f"Зачислено: {final_amount}",
                disable_mentions=1
            )

        if command in ['топ', 'top', 'лучшие', 'toppet', 'топпет', 'topwork', 'топработа']:
            if command in ['toppet', 'топпет']:
                sub = 'pet'
            elif command in ['topwork', 'топработа']:
                sub = 'work'
            elif len(arguments) > 1:
                sub = arguments[1].lower()
            else:
                sub = 'money'

            msg, kb = await build_top_text(chat_id, sub)
            await message.answer(msg, disable_mentions=1, keyboard=kb)
            return True

        if command in ['положить', 'deposit', 'депозит']:
            if len(arguments) < 2:
                await message.reply("📝 Использование: /положить [сумма]", disable_mentions=1)
                return True
            
            try:
                amount = int(arguments[1])
            except:
                await message.reply("Укажите корректную сумму!", disable_mentions=1)
                return True
            
            if amount <= 0:
                await message.reply("Сумма должна быть больше 0!", disable_mentions=1)
                return True
            
            if not await subtract_balance(user_id, amount):
                await message.reply("Недостаточно средств!", disable_mentions=1)
                return True
            
            bank = await get_bank(user_id)
            await set_bank(user_id, bank + amount)
            log_transaction(user_id, f"Банк: положил {amount}$ на счет")
            
            await message.reply(f"✅ {amount} монет положено в банк!", disable_mentions=1)

        if command in ['снять', 'withdraw', 'вывести']:
            if len(arguments) < 2:
                await message.reply("📝 Использование: /снять [сумма]", disable_mentions=1)
                return True
            
            try:
                amount = int(arguments[1])
            except:
                await message.reply("Укажите корректную сумму!", disable_mentions=1)
                return True
            
            if amount <= 0:
                await message.reply("Сумма должна быть больше 0!", disable_mentions=1)
                return True
            
            bank = await get_bank(user_id)
            if bank < amount:
                await message.reply("Недостаточно средств в банке!", disable_mentions=1)
                return True
            
            await set_bank(user_id, bank - amount)
            await add_balance(user_id, amount)
            log_transaction(user_id, f"Банк: снял {amount}$ со счета")
            
            await message.reply(f"✅ {amount} монет снято с банка!", disable_mentions=1)

        if command in ['благо', 'charity', 'благотворительность']:
            if len(arguments) < 2:
                await message.reply("📝 Использование: /благо [сумма]", disable_mentions=1)
                return True
            
            try:
                amount = int(arguments[1])
            except:
                await message.reply("Укажите корректную сумму!", disable_mentions=1)
                return True
            
            if amount <= 0:
                await message.reply("Сумма должна быть больше 0!", disable_mentions=1)
                return True
            
            if not await subtract_balance(user_id, amount):
                await message.reply("Недостаточно средств!", disable_mentions=1)
                return True
            
            economy = load_economy()
            user_id_str = str(user_id)
            if user_id_str not in economy['users']:
                await get_balance(user_id)
                economy = load_economy()
            
            economy['users'][user_id_str]['charity'] = economy['users'][user_id_str].get('charity', 0) + amount
            save_economy(economy)
            log_transaction(user_id, f"Благотворительность: пожертвовал {amount}$")
            
            await message.reply(f"❤️ Спасибо за {amount} монет в благотворительность!", disable_mentions=1)

        if command in ['топблаго', 'topcharity', 'топблаготворительность']:
            economy = load_economy()
            charity_list = []
            
            for user_id_str, user_data in economy['users'].items():
                charity = user_data.get('charity', 0)
                if charity > 0:
                    charity_list.append((int(user_id_str), charity))
            
            charity_list.sort(key=lambda x: x[1], reverse=True)
            top_charity = charity_list[:10]
            
            if not top_charity:
                await message.reply("Нет данных о благотворительности!", disable_mentions=1)
                return True
            
            msg = "❤️ Топ 10 благодетелей:\n\n"
            for idx, (uid, amount) in enumerate(top_charity, 1):
                name = await get_user_name(uid, chat_id)
                medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(idx, f"{idx}.")
                msg += f"{medal} [id{uid}|{name}]: {amount:,} 💰\n".replace(",", ".")
            
            await message.reply(msg, disable_mentions=1)

        if command in ['казино', 'casino']:
            if len(arguments) < 2:
                await message.reply("📝 Использование: /казино [ставка]", disable_mentions=1)
                return True
            
            if time.time() - user_casino_cooldown.get(user_id, 0) < 10:
                await message.reply(f"⏳ Подождите {int(10 - (time.time() - user_casino_cooldown[user_id]))} сек.", disable_mentions=1)
                return True
            
            try:
                bet = int(arguments[1])
            except:
                await message.reply("Укажите корректную ставку!", disable_mentions=1)
                return True
            
            ud_eco = await get_user_economy_data(user_id)
            economy = load_economy()
            max_bet = economy['settings']['max_bet']

            if bet > max_bet:
                return await message.reply(f"❌ Максимальная ставка: {max_bet:,}$".replace(",","."))

            if bet <= 0: return await message.reply("Ставка должна быть больше 0!")
            
            if not await subtract_balance(user_id, bet):
                await message.reply("Недостаточно средств!", disable_mentions=1)
                return True

            user_casino_cooldown[user_id] = time.time()

            user_name = await get_user_name(user_id, chat_id)
            user_link = f"[id{user_id}|{user_name}]"
            slots_emojis = ['🍒', '🍋', '🍇', '💎', '7️⃣', '🔔', '💰', '🍉', '🎰', '🎲', '🎯']
            msg_text = f"🎰 Казино запущено!\n👤 Игрок: {user_link}\n💰 Ставка: {bet:,}$\n\n🎡 Крутим... [🎰 🎰 🎰]".replace(",", ".")

            try:
                sent_msg = await message.answer(msg_text, disable_mentions=1)
                sent_cmid = sent_msg.conversation_message_id
            except Exception:
                await add_balance(user_id, bet) # Return bet
                await message.reply("❌ Ошибка запуска казино. Ставка возвращена.")
                return True
            
            # Calculate result using unified logic
            ud_eco = await get_user_economy_data(user_id)
            ud_full = await get_user_data(user_id)
            forced_rate = 0.0 if ud_full.get('no_comm_until', 0) > time.time() else None
            results, win, mult, commission, comm_rate = get_casino_result(bet, ud_eco.get('vip_level', 0), forced_rate=forced_rate)
            r1, r2, r3 = results

            # Suspense Animation
            # 1. Spin all (fast)
            for _ in range(4):
                await asyncio.sleep(0.3)
                spin_text = f"[{random.choice(slots_emojis)} {random.choice(slots_emojis)} {random.choice(slots_emojis)}]"
                try: await bot.api.messages.edit(peer_id=peer_id, conversation_message_id=sent_cmid, message=f"🎰 Казино запущено!\n👤 Игрок: {user_link}\n💰 Ставка: {bet:,}$\n\n🎡 Крутим... {spin_text}".replace(",", "."), disable_mentions=1)
                except: pass
            
            # 2. Stop 1st
            await asyncio.sleep(0.5)
            spin_text = f"[{r1} {random.choice(slots_emojis)} {random.choice(slots_emojis)}]"
            try: await bot.api.messages.edit(peer_id=peer_id, conversation_message_id=sent_cmid, message=f"🎰 Казино запущено!\n👤 Игрок: {user_link}\n💰 Ставка: {bet:,}$\n\n🎡 Крутим... {spin_text}".replace(",", "."), disable_mentions=1)
            except: pass

            # 3. Stop 2nd
            await asyncio.sleep(0.6)
            spin_text = f"[{r1} {r2} {random.choice(slots_emojis)}]"
            try: await bot.api.messages.edit(peer_id=peer_id, conversation_message_id=sent_cmid, message=f"🎰 Казино запущено!\n👤 Игрок: {user_link}\n💰 Ставка: {bet:,}$\n\n🎡 Крутим... {spin_text}".replace(",", "."), disable_mentions=1)
            except: pass

            # 4. Stop 3rd (Final)
            await asyncio.sleep(0.8)

            res_str = f"[{r1} {r2} {r3}]"
            keyboard = None
            if win > 0:
                await add_balance(user_id, win)
                
                econ = load_economy()
                if 'server_stats' not in econ: econ['server_stats'] = {'collected_commissions': 0}
                econ['server_stats']['collected_commissions'] += commission
                save_economy(econ)

                new_bal = await get_balance(user_id)
                final_text = f"🎰 Казино запущено!\n👤 Игрок: {user_link}\n💰 Ставка: {bet:,}$\n\n🎡 Результат: {res_str}\n✅ Выигрыш: {win:,}$ (x{mult})\n📉 Комиссия ({int(comm_rate*100)}%): {commission:,}$\n💰 Баланс: {new_bal:,}$".replace(",", ".")
                if mult >= 30:
                    announcement = f"🔥 ВНИМАНИЕ! 🔥\n🎰 Игрок {user_link} сорвал КУШ в казино!\n📈 Множитель: x{mult}\n💰 Сумма выигрыша: {win:,}$!".replace(",", ".")
                    await bot.api.messages.send(peer_id=peer_id, message=announcement, random_id=0, disable_mentions=1)
            else:
                new_bal = await get_balance(user_id)
                final_text = f"🎰 Казино запущено!\n👤 Игрок: {user_link}\n💰 Ставка: {bet:,}$\n\n🎡 Результат: {res_str}\n❌ Вы проиграли!\n💰 Баланс: {new_bal:,}$".replace(",", ".")
                keyboard = None

            try:
                await bot.api.messages.edit(peer_id=peer_id, conversation_message_id=sent_cmid, message=final_text, disable_mentions=1, keyboard=keyboard)
            except:
                await message.answer(final_text, disable_mentions=1, keyboard=keyboard)

        if command in ['buyvip', 'купитьвип', 'vip']:
            msg = "✨ Выберите уровень VIP-статуса (на 30 дней):\n\n"
            kb = Keyboard(inline=True)
            for tid, conf in VIP_CONFIG.items():
                msg += f"🔹 {conf['name']}:\n"
                msg += f"💰 Цена: {conf['price']:,}$\n".replace(",",".")
                msg += f"📉 Комиссия: {int(conf['comm']*100)}%\n"
                msg += f"🔨 Работа: в {conf['work_div']}x быстрее\n"
                msg += f"💰 Зарплата: +{conf['pay_bonus']}%\n"
                msg += f"🎁 Приз: x{conf['prize_mult']}\n\n"
                kb.add(Callback(conf['name'], {"command": "buy_vip_tier", "tier": tid, "chatId": chat_id}), color=conf['color']).row()
            
            kb.add(Callback("❌ Закрыть", {"command": "delete_msg"}), color=KeyboardButtonColor.SECONDARY)
            await message.reply(msg, keyboard=kb)
            return True

        if command in ['промо', 'promo', 'бонус']:
            if len(arguments) < 2:
                await message.reply("📝 Использование: /промо [код]", disable_mentions=1)
                return True
            
            promo_code = arguments[1].upper()
            
            # Загружаем коды из экономики
            econ = load_economy()
            
            # Коды по умолчанию
            default_codes = {
                'WELCOME': {'reward': 1000},
                'BONUS500': {'reward': 500},
                'VIP1000': {'reward': 1000},
                'LUCKY2000': {'reward': 2000}
            }
            
            # Объединяем стандартные и созданные администратором коды
            all_codes = default_codes.copy()
            if 'promo_codes' in econ and isinstance(econ['promo_codes'], dict):
                for code, data in econ['promo_codes'].items():
                    all_codes[code] = data
            
            if promo_code not in all_codes:
                await message.answer("❌ Неверный промо-код!", disable_mentions=1)
                return True
            
            code_data = all_codes[promo_code]
            # Поддержка старого формата (если в default_codes просто число)
            if isinstance(code_data, int):
                code_data = {'reward': code_data}

            # Проверяем лимит активаций для созданных кодов
            if 'promo_codes' in econ and promo_code in econ['promo_codes']:
                if code_data['max_uses'] is not None and code_data['uses'] >= code_data['max_uses']:
                    await message.answer(f"❌ Промо-код '{promo_code}' больше не доступен! Лимит активаций исчерпан.", disable_mentions=1)
                    return True
            
            user_id_str = str(user_id)
            user_eco = await get_user_economy_data(user_id)
            
            # Проверка на повторное использование
            if promo_code in user_eco.get('used_promos', []):
                await message.answer("❌ Вы уже использовали этот код!", disable_mentions=1)
                return True

            reward = code_data.get('reward', 0)
            vip_days = code_data.get('vip_days', 0)
            vip_lvl = code_data.get('vip_level', 1)
            
            result_msg = "✅ Промо-код активирован!"
            
            if reward > 0:
                await add_balance(user_id, reward)
                result_msg += f"\n💰 Получено: {reward:,}$".replace(",", ".")
            
            econ = load_economy()
            
            if vip_days > 0:
                u_data = econ['users'][user_id_str]
                current_now = datetime.now()
                if u_data.get('vip') and u_data.get('vip_until'):
                    until_dt = datetime.fromisoformat(u_data['vip_until'])
                    if until_dt < current_now: until_dt = current_now
                    new_until = (until_dt + timedelta(days=vip_days)).isoformat()
                else:
                    new_until = (current_now + timedelta(days=vip_days)).isoformat()
                u_data['vip'] = True
                u_data['vip_level'] = vip_lvl
                u_data['vip_until'] = new_until
                result_msg += f"\n✨ {VIP_CONFIG.get(vip_lvl, {'name': 'VIP'}).get('name')} на {vip_days} дн."

            if 'used_promos' not in econ['users'][user_id_str]:
                econ['users'][user_id_str]['used_promos'] = []
            
            econ['users'][user_id_str]['used_promos'].append(promo_code)
            if 'promo_codes' in econ and promo_code in econ['promo_codes']:
                econ['promo_codes'][promo_code]['uses'] += 1
            
            save_economy(econ)
            await message.answer(result_msg, disable_mentions=1)

        if command in ['promolist', 'списокпромо', 'кодысписок']:
            econ = load_economy()
            
            # Коды по умолчанию
            msg = "📋 Доступные промо-коды:\n\n"
            msg += "🔵 Стандартные коды:\n"
            msg += "WELCOME - +1000 монет\n"
            msg += "BONUS500 - +500 монет\n"
            msg += "VIP1000 - +1000 монет\n"
            msg += "LUCKY2000 - +2000 монет\n"
            
            
            msg += "\n💡 Каждый код можно использовать только один раз!"
            
            await message.reply(msg, disable_mentions=1)

        if command in ['status', 'статус', 'стаус']:
            if await get_role(user_id, chat_id) < 4:
                await message.reply("Недостаточно прав!")
                return True
            
            now = datetime.now().strftime("%H:%M:%S")
            
            # Информация о беседе для ст. админов и выше
            sql.execute("SELECT user_id FROM user_roles WHERE chat_id = ?", (chat_id,))
            mods = sql.fetchall()
            
            # Получаем тип беседы
            sql.execute("SELECT chat_type FROM chats WHERE chat_id = ?", (chat_id,))
            fetch_type = sql.fetchone()
            chat_type = fetch_type[0] if fetch_type and fetch_type[0] else 'def'

            ping_ms = await get_bot_ping_ms()
            ping_text = f"⚡ Пинг сервера (полл): {ping_ms} мс\n" if ping_ms is not None else "⚠️ Не удалось замерить пинг\n"

            msg = f"👤 Статус бота в беседе:\n"
            msg += f"🕒 Время сервера: {now}\n"
            msg += f"✅ Бот работает\n"
            msg += ping_text
            msg += f"🏷️ Тип беседы: {chat_type}\n\n"

            await message.reply(msg, disable_mentions=1)

        if command in ['открытьдепозит', 'opendepositvip']:
            econ = load_economy()
            user_id_str = str(user_id)
            if user_id_str not in econ['users']:
                await get_balance(user_id)
                econ = load_economy()

            if not econ['users'][user_id_str].get('vip', False):
                await message.reply("❌ Эта команда доступна только для VIP!", disable_mentions=1)
                return True

            if len(arguments) < 3:
                await message.reply("📝 Использование: /открытьдепозит [дни] [сумма]\nПример: /открытьдепозит 5 1000", disable_mentions=1)
                return True

            try:
                days = int(arguments[1])
                amount = int(arguments[2])
            except:
                await message.reply("Укажите корректное количество дней и сумму!", disable_mentions=1)
                return True

            if days <= 0 or days > 365:
                await message.reply("Указывайте количество дней от 1 до 365!", disable_mentions=1)
                return True

            if amount <= 0:
                await message.reply("Сумма должна быть больше 0!", disable_mentions=1)
                return True

            # Не даём открыть второй депозит, пока есть активный
            if econ['users'][user_id_str].get('deposits') and len(econ['users'][user_id_str].get('deposits', [])) > 0:
                await message.reply("❗ У вас уже есть открытый депозит. Закройте его командой /закрытьдепозит.", disable_mentions=1)
                return True

            if not await subtract_balance(user_id, amount):
                await message.reply("Недостаточно средств!", disable_mentions=1)
                return True

            econ = load_economy()
            settings = econ.get('settings', {})
            min_percent = settings.get('deposit_percent_min', 1)
            max_percent = settings.get('deposit_percent_max', 3)
            percent = random.randint(min_percent, max_percent)

            # Бонус к проценту за длительность вклада
            if days >= 360: percent += 7
            elif days >= 180: percent += 4
            elif days >= 90: percent += 2
            elif days >= 30: percent += 1

            if 'deposits' not in econ['users'][user_id_str] or not isinstance(econ['users'][user_id_str].get('deposits'), list):
                econ['users'][user_id_str]['deposits'] = []

            deposit = {
                'amount': amount,
                'created': datetime.now().isoformat(),
                'percent': percent,
                'duration_days': days,
                'close_date': (datetime.now() + timedelta(days=days)).isoformat()
            }
            econ['users'][user_id_str]['deposits'].append(deposit)
            save_economy(econ)

            total_expected = amount + (amount * percent * days) // 100
            await message.reply(f"✅ Депозит открыт!\n💰 Сумма: {amount:,}$\n📅 На {days} дней\n💎 Под {percent}% в день\n💵 К получению: За {days}д будет ~{total_expected:,}$".replace(",", "."), disable_mentions=1)

        if command in ['закрытьдепозит', 'closedepositvip']:
            econ = load_economy()
            user_id_str = str(user_id)
            if user_id_str not in econ['users']:
                await get_balance(user_id)
                econ = load_economy()

            deposits = econ['users'][user_id_str].get('deposits')
            if not deposits or not isinstance(deposits, list) or len(deposits) == 0:
                await message.reply("❌ У вас нет открытых депозитов!", disable_mentions=1)
                return True

            deposit = deposits[0]
            try:
                created = datetime.fromisoformat(deposit.get('created', ''))
                days_passed = max((datetime.now() - created).days, 0)
            except Exception:
                days_passed = 0

            percent = int(deposit.get('percent', 5))
            amount = int(deposit.get('amount', 0))
            duration_days = int(deposit.get('duration_days', 0))

            interest = (amount * percent * days_passed) // 100
            total = amount + interest

            await add_balance(user_id, total)
            econ = load_economy()
            econ['users'][user_id_str]['deposits'].pop(0)
            save_economy(econ)
            
            await message.reply(
                f"✅ Депозит закрыт!\n"
                f"Основная сумма: {amount}\n"
                f"Процент: {percent}%/день\n"
                f"Прошло дней: {days_passed}/{duration_days}\n"
                f"Проценты за весь срок ({duration_days} дн.): {interest}\n"
                f"💵 Итого получено: {total}",
                disable_mentions=1
            )

        if command in ['offer', 'предложение', 'suggestion']:
            if len(arguments) < 2:
                await message.reply("📝 Использование: /offer [ваше предложение]", disable_mentions=1)
                return True
            
            suggestion = await get_string(arguments, 1)
            
            sql.execute("SELECT end_time FROM report_bans WHERE user_id = ?", (user_id,))
            ban_info = sql.fetchone()
            if ban_info:
                end_time = ban_info[0]
                if end_time == 0 or end_time > time.time(): # Ban is permanent or still active
                    return await message.reply("❌ Вы заблокированы в системе предложений.")
                else: # Ban expired, remove it
                    sql.execute("DELETE FROM report_bans WHERE user_id = ?", (user_id,))
                    database.commit()

            date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sql.execute("INSERT INTO support_tickets (user_id, type, text, date, chat_id) VALUES (?, 'offer', ?, ?, ?)", (user_id, suggestion, date_str, chat_id))
            tid = sql.lastrowid
            database.commit()
            
            user_link = await get_user_link(user_id)
            msg_to_admins = f"💡 НОВОЕ ПРЕДЛОЖЕНИЕ #{tid}\n👤 От: {user_link}\n📄 Текст: {suggestion}\n\n⏳ Статус: Ожидание"
            
            kb = (
                Keyboard(inline=True)
                .add(Callback("✉️ Ответить", {"command": "ticket_reply", "id": tid}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("⏳ Рассмотреть", {"command": "ticket_consider", "id": tid}), color=KeyboardButtonColor.PRIMARY).row()
                .add(Callback("❌ Отклонить", {"command": "ticket_reject", "id": tid}), color=KeyboardButtonColor.NEGATIVE)
            )

            try:
                await bot.api.messages.send(peer_id=CREATOR_ID, message=msg_to_admins, random_id=0, keyboard=kb)
                await send_log(msg_to_admins, keyboard=kb)
                await message.reply(f"✅ Предложение #{tid} успешно отправлено администрации проекта.")
            except Exception as e:
                await message.reply(f"❌ Ошибка отправки: {e}")

        if command in ['report', 'жалоба', 'репорт']:
            # Проверка блокировки в системе репортов
            sql.execute("SELECT end_time FROM report_bans WHERE user_id = ?", (user_id,))
            ban_info = sql.fetchone()
            if ban_info:
                end_time = ban_info[0]
                if end_time == 0 or end_time > time.time(): # Бан навсегда или время не вышло
                    return await message.reply("❌ Вы заблокированы в системе репортов.")
                else: # Срок бана истек
                    sql.execute("DELETE FROM report_bans WHERE user_id = ?", (user_id,)); database.commit()

            if len(arguments) < 2 and not message.reply_message:
                return await message.reply("📝 Использование: /report [суть жалобы] (или ответьте на сообщение нарушителя)")

            target_id = message.reply_message.from_id if message.reply_message else 0
            report_text = await get_string(arguments, 1) or "Без описания"
            date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            user_link = await get_user_link(user_id)
            
            chat_title = "Личные сообщения"
            if chat_id:
                try:
                    conv = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id)
                    chat_title = conv.items[0].chat_settings.title
                except: chat_title = f"Чат #{chat_id}"
            
            sql.execute("INSERT INTO support_tickets (user_id, type, text, date, chat_id, target_id) VALUES (?, 'report', ?, ?, ?, ?)", (user_id, report_text, date_str, chat_id, target_id))
            tid = sql.lastrowid
            database.commit()

            msg_to_admins = f"🚩 НОВЫЙ РЕПОРТ #{tid}\n👤 От: {user_link}\n📍 Где: {chat_title}\n\n⏳ Статус: Ожидание\n\n"
            
            if target_id:
                target_link = await get_user_link(target_id)
                msg_to_admins += f"👤 Нарушитель: {target_link}\n💬 Сообщение: {message.reply_message.text}\n\n"
            
            msg_to_admins += f"📄 Суть: {report_text}"

            kb = (
                Keyboard(inline=True)
                .add(Callback("✉️ Ответить", {"command": "ticket_reply", "id": tid}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("⏳ Рассмотреть", {"command": "ticket_consider", "id": tid}), color=KeyboardButtonColor.PRIMARY).row()
                .add(Callback("❌ Отклонить", {"command": "ticket_reject", "id": tid}), color=KeyboardButtonColor.NEGATIVE)
            )

            try:
                # Отправляем владельцу и в чат логов
                await bot.api.messages.send(peer_id=CREATOR_ID, message=msg_to_admins, random_id=0, keyboard=kb)
                await send_log(msg_to_admins, keyboard=kb)
                # Подтверждение пользователю
                await message.reply(f"✅ Ваша жалоба #{tid} успешно отправлена администрации проекта.")
            except Exception as e:
                await message.reply(f"❌ Не удалось отправить репорт: {e}")

        if command in ['giveupgrade', 'выдатьулучшение']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("❌ Только разработчик может использовать эту команду!", disable_mentions=1)
                return True
            
            if len(arguments) < 3:
                await message.reply("📝 Использование: /giveupgrade [ID клана] [уровень]", disable_mentions=1)
                return True
            
            try:
                target_clan_id = int(arguments[1])
                target_level = int(arguments[2])
            except:
                await message.reply("Укажите корректные ID клана и уровень!", disable_mentions=1)
                return True
            
            if target_level < 1 or target_level > len(CLAN_LEVELS):
                await message.reply(f"Уровень должен быть от 1 до {len(CLAN_LEVELS)}!", disable_mentions=1)
                return True

            sql.execute("SELECT name FROM clans WHERE clan_id = ?", (target_clan_id,))
            clan = sql.fetchone()
            if not clan:
                await message.reply("❌ Клан не найден!", disable_mentions=1)
                return True
                
            sql.execute("UPDATE clans SET level = ? WHERE clan_id = ?", (target_level, target_clan_id))
            database.commit()
            await save_clan_to_json(target_clan_id)
            
            level_title = CLAN_LEVELS.get(target_level, {}).get('title', f'Уровень {target_level}')
            await message.reply(f"✅ Клану «{clan[0]}» установлен уровень {target_level} ({level_title})!", disable_mentions=1)
            await log_action(user_id, chat_id, f"Установил уровень {target_level} клану «{clan[0]}» (ID: {target_clan_id}).")
            return True

        if command in ['выдатьмонеты', 'givemoney', 'givecash']:
            # Проверяем, что это владелец беседы или администратор
            x = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id, extended=1, fields='chat_settings', group_id=message.group_id)
            x = json.loads(x.json())
            owner_id = None
            for i in x['items']: 
                owner_id = int(i["chat_settings"]["owner_id"])
            
            if not await check_perm(user_id, chat_id, command, 6):
                await message.reply("❌ Только разработчик может выдавать монеты!", disable_mentions=1)
                return True
            
            recipient_id = user_id
            amount = 0
            
            if message.reply_message:
                recipient_id = message.reply_message.from_id
                try:
                    amount = int(arguments[1])
                except:
                    await message.reply("Укажите корректное количество монет!", disable_mentions=1)
                    return True
            elif len(arguments) >= 3 and await getID(arguments[1]):
                recipient_id = await getID(arguments[1])
                try:
                    amount = int(arguments[2])
                except:
                    await message.reply("Укажите корректное количество монет!", disable_mentions=1)
                    return True
            else:
                await message.reply("📝 Использование: /выдатьмонеты [пользователь] [количество]", disable_mentions=1)
                return True
            
            if amount <= 0:
                await message.reply("Количество должно быть больше 0!", disable_mentions=1)
                return True
            
            await add_balance(recipient_id, amount)
            
            # Линкабельные имена (через VK API)
            try:
                owner_info = await bot.api.users.get(user_ids=user_id)
                owner_name = f"[id{user_id}|{owner_info[0].first_name} {owner_info[0].last_name}]"
            except:
                owner_name = f"[id{user_id}|{await get_user_name(user_id, chat_id)}]"

            try:
                recipient_info = await bot.api.users.get(user_ids=recipient_id)
                recipient_name = f"[id{recipient_id}|{recipient_info[0].first_name} {recipient_info[0].last_name}]"
            except:
                recipient_name = f"[id{recipient_id}|{await get_user_name(recipient_id, chat_id)}]"

            await message.reply(
                f"✅ Монеты выданы!\n"
                f"От: {owner_name}\n"
                f"Кому: {recipient_name}\n"
                f"Сумма: {amount} 💰",
                disable_mentions=1
            )
            try: u_info = await bot.api.users.get(user_ids=recipient_id); u_name = f"[id{recipient_id}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{recipient_id}"
            await log_action(user_id, chat_id, f"Выдал {amount} монет пользователю {u_name}.")

        if command in ['resetmoney', 'обнулить', 'resetbalance']:
            if not await check_perm(user_id, chat_id, command, 6):
                await message.reply("❌ Только разработчик может обнулять баланс!", disable_mentions=1)
                return True
            
            target_user = 0
            if message.reply_message:
                target_user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                target_user = await getID(arguments[1])
            else:
                await message.reply("📝 Использование: /обнулить [пользователь]", disable_mentions=1)
                return True
            
            await set_balance(target_user, 0)
            await set_bank(target_user, 0)
            
            try:
                user_info = await bot.api.users.get(user_ids=target_user)
                target_name = f"[id{target_user}|{user_info[0].first_name} {user_info[0].last_name}]"
            except:
                target_name = f"[id{target_user}|{await get_user_name(target_user, chat_id)}]"

            await message.reply(f"✅ Баланс и банк пользователя {target_name} полностью обнулены.", disable_mentions=1)
            try: u_info = await bot.api.users.get(user_ids=target_user); u_name = f"[id{target_user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{target_user}"
            await log_action(user_id, chat_id, f"Обнулил баланс пользователя {u_name}.")

        if command in ['setbalance', 'setbal', 'установитьбаланс']:
            if not await check_perm(user_id, chat_id, command, 6):
                await message.reply("❌ Только разработчик может устанавливать баланс!", disable_mentions=1)
                return True
            
            target_user = 0
            amount = 0
            
            if message.reply_message:
                target_user = message.reply_message.from_id
                try: amount = int(arguments[1])
                except:
                    await message.reply("Укажите корректную сумму!", disable_mentions=1)
                    return True
            elif len(arguments) >= 3 and await getID(arguments[1]):
                target_user = await getID(arguments[1])
                try: amount = int(arguments[2])
                except:
                    await message.reply("Укажите корректную сумму!", disable_mentions=1)
                    return True
            else:
                await message.reply("📝 Использование: /setbalance [пользователь] [сумма]", disable_mentions=1)
                return True
            
            if amount < 0:
                await message.reply("Сумма не может быть отрицательной!", disable_mentions=1)
                return True

            await set_balance(target_user, amount)
            
            try:
                user_info = await bot.api.users.get(user_ids=target_user)
                target_name = f"[id{target_user}|{user_info[0].first_name} {user_info[0].last_name}]"
            except:
                target_name = f"[id{target_user}|{await get_user_name(target_user, chat_id)}]"

            await message.reply(f"✅ Баланс пользователя {target_name} установлен на {amount:,}$.".replace(",", "."), disable_mentions=1)
            try: u_info = await bot.api.users.get(user_ids=target_user); u_name = f"[id{target_user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{target_user}"
            await log_action(user_id, chat_id, f"Установил баланс пользователя {u_name} на {amount}$.")

        if command in ['giveslot', 'выдатьслот']:
            if await get_role(user_id, chat_id) < 6:
                return True
            
            target, err = await get_target_user(message, arguments)
            if err: return await message.reply(err)
            
            try: 
                count = int(arguments[-1])
            except: 
                return await message.reply("📝 Использование: /giveslot [user] [кол-во]")
            
            ud = await get_user_data(target)
            new_slots = ud.get('biz_slots', 0) + count
            await update_user_data(target, 'biz_slots', new_slots)
            
            await message.reply(f"✅ Пользователю {await get_user_link(target)} выдано {count} слотов для бизнеса (Всего: {2 + new_slots}).")
            await log_action(user_id, chat_id, f"Выдал {count} слотов для бизнеса пользователю {target}.")
            return True

        if command in ['slots', 'слоты', 'бизслоты']:
            # Вызываем то же меню, что и через кнопку
            payload = {"command": "slots_menu", "chatId": chat_id, "user": user_id}
            await main_event_handlers(GroupTypes.MessageEvent(group_id=message.group_id, event_id="0", object={"peer_id": message.peer_id, "user_id": user_id, "payload": payload, "event_id": "0", "conversation_message_id": message.conversation_message_id}))
            return True

        if command in ['givemats', 'выдатьматы']:
            # Проверяем, что это владелец беседы или администратор
            x = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id, extended=1, fields='chat_settings', group_id=message.group_id)
            x = json.loads(x.json())
            owner_id = None
            for i in x['items']: 
                owner_id = int(i["chat_settings"]["owner_id"])
            
            if not await check_perm(user_id, chat_id, command, 6):
                await message.reply("❌ Только разработчик может выдавать материалы!", disable_mentions=1)
                return True
            
            if len(arguments) < 3:
                await message.reply("📝 Использование: /givemats [ID клана] [количество]", disable_mentions=1)
                return True
            
            try:
                target_clan_id = int(arguments[1])
                amount = int(arguments[2])
            except:
                await message.reply("Укажите корректные ID и количество!", disable_mentions=1)
                return True
            
            if amount == 0:
                await message.reply("Количество не может быть 0!", disable_mentions=1)
                return True

            sql.execute("SELECT name FROM clans WHERE clan_id = ?", (target_clan_id,))
            clan = sql.fetchone()
            if not clan:
                await message.reply("❌ Клан не найден!", disable_mentions=1)
                return True
                
            sql.execute("UPDATE clans SET mats = mats + ? WHERE clan_id = ?", (amount, target_clan_id))
            database.commit()
            
            await message.reply(f"✅ Клану «{clan[0]}» выдано {amount} материалов!", disable_mentions=1)

        if command in ['giveexp', 'выдатьопыт']:
            # Проверяем, что это владелец беседы или администратор
            x = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id, extended=1, fields='chat_settings', group_id=message.group_id)
            x = json.loads(x.json())
            owner_id = None
            for i in x['items']: 
                owner_id = int(i["chat_settings"]["owner_id"])
            
            if not await check_perm(user_id, chat_id, command, 6):
                await message.reply("❌ Только разработчик может выдавать опыт клану!", disable_mentions=1)
                return True
            
            if len(arguments) < 3:
                await message.reply("📝 Использование: /giveexp [ID клана] [количество]", disable_mentions=1)
                return True
            
            try:
                target_clan_id = int(arguments[1])
                amount = int(arguments[2])
            except:
                await message.reply("Укажите корректные ID и количество!", disable_mentions=1)
                return True
            
            if amount == 0:
                await message.reply("Количество не может быть 0!", disable_mentions=1)
                return True

            sql.execute("SELECT name FROM clans WHERE clan_id = ?", (target_clan_id,))
            clan = sql.fetchone()
            if not clan:
                await message.reply("❌ Клан не найден!", disable_mentions=1)
                return True
                
            sql.execute("UPDATE clans SET exp = exp + ? WHERE clan_id = ?", (amount, target_clan_id))
            database.commit()
            
            await message.reply(f"✅ Клану «{clan[0]}» выдано {amount} опыта!", disable_mentions=1)
            await log_action(user_id, chat_id, f"Выдал {amount} опыта клану «{clan[0]}» (ID: {target_clan_id}).")

        if command in ['cancelwar', 'отменитьвойну']:
            if not await check_perm(user_id, chat_id, command, 6):
                await message.reply("❌ Только разработчик может отменять войны!", disable_mentions=1)
                return True
            
            if len(arguments) < 2:
                await message.reply("📝 Использование: /cancelwar [ID войны]", disable_mentions=1)
                return True
            
            try:
                war_id = int(arguments[1])
            except:
                await message.reply("ID войны должен быть числом!", disable_mentions=1)
                return True
            
            sql.execute("SELECT attacker_id, defender_id, status FROM clan_wars WHERE war_id = ?", (war_id,))
            war = sql.fetchone()
            
            if not war:
                await message.reply("❌ Война с таким ID не найдена!", disable_mentions=1)
                return True
            
            # Возврат ресурсов, если война была активна (ресурсы списываются при начале)
            if war[2] == 'active':
                sql.execute("UPDATE clans SET money = money + ?, mats = mats + ?, exp = exp + ? WHERE clan_id IN (?, ?)", 
                            (CLAN_WAR_COST_MONEY, CLAN_WAR_COST_MATS, CLAN_WAR_COST_EXP, war[0], war[1]))
                await save_clan_to_json(war[0])
                await save_clan_to_json(war[1])
            
            sql.execute("DELETE FROM clan_wars WHERE war_id = ?", (war_id,))
            database.commit()
            
            await message.reply(f"✅ Война #{war_id} принудительно отменена! (Статус был: {war[2]})", disable_mentions=1)
            await log_action(user_id, chat_id, f"Отменил войну #{war_id}.")

        if command in ['activewars', 'активныевойны']:
            if not await check_perm(user_id, chat_id, command, 6):
                await message.reply("❌ Только разработчик может смотреть активные войны!", disable_mentions=1)
                return True
            
            sql.execute("SELECT war_id, attacker_id, defender_id, start_time, end_time FROM clan_wars WHERE status = 'active'")
            wars = sql.fetchall()
            
            if not wars:
                await message.reply("Нет активных войн.")
                return True
                
            msg = "⚔ Активные войны:\n"
            for w in wars:
                sql.execute("SELECT name FROM clans WHERE clan_id = ?", (w[1],))
                att_name = sql.fetchone()[0]
                sql.execute("SELECT name FROM clans WHERE clan_id = ?", (w[2],))
                def_name = sql.fetchone()[0]
                rem_time = int((w[4] - time.time()) / 60)
                msg += f"🆔 {w[0]} | {att_name} vs {def_name} | ⏳ {rem_time} мин\n"
            
            await message.reply(msg)

        if command in ['выдатьвип', 'givevip', 'setvip']:
            # Проверяем, что это владелец беседы
            x = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id, extended=1, fields='chat_settings', group_id=message.group_id)
            x = json.loads(x.json())
            owner_id = None
            for i in x['items']: 
                owner_id = int(i["chat_settings"]["owner_id"])
            
            if not await check_perm(user_id, chat_id, command, 6):
                await message.reply("❌ Только разработчик может выдавать VIP!", disable_mentions=1)
                return True
            
            recipient_id = user_id
            days = 30
            vip_level_to_set = 1 # Default VIP level

            # Determine recipient and starting index for days/level arguments
            arg_start_idx = 1 # Default for /command [days] [level]
            
            if message.reply_message:
                recipient_id = message.reply_message.from_id
                arg_start_idx = 1 # Days starts at arguments[1]
            elif len(arguments) > 1:
                resolved_id = await getID(arguments[1])
                if resolved_id:
                    recipient_id = resolved_id
                    arg_start_idx = 2 # Days starts at arguments[2]
                else:
                    # If arg[1] is not a user ID, assume it's days for current user
                    recipient_id = user_id
                    arg_start_idx = 1
            else:
                # No arguments, no reply, assume current user, default days/level
                recipient_id = user_id
                arg_start_idx = 1

            # Parse days argument
            if len(arguments) > arg_start_idx and arguments[arg_start_idx].isdigit():
                days = int(arguments[arg_start_idx])
                
            # Parse VIP level argument (if provided after days)
            if len(arguments) > arg_start_idx + 1 and arguments[arg_start_idx + 1].isdigit():
                potential_level = int(arguments[arg_start_idx + 1])
                if potential_level in VIP_CONFIG:
                    vip_level_to_set = potential_level
                else:
                    await message.reply("❌ Неверный уровень VIP! Доступны 1 или 2.", disable_mentions=1)
                    return True
            
            if recipient_id == user_id and arg_start_idx == 1 and len(arguments) < 2:
                await message.reply("📝 Использование: /выдатьвип [пользователь] [дней=30] [уровень=1]", disable_mentions=1)
                return True
            
            # Выдаем ВИП статус
            econ = load_economy()
            user_id_str = str(recipient_id)
            if user_id_str not in econ['users']:
                await get_balance(recipient_id)
                econ = load_economy()

            u_data = econ['users'][user_id_str]
            current_now = datetime.now()
            if u_data.get('vip') and u_data.get('vip_until'):
                try:
                    base_date = datetime.fromisoformat(u_data['vip_until'])
                    if base_date < current_now: base_date = current_now
                except:
                    base_date = current_now
            else:
                base_date = current_now
            
            vip_until = (base_date + timedelta(days=days)).isoformat()
            u_data['vip'] = True
            u_data['vip_level'] = vip_level_to_set
            u_data['vip_until'] = vip_until
            save_economy(econ)
            
            recipient_name = await get_user_name(recipient_id, chat_id)
            vip_name = VIP_CONFIG.get(vip_level_to_set, {}).get('name', f'VIP {vip_level_to_set}')
            await message.reply(f"✨ {vip_name} статус выдан {recipient_name} на {days} дней!", disable_mentions=1)
            try: u_info = await bot.api.users.get(user_ids=recipient_id); u_name = f"[id{recipient_id}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{recipient_id}"
            await log_action(user_id, chat_id, f"Выдал VIP пользователю {u_name} на {days} дней.")

        if command in ['раздача', 'giveall', 'раздать']:
            # Проверяем, что это владелец беседы
            x = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id, extended=1, fields='chat_settings', group_id=message.group_id)
            x = json.loads(x.json())
            owner_id = None
            for i in x['items']: 
                owner_id = int(i["chat_settings"]["owner_id"])
            
            if not await check_perm(user_id, chat_id, command, 6):
                await message.reply("Только разработчик может проводить раздачу!", disable_mentions=1)
                return True
            
            if len(arguments) < 2:
                await message.reply("📝 Использование: /раздача [сумма]", disable_mentions=1)
                return True
            
            try:
                amount_per_user = int(arguments[1])
            except:
                await message.reply("Укажите корректную сумму!", disable_mentions=1)
                return True
            
            if amount_per_user <= 0:
                await message.reply("Сумма должна быть больше 0!", disable_mentions=1)
                return True
            
            # Получаем участников беседы
            try:
                members = await bot.api.messages.get_conversation_members(peer_id=message.peer_id)
                members_list = members.items
                
                count = 0
                for member in members_list:
                    if member.member_id > 0:  # Пропускаем сообщества
                        await add_balance(member.member_id, amount_per_user)
                        count += 1
                
                await message.reply(f"🎁 Раздача завершена!\n✅ Выдано {count} участникам по {amount_per_user} монет!", disable_mentions=1)
            except Exception as e:
                print(f"Ошибка при раздаче: {e}")
                await message.reply("Не удалось провести раздачу!", disable_mentions=1)

        if command in ['sync', 'синк', 'синхро']:
            # Проверяем, что это владелец беседы
            x = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id, extended=1, fields='chat_settings', group_id=message.group_id)
            x = json.loads(x.json())
            owner_id = None
            for i in x['items']: 
                owner_id = int(i["chat_settings"]["owner_id"])
            
            if not await check_perm(user_id, chat_id, command, 6):
                await message.reply("Только разработчик может синхронизировать базу!", disable_mentions=1)
                return True
            
            try:
                # Синхронизируем базу данных
                database.commit()
                
                sql.execute("SELECT clan_id FROM clans")
                all_clans = sql.fetchall()
                for (cid,) in all_clans:
                    await save_clan_to_json(cid)
                    await asyncio.sleep(0.01)

                await message.reply(f"✅ Синхронизация БД выполнена!", disable_mentions=1)
                await log_action(user_id, chat_id, "Выполнил синхронизацию БД.")
            except Exception as e:
                print(f"Ошибка при синхронизации: {e}")
                await message.reply(f"❌ Ошибка при синхронизации: {e}", disable_mentions=1)

        if command in ['type', 'тип', 'типбеседы']:
            # Проверяем, что это владелец беседы
            x = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id, extended=1, fields='chat_settings', group_id=message.group_id)
            x = json.loads(x.json())
            owner_id = None
            for i in x['items']: 
                owner_id = int(i["chat_settings"]["owner_id"])
            
            if not await check_perm(user_id, chat_id, command, 6):
                await message.reply("Только разработчик может выбирать тип!", disable_mentions=1)
                return True
            
            # Страница 1 (макс 10 кнопок)
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("DEF", {"command": "set_type", "type": "def", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("EXT", {"command": "set_type", "type": "ext", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("PL", {"command": "set_type", "type": "pl", "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
                .row()
                .add(Callback("HEL", {"command": "set_type", "type": "hel", "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
                .add(Callback("LD", {"command": "set_type", "type": "ld", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("ADM", {"command": "set_type", "type": "adm", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .row()
                .add(Callback("MOD", {"command": "set_type", "type": "mod", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("TEX", {"command": "set_type", "type": "tex", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("TEST", {"command": "set_type", "type": "test", "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .row()
                .add(Callback("Дальше >>", {"command": "type_page", "page": 2, "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY)
            )
            
            await message.reply("Выберите тип беседы:", keyboard=keyboard)

        if command in ['создатьпромо', 'createpromo', 'newpromo']:
            # Проверяем, что это владелец беседы или администратор
            x = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id, extended=1, fields='chat_settings', group_id=message.group_id)
            x = x.model_dump() if hasattr(x, "model_dump") else json.loads(x.json())
            owner_id = None
            for i in x['items']: 
                owner_id = int(i["chat_settings"]["owner_id"])
            
            if not await check_perm(user_id, chat_id, command, 6):
                await message.reply("❌ Только разработчик может создавать промо-коды!", disable_mentions=1)
                return True
            
            if len(arguments) < 3:
                await message.reply("📝 Использование: /создатьпромо [код] [награда] [лимит] [дней_вип] [уровень_вип]", disable_mentions=1)
                return True
            
            promo_code = arguments[1].upper()
            try:
                reward = int(arguments[2])
            except:
                await message.reply("Укажите корректное количество монет для награды!", disable_mentions=1)
                return True
            
            # Количество активаций - опционально (по умолчанию бесконечно)
            max_uses = None
            if len(arguments) >= 4:
                try:
                    max_uses = int(arguments[3])
                    if max_uses <= 0:
                        await message.reply("Количество активаций должно быть больше 0!", disable_mentions=1)
                        return True
                except:
                    await message.reply("Укажите корректное количество активаций!", disable_mentions=1)
                    return True
            
            vip_days = 0
            if len(arguments) >= 5:
                try: vip_days = int(arguments[4])
                except:
                    return await message.reply("Дни VIP должны быть числом!")
            
            vip_level = 1
            if len(arguments) >= 6:
                try: 
                    vip_level = int(arguments[5])
                    if vip_level not in [1, 2]: raise ValueError
                except: return await message.reply("❌ Уровень VIP должен быть 1 или 2!")
            
            if reward <= 0:
                await message.reply("Награда должна быть больше 0!", disable_mentions=1)
                return True
            
            # Сохраняем промо-код
            econ = load_economy()
            
            if 'promo_codes' not in econ:
                econ['promo_codes'] = {}
            
            if promo_code in econ['promo_codes']:
                await message.reply(f"❌ Промо-код '{promo_code}' уже существует!", disable_mentions=1)
                return True
            
            econ['promo_codes'][promo_code] = {
                'reward': reward,
                'vip_days': vip_days,
                'vip_level': vip_level,
                'created_by': user_id,
                'created_date': datetime.now().isoformat(),
                'uses': 0,
                'max_uses': max_uses
            }
            save_economy(econ)
            
            max_uses_text = f" (максимум {max_uses} активаций)" if max_uses else " (неограниченное количество активаций)"
            vip_text = f" + {VIP_CONFIG.get(vip_level, {}).get('name', 'VIP')} на {vip_days} дн." if vip_days > 0 else ""
            await message.reply(
                f"✅ Промо-код создан!\n"
                f"Код: {promo_code}\n"
                f"Награда: {reward:,}$ 💰{vip_text}{max_uses_text}".replace(",", "."),
                disable_mentions=1
            )
            await log_action(user_id, chat_id, f"Создал промо-код «{promo_code}» (Награда: {reward}, Активаций: {max_uses if max_uses else '∞'}).")

        if command in ['удалитьпромо', 'deletepromo', 'removepromo']:
            # Проверяем, что это владелец беседы или администратор
            x = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id, extended=1, fields='chat_settings', group_id=message.group_id)
            if not await check_perm(user_id, chat_id, command, 6):
                await message.reply("❌ Только разработчик может удалять промо-коды!", disable_mentions=1)
                return True
            
            if len(arguments) < 2:
                await message.reply("📝 Использование: /удалитьпромо [код]", disable_mentions=1)
                return True
            
            promo_code = arguments[1].upper()
            
            # Удаляем промо-код
            econ = load_economy()
            
            if 'promo_codes' not in econ or promo_code not in econ['promo_codes']:
                await message.reply(f"❌ Промо-код '{promo_code}' не найден!", disable_mentions=1)
                return True
            
            del econ['promo_codes'][promo_code]
            save_economy(econ)
            
            await message.reply(f"✅ Промо-код '{promo_code}' удален!", disable_mentions=1)
            await log_action(user_id, chat_id, f"Удалил промо-код «{promo_code}».")

        if command in ['say', 'сказать', 'отправить']:
            # Проверяем, что это владелец беседы
            x = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id, extended=1, fields='chat_settings', group_id=message.group_id)
            x = json.loads(x.json())
            owner_id = None
            for i in x['items']: 
                owner_id = int(i["chat_settings"]["owner_id"])
            
            if not await check_perm(user_id, chat_id, command, 6):
                await message.reply("❌ Только разработчик может использовать эту команду!", disable_mentions=1)
                return True
            
            if len(arguments) < 3:
                await message.reply("📝 Использование: /say [chat_id] [сообщение]\nПример: /say 1 привет", disable_mentions=1)
                return True
            
            try:
                target_chat_id = int(arguments[1])
            except:
                await message.reply("❌ Неверный chat_id!", disable_mentions=1)
                return True
            
            text = await get_string(arguments, 2)
            if not text:
                await message.reply("Укажите текст сообщения!", disable_mentions=1)
                return True
            
            try:
                # Преобразуем chat_id в peer_id (peer_id = 2000000000 + chat_id)
                target_peer_id = 2000000000 + target_chat_id
                await bot.api.messages.send(
                    peer_id=target_peer_id,
                    message=text,
                    random_id=0
                )
                await message.reply(f"✅ Сообщение отправлено в чат {target_chat_id}!", disable_mentions=1)
                await log_action(user_id, chat_id, f"Отправил сообщение от имени бота в чат {target_chat_id}:\n{text}")
            except Exception as e:
                await message.reply(f"❌ Ошибка при отправке: {str(e)}", disable_mentions=1)

        if command in ['notop', 'скрытьтоп']:
            target_user = user_id
            is_self = True
            if message.reply_message:
                target_user = message.reply_message.from_id
                is_self = (user_id == target_user)
            elif len(arguments) > 1 and await getID(arguments[1]):
                target_user = await getID(arguments[1])
                is_self = (user_id == target_user)

            sender_global_role = await get_global_role(user_id)
            ud = await get_user_data(target_user)

            sql.execute("SELECT 1 FROM notop_users WHERE user_id = ?", (target_user,))
            is_hidden = sql.fetchone()

            if is_hidden:
                # Unhiding is always free and allowed for self or by admin
                if is_self or sender_global_role >= 5:
                    sql.execute("DELETE FROM notop_users WHERE user_id = ?", (target_user,))
                    database.commit()
                    target_name = await get_user_name(target_user, chat_id)
                    await message.reply(f"✅ Пользователь [id{target_user}|{target_name}] теперь снова отображается в топе.")
                else:
                    await message.reply("❌ Вы не можете вернуть другого пользователя в топ.")
            else:
                # Hiding logic
                if ud.get('has_notop') == 1 or sender_global_role >= 5: # Feature bought or Admin
                    sql.execute("INSERT OR IGNORE INTO notop_users (user_id) VALUES (?)", (target_user,))
                    database.commit()
                    target_name = await get_user_name(target_user, chat_id)
                    await message.reply(f"✅ Пользователь [id{target_user}|{target_name}] скрыт из топа.")
                else: # Must buy
                    if not is_self:
                        await message.reply("❌ Вы можете скрыть из топа только себя.")
                        return True
                    
                    cost = 1000000
                    if await subtract_balance(user_id, cost):
                        await update_user_data(user_id, 'has_notop', 1)
                        sql.execute("INSERT OR IGNORE INTO notop_users (user_id) VALUES (?)", (target_user,))
                        database.commit()
                        await message.reply(f"✅ Вы купили услугу и скрыты из топа. Списано {cost:,} монет. Теперь вы сможете скрываться и открываться бесплатно!".replace(",", "."))
                    else:
                        await message.reply(f"❌ Недостаточно средств для покупки услуги. Стоимость: {cost:,} монет.".replace(",", "."))

        if command in ['help', 'помощь', 'хелп', 'команды', 'commands']:
            user_role = await get_role(user_id, chat_id)
            is_tester = await get_tester_role(user_id) >= 1
            
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("👤 Пользователь", {"command": "help_menu", "category": "user", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("💰 Экономика", {"command": "help_menu", "category": "economy", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .row()
            )
            if is_tester or user_role >= 6:
                keyboard.add(Callback("🧪 Тестеры", {"command": "help_menu", "category": "tester", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            keyboard.add(Callback("🏰 Кланы", {"command": "help_menu", "category": "clans", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            
            if user_role >= 1:
                keyboard.row().add(Callback("🛡 Модерация", {"command": "help_menu", "category": "staff", "chatId": chat_id}), color=KeyboardButtonColor.SECONDARY).row()
            else:
                keyboard.row()
            
            keyboard.add(Callback("❌ Закрыть", {"command": "delete_msg", "user": user_id}), color=KeyboardButtonColor.NEGATIVE)

            await message.reply("📚 Меню помощи\nВыберите категорию команд:", disable_mentions=1, keyboard=keyboard)

        if command in ['выдатьдолжность', 'giveposition', 'setposition']:
            if not await check_perm(user_id, chat_id, command, 6):
                await message.reply("❌ Только разработчик может выдавать должности!", disable_mentions=1)
                return True
            
            target_user = 0
            if message.reply_message:
                target_user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                target_user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                target_user = await getID(arguments[1])
            else:
                await message.reply("📝 Использование: /выдатьдолжность [пользователь]", disable_mentions=1)
                return True
            
            positions = {
                '1': 'Младший модератор',
                '2': 'Модератор',
                '3': 'Старший модератор',
                '4': 'Куратор модерации',
                '5': 'Заместитель главного модератора',
                '6': 'Главный модератор'
            }
            
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("1️⃣ Младший модератор", {"command": "set_position", "position": positions['1'], "user": target_user, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("2️⃣ Модератор", {"command": "set_position", "position": positions['2'], "user": target_user, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.PRIMARY)
                .row()
                .add(Callback("3️⃣ Старший модератор", {"command": "set_position", "position": positions['3'], "user": target_user, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.POSITIVE)
                .add(Callback("4️⃣ Куратор модерации", {"command": "set_position", "position": positions['4'], "user": target_user, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.POSITIVE)
                .row()
                .add(Callback("5️⃣ Заместитель главного", {"command": "set_position", "position": positions['5'], "user": target_user, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("6️⃣ Главный модератор", {"command": "set_position", "position": positions['6'], "user": target_user, "chatId": chat_id, "initiator": user_id}), color=KeyboardButtonColor.NEGATIVE)
                .row()
                .add(Callback("❌ Отмена", {"command": "cancel"}), color=KeyboardButtonColor.SECONDARY)
            )
            
            target_name = await get_user_name(target_user, chat_id)
            await message.reply(f"Выберите должность для {target_name}:", keyboard=keyboard)

        if command in ['удалитьдолжность', 'removeposition', 'clearposition']:
            if not await check_perm(user_id, chat_id, command, 6):
                await message.reply("❌ Только разработчик может удалять должности!", disable_mentions=1)
                return True
            
            user = 0
            if message.reply_message:
                user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.reply("📝 Использование: /удалитьдолжность [пользователь]", disable_mentions=1)
                return True
            
            await update_user_data(user, 'position', 'Не указана')
            
            target_name = await get_user_name(user, chat_id)
            
            await message.reply(
                f"✅ Должность удалена у {target_name}!",
                disable_mentions=1
            )
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Удалил должность у пользователя {u_name}.")

        if command in ['snick', 'setnick', 'nick', 'addnick', 'ник', 'сетник', 'аддник']:
            if not await check_perm(user_id, chat_id, command, 1):
                await message.reply("Недостаточно прав!")
                return True

            user = 0
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) == 0:
                await message.reply("Вы не можете установить ник данному пользователю!", disable_mentions=1)
                return True

            new_nick = await get_string(arguments, arg)
            if not new_nick:
                await message.reply("Укажите ник пользователя!", disable_mentions=1)
                return True
            else: await setnick(user, chat_id, new_nick)

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) установил новое имя @id{user} (пользователю)!\nНовый ник: {new_nick}", disable_mentions=1)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Установил ник пользователю {u_name}.\nНовый ник: {new_nick}")

        if command in ['rnick', 'removenick', 'clearnick', 'cnick', 'рник', 'удалитьник', 'снятьник']:
            if not await check_perm(user_id, chat_id, command, 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) == 0:
                await message.reply("Вы не можете удалить ник данному пользователю!", disable_mentions=1)
                return True

            await rnick(user, chat_id)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) убрал(-а) ник у @id{user} (пользователя)!", disable_mentions=1)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Удалил ник у пользователя {u_name}.")

        if command in ['getacc', 'acc', 'гетакк', 'аккаунт', 'account']:
            if not await check_perm(user_id, chat_id, command, 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            nick = await get_string(arguments, 1)
            if not nick:
                await message.reply("Укажите ник!", disable_mentions=1)
                return True

            nick_result = await get_acc(chat_id, nick)

            if not nick_result:
                await message.reply(f"Ник «{nick}» никому не принадлежит!", disable_mentions=1)
            else:
                info = await bot.api.users.get(user_ids=nick_result)
                if info:
                    await message.reply(f"Ник «{nick}» принадлежит @id{nick_result} ({info[0].first_name} {info[0].last_name})", disable_mentions=1)
                else:
                    await message.reply(f"Ник «{nick}» принадлежит @id{nick_result}, но не удалось получить информацию о пользователе (возможно, страница удалена).", disable_mentions=1)

        if command in ['getnick', 'gnick', 'гник', 'гетник']:
            if not await check_perm(user_id, chat_id, command, 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            nick = await get_nick(user, chat_id)
            if not nick: await message.reply(f"У данного @id{user} (пользователя) нет ника!", disable_mentions=1)
            else: await message.reply(f"Ник данного @id{user} (пользователя): {nick}", disable_mentions=1)

        if command in ['никлист', 'ники', 'всеники', 'nlist', 'nickslist', 'nicklist', 'nicks']:
            if not await check_perm(user_id, chat_id, command, 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            nicks = await nlist(chat_id, 1)
            nick_list = '\n'.join(nicks)
            if nick_list == "": nick_list = "Ники отсутствуют!"

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("⏪", {"command": "nicksMinus", "page": 1, "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("Без ников", {"command": "nonicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("⏩", {"command": "nicksPlus", "page": 1, "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
            )

            await message.reply(f"Пользователи с ником [1 страница]:\n{nick_list}\n\nПользователи без ников: «/nonick»", disable_mentions=1, keyboard=keyboard)

        if command in ['nonick', 'nonicks', 'nonicklist', 'nolist', 'nnlist', 'безников', 'ноникс']:
            if not await check_perm(user_id, chat_id, command, 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            nonicks = await nonick(chat_id, 1)
            nonick_list = '\n'.join(nonicks)
            if nonick_list == "": nonick_list = "Пользователи без ников отсутствуют!"

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("⏪", {"command": "nonickMinus", "page": 1, "chatId": chat_id}), color=KeyboardButtonColor.NEGATIVE)
                .add(Callback("С никами", {"command": "nicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("⏩", {"command": "nonickPlus", "page": 1, "chatId": chat_id}),
                     color=KeyboardButtonColor.POSITIVE)
            )

            await message.reply(f"Пользователи без ников [1]:\n{nonick_list}\n\nПользователи с никами: «/nlist»", disable_mentions=1, keyboard=keyboard)

        if command in ['kick', 'кик', 'исключить']:
            if not await check_perm(user_id, chat_id, 'kick', 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете исключить данного пользователя!", disable_mentions=1)
                return True

            reason = await get_string(arguments, arg)

            try: await bot.api.messages.remove_chat_user(chat_id, user)
            except:
                await message.reply(f"Не удается исключить данного @id{user} (пользователя)! Необходимо забрать у него звезду.", disable_mentions=1)
                return True

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Очистить", {"command": "clear", "chatId": chat_id, "user": user}), color=KeyboardButtonColor.NEGATIVE)
            )

            if not reason: await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) кикнул(-а) @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1, keyboard=keyboard)
            else: await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) кикнул(-а) @id{user} ({await get_user_name(user, chat_id)})\nПричина: {reason}", disable_mentions=1, keyboard=keyboard)

            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and await get_role(user_id, chat_id) < 5:
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['warn', 'варн']:
            if not await check_perm(user_id, chat_id, command, 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете выдать пред данному пользователю!", disable_mentions=1)
                return True

            reason = await get_string(arguments, arg)
            if not reason:
                await message.reply("Укажите причину предупреждения!")
                return True

            warns = await warn(chat_id, user, user_id, reason)
            if warns < 3:
                keyboard = (
                    Keyboard(inline=True)
                    .add(Callback("Снять варн", {"command": "unwarn", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
                    .add(Callback("Очистить", {"command": "clear", "chatId": chat_id, "user": user}), color=KeyboardButtonColor.NEGATIVE)
                )
                await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) предупреждение @id{user} ({await get_user_name(user, chat_id)})\nПричина: {reason}\nКоличество предупреждений: {warns}", disable_mentions=1, keyboard=keyboard)
            else:
                keyboard = (
                    Keyboard(inline=True)
                    .add(Callback("Очистить", {"command": "clear", "chatId": chat_id, "user": user}),color=KeyboardButtonColor.NEGATIVE)
                )
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) последнее предупреждение @id{user} ({await get_user_name(user, chat_id)}) (3/3)\nПричина: {reason}\n@id{user} (Пользователь) был исключен за большое количество предупреждений!",disable_mentions=1, keyboard=keyboard)
                try: await bot.api.messages.remove_chat_user(chat_id, user)
                except: pass
                await clear_warns(chat_id, user)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Выдал предупреждение пользователю {u_name}.\nПричина: {reason}\nВсего: {warns}/3")

            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and await get_role(user_id, chat_id) < 6:
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['unwarn', 'унварн', 'анварн', 'снятьвыговор', 'unvyg', 'анвыг', 'unvig']:
            if not await check_perm(user_id, chat_id, 'unwarn', 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете снять пред данному пользователю!", disable_mentions=1)
                return True

            if await get_warns(user, chat_id) < 1:
                await message.reply("У пользователя нет предупреждений!")
                return True

            warns = await unwarn(chat_id, user)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) снял(-а) выговор @id{user} ({await get_user_name(user, chat_id)})\nКоличество выговоров: {warns}", disable_mentions=1)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Снял предупреждение с пользователя {u_name}.\nОсталось: {warns}/3")

        if command in ['getwarn', 'gwarn', 'getwarns', 'гетварн', 'гварн']:
            if not await check_perm(user_id, chat_id, command, 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Вы не указали @пользователя!", disable_mentions=1)
                return True

            warns = await gwarn(user, chat_id)
            string_info = str
            if not warns: string_info = "Активных предупреждений нет!"
            else: string_info = f"@id{warns['moder']} (Модератор) | {warns['reason']} | {warns['count']}/3 | {warns['time']}"

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("История предупреждений", {"command": "warnhistory", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            )

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}), информация о активных предупреждениях @id{user} (пользователя):\n{string_info}", disable_mentions=1, keyboard=keyboard)

        if command in ['warnhistory', 'historywarns', 'whistory', 'историяварнов', 'историяпредов']:
            if not await check_perm(user_id, chat_id, command, 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            warnhistory_mass = await warnhistory(user, chat_id)
            if not warnhistory_mass: wh_string = "Предупреждений не было!"
            else: wh_string = '\n'.join(warnhistory_mass)

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Активные предупреждения", {"command": "activeWarns", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                .add(Callback("Вся информация", {"command": "stats", "user": user, "chatId": chat_id}),color=KeyboardButtonColor.PRIMARY)
            )

            await message.reply(f"Информация о всех предупреждениях @id{user} ({await get_user_name(user, chat_id)})\nКоличество предупреждений пользователя: {await get_warns(user, chat_id)}\n\nИнформация о последних 10 предупреждений пользователя:\n{wh_string}", disable_mentions=1, keyboard=keyboard)

        if command in ['warnlist', 'warns', 'wlist', 'варны', 'варнлист']:
            if not await check_perm(user_id, chat_id, command, 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            warns = await warnlist(chat_id)
            if warns == False: warns_string = "Пользователей с предупреждениями нет!"
            else: warns_string = '\n'.join(warns)

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}), список пользователей с варнами:\n{warns_string}", disable_mentions=1)

        if command in ['staff', 'стафф']:
            if not await check_perm(user_id, chat_id, command, 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            roles_data = await staff(chat_id)

            devs_text = ""
            if "Разработчик бота" in roles_data:
                devs_list = roles_data.pop("Разработчик бота")
                devs_text = f"Разработчик бота:\n" + "\n".join(devs_list) + "\n\n"

            sql.execute("SELECT owner_id FROM chats WHERE chat_id = ?", (chat_id,))
            owner_id = sql.fetchone()[0]

            if owner_id < 1: owner = f"— [club{abs(owner_id)}|Сообщество]"
            else: owner = f"— [id{owner_id}|{await get_user_name(owner_id, chat_id)}]"

            res_msg = f"{devs_text}Владелец беседы:\n{owner}\n\n"

            # Вывод ролей строго по иерархии (для второй половины файла)
            staff_order = ["Старшие администраторы", "Администраторы", "Старшие модераторы", "Модераторы"]
            for role_name in staff_order:
                if role_name in roles_data:
                    users = roles_data.pop(role_name)
                    res_msg += f"{role_name}:\n" + "\n".join(users) + "\n\n"
            
            # Вывод оставшихся кастомных ролей
            for role_name, users in roles_data.items():
                res_msg += f"{role_name}:\n" + "\n".join(users) + "\n\n"
            
            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Никнеймы", {"command": "nicks", "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            )
            
            await message.reply(res_msg.strip(), disable_mentions=1, keyboard=keyboard)

        if command in ['reg', 'registration', 'regdate', 'рег', 'регистрация', 'датарегистрации']:
            if not await check_perm(user_id, chat_id, command, 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            if message.reply_message:user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):user = await getID(arguments[1])
            else: user = user_id

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Вся информация", {"command": "stats", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
            )
            
            try:
                info = await bot.api.users.get(user_ids=user)
                user_name = f"{info[0].first_name} {info[0].last_name}"
            except:
                user_name = "пользователя"

            reg_info = await get_registration_date(user)
            if not reg_info: reg_info = "Не удалось определить"
            
            await message.reply(f"Дата регистрации @id{user} ({user_name}): {reg_info}", disable_mentions=1, keyboard=keyboard)

        if command in ['mute', 'мут', 'мьют', 'муте', 'addmute']:
            if not await check_perm(user_id, chat_id, command, 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 2
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 2
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 3
            else:
                await message.reply("Укажите пользователя!")
                return True

            if len(arguments) < 4 and arg == 3:
                await message.reply("Укажите аргументы команды!")
                return True

            if len(arguments) < 3 and arg == 2:
                await message.reply("Укажите аргументы команды!")
                return True

            await checkMute(chat_id, user)

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете выдать мут данному пользователю!", disable_mentions=1)
                return True

            if await get_mute(user, chat_id):
                await message.reply("Пользователь уже замьючен!")
                return True

            reason = await get_string(arguments, arg)
            if not reason:
                await message.reply("Укажите причину предупреждения!")
                return True

            if arg == 3: mute_time = arguments[2]
            else: mute_time = arguments[1]
            try: mute_time = int(mute_time)
            except:
                await message.reply("Укажите время в минутах!")
                return True


            if mute_time < 1 or mute_time > 1000:
                await message.reply("Время не должно превышать 1000, и быть не менее 0!")
                return True

            await mute(user, chat_id, user_id, reason, mute_time)

            do_time = datetime.now() + timedelta(minutes=mute_time)
            mute_time = str(do_time).split('.')[0]


            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Снять мут", {"command": "unmute", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
                .add(Callback("Очистить", {"command": "clear", "chatId": chat_id, "user": user}), color=KeyboardButtonColor.NEGATIVE)
            )

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) замутил(-а) @id{user} ({await get_user_name(user, chat_id)})\nПричина: {reason}\nМут выдан до: {mute_time}", disable_mentions=1, keyboard=keyboard)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Заглушил пользователя {u_name}.\nВремя: {arguments[2] if arg==3 else arguments[1]} мин\nПричина: {reason}")
            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and await get_role(user_id, chat_id) < 5:
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['unmute', 'снятьмут', 'анмут', 'анмьют', 'унмут']:
            if not await check_perm(user_id, chat_id, command, 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            if message.reply_message:user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            await checkMute(chat_id, user)

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете снять мут данному пользователю!", disable_mentions=1)
                return True

            if not await get_mute(user, chat_id):
                await message.reply(f"У @id{user} (пользователя) нет мута!", disable_mentions=1)
                return True

            await unmute(user, chat_id)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) размутил(-а) @id{user} ({await get_user_name(user, chat_id)})")
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Снял мут с пользователя {u_name}.")

        if command in ['getmute', 'gmute', 'гмут', 'гетмут', 'чекмут']:
            if not await check_perm(user_id, chat_id, command, 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            if message.reply_message:user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            await checkMute(chat_id, user)

            mute_string = str
            gmute = await get_mute(user, chat_id)
            if not gmute: mute_string = "У пользователя нет мута!"
            else:
                do_time = datetime.fromisoformat(gmute['date']) + timedelta(minutes=gmute['time'])
                mute_time = str(do_time).split('.')[0]

                try:
                    int(gmute['moder'])
                    mute_string = f"@id{gmute['moder']} (Модератор) | {gmute['reason']} | {gmute['date']} | До: {mute_time}"
                except: mute_string = f"Бот | {gmute['reason']} | {gmute['date']} | До: {mute_time}"

            await message.reply(f"Информация о муте @id{user} ({await get_user_name(user, chat_id)}):\n\n{mute_string}", disable_mentions=1)

        if command in ['mutelist', 'mutes', 'муты', 'мутлист']:
            if not await check_perm(user_id, chat_id, command, 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            mutes = await mutelist(chat_id)
            if not mutes: mutes_str = ""
            else:
                mutes_str = '\n'.join(mutes)

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}), список пользователей с мутами:\n{mutes_str}", disable_mentions=1)

        if command in ['clear', 'delete', 'чистка', 'очистить', 'удалить']:
            if not await check_perm(user_id, chat_id, command, 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            cmids_to_delete = []
            targets_uids = []

            # 1. Обработка ответа на сообщение (Reply)
            if message.reply_message:
                t_uid = message.reply_message.from_id
                if t_uid == -message.group_id or await equals_roles(user_id, t_uid, chat_id) >= 2:
                    cmids_to_delete.append(message.reply_message.conversation_message_id)
                    targets_uids.append(t_uid)
                else:
                    return await message.reply("❌ Недостаточно прав для удаления сообщения этого пользователя!", disable_mentions=1)

            # 2. Обработка пересланных сообщений (Forwarded)
            if message.fwd_messages:
                for fwd in message.fwd_messages:
                    f_uid = fwd.from_id
                    f_cmid = fwd.conversation_message_id
                    # conversation_message_id доступен если сообщения из этой же беседы
                    if f_cmid and (f_uid == -message.group_id or await equals_roles(user_id, f_uid, chat_id) >= 2):
                        if f_cmid not in cmids_to_delete:
                            cmids_to_delete.append(f_cmid)
                            if f_uid not in targets_uids: targets_uids.append(f_uid)

            if cmids_to_delete:
                # Добавляем само сообщение с командой в список на удаление
                cmids_to_delete.append(message.conversation_message_id)
                try:
                    await bot.api.messages.delete(group_id=message.group_id, peer_id=peer_id, delete_for_all=True, cmids=cmids_to_delete)
                    # Логирование
                    u_logs = []
                    for uid in targets_uids:
                        if uid == -message.group_id: u_logs.append("Бот")
                        else: u_logs.append(f"ID{uid}")
                    await log_action(user_id, chat_id, f"Удалил {len(cmids_to_delete)-1} сообщений через {command} (от: {', '.join(u_logs)})")
                except Exception: pass
                return True

            # 3. Массовая очистка по упоминанию/ID (старое поведение /clear)
            user = 0
            if len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                return await message.reply("📝 Чтобы удалить сообщения, ответьте на них, перешлите их или укажите @упоминание пользователя для массовой чистки.", disable_mentions=1)

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете очистить сообщения данного пользователя!", disable_mentions=1)
                return True

            await clear(user, chat_id, message.group_id, message.peer_id)
            user_link = await get_user_link(user)
            await message.reply(f"✅ Сообщения пользователя {user_link} были очищены.", disable_mentions=1)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Очистил сообщения пользователя {u_name}.")

        if command in ['alt', 'альт', 'альтернативные']:
            if not await check_perm(user_id, chat_id, command, 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            commands_levels = {
                1: [
                    '\nКоманды модераторов:',
                    '/setnick — snick, nick, addnick, ник, сетник, аддник',
                    '/removenick —  removenick, clearnick, cnick, рник, удалитьник, снятьник',
                    '/getnick — gnick, гник, гетник',
                    '/getacc — acc, гетакк, аккаунт, account',
                    '/nlist — ники, всеники, nlist, nickslist, nicklist, nicks',
                    '/nonick — nonicks, nonicklist, nolist, nnlist, безников, ноникс',
                    '/kick — кик, исключить',
                    '/warn — пред, варн, pred, предупреждение',
                    '/unwarn — унварн, анварн, снятьпред, минуспред',
                    '/getwarn — gwarn, getwarns, гетварн, гварн',
                    '/warnhistory — historywarns, whistory, историяварнов, историяпредов',
                    '/warnlist — warns, wlist, варны, варнлист',
                    '/staff — стафф',
                    '/reg — registration, regdate, рег, регистрация, датарегистрации',
                    '/mute — мут, мьют, муте, addmute',
                    '/unmute — снятьмут, анмут, унмут, снятьмут',
                    '/alt — альт, альтернативные',
                    '/getmute -- gmute, гмут, гетмут, чекмут',
                    '/mutelist -- mutes, муты, мутлист',
                    '/clear -- чистка, очистить, очистка',
                    '/getban -- чекбан, гетбан, checkban',
                    '/delete -- удалить',
                    '/aban — абан, заморозить',
                    '/unaban — упабан, разморозить',
                    '/tstats — тестстат',
                    '/modstats — мстатс'
                ],
                2: [
                    '\nКоманды старших модераторов:',
                    '/ban — бан, блокировка',
                    '/unban -- унбан, снятьбан',
                    '/addmoder -- moder',
                    '/removerole -- rrole, снятьроль',
                    '/zov - зов, вызов',
                    '/online - ozov, озов',
                    '/onlinelist - olist, олист',
                    '/banlist - bans, банлист, баны',
                    '/inactive - ilist, inactive',
                    '/masskick - mkick'
                ],
                3: [
                    '\nКоманды администраторов:',
                    '/quiet -- silence, тишина',
                    '/skick -- скик, снят',
                    '/sban -- сбан',
                    '/sunban — сунбан, санбан',
                    '/addsenmoder — senmoder',
                    '/rnickall -- allrnick, arnick, mrnick',
                    '/sremovenick -- srnick',
                    '/szov -- serverzov, сзов',
                '/srole -- prole, pullrole',
                '/editstats -- редстатс'
                ],
                4: [
                    '\nКоманды старших администраторов:',
                    '/addadmin -- admin',
                    '/serverinfo -- sinfo',
                    '/filter -- none',
                '/sremoverole -- srrole',
                '/infochat -- инфочат',
                '/infoid -- groups'
                ],
                5: [
                    '\nСписок команд владельца беседы',
                    '/antiflood -- af',
                    '/welcometext -- welcome, wtext',
                    '/invite -- none',
                    '/leave -- none',
                '/server -- setserver',
                    '/editowner -- owner, setowner',
                    '/setleader -- сетлидер',
                    '/removeleader -- снятьлидера'
                ],
                6: [
                    '/addsenadmin -- senadm, addsenadm, senadmin',
                    '\nКоманды разработчика:',
                    '/sync -- sync',
                    '/выдатьмонеты -- givemoney',
                    '/выдатьвип -- givevip',
                    '/givecmd -- gcmd',
                    '/uncmd -- ucmd',
                    '/раздача -- giveall',
                    '/type -- type',
                    '/сетправила -- setrules',
                    '/сетправилабота -- setbotrules',
                    '/сетинфо -- setinfo',
                    '/games -- games',
                    '/выдатьмонеты -- givemoney, givecash',
                    '/создатьпромо -- newpromo',
                    '/удалитьпромо -- delpromo',
                    '/editowner -- setowner',
                    '/forceowner -- fowner',
                    '/masskick all -- mkickall',
                    '/say -- say',
                    '/banwords -- bws',
                    '/givemats -- gmats',
                    '/giveexp -- gexp',
                    '/setdev -- setdev',
                    '/news -- news',
                    '/выдатьдолжность -- setpos',
                    '/удалитьдолжность -- delpos',
                    '/cancelwar -- cwar',
                    '/activewars -- awars',
                    '/gban -- gban',
                    '/gbanpl -- gbanpl',
                    '/ungban -- ungban',
                    '/gzov -- gzov',
                    '/setleader -- setleader',
                    '/removeleader -- rmleader',
                    '/grole -- grole',
                    '/grrole -- grrole',
                    '/giveupgrade -- gup',
                    '/resetmoney -- rmoney',
                    '/setbalance -- sbal',
                    '/newrole -- nrole',
                    '/delrole -- drole',
                    '/role -- role',
                    '/editcmd -- ecmd',
                    '/stats_eco -- se',
                    '/delclan -- dclan',
                    '/gzov -- гзов',
                    '/addtester -- settester',
                    '/removetester -- снятьтестера',
                    '/debuglog -- дебаглог',
                    '/resetwork -- сбросработы',
                    '/resetwarcd -- сбросвойн',
                    '/clearchat -- очисткачата'
                ]
            }

            user_role = await get_role(user_id, chat_id)

            commands = []
            for i in commands_levels.keys():
                if i <= user_role:
                    for b in commands_levels[i]:
                        commands.append(b)

            level_commands = '\n'.join(commands)

            await message.reply(f"Альтернативные команды\n\n{level_commands}", disable_mentions=1)

        if command in ['getban', 'чекбан', 'гетбан', 'checkban']:
            if not await check_perm(user_id, chat_id, command, 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            # Get linkable name for header
            name = await get_user_link(user)
            # Global bans
            gb_all = "Отсутствует"
            gb_pl = "Отсутствует"
            
            sql.execute("SELECT ban_type, moder, reason, date FROM global_bans WHERE user_id = ?", (user,))
            gb = sql.fetchone()
            
            if gb:
                b_type, b_moder, b_reason, b_date = gb
                moder_link = f"[id{b_moder}|Модератор]"
                gb_info = f"{moder_link} | {b_reason} | {b_date} МСК (UTC+3)"
                if b_type == 'all':
                    gb_all = gb_info # Отображаем в общем разделе
                    gb_pl = gb_info # Глобальный бан распространяется и на PL-чаты
                elif b_type == 'pl':
                    gb_pl = gb_info

            # Network bans (Pull)
            pull_ban_str = "Беседа не в сетке"
            current_pull = await get_server_chats(chat_id)
            if current_pull:
                pb_count = 0
                ban_info = None
                for pid in current_pull:
                    try:
                        sql.execute(f"SELECT moder, reason, date_string FROM bans_{pid} WHERE user_id = ?", (user,))
                        res = sql.fetchone()
                        if res: 
                            pb_count += 1
                            if ban_info is None: ban_info = res
                    except: pass
                
                if pb_count > 0 and ban_info: pull_ban_str = f"[id{ban_info[0]}|Модератор] | {ban_info[1]} | {ban_info[2]} МСК (UTC+3)"
                elif pb_count > 0: pull_ban_str = f"Есть (в {pb_count} беседах)"
                else: pull_ban_str = "Отсутствует"

            # Local bans
            sql.execute("SELECT chat_id, chat_type FROM chats")
            all_chats = sql.fetchall()
            
            found_bans = []
            count = 0
            
            ctype_map = {
                'def': 'Общая беседа', 'pl': 'Беседа игроков', 'ext': 'Расширенная беседа',
                'hel': 'Беседа хеллперов', 'ld': 'Беседа лидеров', 'adm': 'Беседа администраторов',
                'mod': 'Беседа модераторов', 'tex': 'Беседа техов', 'test': 'Беседа тестеров',
                'med': 'Беседа медиа', 'ruk': 'Беседа руководства', 'users': 'Беседа пользователей'
            }

            for c_id, c_type in all_chats:
                try:
                    sql.execute(f"SELECT moder, reason, date_string FROM bans_{c_id} WHERE user_id = ?", (user,))
                    res = sql.fetchone()
                    if res:
                        count += 1
                        try:
                            conv = await bot.api.messages.get_conversations_by_id(peer_ids=2000000000+c_id)
                            title = conv.items[0].chat_settings.title
                        except: title = f"Chat #{c_id}"

                        local_moder_link = f"[id{res[0]}|Модератор]"
                        line = f"{count}) {title} | {local_moder_link} | {res[1]} | {res[2]} МСК (UTC+3)"
                        found_bans.append(line)
                except: pass
            
            bans_text = "\n".join(found_bans) if found_bans else "Отсутствуют"
            
            msg = (
                f"Информация о блокировках: {name}\n\n"
                f"Информация об общей блокировке в беседах:\n{gb_all}\n"
                f"Информация о блокировке в беседах игроков:\n{gb_pl}\n\n"
                f"Информация о банах пользователя:\n{bans_text}"
            )
            
            await message.reply(msg, disable_mentions=1)

        if command in ['ban', 'бан', 'блокировка']:
            if not await check_perm(user_id, chat_id, 'ban', 2):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете выдать бан данному пользователю!", disable_mentions=1)
                return True

            reason = await get_string(arguments, arg)
            if not reason:
                await message.reply("Укажите причину бана!")
                return True

            if await checkban(user, chat_id):
                await message.reply("Пользователь уже заблокирован в этой беседе!")
                return True

            await ban(user, user_id, chat_id, reason)

            try: await bot.api.messages.remove_chat_user(chat_id, user)
            except: pass

            keyboard = (
                Keyboard(inline=True)
                .add(Callback("Снять бан", {"command": "unban", "user": user, "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
            )

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) заблокировал(-а) @id{user} ({await get_user_name(user, chat_id)})\nПричина: {reason}", disable_mentions=1, keyboard=keyboard)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Заблокировал пользователя {u_name}.\nПричина: {reason}")
            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and await get_role(user_id, chat_id) < 5:
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['unban', 'унбан', 'снятьбан']:
            if not await check_perm(user_id, chat_id, command, 2):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            getban = await checkban(user, chat_id)
            if not getban:
                await message.reply("Пользователь не заблокирован в этой беседе")
                return True

            if await equals_roles(user_id, getban['moder'], chat_id) < 1:
                await message.reply("Вы не можете снять бан данному пользователю, т.к. его заблокировал человек с уровнем прав выше!", disable_mentions=1)
                return True

            await unban(user, chat_id)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) разблокировал(-а) @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Разблокировал пользователя {u_name}.")

        if command in ['addmoder', 'moder']:
            if not await check_perm(user_id, chat_id, command, 2):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            # NEW CHECK
            sql.execute("SELECT level FROM global_managers WHERE user_id = ?", (user,))
            if sql.fetchone():
                return await message.reply("❌ Нельзя выдать локальную роль пользователю с глобальными правами!")

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете выдать роль данному пользователю!", disable_mentions=1)
                return True

            await roleG(user, chat_id, 1)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) права модератора @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Выдал права модератора пользователю {u_name}.")
            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and await get_role(user_id, chat_id) < 5:
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['removerole', 'rrole', 'снятьроль']:
            if not await check_perm(user_id, chat_id, command, 2):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            # NEW CHECK
            sql.execute("SELECT level FROM global_managers WHERE user_id = ?", (user,))
            if sql.fetchone():
                return await message.reply("❌ Нельзя выдать локальную роль пользователю с глобальными правами!")

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете выдать роль данному пользователю!", disable_mentions=1)
                return True

            await roleG(user, chat_id, 0)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) забрал(-а) роль у @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Снял роль с пользователя {u_name}.")
            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and await get_role(user_id, chat_id) < 5:
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['zov', 'зов', 'вызов']:
            if not await check_perm(user_id, chat_id, command, 2):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            reason = await get_string(arguments, 1)
            if not reason:
                await message.reply("Укажите причину вызова!")
                return True

            users = await bot.api.messages.get_conversation_members(peer_id=message.peer_id, fields=["online_info", "online"])
            users = json.loads(users.json())
            user_f = []
            gi = 0
            for i in users["profiles"]:
                if not i['id'] == user_id:
                    gi = gi + 1
                    if gi <= 100:
                        user_f.append(f"@id{i['id']} (🖤)")
            zov_users = ''.join(user_f)

            await message.answer(f"🔔 Вы были вызваны @id{user_id} (администратором) беседы\n\n{zov_users}\n\n❗ Причина вызова: {reason}")

        if command in ['ozov', 'online', 'озов']:
            if not await check_perm(user_id, chat_id, command, 2):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            reason = await get_string(arguments, 1)
            if not reason:
                await message.reply("Укажите причину вызова!")
                return True

            users = await bot.api.messages.get_conversation_members(peer_id=message.peer_id, fields=["online_info", "online"])
            users = json.loads(users.json())
            online_users = []
            gi = 0
            for i in users["profiles"]:
                if i["online"] == 1:
                    if not i['id'] == user_id:
                        gi = gi + 1
                        if gi <= 100:
                            online_users.append(f"@id{i['id']} (♦️)")

            online_zov = "".join(online_users)
            await message.answer(f"🔔 Вы были вызваны @id{user_id} (администратором) беседы\n\n{online_zov}\n\n❗ Причина вызова: {reason}")

        if command in ['onlinelist', 'olist', 'олист']:
            if not await check_perm(user_id, chat_id, command, 2):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            users = await bot.api.messages.get_conversation_members(peer_id=message.peer_id, fields=["online", "online_info"])
            users = json.loads(users.json())
            online_users = []
            gi = 0
            for i in users["profiles"]:
                if i["online"] == 1:
                    if not i['id'] == user_id:
                        gi = gi + 1
                        if gi <= 80:
                            if i["online_info"]["is_mobile"] == False:
                                online_users.append(f"@id{i['id']} ({await get_user_name(i['id'], chat_id)}) -- 💻")
                            else:
                                online_users.append(f"@id{i['id']} ({await get_user_name(i['id'], chat_id)}) -- 📱")

            olist_users = "\n".join(online_users)
            await message.reply(f"@id{user_id} ({await get_user_name(user_id, chat_id)}), cписок пользователей онлайн\n\n{olist_users}\n\nВсего в онлайн: {gi}", disable_mentions=1)

        if command in ['banlist', 'bans', 'банлист', 'баны']:
            if not await check_perm(user_id, chat_id, command, 2):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            bans = await banlist(chat_id)
            bans_do = []
            gi = 0
            for i in bans:
                gi = gi + 1
                if gi <= 10:
                    bans_do.append(i)
            bans_str = "\n".join(bans_do)

            await message.reply(f"Информация о последних 10 блокировках в беседе:\n\n{bans_str}\n\nВсего блокировок: {gi}", disable_mentions=1)

        if command in ['delete', 'удалить']:
            if not await check_perm(user_id, chat_id, command, 1):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            if not message.reply_message:
                await message.reply("Чтобы удалить сообщение, нужно ответить на него!")
                return True

            cmid = message.reply_message.conversation_message_id
            user = message.reply_message.from_id

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете удалить сообщение данного пользователя!", disable_mentions=1)
                return True

            try: await bot.api.messages.delete(group_id=message.group_id, peer_id=peer_id, delete_for_all=True, cmids=cmid)
            except: pass

            try: await bot.api.messages.delete(group_id=message.group_id, peer_id=peer_id, delete_for_all=True, cmids=message.conversation_message_id)
            except: pass
            
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Удалил сообщение пользователя {u_name}.")

        if command in ['inactivelist', 'inactive', 'ilist']:
            if not await check_perm(user_id, chat_id, command, 2):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            users = await bot.api.messages.get_conversation_members(peer_id=message.peer_id,fields=["online_info", "online", "last_seen"])
            users = json.loads(users.json())
            unactive_users_day = []
            count_uad = 0
            unactive_users_moon = []
            count_uam = 0
            for i in users["profiles"]:
                try:
                    currency_time = time.time()
                    time_seen = i['last_seen']['time']
                    last_seen_device_list = {1: "📱", 2: "📱", 3: "📱", 4: "📱", 5: "📱", 6: "💻", 7: "💻"}
                    last_seen_device = last_seen_device_list.get(i['last_seen']['platform'])
                    if time_seen <= currency_time - 604800:
                        count_uam = count_uam + 1
                        if count_uam <= 30:
                            info = await bot.api.users.get(i['id'])
                            unactive_users_moon.append(
                                f"{count_uam}) @id{i['id']} ({info[0].first_name} {info[0].last_name}) -- {last_seen_device}")
                    elif time_seen <= currency_time - 86400:
                        count_uad = count_uad + 1
                        if count_uad <= 30:
                            info = await bot.api.users.get(i['id'])
                            unactive_users_day.append(
                                f"{count_uad}) @id{i['id']} ({info[0].first_name} {info[0].last_name}) -- {last_seen_device}")
                except:
                    pass
            uad = "\n".join(unactive_users_day)
            uam = "\n".join(unactive_users_moon)
            await message.reply(f"Список неактивных пользователей [Более недели]\n{uam}\n\nБолее дня\b{uad}", disable_mentions=1)

        if command in ['mkick', 'мкик', 'masskick']:
            if not await check_perm(user_id, chat_id, command, 2):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            if len(arguments) <= 1:
                await message.reply("Укажите пользователя(-ей)", disable_mentions=1)
                return True
            if len(arguments) >= 30:
                await message.reply("Не более 30 пользователей!", disable_mentions=1)
                return True

            if arguments[1] in ['all', 'все']:
                if not await check_perm(user_id, chat_id, 'masskick_all', 6):
                    await message.reply("Недостаточно прав!", disable_mentions=1)
                    return True

                users = await bot.api.messages.get_conversation_members(peer_id=message.peer_id,
                                                                        fields=["online_info", "online"])
                users = json.loads(users.json())
                user_f = []
                gi = 0
                for i in users["profiles"]:
                    if not i['id'] == user_id and await get_role(i['id'], chat_id) <= 0:
                        await bot.api.messages.remove_chat_user(chat_id, int(i['id']))

                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) исключил(-а) пользователей без ролей", disable_mentions=1)
                return True


            do_users = []
            for i in range(len(arguments)):
                if i <= 0:
                    pass
                else:
                    do_users.append(arguments[i])
            users = []
            for i in do_users:
                idp = await getID(i)
                if idp:
                    users.append(idp)
            kick_users_list = []
            for i in users:
                if await equals_roles(user_id, i, chat_id) < 2:
                    await message.answer(f"У @id{i} уровень прав выше!", disable_mentions=1)
                else:
                    try:
                        await bot.api.messages.remove_chat_user(chat_id, i)
                        info = await bot.api.users.get(int(i))
                        kick_users_list.append(f"@id{i} ({info[0].first_name})")
                    except:
                        pass
            kick_users = ", ".join(kick_users_list)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) исключил пользователей: {kick_users}", disable_mentions=1)
            await add_punishment(chat_id, user_id)
            await log_action(user_id, chat_id, f"Массово исключил пользователей: {kick_users}")
            if await get_sliv(user_id, chat_id) and await get_role(user_id, chat_id) < 5:
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['quiet', 'silence', 'тишина']:
            if not await check_perm(user_id, chat_id, command, 3):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            silence = await quiet(chat_id)
            if silence: 
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) включил(-а) режим тишины!")
                await log_action(user_id, chat_id, "Включил режим тишины.")
            else: 
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выключил(-а) режим тишины!")
                await log_action(user_id, chat_id, "Выключил режим тишины.")

        if command in ['skick', 'снят', 'скик']:
            if not await check_perm(user_id, chat_id, command, 3):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете исключить данного пользователя!", disable_mentions=1)
                return True

            server_chats = await get_server_chats(chat_id)
            if not server_chats:
                await message.reply("Сначала укажите сервер, используя /server <число>")
                return True

            reason = await get_string(arguments, arg)

            moder_name = await get_user_name(user_id, chat_id)
            target_name = await get_user_name(user, chat_id)
            
            moder_link = f"[id{user_id}|{moder_name}]"
            target_link = f"[id{user}|{target_name}]"
            
            server_id = await get_server_id(chat_id)
            
            base_msg = f"{moder_link} исключил(-а) в беседах сервера «{server_id}» {target_link}"
            msg_to_send = f"{base_msg}\nПричина: {reason}" if reason else base_msg

            for i in server_chats:
                try:
                    await bot.api.messages.remove_chat_user(i, user)
                    await bot.api.messages.send(peer_id=2000000000+i, message=msg_to_send, disable_mentions=1, random_id=0)
                except:
                    if i == chat_id:
                        try:
                            await bot.api.messages.send(peer_id=2000000000 + i, message=msg_to_send, disable_mentions=1, random_id=0)
                        except: pass
            if not chat_id in server_chats: await message.answer(msg_to_send, disable_mentions=1)
            await add_punishment(chat_id, user_id)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Исключил пользователя {u_name} с сервера.\nПричина: {reason if reason else 'Не указана'}")
            if await get_sliv(user_id, chat_id) and await get_role(user_id, chat_id) < 5:
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['sban', 'сбан']:
            if not await check_perm(user_id, chat_id, command, 3):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете заблокировать данного пользователя!", disable_mentions=1)
                return True

            server_chats = await get_server_chats(chat_id)
            if not server_chats:
                await message.reply("Сначала укажите сервер, используя /server <число>")
                return True

            reason = await get_string(arguments, arg)
            if not reason:
                await message.reply("Укажите причину блокировки!")
                return True

            moder_name = await get_user_name(user_id, chat_id)
            target_name = await get_user_name(user, chat_id)
            first_name = await get_first_name_safe(user)

            moder_link = f"[id{user_id}|{moder_name}]"
            target_link = f"[id{user}|{target_name}]"

            server_id = await get_server_id(chat_id)
            await message.reply(f"🚫 {moder_link} заблокировал(-а) {target_link} в беседах сервера «{server_id}»!\nПричина: {reason}", disable_mentions=1)

            date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            kick_msg = (f"{first_name}, находится в блокировке бесед сервера!\n"
                        f"Информация о блокировке:\n"
                        f"@id{user_id} (Модератор) | {reason} | {date_str} МСК (UTC+3)")
            
            failed_chats = []
            for i in server_chats:
                try:
                    await ban(user, user_id, i, reason)
                    await bot.api.messages.remove_chat_user(chat_id=i, user_id=user)
                    await bot.api.messages.send(peer_id=2000000000+i, message=kick_msg, random_id=0, disable_mentions=1)
                except Exception as e:
                    if getattr(e, "code", 0) != 935:
                        failed_chats.append(str(i))

            if failed_chats:
                await message.answer(f"⚠️ Не удалось исключить пользователя из чатов с ID: {', '.join(failed_chats)}. Возможно, у бота нет прав администратора в этих беседах.", disable_mentions=1)
 
            await add_punishment(chat_id, user_id)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Заблокировал пользователя {u_name} на сервере.\nПричина: {reason}")
            if await get_sliv(user_id, chat_id) and await get_role(user_id, chat_id) < 5:
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['sunban', 'санбан', 'сунбан']:
            if not await check_perm(user_id, chat_id, command, 3):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            if message.reply_message:
                user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            server_chats = await get_server_chats(chat_id)
            if not server_chats:
                await message.reply("Сначала укажите сервер, используя /server <число>")
                return True

            count = 0
            for i in server_chats:
                try: 
                    await unban(user, i)
                    count += 1
                except Exception as e: print(f"Failed to unban in {i}: {e}")

            moder_name = await get_user_name(user_id, chat_id)
            target_name = await get_user_name(user, chat_id)
            
            moder_link = f"[id{user_id}|{moder_name}]"
            target_link = f"[id{user}|{target_name}]"
            
            server_id = await get_server_id(chat_id)
            await message.answer(f"{moder_link} разблокировал(-а) {target_link} в беседах сервера «{server_id}» (всего бесед: {count})", disable_mentions=1)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Разблокировал пользователя {u_name} на сервере.")

        if command in ['addsenmoder', 'senmoder']:
            if not await check_perm(user_id, chat_id, command, 3):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            # NEW CHECK
            sql.execute("SELECT level FROM global_managers WHERE user_id = ?", (user,))
            if sql.fetchone():
                return await message.reply("❌ Нельзя выдать локальную роль пользователю с глобальными правами!")

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете выдать роль данному пользователю!", disable_mentions=1)
                return True

            await roleG(user, chat_id, 2)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) права старшего модератора @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Выдал права старшего модератора пользователю {u_name}.")
            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and await get_role(user_id, chat_id) < 5:
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['rnickall', 'allrnick', 'arnick', 'mrnick']:
            if not await check_perm(user_id, chat_id, command, 3):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            await rnickall(chat_id)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) очистил(-а) ники в беседе", disable_mentions=1)
            await log_action(user_id, chat_id, f"Очистил все ники в беседе.")

        if command in ['sremovenick', 'srnick']:
            if not await check_perm(user_id, chat_id, command, 3):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            if message.reply_message:user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            server_chats = await get_server_chats(chat_id)
            if not server_chats:
                await message.reply("Сначала укажите сервер, используя /server <число>")
                return True

            for i in server_chats:
                try: await rnick(user, i)
                except: pass

            moder_name = await get_user_name(user_id, chat_id)
            target_name = await get_user_name(user, chat_id)
            
            moder_link = f"[id{user_id}|{moder_name}]"
            target_link = f"[id{user}|{target_name}]"
            
            server_id = await get_server_id(chat_id)
            await message.answer(f"{moder_link} убрал(-а) ник {target_link} в беседах сервера «{server_id}»", disable_mentions=1)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Удалил ник пользователя {u_name} на сервере.")

        if command in ['addadmin', 'admin']:
            if not await check_perm(user_id, chat_id, command, 4):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            # NEW CHECK
            sql.execute("SELECT level FROM global_managers WHERE user_id = ?", (user,))
            if sql.fetchone():
                return await message.reply("❌ Нельзя выдать локальную роль пользователю с глобальными правами!")

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете выдать роль данному пользователю!", disable_mentions=1)
                return True

            await roleG(user, chat_id, 3)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) права администратора @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Выдал права администратора пользователю {u_name}.")
            await add_punishment(chat_id, user_id)
            if await get_sliv(user_id, chat_id) and await get_role(user_id, chat_id) < 5:
                await roleG(user_id, chat_id, 0)
                await message.reply(
                    f"❗️ Уровень прав @id{user_id} (пользователя) был снят из-за подозрений в сливе беседы\n\n{await staff_zov(chat_id)}")

        if command in ['serverinfo', 'sinfo']:
            if not await check_perm(user_id, chat_id, command, 4):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            server_chats = await get_server_chats(chat_id)
            if not server_chats: server_str = "Беседа не привязана к серверу!"
            else: server_str = f"ID сервера: {await get_server_id(chat_id)} | Всего бесед на сервере: {len(server_chats)}"

            await message.reply(f"Информация о сервере\n{server_str}")

        if command in ['demote']:
            if not await check_perm(user_id, chat_id, command, 4):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            users = await bot.api.messages.get_conversation_members(peer_id=message.peer_id, fields=["online_info", "online"])
            users = json.loads(users.json())
            for i in users["profiles"]:
                if not i['id'] == user_id and await get_role(i['id'], chat_id) < 1:
                    try: await bot.api.messages.remove_chat_user(chat_id, i['id'])
                    except: pass

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) исключил(-а) всех участников без ролей!", disable_mentions=1)
            await log_action(user_id, chat_id, f"Исключил всех участников без ролей.")

        if command in ['banwords', 'bws', 'банворды']:
            # Теперь это глобальная команда, доступна только разработчику
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            if len(arguments) < 2:
                bwss = await get_banwords()
                bwss_str = ', '.join(bwss)
                if not bwss_str: bwss_str = "Список пуст"
                await message.reply(f"🚫 Глобальный список запрещенных слов:\n{bwss_str}\n\n"
                                    f"Добавить: /{command} add <слова через запятую>\n"
                                    f"Удалить: /{command} delete <слова через запятую>")
            else:
                action = arguments_lower[1]
                words_str = await get_string(arguments, 2)
                
                if not words_str:
                    await message.reply("Укажите слова!")
                    return True

                words_list = [w.strip() for w in words_str.split(',') if w.strip()]

                if action in ['удалить', 'clear', 'delete', 'remove']:
                    for w in words_list:
                        await banwords(w, True)
                    await message.answer(f"✅ Удалено слов: {len(words_list)}")
                    await log_action(user_id, chat_id, f"Удалил из запрещенных слов: {', '.join(words_list)}")
                elif action in ['add', 'добавить']:
                    for w in words_list:
                        await banwords(w, False)
                    await message.answer(f"✅ Добавлено слов: {len(words_list)}")
                    await log_action(user_id, chat_id, f"Добавил в запрещенные слова: {', '.join(words_list)}")
                else:
                    await message.reply("Неизвестное действие! Используйте add или delete.")

        if command in ['filter']:
            if await get_role(user_id, chat_id) < 4:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            if await get_filter(chat_id):
                await set_filter(chat_id, 0)
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выключил(-а) фильтр запрещенных слов", disable_mentions=1)
                await log_action(user_id, chat_id, "Выключил фильтр запрещенных слов.")
            else:
                await set_filter(chat_id, 1)
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) включил(-а) фильтр запрещенных слов", disable_mentions=1)
                await log_action(user_id, chat_id, "Включил фильтр запрещенных слов.")

        if command in ['фильтр', 'filterword']:
            if await get_role(user_id, chat_id) < 4:
                await message.reply("❌ Недостаточно прав!")
                return True
            
            if len(arguments) < 2:
                lbw = await get_local_banwords(chat_id)
                if not lbw: return await message.reply("📝 Список запрещенных слов этого чата пуст.")
                msg = "🚫 Запрещенные слова и время мута:\n\n"
                for word, dur in lbw:
                    msg += f"• {word} — {dur} мин.\n"
                msg += "\n➕ Добавить: /фильтр + [слово] [минуты]\n➖ Удалить: /фильтр - [слово]"
                return await message.reply(msg)
            
            action = arguments[1].lower()
            if action in ['+', 'add', 'добавить']:
                if len(arguments) < 3: return await message.reply("📝 Укажите слово!")
                word = arguments[2].lower()
                duration = 30
                if len(arguments) > 3 and arguments[3].isdigit():
                    duration = int(arguments[3])
                
                try:
                    sql.execute(f"INSERT OR REPLACE INTO banwords_{chat_id} (banword, duration) VALUES (?, ?)", (word, duration))
                    database.commit()
                    await message.reply(f"✅ Слово «{word}» добавлено в фильтр. Мут при обнаружении: {duration} мин.")
                except Exception as e:
                    await message.reply(f"❌ Ошибка БД: {e}")
            
            elif action in ['-', 'del', 'удалить']:
                if len(arguments) < 3: return await message.reply("📝 Укажите слово!")
                word = arguments[2].lower()
                sql.execute(f"DELETE FROM banwords_{chat_id} WHERE banword = ?", (word,))
                database.commit()
                await message.reply(f"✅ Слово «{word}» удалено из фильтра чата.")
            return True

        if command in ['sremoverole', 'srrole']:
            if await get_role(user_id, chat_id) < 4:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = int
            if message.reply_message:user = message.reply_message.from_id
            elif len(arguments) >= 2 and await getID(arguments[1]):user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете снять роль данному пользователю!", disable_mentions=1)
                return True

            server_chats = await get_server_chats(chat_id)
            if not server_chats:
                await message.reply("Сначала укажите сервер, используя /server <число>")
                return True

            for i in server_chats:
                try: await roleG(user, i, 0)
                except: pass

            moder_name = await get_user_name(user_id, chat_id)
            target_name = await get_user_name(user, chat_id)
            
            moder_link = f"[id{user_id}|{moder_name}]"
            target_link = f"[id{user}|{target_name}]"
            
            server_id = await get_server_id(chat_id)
            await message.answer(f"{moder_link} забрал(-а) роль у {target_link} в беседах сервера «{server_id}»", disable_mentions=1)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Забрал роль у пользователя {u_name} на сервере.")

        if command in ['antiflood', 'af']:
            if not await check_perm(user_id, chat_id, command, 5):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            if await get_antiflood(chat_id):
                await set_antiflood(chat_id, 0)
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выключил(-а) режим антифлуда", disable_mentions=1)
                await log_action(user_id, chat_id, "Выключил режим антифлуда.")
            else:
                await set_antiflood(chat_id, 1)
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) включил(-а) режим антифлуда", disable_mentions=1)
                await log_action(user_id, chat_id, "Включил режим антифлуда.")

        if command in ['games', 'игры', 'игровые', 'setgame', 'сетгейм']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            if await get_games(chat_id):
                await set_games(chat_id, 0)
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выключил(-а) игровые команды", disable_mentions=1)
                await log_action(user_id, chat_id, "Выключил игровые команды.")
            else:
                await set_games(chat_id, 1)
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) включил(-а) игровые команды", disable_mentions=1)
                await log_action(user_id, chat_id, "Включил игровые команды.")

        if command in ['welcome', 'welcometext', 'wtext']:
            if not await check_perm(user_id, chat_id, command, 5):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            if len(arguments) < 2:
                await message.reply(f"Текущее приветствие:\n{await get_welcome(chat_id)}\n\n"
                                   f"Изменить: /welcome [текст]\n"
                                   f"Выключить: /welcome off\n\n"
                                   f"Доступные переменные:\n"
                                   f"%u — @id пользователя\n"
                                   f"%n — тег с именем пользователя\n"
                                   f"%i — @id пригласившего\n"
                                   f"%p — тег пригласившего", disable_mentions=1)
                return True

            text = await get_string(arguments, 1)
            await set_welcome(chat_id, text)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) изменил(-а) приветствие в беседе")
            await log_action(user_id, chat_id, f"Изменил приветствие: {text}")

        if command in ['addbiz', 'добавитьбиз']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!")
                return True
            
            if len(arguments) < 4:
                return await message.reply("📝 Использование: /addbiz [тип: default/station] [цена] [название]")
            
            b_type = arguments[1].lower()
            try: price = int(arguments[2])
            except: return await message.reply("Цена должна быть числом!")
            
            name = await get_string(arguments, 3)
            profit = int(price * 0.05)
            
            sql.execute("INSERT INTO businesses (name, price, profit_per_hour, type) VALUES (?, ?, ?, ?)", (name, price, profit, b_type))
            bid = sql.lastrowid; database.commit()
            
            await message.reply(f"✅ Бизнес «{name}» добавлен! ID: {bid}")
            await log_action(user_id, chat_id, f"Добавил новый бизнес «{name}» (ID: {bid}, Тип: {b_type}, Цена: {price}).")
            return True

        if command in ['delbiz', 'удалитьбиз']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("❌ Недостаточно прав!")
                return True
            
            if len(arguments) < 2:
                return await message.reply("📝 Использование: /delbiz [ID бизнеса]")
            
            try: bid = int(arguments[1])
            except: return await message.reply("ID должен быть числом!")
            
            sql.execute("SELECT name FROM businesses WHERE id = ?", (bid,))
            res = sql.fetchone()
            if not res: return await message.reply("❌ Бизнес не найден!")
            
            sql.execute("DELETE FROM businesses WHERE id = ?", (bid,))
            database.commit()
            
            await message.reply(f"✅ Бизнес «{res[0]}» (ID: {bid}) удален!")
            await log_action(user_id, chat_id, f"Удалил бизнес «{res[0]}» (ID: {bid}).")
            return True

        # --- AUCTION COMMANDS ---
        if command in ['start_auction', 'стартаук']:
            if len(arguments) < 2:
                return await message.reply("📝 Использование: /start_auction [ID бизнеса] [длительность_мин (опционально)] [мин.ставка (опционально)]")
            try: bid = int(arguments[1])
            except: return await message.reply("ID должен быть числом!")

            sql.execute("SELECT name, price, owner_id, clan_owner_id FROM businesses WHERE id = ?", (bid,))
            res = sql.fetchone()
            if not res: return await message.reply("❌ Бизнес не найден!")
            name, price, owner, c_owner = res

            # Только владелец бизнеса или админ может выставить на аукцион
            if owner != user_id and await get_role(user_id, chat_id) < 6:
                return await message.reply("❌ Только владелец бизнеса или админ может начать аукцион!")

            duration = 60
            if len(arguments) > 2:
                try: duration = int(arguments[2])
                except: pass
            min_bid = price
            if len(arguments) > 3:
                try: min_bid = int(arguments[3])
                except: pass

            now = int(time.time())
            end_time = now + max(1, duration) * 60

            # Снимаем владельца с бизнеса на время аукциона
            sql.execute("UPDATE businesses SET owner_id = 0, clan_owner_id = 0 WHERE id = ?", (bid,))
            sql.execute("INSERT INTO auctions (biz_id, seller_id, start_time, end_time, min_bid, status) VALUES (?, ?, ?, ?, ?, 'active')", (bid, owner, now, end_time, min_bid))
            database.commit()

            await message.reply(f"🏷 Аукцион начат: «{name}» (ID: {bid}). Минимальная ставка: {min_bid:,}$. Длительность: {duration} мин.")
            return True

        if command in ['bid', 'ставка']:
            if len(arguments) < 3:
                return await message.reply("📝 Использование: /bid [ID бизнеса] [сумма]")
            try: bid_id = int(arguments[1]); amount = int(arguments[2])
            except: return await message.reply("ID и сумма должны быть числами!")

            sql.execute("SELECT a.id, a.min_bid, a.highest_bid, a.highest_bidder, a.end_time, b.name, a.seller_id FROM auctions a LEFT JOIN businesses b ON a.biz_id = b.id WHERE a.biz_id = ? AND a.status = 'active'", (bid_id,))
            auc = sql.fetchone()
            if not auc: return await message.reply("❌ Для этого бизнеса нет активного аукциона!")

            a_id, min_bid, highest_bid, highest_bidder, end_time, biz_name, seller = auc
            now = int(time.time())
            if end_time <= now:
                return await message.reply("⏳ Аукцион уже завершился — дождитесь обработки командой /end_auctions")

            # Минимальный шаг: 5% от min_bid или 1
            step = max(1, int(min_bid * 0.05))
            required = max(min_bid, highest_bid + step)
            if amount < required:
                return await message.reply(f"❌ Ставка слишком мала! Минимальная допустимая ставка: {required:,}$")

            bal = await get_balance(user_id)
            if bal < amount:
                return await message.reply("❌ Недостаточно средств для ставки!")

            # Снимаем средства с нового победителя и возвращаем предыдущему, если был
            if not await subtract_balance(user_id, amount):
                return await message.reply("❌ Ошибка списания средств!")
            if highest_bidder and highest_bidder != 0:
                await add_balance(highest_bidder, highest_bid)

            # Обновляем лучшую ставку
            sql.execute("UPDATE auctions SET highest_bid = ?, highest_bidder = ? WHERE id = ?", (amount, user_id, a_id))
            # Если ставка сделана за 30 сек до конца — продлеваем на 30 сек
            if end_time - now < 30:
                new_end = end_time + 30
                sql.execute("UPDATE auctions SET end_time = ? WHERE id = ?", (new_end, a_id))
            database.commit()

            await message.reply(f"✅ Ваша ставка {amount:,}$ принята на аукционе «{biz_name}» (ID: {bid_id})!".replace(",", "."))
            return True

        if command in ['auctions', 'аукционы']:
            now = int(time.time())
            sql.execute("SELECT a.biz_id, b.name, a.min_bid, a.highest_bid, a.highest_bidder, a.end_time FROM auctions a LEFT JOIN businesses b ON a.biz_id = b.id WHERE a.status = 'active' ORDER BY a.end_time ASC LIMIT 30")
            rows = sql.fetchall()
            if not rows:
                return await message.reply("🛎 Активных аукционов нет.")
            msg = "🏷 Активные аукционы:\n\n"
            for biz_id, name, min_bid, highest, hb, end_t in rows:
                left = max(0, end_t - now)
                mins = left // 60; secs = left % 60
                hb_str = f"{highest:,}$ (участвует: {hb})" if highest and highest > 0 else "нет ставок"
                msg += f"ID {biz_id} — {name} | min: {min_bid:,}$ | top: {hb_str} | оставшееcь: {mins}м {secs}с\n".replace(",", ".")
            return await message.reply(msg)

        if command in ['end_auctions', 'end_auction', 'завершитьаук']:
            # Можно завершить все просроченные или конкретный по ID
            now = int(time.time())
            target = None
            if len(arguments) > 1:
                try: target = int(arguments[1])
                except: return await message.reply("ID должен быть числом!")

            if target:
                sql.execute("SELECT id, biz_id, seller_id, highest_bid, highest_bidder FROM auctions WHERE biz_id = ? AND status = 'active'", (target,))
                auctions = [sql.fetchone()]
            else:
                sql.execute("SELECT id, biz_id, seller_id, highest_bid, highest_bidder FROM auctions WHERE status = 'active' AND end_time <= ?", (now,))
                auctions = sql.fetchall()

            if not auctions or auctions == [None]:
                return await message.reply("⚠ Нет аукционов для завершения.")

            for a in auctions:
                if not a: continue
                a_id, biz_id, seller_id, highest_bid, highest_bidder = a
                # Завершение
                if highest_bid and highest_bidder and highest_bidder != 0:
                    # Переводим деньги продавцу (если это пользователь) и передаем бизнес победителю
                    if seller_id and seller_id > 0:
                        await add_balance(seller_id, highest_bid)
                    else:
                        econ = load_economy()
                        if 'server_stats' not in econ: econ['server_stats'] = {}
                        econ['server_stats']['auctions_income'] = econ['server_stats'].get('auctions_income', 0) + highest_bid
                        save_economy(econ)
                    sql.execute("UPDATE businesses SET owner_id = ? WHERE id = ?", (highest_bidder, biz_id))
                    result_msg = f"🏁 Аукцион ID {biz_id} завершён. Победитель: {await get_user_link(highest_bidder)} — ставка {highest_bid:,}$"
                else:
                    # Ни одной ставки — возвращаем бизнес продавцу
                    sql.execute("UPDATE businesses SET owner_id = ? WHERE id = ?", (seller_id, biz_id))
                    result_msg = f"🏁 Аукцион ID {biz_id} завершён. Ставок не было — бизнес возвращён продавцу."

                sql.execute("UPDATE auctions SET status = 'finished' WHERE id = ?", (a_id,))
                database.commit()
                try: await message.reply(result_msg.replace(",", "."))
                except: pass
            return True

        if command in ['editbiz', 'редбиз']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!")
                return True
            
            if len(arguments) < 4:
                return await message.reply("📝 Использование: /editbiz [ID] [поле: name/price/profit] [значение]")
            
            try: bid = int(arguments[1])
            except: return await message.reply("ID должен быть числом!")
            
            field = arguments[2].lower()
            raw_val = await get_string(arguments, 3)
            if field == 'name': val = raw_val
            elif field in ['price', 'profit']:
                try: 
                    val = int(raw_val.replace('.', '').replace(',', ''))
                    if field == 'profit': field = 'profit_per_hour'
                except: return await message.reply("Значение должно быть числом!")
            else: return await message.reply("❌ Неверное поле! Доступно: name, price, profit")
            
            sql.execute(f"UPDATE businesses SET {field} = ? WHERE id = ?", (val, bid)); database.commit()
            await message.reply(f"✅ Бизнес #{bid} обновлен. Поле «{field}» теперь: {val}")
            return True

        if command in ['resetclanbiz', 'делкланбиз']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("❌ Недостаточно прав!")
                return True
            if len(arguments) < 2:
                return await message.reply("📝 Использование: /resetclanbiz [ID бизнеса]")
            try: bid = int(arguments[1])
            except: return await message.reply("❌ ID должен быть числом!")
            sql.execute("SELECT name, clan_owner_id, price FROM businesses WHERE id = ?", (bid,))
            res = sql.fetchone()
            if not res: return await message.reply("❌ Бизнес не найден!")
            if res[1] == 0: return await message.reply("❌ Этот бизнес не принадлежит клану!")
            
            base_profit = int(res[2] * 0.05)
            sql.execute("UPDATE businesses SET owner_id = 0, clan_owner_id = 0, level = 1, profit_per_hour = ?, special_order_active = 0, active_route = 1 WHERE id = ?", (base_profit, bid))
            database.commit()
            await message.reply(f"✅ Бизнес «{res[0]}» (ID: {bid}) изъят у клана и возвращен государству.")
            await log_action(user_id, chat_id, f"Изъял клановый бизнес «{res[0]}» (ID: {bid}).")
            return True

        if command in ['reindexclans', 'переиндекскланов']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("❌ Недостаточно прав!")
                return True
            
            if len(arguments) < 2 or arguments[1].lower() != "confirm":
                return await message.reply("⚠️ ВНИМАНИЕ! Эта команда переиндексирует все ID кланов и обновит все связанные ссылки. Это потенциально опасная операция!\n\nДля подтверждения напишите: /reindexclans confirm")

            await message.reply("⏳ Начинаю переиндексацию ID кланов... Это может занять время.")
            
            try:
                # 1. Получаем все данные о кланах
                sql.execute("SELECT clan_id, name, owner_id, tag, level, exp, money, mats, max_mats, r0_name, r1_name, r2_name, r3_name, r4_name, r5_name, type, treasury, tactic, tactic_end, wins FROM clans ORDER BY clan_id ASC")
                all_clans_data = sql.fetchall()
                
                if not all_clans_data:
                    return await message.reply("❌ В базе нет кланов для переиндексации!")
                
                # 2. Очищаем таблицу clans и сбрасываем AUTOINCREMENT
                sql.execute("DELETE FROM clans")
                sql.execute("DELETE FROM sqlite_sequence WHERE name='clans'")
                database.commit()
                
                # 3. Создаем карту старых ID на новые
                id_mapping = {}
                new_clan_id_counter = 1
                
                # 4. Вставляем кланы обратно, получая новые ID
                for old_clan_id, name, owner_id, tag, level, exp, money, mats, max_mats, r0_name, r1_name, r2_name, r3_name, r4_name, r5_name, type, treasury, tactic, tactic_end, wins in all_clans_data:
                    sql.execute(
                        "INSERT INTO clans (name, owner_id, tag, level, exp, money, mats, max_mats, r0_name, r1_name, r2_name, r3_name, r4_name, r5_name, type, treasury, tactic, tactic_end, wins) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (name, owner_id, tag, level, exp, money, mats, max_mats, r0_name, r1_name, r2_name, r3_name, r4_name, r5_name, type, treasury, tactic, tactic_end, wins)
                    )
                    new_id = sql.lastrowid
                    id_mapping[old_clan_id] = new_id
                    new_clan_id_counter += 1
                database.commit()
                
                # 5. Обновляем все связанные таблицы
                for old_id, new_id in id_mapping.items():
                    # user_data
                    sql.execute("UPDATE user_data SET clan_id = ? WHERE clan_id = ?", (new_id, old_id))
                    # clan_wars
                    sql.execute("UPDATE clan_wars SET attacker_id = ? WHERE attacker_id = ?", (new_id, old_id))
                    sql.execute("UPDATE clan_wars SET defender_id = ? WHERE defender_id = ?", (new_id, old_id))
                    # clan_quests
                    sql.execute("UPDATE clan_quests SET clan_id = ? WHERE clan_id = ?", (new_id, old_id))
                    # clan_bosses
                    sql.execute("UPDATE clan_bosses SET clan_id = ? WHERE clan_id = ?", (new_id, old_id))
                    # clan_boss_cooldowns
                    sql.execute("UPDATE clan_boss_cooldowns SET clan_id = ? WHERE clan_id = ?", (new_id, old_id))
                    # businesses
                    sql.execute("UPDATE businesses SET clan_owner_id = ? WHERE clan_owner_id = ?", (new_id, old_id))
                    # clan_alliances
                    sql.execute("UPDATE clan_alliances SET clan1 = ? WHERE clan1 = ?", (new_id, old_id))
                    sql.execute("UPDATE clan_alliances SET clan2 = ? WHERE clan2 = ?", (new_id, old_id))
                    # clan_ally_requests
                    sql.execute("UPDATE clan_ally_requests SET from_clan = ? WHERE from_clan = ?", (new_id, old_id))
                    sql.execute("UPDATE clan_ally_requests SET to_clan = ? WHERE to_clan = ?", (new_id, old_id))
                
                database.commit()

                # 6. Обновляем clans.json для всех кланов
                for new_id in id_mapping.values():
                    await save_clan_to_json(new_id)
                
                await message.reply(f"✅ Переиндексация ID кланов завершена!\n📊 Всего кланов переиндексировано: {len(all_clans_data)}\n🔄 Все связанные ссылки обновлены.")
                await log_action(user_id, chat_id, f"Переиндексировал {len(all_clans_data)} кланов. ID теперь идут последовательно.")
                
            except Exception as e:
                await message.reply(f"❌ Ошибка при переиндексации кланов: {str(e)}")
                logging.error(f"Reindex clans error: {e}")
            
            return True

        if command in ['resetbiz', 'бизнесслёт']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("❌ Недостаточно прав!")
                return True
            if len(arguments) < 2:
                return await message.reply("📝 Использование: /resetbiz [ID бизнеса]")
            try: bid = int(arguments[1])
            except: return await message.reply("❌ ID должен быть числом!")
            
            sql.execute("SELECT name, price, owner_id, clan_owner_id FROM businesses WHERE id = ?", (bid,))
            res = sql.fetchone()
            if not res: return await message.reply("❌ Бизнес не найден!")
            
            base_profit = int(res[1] * 0.05)
            sql.execute("UPDATE businesses SET owner_id = 0, clan_owner_id = 0, level = 1, profit_per_hour = ?, special_order_active = 0, active_route = 1 WHERE id = ?", (base_profit, bid))
            database.commit()
            
            await message.reply(f"✅ Бизнес «{res[0]}» (ID: {bid}) успешно сброшен. Владельцы удалены, уровень сброшен до 1.")
            await log_action(user_id, chat_id, f"Принудительно сбросил бизнес «{res[0]}» (ID: {bid}) в гос. собственность.")
            return True

        if command in ['reindexbiz', 'переиндексбиз', 'rebiz']:
            # Только разработчик может переиндексировать бизнесы
            if await get_role(user_id, chat_id) < 6:
                await message.reply("❌ Недостаточно прав! Только разработчик может переиндексировать бизнесы.")
                return True
            
            try:
                await message.reply("⏳ Переиндексирование ID бизнесов... Это может занять время.")
                
                # Получаем все бизнесы, отсортированные по ID
                sql.execute("SELECT id, name, price, profit_per_hour, owner_id, type, last_collect, active_route, repair_until, special_order_active, clan_owner_id, level FROM businesses ORDER BY id ASC")
                all_bizs = sql.fetchall()
                
                if not all_bizs:
                    return await message.reply("❌ В базе нет бизнесов для переиндексирования!")
                
                # Создаем временную таблицу
                sql.execute("ALTER TABLE businesses RENAME TO businesses_old")
                database.commit()
                
                # Создаем новую таблицу с тем же схемой
                sql.execute("CREATE TABLE IF NOT EXISTS businesses (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, price BIGINT, profit_per_hour BIGINT, owner_id BIGINT DEFAULT 0, type TEXT DEFAULT 'default', last_collect INTEGER DEFAULT 0, active_route INTEGER DEFAULT 1, repair_until INTEGER DEFAULT 0, special_order_active INTEGER DEFAULT 0, clan_owner_id INTEGER DEFAULT 0, level INTEGER DEFAULT 1)")
                database.commit()
                
                # Словарь для сопоставления старых ID на новые
                id_mapping = {}
                
                # Вставляем бизнесы с новыми ID
                for idx, biz in enumerate(all_bizs, 1):
                    old_id = biz[0]
                    sql.execute(
                        "INSERT INTO businesses (id, name, price, profit_per_hour, owner_id, type, last_collect, active_route, repair_until, special_order_active, clan_owner_id, level) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (idx, biz[1], biz[2], biz[3], biz[4], biz[5], biz[6], biz[7], biz[8], biz[9], biz[10], biz[11])
                    )
                    id_mapping[old_id] = idx
                
                database.commit()
                
                # Обновляем ссылки в таблице biz_offers
                sql.execute("SELECT id, biz_id FROM biz_offers")
                offers = sql.fetchall()
                for offer_id, old_biz_id in offers:
                    new_biz_id = id_mapping.get(old_biz_id, old_biz_id)
                    sql.execute("UPDATE biz_offers SET biz_id = ? WHERE id = ?", (new_biz_id, offer_id))
                
                # Обновляем ссылки в таблице clan_wars
                sql.execute("SELECT war_id, target_biz_id FROM clan_wars WHERE target_biz_id > 0")
                wars = sql.fetchall()
                for war_id, old_biz_id in wars:
                    new_biz_id = id_mapping.get(old_biz_id, old_biz_id)
                    sql.execute("UPDATE clan_wars SET target_biz_id = ? WHERE war_id = ?", (new_biz_id, war_id))
                
                database.commit()
                
                # Удаляем старую таблицу
                sql.execute("DROP TABLE businesses_old")
                database.commit()
                
                msg = f"""✅ Переиндексирование завершено!
📊 Всего бизнесов переиндексировано: {len(all_bizs)}
🔄 Все ссылки в других таблицах обновлены.
✨ ID теперь идут последовательно от 1 до {len(all_bizs)}"""
                
                await message.reply(msg)
                await log_action(user_id, chat_id, f"Переиндексировал {len(all_bizs)} бизнесов. ID теперь идут последовательно.")
                
            except Exception as e:
                await message.reply(f"❌ Ошибка при переиндексировании: {str(e)}")
                logging.error(f"Reindex error: {e}")
                # Попытка восстановления
                try:
                    sql.execute("DROP TABLE IF EXISTS businesses")
                    sql.execute("ALTER TABLE businesses_old RENAME TO businesses")
                    database.commit()
                except:
                    pass
            
            return True

        if command in ['setpetlvl', 'сетпетлвл']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("❌ Недостаточно прав!")
                return True
            target, err = await get_target_user(message, arguments)
            if err: return await message.reply(err)
            try: lvl = int(arguments[-1])
            except: return await message.reply("📝 Использование: /setpetlvl [пользователь] [уровень]")
            
            p_data = await get_pet_data(target)
            if not p_data: return await message.reply("❌ У этого пользователя нет питомца!")
            
            sql.execute("UPDATE pets SET level = ?, exp = 0 WHERE user_id = ?", (lvl, target))
            database.commit()
            t_name = await get_user_name(target, chat_id)
            await message.reply(f"✅ Уровень питомца пользователя [id{target}|{t_name}] изменен на {lvl}.")
            await log_action(user_id, chat_id, f"Изменил уровень питомца пользователя {target} на {lvl}.")
            return True

        if command in ['invite']:
            if not await check_perm(user_id, chat_id, command, 5):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            result = await invite_kick(chat_id, True)
            if result: 
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) включил(-а) функцию приглашения модераторами")
                await log_action(user_id, chat_id, "Включил функцию приглашения модераторами.")
            else: 
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выключил(-а) функцию приглашения модераторами")
                await log_action(user_id, chat_id, "Выключил функцию приглашения модераторами.")

        if command in ['leave']:
            if not await check_perm(user_id, chat_id, command, 5):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            result = await leave_kick(chat_id, True)
            if result: 
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) включил(-а) функцию исключения при выходе")
                await log_action(user_id, chat_id, "Включил функцию исключения при выходе.")
            else: 
                await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выключил(-а) функцию исключения при выходе")
                await log_action(user_id, chat_id, "Выключил функцию исключения при выходе.")

        if command in ['addsenadmin', 'addsenadm', 'senadm', 'senadmin']:
            if not await check_perm(user_id, chat_id, command, 5):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            if message.reply_message: user = message.reply_message.from_id
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await equals_roles(user_id, user, chat_id) < 2:
                await message.reply("Вы не можете выдать роль данному пользователю!", disable_mentions=1)
                return True

            await roleG(user, chat_id, 4)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) выдал(-а) права старшего администратора @id{user} ({await get_user_name(user, chat_id)})", disable_mentions=1)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Выдал права старшего администратора пользователю {u_name}.")

        if command in ['server', 'setserver']:
            if not await check_perm(user_id, chat_id, command, 5):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            if len(arguments) < 2:
                await message.reply("Укажите номер сервера! 0 - удалить привязку.")
                return True

            server_id_arg = arguments[1]
            try: server_id = int(server_id_arg)
            except:
                await message.reply("ID сервера должно быть в виде числа")
                return True

            if server_id < 0:
                await message.reply("ID сервера не должен быть меньше нуля")
                return True
            if server_id > 2000:
                await message.reply("ID сервера не должен быть больше 2000")
                return True

            await set_server(chat_id, server_id)
            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) изменил(-а) сервер беседы на {server_id}")
            await log_action(user_id, chat_id, f"Изменил сервер беседы на {server_id}.")

        if command in ['news']:
            # Только владелец беседы может рассылать новости
            x = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id, extended=1, fields='chat_settings', group_id=message.group_id)
            x = json.loads(x.json())
            owner_id = None
            for i in x['items']:
                owner_id = int(i["chat_settings"]["owner_id"])

            if owner_id != user_id and await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            reason = await get_string(arguments, 1)
            if not reason:
                await message.reply("Укажите текст!")
                return True

            peer_ids = await get_all_peerids()
            for i in peer_ids:
                try: await bot.api.messages.send(peer_id=i, message=reason, disable_mentions=1, random_id=0)
                except: pass
            
            await log_action(user_id, chat_id, f"Сделал рассылку новостей:\n{reason}")

        if command in ['gban', 'гбан']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!")
                return True
            
            user = 0
            arg_offset = 1
            if message.reply_message:
                user = message.reply_message.from_id
            elif len(arguments) > 1:
                resolved_id = await getID(arguments[1])
                if resolved_id:
                    user = resolved_id
                    arg_offset = 2
            
            if not user:
                return await message.reply("Укажите пользователя!")

            reason = await get_string(arguments, arg_offset) or "Не указана"
            
            date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sql.execute("INSERT OR REPLACE INTO global_bans (user_id, ban_type, moder, reason, date) VALUES (?, 'all', ?, ?, ?)", (user, user_id, reason, date_str))
            database.commit()
            
            try:
                info = await bot.api.users.get(user_ids=user)
                user_name = f"[id{user}|{info[0].first_name} {info[0].last_name}]"
                first_name = info[0].first_name
            except: 
                user_name = f"@id{user}"
                first_name = f"@id{user}"
            
            await message.reply(f"🚫 Пользователь {user_name} заблокирован во всех беседах!\nПричина: {reason}\n\n⏳ Начинаю процедуру исключения из всех бесед...", disable_mentions=1)
            
            kick_msg = (f"{user_name}, находится в общей блокировке!\n"
                        f"Информация о блокировке:\n"
                        f"[id{user_id}|Модератор] | {reason} | {date_str} МСК (UTC+3)")
            
            sql.execute("SELECT chat_id FROM chats")
            for (cid,) in sql.fetchall():
                try: 
                    await bot.api.messages.remove_chat_user(cid, user)
                    await bot.api.messages.send(peer_id=2000000000+cid, message=kick_msg, random_id=0, disable_mentions=1)
                except: pass
            
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Выдал глобальный бан пользователю {u_name}.\nПричина: {reason}")

        if command in ['gbanpl', 'гбанпл']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!")
                return True
            
            user = 0
            arg_offset = 1
            if message.reply_message:
                user = message.reply_message.from_id
            elif len(arguments) > 1:
                resolved_id = await getID(arguments[1])
                if resolved_id:
                    user = resolved_id
                    arg_offset = 2
            
            if not user:
                return await message.reply("Укажите пользователя!")

            reason = await get_string(arguments, arg_offset) or "Не указана"
            
            date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sql.execute("INSERT OR REPLACE INTO global_bans (user_id, ban_type, moder, reason, date) VALUES (?, 'pl', ?, ?, ?)", (user, user_id, reason, date_str))
            database.commit()
            
            try:
                info = await bot.api.users.get(user_ids=user)
                user_name = f"[id{user}|{info[0].first_name} {info[0].last_name}]"
                first_name = info[0].first_name
            except: 
                user_name = f"@id{user}"
                first_name = f"@id{user}"
            
            await message.reply(f"🚫 Пользователь {user_name} заблокирован в игровых беседах (PL)!\nПричина: {reason}\n\n⏳ Начинаю процедуру исключения из игровых бесед...", disable_mentions=1)
            
            kick_msg = (f"{user_name}, находится в блокировке игровых бесед!\n"
                        f"Информация о блокировке:\n"
                        f"[id{user_id}|Модератор] | {reason} | {date_str} МСК (UTC+3)")
            
            sql.execute("SELECT chat_id FROM chats WHERE chat_type = 'pl'")
            for (cid,) in sql.fetchall():
                try: 
                    await bot.api.messages.remove_chat_user(cid, user)
                    await bot.api.messages.send(peer_id=2000000000+cid, message=kick_msg, random_id=0, disable_mentions=1)
                except: pass
            
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Выдал глобальный бан (PL) пользователю {u_name}.\nПричина: {reason}")

        if command in ['ungban', 'gunban', 'разгбан', 'gunbanpl', 'ungbanpl']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!")
                return True
            
            user = 0
            if message.reply_message:
                user = message.reply_message.from_id
            elif len(arguments) > 1:
                resolved_id = await getID(arguments[1])
                if resolved_id:
                    user = resolved_id
            
            if not user:
                return await message.reply("Укажите пользователя!")
                return True
            
            sql.execute("DELETE FROM global_bans WHERE user_id = ?", (user,))
            database.commit()
            
            try:
                info = await bot.api.users.get(user_ids=user)
                user_name = f"[id{user}|{info[0].first_name} {info[0].last_name}]"
            except: user_name = f"@id{user}"
            
            await message.reply(f"✅ Глобальная блокировка с пользователя {user_name} снята!", disable_mentions=1)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Снял глобальный бан с пользователя {u_name}.")

        if command in ['aban', 'абан', 'заморозить']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!")
                return True
            
            target, err = await get_target_user(message, arguments)
            if err: return await message.reply(err)

            t_role = await get_role(target, chat_id)
            t_global = await get_global_role(target)
            if t_role >= 4:
                return await message.reply("❌ Нельзя заморозить права Ст. Администратора или выше!")
            if t_global > 0:
                return await message.reply("❌ Нельзя заморозить права пользователя с глобальной ролью!")
            
            await update_user_data(target, 'aban', 1)
            t_name = await get_user_name(target, chat_id)
            await message.reply(f"❄️ Права администратора [id{target}|{t_name}] временно заморожены.")
            await log_action(user_id, chat_id, f"Заморозил права (aban) пользователю {target}.")
            return True

        if command in ['unaban', 'разморозить', 'упабан']:
            if await get_role(user_id, chat_id) < 1:
                await message.reply("Недостаточно прав!")
                return True
            
            target, err = await get_target_user(message, arguments)
            if err: return await message.reply(err)

            t_role = await get_role(target, chat_id)
            t_global = await get_global_role(target)
            if t_role >= 4:
                return await message.reply("❌ Нельзя разморозить права Ст. Администратора или выше!")
            if t_global > 0:
                return await message.reply("❌ Нельзя разморозить права пользователя с глобальной ролью!")
            
            await update_user_data(target, 'aban', 0)
            t_name = await get_user_name(target, chat_id)
            await message.reply(f"☀️ Права администратора [id{target}|{t_name}] разморожены.")
            await log_action(user_id, chat_id, f"Разморозил права (unaban) пользователю {target}.")
            return True

        if command in ['adddeveloper', 'добавитьразраба']:
            if user_id != CREATOR_ID:
                return await message.reply("⛔ Эта команда доступна только создателю бота.")

            target = 0
            if message.reply_message:
                target = message.reply_message.from_id
            elif len(arguments) > 1:
                resolved_id = await getID(arguments[1])
                if resolved_id:
                    target = resolved_id
            
            if not target:
                return await message.reply("📝 Использование: /adddeveloper [пользователь]")

            if target == CREATOR_ID:
                return await message.reply("Вы не можете выдать права самому себе.")
            
            sql.execute("SELECT level FROM global_managers WHERE user_id = ?", (target,))
            res = sql.fetchone()
            if res and res[0] >= 5:
                return await message.reply("✅ Пользователь уже является разработчиком или выше.")

            sql.execute("INSERT OR REPLACE INTO global_managers (user_id, level) VALUES (?, ?)", (target, 5))
            database.commit()
            
            try: u_info = await bot.api.users.get(user_ids=target); u_name = f"[id{target}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{target}"
            await message.reply(f"✅ Пользователю {u_name} выданы права разработчика.")
            await log_action(user_id, chat_id, f"Выдал права разработчика пользователю {u_name}.")

        if command in ['снятьразработчика', 'removedeveloper']:
            if user_id != CREATOR_ID:
                return await message.reply("⛔ Эта команда доступна только создателю бота.")

            target = 0
            if message.reply_message:
                target = message.reply_message.from_id
            elif len(arguments) > 1:
                resolved_id = await getID(arguments[1])
                if resolved_id:
                    target = resolved_id
            
            if not target:
                return await message.reply("📝 Использование: /снятьразработчика [пользователь]")

            if target == CREATOR_ID:
                return await message.reply("⛔ Нельзя снять права с создателя.")

            sql.execute("DELETE FROM global_managers WHERE user_id = ? AND level = 5", (target,))
            if sql.rowcount == 0:
                return await message.reply("❌ Пользователь не является разработчиком (или имеет более высокий уровень).")
            database.commit()
            
            try: u_info = await bot.api.users.get(user_ids=target); u_name = f"[id{target}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{target}"
            await message.reply(f"✅ С пользователя {u_name} сняты права разработчика.")
            await log_action(user_id, chat_id, f"Снял права разработчика с пользователя {u_name}.")

        if command in ['gzov', 'гзов']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!")
                return True
            
            if len(arguments) < 3: return await message.reply("📝 Использование: /gzov [тип_чатов] [причина]\nТипы: all, pl, def, ext...")
            
            c_type = arguments[1].lower()
            reason = await get_string(arguments, 2)
            
            sql.execute("SELECT chat_id FROM chats") if c_type == 'all' else sql.execute("SELECT chat_id FROM chats WHERE chat_type = ?", (c_type,))
            chats = sql.fetchall()
            
            await message.reply(f"📣 Глобальный сбор начался по {len(chats)} бесед...")
            
            for (cid,) in chats:
                try:
                    users = await bot.api.messages.get_conversation_members(peer_id=2000000000+cid, fields=["online_info", "online"])
                    users = json.loads(users.json())
                    user_f = []
                    gi = 0
                    for b in users["profiles"]:
                        if not b['id'] == user_id:
                            gi = gi + 1
                            if gi <= 100:
                                user_f.append(f"@id{b['id']} (📣)")
                    
                    if user_f:
                        zov_users = ''.join(user_f)
                        await bot.api.messages.send(peer_id=2000000000+cid, message=f"📣 Глобальный сбор!\nВызвал: @id{user_id} (Разработчик)\n\n{zov_users}\n\n❗ Причина: {reason}", random_id=0)
                    
                    await asyncio.sleep(0.2)
                except: pass
            
            await message.reply("✅ Глобальный сбор завершен!")

        if command in ['szov', 'serverzov', 'сзов']:
            if not await check_perm(user_id, chat_id, command, 3):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            reason = await get_string(arguments, 1)
            if not reason:
                await message.reply("Укажите причину вызова!")
                return True

            if not await get_server_chats(chat_id): return await message.reply("Сначала укажите сервер!")

            server_id = await get_server_id(chat_id)
            for i in await get_server_chats(chat_id):
                users = await bot.api.messages.get_conversation_members(peer_id=2000000000+i, fields=["online_info", "online"])
                users = json.loads(users.json())
                user_f = []
                gi = 0
                for b in users["profiles"]:
                    if not b['id'] == user_id:
                        gi = gi + 1
                        if gi <= 100:
                            user_f.append(f"@id{b['id']} (🖤)")
                zov_users = ''.join(user_f)

                await bot.api.messages.send(peer_id=2000000000+i, message=f"🔔 Вы были вызваны @id{user_id} (администратором) бесед сервера «{server_id}»\n\n{zov_users}\n\n❗ Причина вызова: {reason}", random_id=0)

        if command in ['editowner', 'owner', 'setowner']:
            if await get_global_role(user_id) < 2:
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            arg = 0
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]):
                user = await getID(arguments[1])
                arg = 2
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            sql.execute("SELECT owner_id FROM chats WHERE chat_id = ?", (chat_id,))
            current_db_owner = sql.fetchone()[0]
            if user == user_id and current_db_owner == user_id: return await message.reply("Вы уже являетесь владельцем беседы!")

            confirm_arg = arguments[arg].lower() if len(arguments) > arg else ""
            if confirm_arg != "confirm":
                return await message.reply("После указания пользователя напишите <<confirm>> (Пример: /owner confirm)")
            
            await set_onwer(user, chat_id)
            await roleG(user_id, chat_id, 4)

            await message.answer(f"@id{user_id} ({await get_user_name(user_id, chat_id)}) успешно передал(-a) права владельца беседы пользователю @id{user} ({await get_user_name(user, chat_id)})\n@id{user_id} ({await get_user_name(user_id, chat_id)}) выданы права Старшего Администратора.")
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Передал права владельца беседы пользователю {u_name}.")

        if command in ['forceowner', 'fowner']:
            if not await check_perm(user_id, chat_id, command, 6):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True
            
            await set_onwer(user_id, chat_id)
            sql.execute(f"DELETE FROM permissions_{chat_id} WHERE user_id = ?", (user_id,))
            database.commit()
            await message.reply(f"⚡ @id{user_id} ({await get_user_name(user_id, chat_id)}) принудительно стал владельцем беседы!", disable_mentions=1)
            try: u_info = await bot.api.users.get(user_ids=user_id); u_name = f"[id{user_id}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user_id}"
            await log_action(user_id, chat_id, f"Принудительно стал владельцем беседы (был: {u_name}).")

        if command in ['реф', 'ref', 'пригласил', 'referral']:
            ud = await get_user_data(user_id)
            
            if len(arguments) > 1 and arguments[1].lower() in ['отмена', 'cancel', 'reset', 'сброс']:
                if ud.get('referrer_id', 0) == 0:
                    return await message.reply("❌ У вас не установлен пригласивший!")
                await update_user_data(user_id, 'referrer_id', 0)
                return await message.reply("✅ Пригласивший успешно удален.")

            if ud.get('referrer_id', 0) > 0:
                inviter_link = await get_user_link(ud['referrer_id'])
                kb = Keyboard(inline=True).add(Callback("❌ Удалить пригласившего", {"command": "remove_referrer", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.NEGATIVE)
                return await message.reply(f"✅ У вас уже установлен пригласивший: {inviter_link}\nЧтобы его убрать, введите: /реф отмена или нажмите на кнопку.", keyboard=kb)
            
            if len(arguments) < 2:
                return await message.reply(f"🎁 Реферальная система\n\n"
                                         f"Если вас пригласил друг, укажите его ID или ссылку: /реф [ID/ссылка]\n"
                                         f"Ваш пригласивший будет получать 2% бонуса от ваших зарплат на работах!\n\n"
                                         f"🆔 Ваш ID для друзей: {user_id}")
            
            target_id = await getID(arguments[1])
            if not target_id or target_id == user_id:
                return await message.reply("❌ Укажите корректный ID пригласившего (не свой).")
            
            # Проверка регистрации пригласившего
            econ = load_economy()
            if str(target_id) not in econ['users']:
                return await message.reply("❌ Этот пользователь еще не зарегистрирован в боте.")
            
            # Проверка на взаимную рефералку (A пригласил B, B не может пригласить A)
            target_ud = await get_user_data(target_id)
            if target_ud.get('referrer_id') == user_id:
                return await message.reply("❌ Взаимная реферальная система запрещена! Этот пользователь уже указал вас как своего пригласившего.")

            await update_user_data(user_id, 'referrer_id', target_id)
            inviter_link = await get_user_link(target_id)
            await message.reply(f"✅ Готово! Теперь {inviter_link} — ваш пригласивший. Он будет получать 2% бонуса с вашей работы.")
            
            try:
                await bot.api.messages.send(user_id=target_id, message=f"🎊 Пользователь [id{user_id}|{await get_user_name(user_id, chat_id)}] указал вас как пригласившего! Вы будете получать 2% от его зарплат на работах.", random_id=0)
            except: pass
            return True

        if command in ['моирефы', 'myrefs', 'myref']:
            sql.execute("SELECT user_id FROM user_data WHERE referrer_id = ?", (user_id,))
            refs = sql.fetchall()
            
            if not refs:
                return await message.reply("😔 У вас пока нет рефералов. Приглашайте друзей, чтобы получать 2% от их зарплаты!")
            
            ref_ids = [r[0] for r in refs]
            msg = f"🤝 Ваши рефералы (всего: {len(ref_ids)}):\n\n"
            
            formatted_refs = []
            for i in range(0, len(ref_ids), 100):
                chunk = ref_ids[i:i+100]
                try:
                    u_infos = await bot.api.users.get(user_ids=chunk)
                    for u in u_infos:
                        formatted_refs.append(f"• [id{u.id}|{u.first_name} {u.last_name}]")
                except:
                    for uid in chunk:
                        formatted_refs.append(f"• [id{uid}|Пользователь]")
            
            msg += "\n".join(formatted_refs)
            if len(msg) > 4000: msg = msg[:4000] + "\n... (список слишком длинный)"
            return await message.reply(msg, disable_mentions=1)

        if command in ['srole', 'serverrole']:
            if not await check_perm(user_id, chat_id, command, 5):
                await message.reply("Недостаточно прав!", disable_mentions=1)
                return True

            user = 0
            arg = 2
            if message.reply_message:
                user = message.reply_message.from_id
                arg = 1
            elif message.fwd_messages and message.fwd_messages[0].from_id > 0:
                user = message.fwd_messages[0].from_id
                arg = 1
            elif len(arguments) >= 2 and await getID(arguments[1]): user = await getID(arguments[1])
            else:
                await message.reply("Укажите пользователя!", disable_mentions=1)
                return True

            if await get_role(user_id, chat_id) <= await get_role(user, chat_id): return await message.reply(
                "Вы не можете взаимодействовать с данным пользователем!")

            if len(arguments) < arg+1: return await message.reply("Укажите аргументы!")

            if not arguments[arg].isdigit(): return await message.reply("Укажите число!")

            level = int(arguments[arg])
            if level >= await get_role(user_id, chat_id): return await message.reply("Вы не можете выдать роль, которая выше вашей!")

            if level < 0: return await message.reply("Нельзя выдать такую роль!")

            if await get_server_id(chat_id) == 0: return await message.reply("Сначала укажите сервер, используя /server")

            chats = await get_server_chats(chat_id)

            for i in chats:
                await roleG(user, i, level)

            moder_name = await get_user_name(user_id, chat_id)
            target_name = await get_user_name(user, chat_id)
            
            moder_link = f"[id{user_id}|{moder_name}]"
            target_link = f"[id{user}|{target_name}]"
            
            server_id = await get_server_id(chat_id)
            await message.answer(f"{moder_link} выдал(-а) уровень прав {level} в беседах сервера «{server_id}» пользователю {target_link}", disable_mentions=1)
            try: u_info = await bot.api.users.get(user_ids=user); u_name = f"[id{user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{user}"
            await log_action(user_id, chat_id, f"Выдал уровень прав {level} пользователю {u_name} на сервере.")

        # --- CLAN COMMANDS ---
        if command in ['clan', 'клан']:
            ud = await get_user_data(user_id)
            my_clan_id = ud.get('clan_id', 0)
            clan_id = my_clan_id
            
            if len(arguments) > 1:
                action = arguments[1].lower()
                
                if action == "create" and len(arguments) > 2:
                    if clan_id: return await message.reply("❌ Вы уже в клане!")
                    
                    cost = 100000
                    
                    if not await subtract_balance(user_id, cost):
                        return await message.reply(f"❌ Недостаточно средств! Стоимость создания: {cost} монет.")
                    
                    name = await get_string(arguments, 2)
                    if len(name) > 30: return await message.reply("Слишком длинное название!")

                    keyboard = (
                        Keyboard(inline=True)
                        .add(Callback("🔓 Открытый", {"command": "clan_create_finish", "name": name, "type": "open", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE)
                        .add(Callback("🔒 Закрытый", {"command": "clan_create_finish", "name": name, "type": "closed", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.NEGATIVE)
                    )
                    
                    return await message.reply(f"Вы создаете клан «{name}». Выберите тип доступа:\n"
                                               f"🔓 Открытый — любой может вступить\n"
                                               f"🔒 Закрытый — только по приглашению", keyboard=keyboard)
                
                if action == "invite":
                    if not clan_id: return await message.reply("❌ Вы не в клане!")
                    if not await check_clan_perms(user_id, 2): return await message.reply("❌ Недостаточно прав!")
                    target = int
                    if message.reply_message: target = message.reply_message.from_id
                    elif len(arguments) > 2 and await getID(arguments[2]): target = await getID(arguments[2])
                    else: return await message.reply("Укажите пользователя!")
                    
                    if target == user_id: return await message.reply("Нельзя пригласить себя!")
                    if target < 0: return await message.reply("Нельзя пригласить сообщество!")

                    max_m = await get_clan_max_members(clan_id)
                    sql.execute("SELECT count(*) FROM user_data WHERE clan_id = ?", (clan_id,))
                    if sql.fetchone()[0] >= max_m:
                        return await message.reply(f"❌ В вашем клане нет свободных мест! (Лимит: {max_m})")

                    t_ud = await get_user_data(target)
                    if t_ud.get('clan_id'): return await message.reply("❌ Пользователь уже в клане!")
                    
                    sql.execute("SELECT name FROM clans WHERE clan_id = ?", (clan_id,))
                    clan_name = sql.fetchone()[0]
                    
                    target_link = await get_user_link(target)
                    keyboard = (
                        Keyboard(inline=True)
                        .add(Callback("✅ Вступить", {"command": "clan_accept_invite", "clan_id": clan_id, "target": target, "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE)
                        .add(Callback("❌ Отказаться", {"command": "delete_msg", "target": target}), color=KeyboardButtonColor.NEGATIVE)
                    )
                    return await message.answer(f"{target_link}, вас приглашают в клан «{clan_name}»!", keyboard=keyboard, disable_mentions=1)
                
                if action == "kick":
                    if not clan_id: return await message.reply("❌ Вы не в клане!")
                    if not await check_clan_perms(user_id, 4): return await message.reply("❌ Недостаточно прав!")
                    target = int
                    if message.reply_message: target = message.reply_message.from_id
                    elif len(arguments) > 2 and await getID(arguments[2]): target = await getID(arguments[2])
                    else: return await message.reply("Укажите пользователя!")
                    
                    t_ud = await get_user_data(target)
                    if t_ud.get('clan_id') != clan_id: return await message.reply("❌ Пользователь не в вашем клане!")
                    
                    await update_user_data(target, 'clan_id', 0)
                    await update_user_data(target, 'clan_rank', 'Участник')
                    await save_clan_to_json(clan_id)
                    target_link = await get_user_link(target)
                    return await message.answer(f"👢 Пользователь {target_link} исключен из клана.")

                if action == "leave":
                    if not clan_id: return await message.reply("❌ Вы не в клане!")
                    await update_user_data(user_id, 'clan_id', 0)
                    await update_user_data(user_id, 'clan_rank', 'Участник')
                    await save_clan_to_json(clan_id)
                    return await message.reply("🚪 Вы покинули клан.")

                if action == "attack":
                    if not clan_id: return await message.reply("❌ Вы не в клане!")
                    
                    last_attack = ud.get('last_clan_attack', 0)
                    cooldown = 60 # 1 minute
                    if time.time() - last_attack < cooldown:
                        rem = int(cooldown - (time.time() - last_attack))
                        return await message.answer(f"⏳ Атака готова через {rem} сек.")

                    war = await check_war_status(clan_id, chat_id)
                    if not war:
                        return await message.reply("Ваш клан не в войне!")

                    is_attacker = (war[1] == clan_id)
                    points = random.randint(2, 5) # Attack gives more points than mine
                    col = "attacker_score" if is_attacker else "defender_score"
                    sql.execute(f"UPDATE clan_wars SET {col} = {col} + ? WHERE war_id = ?", (points, war[0]))
                    
                    # Quest progress for war points
                    quest_msg = ""
                    q_completed_war, qr_mats_war, qr_exp_war = await check_daily_quest_progress(clan_id, points, "war_points")
                    if q_completed_war:
                        quest_msg = f" | ✅ Квест: +{qr_mats_war:,} м. +{qr_exp_war:,} exp".replace(",",".")

                    await update_user_data(user_id, 'last_clan_attack', int(time.time()))
                    database.commit()
                    
                    # Notification in chat
                    sql.execute("SELECT attacker_score, defender_score, attacker_id, defender_id FROM clan_wars WHERE war_id = ?", (war[0],))
                    w_data = sql.fetchone()
                    if w_data:
                        att_score, def_score, att_id, def_id = w_data
                        
                        sql.execute("SELECT name FROM clans WHERE clan_id = ?", (att_id,)); att_name = sql.fetchone()[0]
                        sql.execute("SELECT name FROM clans WHERE clan_id = ?", (def_id,)); def_name = sql.fetchone()[0]
                        user_name = await get_user_name(user_id, chat_id)
                        sql.execute("SELECT name FROM clans WHERE clan_id = ?", (clan_id,)); my_clan_name = sql.fetchone()[0]
                        
                        phrases = ["наносит сокрушительный удар", "прорывает оборону", "укрепляет позиции", "совершает тактический маневр", "ведет клан к победе"]
                        action_text = random.choice(phrases)
                        notif = (f"⚔ Внимание! Боец [id{user_id}|{user_name}] из клана «{my_clan_name}» {action_text}!\n"
                                 f"💥 +{points} к счету войны.{quest_msg}\n"
                                 f"📊 Текущий счёт: {att_name} {att_score} : {def_score} {def_name}")
                        return await message.answer(notif, disable_mentions=1)
                    return True

                if action in ["bizinfo", "biz"]:
                    if not clan_id: return await message.reply("❌ Вы не в клане!")
                    
                    # Support both /clan biz info ID and /clan bizinfo ID
                    id_arg_idx = 2
                    if action == "biz" and len(arguments) > 2 and arguments[2].lower() == "info":
                        id_arg_idx = 3
                    
                    if len(arguments) <= id_arg_idx: return await message.reply("📝 Использование: /clan bizinfo [ID]")
                    
                    try: bid = int(arguments[id_arg_idx])
                    except: return await message.reply("ID должен быть числом!")
                    
                    sql.execute("SELECT name, price, owner_id, type, repair_until, clan_owner_id FROM businesses WHERE id = ?", (bid,)); res = sql.fetchone()
                    if not res: return await message.reply("❌ Бизнес не найден!")
                    name, price, owner, b_type, repair, c_owner = res
                    if c_owner != clan_id: return await message.reply("❌ Этот бизнес не принадлежит вашему клану!")
                    
                    status = "✅ Работает" if time.time() > repair else f"🛠 Ремонт ({int((repair-time.time())/60)} мин)"
                    msg = f"🏢 Клановый бизнес: {name} (ID: {bid})\n🏷 Цена покупки: {price:,}$\n⚙ Статус: {status}".replace(",", ".")
                    kb = Keyboard(inline=True)
                    if await check_clan_perms(user_id, 4):
                        kb.add(Callback("⚙ Управление", {"command": "biz_manage", "biz_id": bid, "chatId": chat_id}), color=KeyboardButtonColor.PRIMARY)
                    return await message.reply(msg, keyboard=kb)

                if action == "bizwar":
                    if not clan_id: return await message.reply("❌ Вы не в клане!")
                    if not await check_clan_perms(user_id, 4): return await message.reply("❌ Недостаточно прав!")
                    
                    # Проверка лимита бизнесов у атакующего клана
                    sql.execute("SELECT COUNT(*) FROM businesses WHERE clan_owner_id = ?", (clan_id,))
                    if sql.fetchone()[0] >= 1:
                        return await message.reply("❌ Ваш клан не может захватить еще один бизнес (лимит 1)!")
                    
                    # Online Check
                    my_online = await get_clan_online_count(clan_id)
                    if my_online < 10:
                        return await message.reply(f"❌ Ваш клан слишком слаб! Для захвата бизнеса нужно минимум 10 участников онлайн (сейчас: {my_online}).")
                    
                    if len(arguments) < 3: return await message.reply("📝 Использование: /clan bizwar [ID бизнеса]")
                    try: target_bid = int(arguments[2])
                    except: return await message.reply("ID бизнеса должен быть числом!")

                    sql.execute("SELECT name, clan_owner_id FROM businesses WHERE id = ?", (target_bid,))
                    biz_res = sql.fetchone()
                    if not biz_res: return await message.reply("❌ Бизнес не найден!")
                    if biz_res[1] == 0: return await message.reply("❌ Этот бизнес не принадлежит ни одному клану. Его можно просто купить!")
                    if biz_res[1] == clan_id: return await message.reply("❌ Это ваш бизнес!")

                    target_id = biz_res[1]
                    target_online = await get_clan_online_count(target_id)
                    if target_online < 10:
                        return await message.reply(f"❌ Нельзя напасть на этот клан! У них меньше 10 участников онлайн.")

                    sql.execute("SELECT money, mats, exp FROM clans WHERE clan_id = ?", (clan_id,))
                    res = sql.fetchone()
                    if res[0] < CLAN_WAR_COST_MONEY or res[1] < CLAN_WAR_COST_MATS or res[2] < CLAN_WAR_COST_EXP:
                        return await message.reply(f"❌ Недостаточно ресурсов!")

                    sql.execute("SELECT war_id FROM clan_wars WHERE (attacker_id IN (?,?) OR defender_id IN (?,?)) AND status IN ('active', 'pending')", (clan_id, target_id, clan_id, target_id))
                    if sql.fetchone(): return await message.reply("Один из кланов уже воюет!")

                    sql.execute("INSERT INTO clan_wars (attacker_id, defender_id, status, start_time, target_biz_id) VALUES (?, ?, 'pending', ?, ?)", (clan_id, target_id, int(time.time()), target_bid))
                    war_id = sql.lastrowid
                    database.commit()

                    sql.execute("SELECT name FROM clans WHERE clan_id = ?", (clan_id,))
                    att_name = sql.fetchone()[0]
                    
                    kb = Keyboard(inline=True).add(Callback("⚔ Принять вызов", {"command": "clan_war_accept", "war_id": war_id, "chatId": chat_id, "public": True}), color=KeyboardButtonColor.NEGATIVE)
                    return await message.reply(f"⚔ Клан «{att_name}» объявляет войну клану-владельцу за бизнес «{biz_res[0]}»! (ID: {war_id})\n"
                                             f"Победитель получит контроль над предприятием!", keyboard=kb)

                if action == "war":
                    if not clan_id: return await message.reply("❌ Вы не в клане!")
                    if not await check_clan_perms(user_id, 4): return await message.reply("❌ Недостаточно прав!")

                    # Online Check
                    my_online = await get_clan_online_count(clan_id)
                    if my_online < 2: # Changed from 10 to 2 for clan war
                        return await message.reply(f"❌ Ваш клан слишком слаб! Для объявления войны нужно минимум 10 участников онлайн (сейчас: {my_online}).")

                    # Cooldown check for initiating a war (2 hours)
                    war_cooldown_seconds = 30 * 60 # 30 minutes
                    if time.time() - user_clan_war_cooldown.get(user_id, 0) < war_cooldown_seconds:
                        rem_seconds = int(war_cooldown_seconds - (time.time() - user_clan_war_cooldown[user_id]))
                        minutes, seconds = divmod(rem_seconds, 60)
                        return await message.reply(f"⏳ Вы сможете объявить новую войну через {minutes}м {seconds}с.")

                    if len(arguments) < 3: return await message.reply("Укажите ID клана для войны! (/clan war [ID])")
                    
                    try: 
                        target_id = int(arguments[2])
                    except ValueError: 
                        return await message.reply("Неверный ID клана! ID должен быть числом.")
                    
                    if target_id == clan_id: return await message.reply("Нельзя воевать с собой!")
                    sql.execute("SELECT name FROM clans WHERE clan_id = ?", (target_id,))
                    t_name = sql.fetchone()
                    if not t_name: return await message.reply("Клан не найден!")
                    
                    target_online = await get_clan_online_count(target_id)
                    if target_online < 10:
                        return await message.reply(f"❌ Нельзя напасть на клан «{t_name[0]}»! У них меньше 10 участников онлайн.")
                    
                    # Проверка ресурсов
                    sql.execute("SELECT money, mats, exp FROM clans WHERE clan_id = ?", (clan_id,))
                    res = sql.fetchone()
                    if res[0] < CLAN_WAR_COST_MONEY or res[1] < CLAN_WAR_COST_MATS or res[2] < CLAN_WAR_COST_EXP:
                        return await message.reply(f"❌ Недостаточно ресурсов для войны!\nНужно: {CLAN_WAR_COST_MONEY} монет, {CLAN_WAR_COST_MATS} материалов, {CLAN_WAR_COST_EXP} exp.")

                    # Check if already in war
                    sql.execute("SELECT war_id FROM clan_wars WHERE (attacker_id IN (?,?) OR defender_id IN (?,?)) AND status IN ('active', 'pending')", (clan_id, target_id, clan_id, target_id))
                    if sql.fetchone(): return await message.reply("Один из кланов уже находится в войне или имеет активный вызов!")
                    
                    sql.execute("INSERT INTO clan_wars (attacker_id, defender_id, status, start_time) VALUES (?, ?, 'pending', ?)", (clan_id, target_id, int(time.time())))
                    war_id = sql.lastrowid
                    database.commit()

                    # Update cooldown after successful initiation
                    user_clan_war_cooldown[user_id] = time.time()

                    sql.execute("SELECT name FROM clans WHERE clan_id = ?", (clan_id,))
                    att_name = sql.fetchone()[0]

                    # Notify target clan owner
                    sql.execute("SELECT owner_id FROM clans WHERE clan_id = ?", (target_id,))
                    target_owner = sql.fetchone()
                    if target_owner:
                        try:
                            await bot.api.messages.send(
                                user_id=target_owner[0], 
                                message=f"⚔ Внимание! Клан «{att_name}» объявил вам войну! (ID: {war_id})\nЗайдите в /clan чтобы принять или отклонить вызов.", 
                                random_id=0
                            )
                        except: pass
                    
                    keyboard = (
                        Keyboard(inline=True)
                        .add(Callback("⚔ Принять вызов", {"command": "clan_war_accept", "war_id": war_id, "chatId": chat_id, "user": user_id, "public": True}), color=KeyboardButtonColor.NEGATIVE)
                    )
                    
                    return await message.reply(f"⚔ Клан «{att_name}» бросает вызов клану «{t_name[0]}»! (ID: {war_id})\n"
                                               f"Ставка: {CLAN_WAR_COST_MONEY}$ | {CLAN_WAR_COST_MATS} мат. | {CLAN_WAR_COST_EXP} exp\n"
                                               f"Жмите кнопку, чтобы принять бой (доступно Заместителям и выше).", keyboard=keyboard)

                if action == "setrank":
                    if not clan_id: return await message.reply("❌ Вы не в клане!")
                    if not await check_clan_perms(user_id, 5): return await message.reply("❌ Только лидер может менять названия рангов!")
                    
                    if len(arguments) < 4: return await message.reply("📝 Использование: /clan setrank [0-5] [название]")
                    
                    try: rank_id = int(arguments[2])
                    except: return await message.reply("ID ранга должен быть числом 0-5!")
                    
                    if rank_id < 0 or rank_id > 5: return await message.reply("ID ранга должен быть от 0 до 5!")
                    
                    # Map visual ID (0-5) to storage column ID (rX)
                    # 0->0, 1->4, 2->1, 3->5, 4->2, 5->3
                    storage_map = {0: 0, 1: 4, 2: 1, 3: 5, 4: 2, 5: 3}
                    storage_id = storage_map[rank_id]
                    
                    rank_name = await get_string(arguments, 3)
                    if len(rank_name) > 20: return await message.reply("Название слишком длинное!")
                    
                    sql.execute(f"UPDATE clans SET r{storage_id}_name = ? WHERE clan_id = ?", (rank_name, clan_id))
                    database.commit()
                    await save_clan_to_json(clan_id)
                    return await message.reply(f"✅ Ранг {rank_id} переименован в «{rank_name}»!")

                if action in ["giverank", "выдатьранг"]:
                    if not clan_id: return await message.reply("❌ Вы не в клане!")
                    if not await check_clan_perms(user_id, 4): return await message.reply("❌ Только Заместитель и выше могут выдавать ранги!")
                    
                    target = int
                    if message.reply_message: target = message.reply_message.from_id
                    elif len(arguments) > 2 and await getID(arguments[2]): target = await getID(arguments[2])
                    else: return await message.reply("Укажите пользователя!")
                    
                    if target == user_id: return await message.reply("Нельзя менять ранг себе!")
                    
                    t_ud = await get_user_data(target)
                    if t_ud.get('clan_id') != clan_id: return await message.reply("❌ Пользователь не в вашем клане!")
                    
                    if len(arguments) < 4 and not message.reply_message: return await message.reply("Укажите ранг (1-Боец, 2-Мод, 3-Стар, 4-Зам)!")
                    if len(arguments) < 3 and message.reply_message: return await message.reply("Укажите ранг (1-Боец, 2-Мод, 3-Стар, 4-Зам)!")
                    
                    try: 
                        arg_idx = 3 if not message.reply_message else 2
                        rank_lvl = int(arguments[arg_idx])
                    except: return await message.reply("Ранг должен быть числом!")
                    
                    if rank_lvl not in [1, 2, 3, 4]: return await message.reply("Доступные ранги: 1-Боец, 2-Мод, 3-Стар, 4-Зам. Для разжалования используйте /clan demote.")
                    
                    if not await check_clan_perms(user_id, rank_lvl + 1): return await message.reply("Вы не можете выдать ранг выше или равный своему!")
                    
                    rank_names_map = {1: "Боец", 2: "Модератор", 3: "Старейшина", 4: "Заместитель"}
                    rank_str = rank_names_map[rank_lvl]
                    
                    await update_user_data(target, 'clan_rank', rank_str)
                    custom_rank = await get_custom_rank(clan_id, rank_str)
                    await save_clan_to_json(clan_id)
                    target_link = await get_user_link(target)
                    return await message.reply(f"✅ Пользователю {target_link} выдан ранг «{custom_rank}»!")

                if action in ["promote", "повысить"]:
                    if not clan_id: return await message.reply("❌ Вы не в клане!")
                    
                    target = int
                    if message.reply_message: target = message.reply_message.from_id
                    elif len(arguments) > 2 and await getID(arguments[2]): target = await getID(arguments[2])
                    else: return await message.reply("Укажите пользователя!")
                    
                    if target == user_id: return await message.reply("Нельзя повысить себя!")
                    
                    t_ud = await get_user_data(target)
                    if t_ud.get('clan_id') != clan_id: return await message.reply("❌ Пользователь не в вашем клане!")
                    
                    ranks_map = {"Лидер": 5, "Заместитель": 4, "Старейшина": 3, "Модератор": 2, "Боец": 1, "Участник": 0}
                    ranks_list = ["Участник", "Боец", "Модератор", "Старейшина", "Заместитель", "Лидер"]
                    
                    current_rank = t_ud.get('clan_rank', 'Участник')
                    current_lvl = ranks_map.get(current_rank, 0)
                    
                    if current_lvl >= 4:
                        return await message.reply("❌ Максимальный ранг для повышения (Заместитель)!")
                        
                    if not await check_clan_perms(user_id, current_lvl + 2):
                        return await message.reply("❌ Недостаточно прав для повышения этого пользователя!")
                        
                    new_rank = ranks_list[current_lvl + 1]
                    await update_user_data(target, 'clan_rank', new_rank)
                    custom_rank = await get_custom_rank(clan_id, new_rank)
                    await save_clan_to_json(clan_id)
                    target_link = await get_user_link(target)
                    return await message.reply(f"✅ Пользователь {target_link} повышен до звания «{custom_rank}»!")

                if action in ["degrade", "понизить"]:
                    if not clan_id: return await message.reply("❌ Вы не в клане!")
                    
                    target = int
                    if message.reply_message: target = message.reply_message.from_id
                    elif len(arguments) > 2 and await getID(arguments[2]): target = await getID(arguments[2])
                    else: return await message.reply("Укажите пользователя!")
                    
                    if target == user_id: return await message.reply("Нельзя понизить себя!")
                    
                    t_ud = await get_user_data(target)
                    if t_ud.get('clan_id') != clan_id: return await message.reply("❌ Пользователь не в вашем клане!")
                    
                    ranks_map = {"Лидер": 5, "Заместитель": 4, "Старейшина": 3, "Модератор": 2, "Боец": 1, "Участник": 0}
                    ranks_list = ["Участник", "Боец", "Модератор", "Старейшина", "Заместитель", "Лидер"]
                    
                    current_rank = t_ud.get('clan_rank', 'Участник')
                    current_lvl = ranks_map.get(current_rank, 0)
                    
                    if current_lvl <= 0:
                        return await message.reply("❌ Минимальный ранг!")
                        
                    if not await check_clan_perms(user_id, current_lvl + 1):
                        return await message.reply("❌ Недостаточно прав для понижения этого пользователя!")
                        
                    new_rank = ranks_list[current_lvl - 1]
                    await update_user_data(target, 'clan_rank', new_rank)
                    custom_rank = await get_custom_rank(clan_id, new_rank)
                    await save_clan_to_json(clan_id)
                    target_link = await get_user_link(target)
                    return await message.reply(f"✅ Пользователь {target_link} понижен до звания «{custom_rank}»!")

                if action in ["demote", "разжаловать"]:
                    if not clan_id: return await message.reply("❌ Вы не в клане!")
                    if not await check_clan_perms(user_id, 4): return await message.reply("❌ Недостаточно прав!")
                    
                    target = int
                    if message.reply_message: target = message.reply_message.from_id
                    elif len(arguments) > 2 and await getID(arguments[2]): target = await getID(arguments[2])
                    else: return await message.reply("Укажите пользователя!")
                    
                    if target == user_id: return await message.reply("Нельзя разжаловать себя!")
                    
                    t_ud = await get_user_data(target)
                    if t_ud.get('clan_id') != clan_id: return await message.reply("❌ Пользователь не в вашем клане!")
                    
                    await update_user_data(target, 'clan_rank', 'Участник')
                    await save_clan_to_json(clan_id)
                    target_link = await get_user_link(target)
                    return await message.reply(f"✅ Пользователь {target_link} разжалован до участника.")

                if action in ["deposit", "депозит"]:
                    if not clan_id: return await message.reply("❌ Вы не в клане!")
                    if len(arguments) < 3: return await message.reply("📝 Использование: /clan deposit [сумма]")
                    
                    try: amount = int(arguments[2])
                    except: return await message.reply("Укажите корректную сумму!")
                    
                    if amount <= 0: return await message.reply("Сумма должна быть больше 0!")
                    
                    sql.execute("SELECT treasury FROM clans WHERE clan_id = ?", (clan_id,))
                    res = sql.fetchone()
                    if res and not res[0] and not await check_clan_perms(user_id, 5):
                        return await message.reply("❌ Казна клана закрыта для пополнения!")

                    if not await subtract_balance(user_id, amount):
                        return await message.reply("❌ Недостаточно средств на балансе!")
                    
                    sql.execute("UPDATE clans SET money = money + ? WHERE clan_id = ?", (amount, clan_id))
                    
                    # Quest progress
                    quest_msg = ""
                    q_completed, qr_mats, qr_exp = await check_daily_quest_progress(clan_id, amount, "deposit")
                    if q_completed:
                        quest_msg = f"\n✅ Квест выполнен! Награда: +{qr_mats:,} мат. +{qr_exp:,} exp".replace(",",".")

                    database.commit()
                    await save_clan_to_json(clan_id)
                    return await message.reply(f"✅ Вы успешно внесли {amount:,} монет в казну клана!{quest_msg}".replace(",", "."))

                if action in ["withdraw", "снять"]:
                    if not clan_id: return await message.reply("❌ Вы не в клане!")
                    if not await check_clan_perms(user_id, 4): return await message.reply("❌ Только лидер и заместитель может снимать деньги!")
                    
                    if len(arguments) < 3: return await message.reply("📝 Использование: /clan withdraw [сумма]")
                    try: amount = int(arguments[2])
                    except: return await message.reply("Укажите корректную сумму!")
                    
                    if amount <= 0: return await message.reply("Сумма должна быть больше 0!")
                    
                    sql.execute("SELECT money FROM clans WHERE clan_id = ?", (clan_id,))
                    c_money = sql.fetchone()[0]
                    if c_money < amount: return await message.reply("❌ В казне недостаточно средств!")
                    
                    sql.execute("UPDATE clans SET money = money - ? WHERE clan_id = ?", (amount, clan_id))
                    database.commit()
                    await add_balance(user_id, amount)
                    await save_clan_to_json(clan_id)
                    return await message.reply(f"✅ Вы сняли {amount} монет из казны клана!")

                if action in ["setname", "сменитьимя"]:
                    if not clan_id: return await message.reply("❌ Вы не в клане!")
                    if not await check_clan_perms(user_id, 5): return await message.reply("❌ Только лидер может менять название!")
                    
                    cost = 500000
                    
                    if not await subtract_balance(user_id, cost):
                         return await message.reply(f"❌ Недостаточно средств! Стоимость: {cost} монет.")
                    
                    new_name = await get_string(arguments, 2)
                    if not new_name: return await message.reply("Укажите новое название!")
                    
                    sql.execute("UPDATE clans SET name = ? WHERE clan_id = ?", (new_name, clan_id))
                    database.commit()
                    await save_clan_to_json(clan_id)
                    return await message.reply(f"✅ Название клана изменено на «{new_name}»! Списано {cost} монет.")

                if action in ["settag", "сменитьтег"]:
                    if not clan_id: return await message.reply("❌ Вы не в клане!")
                    if not await check_clan_perms(user_id, 5): return await message.reply("❌ Только лидер может менять тег!")
                    
                    if len(arguments) < 3: return await message.reply("📝 Использование: /clan settag [TEG]")
                    
                    cost = 500000
                    
                    if not await subtract_balance(user_id, cost):
                         return await message.reply(f"❌ Недостаточно средств! Стоимость: {cost} монет.")
                    
                    new_tag = arguments[2].upper()
                    if len(new_tag) > 5: return await message.reply("Тег не может быть длиннее 5 символов!")
                    
                    sql.execute("SELECT 1 FROM clans WHERE tag = ?", (new_tag,))
                    if sql.fetchone(): return await message.reply(f"❌ Тег [{new_tag}] уже занят!")

                    sql.execute("UPDATE clans SET tag = ? WHERE clan_id = ?", (new_tag, clan_id))
                    database.commit()
                    await save_clan_to_json(clan_id)
                    return await message.reply(f"✅ Тег клана изменен на [{new_tag}]! Списано {cost} монет.")

                if action in ["delete", "удалить"]:
                    if not clan_id: return await message.reply("❌ Вы не в клане!")
                    if not await check_clan_perms(user_id, 5): return await message.reply("❌ Только Лидер может удалить клан!")
                    
                    if len(arguments) < 3 or arguments[2].lower() != "подтверждаю":
                        return await message.reply("⚠ Вы уверены, что хотите удалить клан? Это действие необратимо!\n"
                                                   "Все ресурсы, опыт и история войн будут потеряны.\n\n"
                                                   "Для удаления напишите: /clan удалить подтверждаю")
                    
                    sql.execute("DELETE FROM clans WHERE clan_id = ?", (clan_id,))
                    sql.execute("UPDATE user_data SET clan_id = 0, clan_rank = 'Участник' WHERE clan_id = ?", (clan_id,))
                    sql.execute("DELETE FROM clan_wars WHERE attacker_id = ? OR defender_id = ?", (clan_id, clan_id))
                    sql.execute("DELETE FROM clan_quests WHERE clan_id = ?", (clan_id,))
                    sql.execute("UPDATE businesses SET clan_owner_id = 0 WHERE clan_owner_id = ?", (clan_id,))
                    database.commit()
                    await save_clan_to_json(clan_id)
                    await log_action(user_id, chat_id, f"Удалил свой клан через команду (ID: {clan_id}).")
                    return await message.reply("🗑 Клан успешно удален.")
                
                if action == "donate":
                    if not clan_id: return await message.reply("❌ Вы не в клане!")
                    if len(arguments) < 3: return await message.reply("📝 Использование: /clan donate [ID бизнеса]")
                    
                    try: bid = int(arguments[2])
                    except: return await message.reply("ID бизнеса должен быть числом!")
                    
                    cursor = database.cursor()
                    cursor.execute("SELECT owner_id, name FROM businesses WHERE id = ?", (bid,))
                    res = cursor.fetchone()
                    if not res: return await message.reply("❌ Бизнес не найден!")
                    if res[0] != user_id: return await message.reply("❌ Это не ваш бизнес!")
                    
                    kb = Keyboard(inline=True).add(Callback("✅ Подтвердить передачу", {"command": "clan_donate_confirm", "biz_id": bid, "clan_id": clan_id, "user": user_id, "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE).row().add(Callback("❌ Отмена", {"command": "delete_msg", "user": user_id}), color=KeyboardButtonColor.NEGATIVE)
                    return await message.reply(f"⚠ Вы уверены, что хотите передать бизнес «{res[1]}» (ID: {bid}) клану?\nЭто действие необратимо.", keyboard=kb)

                if action == "reclaimbiz":
                    if not clan_id: return await message.reply("❌ Вы не в клане!")
                    if not await check_clan_perms(user_id, 5): return await message.reply("❌ Только лидер клана может забирать бизнес клана!")
                    if len(arguments) < 3: return await message.reply("📝 Использование: /clan reclaimbiz [ID бизнеса]")
                    
                    try: bid = int(arguments[2])
                    except: return await message.reply("ID бизнеса должен быть числом!")
                    
                    sql.execute("SELECT name, clan_owner_id FROM businesses WHERE id = ?", (bid,))
                    res = sql.fetchone()
                    if not res: return await message.reply("❌ Бизнес не найден!")
                    if res[1] != clan_id: return await message.reply("❌ Этот бизнес не принадлежит вашему клану!")
                    
                    # Проверка наличия свободного слота для бизнеса у лидера
                    sql.execute("SELECT COUNT(*) FROM businesses WHERE owner_id = ?", (user_id,))
                    owned_count = sql.fetchone()[0]
                    ud_leader = await get_user_data(user_id)
                    max_slots = 2 + ud_leader.get('biz_slots', 0)
                    if owned_count >= max_slots:
                        return await message.reply(f"❌ У вас нет свободных слотов для бизнеса ({owned_count}/{max_slots})!")
                    
                    sql.execute("UPDATE businesses SET owner_id = ?, clan_owner_id = 0 WHERE id = ?", (user_id, bid))
                    database.commit()
                    await save_clan_to_json(clan_id)
                    return await message.reply(f"✅ Бизнес «{res[0]}» (ID: {bid}) забран лидером и теперь принадлежит вам.")

                if action == "join":
                    if clan_id: return await message.reply("❌ Вы уже в клане!")
                    if len(arguments) < 3: return await message.reply("📝 Использование: /clan join [ID клана]")
                    
                    try: target_clan_id = int(arguments[2])
                    except: return await message.reply("ID клана должен быть числом!")
                    
                    sql.execute("SELECT name, type FROM clans WHERE clan_id = ?", (target_clan_id,))
                    c_info = sql.fetchone()
                    
                    if not c_info: return await message.reply("❌ Клан не найден!")
                    
                    if c_info[1] != 'open':
                        return await message.reply("🔒 Этот клан закрытый! Вступление только по приглашению.")
                    
                    max_m = await get_clan_max_members(target_clan_id)
                    sql.execute("SELECT count(*) FROM user_data WHERE clan_id = ?", (target_clan_id,))
                    if sql.fetchone()[0] >= max_m:
                        return await message.reply(f"❌ В клане «{c_info[0]}» нет свободных мест! (Лимит: {max_m})")

                    await update_user_data(user_id, 'clan_id', target_clan_id)
                    await save_clan_to_json(target_clan_id)
                    return await message.reply(f"✅ Вы вступили в клан «{c_info[0]}»!")

                if action == "salary":
                    if not clan_id: return await message.reply("❌ Вы не в клане!")
                    
                    ud = await get_user_data(user_id)
                    last_salary_claim = ud.get('last_clan_salary', 0)
                    cooldown = 86400 # 24 hours
                    
                    if time.time() - last_salary_claim < cooldown:
                        rem_seconds = int(cooldown - (time.time() - last_salary_claim))
                        hours, remainder = divmod(rem_seconds, 3600)
                        minutes, seconds = divmod(remainder, 60)
                        return await message.reply(f"⏳ Вы сможете получить зарплату через {hours}ч {minutes}м.")

                    # Get user's rank and the corresponding salary
                    user_rank_str = ud.get('clan_rank', 'Участник')
                    ranks_map = {"Лидер": 3, "Заместитель": 2, "Старейшина": 5, "Модератор": 1, "Боец": 4, "Участник": 0}
                    rank_num = ranks_map.get(user_rank_str, 0)
                    
                    sql.execute(f"SELECT r{rank_num}_salary, money FROM clans WHERE clan_id = ?", (clan_id,))
                    res = sql.fetchone()
                    if not res: return await message.reply("❌ Ошибка получения данных клана.")
                    
                    salary_amount, clan_money = res
                    
                    if salary_amount <= 0:
                        return await message.reply("💸 Для вашей должности не установлена зарплата.")
                        
                    if clan_money < salary_amount:
                        return await message.reply("❌ В казне клана недостаточно средств для выплаты зарплаты!")
                        
                    # Perform transaction
                    sql.execute("UPDATE clans SET money = money - ? WHERE clan_id = ?", (salary_amount, clan_id))
                    await add_balance(user_id, salary_amount)
                    await update_user_data(user_id, 'last_clan_salary', int(time.time()))
                    database.commit()
                    await save_clan_to_json(clan_id)
                    
                    return await message.reply(f"✅ Вы получили зарплату в размере {salary_amount:,}$!".replace(",", "."))

                if action == "setsalary":
                    if not clan_id: return await message.reply("❌ Вы не в клане!")
                    if not await check_clan_perms(user_id, 5): return await message.reply("❌ Только лидер может устанавливать зарплату!")
                    
                    if len(arguments) < 4:
                        sql.execute("SELECT r0_name, r0_salary, r1_name, r1_salary, r2_name, r2_salary, r3_name, r3_salary, r4_name, r4_salary, r5_name, r5_salary FROM clans WHERE clan_id = ?", (clan_id,))
                        salaries = sql.fetchone()
                        msg = "💰 Текущие зарплаты в клане (в сутки):\n"
                        msg += f"0. {salaries[0] or 'Участник'}: {salaries[1]:,}$\n"
                        msg += f"1. {salaries[8] or 'Боец'}: {salaries[9]:,}$\n"
                        msg += f"2. {salaries[2] or 'Модератор'}: {salaries[3]:,}$\n"
                        msg += f"3. {salaries[10] or 'Старейшина'}: {salaries[11]:,}$\n"
                        msg += f"4. {salaries[4] or 'Заместитель'}: {salaries[5]:,}$\n"
                        msg += f"5. {salaries[6] or 'Лидер'}: {salaries[7]:,}$\n"
                        msg += "\n📝 Для установки: /clan setsalary [ID ранга] [сумма]"
                        return await message.reply(msg.replace(",", "."))

                    try:
                        rank_id = int(arguments[2])
                        amount = int(arguments[3])
                    except:
                        return await message.reply("📝 Использование: /clan setsalary [ID ранга] [сумма]")
                        
                    if rank_id < 0 or rank_id > 5:
                        return await message.reply("ID ранга должен быть от 0 до 5.")
                        
                    if amount < 0:
                        return await message.reply("Сумма не может быть отрицательной.")
                        
                    storage_map = {0: 0, 1: 4, 2: 1, 3: 5, 4: 2, 5: 3}
                    storage_id = storage_map.get(rank_id)
                    if storage_id is None:
                        return await message.reply("Неверный ID ранга.")
                        
                    sql.execute(f"UPDATE clans SET r{storage_id}_salary = ? WHERE clan_id = ?", (amount, clan_id))
                    database.commit()
                    
                    return await message.reply(f"✅ Зарплата для ранга {rank_id} установлена в размере {amount:,}$".replace(",", "."))

                if action in ["passleader", "передать"]:
                    if not clan_id: return await message.reply("❌ Вы не в клане!")
                    if not await check_clan_perms(user_id, 5): return await message.reply("❌ Только Лидер может передать управление кланом!")
                    
                    target = None
                    if message.reply_message: target = message.reply_message.from_id
                    elif len(arguments) > 2 and await getID(arguments[2]): target = await getID(arguments[2])
                    else: return await message.reply("📝 Использование: /clan passleader [@упоминание/ссылка]")
                    
                    if target == user_id: return await message.reply("Вы и так лидер!")
                    
                    t_ud = await get_user_data(target)
                    if t_ud.get('clan_id') != clan_id: return await message.reply("❌ Этот пользователь не состоит в вашем клане!")
                    
                    target_name = await get_user_name(target, chat_id)
                    keyboard = (
                        Keyboard(inline=True)
                        .add(Callback("✅ Подтвердить", {"command": "clan_pass_confirm", "new_leader": target, "user": user_id, "target": user_id, "chatId": chat_id}), color=KeyboardButtonColor.POSITIVE)
                        .add(Callback("❌ Отмена", {"command": "delete_msg"}), color=KeyboardButtonColor.NEGATIVE)
                    )
                    return await message.reply(f"⚠ Вы уверены, что хотите передать лидерство клана пользователю [id{target}|{target_name}]?\n\nВы станете Заместителем, а он — Лидером.", keyboard=keyboard)

            if not clan_id: return await message.answer("🏰 Вы не в клане!\nСоздать: /clan create [название]")
            
            text, kb = await get_clan_menu_data(user_id, chat_id)
            
            return await message.answer(text, keyboard=kb, disable_mentions=1)

        if command in ['topclan', 'топклан', 'клантоп']:
            sql.execute("SELECT name, level, exp, tag, wins FROM clans ORDER BY level DESC, wins DESC, exp DESC LIMIT 10")
            top_clans = sql.fetchall()
            
            if not top_clans:
                await message.reply("Кланов пока нет!", disable_mentions=1)
                return True
                
            msg = "🏆 Топ 10 кланов:\n\n"
            for idx, (name, level, exp, tag, wins) in enumerate(top_clans, 1):
                tag_str = f"[{tag}]" if tag else ""
                msg += f"{idx}. {tag_str} {name} — {level} ур. | ⚔ {wins} | ✨ {exp}\n"
            
            await message.reply(msg, disable_mentions=1)

        if command in ['delclan', 'deleteclan', 'удалитьклан']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("❌ Только разработчик может удалять кланы!", disable_mentions=1)
                return True
            
            if len(arguments) < 2:
                await message.reply("📝 Использование: /delclan [ID клана]", disable_mentions=1)
                return True
            
            try:
                target_clan_id = int(arguments[1])
            except:
                await message.reply("ID клана должен быть числом!", disable_mentions=1)
                return True
            
            sql.execute("SELECT name FROM clans WHERE clan_id = ?", (target_clan_id,))
            clan = sql.fetchone()
            if not clan:
                await message.reply("❌ Клан не найден!", disable_mentions=1)
                return True
            
            sql.execute("DELETE FROM clans WHERE clan_id = ?", (target_clan_id,))
            sql.execute("UPDATE user_data SET clan_id = 0, clan_rank = 'Участник' WHERE clan_id = ?", (target_clan_id,))
            sql.execute("DELETE FROM clan_wars WHERE attacker_id = ? OR defender_id = ?", (target_clan_id, target_clan_id))
            sql.execute("DELETE FROM clan_quests WHERE clan_id = ?", (target_clan_id,))
            sql.execute("UPDATE businesses SET clan_owner_id = 0 WHERE clan_owner_id = ?", (target_clan_id,))
            database.commit()
            
            await save_clan_to_json(target_clan_id)
            
            await message.reply(f"✅ Клан «{clan[0]}» (ID: {target_clan_id}) успешно удален.", disable_mentions=1)
            await log_action(user_id, chat_id, f"Удалил клан «{clan[0]}» (ID: {target_clan_id}).")

        if command in ['stats_eco', 'экостат']:
            if await get_role(user_id, chat_id) < 6:
                await message.reply("Недостаточно прав!")
                return True
            
            econ = load_economy()
            total_balance = 0
            total_bank = 0
            
            for u in econ['users'].values():
                total_balance += u.get('balance', 0)
                total_bank += u.get('bank', 0)
                
            commissions = econ.get('server_stats', {}).get('collected_commissions', 0)
            
            await message.reply(
                f"📊 Экономика сервера:\n"
                f"💰 Всего денег на руках: {total_balance:,}$\n"
                f"🏦 Всего денег в банках: {total_bank:,}$\n"
                f"📉 Собрано комиссий: {commissions:,}$".replace(",", "."),
                disable_mentions=1
            )

        if command in ['check_sync', 'статуссвязи', 'link_status']:
            if await get_role(user_id, chat_id) < 5:
                 await message.reply("Недостаточно прав!")
                 return True
            
            # Находим конфиг для текущего чата (если команда вызвана в журнале)
            current_config = None
            for conf in SHEETS_CONFIG:
                if conf["sheet_chat_id"] == chat_id:
                    current_config = conf
                    break
            
            if not current_config:
                await message.reply(f"❌ Этот чат (ID: {chat_id}) не привязан ни к одной Google Таблице в настройках.")
                return True
                
            await message.reply(f"⏳ Проверяю связь ников таблицы '{current_config['name']}' с базой бота...")
            
            try:
                loop = asyncio.get_running_loop()
                def _check_nicks(conf_name, worksheet_name):
                    sh = gs_client.open(conf_name)
                    ws = sh.worksheet(worksheet_name)
                    return ws.get_all_values()
                
                rows = await loop.run_in_executor(None, _check_nicks, current_config['name'], current_config['worksheet_name'])
                found_count = 0
                missing_list = []

                for i in range(1, len(rows)):
                    row = rows[i]
                    if not row or len(row) <= 0: continue
                    
                    nick_sheet = row[current_config["vk_id_col"]].strip() # Берем ник из настроенного столбца
                    
                    # Список строк-заголовков и служебных слов, которые нужно игнорировать
                    ignored_values = [
                        'вакантно', 'главная модерация discord', 'старшие модераторы discord', 
                        'модераторы discord', 'младшие модераторы discord'
                    ]
                    
                    if not nick_sheet or nick_sheet.lower() in ignored_values: continue

                    sql.execute(f"SELECT user_id FROM nicks_{chat_id} WHERE nick = ?", (nick_sheet,))
                    if sql.fetchone():
                        found_count += 1
                    else:
                        missing_list.append(nick_sheet)
                
                msg = (f"📊 Результат проверки связи (по чату {chat_id}):\n"
                       f"✅ Связано (найдено в боте): {found_count}\n"
                       f"❌ Не найдено в боте: {len(missing_list)}\n\n")
                
                if missing_list:
                    msg += "⚠️ Этим никам из таблицы нужно прописать /setnick в Журнале:\n" + ", ".join(missing_list[:15])
                    if len(missing_list) > 15: msg += f"\n...и еще {len(missing_list)-15}"
                else:
                    msg += "🎉 Все ники из таблицы найдены в боте!"

                await message.reply(msg)
            
            except Exception as e:
                await message.reply(f"❌ Ошибка при проверке: {e}")


        if command in ['lastlogs', 'logs', 'логи']:
            if await get_role(user_id, chat_id) < 5:
                await message.reply("Недостаточно прав!")
                return True
            
            try:
                history = await bot.api.messages.get_history(peer_id=LOG_PEER_ID, count=10)
                msgs = []
                for item in history.items:
                    if item.text:
                        msgs.append(f"🔹 {item.text}")
                
                response = "📜 Последние 10 логов:\n\n" + "\n\n".join(msgs)
                await message.reply(response, disable_mentions=1)
            except Exception as e:
                await message.reply(f"❌ Ошибка получения логов: {e}")

        if command in ['listchats', 'списокчатов']:
            if await get_global_role(user_id) < 5:
                await message.reply("Недостаточно прав!")
                return True
            
            sql.execute("SELECT chat_id, chat_type, owner_id FROM chats")
            chats = sql.fetchall()
            
            msg = f"📋 Список чатов ({len(chats)}):\n"
            for c in chats:
                cid, ctype, oid = c # Original query
                
                # Fetch peer_id from the database for the chat_id
                sql.execute("SELECT peer_id FROM chats WHERE chat_id = ?", (cid,))
                peer_id_val = sql.fetchone()[0]

                title = "Неизвестно"
                try:
                    conv_info = await bot.api.messages.get_conversations_by_id(peer_ids=peer_id_val)
                    if conv_info.items:
                        title = conv_info.items[0].chat_settings.title
                except Exception:
                    pass # Bot might not have access or chat deleted

                owner_name = await get_user_name(oid, cid)
                chunk = f"ID: {cid} | Название: {title} | Тип: {ctype} | Владелец: [id{oid}|{owner_name}]\n"
                if len(msg) + len(chunk) > 4000:
                    await message.reply(msg, disable_mentions=1)
                    msg = ""
                msg += chunk
            
            if msg: await message.reply(msg, disable_mentions=1)

        if command in ['infochat', 'инфочат']:
            if await get_global_role(user_id) < 3:
                await message.reply("Недостаточно прав!")
                return True
            
            if len(arguments) < 2:
                await message.reply("📝 Использование: /infochat [ID чата]")
                return True
            
            try: target_cid = int(arguments[1])
            except: return await message.reply("ID чата должен быть числом!")
            
            sql.execute("SELECT peer_id, owner_id, chat_type, in_pull FROM chats WHERE chat_id = ?", (target_cid,))
            res = sql.fetchone()
            if not res: return await message.reply("❌ Чат не найден в базе данных.")
            
            t_peer, t_owner, t_type, t_server = res
            
            try:
                # Получаем информацию о беседе и участниках
                conv_members = await bot.api.messages.get_conversation_members(peer_id=t_peer, fields=["first_name", "last_name"])
                conv_info = await bot.api.messages.get_conversations_by_id(peer_ids=t_peer)
                
                chat_settings = conv_info.items[0].chat_settings
                title = chat_settings.title
                count = chat_settings.members_count
                
                # Участники
                profiles = {p.id: f"{p.first_name} {p.last_name}" for p in conv_members.profiles}
                groups = {g.id: g.name for g in conv_members.groups}
                
                items = conv_members.items
                admins = []
                member_list = []
                
                # Собираем админов и участников
                for item in items:
                    mid = item.member_id
                    name = profiles.get(mid, groups.get(abs(mid), f"id{mid}"))
                    
                    if item.is_admin or item.is_owner:
                        link = f"@id{mid}" if mid > 0 else f"@club{abs(mid)}"
                        if mid > 0: link = f"[id{mid}|{name}]"
                        else: link = f"[club{abs(mid)}|{name}]"
                        admins.append(link)
                        
                    if len(member_list) < 50:
                        member_list.append(name)
                        
                remaining = count - 50 if count > 50 else 0
                
                # Ссылка на чат
                try:
                    link_obj = await bot.api.messages.get_invite_link(peer_id=t_peer)
                    link = link_obj.link
                except:
                    link = "Недоступна (нет прав админа)"
                
                status_chat = "🟢 Чат активен"
                
            except Exception as e:
                title = "Недоступно (бот кикнут или нет прав)"
                count = "?"
                member_list = []
                remaining = 0
                admins = []
                link = "Недоступна"
                status_chat = f"🔴 Ошибка доступа: {e}"

            owner_name = await get_user_name(t_owner, target_cid)
            if not owner_name or owner_name == "Пользователь":
                 try:
                     u_o = await bot.api.users.get(user_ids=t_owner)
                     owner_name = f"{u_o[0].first_name} {u_o[0].last_name}"
                 except: pass

            type_map = {
                'def': 'Обычная', 'pl': 'Игровая (PL)', 'ext': 'Расширенная',
                'hel': 'Хелперская', 'ld': 'Лидерская', 'adm': 'Админская',
                'mod': 'Модерская', 'tex': 'Техническая', 'test': 'Тестерская',
                'med': 'Медиа', 'ruk': 'Руководство', 'users': 'Пользовательская'
            }
            type_str = type_map.get(t_type, t_type)

            msg = f"📋 Информация о беседе №{target_cid}\n\n"
            msg += f"👑 Владелец беседы: [id{t_owner}|{owner_name}]\n"
            msg += f"💬 Название чата: {title}\n"
            msg += f"👥 Количество участников: {count}\n"
            
            if member_list:
                msg += "📃 Из них (первые 50):\n"
                for idx, m_name in enumerate(member_list, 1):
                    msg += f"{idx}. {m_name}\n"
                if remaining > 0:
                    msg += f"... и ещё {remaining} участников\n"
            
            msg += f"\n🛡 Количество администраторов: {len(admins)}\n"
            if admins:
                msg += "📃 Из них:\n"
                for idx, adm in enumerate(admins, 1):
                    msg += f"{idx}. {adm}\n"
            
            msg += f"\n🔗 Ссылка на чат: {link}\n"
            msg += f"⚙️ Статус беседы: {status_chat}\n"
            msg += f"💎 Статус чата: {type_str}"
            
            await message.reply(msg, disable_mentions=1)

        if command in ['infoid', 'groups']:
            if await get_global_role(user_id) < 4:
                await message.reply("Недостаточно прав!")
                return True
            
            target = 0
            if len(arguments) > 1 and await getID(arguments[1]):
                target = await getID(arguments[1])
            else:
                return await message.reply("📝 Использование: /infoid [пользователь]")

            sql.execute("SELECT chat_id, chat_type FROM chats WHERE owner_id = ?", (target,))
            chats = sql.fetchall()
            
            if not chats:
                target_link = await get_user_link(target)
                return await message.reply(f"У пользователя {target_link} нет бесед.")

            target_link = await get_user_link(target)
            msg = f"📋 Беседы пользователя {target_link} ({len(chats)}):\n"
            for cid, ctype in chats:
                msg += f"ID: {cid} | {ctype}\n"
            
            await message.reply(msg, disable_mentions=1)

        if command in ['addblack', 'чс']:
            if await get_global_role(user_id) < 3:
                return await message.reply("Недостаточно прав!")
            
            target = 0
            arg_offset = 1
            if message.reply_message:
                target = message.reply_message.from_id
            elif len(arguments) > 1:
                resolved_id = await getID(arguments[1])
                if resolved_id:
                    target = resolved_id
                    arg_offset = 2
            
            if not target:
                return await message.reply("Укажите пользователя!")

            reason = await get_string(arguments, arg_offset) or "Не указана"
            date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            sql.execute("INSERT OR REPLACE INTO blacklist (user_id, reason, moder_id, date) VALUES (?, ?, ?, ?)", (target, reason, user_id, date_str))
            database.commit()
            target_link = await get_user_link(target)
            await message.reply(f"✅ Пользователь {target_link} добавлен в ЧС бота.\nПричина: {reason}")
            await log_action(user_id, chat_id, f"Добавил в ЧС бота пользователя {target}. Причина: {reason}")

        if command in ['unblack', 'унчс']:
            if await get_global_role(user_id) < 3:
                return await message.reply("Недостаточно прав!")
            
            target = 0
            if message.reply_message:
                target = message.reply_message.from_id
            elif len(arguments) > 1:
                resolved_id = await getID(arguments[1])
                if resolved_id:
                    target = resolved_id
            
            if not target:
                return await message.reply("Укажите пользователя!")
            
            sql.execute("DELETE FROM blacklist WHERE user_id = ?", (target,))
            database.commit()
            target_link = await get_user_link(target)
            await message.reply(f"✅ Пользователь {target_link} удален из ЧС бота.")
            await log_action(user_id, chat_id, f"Убрал из ЧС бота пользователя {target}.")

        if command in ['blacklist', 'чслист']:
            if await get_global_role(user_id) < 1:
                return await message.reply("Недостаточно прав!")
            
            sql.execute("SELECT user_id, reason, date FROM blacklist")
            bl = sql.fetchall()
            if not bl: return await message.reply("ЧС бота пуст.")
            
            msg = "📋 Черный список бота:\n"
            for u, r, d in bl:
                name = await get_user_name(u, chat_id)
                msg += f"• [id{u}|{name}]: {r} ({d})\n"
            await message.reply(msg, disable_mentions=1)

        if command in ['banid']:
            if await get_global_role(user_id) < 3:
                return await message.reply("Недостаточно прав!")
            
            try: target_chat = int(arguments[1])
            except: return await message.reply("Укажите ID чата цифрами!")
            
            reason = await get_string(arguments, 2) or "Не указана"
            date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            sql.execute("INSERT OR REPLACE INTO banned_chats (chat_id, reason, moder_id, date) VALUES (?, ?, ?, ?)", (target_chat, reason, user_id, date_str))
            database.commit()
            await message.reply(f"✅ Чат {target_chat} заблокирован.\nПричина: {reason}")
            await log_action(user_id, chat_id, f"Заблокировал чат {target_chat}. Причина: {reason}")

        if command in ['unbanid']:
            if await get_global_role(user_id) < 3:
                return await message.reply("Недостаточно прав!")
            
            try: target_chat = int(arguments[1])
            except: return await message.reply("Укажите ID чата цифрами!")
            
            sql.execute("DELETE FROM banned_chats WHERE chat_id = ?", (target_chat,))
            database.commit()
            await message.reply(f"✅ Чат {target_chat} разблокирован.")
            await log_action(user_id, chat_id, f"Разблокировал чат {target_chat}.")

        if command in ['banschats']:
            if await get_global_role(user_id) < 1:
                return await message.reply("Недостаточно прав!")
            
            sql.execute("SELECT chat_id, reason FROM banned_chats")
            bc = sql.fetchall()
            if not bc: return await message.reply("Список заблокированных чатов пуст.")
            
            msg = "📋 Заблокированные чаты:\n"
            for c, r in bc:
                msg += f"• ID {c}: {r}\n"
            await message.reply(msg)

        if command in ['banreport']:
            if await get_global_role(user_id) < 4:
                return await message.reply("Недостаточно прав!")
            
            target = 0
            arg_offset = 1
            if message.reply_message:
                target = message.reply_message.from_id
                arg_offset = 1 # Duration/reason starts from arguments[1]
            elif len(arguments) > 1:
                resolved_id = await getID(arguments[1])
                if resolved_id:
                    target = resolved_id
                    arg_offset = 2
            
            if not target:
                return await message.reply("Укажите пользователя!")

            reason = await get_string(arguments, arg_offset) or "Не указана"
            date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            sql.execute("INSERT OR REPLACE INTO report_bans (user_id, reason, moder_id, date) VALUES (?, ?, ?, ?)", (target, reason, user_id, date_str))
            database.commit()
            target_link = await get_user_link(target)
            await message.reply(f"✅ Пользователю {target_link} запрещено писать в репорт.")

        if command in ['unbanreport']:
            if await get_global_role(user_id) < 4:
                return await message.reply("Недостаточно прав!")
            
            target = 0
            if message.reply_message:
                target = message.reply_message.from_id
            elif len(arguments) > 1:
                resolved_id = await getID(arguments[1])
                if resolved_id:
                    target = resolved_id
            
            if not target:
                return await message.reply("Укажите пользователя!")
            
            sql.execute("DELETE FROM report_bans WHERE user_id = ?", (target,))
            database.commit()
            target_link = await get_user_link(target)
            await message.reply(f"✅ Пользователю {target_link} разрешено писать в репорт.")

        if command in ['gbanlist']:
            if await get_global_role(user_id) < 1:
                return await message.reply("Недостаточно прав!")
            
            sql.execute("SELECT user_id, ban_type, reason FROM global_bans LIMIT 20")
            gb = sql.fetchall()
            if not gb: return await message.reply("Глобальных банов нет.")
            
            msg = "🚫 Глобальные баны (последние 20):\n"
            for u, t, r in gb:
                t_str = "ALL" if t == 'all' else "PL"
                name = await get_user_name(u, chat_id)
                msg += f"• [id{u}|{name}] [{t_str}]: {r}\n"
            await message.reply(msg, disable_mentions=1)

        if command in ['notoplist']:
            if await get_global_role(user_id) < 2:
                return await message.reply("Недостаточно прав!")
            
            sql.execute("SELECT user_id FROM notop_users")
            nt = sql.fetchall()
            if not nt: return await message.reply("Список скрытых из топа пуст.")
            
            msg = "👻 Скрытые из топа:\n"
            for (u,) in nt:
                name = await get_user_name(u, chat_id)
                msg += f"• [id{u}|{name}]\n"
            await message.reply(msg, disable_mentions=1)

        # Shortcuts for Global Roles
        if command in ['addzamdirector', 'addoszamdirector', 'adddirector', 'adddeveloper']:
            if await get_global_role(user_id) < 5: # Only Dev can use these shortcuts based on hierarchy logic, or Level 2 can add level 1?
                # Let's stick to strict hierarchy: Level 5+ can add any.
                if await get_priority(user_id, chat_id) < 200:
                    return await message.reply("Недостаточно прав!")
            
            target = 0
            if message.reply_message:
                target = message.reply_message.from_id
            elif len(arguments) > 1:
                resolved_id = await getID(arguments[1])
                if resolved_id:
                    target = resolved_id
            
            if not target:
                return await message.reply("Укажите пользователя!")
                return True

            lvl = 0
            role_str = ""
            if command == 'addzamdirector': lvl = 2; role_str = "Зам. руководителя"
            elif command == 'addoszamdirector': lvl = 3; role_str = "Осн. зам. руководителя"
            elif command == 'adddirector': lvl = 4; role_str = "Специальный руководитель"
            elif command == 'adddeveloper': lvl = 5; role_str = "Разработчик"

            sql.execute("INSERT OR REPLACE INTO global_managers (user_id, level) VALUES (?, ?)", (target, lvl))
            database.commit()
            target_link = await get_user_link(target)
            await message.reply(f"✅ Пользователю {target_link} выдана роль «{role_str}» (G-Level {lvl}).")
            await log_action(user_id, chat_id, f"Выдал глобальную роль {role_str} пользователю {target}.")

        if command in ['resetwork', 'сбросработы', 'обнулитьработу']:
            if not await check_perm(user_id, chat_id, command, 6):
                await message.reply("❌ Только разработчик может сбрасывать КД работы!", disable_mentions=1)
                return True
            
            target_user = 0
            if message.reply_message:
                target_user = message.reply_message.from_id
            elif len(arguments) > 1:
                resolved_id = await getID(arguments[1])
                if resolved_id:
                    target_user = resolved_id
            
            if not target_user:
                await message.reply("📝 Использование: /resetwork [пользователь]", disable_mentions=1)
                return True
            
            economy = load_economy()
            str_uid = str(target_user)
            if str_uid not in economy['users']:
                await get_balance(target_user)
                economy = load_economy()
            
            economy['users'][str_uid]['last_job_time'] = 0
            save_economy(economy)
            
            try: u_info = await bot.api.users.get(user_ids=target_user); u_name = f"[id{target_user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{target_user}"
            
            await message.reply(f"✅ Кулдаун работы сброшен для {u_name}!", disable_mentions=1)
            await log_action(user_id, chat_id, f"Сбросил КД работы пользователю {u_name}.")

        if command in ['resetwarcd', 'сбросвойн', 'resetwar']:
            if not await check_perm(user_id, chat_id, command, 6):
                await message.reply("❌ Только разработчик может сбрасывать КД войн!", disable_mentions=1)
                return True
            
            target_user = 0
            if message.reply_message:
                target_user = message.reply_message.from_id
            elif len(arguments) > 1:
                resolved_id = await getID(arguments[1])
                if resolved_id:
                    target_user = resolved_id
            
            if not target_user:
                await message.reply("📝 Использование: /resetwarcd [пользователь]", disable_mentions=1)
                return True
            
            user_clan_war_cooldown.pop(target_user, None)
            
            try: u_info = await bot.api.users.get(user_ids=target_user); u_name = f"[id{target_user}|{u_info[0].first_name} {u_info[0].last_name}]"
            except: u_name = f"@id{target_user}"
            
            await message.reply(f"✅ Кулдаун войн сброшен для {u_name}!", disable_mentions=1)
            await log_action(user_id, chat_id, f"Сбросил КД войн пользователю {u_name}.")
            return True

        if command in ['clearchat']:
            if await get_global_role(user_id) < 3:
                return await message.reply("Недостаточно прав!")
            
            # Confirm
            if len(arguments) < 2 or arguments[1].lower() != "confirm":
                return await message.reply("⚠ Это действие удалит ВСЕ данные этого чата (ники, варны, баны, настройки)!\nДля подтверждения напишите: /clearchat confirm")

            c_id = chat_id
            try:
                sql.execute(f"DELETE FROM chats WHERE chat_id = ?", (c_id,))
                sql.execute(f"DROP TABLE IF EXISTS permissions_{c_id}")
                sql.execute(f"DROP TABLE IF EXISTS nicks_{c_id}")
                sql.execute(f"DROP TABLE IF EXISTS warns_{c_id}")
                sql.execute(f"DROP TABLE IF EXISTS bans_{c_id}")
                sql.execute(f"DROP TABLE IF EXISTS mutes_{c_id}")
                sql.execute(f"DROP TABLE IF EXISTS messages_{c_id}")
                database.commit()
                await message.reply("♻ Чат был полностью сброшен. Напишите /start для повторной активации.")
                await bot.api.messages.remove_chat_user(c_id, user_id) # Optional kick to force re-add or just leave
            except Exception as e:
                await message.reply(f"Ошибка сброса: {e}")

        if command in ['dbprune', 'чисткабд']:
            if await get_global_role(user_id) < 5:
                return True
            
            if len(arguments) < 2:
                await message.reply("📝 Использование: /dbprune [кол-во дней]\nУдалит все сообщения из базы старше указанного периода.")
                return True
            
            try:
                days = int(arguments[1])
                if days < 0: raise ValueError
            except:
                await message.reply("❌ Укажите корректное количество дней (число от 0)!")
                return True

            await message.reply(f"⏳ Начинаю чистку сообщений старше {days} дн. и оптимизацию базы...")
            
            # Вызываем существующую функцию чистки с принудительным сжатием (vacuum)
            deleted = await prune_old_messages(days_to_keep=days, run_vacuum=True)
            
            # Форматируем число для вывода
            deleted_fmt = f"{deleted:,}".replace(",", ".")
            
            await message.reply(f"✅ Чистка завершена!\n🗑 Удалено старых сообщений: {deleted_fmt}\n🗜 База данных оптимизирована.")
            await log_action(user_id, chat_id, f"Выполнил ручную чистку БД (сообщения старше {days} дн). Удалено: {deleted}")
            return True

        if command in ['wipe_economy', 'вайп']:
            if user_id != CREATOR_ID:
                return await message.reply("⛔ Команда доступна только разработчику.")
            
            if len(arguments) < 2 or arguments[1] != "confirm":
                return await message.reply("⚠️ ВНИМАНИЕ! Это действие полностью обнулит экономику, кланы, бизнесы и списки сообщений!\n\nДля подтверждения напишите: /вайп confirm")

            # 1. Clear economy.json
            economy = load_economy()
            economy['users'] = {}
            if 'server_stats' in economy:
                economy['server_stats']['collected_commissions'] = 0
            save_economy(economy)

            # 2. Clear clans.json
            if os.path.exists("clans.json"):
                with open("clans.json", "w", encoding="utf-8") as f:
                    json.dump({}, f)

            # 3. Wipe SQLite tables
            tables_to_clear = [
                "clans", "clan_wars", "clan_quests", "clan_bosses", 
                "clan_boss_attacks", "clan_alliances", "clan_ally_requests", 
                "biz_offers", "pets", "charity", "notop_users",
                "support_tickets", "applications"
            ]
            for table in tables_to_clear:
                try: sql.execute(f"DELETE FROM {table}")
                except: pass
            
            # 4. Reset user_data progression
            sql.execute("""UPDATE user_data SET 
                points = 0, clan_id = 0, clan_rank = 'Участник', 
                last_clan_mine = 0, last_clan_attack = 0, 
                last_clan_salary = 0, clan_mats_mined = 0, 
                clan_war_points = 0, inventory = '[]', 
                biz_slots = 0, has_notop = 0, custom_prefix = NULL""")
            
            # 5. Restore businesses automatically
            sql.execute("DELETE FROM businesses")
            default_businesses = [
                ("Курский вокзал", 10500000, 525000, "station"),
                ("Павелецкий вокзал", 10500000, 525000, "station"),
                ("Белорусский вокзал", 10500000, 525000, "station"),
                ("Рижский вокзал", 10500000, 525000, "station"),
                ("Казанский вокзал", 10500000, 525000, "station"),
                ("Шаурмичная у шахида🌯", 100000, 5000, "default"),
                ("Магазин 24/7🛒", 250000, 12500, "default"),
                ("Кофейня «У Палыча»☕", 500000, 25000, "default"),
                ("АЗС «ГазМяс»⛽", 1500000, 75000, "default"),
                ("ТЦ «Мармелад»🛍️", 5000000, 250000, "default"),
                ("IT-Компания «Skynet»💻", 25000000, 1250000, "default"),
                ("Нефтяная вышка ⛽", 100000000, 5000000, "default"),
                ("Космодром 🚀", 500000000, 25000000, "default"),
                ("Остров «Bora-Bora» 🏝️", 1000000000, 50000000, "default"),
                ("Гипермаркет «Лента»🛒", 15000000, 750000, "default"),
                ("Отель «Mariott»🏨", 35000000, 1750000, "default"),
                ("Ночной клуб «Status»🕺", 8000000, 400000, "default")
            ]
            sql.executemany("INSERT INTO businesses (name, price, profit_per_hour, type) VALUES (?, ?, ?, ?)", default_businesses)

            database.commit()
            await message.reply("✅ Глобальный вайп завершен. Все данные обнулены, список бизнесов восстановлен автоматически.")
            return True

    else:
        if user_id < 1: return True
        if await check_chat(chat_id):
            if await get_mute(user_id, chat_id):
                try: await bot.api.messages.delete(group_id=message.group_id, peer_id=message.peer_id, delete_for_all=True, cmids=message.conversation_message_id)
                except: pass
                return True
            elif await check_quit(chat_id) and await get_role(user_id, chat_id) < 1:
                try: await bot.api.messages.delete(group_id=message.group_id, peer_id=message.peer_id, delete_for_all=True, cmids=message.conversation_message_id)
                except: pass
                return True # Останавливаем обработку, если тишина включена для обычных пользователей
            else:
                # Link Mute Check for all non-command messages (but skip if it's a command)
                # Check if this is NOT a command (check if first char is not a bot identifier)
                is_command = False
                if arguments and len(arguments[0]) > 0:
                    first_char = arguments[0][0]
                    is_command = first_char in ['/', '!', '+']
                
                if not is_command and await get_role(user_id, chat_id) < 1 and await get_link_filter(chat_id):
                    if re.search(r"(https?://|www\.|vk\.me|t\.me|[a-zA-Z0-9-]+\.[a-z]{2,})", message.text, re.IGNORECASE):
                        await mute(user_id, chat_id, 'Бот', 'Отправка ссылок', 30)
                        keyboard = (Keyboard(inline=True).add(Callback("Снять мут", {"command": "unmute", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE))
                        await message.reply(f"@id{user_id} (Пользователь) получил(-а) мут на 30 минут за отправку ссылки!", disable_mentions=1, keyboard=keyboard)
                        try: await bot.api.messages.delete(group_id=message.group_id, peer_id=message.peer_id, delete_for_all=True, cmids=message.conversation_message_id)
                        except: pass
                        return True

                # Banwords Check (if filter is enabled)
                if await get_filter(chat_id):
                    # 1. Проверяем локальный фильтр чата
                    lbw = await get_local_banwords(chat_id)
                    for word, duration in lbw:
                        if word in message.text.lower() and await get_role(user_id, chat_id) < 1:
                            await mute(user_id, chat_id, 'Бот', f'Запрещенное слово: {word}', duration)
                            keyboard = (
                                Keyboard(inline=True)
                                .add(Callback("Снять мут", {"command": "unmute", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE)
                            )
                            await message.reply(f"@id{user_id} (Пользователь) получил(-а) мут на {duration} минут за написание запрещенного слова!", disable_mentions=1, keyboard=keyboard)
                            try: await bot.api.messages.delete(group_id=message.group_id, peer_id=message.peer_id,delete_for_all=True, cmids=message.conversation_message_id)
                            except: pass
                            return True

                    # 2. Проверяем глобальный фильтр, если локальный не сработал
                    bws = await get_banwords()
                    for i in bws:
                        if i in message.text.lower() and await get_role(user_id, chat_id) < 1:
                            await mute(user_id, chat_id, 'Бот', 'Глобальное запрещенное слово', 30)
                            keyboard = (
                                Keyboard(inline=True)
                                .add(Callback("Снять мут", {"command": "unmute", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE)
                            )
                            await message.reply(f"@id{user_id} (Пользователь) получил(-а) мут на 30 минут за написание запрещенного слова!", disable_mentions=1, keyboard=keyboard)
                            try: await bot.api.messages.delete(group_id=message.group_id, peer_id=message.peer_id,delete_for_all=True, cmids=message.conversation_message_id)
                            except: pass
                            return True

            await new_message(user_id, message.message_id, message.conversation_message_id, chat_id, message.text)
            if await get_spam(user_id, chat_id) and await get_role(user_id, chat_id) < 1 and not await get_mute(user_id, chat_id):
                keyboard = (
                    Keyboard(inline=True)
                    .add(Callback("Снять мут", {"command": "unmute", "chatId": chat_id, "user": user_id}), color=KeyboardButtonColor.POSITIVE)
                )
                await message.answer(f"@id{user_id} (Пользователь) получил(-а) мут на 30 минут за спам!", disable_mentions=1, keyboard=keyboard)
                await mute(user_id, chat_id, 'Bot', 'Спам', 30)
                try:await bot.api.messages.delete(group_id=message.group_id, peer_id=message.peer_id,delete_for_all=True, cmids=message.conversation_message_id)
                except: pass

    return True


 #в config.json токен бота


@bot.on.private_message()
async def private_handler(message: Message):
    # ... (код функции остается прежним до конца)
    # ВАЖНО: Удалите весь дублирующийся код, который начинается ПОСЛЕ этой функции в вашем файле!
    # Ваш файл main.py должен заканчиваться запуском бота:
    user_id = message.from_id
    text = message.text.strip()

    # Проверка техработ в ЛС
    sql.execute("SELECT value FROM global_settings WHERE key = 'maintenance_mode'")
    g_maint = sql.fetchone()
    if g_maint and g_maint[0] == "1" and await get_global_role(user_id) < 5:
        return

    if user_id in user_states:
        state = user_states[user_id]
        if state.get("action") == "add_bug_comment":
            bid = state["bid"]
            sql.execute("UPDATE support_tickets SET tester_comment = ? WHERE id = ?", (text, bid))
            database.commit()
            del user_states[user_id]
            await message.answer(f"✅ Комментарий к багу #{bid} успешно добавлен!")
            return

        if state.get("action") == "bug_reply":
            target_user = state["target_user"]
            bid = state["bid"]
            try:
                await bot.api.messages.send(
                    user_id=target_user,
                    message=f"✉️ Тестировщик ответил на ваш баг-репорт #{bid}:\n\n{text}",
                    random_id=0
                )
                await message.answer(f"✅ Ответ на баг-репорт #{bid} успешно отправлен пользователю!")
            except Exception as e:
                await message.answer(f"❌ Не удалось отправить сообщение автору: {e}")
            
            del user_states[user_id]
            return

        if state.get("action") == "reply_ticket":
            target_user = state["target_user"]
            tid = state["tid"]
            new_status = "закрыт"
            
            if text.lower() == "отмена":
                del user_states[user_id]
                await message.answer("❌ Ответ отменен.")
                return

            sql.execute("SELECT type FROM support_tickets WHERE id = ?", (tid,))
            ticket_type = sql.fetchone()[0]
            type_text = "предложение" if ticket_type == "offer" else "жалобу"
            verb = "одобрил" if new_status == "approved" else "отклонил" if new_status == "rejected" else "ответил на"

            try:
                await bot.api.messages.send(
                    user_id=target_user,
                    message=f"🔔 Администратор {verb} вашу {type_text} #{tid}:\n\n{text}",
                    random_id=0
                )
                sql.execute("UPDATE support_tickets SET status = ? WHERE id = ?", (new_status, tid))
                database.commit()
                
                target_link = await get_user_link(target_user)

                # Удаление кнопок из исходного сообщения уведомления
                source_peer = state.get("source_peer")
                source_cmid = state.get("source_cmid")
                if source_peer and source_cmid:
                    try:
                        resp = await bot.api.messages.get_by_conversation_message_id(peer_id=source_peer, conversation_message_ids=[source_cmid])
                        if resp.items:
                            updated_text = resp.items[0].text.replace("⏳ Статус: Ожидание", "✅ Статус: Закрыт").replace("⏳ Статус: На рассмотрении", "✅ Статус: Закрыт")
                            await bot.api.messages.edit(peer_id=source_peer, conversation_message_id=source_cmid, message=updated_text, keyboard=None)
                    except: pass

                await message.answer(f"✅ Ответ на тикет #{tid} отправлен пользователю {target_link}!")
            except Exception as e:
                await message.answer(f"❌ Не удалось отправить сообщение: {e}")
            
            del user_states[user_id]
            return

    if text.lower() in ["/сразраб", "/setdev"]:
        if user_id == 460366734:
            sql.execute("INSERT OR REPLACE INTO global_managers (user_id, level) VALUES (?, ?)", (user_id, 5))
            database.commit()
            await message.answer("✅ Вы назначены глобальным разработчиком!")
            return

    # Если это просто сообщение — отправляем приветствие с кнопками
    
    await message.answer("👋 Привет! По всем вопросам пишите администрации через команду /offer в беседе или ЛС.\n\n"
                         "🔗 Наша группа: https://vk.com/cherepovets.teams.manager\n"
                         "💬 Наш чат: https://vk.me/join/TDDJPEAW8d7yeZlTlN8IsNNh3q5CHcmGE3Q=",
                         disable_mentions=1)

# --- АВТОПОСТИНГ ИЗ ГРУППЫ ---
@bot.on.raw_event(GroupEventType.WALL_POST_NEW, dataclass=GroupTypes.WallPostNew)
async def wall_post_handler(event: GroupTypes.WallPostNew):
    post = event.object

    # Игнорируем "предложку"
    if post.post_type == "suggest":
        return

    # Формируем вложение (ссылку на пост)
    attachment = f"wall{post.owner_id}_{post.id}"
    
    text = "🔥 Новый пост в нашей группе!\nСкорее переходи и читай 👇"
    
    # Получаем все чаты из базы данных
    sql.execute("SELECT peer_id FROM chats WHERE autopost = 1")
    chats = sql.fetchall()
    
    sent_count = 0
    for (peer_id,) in chats:
        try:
            await bot.api.messages.send(
                peer_id=peer_id,
                message=text,
                attachment=attachment,
                random_id=0
            )
            sent_count += 1
            await asyncio.sleep(0.1) # Небольшая задержка, чтобы ВК не блокировал за спам
        except Exception:
            pass # Игнорируем ошибки (например, если бота кикнули из чата)
    
    print(f"[AUTOPOST] Новый пост отправлен в {sent_count} чатов.")

@bot.error_handler.register_error_handler(Exception)
async def global_error_handler(e: Exception):
    """Перехватывает ошибки и отправляет отчет создателю в ЛС."""
    # Не шлем отчеты при ошибках сети или флуде, чтобы не создавать рекурсивных падений
    if any(err in str(e).lower() for err in ["flood control", "name resolution", "temporary failure", "connector", "connection", "internal server error"]):
        return
    if isinstance(e, VKAPIError) and e.code == 10: return

    # Получаем полный стек ошибки (traceback)
    tb = traceback.format_exc()
    error_msg = f"⚠️ КРИТИЧЕСКАЯ ОШИБКА БОТА!\n\n❌ Ошибка: {str(e)}\n\n📜 Traceback:\n{tb}"
    
    # Обрезаем, если сообщение слишком длинное для ВК (макс 4096 символов)
    if len(error_msg) > 4000:
        error_msg = error_msg[:4000] + "..."
    
    # Сохраняем в память для тестеров
    LAST_ERRORS.append(f"[{datetime.now().strftime('%H:%M:%S')}] {str(e)}")

    try:
        await bot.api.messages.send(user_id=CREATOR_ID, message=error_msg, random_id=0)
    except Exception as send_error:
        print(f"Не удалось отправить отчет об ошибке в ВК: {send_error}")
    
    # Обязательно пробрасываем ошибку дальше, чтобы она отобразилась в консоли сервера
    raise e

bot.loop_wrapper.add_task(google_sheets_loop())
# bot.loop_wrapper.add_task(pruning_loop())
bot.run_forever()