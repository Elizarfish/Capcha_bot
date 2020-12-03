import psycopg2

con = psycopg2.connect(
    database="",
    user="",
    password="",
    host="",
    port=""
)

cur = con.cursor()
cur.execute('''
    CREATE TABLE banlist
    (id SERIAL PRIMARY KEY NOT NULL,
    user_id INT NOT NULL,
    time timestamp NOT NULL,
    chat_id BIGINT NOT NULL,
    captcha_message_id INT NOT NULL,
    answer INT NOT NULL);
''')

con.commit()

con.close()
