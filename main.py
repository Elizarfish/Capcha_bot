import time
import threading

import psycopg2

from random import randint
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import Updater, BaseFilter, MessageHandler, CallbackQueryHandler, CommandHandler
from telegram.error import BadRequest


class FilterNewChatMembers(BaseFilter):
    ''' Фильтрация сообщений о входе '''
    def __init__(self):
        # Пользователи проходящие проверку капчей
        self.status_members = ['member', 'restricted', 'left', 'kicked']

    def __call__(self, update):
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        message = update.effective_message

        if message.new_chat_members:
            # Проверка, если пользователю уже давалась капча
            with con.cursor() as cur:
                cur.execute('SELECT * FROM banlist WHERE chat_id=%s AND user_id=%s',
                            (chat_id, user_id))
                if cur.fetchone():
                    return False

            member_status = message.bot.getChatMember(chat_id, user_id)['status']
            if member_status in self.status_members:
                return True
        return False


def banUser():
    '''
    Работает второстепенным потоком, банит
    пользователей не ответивших или ответивших
    неправильно, по истечению времени указанного в бд
    '''

    while True:
        time.sleep(60)
        with con.cursor() as cur:
            cur.execute('SELECT * FROM banlist WHERE time<LOCALTIMESTAMP')
            for banrecord in cur.fetchall():
                ban = {
                    "id_record": banrecord[0],
                    "user_id": banrecord[1],
                    "chat_id": banrecord[3],
                    "captcha_message_id": banrecord[4]
                }
                cur.execute('DELETE FROM banlist WHERE id=%s', (ban['id_record'], ))
                con.commit()
                dispatcher.bot.kick_chat_member(
                    ban['chat_id'],
                    ban['user_id']
                )


def captcha(update, context):
    '''
    Создаёт капчу, и отсылает пользователю,
    при этом заносит его в базу данных, если не
    ответит на неё в течение дня - будет кикнут
    '''

    user = update.effective_user
    chat = update.effective_chat
    captcha_answer = randint(1, 8)
    kick_date = (update.message.date + timedelta(days=1)).replace(tzinfo=None)

    if update.effective_user.username:
        username = "@" + user.username
    else:
        username = " ".join([user.first_name, user.last_name])

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(i, callback_data=str(i)) for i in range(1, 9)
    ]])

    captcha_msg = update.message.reply_text(
        '%s, выбери цифру %s' % (username, captcha_answers[captcha_answer]),
        reply_markup=keyboard
    )

    with con.cursor() as cur:
        cur.execute(
            'INSERT INTO banlist (user_id, time, chat_id, captcha_message_id, answer) VALUES (%s, %s, %s, %s, %s)',
            (user.id, kick_date, chat.id, captcha_msg.message_id, captcha_answer)
        )
        con.commit()

    context.bot.restrictChatMember(
        chat.id, user.id,
        permissions=ChatPermissions(can_send_messages=False)
    )


def checkCorrectlyCaptcha(update, context):
    '''
    Проверяю правильность ответа пользователя на капчу,
    если ответ правильный, то ограничение readonly снимается,
    если нет, то кик через 3-ок суток и отправляется сообщение
    с направлением к админу за разблокировкой
    '''

    chat = update.effective_chat
    user = update.effective_user
    message_id = update.callback_query.message.message_id
    user_captcha_answer = update.callback_query.data

    with con.cursor() as cur:
        cur.execute(
            'SELECT answer FROM banlist WHERE user_id=%s AND captcha_message_id=%s AND chat_id=%s',
            (user.id, message_id, chat.id)
        )
        record = cur.fetchone()

        if record:
            # Удаляю сообщение с капчей
            context.bot.delete_message(chat.id, message_id)
            # Проверяю ответ пользователя на капчу
            if user_captcha_answer == str(record[0]):
                cur.execute(
                    'DELETE FROM banlist WHERE user_id=%s AND chat_id=%s',
                    (user.id, chat.id)
                )
                context.bot.restrictChatMember(
                    chat.id, user.id,
                    permissions=ChatPermissions(
                        can_send_messages=True,
                        can_send_media_messages=True,
                        can_send_polls=True,
                        can_send_other_messages=True,
                        can_add_web_page_previews=True,
                    )
                )
            else:
                if update.effective_user.username:
                    username = "@" + user.username
                else:
                    username = " ".join([user.first_name, user.last_name])

                context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="%s, капча введена не правильно, обратитесь к админу в течении 3-х дней для разблокировки." % username
                )
                cur.execute(
                    'UPDATE banlist SET time=%s WHERE user_id=%s AND chat_id=%s',
                    (datetime.now(tz=None)+timedelta(days=3), user.id, chat.id)
                )
            con.commit()


def unban(update, context):
    ''' Убирает из бани пользователя '''
    chat = update.effective_chat
    command_user = update.effective_user
    message = update.effective_message
    member_status = message.bot.getChatMember(chat.id, command_user.id)['status']

    # Будет выполнено только если комманду прислал администратор
    if member_status in ['owner', 'administrator', 'creator']:
        # Ищем Id пользователя для разбана, либо в
        # пересланном сообщении либо указанное аргументом в комманде
        command = message['text'].split(" ")
        if len(command) > 1:
            user_id = command[1]
        elif 'reply_to_message' in message.to_dict():
            user_id = message.reply_to_message.to_dict()['from']['id']
        else:
            return

        # Снимаем бан и возвращаем права
        context.bot.unban_chat_member(chat.id, user_id, only_if_banned=True)
        context.bot.restrictChatMember(
            chat.id, user_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            )
        )

        # Убираем из бд оставшиеся записи бана
        with con.cursor() as cur:
            cur.execute(
                'SELECT captcha_message_id FROM banlist WHERE user_id=%s AND chat_id=%s',
                (user_id, chat.id)
            )
            captcha_message_id = cur.fetchone()

            if captcha_message_id:
                try:
                    context.bot.delete_message(chat.id, captcha_message_id[0])
                except BadRequest:
                    pass

            cur.execute(
                'DELETE FROM banlist WHERE user_id=%s AND chat_id=%s',
                (user_id, chat.id)
            )
            con.commit()


def main():
    global dispatcher
    '''
    Запускаем бота, создаём вебхуки,
    привязываем обработчики и фильтры.
    '''

    updater = Updater(token="your_token")
    dispatcher = updater.dispatcher
    filter = FilterNewChatMembers()


    dispatcher.add_handler(MessageHandler(filter, captcha))
    dispatcher.add_handler(CallbackQueryHandler(checkCorrectlyCaptcha))
    dispatcher.add_handler(CommandHandler('unban', unban))

    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    # Connect to DB
    con = psycopg2.connect(
        database="",
        user="",
        password="",
        host="",
        port=""
    )

    # Словарь для конвертация цифр на слова
    captcha_answers = {
        1: "один",
        2: "два",
        3: "три",
        4: "четыре",
        5: "пять",
        6: "шесть",
        7: "семь",
        8: "восемь"
    }

    # Второстепенный поток бана пользователей
    threading.Thread(target=banUser).start()

    # Тело бота
    main()
