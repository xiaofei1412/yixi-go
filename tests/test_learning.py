"""Phase 3: persisted labels, accurate denominators and calendar boundaries."""
from datetime import datetime, timedelta, timezone
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import server


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'learning.sqlite3'
        self.db_patch = patch.object(server, 'DB_PATH', self.path)
        self.db_patch.start()
        self.now = datetime(2026, 10, 2, 16, 30, tzinfo=timezone.utc)  # Oct 3 in Shanghai
        self.time_patch = patch.object(server, 'study_now', return_value=self.now)
        self.time_patch.start()
        self.client = TestClient(server.app)
        self.counter = 0

    def tearDown(self):
        self.client.close()
        self.time_patch.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def check(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def point(self, point_id='p', available=True):
        point = {'game_id':'g','node':'root/0','step':1,'player':1,'size':9,'title':'棋友',
                 'engine':server.engine.cache_identity if available else ['old-model']}
        with server.database() as con:
            con.execute('INSERT INTO study_points(id,data,note,streak,due) VALUES (?,?,?,2,?)',
                        (point_id,json.dumps(point),'原笔记',self.now.isoformat()))
        return point_id

    def record(self, point_id, verdict, when=None, legacy=False, created=None):
        self.counter += 1
        when = when or self.now
        result = {'verdict':verdict,'answer':'C7' if verdict != 'revealed' else None,
                  'loss':0 if verdict == 'accepted' else 5 if verdict == 'retry' else None}
        if not legacy:
            result['completed_at'] = when.isoformat()
        with server.database() as con:
            con.execute('INSERT INTO study_attempts VALUES (?,?,?,?)',
                        (str(self.counter),point_id,json.dumps(result),(created or when).isoformat()))

    def tags(self, point_id, tags):
        return self.client.post(f'/api/study/{point_id}/tags', json={'tags':tags})

    def stats(self, period='7'):
        return self.check(self.client.get(f'/api/study/stats?days={period}'))

    def test_empty_stats_null_rate_and_no_engine_work(self):
        with patch.object(server.engine,'query',side_effect=AssertionError('must not search')):
            stats = self.stats()
        self.assertEqual(stats['summary']['pass_rate'], None)
        self.assertEqual(stats['summary']['completed'], 0)
        self.assertEqual(stats['end_day'], '2026-10-03')
        self.assertEqual(stats['start_day'], '2026-09-27')
        self.assertEqual(len(stats['daily']), 7)
        self.assertTrue(all(day['accepted'] == 0 for day in stats['daily']))

    def test_rate_excludes_reveals_and_incomplete_sessions(self):
        for i, verdict in enumerate(('accepted','retry','revealed')):
            self.record(self.point(str(i)), verdict)
        with server.database() as con:
            con.execute('INSERT INTO study_attempts VALUES (?,?,NULL,?)',('unfinished','0',self.now.isoformat()))
        summary = self.stats()['summary']
        self.assertEqual((summary['completed'],summary['answered'],summary['pass_rate']), (3,2,50))
        self.assertEqual((summary['practiced_points'],summary['active_days']), (3,1))

    def test_reveal_only_is_not_zero_percent(self):
        self.record(self.point(),'revealed')
        self.assertIsNone(self.stats()['summary']['pass_rate'])

    def test_completion_date_midnight_and_inclusive_seven_day_window(self):
        p = self.point()
        self.record(p,'accepted',datetime(2026,10,2,15,59,tzinfo=timezone.utc))
        self.record(p,'retry',datetime(2026,10,2,16,0,tzinfo=timezone.utc),created=self.now-timedelta(days=20))
        start = datetime(2026,9,26,16,tzinfo=timezone.utc)
        self.record(p,'revealed',start)
        self.record(p,'accepted',start-timedelta(seconds=1))
        self.record(p,'accepted',self.now+timedelta(minutes=1))
        stats = self.stats()
        self.assertEqual(stats['summary']['completed'],3)
        self.assertEqual(stats['daily'][-1]['retry'],1)
        self.assertEqual(stats['daily'][-2]['accepted'],1)
        self.assertEqual(stats['daily'][0]['revealed'],1)
        self.assertEqual(self.stats('all')['summary']['completed'],4)
        self.assertEqual(len(self.stats('all')['daily']),30)
        self.assertEqual(self.stats('30')['summary']['completed'],4)

    def test_legacy_time_fallback_is_marked(self):
        self.record(self.point(),'accepted',legacy=True)
        self.assertEqual(self.stats()['summary']['estimated_times'],1)
        history = self.check(self.client.get('/api/study/p/learning'))['history']
        self.assertTrue(history[0]['time_estimated'])
        self.assertEqual(history[0]['completed_at'],self.now.isoformat())

    def test_tags_validation_update_and_clear_preserve_progress(self):
        p = self.point()
        before = self.check(self.client.get('/api/study'))['points'][0]
        for tags in (['死活','死活'],['错误标签'],['布局','死活','手筋','官子'],[' 死活']):
            self.assertEqual(self.tags(p,tags).status_code,422)
        self.check(self.tags(p,['死活','官子']))
        self.assertEqual(set(self.check(self.client.get('/api/study'))['points'][0]['tags']),{'死活','官子'})
        self.check(self.tags(p,['手筋']))
        learning = self.check(self.client.get('/api/study/p/learning'))
        self.assertEqual(learning['tags'],['手筋'])
        self.assertEqual(learning['note'],'原笔记')
        self.check(self.tags(p,[]))
        after = self.check(self.client.get('/api/study'))['points'][0]
        self.assertEqual(after,before)
        self.assertEqual(self.tags('missing',[]).status_code,404)

    def test_topics_use_latest_per_distinct_card_not_attempt_count(self):
        p = self.point()
        self.check(self.tags(p,['死活']))
        for _ in range(10):
            self.record(p,'retry')
        topic = self.stats()['topics'][0]
        self.assertEqual((topic['sample_count'],topic['needs_work'],topic['sufficient']),(1,1,False))
        self.record(p,'accepted')
        self.assertEqual(self.stats()['topics'][0]['needs_work'],0)
        for point_id, verdict in [('q','retry'),('r','revealed')]:
            self.point(point_id)
            self.check(self.tags(point_id,['死活']))
            self.record(point_id,verdict)
        topic = self.stats()['topics'][0]
        self.assertEqual((topic['sample_count'],topic['needs_work'],topic['sufficient']),(3,2,True))

    def test_multi_tags_and_current_relabeling(self):
        self.point()
        self.record('p','retry')
        self.check(self.tags('p',['死活','手筋']))
        self.assertEqual(len(self.stats()['topics']),2)
        self.check(self.tags('p',['官子']))
        self.assertEqual([t['tag'] for t in self.stats()['topics']],['官子'])

    def test_model_stale_records_count_in_history_but_not_topic_advice(self):
        self.point('old',available=False)
        self.check(self.tags('old',['死活']))
        self.record('old','retry')
        self.point('new')
        stats = self.stats()
        self.assertEqual(stats['summary']['completed'],1)
        self.assertEqual(stats['topics'],[])
        self.assertEqual((stats['due_count'],stats['untagged_count']),(1,1))

    def test_learning_history_is_bounded_sorted_and_omits_reference_answers(self):
        self.point()
        for i in range(12):
            self.record('p','accepted',self.now-timedelta(minutes=12-i))
        data = self.check(self.client.get('/api/study/p/learning'))
        self.assertEqual(len(data['history']),10)
        self.assertEqual(data['history'][0]['id'],'12')
        self.assertEqual(data['history'][-1]['id'],'3')
        self.assertFalse({'best','actual','frames','query'} & set(data['history'][0]))
        self.assertEqual(self.client.get('/api/study/missing/learning').status_code,404)

    def test_invalid_statistics_period(self):
        for period in ('0','8','-1','abc'):
            self.assertEqual(self.client.get('/api/study/stats',params={'days':period}).status_code,422)

    def test_phase_two_schema_upgrade_preserves_existing_records(self):
        # Construct the actual old table layout before the first new database call.
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute("CREATE TABLE study_points (id TEXT PRIMARY KEY,data TEXT NOT NULL,note TEXT NOT NULL DEFAULT '',streak INTEGER NOT NULL DEFAULT 0,due TEXT NOT NULL)")
            con.execute('CREATE TABLE study_attempts (id TEXT PRIMARY KEY,point_id TEXT NOT NULL,result TEXT,created TEXT NOT NULL)')
            raw = json.dumps({'engine':server.engine.cache_identity,'game_id':'old-game','node':'root/0','step':1,'player':1,'size':9,'title':'旧棋谱'})
            con.execute('INSERT INTO study_points VALUES (?,?,?,3,?)',('legacy',raw,'旧笔记',self.now.isoformat()))
            result = json.dumps({'verdict':'retry','answer':'A9','loss':5})
            con.execute('INSERT INTO study_attempts VALUES (?,?,?,?)',('legacy-attempt','legacy',result,self.now.isoformat()))
        self.check(self.tags('legacy',['官子']))
        with TestClient(server.app) as reopened:
            learning = self.check(reopened.get('/api/study/legacy/learning'))
            self.assertEqual((learning['note'],learning['tags']),('旧笔记',['官子']))
            listing = self.check(reopened.get('/api/study'))
            self.assertEqual((listing['completed'],listing['points'][0]['streak']),(1,3))
        self.assertEqual(self.stats()['summary']['estimated_times'],1)


if __name__ == '__main__':
    unittest.main()
