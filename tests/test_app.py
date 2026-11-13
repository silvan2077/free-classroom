import io
import tempfile
import unittest
from datetime import date
from pathlib import Path

from app import create_app


HEADER = '课程名,星期,节次,周次\n'
SAMPLE = HEADER + '电力电子技术,1,1-2,"1-5,7-12"\n创新方法基础,1,9-10,"4,6-8"\n'
ROOM = '教3404（西）'


class ClassroomTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.database = Path(directory) / 'test.sqlite3'
        self.client = self.make_client()

    def make_client(self, today=date(2026, 11, 12)):
        return create_app(self.database, today=lambda: today).test_client()

    def preview(self, *files):
        return self.client.post('/api/preview', data={
            'file': [(io.BytesIO(csv.encode('utf-8-sig')), f'课表{i}.csv')
                     for i, (_, _, csv) in enumerate(files, 1)],
            'room': [room for room, _, _ in files],
            'weeks': [weeks for _, weeks, _ in files],
        }).get_json()

    def apply(self, preview):
        return self.client.post('/api/import', json={'token': preview['token']}).get_json()['files']

    def query(self, week, group=None, course=(), room=(), weekday=1):
        params = {'week': week, 'weekday': weekday, 'course': course, 'room': room}
        if group:
            params['group'] = group
        return self.client.get('/api/query', query_string=params).get_json()['groups']

    def test_default_week_and_date_outside_term(self):
        state = self.client.get('/api/state').get_json()
        self.assertEqual((state['week'], state['weekday']), (11, 4))
        outside = self.make_client(date(2027, 1, 18))
        self.assertIsNone(outside.get('/api/state').get_json()['week'])

    def test_rename_room_preserves_lessons_and_rejects_conflicts_and_stale_imports(self):
        self.apply(self.preview((ROOM, 'all', SAMPLE), ('教3405', 'all', HEADER)))
        pending = self.preview((ROOM, 'all', SAMPLE))
        self.assertEqual(self.client.put('/api/room-name', json={'room': ROOM, 'name': '教3405'}).status_code, 409)
        new_name = '教学楼 3404'
        result = self.client.put('/api/room-name', json={'room': ROOM, 'name': new_name})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(set(result.get_json()['rooms']), {new_name, '教3405'})
        self.assertFalse(self.apply(pending)[0]['imported'])
        self.client = self.make_client()
        rooms = self.query(6, '9-10', ('创新方法基础',), (new_name,))[0]['rooms']
        self.assertEqual(rooms, [{'room': new_name, 'lessons': [{'course': '创新方法基础', 'periods': [9, 10]}]}])

    def test_photo_example_and_room_course_filters(self):
        self.apply(self.preview((ROOM, 'all', SAMPLE), ('教3405', 'all', HEADER)))
        self.assertEqual(len(self.query(6)), 5)
        self.assertIn({'room': ROOM, 'lessons': []}, self.query(6, '1-2')[0]['rooms'])
        self.assertIn({'room': ROOM, 'lessons': []}, self.query(5, '9-10')[0]['rooms'])
        self.assertNotIn(ROOM, [item['room'] for item in self.query(6, '9-10')[0]['rooms']])
        results = self.query(6, '9-10', ('电力电子技术', '创新方法基础'))[0]['rooms']
        self.assertIn({'room': ROOM, 'lessons': [{'course': '创新方法基础', 'periods': [9, 10]}]}, results)
        self.assertIn({'room': '教3405', 'lessons': []}, results)
        self.assertEqual(self.query(6, '9-10', room=(ROOM,))[0]['rooms'], [])

    def test_room_schedule_shows_occupied_and_free_groups_for_one_room(self):
        self.apply(self.preview((ROOM, 'all', SAMPLE), ('教3405', 'all', HEADER)))
        params = {'room': ROOM, 'week': 6, 'weekday': 1}
        response = self.client.get('/api/room-schedule', query_string=params)
        self.assertEqual(response.status_code, 200)
        groups = response.get_json()['groups']
        self.assertEqual([group['group'] for group in groups], ['1-2', '3-4', '5-6', '7-8', '9-10'])
        self.assertEqual(groups[0]['rooms'], [{'room': ROOM, 'lessons': []}])
        self.assertEqual(groups[-1]['rooms'], [
            {'room': ROOM, 'lessons': [{'course': '创新方法基础', 'periods': [9, 10]}]},
        ])
        self.assertEqual(self.client.get('/api/room-schedule', query_string={
            **params, 'room': '未导入教室',
        }).status_code, 400)

    def test_room_week_shows_seven_days_with_the_selected_rooms_actual_periods(self):
        selected = HEADER + '周一课程,1,1-2,6\n周日课程,7,2,6\n下一周课程,2,1,7\n'
        another = HEADER + '其他教室课程,2,1-2,6\n'
        self.apply(self.preview((ROOM, 'all', selected), ('教3405', 'all', another)))
        params = {'room': ROOM, 'week': 6}
        response = self.client.get('/api/room-week', query_string=params)
        self.assertEqual(response.status_code, 200)
        groups = response.get_json()['groups']
        self.assertEqual([group['group'] for group in groups], ['1-2', '3-4', '5-6', '7-8', '9-10'])
        days = groups[0]['days']
        self.assertEqual(len(days), 7)
        self.assertEqual(days[0], [{'course': '周一课程', 'periods': [1, 2]}])
        self.assertEqual(days[1:6], [[], [], [], [], []])
        self.assertEqual(days[6], [{'course': '周日课程', 'periods': [2]}])
        self.assertEqual(self.client.get('/api/room-week', query_string={**params, 'week': 21}).status_code, 400)

    def test_custom_range_requires_every_period_to_be_free(self):
        self.apply(self.preview((ROOM, 'all', HEADER + '第 4 节有课,1,4,6\n'),
                                ('教3405', 'all', HEADER + '范围之外,1,9-10,6\n')))
        params = {'week': 6, 'weekday': 1}
        for label, expected_rooms in [('1-2', ['教3404（西）', '教3405']),
                                      ('1-4', ['教3405']), ('3-8', ['教3405'])]:
            with self.subTest(label=label):
                response = self.client.get('/api/query', query_string={**params, 'periods': label})
                self.assertEqual(response.get_json()['groups'], [{'group': label, 'rooms': [
                    {'room': room, 'lessons': []} for room in expected_rooms
                ]}])

    def test_custom_range_cannot_span_an_unconfigured_period(self):
        self.client.put('/api/settings', json={'start': '2026-08-31', 'weeks': 20, 'groups': ['1-2', '4-6']})
        response = self.client.get('/api/query', query_string={'week': 6, 'weekday': 1, 'periods': '2-4'})
        self.assertEqual(response.status_code, 400)

    def test_manual_edit_adds_updates_and_clears_only_the_selected_group(self):
        self.apply(self.preview((ROOM, 'all', SAMPLE)))
        revision = self.client.get('/api/state').get_json()['revision']
        payload = {'room': ROOM, 'week': 6, 'weekday': 1, 'group': '9-10', 'revision': revision,
                   'previous': ['创新方法基础', '创新方法基础'], 'courses': ['新课程', '']}
        response = self.client.put('/api/room-schedule', json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.put('/api/room-schedule', json=payload).status_code, 409)
        addition = dict(payload, group='1-2', previous=['', ''], courses=['', '补课'])
        self.assertEqual(self.client.put('/api/room-schedule', json=addition).status_code, 200)

        self.client = self.make_client()
        self.assertEqual(self.query(6, '9-10', ('新课程',))[0]['rooms'][0]['lessons'],
                         [{'course': '新课程', 'periods': [9]}])
        self.assertEqual(self.query(6, '1-2', ('补课',))[0]['rooms'][0]['lessons'],
                         [{'course': '补课', 'periods': [2]}])
        self.assertEqual(self.query(7, '9-10', ('创新方法基础',))[0]['rooms'][0]['lessons'],
                         [{'course': '创新方法基础', 'periods': [9, 10]}])

    def test_replace_selected_weeks_and_reopen_database(self):
        self.apply(self.preview((ROOM, 'all', SAMPLE)))
        pending = self.preview((ROOM, '7-10', HEADER))
        self.assertEqual({item['week'] for item in pending['files'][0]['old']}, {7, 8, 9, 10})
        self.assertTrue(self.apply(pending)[0]['imported'])
        self.client = self.make_client()
        self.assertEqual(self.query(7, '1-2')[0]['rooms'], [{'room': ROOM, 'lessons': []}])
        self.assertEqual(self.query(11, '1-2')[0]['rooms'], [])
        self.assertEqual(self.query(1, '1-2')[0]['rooms'], [])
        self.assertEqual(self.query(20, '1-2')[0]['rooms'], [{'room': ROOM, 'lessons': []}])

    def test_invalid_file_does_not_block_other_rooms(self):
        self.apply(self.preview((ROOM, 'all', SAMPLE)))
        invalid = HEADER + '一门课,1,3,1\n另一门课,1,3-4,1\n'
        pending = self.preview((ROOM, '1', invalid), ('教3405', '2', HEADER))
        self.assertEqual(pending['files'][0]['errors'][0]['line'], 3)
        self.assertEqual([item['room'] for item in self.apply(pending)], ['教3405'])
        self.assertEqual(self.query(1, '1-2')[0]['rooms'], [{'room': '教3405', 'lessons': []}])

    def test_stale_preview_after_target_data_changes(self):
        first = self.preview((ROOM, '1', HEADER + '旧预览,1,1-2,1\n'))
        self.apply(self.preview((ROOM, '1', HEADER + '先提交,1,1-2,1\n')))
        self.assertFalse(self.apply(first)[0]['imported'])
        self.assertEqual(self.query(1, '1-2', ('先提交',))[0]['rooms'][0]['lessons'][0]['course'], '先提交')

    def test_blank_fields_cannot_clear_existing_schedule(self):
        self.apply(self.preview((ROOM, 'all', SAMPLE)))
        invalid = self.preview((ROOM, '1', HEADER + ',,,\n'))
        self.assertIsNone(invalid['token'])
        self.assertEqual(invalid['files'][0]['errors'][0]['line'], 2)
        self.assertEqual(self.query(1, '1-2')[0]['rooms'], [])

    def test_query_rejects_stale_semester_settings(self):
        initial = self.client.get('/api/state').get_json()
        changed = dict(initial['settings'], start='2026-09-07')
        self.client.put('/api/settings', json=changed)
        response = self.client.get('/api/query', query_string={
            'week': 11, 'weekday': 4, 'revision': initial.get('revision', 0),
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('学期设置已更改', response.get_json()['error'])

    def test_batch_overlap_and_out_of_range_records(self):
        pending = self.preview((ROOM, '1-2', HEADER), (ROOM, '2-3', HEADER),
                               ('教3405', '1', HEADER + '范围外,1,1,2\n'))
        self.assertIsNone(pending['token'])
        self.assertTrue(all(item['errors'] for item in pending['files']))
        self.assertEqual(pending['files'][2]['errors'][0]['line'], 2)

    def test_partial_group_occupancy_and_settings(self):
        self.apply(self.preview((ROOM, 'all', HEADER + '单节课,1,1,1\n')))
        self.assertEqual(self.query(1, '1-2')[0]['rooms'], [])
        occupied = self.query(1, '1-2', ('单节课',))[0]['rooms'][0]
        self.assertEqual(occupied['lessons'], [{'course': '单节课', 'periods': [1]}])
        payload = {'start': '2026-08-31', 'weeks': 20, 'groups': ['2-3']}
        self.assertEqual(self.client.put('/api/settings', json=payload).status_code, 400)
        reset = self.client.post('/api/reset', json=payload)
        self.assertEqual(reset.get_json()['rooms'], [])
        self.assertEqual(reset.get_json()['settings']['groups'], ['2-3'])

    def test_template_has_bom_and_can_be_imported(self):
        template = self.client.get('/template.csv')
        self.assertTrue(template.data.startswith(b'\xef\xbb\xbf'))
        pending = self.preview((ROOM, 'all', template.data.decode('utf-8-sig')))
        self.assertFalse(pending['files'][0]['errors'])


if __name__ == '__main__':
    unittest.main()
