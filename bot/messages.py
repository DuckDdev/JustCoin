"""Все тексты бота. Только русский язык, ничего секретного в публичных строках."""

from __future__ import annotations

from bot.config import CURRENCY_EMOJI, CURRENCY_NAME, TOP_SIZE

# --- Общее ---------------------------------------------------------------------
HELP = (
    f"🪙 <b>{CURRENCY_NAME}</b> — бот с виртуальной валютой.\n"
    "\n"
    "💰 <b>Команды</b>\n"
    "• /jcoin — получить джаст коины\n"
    "• /jtop — глобальный топ игроков\n"
    "• /jstats — твоя статистика и карточка\n"
    "• /promo &lt;код&gt; — активировать промокод\n"
    "\n"
    "🎨 <b>Джасгнит</b>\n"
    "• /jasgnight — магазин тем карточки\n"
    "• /theme &lt;тема&gt; — включить купленную тему\n"
    "\n"
    "🪪 <b>JustID</b>\n"
    "• /setid $John — занять свой идентификатор\n"
    "• /myid — посмотреть свой идентификатор\n"
    "• /id $John — карточка игрока по его идентификатору\n"
    "\n"
    "📦 Иногда после /jcoin к тебе едет курьер с посылкой. "
    "Когда он прибудет, бот пришлёт кнопку «Забрать посылку».\n"
    "\n"
    "💬 Также можно писать боту:\n"
    "• «Джасткоины» или /justcoins — получить джаст коины\n"
    "• «Топ» или /top — глобальный топ\n"
    "• «$John» — карточка игрока с таким JustID\n"
    "\n"
    "ℹ️ Баланс общий для всех чатов: он привязан к твоему аккаунту, а не к беседе."
)

# --- /jcoin --------------------------------------------------------------------
CLAIM_COOLDOWN = "Подожди! через {time} будет возможность получить джаст коины"
CLAIM_SUCCESS = "Вы получили {amount}"
CLAIM_UPGRADE_NOTE = "Апгрейд Jarvis x2 ⚡"
CLAIM_TOP_PLACE = "Место в топе: {place}/{total}"

COURIER_ARRIVED = (
    "Поздравляю, к тебе едет курьер через {time} "
    "ты должен забрать посылку ввиде {amount}"
)

# --- Курьер --------------------------------------------------------------------
PACKAGE_BUTTON = "📦 Забрать посылку"
PACKAGE_IN_TRANSIT = "Курьер ещё в пути, осталось {time}"
PACKAGE_ALREADY_TAKEN = "Эту посылку ты уже забрал"
PACKAGE_NOT_YOURS = "Это не твоя посылка"
PACKAGE_TAKEN = "📦 Ты забрал посылку! Тебе начислено {amount}\n{place}"
PACKAGE_AMOUNT = "{amount}"

# --- /jtop ---------------------------------------------------------------------
TOP_TITLE = "🏆 Топ игроков:"
TOP_ROW = "{index}. {name} — {amount}"
TOP_YOUR_PLACE = "Твоё место: {place}/{total} ({amount})"
# Заголовок страницы: видно, что игроков больше, чем показано.
TOP_TITLE_PAGE = "🏆 Топ игроков (стр. {page}/{pages}, всего {total}):"
TOP_PAGE_HINT = "\n<i>Показаны игроки {start}–{finish} из {total}</i>"
TOP_EMPTY = "🏆 Топ игроков пока пуст. Начни с команды /jcoin"

# --- /jstats -------------------------------------------------------------------
STATS_FALLBACK_TITLE = "📊 Статистика"
STATS_FALLBACK = (
    "📊 <b>Статистика</b>\n"
    "\n"
    "👤 Имя: {name}\n"
    "🪙 Баланс: {balance}\n"
    "🏆 Место в топе: {place}/{total}\n"
    "💰 Всего заработано: {earned}\n"
    "🔄 Получений: {claims}\n"
    "📦 Посылок получено: {packages}\n"
    "📅 В игре с: {since}"
)
STATS_CAPTION = "🪙 Твоя статистика в {currency}"

