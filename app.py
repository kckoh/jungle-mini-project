# app.py
from celery import Celery

from flask import Flask, jsonify, render_template, request, redirect, url_for, session
from functools import wraps
from pymongo import MongoClient, ReturnDocument, ASCENDING, DESCENDING
import os
from openai import OpenAI
from datetime import datetime
import json
from bson import ObjectId
import re
from flask_bcrypt import Bcrypt

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-here')

bcrypt = Bcrypt(app)

# 로그인 체크 데코레이터
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'email' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

# TODO
# problem_new.html -> frontend: javascript fetch API ; backend:
# problems_list.html -> backend API: GET /api/posts GET /api/search, frontend
# /api/posts (pagination) + /api/search (search)
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
users = db["users"]

# 한 번만 실행
posts.create_index([("title", ASCENDING)])
posts.create_index([("description", ASCENDING)])

# 메인화면
@app.get("/")
@login_required
def index():
    return redirect(url_for('problems'))


@app.get("/db/ping")
def db_ping():
    try:
        db.command("ping")
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


# 로그인 화면으로 전환
@app.get("/login")
def login_page():
    if 'email' in session:
        return redirect(url_for('problems_list'))
    return render_template('login.html')


# 회원가입 화면으로 전환
@app.get("/signup")
def signup_page():
    if 'email' in session:
        return redirect(url_for('problems_list'))
    return render_template('signup.html')


# 세션 삭제 및 로그아웃 처리
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login_page'))


# 로그인 처리(클라이언트에 json을 받아와 json으로 응답 보냄)
@app.post("/api/login")
def login():
    try:
        data = request.json
        email = data.get('email')
        password = data.get('password')

        user = users.find_one({"email": email})
        if user and bcrypt.check_password_hash(user['hashed_password'], password):
            session['email'] = email
            return jsonify({"success": True, "message": "로그인에 성공했습니다."})
        else:
            return jsonify({"success": False, "message": "아이디와 비밀번호를 확인해주세요."})

    except Exception as e:
        return jsonify({"success": False, "message": "로그인 중 오류가 발생했습니다."})


