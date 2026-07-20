import datetime as dt
import hashlib
import json
import uuid

from flask import Blueprint, jsonify, request

from cpm_back.auth import require_auth, require_role
from cpm_back.config import config
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection
from cpm_back.services.homework_files import HomeworkWorkflow, HomeworkWorkflowError
from cpm_back.services.homework_files.push import dispatch_push

homework_chat_bp=Blueprint('homework_chat',__name__,url_prefix='/api/homework-chat')


@homework_chat_bp.errorhandler(HomeworkWorkflowError)
def error(exc): return jsonify({'status':False,'error':exc.code,'details':exc.details}),exc.status


def _thread(cur,homework_id,student_id,create=False):
    cur.execute('SELECT id,status FROM homework_chat_threads WHERE homework_id=%s AND student_id=%s',(homework_id,student_id)); row=cur.fetchone()
    if not row and create:
        cur.execute("INSERT INTO homework_chat_threads (homework_id,student_id) VALUES (%s,%s) ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)",(homework_id,student_id))
        cur.execute('SELECT id,status FROM homework_chat_threads WHERE homework_id=%s AND student_id=%s',(homework_id,student_id)); row=cur.fetchone()
    return row


def _access(cur,user,thread_id):
    cur.execute('SELECT * FROM homework_chat_threads WHERE id=%s',(thread_id,)); thread=cur.fetchone()
    if not thread: raise HomeworkWorkflowError('thread_not_found',404)
    HomeworkWorkflow(config)._assert_actor(cur,user,thread['student_id'])
    return thread


def _ensure_chat_open(cur,homework_id,student_id):
    cur.execute('SELECT published FROM homework WHERE id=%s',(homework_id,)); hw=cur.fetchone()
    if not hw or not hw['published']: raise HomeworkWorkflowError('homework_not_published',403)
    cur.execute('SELECT status FROM homework_sessions WHERE homework_id=%s AND student_id=%s',(homework_id,student_id)); session=cur.fetchone()
    if session and session['status']: raise HomeworkWorkflowError('chat_closed_after_grade',409)


def _ensure_student_has_proctor(cur,user,student_id):
    if user.get('role')!='student':return
    cur.execute('SELECT 1 FROM students s JOIN proctors p ON p.group_id=s.group_id WHERE s.id=%s',(student_id,))
    if not cur.fetchone():raise HomeworkWorkflowError('proctor_not_assigned',409)


@homework_chat_bp.get('/thread')
@require_auth
def thread_summary(current_user=None):
    homework_id=request.args.get('homework_id',type=int); student_id=request.args.get('student_id',type=int) or current_user['id']
    conn=get_db_connection()
    try:
        cur=conn.cursor(dictionary=True); HomeworkWorkflow(config)._assert_actor(cur,current_user,student_id); _ensure_chat_open(cur,homework_id,student_id);_ensure_student_has_proctor(cur,current_user,student_id)
        thread=_thread(cur,homework_id,student_id)
        return jsonify({'thread':thread})
    finally: close_db_connection(conn)


@homework_chat_bp.get('/threads/<int:thread_id>/messages')
@require_auth
def history(thread_id,current_user=None):
    after=request.args.get('after',0,type=int); limit=min(max(request.args.get('limit',100,type=int),1),200)
    conn=get_db_connection()
    try:
        cur=conn.cursor(dictionary=True); _access(cur,current_user,thread_id)
        cur.execute('SELECT id,client_message_id,sender_role,sender_id,kind,body,event_code,created_at FROM homework_chat_messages WHERE thread_id=%s AND id>%s ORDER BY id LIMIT %s',(thread_id,after,limit)); return jsonify({'items':cur.fetchall()})
    finally: close_db_connection(conn)


