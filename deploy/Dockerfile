FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./requirements.txt
COPY deploy/requirements.txt ./deploy/requirements.txt
RUN python -m pip install --no-cache-dir -r deploy/requirements.txt \
    && install -d -o 1000 -g 1000 -m 0700 /app/data

COPY app.py timetable.py ./
COPY templates/ ./templates/
COPY static/ ./static/

USER 1000:1000
EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--access-logfile", "-", "app:create_app()"]