# --- Промокоды -----------------------------------------------------------------
PROMO_INVALID = "Такого промокода не существует"
PROMO_ALREADY = "Этот промокод ты уже активировал"
PROMO_EXHAUSTED = "Этот промокод больше не действует"
PROMO_USAGE = "Использование: /promo <код>"

PROMO_JARVIS_ACTIVATED = (
    "🤖 Доступ разрешён. Ты получил {amount} секретный Jarvis-коин. "
    "Открыта секретная команда: /jupgrade"
)
PROMO_COINS_ACTIVATED = "🤖 Промокод активирован. Ты получил {amount} Jarvis-коинов"
PROMO_GENERIC = "🎉 Промокод активирован!"

# --- Магазин апгрейдов ---------------------------------------------------------
SHOP_HEADER = "🤖 <b>Jarvis Shop</b>"
SHOP_BALANCE = "🔋 Jarvis-коинов: <b>{coins}</b>"
SHOP_OWNED = "✅ Твои апгрейды: {status}"
SHOP_COOLDOWN_NOW = "⏱ Кулдаун сейчас: {time}"
SHOP_MULTIPLIERS = "\n<b>💰 Множители</b>"
SHOP_COOLDOWNS = "\n<b>⏱ Сокращение кулдауна</b>"
SHOP_BUY = "Купить за {cost} 🔋"
SHOP_REFRESH = "🔄 Обновить"
SHOP_HINT = "\n<i>Апгрейды покупаются один раз и действуют навсегда.</i>"

SHOP_BOUGHT = "✅ Куплен апгрейд: {title}"
SHOP_NOT_ENOUGH = "Не хватает Jarvis-коинов: нужно ещё {missing}"
SHOP_ALREADY_OWNED = "Этот апгрейд уже куплен или слабее текущего"
SHOP_NOT_FOR_SALE = "Этот апгрейд выдаёт только администратор"
SHOP_UNKNOWN = "Такого апгрейда не существует"

# --- Баны ----------------------------------------------------------------------
BAN_HEADER = "🚫 Вы заблокированы в боте"
BAN_FOOTER = "Вопросы — к администратору"
BAN_LIFTED = "✅ Ваша блокировка снята. Приятной игры!"
BAN_USAGE = (
    "Использование:\n"
    "/ban <игрок> [срок] [причина] — например /ban $John 7д спам\n"
    "/unban <игрок> — снять бан\n"
    "/bans — список забаненных\n"
    "Срок: 30м, 12h, 7д, 4w. Без срока — бан бессрочный."
)
BAN_DONE = "🚫 {name} забанен{until}\nПричина: {reason}"
BAN_DONE_FOREVER = "🚫 {name} забанен навсегда\nПричина: {reason}"
BAN_UNDONE = "✅ Бан игрока {name} снят"
BAN_NOT_BANNED = "Игрок {name} не забанен"
BAN_ALREADY = "Игрок {name} уже забанен"
BAN_SELF = "Нельзя забанить самого себя"
BAN_ADMIN = "Нельзя забанить администратора"
BAN_BAD_DURATION = (
    "Не разобрал срок «{raw}». Примеры: 30м, 12h, 7д, 4w"
)
BAN_NO_TARGET = "Не удалось определить игрока. Укажите @username, ID или JustID"
BAN_LIST_EMPTY = "Забаненных нет"
BAN_LIST_HEADER = "🚫 <b>Забаненные ({count})</b>\n"
BAN_LIST_ROW = "• {name} — {reason}{until}"
BAN_LIST_UNTIL = " до {date}"
BAN_LIST_FOREVER = " навсегда"
BAN_STATS = "🚫 Активных банов: {count}"

# --- JustID --------------------------------------------------------------------
JUSTID_USAGE = (
    "Использование: /setid <имя>\n"
    "Имя пишется с $ или без: $John или John — одно и то же\n"
    "Освободить имя: /setid off"
)
JUSTID_SET = "✅ Твой JustID: {just_id}\nНайти тебя можно командой /id {just_id}"
JUSTID_SAME = "Твой JustID уже такой: {just_id}"
JUSTID_INVALID = (
    "Не удалось поставить JustID. Требования: от {min} до {max} символов, "
    "только латиница, цифры, точка и подчёркивание"
)
JUSTID_RESERVED = "Имя {just_id} зарезервировано системой"
JUSTID_TAKEN = "Имя {just_id} уже занято"
JUSTID_MY = "Твой JustID: {just_id}"
JUSTID_MY_NONE = "У тебя пока нет JustID.\nПридумай его: /setid $John"
JUSTID_RELEASED = "✅ JustID {just_id} освобождён"
JUSTID_NOT_FOUND = "Игрок с JustID {just_id} не найден"
JUSTID_LINK_UNKNOWN = (
    "Ник с {just_id} не найден.\n"
    "Свой ник можно занять командой /setid $John"
)