@homework_chat_bp.post('/messages')
@require_auth
def send_message(current_user=None):
    body=request.get_json(silent=True) or {}; text=(body.get('text') or '').strip(); client_id=body.get('client_message_id') or request.headers.get('Idempotency-Key')
    if not text or len(text)>1000: raise HomeworkWorkflowError('message_length')
    try: uuid.UUID(str(client_id))
    except (ValueError,TypeError): raise HomeworkWorkflowError('invalid_client_message_id')
    homework_id=int(body.get('homework_id')); student_id=int(body.get('student_id') or current_user['id'])
    conn=get_db_connection()
    try:
        cur=conn.cursor(dictionary=True); HomeworkWorkflow(config)._assert_actor(cur,current_user,student_id); _ensure_chat_open(cur,homework_id,student_id);_ensure_student_has_proctor(cur,current_user,student_id)
        cur.execute("SELECT setting_value FROM application_settings WHERE setting_key='chat_messages_per_minute'"); setting=cur.fetchone(); rate=int(setting['setting_value'] if setting else 20)
        cur.execute("SELECT COUNT(*) count,MIN(created_at) oldest FROM homework_chat_messages WHERE sender_role=%s AND sender_id=%s AND kind='user' AND created_at>DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 60 SECOND)",(current_user['role'],current_user['id'])); usage=cur.fetchone()
        if usage['count']>=rate:
            retry=max(1,60-int((dt.datetime.utcnow()-usage['oldest']).total_seconds()))
            raise HomeworkWorkflowError('rate_limit',429,{'retry_after_seconds':retry})
        thread=_thread(cur,homework_id,student_id,True)
        cur.execute('SELECT id FROM homework_chat_messages WHERE thread_id=%s AND client_message_id=%s',(thread['id'],str(client_id))); existing=cur.fetchone()
        if existing:
            conn.rollback(); return jsonify({'id':existing['id'],'duplicate':True})
        cur.execute("INSERT INTO homework_chat_messages (thread_id,client_message_id,sender_role,sender_id,kind,body) VALUES (%s,%s,%s,%s,'user',%s)",(thread['id'],str(client_id),current_user['role'],current_user['id'],text)); message_id=cur.lastrowid
        cur.execute("INSERT INTO homework_realtime_outbox (thread_id,event_type,entity_id,payload_json,expires_at) VALUES (%s,'message.created',%s,%s,DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 1 DAY))",(thread['id'],message_id,json.dumps({'message_id':message_id})))
        # No body copy: notification points to the thread only.
        if current_user['role']=='student':
            cur.execute('SELECT p.id FROM students s JOIN proctors p ON p.group_id=s.group_id WHERE s.id=%s',(student_id,)); recipient=cur.fetchone()
            if recipient: cur.execute("INSERT INTO notifications (recipient_role,recipient_id,kind,homework_id,student_id,thread_id) VALUES ('proctor',%s,'chat_message',%s,%s,%s)",(recipient['id'],homework_id,student_id,thread['id']))
            cur.execute("INSERT INTO notifications (recipient_role,recipient_id,kind,homework_id,student_id,thread_id) SELECT 'admin',admin_id,'chat_message',%s,%s,%s FROM homework_chat_admin_followers WHERE thread_id=%s",(homework_id,student_id,thread['id'],thread['id']))
        else:
            cur.execute("INSERT INTO notifications (recipient_role,recipient_id,kind,homework_id,student_id,thread_id) VALUES ('student',%s,'chat_message',%s,%s,%s)",(student_id,homework_id,student_id,thread['id']))
        conn.commit()
        if current_user['role']=='student' and recipient:
            dispatch_push('proctor',recipient['id'],'Новое сообщение',f'Сообщение по домашней работе: {text[:100]}',f'/cabinet/proctor/messages')
        elif current_user['role']!='student':
            dispatch_push('student',student_id,'Новое сообщение',f'Сообщение по домашней работе: {text[:100]}',f'/cabinet/student/homework')
        return jsonify({'id':message_id,'thread_id':thread['id']}),201
    except Exception: conn.rollback(); raise
    finally: close_db_connection(conn)


@homework_chat_bp.post('/threads/<int:thread_id>/read')
@require_auth
def mark_read(thread_id,current_user=None):
    body=request.get_json(silent=True) or {}; message_id=int(body.get('message_id') or 0); conn=get_db_connection()
    try:
        cur=conn.cursor(dictionary=True); _access(cur,current_user,thread_id)
        cur.execute('INSERT INTO homework_chat_reads (thread_id,reader_role,reader_id,last_message_id) VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE last_message_id=GREATEST(last_message_id,VALUES(last_message_id))',(thread_id,current_user['role'],current_user['id'],message_id))
        cur.execute("INSERT INTO homework_realtime_outbox (thread_id,event_type,entity_id,expires_at) VALUES (%s,'message.read',%s,DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 1 DAY))",(thread_id,message_id)); conn.commit(); return jsonify({'status':True})
    except Exception: conn.rollback(); raise
    finally: close_db_connection(conn)


