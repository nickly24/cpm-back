import logging
import json
import threading
import time
from flask_socketio import SocketIO, join_room, leave_room
from cpm_back.auth import verify_token
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
from cpm_back.services.homework_files.realtime_events import queue_thread_event

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
        return {'ok': True, 'thread_id': thread_id}
    except Exception:return {'ok': False}
    finally:close_db_connection(connection)


@socketio.on('thread.unsubscribe')
def unsubscribe(data):
    thread_id=int((data or {}).get('thread_id') or 0)
    if thread_id:
        leave_room(f'homework-thread:{thread_id}')


@socketio.on('typing.set')
def set_typing(data):
    data=data or {};thread_id=int(data.get('thread_id') or 0);user=verify_token(data.get('token'));active=bool(data.get('active'))
    if not thread_id or not user:return {'ok':False}
    connection=None
    try:
        connection=get_db_connection();cursor=connection.cursor(dictionary=True)
        cursor.execute('SELECT student_id FROM homework_chat_threads WHERE id=%s',(thread_id,));thread=cursor.fetchone()
        if not thread:return {'ok':False}
        from cpm_back.config import config
        from cpm_back.services.homework_files import HomeworkWorkflow
        HomeworkWorkflow(config)._assert_actor(cursor,user,thread['student_id'])
        cursor.execute(
            'INSERT INTO homework_realtime_presence (thread_id,actor_role,actor_id,is_typing,expires_at) '
            'VALUES (%s,%s,%s,%s,DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 4 SECOND)) '
            'ON DUPLICATE KEY UPDATE is_typing=VALUES(is_typing),expires_at=VALUES(expires_at)',
            (thread_id,user['role'],user['id'],active),
        )
        queue_thread_event(cursor,thread_id,'typing.changed',payload={
            'actor_role':user['role'],'actor_id':user['id'],'active':active,'expires_in_ms':4000,
        },ttl='10 SECOND')
        connection.commit();return {'ok':True}
    except Exception:
        if connection:connection.rollback()
        return {'ok':False}
    finally:close_db_connection(connection)

def _poll(app):
    last_id=None
    with app.app_context():
        while True:
            connection=None
            try:
                connection=get_db_connection();cursor=connection.cursor(dictionary=True)
                if last_id is None:
                    cursor.execute('SELECT COALESCE(MAX(id),0) AS last_id FROM homework_realtime_outbox')
                    last_id=cursor.fetchone()['last_id']
                cursor.execute('SELECT id,thread_id,event_type,entity_id,payload_json FROM homework_realtime_outbox WHERE id>%s AND expires_at>UTC_TIMESTAMP(6) ORDER BY id LIMIT 500',(last_id,))
                for row in cursor.fetchall():
                    last_id=row['id']
                    raw=row.get('payload_json');payload=json.loads(raw) if isinstance(raw,str) else (raw or {})
                    room=payload.get('room') if isinstance(payload,dict) else None
                    data=payload.get('data') if room else payload
                    if not isinstance(data,dict):data={}
                    if row['entity_id'] is not None:data.setdefault('entity_id',row['entity_id'])
                    if row['event_type']=='message.created' and row['entity_id']:
                        cursor.execute(
                            'SELECT id,client_message_id,sender_role,sender_id,kind,body,event_code,created_at '
                            'FROM homework_chat_messages WHERE id=%s',(row['entity_id'],),
                        )
                        message=cursor.fetchone()
                        if not message:continue
                        data={'message':message,'entity_id':row['entity_id']}
                    socketio.emit(row['event_type'],data,to=room or f'homework-thread:{row["thread_id"]}')
            except Exception as exc:logger.debug('realtime_outbox error_code=%s',type(exc).__name__)
            finally:close_db_connection(connection)
            time.sleep(.5)
