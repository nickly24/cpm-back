import logging
import threading
import time
from flask_socketio import SocketIO, join_room
from cpm_back.auth import verify_token
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

logger=logging.getLogger(__name__)
socketio=SocketIO(async_mode='threading',manage_session=False,logger=False,engineio_logger=False)
_started=set();_lock=threading.Lock()

def init_realtime(app,origins):
    socketio.init_app(app,cors_allowed_origins=origins,async_mode='threading')
    with _lock:
        if id(app) not in _started:
            _started.add(id(app));threading.Thread(target=_poll,args=(app,),daemon=True,name='homework-realtime-outbox').start()

@socketio.on('connect')
def connected(auth):
    user=verify_token((auth or {}).get('token'))
    if not user:return False
    join_room(f'user:{user["role"]}:{user["id"]}')

@socketio.on('thread.subscribe')
def subscribe(data):
    thread_id=int((data or {}).get('thread_id') or 0)
    user=verify_token((data or {}).get('token'))
    if not thread_id or not user:return
    connection=None
    try:
        connection=get_db_connection();cursor=connection.cursor(dictionary=True)
        cursor.execute('SELECT student_id FROM homework_chat_threads WHERE id=%s',(thread_id,));thread=cursor.fetchone()
        if not thread:return
        from cpm_back.config import config
        from cpm_back.services.homework_files import HomeworkWorkflow
        HomeworkWorkflow(config)._assert_actor(cursor,user,thread['student_id'])
        join_room(f'homework-thread:{thread_id}')
    except Exception:return
    finally:close_db_connection(connection)

def _poll(app):
    last_id=0
    with app.app_context():
        while True:
            connection=None
            try:
                connection=get_db_connection();cursor=connection.cursor(dictionary=True)
                cursor.execute('SELECT id,thread_id,event_type,entity_id FROM homework_realtime_outbox WHERE id>%s AND expires_at>UTC_TIMESTAMP(6) ORDER BY id LIMIT 500',(last_id,))
                for row in cursor.fetchall():
                    last_id=row['id'];socketio.emit(row['event_type'],{'entity_id':row['entity_id']},to=f'homework-thread:{row["thread_id"]}')
            except Exception as exc:logger.debug('realtime_outbox error_code=%s',type(exc).__name__)
            finally:close_db_connection(connection)
            time.sleep(.5)