# --- Темы / магазин «Джасгнит» -------------------------------------------------
THEME_USAGE = "Использование: /theme <тема> — переключить купленную тему"
THEME_SET = "🎨 Тема карточки: {title}"
THEME_RESET = "🎨 Тема карточки сброшена на «{title}»"
THEME_UNKNOWN = "Такой темы нет. Доступные: {themes}"

JASG_HEADER = "🛍 <b>Джасгнит</b> — магазин тем карточки"
JASG_BALANCE = "🪙 Твой баланс: <b>{balance}</b>"
JASG_CURRENT = "🎨 Сейчас активна: <b>{title}</b>"
JASG_OWNED = "🔓 Куплено тем: {count}"
JASG_BUY = "Купить и включить"
JASG_APPLY = "Включить"
JASG_BOUGHT = "🎨 Тема «{title}» куплена и включена"
JASG_APPLIED = "🎨 Тема «{title}» включена"
JASG_NOT_ENOUGH = "Не хватает джаст коинов: нужно ещё {missing}"
JASG_ALREADY = "Тема «{title}» уже куплена"
JASG_ACTIVE = "Тема «{title}» уже активна"
JASG_HINT = (
    "\n<i>Темы покупаются один раз и действуют навсегда. "
    "Купленную тему можно включать и выключать бесплатно.</i>"
)

# --- Админ-панель -------------------------------------------------------------
AP_TITLE = "🛠 <b>Панель администратора</b>"
AP_HINT = "<i>Выберите раздел</i>"
AP_SECTION_MAINT = "🔧 Техработы"
AP_SECTION_PROMO = "🎟 Промокоды"
AP_SECTION_SHOP = "🤖 Товары Jarvis Shop"
AP_SECTION_THEMES = "🎨 Темы Джасгнита"
AP_SECTION_BANS = "🚫 Баны"
AP_SECTION_STATUS = "🏅 Статусы"
AP_SECTION_CHATS = "💬 Написать в чат"
AP_SECTION_STATS = "📊 Статистика"
AP_BACK = "◀️ Назад"
AP_CLOSE = "✖️ Закрыть"
AP_REFRESH = "🔄 Обновить"
AP_DENIED = "Панель только для администраторов"

AP_MAINT_TITLE = "🔧 <b>Техработы</b>\n<i>Нажмите на функцию, чтобы переключить</i>"
AP_MAINT_ROW = "{mark} {name}"
AP_MAINT_TOGGLED = "Функция {name} {state}"
AP_MAINT_REASON_PROMPT = (
    "Введите причину для {name} одним сообщением.\n"
    "Пустое сообщение включит функцию без причины."
)
AP_MAINT_REASON_SET = "Функция {name} отключена. Причина: {reason}"
AP_MAINT_REASON_CLEARED = "Функция {name} снова включена"
AP_MAINT_CANCELLED = "Отменено"

AP_STATS_TITLE = "📊 <b>Статистика</b>"
AP_STATS_LINE = "👥 Игроков: {players}"
AP_STATS_CHATS = "💬 Чатов: {chats}"
AP_STATS_BANS = "🚫 Активных банов: {bans}"
AP_STATS_THEMES = "🎨 Тем куплено: {themes}"
AP_STATS_ITEMS = "🤖 Товаров: {items}"
AP_STATS_PACKAGES = "📦 Посылок в работе: {packages}"

AP_PROMO_TITLE = "🎟 <b>Промокоды</b>"
AP_PROMO_ROW = "{mark} <code>{code}</code> — {coins} 🔋{uses}"
AP_PROMO_TOGGLE = "Промокод {code} {state}"
AP_PROMO_NEW = "✨ Создать промокод"
AP_PROMO_HINT = (
    "\n<i>Новый промокод: напишите код и количество Jarvis-коинов, "
    "например: <code>GOLD50 50</code></i>"
)