# 회원가입 처리(클라이언트에 json을 받아와 DB에 데이터 입력 후 json으로 응답 보냄)
@app.post("/api/signup")
def signup():
    try:
        data = request.json
        email = data.get('email')
        password = data.get('password')
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')


        if users.find_one({"email": email}):
            return jsonify({"success": False, "message": "이미 사용 중인 이메일입니다."})

        users.insert_one({
            "email": email,
            "hashed_password": hashed_password
        })

        return jsonify({"success": True, "message": "회원가입이 완료되었습니다."})

    except Exception as e:
        return jsonify({"success": False, "message": "회원가입 중 오류가 발생했습니다."})


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
def get_store_keywords(pid,title_description_approach):

    # save the data to the mongodb
    doc = add_meta(title_description_approach)

    # call chatgpt API
    prompt = f"""
    You are an expert algorithm problem analyst.
    Given a problem Title, Description, and an Approach, do the following:

    1. Extract only the essential keywords required to solve it.
    2. Provide clear Korean explanations for each keyword (≤2 sentences, practical: 왜 필요한지/언제 쓰는지).
    3. Based on the given Approach, generate concrete suggestions or warnings, and give me your opinions on the user approach (e.g., is it the right aproach?) and put them in the "advice" array.

    ⚠️ VERY IMPORTANT:
    - "advice" MUST be ONLY a list of plain strings.
    - DO NOT include keyword/explanation objects inside advice.
    - If advice is not a string, your answer is invalid.

    OUTPUT RULES:
    - Return STRICTLY valid JSON with exactly 4 arrays:
    {{
      "data_structures": [{{"keyword": "...", "explanation": "..."}}],
      "algorithms": [{{"keyword": "...", "explanation": "..."}}],
      "concepts": [{{"keyword": "...", "explanation": "..."}}],
      "advice": ["...", "..."]
    }}

    ✅ Correct example:
    "advice": ["조건문 대신 switch문을 고려해라", "입력이 많으므로 O(n log n) 접근이 필요하다"]

    ❌ Wrong example (DO NOT do this):
    "advice": [{{"keyword": "조건문", "explanation": "점수 비교에 필요하다"}}]

    - 3~8 total items across data_structures/algorithms/concepts (avoid duplicates or synonyms).
    - advice must be 2–5 items, short and actionable, each based on the provided Approach.
    - "advice" MUST be a non-empty list of plain strings (length 2–5).
    - If advice would be empty, your answer is INVALID — generate best-practice advice inferred from Title/Description.
    - DO NOT output objects inside "advice".


    Title: {title_description_approach.get("title")}
    Description: {title_description_approach.get("description")}
    Approach: {title_description_approach.get("approach")}
    """

    response = openai.chat.completions.create(
        model="gpt-4.1",              # 사용 모델 지정
        messages=[
            {"role": "system", "content": "You are an expert algorithm problem analyst."},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"}  # JSON 강제
    )

    parsed = json.loads(response.choices[0].message.content)
#     doc.setdefault("data_structures",parsed['data_structures'])
#     doc.setdefault("algorithms",parsed['algorithms'])
#     doc.setdefault("concepts",parsed['concepts'])

    to_set = {
    "data_structures": parsed.get("data_structures", []),
    "algorithms": parsed.get("algorithms", []),
    "concepts": parsed.get("concepts", []),
    "advice": parsed.get("advice", []),
    }
    posts.update_one({"_id": ObjectId(pid)}, {"$set": to_set})
    # app.logger.info(f"Inserted Problem ID: {doc['id']}")

    return pid


@celery.task
def get_store_aisuggestion(pid, post):

    # call chatgpt API
    prompt = f"""
    You are an expert algorithm problem analyst.
    Given a problem Title, Description, and an Approach, do the following:


    1. Provide clear Korean explanations for each keyword (≤2 sentences, practical: 왜 필요한지/언제 쓰는지).
    2. Based on the given Approach, generate concrete suggestions or warnings, and give me your opinions on the user approach (e.g., is it the right aproach?) and put them in the "advice" array.

    ⚠️ VERY IMPORTANT:
    - "advice" MUST be ONLY a list of plain strings.
    - DO NOT include keyword/explanation objects inside advice.
    - If advice is not a string, your answer is invalid.

    OUTPUT RULES:
    - Return STRICTLY valid JSON with exactly 4 arrays:
    {{
      "advice": ["...", "..."]
    }}

    ✅ Correct example:
    "advice": ["조건문 대신 switch문을 고려해라", "입력이 많으므로 O(n log n) 접근이 필요하다"]

    ❌ Wrong example (DO NOT do this):
    "advice": [{{"keyword": "조건문", "explanation": "점수 비교에 필요하다"}}]

    - advice must be 2–5 items, short and actionable, each based on the provided Approach.
    - "advice" MUST be a non-empty list of plain strings (length 2–5).
    - If advice would be empty, your answer is INVALID — generate best-practice advice inferred from Title/Description.
    - DO NOT output objects inside "advice".

    Title: {post.get("title", "")}
    Description: {post.get("description", "")}
    Approach: {post.get("approach", "")}

    """

    response = openai.chat.completions.create(
        model="gpt-4o-mini",  # or "gpt-4o-mini" if you want cheaper/faster
        messages=[
            {"role": "system", "content": "You are an expert algorithm problem analyst and senior code reviewer."},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"}  # ensures valid JSON
    )

    deserialized = json.loads(response.choices[0].message.content)
    aisuggestion = {'advice': deserialized['advice']}
    # doc.setdefault("data_structures",parsed['data_structures'])
    # doc.setdefault("algorithms",parsed['algorithms'])
    # doc.setdefault("concepts",parsed['concepts'])
    # result = posts.insert_one(doc)
    result = posts.find_one_and_update(
        {"_id": ObjectId(pid)},
        {"$set": aisuggestion},
        return_document=ReturnDocument.AFTER
    )
    # _id = str(result.inserted_id)
    return "hello"


@app.route("/api/posts", methods=['POST'])
def create_post():
    # {"title": "some title" , "description": "주어진 배열에서 구간 합을 구하는 문제"}
    data = request.get_json(silent=True)
    if data is None:
        return jsonify(error="JSON body required with Content-Type: application/json"), 400

    if 'email' not in session:
        return jsonify(error="login required"), 403

    data['email'] = session.get('email')

    # //mongdb insert
    doc = add_meta(data)
    tmp = posts.insert_one(doc)
    data["_id"] = str(tmp.inserted_id)
    pid = str(tmp.inserted_id)



    # celery
    task = get_store_keywords.delay(pid,data)

    return jsonify({"task_id": task.id, "success": True}), 201

# assuming that pid = objectid from the mongodb
@app.route("/problems/<pid>")
@login_required
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
        "approach": result['approach'],
        # "code": "def prefix_sum(arr):\n    ...",
    }
    if 'codeSnippets' in result:
        item.setdefault('code',result['codeSnippets'] )
    # if 'aiSuggestion' in result:
    #     item.setdefault('aiSuggestion', result['aiSuggestion'])
    if 'advice' in result:
        item.setdefault('aiSuggestion', result['advice'])

    # unpack data_structures, algorithms, concepts
    if 'data_structures' in result:
        data = result['data_structures'] + result['algorithms'] + result['concepts']

        keyword_solution = {
            item["keyword"]: item["explanation"]
            for item in data
        }
    else:
        keyword_solution = {}
    return render_template("problems/problem_detail.html", item=item, keyword_solution=keyword_solution)

@app.route("/problems/new")
@login_required
def new_problem():
    return render_template("problems/problem_new.html")



ALLOWED_FIELDS = {"title", "description"}

@app.route("/problems")
@login_required
def problems():
    # retrieve the email from the session
    page = int(request.args.get('page', 1, type=int))

    skipCnt = (page - 1) * 5

    email = (session.get("email") or "").strip().lower()

    q = (request.args.get("q") or "").strip()
    field_mode = request.args.get("field_mode", "title")
    if field_mode not in ALLOWED_FIELDS:
        field_mode = "title"

    # 1) 항상 이메일로 스코프
    query = {"email": email}

    # 2) 선택적 키워드 검색(단일 필드)
    if q:
        regex = re.compile(re.escape(q), re.IGNORECASE)
        query[field_mode] = regex

    # 3) 한 번의 find로 이메일 + 검색 동시 적용
    cursor = (
        posts.find(
            query,
            {
                "title": 1,
                "description": 1,
                "email": 1,
                "data_structures.keyword": 1,
                "algorithms.keyword": 1,
                "concepts.keyword": 1,
                "created_at": 1,
                "aiSuggestion": 1,
                "codeSnippets": 1
            },
        )
        .sort("_id", DESCENDING)
        .skip(skipCnt)
        .limit(6)
    )
    results = list(cursor)

    visible_results = results[:5]

    if len(results) == 6:
        nextPage = True
    else:
        nextPage = False
    email = (session.get("email") or "").strip().lower()

    # 4) processing
    for doc in visible_results:
        # created_at 안전 변환
        if doc.get("created_at"):
            try:
                doc["created_at"] = doc["created_at"].date()
            except Exception:
                pass  # 타입이 다르면 그대로 둠
        tags = []
        for path in ("data_structures", "algorithms", "concepts"):
            for item in doc.get(path, []) or []:
                kw = item.get("keyword")
                if kw:
                    tags.append(kw)
        if tags:
            doc['tags'] = tags

    return render_template(
        "problems/problems_list.html",
        items=visible_results,
        page=page,
        nextPage=nextPage,
        q=q,
        field_mode=field_mode,
    )

@app.route("/api/posts/<pid>", methods=['PATCH'])
def update_post(pid):
    data = request.get_json()
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
