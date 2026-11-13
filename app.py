import argparse
import secrets
from datetime import date
from pathlib import Path
from threading import Lock
from time import monotonic

from flask import Flask, Response, g, render_template, request

from timetable import (CSV_TEMPLATE, EditConflict, connect, initialize, preview_files,
                       query_day, query_room_week, read_state, rename_room, replace_file,
                       save_room_group, save_settings)


DEFAULT_DATABASE = Path(__file__).resolve().parent / 'data' / 'classrooms.sqlite3'
PREVIEW_LIFETIME = 30 * 60


def create_app(database_path=DEFAULT_DATABASE, today=date.today):
    app = Flask(__name__)
    app.json.ensure_ascii = False
    initialize(database_path)
    previews = {}
    preview_lock = Lock()

    def database():
        if 'db' not in g:
            g.db = connect(database_path)
        return g.db

    def json_payload():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ValueError('请求格式有误，请刷新后重试。')
        return payload

    @app.teardown_appcontext
    def close_database(_error):
        db = g.pop('db', None)
        if db is not None:
            db.close()

    @app.errorhandler(ValueError)
    def invalid_value(error):
        return {'error': str(error)}, 400

    @app.errorhandler(EditConflict)
    def edit_conflict(error):
        return {'error': str(error)}, 409

    @app.get('/')
    def index():
        return render_template('index.html')

    @app.get('/template.csv')
    def csv_template():
        return Response(CSV_TEMPLATE.encode('utf-8-sig'), content_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': 'attachment; filename="classroom-template.csv"'})

    @app.get('/api/state')
    def state():
        with database() as db:
            db.execute('BEGIN')
            return read_state(db, today())

    @app.put('/api/settings')
    @app.post('/api/reset')
    def update_settings():
        save_settings(database(), json_payload(), reset=request.method == 'POST')
        return state()

    def day_parameters():
        week = request.args.get('week', type=int)
        weekday = request.args.get('weekday', type=int)
        if week is None or weekday is None:
            raise ValueError('请选择教学周和星期。')
        return week, weekday

    @app.get('/api/query')
    def query():
        week, weekday = day_parameters()
        with database() as db:
            db.execute('BEGIN')
            groups = query_day(db, week, weekday, request.args.get('group'),
                               set(request.args.getlist('room')), set(request.args.getlist('course')),
                               request.args.get('revision', type=int), period_range=request.args.get('periods'))
        return {'groups': groups}

    @app.get('/api/room-schedule')
    def room_schedule():
        week, weekday = day_parameters()
        room = request.args.get('room', '').strip()
        if not room:
            raise ValueError('请选择教室。')
        with database() as db:
            db.execute('BEGIN')
            groups = query_day(db, week, weekday, None, {room}, None,
                               request.args.get('revision', type=int))
        return {'groups': groups}

    @app.put('/api/room-schedule')
    def update_room_schedule():
        save_room_group(database(), json_payload())
        return state()

    @app.get('/api/room-week')
    def room_week():
        room = request.args.get('room', '').strip()
        if not room:
            raise ValueError('请选择教室。')
        with database() as db:
            db.execute('BEGIN')
            groups = query_room_week(db, room, request.args.get('week', type=int),
                                     request.args.get('revision', type=int))
        return {'groups': groups}

    @app.put('/api/room-name')
    def update_room_name():
        payload = json_payload()
        name = rename_room(database(), payload.get('room'), payload.get('name'))
        return {**state(), 'renamed_room': name}

    @app.post('/api/preview')
    def preview():
        files = request.files.getlist('file')
        rooms = request.form.getlist('room')
        scopes = request.form.getlist('weeks')
        if not files or len(files) != len(rooms) or len(files) != len(scopes):
            raise ValueError('请选择 CSV，并为每份文件填写教室和替换周次。')
        with database() as db:
            db.execute('BEGIN')
            summaries, revision, valid = preview_files(db, files, rooms, scopes)
        token = None
        if valid:
            token = secrets.token_urlsafe(24)
            with preview_lock:
                now = monotonic()
                for key, (expires, _, _) in list(previews.items()):
                    if expires <= now:
                        del previews[key]
                previews[token] = (now + PREVIEW_LIFETIME, revision, valid)
        return {'token': token, 'files': summaries}

    @app.post('/api/import')
    def apply_import():
        token = json_payload().get('token')
        with preview_lock:
            staged = previews.pop(token, None) if isinstance(token, str) else None
        if staged is None or staged[0] <= monotonic():
            raise ValueError('预览已失效，请重新预览后导入。')
        _, revision, files = staged
        results = []
        for summary, entries in files:
            error = None
            try:
                replace_file(database(), revision, summary, entries)
            except ValueError as exception:
                error = str(exception)
            results.append({'file': summary['file'], 'room': summary['room'],
                            'imported': error is None, 'error': error})
        return {'files': results}

    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='运行本机空闲教室查询网页')
    parser.add_argument('--port', type=int, default=5000)
    parser.add_argument('--database', type=Path, default=DEFAULT_DATABASE)
    options = parser.parse_args()
    app = create_app(options.database)
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.run(host='127.0.0.1', port=options.port)