@homework_chat_bp.post('/threads/<int:thread_id>/typing')
@require_auth
def typing(thread_id,current_user=None):
    active=bool((request.get_json(silent=True) or {}).get('active')); conn=get_db_connection()
    try:
        cur=conn.cursor(dictionary=True); _access(cur,current_user,thread_id)
        cur.execute('INSERT INTO homework_realtime_presence (thread_id,actor_role,actor_id,is_typing,expires_at) VALUES (%s,%s,%s,%s,DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 3 SECOND)) ON DUPLICATE KEY UPDATE is_typing=VALUES(is_typing),expires_at=VALUES(expires_at)',(thread_id,current_user['role'],current_user['id'],active)); conn.commit(); return jsonify({'status':True})
    finally: close_db_connection(conn)


@homework_chat_bp.get('/threads/<int:thread_id>/presence')
@require_auth
def presence(thread_id,current_user=None):
    conn=get_db_connection()
    try:
        cur=conn.cursor(dictionary=True);_access(cur,current_user,thread_id)
        cur.execute('SELECT actor_role,actor_id,is_typing FROM homework_realtime_presence WHERE thread_id=%s AND is_typing=1 AND expires_at>UTC_TIMESTAMP(6) AND NOT (actor_role=%s AND actor_id=%s)',(thread_id,current_user['role'],current_user['id']));return jsonify({'items':cur.fetchall()})
    finally:close_db_connection(conn)


@homework_chat_bp.get('/threads/<int:thread_id>/reads')
@require_auth
def reads(thread_id,current_user=None):
    conn=get_db_connection()
    try:
        cur=conn.cursor(dictionary=True);_access(cur,current_user,thread_id)
        cur.execute('SELECT reader_role,reader_id,last_message_id FROM homework_chat_reads WHERE thread_id=%s',(thread_id,));return jsonify({'items':cur.fetchall()})
    finally:close_db_connection(conn)


@homework_chat_bp.post('/threads/<int:thread_id>/follow')
@require_role('admin')
def follow(thread_id,current_user=None):
    conn=get_db_connection()
    try:
        cur=conn.cursor(); cur.execute('INSERT IGNORE INTO homework_chat_admin_followers (thread_id,admin_id) VALUES (%s,%s)',(thread_id,current_user['id'])); conn.commit(); return jsonify({'status':True})
    finally: close_db_connection(conn)


@homework_chat_bp.delete('/threads/<int:thread_id>/follow')
@require_role('admin')
def unfollow(thread_id,current_user=None):
    conn=get_db_connection()
    try:
        cur=conn.cursor(); cur.execute('DELETE FROM homework_chat_admin_followers WHERE thread_id=%s AND admin_id=%s',(thread_id,current_user['id'])); conn.commit(); return jsonify({'status':True})
    finally: close_db_connection(conn)


@homework_chat_bp.get('/inbox')
@require_role('proctor','admin')
def inbox(current_user=None):
    search=(request.args.get('search') or '').strip(); conn=get_db_connection()
    try:
        cur=conn.cursor(dictionary=True); params=[]; scope=''
        if current_user['role']=='proctor': scope=' AND p.id=%s'; params.append(current_user['id'])
        if search: scope+=' AND (s.full_name LIKE %s OR h.name LIKE %s)'; params.extend([f'%{search}%',f'%{search}%'])
        unread="(SELECT COUNT(*) FROM homework_chat_messages m WHERE m.thread_id=t.id AND m.id>COALESCE((SELECT r.last_message_id FROM homework_chat_reads r WHERE r.thread_id=t.id AND r.reader_role=%s AND r.reader_id=%s),0))"
        if current_user['role']=='admin':
            unread=f"IF(EXISTS(SELECT 1 FROM homework_chat_admin_followers af WHERE af.thread_id=t.id AND af.admin_id=%s),{unread},0)";prefix=[current_user['id'],current_user['role'],current_user['id']]
        else:prefix=[current_user['role'],current_user['id']]
        cur.execute('SELECT t.id,t.homework_id,t.student_id,t.updated_at,s.full_name student_name,h.name homework_name,'+unread+' unread,EXISTS(SELECT 1 FROM homework_chat_admin_followers af WHERE af.thread_id=t.id AND af.admin_id=%s) following FROM homework_chat_threads t JOIN students s ON s.id=t.student_id JOIN homework h ON h.id=t.homework_id LEFT JOIN proctors p ON p.group_id=s.group_id WHERE t.status=\'active\''+scope+' ORDER BY t.updated_at DESC LIMIT 100',tuple(prefix+[current_user['id']]+params)); return jsonify({'items':cur.fetchall()})
    finally: close_db_connection(conn)


