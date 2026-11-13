import csv
import io
import re
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path


DEFAULT_START = '2026-08-31'
DEFAULT_WEEKS = 20
DEFAULT_GROUPS = '1-2,3-4,5-6,7-8,9-10'
CSV_HEADER = ('课程名', '星期', '节次', '周次')
CSV_TEMPLATE = '课程名,星期,节次,周次\r\n电力电子技术,1,1-2,"1-5,7-12"\r\n创新方法基础,1,9-10,"4,6-8"\r\n'


class EditConflict(ValueError):
    pass


def connect(path):
    db = sqlite3.connect(path)
    db.execute('PRAGMA foreign_keys = ON')
    return db


def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as db, db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                start TEXT NOT NULL, weeks INTEGER NOT NULL,
                groups TEXT NOT NULL, revision INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rooms (name TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS lessons (
                room TEXT NOT NULL REFERENCES rooms(name) ON DELETE CASCADE,
                week INTEGER NOT NULL, weekday INTEGER NOT NULL,
                period INTEGER NOT NULL, course TEXT NOT NULL,
                PRIMARY KEY (room, week, weekday, period)
            );
        ''')
        db.execute('INSERT OR IGNORE INTO settings VALUES (1, ?, ?, ?, 0)',
                   (DEFAULT_START, DEFAULT_WEEKS, DEFAULT_GROUPS))


def get_settings(db):
    start, weeks, groups, revision = db.execute(
        'SELECT start, weeks, groups, revision FROM settings WHERE id = 1').fetchone()
    return {'start': start, 'weeks': weeks, 'groups': groups.split(',')}, revision


def parse_range(value, maximum):
    match = re.fullmatch(r'\s*(\d+)\s*(?:[-–]\s*(\d+)\s*)?', value)
    if not match:
        raise ValueError('请填写正整数或范围，例如 3-4。')
    first = int(match[1])
    last = int(match[2]) if match[2] else first
    if not 1 <= first <= last <= maximum:
        raise ValueError(f'数字须在 1–{maximum} 内，且范围的起始值不能大于结束值。')
    return range(first, last + 1)


def parse_weeks(value, maximum):
    return sorted({week for item in value.split(',') for week in parse_range(item, maximum)})


def periods_by_group(groups):
    return {group: parse_range(group, 999) for group in groups}


def save_settings(db, payload, reset):
    try:
        start = date.fromisoformat(payload.get('start', ''))
    except (ValueError, TypeError):
        raise ValueError('请输入有效的第 1 周日期。') from None
    if start.isoweekday() != 1:
        raise ValueError('第 1 周的起始日期必须是周一。')
    weeks = payload.get('weeks')
    if type(weeks) is not int or weeks < 1 or weeks > (date.max - start).days // 7:
        raise ValueError('请输入有效的学期总周数。')
    groups = payload.get('groups')
    if not isinstance(groups, list) or not groups or not all(isinstance(group, str) for group in groups):
        raise ValueError('请至少填写一个节次组。')

    used_periods = set()
    normalized_groups = []
    for group in groups:
        periods = parse_range(group, 999)
        if used_periods.intersection(periods):
            raise ValueError('节次组之间不能重叠。')
        used_periods.update(periods)
        first, last = periods.start, periods.stop - 1
        normalized_groups.append(str(first) if first == last else f'{first}-{last}')
    normalized_groups.sort(key=lambda group: int(group.split('-')[0]))

    with db:
        db.execute('BEGIN IMMEDIATE')
        if reset:
            db.execute('DELETE FROM rooms')
        else:
            for week, period in db.execute('SELECT DISTINCT week, period FROM lessons'):
                if week > weeks or period not in used_periods:
                    raise ValueError('新设置超出已有排课的周次或节次范围，请保留相应范围。')
        db.execute('UPDATE settings SET start = ?, weeks = ?, groups = ?, revision = revision + 1 WHERE id = 1',
                   (start.isoformat(), weeks, ','.join(normalized_groups)))


def read_state(db, today):
    settings, revision = get_settings(db)
    week = (today - date.fromisoformat(settings['start'])).days // 7 + 1
    return {
        'settings': settings,
        'revision': revision,
        'week': week if 1 <= week <= settings['weeks'] else None,
        'weekday': today.isoweekday(),
        'rooms': [room for room, in db.execute('SELECT name FROM rooms ORDER BY name')],
        'courses': [course for course, in db.execute('SELECT DISTINCT course FROM lessons ORDER BY course')],
    }


def read_csv(upload, selected_weeks, settings):
    errors = []
    entries = {}
    try:
        source = upload.stream.read().decode('utf-8-sig')
    except UnicodeDecodeError:
        return entries, [{'line': None, 'message': '请将文件保存为 UTF-8 编码的 CSV。'}]

    reader = csv.reader(io.StringIO(source, newline=''), strict=True)
    allowed_periods = set().union(*periods_by_group(settings['groups']).values())
    try:
        header = [cell.strip() for cell in next(reader, [])]
        if len(header) != len(CSV_HEADER) or set(header) != set(CSV_HEADER):
            return entries, [{'line': 1, 'message': '表头须包含课程名、星期、节次、周次四列。'}]

        while True:
            line = reader.line_num + 1
            row = next(reader, None)
            if row is None:
                break
            if not row or (len(row) == 1 and not row[0].strip()):
                continue
            try:
                if len(row) != len(header):
                    raise ValueError('该行须有四列。周次含英文逗号时，请用双引号包住该字段。')
                fields = dict(zip(header, (cell.strip() for cell in row)))
                course = fields['课程名']
                if not course:
                    raise ValueError('课程名不能为空。')
                if not re.fullmatch(r'[1-7]', fields['星期']):
                    raise ValueError('星期须填 1 至 7。')
                weekday = int(fields['星期'])
                try:
                    periods = parse_range(fields['节次'], max(allowed_periods))
                    weeks = parse_weeks(fields['周次'], settings['weeks'])
                except ValueError as error:
                    raise ValueError(f'节次或周次无效。{error}') from None
                if not allowed_periods.issuperset(periods):
                    raise ValueError('排课节次超出学期配置的节次范围。')
                if not selected_weeks.issuperset(weeks):
                    raise ValueError('排课周次超出本次选定的替换范围。')
                slots = [(week, weekday, period) for week in weeks for period in periods]
                for week, day, period in slots:
                    previous = entries.get((week, day, period))
                    if previous is not None and previous != course:
                        raise ValueError(f'第 {week} 周星期 {day} 第 {period} 节与「{previous}」冲突。')
                entries.update(dict.fromkeys(slots, course))
            except ValueError as error:
                errors.append({'line': line, 'message': str(error)})
    except csv.Error as error:
        errors.append({'line': max(1, reader.line_num), 'message': f'CSV 格式错误（{error}）。'})
    return entries, errors


def target_rows(db, room, weeks):
    rows = db.execute('SELECT week, weekday, period, course FROM lessons WHERE room = ? '
                      'ORDER BY week, weekday, period', (room,))
    return [row for row in rows if row[0] in weeks]


def preview_files(db, files, rooms, scopes):
    settings, revision = get_settings(db)
    previews = []
    entries_by_file = []
    for upload, room, scope in zip(files, rooms, scopes):
        room = room.strip()
        preview = {'file': upload.filename, 'room': room, 'weeks': [], 'errors': [],
                   'count': 0, 'old': [], 'new_room': False}
        previews.append(preview)
        try:
            if not room:
                raise ValueError('请填写教室编号。')
            weeks = (list(range(1, settings['weeks'] + 1)) if scope == 'all'
                     else parse_weeks(scope, settings['weeks']))
        except ValueError as error:
            preview['errors'].append({'line': None, 'message': str(error)})
            entries_by_file.append({})
            continue

        week_set = set(weeks)
        entries, errors = read_csv(upload, week_set, settings)
        entries_by_file.append(entries)
        existing = db.execute('SELECT 1 FROM rooms WHERE name = ?', (room,)).fetchone()
        old = [dict(zip(('week', 'weekday', 'period', 'course'), row))
               for row in target_rows(db, room, week_set)]
        preview.update(weeks=weeks, count=len(entries), errors=errors, old=old, new_room=not existing)

    for index, first in enumerate(previews):
        for second in previews[index + 1:]:
            if first['room'] == second['room'] and set(first['weeks']).intersection(second['weeks']):
                first['errors'].append({'line': None, 'message': f'与「{second["file"]}」替换的周次重叠。'})
                second['errors'].append({'line': None, 'message': f'与「{first["file"]}」替换的周次重叠。'})

    return previews, revision, [(item, rows) for item, rows in zip(previews, entries_by_file) if not item['errors']]


def replace_file(db, revision, preview, entries):
    room = preview['room']
    weeks = set(preview['weeks'])
    expected = [(row['week'], row['weekday'], row['period'], row['course']) for row in preview['old']]
    with db:
        db.execute('BEGIN IMMEDIATE')
        if get_settings(db)[1] != revision or target_rows(db, room, weeks) != expected:
            raise ValueError('学期设置或课表已更改，请重新预览。')
        db.execute('INSERT OR IGNORE INTO rooms(name) VALUES (?)', (room,))
        db.executemany('DELETE FROM lessons WHERE room = ? AND week = ?', ((room, week) for week in weeks))
        db.executemany('INSERT INTO lessons VALUES (?, ?, ?, ?, ?)',
                       ((room, week, weekday, period, course) for (week, weekday, period), course in entries.items()))


def validate_week(settings, week):
    if type(week) is not int or not 1 <= week <= settings['weeks']:
        raise ValueError('请选择学期内的教学周。')


def validate_day(settings, week, weekday):
    validate_week(settings, week)
    if type(weekday) is not int or not 1 <= weekday <= 7:
        raise ValueError('请选择星期。')


def query_settings(db, revision):
    settings, current_revision = get_settings(db)
    if revision is not None and revision != current_revision:
        raise ValueError('学期设置已更改，请刷新后重新查询。')
    return settings


def group_lessons(schedule, periods):
    lessons = {}
    for period in periods:
        if period in schedule:
            lessons.setdefault(schedule[period], []).append(period)
    return lessons


def lesson_entries(lessons):
    return [{'course': course, 'periods': occupied} for course, occupied in lessons.items()]


def query_day(db, week, weekday, group, rooms, courses, revision=None, *, period_range=None):
    settings = query_settings(db, revision)
    validate_day(settings, week, weekday)
    groups = periods_by_group(settings['groups'])
    if period_range is not None:
        configured = set().union(*groups.values())
        selected = parse_range(period_range, max(configured))
        if not configured.issuperset(selected):
            raise ValueError('查询范围须由已配置的连续节次组成。')
        first, last = selected.start, selected.stop - 1
        label = str(first) if first == last else f'{first}-{last}'
        groups = {label: selected}
    elif group:
        if group not in groups:
            raise ValueError('请选择有效的节次组。')
        groups = {group: groups[group]}
    active_rooms = [room for room, in db.execute('SELECT name FROM rooms ORDER BY name')
                    if not rooms or room in rooms]
    if courses is None and not active_rooms:
        raise ValueError('所选教室不存在，请刷新课表。')
    schedule = {room: {} for room in active_rooms}
    for room, period, course in db.execute('SELECT room, period, course FROM lessons WHERE week = ? AND weekday = ?',
                                           (week, weekday)):
        if room in schedule:
            schedule[room][period] = course

    results = []
    for label, periods in groups.items():
        matches = []
        for room, timetable in schedule.items():
            lessons = group_lessons(timetable, periods)
            if courses is None or not lessons or courses.intersection(lessons):
                matches.append({'room': room, 'lessons': lesson_entries(lessons)})
        results.append({'group': label, 'rooms': matches})
    return results


def query_room_week(db, room, week, revision):
    settings = query_settings(db, revision)
    validate_week(settings, week)
    if not db.execute('SELECT 1 FROM rooms WHERE name = ?', (room,)).fetchone():
        raise ValueError('所选教室不存在，请刷新课表。')
    days = [{} for _ in range(7)]
    for weekday, period, course in db.execute('SELECT weekday, period, course FROM lessons '
                                              'WHERE room = ? AND week = ?', (room, week)):
        days[weekday - 1][period] = course
    return [{'group': group, 'days': [lesson_entries(group_lessons(day, periods)) for day in days]}
            for group, periods in periods_by_group(settings['groups']).items()]


def rename_room(db, room, name):
    if not isinstance(room, str) or not isinstance(name, str) or not name.strip():
        raise ValueError('请输入新的教室名称。')
    name = name.strip()
    with db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute('SELECT 1 FROM rooms WHERE name = ?', (room,)).fetchone():
            raise EditConflict('原教室名称已更改，请刷新课表后重试。')
        if room == name:
            return name
        if db.execute('SELECT 1 FROM rooms WHERE name = ?', (name,)).fetchone():
            raise EditConflict('这个教室名称已存在，请换一个名称。')
        db.execute('INSERT INTO rooms(name) VALUES (?)', (name,))
        db.execute('UPDATE lessons SET room = ? WHERE room = ?', (name, room))
        db.execute('DELETE FROM rooms WHERE name = ?', (room,))
        db.execute('UPDATE settings SET revision = revision + 1 WHERE id = 1')
    return name


def save_room_group(db, payload):
    room = payload.get('room')
    week = payload.get('week')
    weekday = payload.get('weekday')
    group = payload.get('group')
    revision = payload.get('revision')
    if not isinstance(room, str) or not isinstance(group, str) or type(revision) is not int:
        raise ValueError('请选择教室和节次组。')
    with db:
        db.execute('BEGIN IMMEDIATE')
        settings, current_revision = get_settings(db)
        if revision != current_revision:
            raise EditConflict('学期设置已更改，请关闭编辑并刷新课表后重试。')
        validate_day(settings, week, weekday)
        periods = periods_by_group(settings['groups']).get(group)
        if periods is None or not db.execute('SELECT 1 FROM rooms WHERE name = ?', (room,)).fetchone():
            raise ValueError('请选择已导入的教室和有效的节次组。')

        previous = payload.get('previous')
        courses = payload.get('courses')
        for values in (previous, courses):
            if (not isinstance(values, list) or len(values) != len(periods)
                    or not all(isinstance(course, str) for course in values)):
                raise ValueError('请为该组的每一节填写课程名，空闲节次可以留空。')

        key = (room, week, weekday)
        scope = (*key, periods.start, periods.stop - 1)
        current = dict(db.execute('SELECT period, course FROM lessons '
                                  'WHERE room = ? AND week = ? AND weekday = ? AND period BETWEEN ? AND ?', scope))
        if [current.get(period, '') for period in periods] != previous:
            raise EditConflict('这组课表已被更新，请关闭编辑后查看最新课表。')

        db.execute('DELETE FROM lessons WHERE room = ? AND week = ? AND weekday = ? AND period BETWEEN ? AND ?', scope)
        db.executemany('INSERT INTO lessons VALUES (?, ?, ?, ?, ?)',
                       ((*key, period, course) for period, value in zip(periods, courses) if (course := value.strip())))
