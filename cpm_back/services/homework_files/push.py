import json
import logging
import threading
from flask import current_app
from cpm_back.db.mysql_pool import get_db_connection, close_db_connection

logger=logging.getLogger(__name__)

def dispatch_push(role,user_id,title,body,url='/'):
    if not current_app.config.get('VAPID_PRIVATE_KEY'):return
    app=current_app._get_current_object()
    threading.Thread(target=_send,args=(app,role,user_id,title,body,url),daemon=True).start()

def _send(app,role,user_id,title,body,url):
    with app.app_context():
        connection=get_db_connection()
        try:
            cursor=connection.cursor(dictionary=True);cursor.execute('SELECT id,endpoint,p256dh,auth_secret FROM push_subscriptions WHERE user_role=%s AND user_id=%s AND enabled=1',(role,user_id));subscriptions=cursor.fetchall()
            from pywebpush import webpush,WebPushException
            for subscription in subscriptions:
                try:webpush(subscription_info={'endpoint':subscription['endpoint'],'keys':{'p256dh':subscription['p256dh'],'auth':subscription['auth_secret']}},data=json.dumps({'title':title,'body':body,'url':url},ensure_ascii=False),vapid_private_key=app.config['VAPID_PRIVATE_KEY'],vapid_claims={'sub':app.config['VAPID_SUBJECT']})
                except WebPushException as exc:
                    status=getattr(getattr(exc,'response',None),'status_code',None)
                    if status in (404,410):cursor.execute('DELETE FROM push_subscriptions WHERE id=%s',(subscription['id'],))
                    else:logger.info('push_failed subscription_id=%s status=%s',subscription['id'],status)
            connection.commit()
        finally:close_db_connection(connection)