@homework_chat_bp.get('/settings')
@require_role('admin')
def settings(current_user=None):
    conn=get_db_connection()
    try:
        cur=conn.cursor(dictionary=True); cur.execute("SELECT setting_value FROM application_settings WHERE setting_key='chat_messages_per_minute'"); row=cur.fetchone(); return jsonify({'chat_messages_per_minute':int(row['setting_value'] if row else 20)})
    finally: close_db_connection(conn)


@homework_chat_bp.put('/settings')
@require_role('admin')
def update_settings(current_user=None):
    value=int((request.get_json(silent=True) or {}).get('chat_messages_per_minute',0))
    if not 1<=value<=100: raise HomeworkWorkflowError('invalid_rate_limit')
    conn=get_db_connection()
    try:
        cur=conn.cursor(); cur.execute("INSERT INTO application_settings (setting_key,setting_value,updated_by) VALUES ('chat_messages_per_minute',%s,%s) ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value),updated_by=VALUES(updated_by)",(str(value),current_user['id'])); conn.commit(); return jsonify({'chat_messages_per_minute':value})
    finally: close_db_connection(conn)


@homework_chat_bp.post('/push-subscriptions')
@require_auth
def register_push(current_user=None):
    body=request.get_json(silent=True) or {}; endpoint=body.get('endpoint'); keys=body.get('keys') or {}
    if not endpoint or not keys.get('p256dh') or not keys.get('auth'): raise HomeworkWorkflowError('invalid_push_subscription')
    digest=hashlib.sha256(endpoint.encode()).hexdigest(); conn=get_db_connection()
    try:
        cur=conn.cursor(); cur.execute('INSERT INTO push_subscriptions (user_role,user_id,endpoint_hash,endpoint,p256dh,auth_secret) VALUES (%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE user_role=VALUES(user_role),user_id=VALUES(user_id),p256dh=VALUES(p256dh),auth_secret=VALUES(auth_secret),enabled=1',(current_user['role'],current_user['id'],digest,endpoint,keys['p256dh'],keys['auth'])); conn.commit(); return jsonify({'status':True})
    finally: close_db_connection(conn)


@homework_chat_bp.get('/notifications')
@require_auth
def notifications(current_user=None):
    conn=get_db_connection()
    try:
        cur=conn.cursor(dictionary=True); cur.execute("DELETE FROM notifications WHERE created_at<DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 30 DAY)"); cur.execute('SELECT * FROM notifications WHERE recipient_role=%s AND recipient_id=%s ORDER BY id DESC LIMIT 100',(current_user['role'],current_user['id'])); rows=cur.fetchall(); conn.commit(); return jsonify({'items':rows})
    finally: close_db_connection(conn)


@homework_chat_bp.put('/push-subscriptions/toggle')
@require_auth
def toggle_push(current_user=None):
    enabled=bool((request.get_json(silent=True) or {}).get('enabled'));conn=get_db_connection()
    try:
        cur=conn.cursor();cur.execute('UPDATE push_subscriptions SET enabled=%s WHERE user_role=%s AND user_id=%s',(enabled,current_user['role'],current_user['id']));conn.commit();return jsonify({'enabled':enabled})
    finally:close_db_connection(conn)


@homework_chat_bp.delete('/push-subscriptions')
@require_auth
def delete_push(current_user=None):
    endpoint=(request.get_json(silent=True) or {}).get('endpoint')
    if not endpoint:raise HomeworkWorkflowError('endpoint_required')
    digest=hashlib.sha256(endpoint.encode()).hexdigest();conn=get_db_connection()
    try:
        cur=conn.cursor();cur.execute('DELETE FROM push_subscriptions WHERE endpoint_hash=%s AND user_role=%s AND user_id=%s',(digest,current_user['role'],current_user['id']));conn.commit();return jsonify({'status':True})
    finally:close_db_connection(conn)


@homework_chat_bp.post('/notifications/<int:notification_id>/read')
@require_auth
def notification_read(notification_id,current_user=None):
    conn=get_db_connection()
    try:
        cur=conn.cursor(); cur.execute('UPDATE notifications SET read_at=UTC_TIMESTAMP(6) WHERE id=%s AND recipient_role=%s AND recipient_id=%s',(notification_id,current_user['role'],current_user['id'])); conn.commit(); return jsonify({'status':True})
    finally: close_db_connection(conn)
