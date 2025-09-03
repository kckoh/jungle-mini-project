# app.py
from celery import Celery
from flask import Flask, jsonify, render_template, request, redirect, url_for, session, flash
from pymongo import MongoClient, ReturnDocument
import os
from openai import OpenAI
from datetime import datetime
import json
from bson import ObjectId
from functools import wraps
import math
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

def get_openai_key():
    key_file = os.environ.get("OPENAI_API_KEY_FILE")
    if key_file and os.path.exists(key_file):
        with open(key_file) as f:
            return f.read().strip()
    return os.environ.get("OPENAI_API_KEY")

openai = OpenAI(api_key=get_openai_key())

# MongoDB
client = MongoClient(os.environ.get("MONGO_URI"))
db = client.get_database()
# Post Table
posts = db["posts"]

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
    return render_template('index.html')

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

@app.route("/test/chatgpt")
def get_chatgpt_test():
    response = openai.responses.create(
        model="gpt-4o",
        instructions="You are a coding assistant.",
        input="Hi",
    )

    return jsonify({"result":response.output_text})

def to_json(doc):
    """Mongo -> JSON-safe (ObjectId/datetime handling)."""
    if not doc:
        return None
    out = dict(doc)
    if "_id" in out:
        out["_id"] = str(out["_id"])
    for k, v in out.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
    return out
    # Optional: add server-side fields
def add_meta(d):
    d.setdefault("created_at", datetime.utcnow())
    d.setdefault("updated_at", datetime.utcnow())
    return d



@celery.task
def get_store_keywords(title_description):
    # save the data to the mongodb
    doc = add_meta(title_description)


    # call chatgpt API
    prompt =  f"""You are an expert algorithm problem analyst.
    Given a problem Title and Description, extract only the essential keywords required to solve it, and give a crisp Korean explanation for each keyword.

    OUTPUT RULES:
    - Return STRICTLY valid JSON with these 3 arrays:
      {{
        "data_structures": [{"keyword": "...", "explanation": "..."}],
        "algorithms": [{"keyword": "...", "explanation": "..."}],
        "concepts": [{"keyword": "...", "explanation": "..."}]
      }}
    - 3~8 items total; avoid duplicates and synonyms.
    - Explanations must be ≤ 2 sentences, Korean, practical (왜 필요한지/언제 쓰는지).

    Title: {title_description.get("title")}
    Description: {title_description.get("description")}
    """
    response = openai.chat.completions.create(
        model="gpt-4o-mini",  # or "gpt-4o-mini" if you want cheaper/faster
        messages=[
            {"role": "system", "content": "You are an expert algorithm problem analyst."},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"}  # ensures valid JSON
    )

    # print(response.choices[0].message.content)
    parsed = json.loads(response.choices[0].message.content)
    doc.setdefault("data_structures",parsed['data_structures'])
    doc.setdefault("algorithms",parsed['algorithms'])
    doc.setdefault("concepts",parsed['concepts'])
    result = posts.insert_one(doc)

    _id = str(result.inserted_id)
    return _id



@celery.task
def get_store_aisuggestion(pid, post):

    # call chatgpt API
    prompt = f"""You are an expert algorithm problem analyst and senior code reviewer.
    ...
    Rules:
    - Return STRICT, VALID JSON only (no extra text).
    - Use this schema:
    {{
      "keywords": {{
        "data_structures": [{{"keyword": "...", "explanation": "..."}}],
        "algorithms": [{{"keyword": "...", "explanation": "..."}}]
      }},
      "code_review": {{
        "summary": "...",
        "approach": "...",
        "time_complexity": "e.g., O(N log N)",
        "space_complexity": "e.g., O(N)",
        "edge_cases_missing": ["..."],
        "test_cases_suggested": ["input/output example ..."],
        "refactoring_suggestions": ["..."]
      }},
      "study_plan": [
        {{"topic": "...", "why": "...", "what_to_focus": ["...", "..."] }}
      ],
      "uncertainties": [
        {{"item": "...", "reason": "...", "label": "모르겠습니다|추측입니다|확실하지 않음"}}
      ],
      "confidence": 0.0
    }}

    Title: {post.get("title", "")}
    Description: {post.get("description", "")}

    Code Snippets:
    {post.get("codeSnippets", "")}
    ...
    """
    response = openai.chat.completions.create(
        model="gpt-4o-mini",  # or "gpt-4o-mini" if you want cheaper/faster
        messages=[
            {"role": "system", "content": "You are an expert algorithm problem analyst and senior code reviewer."},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"}  # ensures valid JSON
    )

    print("response====",response.choices[0].message.content)
    # parsed = json.loads(response.choices[0].message.content)
    # doc.setdefault("data_structures",parsed['data_structures'])
    # doc.setdefault("algorithms",parsed['algorithms'])
    # doc.setdefault("concepts",parsed['concepts'])
    # result = posts.insert_one(doc)

    # _id = str(result.inserted_id)
    return "hello"



@app.route("/api/posts", methods=['POST'])
def create_post():
    data = request.get_json(silent=True)
    if data is None:
            return jsonify(error="JSON body required with Content-Type: application/json"), 400
    # celery
    task = get_store_keywords.delay(data)

    return jsonify({"task_id": task.id }), 201



# assuming that pid = objectid from the mongodb
@app.route("/problems/<pid>")
def problem_detail(pid):
    try:
        result = posts.find_one({"_id": ObjectId(pid)})
    except Exception as e:
        return redirect("/")
    if not result:
        return redirect("/")
    # fake data
    item = {
        "id": pid,
        "title": result["title"],
        "description": result['description'],
        "created_at": result['created_at'].date(),
        # "code": "def prefix_sum(arr):\n    ...",
    }
    if 'codeSnippets' in result:
        item.setdefault('code',result['codeSnippets'] )
    if 'aiSuggestion' in result:
        item.setdefault('aiSuggestion', result['aiSuggestion'])
    # unpack data_structures, algorithms, concepts
    data = result['data_structures'] + result['algorithms'] + result['concepts']

    keyword_solution = {
        item["keyword"]: item["explanation"]
        for item in data
    }

    return render_template("problems/problem_detail.html", item=item, keyword_solution=keyword_solution)

# TODO
# need to store aiSuggestion as the key value in the backend

# TODO
# handle aisuggestion in the frontend

@app.route("/api/posts/<pid>", methods=['PATCH'])
def update_post(pid):
    data = request.get_json()
    print("data=====", data)
    try:
        result = posts.find_one_and_update(
            {"_id": ObjectId(pid)},
            {"$set": data},
            return_document=ReturnDocument.AFTER
        )
    except Exception as e:
        return redirect("/")

    # get the post by id

    # after then use celery to update the aisuggestion
    result["_id"] = str(result["_id"])
    task = get_store_aisuggestion.delay(pid,result)
    return data, 200


if __name__ == "__main__":
    app.run(
        host=os.environ.get("FLASK_HOST", "0.0.0.0"),
        port=int(os.environ.get("FLASK_PORT", 5000)),
        debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true",
        use_reloader=False,
    )