AP_SHOP_TITLE = "🤖 <b>Товары Jarvis Shop</b>"
AP_SHOP_ROW = "{mark} {title} — {cost} 🔋{extra}"
AP_SHOP_EMPTY = "Товаров пока нет"
AP_SHOP_NEW = "✨ Создать товар"
AP_SHOP_DELETE = "🗑 Удалить: {code}"
AP_ITEM_STEP1 = (
    "Шаг 1 из 2. Введите <b>название</b> товара и <b>стоимость</b> "
    "одним сообщением, например:\n<code>Золотой множитель 250</code>"
)
AP_ITEM_STEP2 = (
    "Шаг 2 из 2. Товар «{title}» за {cost}.\nВыберите <b>тип</b> товара:"
)
AP_ITEM_TYPE_MULT = "💰 Множитель (x2, x3, x5…)"
AP_ITEM_TYPE_CD = "⏱ Сокращение кулдауна (минуты)"
AP_ITEM_TYPE_THEME = "🎨 Тема карточки"
AP_ITEM_VALUE_PROMPT = "Введите <b>значение</b> для «{title}».\n{hint}"
AP_ITEM_THEME_PROMPT = (
    "Введите <b>цвет</b> темы «{title}» в формате <code>#RRGGBB</code>, "
    "например <code>#FFAA00</code>"
)
AP_ITEM_FAILED = "❌ Не удалось создать товар: {detail}"
AP_ITEM_CANCEL = "❌ Создание отменено"
AP_ITEM_WRONG_FORMAT = (
    "Не разобрал сообщение. Формат: <b>Название цена</b>, "
    "например: <code>Золотой множитель 250</code>"
)
AP_ITEM_BAD_VALUE = "Значение должно быть целым числом больше нуля"

AP_CHATS_TITLE = "💬 <b>Выберите чат</b>\n<i>Куда бот писать от своего имени</i>"
AP_CHATS_EMPTY = (
    "Бот ещё не запомнил ни одного чата.\n"
    "Чаты появляются после первых сообщений в них."
)
AP_CHATS_ROW = "{title}"
AP_CHATS_SEND_PROMPT = (
    "Введите сообщение для чата\n<b>{title}</b>.\n"
    "Оно будет отправлено от имени бота."
)
AP_CHATS_SENT = "✅ Сообщение отправлено в <b>{title}</b>"
AP_CHATS_FAILED = "❌ Не удалось отправить в {title}: бот там заблокирован"
AP_CHATS_PAGE = "Страница {page}/{pages}"
AP_CHATS_NEED_TEXT = "Сначала выберите чат из списка"

# --- Донаты (Telegram Stars) --------------------------------------------------
DONATE_HEADER = "⭐️ <b>Поддержать разработчика</b>"
DONATE_INTRO = (
    "Telegram Stars — внутренняя валюта Telegram. Она идёт напрямую "
    "разработчику, без комиссий приложений.\n"
    "Просто купи звёзды и кинь их боту — деньги придут мне на аккаунт."
)
DONATE_CATALOG = "💎 <b>Варианты</b>\n{catalog}"
DONATE_CUSTOM_HINT = (
    "Или своя сумма от {min} до {max}: <code>/donate 250</code>"
)
DONATE_USAGE = (
    "Напишите сумму звёзд или код позиции.\n"
    "Например: <code>/donate 50</code>"
)
DONATE_BAD_AMOUNT = (
    "Сумма должна быть целым числом от {min} до {max} звёзд"
)
DONATE_INVOICE_SENT = "Счёт на {stars} ⭐️ отправлен. Нажмите «Оплатить»."
DONATE_SENDING_FAILED = "Не удалось отправить счёт: {error}"
DONATE_ALREADY_PAID = "Этот счёт уже оплачен"
DONATE_APPROVED = "Счёт принят"
DONATE_REJECTED = "Оплата не прошла: {reason}"
DONATE_NOT_REGISTERED = "Счёт не найден, оплата отклонена"
DONATE_SUCCESS = (
    "⭐️ Спасибо! Принято {stars} звёзд.\n"
    "Поддержка очень помогает развитию бота."
)
DONATE_STATS = "Всего принято: {stars} ⭐️"
DONATE_THANKS_ADMIN = "{stars} ⭐️ от {name}"
DONATE_REFUNDED = "Платёж возвращён"

