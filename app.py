# app.py
from celery import Celery
from flask import Flask, jsonify, render_template, request, redirect, url_for, session, flash
from pymongo import MongoClient
import math
from werkzeug.security import check_password_hash, generate_password_hash
from functools import wraps
import os
import re
import time
import unicodedata
import requests
from bs4 import BeautifulSoup
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")


leetcode_session = os.getenv("LEETCODE_SESSION")
leetcode_csrf = os.getenv("LEETCODE_CSRF")

# Celery 설정
app.config.update(
    CELERY_BROKER_URL=os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0"),
    CELERY_RESULT_BACKEND=os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
)
celery = Celery(
    app.import_name,
    broker=app.config["CELERY_BROKER_URL"],
    backend=app.config["CELERY_RESULT_BACKEND"],
)
# Flask의 설정값 전체를 Celery config로도 반영
celery.conf.update(app.config)

# MongoDB
client = MongoClient(os.environ.get("MONGO_URI"))
db = client.get_database()
users_col = db.get_collection("users")

# Auth guard toggle (default: disabled for local dev)
AUTH_GUARD_ENABLED = os.environ.get("AUTH_GUARD_ENABLED", "false").lower() == "true"

# D : 로그인, 회원가입 브랜치 머지하면 삭제
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not AUTH_GUARD_ENABLED:
            return view(*args, **kwargs)
        if not session.get("user_id"):
            nxt = request.path
            return redirect(url_for("login", next=nxt))
        return view(*args, **kwargs)
    return wrapped

# UI: 메인 페이지
@app.get("/")
def index():
    return render_template("index.html")

# health check API
@app.get("/api/health")
def health():
    return jsonify(ok=True)

@app.get("/db/ping")
def db_ping():
    try:
        db.command("ping")
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500



@app.route("/problems")
@login_required
def problems():
    # TODO: 실제 DB 연동 후 목록 가져오기 (데모 데이터)
    items_all = [
        {
            "id": 1,
            "title": "구간 합 구하기",
            "body": "누적 합으로 구간 합을 효율적으로...",
            "created_at": "방금 전",
            "created_ts": 3,
            "tags": ["prefix-sum", "array"],
            "code": "def prefix_sum(arr):\n    ...",
        },
        {
            "id": 2,
            "title": "두 수의 합",
            "body": "해시를 사용하여 O(N)으로 탐색...",
            "created_at": "1시간 전",
            "created_ts": 2,
            "tags": ["hash", "two-pointer"],
        },
        {
            "id": 3,
            "title": "배낭 문제",
            "body": "DP로 0/1 Knapsack을 해결...",
            "created_at": "어제",
            "created_ts": 1,
            "tags": ["dp", "bf"],
            "code": "// knapsack implementation",
        },
    ]
    # 서버 측 필터링: 다중 필드 텍스트 검색 + 페이지네이션
    q = (request.args.get("q") or "").strip()
    # fields: 다중 선택(예: fields=title&fields=body). 이전 호환(field=title)도 지원
    fields = [f.strip() for f in request.args.getlist("fields") if f.strip()]
    legacy_field = (request.args.get("field") or "").strip()
    if legacy_field and legacy_field not in fields:
        fields.append(legacy_field)
    # 'all'이 들어오면 전체 필드로 간주
    if not fields or (len(fields) == 1 and fields[0] == "all"):
        fields = ["title", "body", "tag"]

    def contains(hay, needle):
        try:
            return needle.lower() in str(hay or "").lower()
        except Exception:
            return False

    items = list(items_all)
    if q:
        def match_keywords(it):
            checks = []
            if "title" in fields:
                checks.append(contains(it.get("title"), q))
            if "body" in fields:
                checks.append(contains(it.get("body"), q))
            if "tag" in fields:
                checks.append(any(contains(t, q) for t in (it.get("tags") or [])))
            return any(checks)

        items = [it for it in items if match_keywords(it)]

    # 페이지네이션만 유지
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    try:
        size = int(request.args.get("size", 5))
    except ValueError:
        size = 5
    size = min(max(1, size), 50)
    total = len(items)
    pages = max(1, math.ceil(total / size))
    page = min(page, pages)
    start = (page - 1) * size
    end = start + size
    page_items = items[start:end]

    return render_template(
        "problems/problems_list.html",
        items=page_items,
        q=q,
        fields=fields,
        page=page,
        size=size,
        total=total,
        pages=pages,
    )

# 추후 수정 (main브랜치 반영)
@app.route("/problems/new", methods=["GET", "POST"])
@login_required
def problem_new():
    if request.method == "POST":
        # TODO: DB 저장 후 상세로 이동
        return redirect(url_for("problems"))
    return render_template("problems/problem_new.html")


#  ---------------- Auth pages ----------------
# 추후 삭제 예정
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        nxt = request.args.get("next") or request.form.get("next")

        user = users_col.find_one({"email": email})
        if not user or not check_password_hash(user.get("password_hash", ""), password):
            flash("이메일 또는 비밀번호가 올바르지 않습니다.", "error")
            return redirect(url_for("login", next=nxt) if nxt else url_for("login"))

        session["user_id"] = str(user.get("_id"))
        session["email"] = user.get("email")
        return redirect(nxt or url_for("index"))
    return render_template("login.html", next=request.args.get("next"))

# 추후 삭제 예정
@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

# 추후 삭제 예정
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        interests = request.form.getlist("interests")  # 체크박스 다중선택
        boj_id = request.form.get("bojId")
        leetcode_id = request.form.get("leetcodeId")
        terms = request.form.get("terms") == "on"

        if not terms:
            flash("이용약관에 동의해야 가입할 수 있습니다.", "error")
            return redirect(url_for("signup"))
        if not email or not password:
            flash("이메일과 비밀번호를 입력해주세요.", "error")
            return redirect(url_for("signup"))
        if password != confirm:
            flash("비밀번호가 일치하지 않습니다.", "error")
            return redirect(url_for("signup"))
        if users_col.find_one({"email": email}):
            flash("이미 가입된 이메일입니다.", "error")
            return redirect(url_for("signup"))

        users_col.insert_one({
            "email": email,
            "password_hash": generate_password_hash(password),
            "interests": interests,
            "bojId": boj_id,
            "leetcodeId": leetcode_id,
        })
        user = users_col.find_one({"email": email})
        session["user_id"] = str(user.get("_id"))
        session["email"] = user.get("email")
        return redirect(url_for("index"))
    # 선택 가능한 관심사 목록 (템플릿에 표시용)
    algorithm_categories = [
        "동적계획법(DP)", "그래프", "트리", "정렬", "탐색",
        "문자열", "수학", "그리디", "분할정복", "백트래킹",
    ]
    return render_template("signup.html", algorithm_categories=algorithm_categories)
#  ---------------- Auth pages ----------------

# 간단한 비동기 작업
@celery.task
def add(x, y):
    return x + y


@app.route("/task")
def run_task():
    task = add.delay(2, 3)  # 비동기 실행
    return jsonify({"task_id": task.id})




@app.route("/result/<task_id>")
def get_result(task_id):
    task = add.AsyncResult(task_id)
    if task.state == "PENDING":
        return jsonify({"state": task.state})
    elif task.state == "SUCCESS":
        return jsonify({"state": task.state, "result": task.result})
    else:
        # 실패/예외 처리
        return jsonify({"state": task.state, "info": str(task.info)})


if __name__ == "__main__":
    app.run(
        host=os.environ.get("FLASK_HOST", "0.0.0.0"),
        port=int(os.environ.get("FLASK_PORT", 5000)),
        debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true",
        use_reloader=False,
    )

