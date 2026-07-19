import importlib.util
import json
import unittest
from pathlib import Path


MODULE_PATH=Path(__file__).parents[1]/'cpm_back/services/homework_files/realtime_events.py'
spec=importlib.util.spec_from_file_location('homework_realtime_events',MODULE_PATH)
events=importlib.util.module_from_spec(spec);spec.loader.exec_module(events)


class Cursor:
    def __init__(self):
        self.calls=[];self.lastrowid=41

    def execute(self,query,params):
        self.calls.append((query,params))


class HomeworkRealtimeEventsTest(unittest.TestCase):
    def test_uuid_job_id_stays_in_json_and_not_bigint_entity_column(self):
        cursor=Cursor();job_id='2c43a5d6-f810-43b1-a35e-18b889691dd8'
        events.queue_job_progress(cursor,7,{
            'id':job_id,'homework_id':197,'status':'running','stage':'optimization','progress':45,'error_code':None,
        })
        params=cursor.calls[0][1]
        self.assertIsNone(params[1])
        payload=json.loads(params[2])
        self.assertEqual(payload['room'],'user:student:7')
        self.assertEqual(payload['data']['job']['id'],job_id)

    def test_notification_and_signal_are_created_together(self):
        cursor=Cursor()
        result=events.create_notification(cursor,'student',7,'graded',197,7)
        self.assertEqual(result,41)
        self.assertEqual(len(cursor.calls),2)
        signal=json.loads(cursor.calls[1][1][2])
        self.assertEqual(signal['room'],'user:student:7')
        self.assertEqual(signal['data']['entity_id'],41)

    def test_message_signal_does_not_copy_message_text_to_outbox(self):
        cursor=Cursor();events.queue_thread_event(cursor,9,'message.created',123)
        params=cursor.calls[0][1]
        self.assertEqual(params[:3],(9,'message.created',123))
        self.assertIsNone(params[3])


if __name__=='__main__':unittest.main()