# --- Антиспам (обновление 1.1.3) ----------------------------------------------
ANTISPAM_FLOOD = "Вы слишком часто нажимаете. Подождите немного"
OFFLINE_NOTICE = "Данный бот был офлайн, попробуй сейчас это сделать"

# --- Кнопки -------------------------------------------------------------------
BTN_CLAIM = "🪙 ДжастКоины"
BTN_TOP = "🏆 Топ"
BTN_CARD = "🖼 Карточка"
BTN_STATS_TEXT = "📝 Статы текстом"
BTN_DONATE = "⭐️ Поддержать"
BTN_HELP = "❓ Помощь"

# --- Описания и ссылки (обновление 1.1.2) ------------------------------------
AP_SECTION_LINKS = "🔗 Ссылки"

AP_ITEM_DESC_PROMPT = (
    "Шаг 4. Товар «{title}» создан.\n"
    "Теперь <b>описание</b> — текст, который увидит игрок в магазине.\n"
    "Отправьте его сообщением или нажмите «Без описания», если он не нужен."
)
AP_ITEM_DESC_SAVED = "✅ Описание сохранено"
AP_ITEM_DESC_NONE = "Без описания"
AP_ITEM_LINKS_TITLE = "🔗 <b>Ссылки на «{title}»</b>"
AP_ITEM_LINKS_EMPTY = "У товара пока нет ссылок"
AP_ITEM_LINKS_ADD = "➕ Добавить ссылку"
AP_ITEM_LINKS_PICK = "🔗 Выберите товар для ссылки"
AP_ITEM_LINK_INPUT = (
    "Отправьте ссылку на «{title}».\n"
    "Формат: <b>Название адрес</b>, например: <code>Наш сайт example.com</code>\n"
    "Без названия подставится имя сайта: <code>example.com</code>"
)
AP_ITEM_LINK_SAVED = "✅ Ссылка «{title}» добавлена"
AP_ITEM_LINKS_DONE = "Готово. Товар «{title}» опубликован"
AP_ITEM_LINK_BAD = "Не понял ссылку. {detail}"
AP_ITEM_LINKS_ROW = "{mark} {title} — {url}{state}"

AP_LINKS_TITLE = "🔗 <b>Ссылки бота</b>\n<i>Видны всем игрокам в /help</i>"
AP_LINKS_EMPTY = "Ссылок пока нет"
AP_LINKS_ADD = "➕ Добавить ссылку"
AP_LINKS_INPUT = (
    "Отправьте ссылку: <b>Название адрес</b>, например "
    "<code>Наш канал t.me/justcoin_robot</code>\n"
    "Без названия подставится имя сайта."
)
AP_LINKS_SAVED = "✅ Ссылка «{title}» добавлена и доступна игрокам"
AP_LINKS_STATE = " (выключена)"

# --- Статусы -------------------------------------------------------------------
STATUS_LABEL = "🏅 Статус: {label}"
STATUS_NONE = "🏅 Статус не присвоен"
ADMIN_SETSTATUS_USAGE = "Использование: /setstatus <статус> | off — ответьте игроку"
ADMIN_SETSTATUS_OK = "✅ Игроку {name} выдан статус «{label}»"
ADMIN_SETSTATUS_CLEARED = "✅ Статус игрока {name} снят"
ADMIN_SETSTATUS_UNKNOWN = "Неизвестный статус: {status}\nДоступные: {statuses}"

# --- Объявления ----------------------------------------------------------------
ANNOUNCE_USAGE = "Использование: /announce <текст объявления>"
ANNOUNCE_SENDING = "📡 Рассылаю объявление в {total} чатов…"
ANNOUNCE_DONE = (
    "📡 Готово: отправлено <b>{sent}</b>, не доставлено <b>{failed}</b>"
)
ANNOUNCE_EMPTY = "Чатов для рассылки нет: бот ещё нигде не работал"
ANNOUNCE_NO_TARGET = "Ответьте на сообщение игрока или укажите его ID"

# --- «Замедлись» ---------------------------------------------------------------
SLOWDOWN_HEADER = "🐌 Замедлись! (х{multiplier})"
SLOWDOWN_REASON = (
    "Бот был оффлайн {duration}, поэтому ожидание выросло в {multiplier} раз"
)
SLOWDOWN_RESET = "Ожидание вернулось к обычному"

# --- Админские команды ---------------------------------------------------------
ADMIN_NO_TARGET = "Не удалось определить игрока. Ответьте ему в личку или укажите ID"
ADMIN_INSTANT_ON = "⚡ Мгновенное получение выдано игроку {name}"
ADMIN_INSTANT_OFF = "Мгновенное получение снято у игрока {name}"
ADMIN_GRANT_OK = "✅ Игроку {name} выдан апгрейд: {title}"
ADMIN_PROMO_CREATED = "✅ Промокод создан: <code>{code}</code> — {coins} Jarvis-коинов"
ADMIN_PROMO_EXISTS = "Такой промокод уже существует"
ADMIN_PROMO_DISABLED = "✅ Промокод {code} отключён"
ADMIN_PROMO_ENABLED = "✅ Промокод {code} включён"
ADMIN_PROMO_USAGE = (
    "Использование: /jpromo <код> <jarvis-коины> [макс.активаций] — создать, "
    "/jpromo off <код> — отключить, /jpromo list — список"
)
ADMIN_PROMO_LIST = "🎟 <b>Промокоды</b>\n"
ADMIN_PROMO_ROW = "• <code>{code}</code> — {coins} 🔋{uses}{state}"
ADMIN_PROMO_EMPTY = "Активных промокодов нет"
ADMIN_UPGRADE_USAGE = (
    "Использование: /jgrant <апгрейд> — выдать апгрейд игроку в ответе, "
    "/jinstant on|off — мгновенное получение"
)
ADMIN_UPGRADE_UNKNOWN = "Неизвестный апгрейд: {upgrade}\nДоступные: {upgrades}"

# --- Технические сообщения -----------------------------------------------------
MAINTENANCE_DISABLED = "Временно эта функция недоступна\nПричина: {reason}"
ANTISPAM = "Не так быстро! Подожди секунду"
BOT_ERROR = "Что-то пошло не так, попробуй ещё раз позже"
NO_RIGHTS = "Эта команда только для админов"
NOT_IN_GROUP = "Эта команда работает только в личке со мной"

MAINTENANCE_USAGE = "Использование: /maintenance_on <функция> <причина>"
MAINTENANCE_OFF_USAGE = "Использование: /maintenance_off <функция>"
MAINTENANCE_STATUS_USAGE = "Использование: /maintenance_status"
MAINTENANCE_UNKNOWN_FEATURE = (
    "Неизвестная функция: {feature}\nДоступные: {features}"
)
MAINTENANCE_ON_CONFIRM = "Функция {feature} отключена. Причина: {reason}"
MAINTENANCE_OFF_CONFIRM = "Функция {feature} снова включена"
MAINTENANCE_STATUS_HEADER = "🛠 Режим техработ:"
MAINTENANCE_STATUS_ON = "• {feature} — отключено ({reason})"
MAINTENANCE_STATUS_OFF = "• {feature} — работает"
MAINTENANCE_STATUS_NONE = "Все функции включены"

# Тексты для логов и уведомлений админам (не показываются игрокам)
LOG_BOT_STARTED = "🟢 Бот запущен"
LOG_BOT_STARTED_DETAILS = (
    "Админов: {admins}\n"
    "Функций под техработы: {features}\n"
    "База: {db}"
)
LOG_CRITICAL = "🔴 Критическая ошибка бота: {error}"
LOG_COURIER_ARRIVED = "📦 Курьер прибыл к игроку {user_id}, посылка {amount}"
LOG_UNKNOWN_COMMAND = "Неизвестная команда: {command}"

# --- Формат --------------------------------------------------------------------
COIN = f"{{amount}} {CURRENCY_EMOJI}"
TOP_SIZE_TEXT = str(TOP_SIZE)
